"""Research runs: discovery (search + watchlist + references) and metric snapshots.

Discovery and snapshots are separate tasks with separate cadences so quota is
spent where it matters: searches are expensive and run rarely; stats
refreshes for top candidates are cheap (1 unit per 50 videos) and run more
often to *measure* view velocity.
"""
import json
import statistics
from datetime import datetime, timezone

from .. import db as dbmod, prefs as prefsmod, store, untrusted
from ..timeutil import iso_utc, parse_iso
from ..util import Blocked, new_id, now
from . import ranking, shorts, youtube_api as Y


def _upsert_video(d, item, source, run_id):
    sn = item.get('snippet', {})
    vid = item['id']
    cls = shorts.classify(item)
    st = item.get('statistics', {})
    t = now()
    title = untrusted.clean(sn.get('title', ''), 300)
    desc = untrusted.clean(sn.get('description', ''), 5000)
    tags = [untrusted.clean(x, 60) for x in (sn.get('tags') or [])[:40]]
    inj = untrusted.injection_signals(title + ' ' + desc + ' ' + ' '.join(tags))
    row = d.one('SELECT sources FROM ref_videos WHERE video_id=?', (vid,))
    sources = json.loads(row['sources']) if row else []
    if source not in sources:
        sources.append(source)
    d.execute('''INSERT INTO ref_videos(video_id, url, channel_id, channel_title, title, description, tags, category_id,
                 published_at, first_seen_at, last_seen_at, duration_s, live, default_language, embed_w, embed_h, shorts,
                 shorts_confidence, sources) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(video_id) DO UPDATE SET channel_title=excluded.channel_title, title=excluded.title,
                 description=excluded.description, tags=excluded.tags, last_seen_at=excluded.last_seen_at,
                 duration_s=excluded.duration_s, live=excluded.live, embed_w=excluded.embed_w, embed_h=excluded.embed_h,
                 shorts=excluded.shorts, shorts_confidence=excluded.shorts_confidence, sources=excluded.sources''',
              (vid, f'https://www.youtube.com/shorts/{vid}' if cls['label'] != 'unlikely_short' else
               f'https://www.youtube.com/watch?v={vid}', sn.get('channelId'), untrusted.clean(sn.get('channelTitle', ''), 120),
               title, desc, json.dumps(tags), sn.get('categoryId'), parse_iso(sn.get('publishedAt')), t, t,
               cls['signals']['duration_s'], sn.get('liveBroadcastContent'), sn.get('defaultLanguage'),
               cls['signals']['player_w'], cls['signals']['player_h'],
               json.dumps({**cls, 'injection_flags': inj}), cls['confidence'], json.dumps(sources)))

    def num(k):
        v = st.get(k)
        return int(v) if v is not None else None
    d.execute('INSERT INTO ref_snapshots(id, video_id, retrieved_at, views, likes, comments, run_id) VALUES (?,?,?,?,?,?,?)',
              (new_id('sn_'), vid, t, num('viewCount'), num('likeCount'), num('commentCount'), run_id))
    return cls


def plan_cost(p):
    r = p['research']
    searches = min(r['max_search_calls_per_run'], len(r['queries']) * len(r['windows_hours']) * r['max_pages_per_query'])
    watch = len(watchlist(p))
    shared = watch * 2 + 10 + searches * 1 + 6
    return {'search_calls': searches, 'shared_units_estimate': shared}


def watchlist(p, d=None):
    d = d or dbmod.get()
    return [r['channel_id'] for r in d.query('SELECT channel_id FROM ref_channels WHERE watch=1')]


