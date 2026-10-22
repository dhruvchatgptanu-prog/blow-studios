"""Explainable ranking of candidates in the monitored sample.

Each component is computed from stored data, normalised to a percentile
within the current candidate set, weighted, and reported with its raw value
so the score can be audited. Velocity is *measured* only when at least two
snapshots exist; otherwise an age-adjusted rate is shown and labelled an
estimate. Unavailable data (hidden likes, unknown channel baseline) lowers
confidence instead of being invented.
"""
import math
import re

STOP = set('the a an and or of to in on for with is are was be this that it my your i you we they at by from '
           'roblox shorts short video funny part new'.split())


def tokens(text):
    return [t for t in re.findall(r"[a-z0-9']+", (text or '').lower()) if t not in STOP and len(t) > 2]


def velocity(snaps):
    """Views/hour from repeated observations; None if fewer than two."""
    s = sorted([x for x in snaps if x['views'] is not None], key=lambda x: x['retrieved_at'])
    if len(s) < 2:
        return None, 0
    a, b = s[-2], s[-1]
    hours = (b['retrieved_at'] - a['retrieved_at']) / 3600
    if hours < 0.25:
        if len(s) >= 3:
            a = s[0]
            hours = (b['retrieved_at'] - a['retrieved_at']) / 3600
        if hours < 0.25:
            return None, len(s)
    return max(0.0, (b['views'] - a['views']) / hours), len(s)


def percentile_map(values):
    vals = sorted(v for v in values if v is not None)
    n = len(vals)

    def pct(v):
        if v is None or n == 0:
            return None
        lo = sum(1 for x in vals if x < v)
        eq = sum(1 for x in vals if x == v)
        return (lo + 0.5 * eq) / n
    return pct


def relevance(video, niche_terms, game_names, negative):
    text = (video['title'] + ' ' + ' '.join(video.get('tags', [])) + ' ' + video.get('description', '')[:400]).lower()
    toks = set(tokens(text))
    niche = set(niche_terms)
    hits = sorted(toks & niche)
    games = [g for g in game_names if g.lower() in text]
    neg = [n for n in negative if n.lower() in text]
    score = min(1.0, 0.25 * len(hits) + 0.35 * len(games) + (0.25 if 'roblox' in text else 0))
    if neg:
        score *= 0.2
    return score, {'niche_terms': hits[:8], 'games': games[:5], 'negative': neg}


