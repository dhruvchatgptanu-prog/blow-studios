"""Learning from the owner's own analytics without over-claiming.

For each production feature (hook type, ending type, length bucket, lead
character, setting, opening framing, pacing, voice provider) videos are
grouped and compared on average percentage viewed and engaged views per day.
A comparison is a *finding* only with at least ``min_sample`` videos in both
groups and a bootstrap confidence interval for the difference that excludes
zero; everything else is a *hypothesis*. Findings nudge future briefs; they
never change the channel identity, characters or format, and correlation is
not presented as causation.
"""
import json
import random
import statistics

from .util import new_id, now

FEATURES = ['hook_type', 'ending_type', 'length_bucket', 'lead_character', 'setting', 'opening_framing', 'pacing',
            'voice_provider']


def video_features(d, video_id):
    v = d.one('SELECT * FROM videos WHERE id=?', (video_id,))
    if not v:
        return {}
    feats = {}
    c = d.one('SELECT features FROM concepts WHERE id=?', (v['concept_id'],)) if v['concept_id'] else None
    if c:
        cf = json.loads(c['features'])
        feats['hook_type'] = cf.get('hook_type')
        feats['ending_type'] = cf.get('ending_type')
    m = d.one('SELECT body FROM manifests WHERE id=?', (v['manifest_id'],)) if v['manifest_id'] else None
    if m:
        body = json.loads(m['body'])
        secs = body['duration_frames'] / body['fps']
        feats['length_bucket'] = '<35s' if secs < 35 else ('35-45s' if secs <= 45 else '>45s')
        feats['lead_character'] = body['cast'][0]['character_id'] if body['cast'] else None
        feats['setting'] = body['setting'].get('preset')
        feats['opening_framing'] = body['shots'][0]['camera']['framing_start'] if body['shots'] else None
        cuts_per_10s = len(body['shots']) / max(1.0, secs) * 10
        feats['pacing'] = 'fast' if cuts_per_10s >= 3 else ('medium' if cuts_per_10s >= 1.8 else 'slow')
    ln = d.one('SELECT alignment FROM audio_lines WHERE video_id=? LIMIT 1', (video_id,))
    if ln:
        feats['voice_provider'] = (json.loads(ln['alignment'] or '{}')).get('provider')
    return {k: v for k, v in feats.items() if v}


def _boot_ci(a, b, n=800, seed=7):
    rnd = random.Random(seed)
    diffs = []
    for _ in range(n):
        sa = [rnd.choice(a) for _ in a]
        sb = [rnd.choice(b) for _ in b]
        diffs.append(statistics.mean(sa) - statistics.mean(sb))
    diffs.sort()
    return diffs[int(0.025 * n)], diffs[int(0.975 * n) - 1]


def dataset(d):
    rows = d.query('''SELECT a.video_id, a.metrics, a.start_date, a.end_date FROM analytics_snapshots a
                      WHERE a.fetched_at = (SELECT MAX(fetched_at) FROM analytics_snapshots b WHERE b.video_id=a.video_id)''')
    out = []
    for r in rows:
        mt = json.loads(r['metrics'] or '{}')
        if not mt:
            continue
        out.append({'video_id': r['video_id'], 'metrics': mt, 'features': video_features(d, r['video_id'])})
    return out


def update_findings(d, p):
    data = dataset(d)
    min_n = p['analytics']['min_sample']
    results = []
    for feat in FEATURES:
        groups = {}
        for x in data:
            val = x['features'].get(feat)
            m = x['metrics'].get('averageViewPercentage')
            if val is None or m is None:
                continue
            groups.setdefault(val, []).append(float(m))
        if len(groups) < 2:
            continue
        ranked = sorted(groups.items(), key=lambda kv: -statistics.mean(kv[1]))
        (top, a), (other, b) = ranked[0], ranked[-1]
        diff = statistics.mean(a) - statistics.mean(b)
        enough = len(a) >= min_n and len(b) >= min_n
        ci = _boot_ci(a, b) if len(a) >= 3 and len(b) >= 3 else None
        kind = 'finding' if enough and ci and (ci[0] > 0 or ci[1] < 0) else 'hypothesis'
        body = {'feature': feat, 'better': top, 'worse': other, 'metric': 'averageViewPercentage',
                'mean_better': round(statistics.mean(a), 2), 'mean_worse': round(statistics.mean(b), 2),
                'n_better': len(a), 'n_worse': len(b), 'difference': round(diff, 2),
                'ci95': [round(ci[0], 2), round(ci[1], 2)] if ci else None,
                'groups': {k: {'n': len(v), 'mean': round(statistics.mean(v), 2)} for k, v in groups.items()},
                'caveat': 'Association in a small sample of this channel\'s videos, not proof of cause.'}
        d.execute('INSERT INTO learning_findings(id, created_at, feature, metric, kind, body) VALUES (?,?,?,?,?,?)',
                  (new_id('lf_'), now(), feat, 'averageViewPercentage', kind, json.dumps(body)))
        results.append(dict(body, kind=kind))
    return results


def latest(d):
    rows = d.query('''SELECT * FROM learning_findings f WHERE created_at = (SELECT MAX(created_at) FROM learning_findings g
                      WHERE g.feature=f.feature) ORDER BY feature''')
    for r in rows:
        r['body'] = json.loads(r['body'])
    return rows


def brief_notes(d):
    """Short, explicitly labelled notes for concept generation."""
    out = []
    for r in latest(d):
        b = r['body']
        label = 'Finding' if r['kind'] == 'finding' else 'Hypothesis (not established)'
        out.append(f'{label}: {b["feature"]}={b["better"]} averaged {b["mean_better"]}% viewed vs {b["mean_worse"]}% for '
                   f'{b["worse"]} (n={b["n_better"]} vs {b["n_worse"]}). Use as a light preference only; keep the channel '
                   f'identity unchanged.')
    return out