def add_reference(url, d=None, actor='owner'):
    """Owner-supplied reference URL -> stored candidate or watched channel (no page fetch)."""
    d = d or dbmod.get()
    kind, ident = Y.parse_reference(url)
    if kind == 'video':
        d.execute('''INSERT INTO ref_videos(video_id, url, title, first_seen_at, last_seen_at, sources, user_reference)
                     VALUES (?,?,?,?,?,?,1) ON CONFLICT(video_id) DO UPDATE SET user_reference=1''',
                  (ident, f'https://www.youtube.com/watch?v={ident}', '(metadata pending)', now(), now(),
                   json.dumps(['owner_reference'])))
    elif kind == 'channel':
        d.execute('INSERT INTO ref_channels(channel_id, watch, added_at, note) VALUES (?,1,?,?) '
                  'ON CONFLICT(channel_id) DO UPDATE SET watch=1', (ident, now(), 'owner watchlist'))
    else:
        items = Y.channels(handle=ident)
        if not items:
            raise ValueError('No channel found for ' + ident)
        cid = items[0]['id']
        _store_channel(d, items[0], watch=True)
        ident = cid
    store.audit('research_reference_added', {'kind': kind, 'id': ident}, actor=actor, d=d)
    return {'kind': kind, 'id': ident}


def _store_channel(d, item, watch=None):
    st = item.get('statistics', {})
    uploads = item.get('contentDetails', {}).get('relatedPlaylists', {}).get('uploads')
    d.execute('''INSERT INTO ref_channels(channel_id, title, uploads_playlist, subscriber_count, subscriber_hidden,
                 video_count, watch, fetched_at, added_at) VALUES (?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title, uploads_playlist=excluded.uploads_playlist,
                 subscriber_count=excluded.subscriber_count, subscriber_hidden=excluded.subscriber_hidden,
                 video_count=excluded.video_count, fetched_at=excluded.fetched_at''' +
              (', watch=excluded.watch' if watch is not None else ''),
              (item['id'], untrusted.clean(item.get('snippet', {}).get('title', ''), 120), uploads,
               int(st['subscriberCount']) if st.get('subscriberCount') else None, int(bool(st.get('hiddenSubscriberCount'))),
               int(st['videoCount']) if st.get('videoCount') else None, int(bool(watch)), now(), now()))


