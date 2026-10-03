"""Compile an authoring plan into the authoritative frame-indexed manifest.

Authoring plans (from the LLM, the editor or a hand-written file) use seconds
for convenience. Compilation converts every time to an integer frame once,
resolves partial poses into complete poses, derives one-second narrative beats
(split at shot boundaries so a beat never straddles a cut) and lists, for every
beat, the fully resolved start/end state of each character and the camera.

Tracks (character keys, actions, camera, lines, sfx, music, prop events) are
what the renderer executes. Beats are an explicit, per-second description of
that same timeline used by the director's script, the editor and QA.
"""
import copy
import math
import re
import zlib

from . import schema as S

MAX_PLAN_BYTES = 400_000


def tc(frame, fps):
    """Frame -> 'MM:SS.ff' (ff = frame within second)."""
    s, f = divmod(int(frame), int(fps))
    return f'{s // 60:02d}:{s % 60:02d}.{f:02d}'


def fr(t, fps):
    return int(round(float(t) * fps))


def _num(v, default=0.0):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _deep_update(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


POSE_KEYS = ('position', 'facing', 'eye_target', 'head', 'brows', 'eyes', 'mouth', 'shoulders', 'arms', 'torso',
             'posture', 'feet')


def resolve_pose(prev, key):
    """Apply a partial key on top of the previous resolved pose."""
    pose = copy.deepcopy(prev)
    if key.get('expression'):
        label = key['expression']
        pose['expression'] = label
        preset = S.EXPRESSIONS.get(label)
        if preset:
            for part in ('brows', 'eyes', 'mouth'):
                pose[part] = copy.deepcopy(preset[part])
    for k in POSE_KEYS:
        if k in key and key[k] is not None:
            if isinstance(key[k], dict) and isinstance(pose.get(k), dict):
                _deep_update(pose[k], key[k])
            else:
                pose[k] = copy.deepcopy(key[k])
    return pose


def _ease(u, kind='in_out'):
    u = max(0.0, min(1.0, u))
    if kind == 'linear':
        return u
    if kind == 'in':
        return u * u
    if kind == 'out':
        return 1 - (1 - u) * (1 - u)
    return u * u * (3 - 2 * u)


def _lerp(a, b, u):
    return a + (b - a) * u


def interp_pose(a, b, u):
    """Blend two resolved poses. Numbers interpolate; labels switch at u >= 0.5."""
    out = copy.deepcopy(a if u < 0.5 else b)
    e = _ease(u)
    out['position'] = [round(_lerp(a['position'][i], b['position'][i], e), 4) for i in range(2)]
    fa, fb = a['facing'], b['facing']
    diff = (fb - fa + 180) % 360 - 180
    out['facing'] = round(fa + diff * e, 3)
    for part in ('head', 'brows', 'eyes', 'torso', 'shoulders'):
        for k in a[part]:
            if isinstance(a[part][k], (int, float)) and isinstance(b[part].get(k), (int, float)):
                out[part][k] = round(_lerp(a[part][k], b[part][k], e), 4)
    out['mouth']['open'] = round(_lerp(a['mouth']['open'], b['mouth']['open'], e), 4)
    return out


def pose_at(keys, frame):
    """Resolved pose of a character track at ``frame`` (keys sorted by frame)."""
    if not keys:
        return copy.deepcopy(S.DEFAULT_POSE)
    if frame <= keys[0]['frame']:
        return copy.deepcopy(keys[0]['pose'])
    for i in range(len(keys) - 1):
        k0, k1 = keys[i], keys[i + 1]
        if k0['frame'] <= frame <= k1['frame']:
            span = max(1, k1['frame'] - k0['frame'])
            # Hold the earlier pose, then transition over the final frames.
            blend = k1.get('blend_frames') or min(span, 10)
            start = k1['frame'] - blend
            if frame <= start:
                return copy.deepcopy(k0['pose'])
            return interp_pose(k0['pose'], k1['pose'], (frame - start) / max(1, blend))
    return copy.deepcopy(keys[-1]['pose'])


def _seed(*parts):
    return zlib.crc32('|'.join(str(p) for p in parts).encode())


def auto_blinks(char_id, duration_frames, fps, authored=(), closed_ranges=()):
    """Deterministic natural blink timing (every ~2.5-4.5 s) plus authored blinks."""
    frames = set(int(f) for f in authored)
    rnd = _seed(char_id)
    t = int(fps * (0.8 + (rnd % 100) / 100.0))
    while t < duration_frames - 6:
        if not any(a <= t <= b for a, b in closed_ranges):
            frames.add(t)
        rnd = (rnd * 1103515245 + 12345) & 0x7fffffff
        t += int(fps * (2.5 + (rnd % 2000) / 1000.0))
    return sorted(f for f in frames if 0 <= f < duration_frames - 4)


WORD_RE = re.compile(r"[A-Za-z0-9']+")


def words(text):
    return WORD_RE.findall(text or '')


def estimate_line_frames(line, fps):
    n = max(1, len(words(line['text'])))
    wps = S.PACE_WPS.get(line.get('pace', 'normal'), 2.7)
    punct = (len(re.findall(r'[,;:]', line['text'])) * 0.18
             + len(re.findall(r'\.\.\.|…|[.!?]+', line['text'])) * 0.25)
    secs = n / wps + punct + 0.15
    return int(math.ceil(secs * fps))


def split_caption_groups(text, max_words=4, max_chars=26):
    ws = text.split()
    groups, cur = [], []
    for w in ws:
        trial = ' '.join(cur + [w])
        if cur and (len(cur) >= max_words or len(trial) > max_chars):
            groups.append(' '.join(cur))
            cur = [w]
        else:
            cur.append(w)
        if re.search(r'[.!?]$', w) and len(cur) >= 2:
            groups.append(' '.join(cur))
            cur = []
    if cur:
        groups.append(' '.join(cur))
    return groups


def captions_for(lines, fps, timings=None):
    """Caption groups per line. ``timings`` maps line_id -> list of word dicts
    {word, start, end} in seconds relative to the video start (aligned)."""
    caps = []
    for ln in lines:
        groups = split_caption_groups(ln['text'])
        if not groups:
            continue
        ws_all = words(ln['text'])
        aligned = (timings or {}).get(ln['id'])
        if aligned and len(aligned) >= len(ws_all) > 0:
            i = 0
            for g in groups:
                n = len(words(g))
                seg = aligned[i:i + n] if n else aligned[i:i + 1]
                i += n
                if not seg:
                    continue
                caps.append({'line_id': ln['id'], 'text': g, 'start_frame': int(math.floor(seg[0]['start'] * fps)),
                             'end_frame': int(math.ceil(seg[-1]['end'] * fps)), 'timing': 'aligned'})
        else:
            span = ln['est_end_frame'] - ln['start_frame']
            total = max(1, len(ws_all))
            cursor = ln['start_frame']
            for g in groups:
                n = max(1, len(words(g)))
                dur = int(round(span * n / total))
                caps.append({'line_id': ln['id'], 'text': g, 'start_frame': cursor, 'end_frame': cursor + dur,
                             'timing': 'estimated'})
                cursor += dur
    # Hold each caption until the next one starts if the gap is short, so text
    # does not flicker; enforce a minimum display time.
    caps.sort(key=lambda c: c['start_frame'])
    for i, c in enumerate(caps):
        nxt = caps[i + 1]['start_frame'] if i + 1 < len(caps) else None
        min_end = c['start_frame'] + int(0.45 * fps)
        c['end_frame'] = max(c['end_frame'], min_end)
        if nxt is not None:
            if nxt - c['end_frame'] <= int(0.25 * fps):
                c['end_frame'] = nxt
            c['end_frame'] = min(c['end_frame'], nxt)
        c['id'] = f'c{i + 1:03d}'
    return caps


def compile_plan(plan, *, fps, width, height, characters=None):
    """Plan dict -> compiled manifest dict. Raises ValueError on malformed input.

    Semantic problems are left for ``validate`` to report so the owner sees all
    of them at once.
    """
    if not isinstance(plan, dict):
        raise ValueError('Plan must be an object')
    duration_s = _num(plan.get('duration_s'), 0)
    if duration_s <= 0:
        raise ValueError('Plan needs a positive duration_s')
    D = fr(duration_s, fps)
    m = {
        'schema_version': S.SCHEMA_VERSION,
        'fps': fps, 'width': width, 'height': height, 'duration_frames': D,
        'coordinate_system': S.COORDINATE_SYSTEM,
        'title': str(plan.get('title', ''))[:100],
        'logline': str(plan.get('logline', ''))[:400],
        'story': {k: str((plan.get('story') or {}).get(k, ''))[:600]
                  for k in ('premise', 'conflict', 'stakes', 'reveal', 'ending', 'ending_type')},
        'setting': copy.deepcopy(plan.get('setting') or {}),
        'cast': copy.deepcopy(plan.get('cast') or []),
        'metadata': copy.deepcopy(plan.get('metadata') or {}),
        'inspiration': copy.deepcopy(plan.get('inspiration') or {}),
        'notes': [],
    }
    hook = plan.get('hook') or {}
    payoff = plan.get('payoff') or {}
    m['hook'] = {'id': 'hook', 'text': str(hook.get('text', ''))[:400], 'question': str(hook.get('question', ''))[:300],
                 'end_frame': fr(_num(hook.get('t_end'), 3.0), fps)}
    m['payoff'] = {'resolves': 'hook', 'text': str(payoff.get('text', ''))[:400],
                   'start_frame': fr(_num(payoff.get('t_start'), duration_s * 0.8), fps)}
    m['setting'].setdefault('preset', 'sky_obby')
    m['setting'].setdefault('time_of_day', 'noon')
    m['setting'].setdefault('lighting', 'natural')
    m['setting'].setdefault('props', [])
    for p in m['setting']['props']:
        pos = p.get('position') or [0, 0, 0]
        p['position'] = [_num(x) for x in (list(pos) + [0, 0, 0])[:3]]
        p['rotation'] = _num(p.get('rotation'), 0)
        p['scale'] = _num(p.get('scale'), 1) or 1

    # Shots
    shots = []
    for i, s in enumerate(plan.get('shots') or []):
        cam = copy.deepcopy(s.get('camera') or {})
        shots.append({
            'id': str(s.get('id') or f's{i + 1}'),
            'start_frame': fr(_num(s.get('start_s')), fps),
            'end_frame': fr(_num(s.get('end_s')), fps),
            'renderer': s.get('renderer', 'blender'),
            'transition_in': s.get('transition_in', 'cut'),
            'clip': s.get('clip'),
            'prompt': str(s.get('prompt', ''))[:1500],
            'camera': {
                'subject': cam.get('subject', 'hero'),
                'framing_start': cam.get('framing_start', cam.get('framing', 'medium')),
                'framing_end': cam.get('framing_end', cam.get('framing_start', cam.get('framing', 'medium'))),
                'angle': cam.get('angle', 'eye'),
                'side': cam.get('side', 'front'),
                'move': cam.get('move', 'static'),
                'ease': cam.get('ease', 'in_out'),
                'shake': _num(cam.get('shake'), 0),
                'lens_mm': (_num(cam.get('lens_mm'), 0) or None),
            },
        })
    shots.sort(key=lambda s: s['start_frame'])
    # Snap rounding seams of up to one frame between consecutive shots.
    for a, b in zip(shots, shots[1:]):
        if abs(a['end_frame'] - b['start_frame']) <= 1:
            a['end_frame'] = b['start_frame']
    if shots and abs(shots[-1]['end_frame'] - D) <= 1:
        shots[-1]['end_frame'] = D
    m['shots'] = shots

    # Character tracks
    cast_ids = [c.get('id') for c in m['cast']]
    perf = plan.get('performance') or {}
    tracks = {}
    for cid in cast_ids:
        raw = sorted((k for k in perf.get(cid, []) if isinstance(k, dict)), key=lambda k: _num(k.get('t')))
        keys = []
        prev = copy.deepcopy(S.DEFAULT_POSE)
        if not raw or _num(raw[0].get('t')) > 0:
            keys.append({'frame': 0, 'pose': copy.deepcopy(prev), 'auto': True, 'explicit': {'position', 'facing'}})
            m['notes'].append(f'{cid}: no pose at frame 0; default standing pose inserted')
        for k in raw:
            f = fr(_num(k.get('t')), fps)
            pose = resolve_pose(prev, k)
            entry = {'frame': f, 'pose': pose, 'explicit': {x for x in ('position', 'facing') if k.get(x) is not None}}
            if k.get('blend_frames') is not None:
                entry['blend_frames'] = max(1, int(_num(k['blend_frames'], 8)))
            if k.get('note'):
                entry['note'] = str(k['note'])[:300]
            if keys and keys[-1]['frame'] == f:
                keys[-1] = entry
            else:
                keys.append(entry)
            prev = pose
        tracks[cid] = {'keys': keys}

    # Actions
    actions = []
    for i, a in enumerate(plan.get('actions') or []):
        start = fr(_num(a.get('t')), fps)
        ph = {k: max(0, int(_num(a.get(k), d))) for k, d in
              (('anticipation_frames', 4), ('main_frames', 12), ('follow_through_frames', 6), ('hold_frames', 0))}
        act = {'id': str(a.get('id') or f'a{i + 1}'), 'character': a.get('character'), 'type': a.get('type'),
               'start_frame': start, **ph, 'params': copy.deepcopy(a.get('params') or {})}
        act['end_frame'] = start + sum(ph.values())
        actions.append(act)
    actions.sort(key=lambda a: a['start_frame'])
    # Locomotion and turns need end keys so the track and the action agree.
    for a in actions:
        if a['character'] not in tracks:
            continue
        keys = tracks[a['character']]['keys']
        end_f = a['start_frame'] + a['anticipation_frames'] + a['main_frames']
        patch = {}
        if a['type'] in S.LOCOMOTION and a['params'].get('to') is not None:
            to = a['params']['to']
            patch['position'] = [_num(to[0]), _num(to[1])]
            a['params']['from'] = pose_at(keys, a['start_frame'])['position']
        if a['type'] == 'turn' and a['params'].get('to_facing') is not None:
            patch['facing'] = _num(a['params']['to_facing'])
            a['params']['from_facing'] = pose_at(keys, a['start_frame'])['facing']
        if not patch:
            continue
        existing = [k for k in keys if k['frame'] == end_f]
        if existing:
            existing[0]['pose'].update(patch)
            existing[0].setdefault('explicit', set()).update(patch)
        else:
            base = pose_at(keys, end_f)
            base.update(patch)
            keys.append({'frame': end_f, 'pose': base, 'auto': True, 'explicit': set(patch), 'blend_frames': 1})
            keys.sort(key=lambda k: k['frame'])
    # Propagate position/facing forward to keys that did not set them, so a
    # later expression key does not snap the character back.
    for tr in tracks.values():
        cur = {}
        for k in tr['keys']:
            ex = k.get('explicit', set())
            for field in ('position', 'facing'):
                if field in ex or field not in cur:
                    cur[field] = copy.deepcopy(k['pose'][field])
                else:
                    k['pose'][field] = copy.deepcopy(cur[field])
        for k in tr['keys']:
            k['explicit'] = sorted(k.get('explicit', set()))
            k.setdefault('auto', False)
    # Start states are read only after propagation, so a second turn starts
    # from the facing the first turn ended on.
    for a in actions:
        if a['character'] not in tracks:
            continue
        keys = tracks[a['character']]['keys']
        start = pose_at(keys, a['start_frame'])
        if a['type'] in S.LOCOMOTION and a['params'].get('to') is not None:
            a['params']['from'] = start['position']
        if a['type'] == 'turn' and a['params'].get('to_facing') is not None:
            a['params']['from_facing'] = start['facing']
    m['tracks'] = {'characters': tracks, 'actions': actions}

    # Prop events from grab/drop actions plus explicit events
    events = []
    for a in actions:
        hand = a['params'].get('hand', 'right')
        if a['type'] == 'grab':
            events.append({'frame': a['start_frame'] + a['anticipation_frames'] + a['main_frames'], 'prop': a['params'].get('prop'),
                           'event': 'attach', 'character': a['character'], 'hand': hand, 'action': a['id']})
        if a['type'] == 'drop':
            events.append({'frame': a['start_frame'] + a['anticipation_frames'], 'prop': a['params'].get('prop'),
                           'event': 'detach', 'character': a['character'], 'hand': hand, 'action': a['id']})
    for e in plan.get('props_events') or []:
        events.append({'frame': fr(_num(e.get('t')), fps), 'prop': e.get('prop'), 'event': e.get('event'),
                       'character': e.get('character'), 'hand': e.get('hand', 'right')})
    events.sort(key=lambda e: e['frame'])
    m['tracks']['props'] = events

    # Blinks
    blinks = {}
    for cid in cast_ids:
        authored = [fr(_num(t), fps) for t in ((plan.get('blinks') or {}).get(cid) or [])]
        closed = []
        keys = tracks[cid]['keys']
        for k0, k1 in zip(keys, keys[1:] + [{'frame': D}]):
            if k0['pose']['eyes']['open'] < 0.45:
                closed.append((k0['frame'], k1['frame']))
        for a in actions:
            if a['character'] == cid and a['type'] == 'turn':
                authored.append(a['start_frame'] + a['anticipation_frames'])
        blinks[cid] = auto_blinks(cid, D, fps, authored, closed)
    m['tracks']['blinks'] = blinks

    # Lines
    lines = []
    for i, ln in enumerate(plan.get('lines') or []):
        line = {'id': str(ln.get('id') or f'l{i + 1}'), 'speaker': ln.get('speaker', 'narrator'),
                'text': str(ln.get('text', '')).strip()[:400], 'start_frame': fr(_num(ln.get('t')), fps),
                'emotion': ln.get('emotion', 'neutral'), 'pace': ln.get('pace', 'normal'),
                'volume': ln.get('volume', 'normal'), 'pause_after_ms': int(_num(ln.get('pause_after_ms'), 0)),
                'delivery': str(ln.get('delivery', ''))[:300]}
        line['est_frames'] = estimate_line_frames(line, fps)
        line['est_end_frame'] = line['start_frame'] + line['est_frames']
        lines.append(line)
    lines.sort(key=lambda l: l['start_frame'])
    for i, ln in enumerate(lines):
        nxt = lines[i + 1]['start_frame'] if i + 1 < len(lines) else D
        ln['window_end_frame'] = nxt
    m['lines'] = lines
    m['captions'] = captions_for(lines, fps)
    m['sfx'] = [{'id': f'x{i + 1}', 'cue': x.get('cue'), 'frame': fr(_num(x.get('t')), fps),
                 'gain_db': _num(x.get('gain_db'), -6)} for i, x in enumerate(plan.get('sfx') or [])]
    m['music'] = [{'frame': fr(_num(x.get('t')), fps), 'cue': x.get('cue', 'playful')}
                  for x in (plan.get('music') or [{'t': 0, 'cue': 'playful'}])]
    m['cover_frame'] = min(D - 1, max(0, fr(_num(plan.get('cover_t'), min(2.0, duration_s / 3)), fps)))

    m['beats'] = derive_beats(m, plan.get('beats') or [])
    return m


def _shot_at(m, f):
    for s in m['shots']:
        if s['start_frame'] <= f < s['end_frame']:
            return s
    return None


def camera_state(shot, f):
    span = max(1, shot['end_frame'] - shot['start_frame'])
    u = _ease((f - shot['start_frame']) / span, shot['camera']['ease'])
    cam = shot['camera']
    order = S.FRAMINGS
    a = order.index(cam['framing_start']) if cam['framing_start'] in order else order.index('medium')
    b = order.index(cam['framing_end']) if cam['framing_end'] in order else a
    ha, hb = S.FRAMING_HEIGHT[order[a]], S.FRAMING_HEIGHT[order[b]]
    h = ha + (hb - ha) * u
    nearest = min(order, key=lambda k: abs(S.FRAMING_HEIGHT[k] - h))
    return {'shot': shot['id'], 'framing': nearest, 'visible_height_ratio': round(h, 3), 'subject': cam['subject'],
            'angle': cam['angle'], 'side': cam['side'], 'move': cam['move'], 'progress': round(u, 3)}


def active_actions(m, cid, a_frame, b_frame):
    out = []
    for a in m['tracks']['actions']:
        if a['character'] != cid or a['end_frame'] <= a_frame or a['start_frame'] >= b_frame:
            continue
        phases = []
        cur = a['start_frame']
        for name in ('anticipation', 'main', 'follow_through', 'hold'):
            n = a[name + '_frames']
            if n and cur < b_frame and cur + n > a_frame:
                phases.append({'phase': name, 'start_frame': max(cur, a_frame), 'end_frame': min(cur + n, b_frame)})
            cur += n
        out.append({'action': a['id'], 'type': a['type'], 'params': a['params'], 'phases': phases,
                    'starts_here': a_frame <= a['start_frame'] < b_frame})
    return out


def derive_beats(m, authored):
    fps, D = m['fps'], m['duration_frames']
    cuts = sorted({s['start_frame'] for s in m['shots']} | {s['end_frame'] for s in m['shots']})
    bounds = sorted(set(list(range(0, D, fps)) + [c for c in cuts if 0 < c < D] + [D]))
    by_sec = {}
    for b in authored:
        if isinstance(b, dict):
            by_sec[int(_num(b.get('start_s', b.get('t', 0))))] = b
    beats = []
    last_auth = {}
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        sec = a // fps
        auth = by_sec.get(sec)
        inherited = auth is None
        if auth is None:
            auth = last_auth
        else:
            last_auth = auth
        shot = _shot_at(m, a)
        beat = {
            'id': f'b{len(beats) + 1:03d}', 'index': len(beats), 'start_frame': a, 'end_frame': b,
            'start_tc': tc(a, fps), 'end_tc': tc(b, fps),
            'shot': shot['id'] if shot else None,
            'purpose': auth.get('purpose', ''), 'purpose_inherited': inherited,
            'description': str(auth.get('description', ''))[:600] if not inherited else '',
            'setting': {'preset': m['setting'].get('preset'), 'time_of_day': m['setting'].get('time_of_day'),
                        'lighting': (auth.get('lighting') or m['setting'].get('lighting')),
                        'notes': str(auth.get('setting_notes', ''))[:300],
                        'props_visible': [p['id'] for p in m['setting'].get('props', [])]},
            'characters': [],
            'camera': None,
            'dialogue': [], 'vocal': [], 'sfx': [], 'music': None, 'captions': [],
            'continuity': list(auth.get('continuity') or []) if not inherited else [],
            'qa': list(auth.get('qa') or []) if not inherited else [],
            'keyframes': [],
        }
        if shot:
            beat['camera'] = {'start': camera_state(shot, a), 'end': camera_state(shot, max(a, b - 1)),
                              'move': shot['camera']['move'], 'ease': shot['camera']['ease'],
                              'renderer': shot['renderer']}
        for c in m['cast']:
            cid = c['id']
            keys = m['tracks']['characters'][cid]['keys']
            p0, p1 = pose_at(keys, a), pose_at(keys, b - 1)
            beat['characters'].append({
                'id': cid, 'start': p0, 'end': p1,
                'actions': active_actions(m, cid, a, b),
                'blinks': [f for f in m['tracks']['blinks'].get(cid, []) if a <= f < b],
                'expression_label': p1.get('expression', 'neutral'),
            })
            for k in keys:
                if a < k['frame'] < b:
                    beat['keyframes'].append({'frame': k['frame'], 'character': cid,
                                              'expression': k['pose'].get('expression'), 'note': k.get('note', '')})
        for ln in m['lines']:
            if ln['start_frame'] < b and ln['est_end_frame'] > a:
                beat['dialogue'].append(ln['id'])
                beat['vocal'].append({'line': ln['id'], 'speaker': ln['speaker'], 'emotion': ln['emotion'],
                                      'pace': ln['pace'], 'volume': ln['volume'], 'pause_after_ms': ln['pause_after_ms']})
        beat['sfx'] = [x['id'] for x in m['sfx'] if a <= x['frame'] < b]
        cue = None
        for mu in m['music']:
            if mu['frame'] <= a:
                cue = mu['cue']
        beat['music'] = cue
        beat['captions'] = [c['id'] for c in m['captions'] if c['start_frame'] < b and c['end_frame'] > a]
        beat['qa'] += auto_qa(m, beat)
        beats.append(beat)
    return beats


def auto_qa(m, beat):
    out = []
    for ch in beat['characters']:
        for act in ch['actions']:
            if act['starts_here']:
                out.append({'check': 'action_occurs', 'character': ch['id'], 'action': act['type'],
                            'auto': True})
                if act['type'] in S.LOCOMOTION or act['type'] in ('turn', 'dance', 'celebrate'):
                    out.append({'check': 'feet_grounded', 'character': ch['id'], 'auto': True})
                if act['type'] in ('grab', 'push', 'reach'):
                    out.append({'check': 'prop_contact', 'character': ch['id'], 'prop': act['params'].get('prop'),
                                'hand': act['params'].get('hand', 'right'), 'auto': True})
        if ch['start'].get('expression') != ch['end'].get('expression') or any(
                k['character'] == ch['id'] and k.get('expression') for k in beat['keyframes']):
            out.append({'check': 'expression_visible', 'character': ch['id'], 'expression': ch['expression_label'],
                        'auto': True})
    if beat['dialogue']:
        out.append({'check': 'dialogue_audible', 'lines': list(beat['dialogue']), 'auto': True})
    return out


def update_line_timing(m, measured):
    """Apply measured line durations/alignments after TTS.

    measured: line_id -> {'duration_s': float, 'words': [{'word','start','end'}] (relative to line start) or None}
    """
    fps = m['fps']
    timings = {}
    for ln in m['lines']:
        info = measured.get(ln['id'])
        if not info:
            continue
        ln['measured_frames'] = int(math.ceil(info['duration_s'] * fps))
        ln['est_end_frame'] = ln['start_frame'] + ln['measured_frames']
        if info.get('words'):
            off = ln['start_frame'] / fps
            timings[ln['id']] = [{'word': w['word'], 'start': off + w['start'], 'end': off + w['end']}
                                 for w in info['words']]
    m['captions'] = captions_for(m['lines'], fps, timings)
    for b in m['beats']:
        b['captions'] = [c['id'] for c in m['captions'] if c['start_frame'] < b['end_frame'] and c['end_frame'] > b['start_frame']]
        b['dialogue'] = [ln['id'] for ln in m['lines'] if ln['start_frame'] < b['end_frame'] and ln['est_end_frame'] > b['start_frame']]
    return m
