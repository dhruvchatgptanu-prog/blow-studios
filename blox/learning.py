"""Learning from the owner's own analytics without over-claiming.

For each production feature (hook type, ending type, length bucket, lead
character, setting, opening framing, pacing, voice provider, and for the
first-person story format: format, mechanism, mode, twist type and narration
pace) videos are grouped and compared on average percentage viewed. A
comparison is a *finding* only with at least ``min_sample`` videos in both
groups and a bootstrap confidence interval for the difference that excludes
zero; everything else is a *hypothesis*. Median views per story mechanism are
reported the same way. Findings nudge future briefs; they never change the
channel identity, characters or format, and correlation is not presented as
causation.
"""
import json
import random
import re
import statistics

from .util import new_id, now

FEATURES = ['hook_type', 'ending_type', 'length_bucket', 'lead_character', 'setting', 'opening_framing', 'pacing',
            'voice_provider', 'format', 'mechanism', 'mode', 'twist_type', 'narration_pace']

# Story-format vocabulary (the shared plan contract). Kept here as plain tuples so nothing depends on the
# format module at import time; values outside these lists are ignored.
FORMATS = ('skit', 'storytime', 'first_person_story')
MODES = ('world_scale', 'grounded')
LOOP_TYPES = ('callback', 'sequel_tease')
MECHANISMS = ('impossible_opponent', 'audience_control', 'constraint_curse', 'everyday_accident', 'rescue_mystery',
              'identity_lie', 'world_status', 'crime_reversal', 'taboo_vs_wholesome', 'self_involvement',
              'absurd_belief', 'words_become_real', 'mild_mischief', 'supernatural')
TWIST_TYPES = ('lie_becomes_true', 'backfire', 'role_flip', 'scale_flip', 'hidden_reason', 'narrator_exposed',
               'karma_loop', 'unexpected_rescuer', 'authority_from_home')
STORY_KEYS = {'mechanism': MECHANISMS, 'mode': MODES, 'twist_type': TWIST_TYPES, 'loop_type': LOOP_TYPES}
# Narration pace buckets (finished-video words per second), from the format's default target band.
WPS_SLOW, WPS_FAST = 4.0, 4.45
_WORD = re.compile(r"[A-Za-z0-9']+")


def enum_value(value, allowed):
    """``value`` normalised to one of ``allowed`` (lower case, spaces and dashes as underscores), else None."""
    if not isinstance(value, str):
        return None
    v = re.sub(r'[\s-]+', '_', value.strip().lower())[:40]
    return v if v in allowed else None


def story_fields(m, plan=None):
    """format, mechanism, mode, twist_type and loop_type of a compiled manifest, falling back to its plan.

    Both are untrusted dicts: only known values are returned, unknown or missing ones are left out."""
    m = m if isinstance(m, dict) else {}
    plan = plan if isinstance(plan, dict) else {}
    out = {}
    fmt = enum_value(m.get('format'), FORMATS) or enum_value(plan.get('format'), FORMATS)
    if fmt:
        out['format'] = fmt
    ms = m.get('story') if isinstance(m.get('story'), dict) else {}
    ps = plan.get('story') if isinstance(plan.get('story'), dict) else {}
    for k, allowed in STORY_KEYS.items():
        v = enum_value(ms.get(k), allowed) or enum_value(ps.get(k), allowed)
        if v:
            out[k] = v
    return out


def stored_story_fields(meta):
    """The validated story fields of the flat copy stored on a video at development (metadata.format_story)."""
    stored = meta.get('format_story') if isinstance(meta, dict) else None
    if not isinstance(stored, dict):
        return {}
    return story_fields({'format': stored.get('format'), 'story': stored})


def narration_wps(body, measured=None):
    """Narration words per second of the finished video: words over voiced seconds.

    ``measured`` is a list of (text, duration_s) for voiced lines; without it the compiled estimate is used."""
    pairs = [(t, s) for t, s in (measured or []) if isinstance(s, (int, float)) and s > 0]
    if not pairs and isinstance(body, dict):
        fps = body.get('fps') or 30
        for ln in body.get('lines') or []:
            fr = ln.get('est_frames') if isinstance(ln, dict) else None
            if isinstance(fr, (int, float)) and fr > 0:
                pairs.append((ln.get('text', ''), fr / fps))
    secs = sum(s for _, s in pairs)
    n_words = sum(len(_WORD.findall(str(t or ''))) for t, _ in pairs)
    if secs <= 0 or not n_words:
        return None
    return round(n_words / secs, 2)