def discover(run_kind='discover', d=None, p=None):
    d = d or dbmod.get()
    p = p or prefsmod.get(d)
    r = p['research']
    if not r['enabled']:
        raise Blocked('Research is disabled in Settings', state='blocked')
    rid = new_id('rr_')
    cost = plan_cost(p)
    d.execute('INSERT INTO research_runs(id, kind, params, status, started_at) VALUES (?,?,?,?,?)',
              (rid, run_kind, json.dumps({'queries': r['queries'], 'windows_hours': r['windows_hours'],
                                          'plan': cost}), 'running', now()))
    coverage = {'searches': [], 'watchlist': [], 'references': [], 'errors': [], 'videos_seen': 0,
                'note': 'Top candidates are within this monitored sample only, not all of YouTube.'}
    found = {}
    t = now()
    try:
        calls = 0
        for q in r['queries']:
            for wh in r['windows_hours']:
                after = iso_utc(t - wh * 3600)
                before = iso_utc(t)
                token = None
                for page in range(r['max_pages_per_query']):
                    if calls >= r['max_search_calls_per_run']:
                        break
                    try:
                        body = Y.search(q, after, before, token, region=r['region_code'], lang=r['relevance_language'],
                                        cache_minutes=r['cache_minutes'])
                    except Y.QuotaExhausted as e:
                        coverage['errors'].append(str(e))
                        raise
                    calls += 1
                    ids = [it['id']['videoId'] for it in body.get('items', []) if it.get('id', {}).get('videoId')]
                    for i in ids:
                        found.setdefault(i, f'search:{q}:{wh}h')
                    coverage['searches'].append({'query': q, 'window_h': wh, 'page': page + 1, 'results': len(ids),
                                                 'total_results_reported': body.get('pageInfo', {}).get('totalResults'),
                                                 'cached': '_cached_at' in body})
                    token = body.get('nextPageToken')
                    if not token:
                        break
        # Watchlist uploads (cheap: 1 unit per page)
        wl = d.query('SELECT channel_id, uploads_playlist FROM ref_channels WHERE watch=1')
        missing = [w['channel_id'] for w in wl if not w['uploads_playlist']]
        if missing:
            for item in Y.channels(missing):
                _store_channel(d, item)
            wl = d.query('SELECT channel_id, uploads_playlist FROM ref_channels WHERE watch=1')
        horizon = t - max(r['windows_hours']) * 3600
        for w in wl:
            if not w['uploads_playlist']:
                coverage['watchlist'].append({'channel': w['channel_id'], 'error': 'uploads playlist unavailable'})
                continue
            body = Y.playlist_items(w['uploads_playlist'])
            n = 0
            for it in body.get('items', []):
                pub = parse_iso(it.get('contentDetails', {}).get('videoPublishedAt'))
                vid = it.get('contentDetails', {}).get('videoId')
                if vid and pub and pub >= horizon:
                    found.setdefault(vid, 'watchlist:' + w['channel_id'])
                    n += 1
            coverage['watchlist'].append({'channel': w['channel_id'], 'recent_uploads': n})
        # Owner references
        for row in d.query('SELECT video_id FROM ref_videos WHERE user_reference=1'):
            found.setdefault(row['video_id'], 'owner_reference')
            coverage['references'].append(row['video_id'])
        # Details + first snapshot (1 unit per 50 ids)
        items = Y.videos(sorted(found)) if found else []
        for it in items:
            _upsert_video(d, it, found.get(it['id'], 'unknown'), rid)
        coverage['videos_seen'] = len(items)
        coverage['missing_details'] = len(found) - len(items)
        # Channel baselines for channels in the sample
        chans = sorted({it['snippet']['channelId'] for it in items if it.get('snippet', {}).get('channelId')})
        _refresh_channels(d, chans[:50], coverage)
        scored = rescore(d, p)
        status = 'succeeded'
        err = ''
    except Blocked as e:
        status, err = 'blocked', str(e)
        scored = None
    except Exception as e:
        status, err = 'failed', f'{type(e).__name__}: {str(e)[:300]}'
        scored = None
    q_used = d.scalar('SELECT COALESCE(SUM(units),0) AS u FROM quota_ledger WHERE at>=?', (t,))
    d.execute('UPDATE research_runs SET status=?, finished_at=?, quota_used=?, coverage=?, error=? WHERE id=?',
              (status, now(), int(q_used or 0), json.dumps(coverage), err[:1000], rid))
    if status == 'blocked':
        raise Blocked(err)
    if status == 'failed':
        raise RuntimeError(err)
    return {'run': rid, 'videos': coverage['videos_seen'], 'scored': scored}


def _refresh_channels(d, chans, coverage):
    known = {r['channel_id'] for r in d.query('SELECT channel_id FROM ref_channels WHERE fetched_at>?', (now() - 86400,))}
    need = [c for c in chans if c not in known]
    if need:
        for item in Y.channels(need):
            _store_channel(d, item)
    # Baseline: median views of recent uploads we have observed for the channel.
    for c in chans:
        rows = d.query('''SELECT v.video_id, MAX(s.views) AS views FROM ref_videos v JOIN ref_snapshots s
                          ON s.video_id=v.video_id WHERE v.channel_id=? AND v.published_at>? GROUP BY v.video_id''',
                       (c, now() - 30 * 86400))
        vals = [r['views'] for r in rows if r['views'] is not None]
        base = {'median_views': statistics.median(vals) if vals else None, 'n': len(vals),
                'note': 'Median of uploads observed by Blox research in the last 30 days'}
        d.execute('UPDATE ref_channels SET baseline=? WHERE channel_id=?', (json.dumps(base), c))
    coverage['channels_baselined'] = len(chans)


