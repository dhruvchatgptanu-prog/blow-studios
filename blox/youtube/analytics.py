"""YouTube Analytics collection for the owner's own published videos.

Only metrics the Analytics API exposes are requested; anything it rejects or
omits is recorded as missing rather than estimated. Data for a day can arrive
two or more days late, so videos are measured only after ``min_age_days`` and
observation windows are reported with every snapshot.
"""
import json
from datetime import datetime, timedelta, timezone

from .. import db as dbmod, prefs as prefsmod, store
from ..http import ProviderError
from ..tasks import handler
from ..util import Blocked, new_id, now
from . import oauth

URL = 'https://youtubeanalytics.googleapis.com/v2/reports'
CORE = ['views', 'engagedViews', 'estimatedMinutesWatched', 'averageViewDuration', 'averageViewPercentage', 'likes',
        'comments', 'shares', 'subscribersGained', 'subscribersLost']


def _query(params):
    return oauth.authed('GET', URL, params=params, timeout=(10, 60)).json()


def metrics_for(video_ids, start, end):
    """Returns (rows by video id, missing metric names)."""
    metrics = list(CORE)
    missing = []
    for _ in range(len(CORE)):
        try:
            j = _query({'ids': 'channel==MINE', 'startDate': start, 'endDate': end, 'metrics': ','.join(metrics),
                        'dimensions': 'video', 'filters': 'video==' + ','.join(video_ids), 'maxResults': 200})
            break
        except ProviderError as e:
            if e.status == 400 and metrics:
                # Drop the newest/least portable metric and retry; record it as missing.
                dropped = metrics.pop(metrics.index('engagedViews') if 'engagedViews' in metrics else -1)
                missing.append(dropped)
                continue
            raise
    else:
        return {}, CORE
    heads = [h['name'] for h in j.get('columnHeaders', [])]
    out = {}
    for row in j.get('rows', []) or []:
        rec = dict(zip(heads, row))
        out[rec['video']] = {k: rec.get(k) for k in metrics}
    return out, missing


def retention(video_id, start, end):
    try:
        j = _query({'ids': 'channel==MINE', 'startDate': start, 'endDate': end,
                    'metrics': 'audienceWatchRatio,relativeRetentionPerformance', 'dimensions': 'elapsedVideoTimeRatio',
                    'filters': f'video=={video_id}'})
    except ProviderError:
        return None
    heads = [h['name'] for h in j.get('columnHeaders', [])]
    return [dict(zip(heads, r)) for r in j.get('rows', []) or []] or None


@handler('analytics.collect')
def collect(ctx=None):
    d = dbmod.get()
    p = prefsmod.get(d)
    st = oauth.status()
    if not st['connected']:
        raise Blocked('Connect YouTube to collect analytics', state='needs_credentials')
    if not st['analytics_scope']:
        store.put('analytics_status', {'at': now(), 'note': 'Analytics permission not granted. Reconnect YouTube with '
                                                            'analytics enabled to learn from performance.'})
        return {'skipped': 'no analytics scope'}
    a = p['analytics']
    cutoff = now() - a['min_age_days'] * 86400
    vids = d.query("SELECT id, youtube_video_id, updated_at FROM videos WHERE status='published' AND youtube_video_id "
                   'IS NOT NULL AND updated_at<?', (cutoff,))
    if not vids:
        store.put('analytics_status', {'at': now(), 'note': 'No published videos old enough to measure yet.'})
        return {'videos': 0}
    today = datetime.now(timezone.utc).date()
    end = (today - timedelta(days=1)).isoformat()
    start = (today - timedelta(days=max(28, a['observation_days'] + a['min_age_days']))).isoformat()
    by_id = {v['youtube_video_id']: v['id'] for v in vids}
    ids = list(by_id)
    stored = 0
    for i in range(0, len(ids), 50):
        rows, missing = metrics_for(ids[i:i + 50], start, end)
        for yid in ids[i:i + 50]:
            ret = retention(yid, start, end)
            d.execute('INSERT INTO analytics_snapshots(id, youtube_video_id, video_id, fetched_at, start_date, end_date, '
                      'metrics, retention, missing) VALUES (?,?,?,?,?,?,?,?,?)',
                      (new_id('an_'), yid, by_id[yid], now(), start, end, json.dumps(rows.get(yid) or {}),
                       json.dumps(ret or []),
                       json.dumps(missing + ([] if ret else ['retention']) + ([] if yid in rows else ['all']))))
            stored += 1
    from ..learning import update_findings
    findings = update_findings(d, p)
    store.put('analytics_status', {'at': now(), 'videos': stored, 'window': [start, end], 'findings': len(findings),
                                   'note': 'Analytics data can lag by 2-3 days.'})
    return {'videos': stored}