def rank(videos, snapshots, channels, prefs, now_ts):
    """videos: list of dicts; snapshots: id -> [snap]; channels: id -> channel row."""
    r = prefs['research']
    w = r['weights']
    niche_terms = tokens(prefs['channel']['niche']) + [t.lower() for t in r['queries'] for t in tokens(t)]
    games = prefs['channel']['game_names']
    neg = prefs['channel']['negative_keywords']
    feats = []
    for v in videos:
        snaps = snapshots.get(v['video_id'], [])
        latest = max(snaps, key=lambda s: s['retrieved_at']) if snaps else None
        views = latest['views'] if latest else None
        likes = latest['likes'] if latest else None
        comments = latest['comments'] if latest else None
        age_h = max(1.0, (now_ts - (v['published_at'] or now_ts)) / 3600)
        vel, nsnap = velocity(snaps)
        est = views / age_h ** 0.85 if views is not None else None
        eng = None
        if views and views >= 200 and likes is not None:
            eng = (likes + 2 * (comments or 0)) / views
        ch = channels.get(v['channel_id']) or {}
        base = (ch.get('baseline') or {}).get('median_views')
        rel_ch = (views / base) if (views is not None and base and (ch.get('baseline') or {}).get('n', 0) >= 5) else None
        rel, rel_ev = relevance(v, niche_terms, games, neg)
        fresh = math.exp(-age_h / 96.0)
        feats.append({'v': v, 'views': views, 'likes': likes, 'comments': comments, 'age_h': age_h,
                      'velocity': vel, 'velocity_measured': vel is not None, 'snapshots': nsnap,
                      'age_adjusted': est, 'engagement': eng, 'channel_relative': rel_ch, 'relevance': rel,
                      'relevance_evidence': rel_ev, 'freshness': fresh,
                      'baseline_n': (ch.get('baseline') or {}).get('n', 0)})
    pm = {k: percentile_map([f[k] for f in feats]) for k in ('velocity', 'age_adjusted', 'engagement',
                                                            'channel_relative')}
    out = []
    for f in feats:
        comps = {}
        conf_notes = []
        # Velocity: measured when available; otherwise the age-adjusted estimate stands in, labelled.
        if f['velocity'] is not None:
            comps['velocity'] = (pm['velocity'](f['velocity']), f'{f["velocity"]:.0f} views/hour measured over '
                                                                 f'{f["snapshots"]} snapshots')
        elif f['age_adjusted'] is not None:
            comps['velocity'] = (pm['age_adjusted'](f['age_adjusted']) * 0.8,
                                 'not yet measured (single snapshot); age-adjusted estimate used at reduced weight')
            conf_notes.append('velocity is an estimate from one observation')
        comps['age_adjusted'] = (pm['age_adjusted'](f['age_adjusted']),
                                 f'{f["views"]} views at {f["age_h"]:.1f} h old (estimate: views/age^0.85)'
                                 if f['views'] is not None else 'views unavailable')
        if f['engagement'] is not None:
            comps['engagement'] = (pm['engagement'](f['engagement']), f'(likes + 2×comments)/views = {f["engagement"]:.3f}')
        else:
            comps['engagement'] = (None, 'likes hidden or too few views')
            conf_notes.append('engagement unavailable')
        if f['channel_relative'] is not None:
            comps['channel_relative'] = (pm['channel_relative'](f['channel_relative']),
                                         f'{f["channel_relative"]:.2f}× the channel median of recent uploads')
        else:
            comps['channel_relative'] = (None, f'channel baseline unavailable ({f["baseline_n"]} recent uploads known)')
            conf_notes.append('no channel baseline')
        comps['relevance'] = (f['relevance'], f'niche match {f["relevance_evidence"]}')
        comps['freshness'] = (f['freshness'], f'{f["age_h"]:.1f} h since publication')
        total_w, score = 0.0, 0.0
        explanation = []
        for k, (val, why) in comps.items():
            weight = w.get(k, 0)
            if val is None or weight == 0:
                explanation.append({'component': k, 'value': None, 'weight': weight, 'contribution': 0, 'why': why})
                continue
            total_w += weight
            score += weight * val
            explanation.append({'component': k, 'value': round(val, 3), 'weight': weight,
                                'contribution': round(weight * val, 4), 'why': why})
        score = score / total_w if total_w else 0.0
        shorts_conf = f['v'].get('shorts_confidence', 0)
        confidence = round(min(1.0, shorts_conf) * (1.0 - 0.12 * len(conf_notes)), 2)
        out.append({'video_id': f['v']['video_id'], 'score': round(score, 4), 'explanation': explanation,
                    'confidence': max(0.05, confidence), 'confidence_notes': conf_notes,
                    'velocity_kind': 'measured' if f['velocity_measured'] else 'estimated',
                    'topics': f['relevance_evidence']['games'] + f['relevance_evidence']['niche_terms'][:3]})
    return diversify(out, videos, r['diversity_lambda'])


def diversify(ranked, videos, lam):
    """Maximal-marginal-relevance re-ordering for topic and channel diversity."""
    by_id = {v['video_id']: v for v in videos}
    pool = sorted(ranked, key=lambda x: -x['score'])
    chosen = []
    while pool:
        best, best_val = None, -1e9
        for cand in pool[:60]:
            v = by_id[cand['video_id']]
            ct = set(tokens(v['title']))
            sim = 0.0
            for c in chosen[-15:]:
                u = by_id[c['video_id']]
                ut = set(tokens(u['title']))
                j = len(ct & ut) / max(1, len(ct | ut))
                same = 1.0 if u['channel_id'] == v['channel_id'] else 0.0
                sim = max(sim, 0.6 * j + 0.4 * same)
            val = (1 - lam) * cand['score'] - lam * sim
            if val > best_val:
                best, best_val = cand, val
        best['diversity_penalty'] = round((1 - lam) * best['score'] - best_val, 4)
        best['rank'] = len(chosen) + 1
        chosen.append(best)
        pool.remove(best)
    return chosen


def emerging_topics(videos, game_names, now_ts):
    """Topics over-represented in the last 24 h compared with the last 7 days of the sample."""
    def count(window_h):
        c = {}
        n = 0
        for v in videos:
            if v['published_at'] and now_ts - v['published_at'] <= window_h * 3600:
                n += 1
                text = (v['title'] + ' ' + ' '.join(v.get('tags', []))).lower()
                found = {g for g in game_names if g.lower() in text} | set(tokens(v['title']))
                for t in found:
                    c[t] = c.get(t, 0) + 1
        return c, n
    c24, n24 = count(24)
    c7, n7 = count(168)
    out = []
    for t, k in c24.items():
        if k < 2:
            continue
        p24 = k / max(1, n24)
        p7 = c7.get(t, 0) / max(1, n7)
        lift = p24 / p7 if p7 else float('inf')
        out.append({'topic': t, 'count_24h': k, 'share_24h': round(p24, 3), 'share_7d': round(p7, 3),
                    'lift': round(lift, 2) if lift != float('inf') else None})
    out.sort(key=lambda x: (-(x['lift'] or 99), -x['count_24h']))
    return {'topics': out[:25], 'sample_24h': n24, 'sample_7d': n7,
            'note': 'Shares are within the monitored sample only, not all of YouTube.'}