def snapshot(d=None, p=None):
    """Refresh statistics for the current top candidates to measure velocity."""
    d = d or dbmod.get()
    p = p or prefsmod.get(d)
    n = p['research']['snapshot_top_n']
    ids = [r['video_id'] for r in d.query(
        'SELECT video_id FROM ref_videos WHERE published_at>? ORDER BY COALESCE(score,0) DESC LIMIT ?',
        (now() - 8 * 86400, n))]
    if not ids:
        return {'refreshed': 0}
    rid = new_id('rr_')
    d.execute('INSERT INTO research_runs(id, kind, params, status, started_at) VALUES (?,?,?,?,?)',
              (rid, 'snapshot', json.dumps({'ids': len(ids)}), 'running', now()))
    items = Y.videos(ids)
    for it in items:
        _upsert_video(d, it, 'snapshot', rid)
    rescore(d, p)
    d.execute('UPDATE research_runs SET status=?, finished_at=?, coverage=? WHERE id=?',
              ('succeeded', now(), json.dumps({'refreshed': len(items), 'requested': len(ids)}), rid))
    return {'refreshed': len(items)}


def rescore(d=None, p=None):
    d = d or dbmod.get()
    p = p or prefsmod.get(d)
    horizon = now() - max(p['research']['windows_hours']) * 3600 - 86400
    rows = d.query('SELECT * FROM ref_videos WHERE (published_at>? OR user_reference=1) AND shorts_confidence>=0.4',
                   (horizon,))
    vids = []
    for r in rows:
        r['tags'] = json.loads(r['tags'])
        vids.append(r)
    if not vids:
        return 0
    snaps = {}
    for s in d.query('SELECT video_id, retrieved_at, views, likes, comments FROM ref_snapshots WHERE retrieved_at>?',
                     (horizon,)):
        snaps.setdefault(s['video_id'], []).append(s)
    chans = {c['channel_id']: dict(c, baseline=json.loads(c['baseline'])) for c in d.query('SELECT * FROM ref_channels')}
    ranked = ranking.rank(vids, snaps, chans, p, now())
    with d.tx():
        for item in ranked:
            d.execute('UPDATE ref_videos SET score=?, rank=?, topics=? WHERE video_id=?',
                      (item['score'], json.dumps(item), json.dumps(item['topics']), item['video_id']))
    store.put('research_emerging', ranking.emerging_topics(vids, p['channel']['game_names'], now()), d)
    return len(ranked)


def top(limit=50, d=None):
    d = d or dbmod.get()
    rows = d.query('SELECT * FROM ref_videos WHERE score IS NOT NULL ORDER BY score DESC LIMIT ?', (limit * 2,))
    out = []
    for r in rows:
        r['rank'] = json.loads(r['rank'] or '{}')
        r['shorts'] = json.loads(r['shorts'] or '{}')
        r['tags'] = json.loads(r['tags'] or '[]')
        r['sources'] = json.loads(r['sources'] or '[]')
        r['snapshots'] = d.query('SELECT retrieved_at, views, likes, comments FROM ref_snapshots WHERE video_id=? '
                                 'ORDER BY retrieved_at', (r['video_id'],))
        out.append(r)
    out.sort(key=lambda r: r['rank'].get('rank', 10 ** 6))
    return out[:limit]


def coverage_summary(d=None):
    d = d or dbmod.get()
    runs = d.query('SELECT * FROM research_runs ORDER BY started_at DESC LIMIT 10')
    for r in runs:
        r['coverage'] = json.loads(r['coverage'] or '{}')
        r['params'] = json.loads(r['params'] or '{}')
    totals = d.one('SELECT COUNT(*) AS videos, SUM(CASE WHEN shorts_confidence>=0.7 THEN 1 ELSE 0 END) AS likely '
                   'FROM ref_videos')
    return {'runs': runs, 'totals': totals, 'emerging': store.get('research_emerging', {}, d),
            'quota': {b: {'used_today': Y.used(b, d), 'limit': Y.limits()[b]} for b in ('shared', 'search', 'insert')},
            'as_of': datetime.now(timezone.utc).isoformat()}