def pace_bucket(wps):
    if wps is None:
        return None
    return f'under {WPS_SLOW}' if wps < WPS_SLOW else (f'{WPS_SLOW}-{WPS_FAST}' if wps <= WPS_FAST else f'over {WPS_FAST}')


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
    body = None
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
    # Story format fields: from the manifest, else the copy stored on the video at development.
    try:
        meta = json.loads(v['metadata'] or '{}')
    except (TypeError, ValueError):
        meta = {}
    feats.update(stored_story_fields(meta))
    feats.update(story_fields(body or {}))
    if body:
        voiced = d.query('SELECT text, duration_s FROM audio_lines WHERE video_id=? AND manifest_id=? AND duration_s > 0',
                         (video_id, v['manifest_id']))
        wps = narration_wps(body, [(r['text'], r['duration_s']) for r in voiced])
        if wps:
            feats['narration_wps'] = wps
            feats['narration_pace'] = pace_bucket(wps)
    return {k: v for k, v in feats.items() if v}


def _boot_ci(a, b, n=800, seed=7, stat=statistics.mean):
    rnd = random.Random(seed)
    diffs = []
    for _ in range(n):
        sa = [rnd.choice(a) for _ in a]
        sb = [rnd.choice(b) for _ in b]
        diffs.append(stat(sa) - stat(sb))
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
    mv = mechanism_views(data, min_n)
    if mv:
        kind = mv.pop('kind')
        d.execute('INSERT INTO learning_findings(id, created_at, feature, metric, kind, body) VALUES (?,?,?,?,?,?)',
                  (new_id('lf_'), now(), 'mechanism_views', 'views', kind, json.dumps(mv)))
        results.append(dict(mv, kind=kind))
    return results


def mechanism_views(data, min_n):
    """Median views per story mechanism. A *finding* only when the best and worst mechanisms each have at least
    ``min_n`` videos and the bootstrap interval for the difference in medians excludes zero."""
    groups = {}
    for x in data:
        mech = x['features'].get('mechanism')
        views = x['metrics'].get('views')
        if mech is None or not isinstance(views, (int, float)) or isinstance(views, bool) or views < 0:
            continue
        groups.setdefault(mech, []).append(float(views))
    if not groups:
        return None
    med = {k: statistics.median(v) for k, v in groups.items()}
    ranked = sorted(groups, key=lambda k: (-med[k], k))
    top, other = ranked[0], ranked[-1]
    a, b = groups[top], groups[other]
    ci = None
    if top != other and len(a) >= 3 and len(b) >= 3:
        ci = _boot_ci(a, b, stat=statistics.median)
    enough = top != other and len(a) >= min_n and len(b) >= min_n
    kind = 'finding' if enough and ci and (ci[0] > 0 or ci[1] < 0) else 'hypothesis'
    return {'feature': 'mechanism_views', 'metric': 'views', 'statistic': 'median', 'better': top, 'worse': other,
            'median_better': round(med[top]), 'median_worse': round(med[other]),
            'n_better': len(a), 'n_worse': len(b), 'difference': round(med[top] - med[other]),
            'ci95': [round(ci[0]), round(ci[1])] if ci else None,
            'groups': {k: {'n': len(groups[k]), 'median_views': round(med[k])} for k in ranked},
            'caveat': 'Median views of a small sample of this channel\'s videos, not proof of cause.', 'kind': kind}


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
        if b.get('metric') == 'views':
            per = '; '.join(f'{k} {g["median_views"]:,} (n={g["n"]})' for k, g in (b.get('groups') or {}).items())
            out.append(f'{label}: median views by story mechanism: {per}. Use as a light preference only; keep the '
                       f'channel identity unchanged.')
            continue
        out.append(f'{label}: {b["feature"]}={b["better"]} averaged {b["mean_better"]}% viewed vs {b["mean_worse"]}% for '
                   f'{b["worse"]} (n={b["n_better"]} vs {b["n_worse"]}). Use as a light preference only; keep the channel '
                   f'identity unchanged.')
    return out
