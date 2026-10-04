"""Compile an authoring plan into the authoritative frame-indexed manifest.

Authoring plans (from the LLM, the editor or a hand-written file) use seconds
for convenience. Compilation converts every time to an integer frame once,
resolves partial poses into complete poses, derives one-second narrative beats
(split at shot boundaries so a beat never straddles a cut) and lists, for every
beat, the fully resolved start/end state of each character and the camera.

Tracks (character keys, actions, camera, lines, sfx, music, prop events) are
what the renderer executes. Beats are an explicit, per-second description of
that same timeline used by the director's script, the editor and QA.

Pace: the plan is authored in "story time". ``compile_plan(..., pace=1.5)``
divides every authored time quantity (seconds and frame counts) by the pace
before it becomes a frame index, so the same story plays 1.5 times faster
(the implicit 10-frame pose blend is authored time too and is compressed).
Perceptual minimums that are checked in real time (the readable-expression
minimum, blink length, caption display time and the natural blink interval)
are deliberately not scaled. A compressed walk that is now too fast for a
walk becomes a run, and a compressed jump keeps the shortest plausible
airtime; both are noted in the manifest. Beats stay one second of story time
each, so every authored beat keeps its own slot.
"""
import bisect
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
    if 'pupil' in a['eyes'] or 'pupil' in b['eyes']:
        # Optional control (absent = 1): blend it like the other numbers instead of switching at u = 0.5.
        out['eyes']['pupil'] = round(_lerp(a['eyes'].get('pupil', 1.0), b['eyes'].get('pupil', 1.0), e), 4)
    return out


DEFAULT_BLEND_FRAMES = 10


def pose_at(keys, frame, default_blend=DEFAULT_BLEND_FRAMES):
    """Resolved pose of a character track at ``frame`` (keys sorted by frame).

    A key without ``blend_frames`` transitions over ``default_blend`` frames
    (at most the gap to the previous key). Manifests compiled at a pace other
    than 1 carry the compressed default on every key."""
    if not keys:
        return copy.deepcopy(S.DEFAULT_POSE)
    if frame <= keys[0]['frame']:
        return copy.deepcopy(keys[0]['pose'])
    for i in range(len(keys) - 1):
        k0, k1 = keys[i], keys[i + 1]
        if k0['frame'] <= frame <= k1['frame']:
            span = max(1, k1['frame'] - k0['frame'])
            # Hold the earlier pose, then transition over the final frames.
            blend = k1.get('blend_frames') or min(span, default_blend)
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


def estimate_line_frames(line, fps, speech_rate=None):
    """Planned length of a line. The voice speaks natively at ``speech_rate``
    (default: the rate the line was compiled with), which shortens words and
    their punctuation pauses alike."""
    rate = line.get('speech_rate', 1.0) if speech_rate is None else speech_rate
    rate = _num(rate, 1.0) or 1.0
    n = max(1, len(words(line['text'])))
    wps = S.PACE_WPS.get(line.get('pace', 'normal'), 2.7)
    punct = (len(re.findall(r'[,;:]', line['text'])) * 0.18
             + len(re.findall(r'\.\.\.|…|[.!?]+', line['text'])) * 0.25)
    secs = (n / wps + punct) / rate + 0.15
    return int(math.ceil(secs * fps))


def check_pace(pace, speech_rate):
    """Validate the pace arguments of compile_plan. Returns (pace, speech_rate) as floats."""
    out = []
    for name, v, (lo, hi) in (('pace', pace, S.PACE_RANGE), ('speech_rate', speech_rate, S.SPEECH_RATE_RANGE)):
        try:
            x = float(v)
        except (TypeError, ValueError):
            raise ValueError(f'{name} must be a number') from None
        if not math.isfinite(x) or not lo <= x <= hi:
            raise ValueError(f'{name} {v!r} is outside {lo}-{hi}')
        out.append(x)
    return tuple(out)


def pace_kwargs(prefs):
    """compile_plan keyword arguments for the owner's production pace.

    A prefs dict without pace settings (hand-built in tests or tools) compiles
    at real time, which is the behaviour before pace existed."""
    pr = (prefs or {}).get('production') or {}
    return {'pace': pr.get('pace', 1.0), 'speech_rate': pr.get('speech_rate', 1.0)}


def manifest_pace(m):
    """Timeline pace a compiled manifest was built with (1.0 for older manifests)."""
    return float((m.get('pace') or {}).get('timeline', 1.0))


def line_fit_allowance(m):
    """Extra speed-up (beyond the native speech rate) line fitting may apply to
    a planned line so it fits a slot compressed by the pace.

    A line that fitted its slot in story time fits again when voiced as fast
    as the timeline runs, so the allowance tops the total up to the pace, but
    never above ``MAX_LINE_SPEEDUP`` in total. At pace 1 it is 1.0 (none)."""
    p = m.get('pace') or {}
    pace, rate = float(p.get('timeline', 1.0)), float(p.get('speech_rate', 1.0))
    return max(1.0, min(pace, S.MAX_LINE_SPEEDUP) / rate)


def tempo_cap(m, max_tempo):
    """Largest tempo change line fitting may apply to a voiced line: the
    owner's gentle ``max_tempo`` or the pace allowance, whichever is larger,
    so that native speech rate times tempo never exceeds MAX_LINE_SPEEDUP
    (unless the speech rate alone already does, in which case no tempo change
    is allowed)."""
    rate = float((m.get('pace') or {}).get('speech_rate', 1.0))
    return min(max(float(max_tempo), line_fit_allowance(m)), max(1.0, S.MAX_LINE_SPEEDUP / rate))


def _phase_lengths(start_frame, start_story_frame, lengths, pace):
    """Compress action phase lengths (story frames) by ``pace``.

    ``start_frame`` is the compiled start, ``start_story_frame`` the authored
    start in story frames. Phase boundaries are converted from their absolute
    story frame, so they round the same way as performance keys authored at
    the same moment. A phase that existed keeps at least one frame and the
    main phase keeps the two frames the rig needs, unless the author gave it
    fewer (the validator then reports it as before)."""
    if pace == 1.0:
        return list(lengths)
    out, cum, pos = [], 0, start_frame
    for i, n in enumerate(lengths):
        cum += n
        length = max(0, int(round((start_story_frame + cum) / pace)) - pos)
        if n > 0:
            length = max(length, min(n, 2 if i == 1 else 1))
        out.append(length)
        pos += length
    return out


CAPTION_MAX_WORDS = 3   # punchy 1-3 word chunks
CAPTION_MAX_CHARS = 16  # mostly one line at the default caption size


def split_caption_groups(text, max_words=CAPTION_MAX_WORDS, max_chars=CAPTION_MAX_CHARS):
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


def _token_spans(tokens, spans):
    """Give each whitespace token of a caption the [start, end) of its spoken words. ``spans`` lists one
    (start, end) per spoken word (WORD_RE words) of the caption in order; a token without letters or
    digits (a dash, an ellipsis) shares the span of the token before it (or after it, when first)."""
    out, i = [], 0
    for tok in tokens:
        n = len(words(tok))
        if n and i < len(spans):
            seg = spans[i:i + n]
            out.append([seg[0][0], seg[-1][1]])
            i += n
        else:
            out.append(None)
    for k in range(len(out)):
        if out[k] is None:
            out[k] = list(out[k - 1]) if k and out[k - 1] else None
    for k in range(len(out) - 1, -1, -1):
        if out[k] is None and k + 1 < len(out) and out[k + 1]:
            out[k] = list(out[k + 1])
    return out


def _estimated_spans(tokens, start, end):
    """Spread a caption's tokens over [start, end) by length (longer words take longer to say)."""
    weight = [len(re.sub(r'[^A-Za-z0-9]', '', t)) + 2 for t in tokens]
    total = float(sum(weight)) or 1.0
    out, acc = [], 0.0
    for w in weight:
        a = start + (end - start) * acc / total
        acc += w
        out.append([a, start + (end - start) * acc / total])
    return out


def captions_for(lines, fps, timings=None):
    """Caption groups per line. ``timings`` maps line_id -> list of word dicts
    {word, start, end} in seconds relative to the video start (aligned).

    Every caption lists its words with frames ('words': [{text, start_frame, end_frame}]) so the active
    word can be highlighted: measured when the line is aligned, else estimated from word lengths."""
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
                spans = _token_spans(g.split(), [(w['start'] * fps, w['end'] * fps) for w in seg])
                caps.append({'line_id': ln['id'], 'text': g, 'start_frame': int(math.floor(seg[0]['start'] * fps)),
                             'end_frame': int(math.ceil(seg[-1]['end'] * fps)), 'timing': 'aligned',
                             'word_spans': spans})
        else:
            span = ln['est_end_frame'] - ln['start_frame']
            total = max(1, len(ws_all))
            cursor = ln['start_frame']
            for g in groups:
                n = max(1, len(words(g)))
                dur = int(round(span * n / total))
                caps.append({'line_id': ln['id'], 'text': g, 'start_frame': cursor, 'end_frame': cursor + dur,
                             'timing': 'estimated', 'word_spans': _estimated_spans(g.split(), cursor, cursor + dur)})
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
        c['words'] = _caption_words(c)
    return caps


def _caption_words(c):
    """Active-word timing inside the caption's display time: each word is active from its own start (the
    first from the caption start) until the next word starts (the last until the caption ends), so exactly
    one word is highlighted at any moment the caption shows."""
    tokens = c['text'].split()
    spans = c.pop('word_spans', None) or _estimated_spans(tokens, c['start_frame'], c['end_frame'])
    a, b = c['start_frame'], c['end_frame']
    starts = [min(b - 1, max(a, int(round(sp[0])))) if sp else a for sp in spans]
    for k in range(1, len(starts)):
        starts[k] = max(starts[k], starts[k - 1])
    out = []
    for k, tok in enumerate(tokens):
        end = starts[k + 1] if k + 1 < len(tokens) else b
        out.append({'text': tok, 'start_frame': starts[k] if k else a, 'end_frame': end})
    return out


def compile_plan(plan, *, fps, width, height, characters=None, pace=1.0, speech_rate=1.0):
    """Plan dict -> compiled manifest dict. Raises ValueError on malformed input.

    Semantic problems are left for ``validate`` to report so the owner sees all
    of them at once.

    ``pace`` compresses the story-time plan into video time (every authored
    second and frame count is divided by it); ``speech_rate`` is how much
    faster than normal the voices speak, used for line length estimates and
    passed to the voice engines on every line. The defaults compile at real
    time.
    """
    if not isinstance(plan, dict):
        raise ValueError('Plan must be an object')
    pace, speech_rate = check_pace(pace, speech_rate)
    duration_s = _num(plan.get('duration_s'), 0)
    if duration_s <= 0:
        raise ValueError('Plan needs a positive duration_s')

    def F(t, default=0.0):
        """Story seconds -> compiled frame."""
        return fr(_num(t, default) / pace, fps)

    def N(n):
        """Story frame count -> compiled frame count."""
        return int(round(n / pace)) if pace != 1.0 else n

    D = F(duration_s)
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
        'pace': {'timeline': pace, 'speech_rate': speech_rate},
        'style': plan_style(plan),
    }
    # Storytime: the cast id whose voice reads the narration lines (voice-over, no lip sync).
    narrator = plan.get('narrator')
    if narrator not in (None, ''):
        m['narrator'] = str(narrator)
    hook = plan.get('hook') or {}
    payoff = plan.get('payoff') or {}
    m['hook'] = {'id': 'hook', 'text': str(hook.get('text', ''))[:400], 'question': str(hook.get('question', ''))[:300],
                 'end_frame': F(hook.get('t_end'), 3.0)}
    m['payoff'] = {'resolves': 'hook', 'text': str(payoff.get('text', ''))[:400],
                   'start_frame': F(payoff.get('t_start'), duration_s * 0.8)}
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
        if cam.get('move') == 'zoom_punch':
            s0, s1 = F(s.get('start_s')), F(s.get('end_s'))
            punch = {'punch_frames': max(S.ZOOM_PUNCH_MIN_FRAMES, N(int(_num(cam.get('punch_frames'),
                                                                                S.ZOOM_PUNCH_FRAMES))))}
            punch['punch_frame'] = (F(cam['punch_t']) if cam.get('punch_t') is not None
                                    else s0 + int(round((s1 - s0) * 0.3)))
        else:
            punch = {}
        shots.append({
            'id': str(s.get('id') or f's{i + 1}'),
            'start_frame': F(s.get('start_s')),
            'end_frame': F(s.get('end_s')),
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
                **punch,
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
            f = F(k.get('t'))
            pose = resolve_pose(prev, k)
            entry = {'frame': f, 'pose': pose, 'explicit': {x for x in ('position', 'facing') if k.get(x) is not None}}
            if k.get('blend_frames') is not None:
                entry['blend_frames'] = max(1, N(int(_num(k['blend_frames'], 8))))
            if k.get('snap'):
                entry['blend_frames'] = 1  # a face swap: the new pose is on from this frame
            if k.get('note'):
                entry['note'] = str(k['note'])[:300]
            if keys and keys[-1]['frame'] == f:
                keys[-1] = entry
            else:
                keys.append(entry)
            prev = pose
        tracks[cid] = {'keys': keys}
    if m['style']['expression_snap'] == 'cut':
        _snap_expressions_to_cuts(m, tracks, max(1, N(DEFAULT_BLEND_FRAMES)), max(1, N(S.SNAP_WINDOW_FRAMES)))

    # Actions
    actions = []
    story_main = {}
    for i, a in enumerate(plan.get('actions') or []):
        start = F(a.get('t'))
        names = ('anticipation_frames', 'main_frames', 'follow_through_frames', 'hold_frames')
        authored = [max(0, int(_num(a.get(k), d))) for k, d in zip(names, (4, 12, 6, 0))]
        ph = dict(zip(names, _phase_lengths(start, _num(a.get('t')) * fps, authored, pace)))
        act = {'id': str(a.get('id') or f'a{i + 1}'), 'character': a.get('character'), 'type': a.get('type'),
               'start_frame': start, **ph, 'params': copy.deepcopy(a.get('params') or {})}
        act['end_frame'] = start + sum(ph.values())
        story_main[id(act)] = authored[1]
        actions.append(act)
    actions.sort(key=lambda a: a['start_frame'])
    # Locomotion and turns need end keys so the track and the action agree.
    blend0 = max(1, N(DEFAULT_BLEND_FRAMES))
    for a in actions:
        if a['character'] not in tracks:
            continue
        keys = tracks[a['character']]['keys']
        if pace != 1.0 and a['type'] == 'jump' and a['params'].get('to') is not None:
            _keep_jump_plausible(m, a, keys, story_main.get(id(a), a['main_frames']), fps, pace)
        end_f = a['start_frame'] + a['anticipation_frames'] + a['main_frames']
        patch = {}
        if a['type'] in S.LOCOMOTION and a['params'].get('to') is not None:
            to = a['params']['to']
            patch['position'] = [_num(to[0]), _num(to[1])]
            a['params']['from'] = pose_at(keys, a['start_frame'], blend0)['position']
        if a['type'] == 'turn' and a['params'].get('to_facing') is not None:
            patch['facing'] = _num(a['params']['to_facing'])
            a['params']['from_facing'] = pose_at(keys, a['start_frame'], blend0)['facing']
        if not patch:
            continue
        existing = [k for k in keys if k['frame'] == end_f]
        if existing:
            existing[0]['pose'].update(patch)
            existing[0].setdefault('explicit', set()).update(patch)
        else:
            base = pose_at(keys, end_f, blend0)
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
        if pace != 1.0:
            # The implicit expression/pose blend is authored time like an
            # explicit blend_frames, so it is compressed too. Writing it on the
            # keys keeps the solver, validator and QA (which assume the
            # real-time default) in agreement.
            for k0, k1 in zip(tr['keys'], tr['keys'][1:]):
                if not k1.get('blend_frames'):
                    k1['blend_frames'] = max(1, min(blend0, k1['frame'] - k0['frame']))
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
    if pace != 1.0:
        _walks_to_runs(m, actions, story_main, fps, pace)
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
        events.append({'frame': F(e.get('t')), 'prop': e.get('prop'), 'event': e.get('event'),
                       'character': e.get('character'), 'hand': e.get('hand', 'right')})
    events.sort(key=lambda e: e['frame'])
    m['tracks']['props'] = events

    # Blinks
    blinks = {}
    for cid in cast_ids:
        authored = [F(t) for t in ((plan.get('blinks') or {}).get(cid) or [])]
        closed = []
        keys = tracks[cid]['keys']
        for k0, k1 in zip(keys, keys[1:] + [{'frame': D}]):
            if k0['pose']['eyes']['open'] < 0.45 or k0['pose'].get('expression') in S.NO_BLINK_EXPRESSIONS:
                closed.append((k0['frame'], k1['frame']))
        for a in actions:
            if a['character'] == cid and a['type'] == 'turn':
                authored.append(a['start_frame'] + a['anticipation_frames'])
        blinks[cid] = auto_blinks(cid, D, fps, authored, closed)
    m['tracks']['blinks'] = blinks

    # Lines
    lines = []
    for i, ln in enumerate(plan.get('lines') or []):
        kind = str(ln.get('kind') or 'dialogue')[:20]
        # A narration line without a speaker is read by the plan's narrator (or the off-screen narrator voice).
        speaker = ln.get('speaker') or (m.get('narrator', 'narrator') if kind == 'narration' else 'narrator')
        line = {'id': str(ln.get('id') or f'l{i + 1}'), 'speaker': speaker, 'kind': kind,
                'text': str(ln.get('text', '')).strip()[:400], 'start_frame': F(ln.get('t')),
                'emotion': ln.get('emotion', 'neutral'), 'pace': ln.get('pace', 'normal'),
                'volume': ln.get('volume', 'normal'), 'pause_after_ms': int(_num(ln.get('pause_after_ms'), 0) / pace),
                'delivery': str(ln.get('delivery', ''))[:300], 'speech_rate': speech_rate}
        line['est_frames'] = estimate_line_frames(line, fps)
        line['est_end_frame'] = line['start_frame'] + line['est_frames']
        lines.append(line)
    lines.sort(key=lambda l: l['start_frame'])
    for i, ln in enumerate(lines):
        nxt = lines[i + 1]['start_frame'] if i + 1 < len(lines) else D
        ln['window_end_frame'] = nxt
    m['lines'] = lines
    m['captions'] = captions_for(lines, fps)
    m['sfx'] = [{'id': f'x{i + 1}', 'cue': x.get('cue'), 'frame': F(x.get('t')),
                 'gain_db': _num(x.get('gain_db'), -6)} for i, x in enumerate(plan.get('sfx') or [])]
    if m['style']['whoosh_on_cuts']:
        _cut_whooshes(m, fps, pace)
    m['music'] = [{'frame': F(x.get('t')), 'cue': x.get('cue', 'playful')}
                  for x in (plan.get('music') or [{'t': 0, 'cue': 'playful'}])]
    if len(m['music']) == 1:
        _act_turn_sections(m, plan, F)
    m['music_dropouts'] = _music_dropouts(m, plan, F, fps, pace)
    m['effects'] = _effects(plan, F, N)
    m['cover_frame'] = min(D - 1, max(0, F(plan.get('cover_t'), min(2.0, duration_s / 3))))

    m['beats'] = derive_beats(m, plan.get('beats') or [])
    return m


def plan_style(plan):
    """The plan's edit style (schema.STYLE_DEFAULTS filled in; unknown values are kept for the validator)."""
    st = dict(S.STYLE_DEFAULTS)
    raw = plan.get('style') if isinstance(plan.get('style'), dict) else {}
    for k in st:
        if k in raw and raw[k] is not None:
            st[k] = raw[k] if k == 'expression_snap' else bool(raw[k])
    return st


def _snap_expressions_to_cuts(m, tracks, blend0, window):
    """Face swaps on the cut: an expression change whose blend would straddle a cut, or that lands within
    ``window`` frames of one, moves onto the cut frame and switches there in one frame (the old face holds
    to the end of the outgoing shot, the new one opens the next). Keys that also place or turn the character
    (and automatic keys) keep their timing."""
    cuts = sorted({sh['start_frame'] for sh in m['shots'] if sh['start_frame'] > 0})
    if not cuts:
        return
    for cid, tr in tracks.items():
        keys = tr['keys']
        for i in range(1, len(keys)):
            k, prev = keys[i], keys[i - 1]
            if k.get('auto') or k.get('explicit') or k['pose'].get('expression') == prev['pose'].get('expression'):
                continue
            blend = k.get('blend_frames') or min(k['frame'] - prev['frame'], blend0)
            nxt = keys[i + 1]['frame'] if i + 1 < len(keys) else m['duration_frames']
            near = [c for c in cuts if k['frame'] - blend - window <= c <= k['frame'] + window
                    and prev['frame'] < c < nxt]
            if not near:
                continue
            c = min(near, key=lambda x: (abs(x - k['frame']), x))
            if c != k['frame']:
                k['authored_frame'] = k['frame']
                k['frame'] = c
            k['blend_frames'] = 1
            k['snap'] = 'cut'


def _cut_whooshes(m, fps, pace):
    """A soft whoosh on cuts, peaking on the cut frame. Skipped where an authored sound already lands near
    the cut, after a fade, and when the previous whoosh is too close (a whoosh on every 1 s cut is enough)."""
    near, gap = fr(0.3 / pace, fps), fr(0.6 / pace, fps)
    taken = [x['frame'] for x in m['sfx']]
    last = None
    for sh in m['shots']:
        c = sh['start_frame']
        if c <= 0 or sh.get('transition_in') == 'fade_in' or any(abs(t - c) <= near for t in taken):
            continue
        if last is not None and c - last < gap:
            continue
        m['sfx'].append({'id': f'xc{len(m["sfx"]) + 1}', 'cue': 'whoosh', 'frame': c, 'gain_db': S.CUT_WHOOSH_GAIN_DB,
                         'auto': 'cut', 'align': 'peak'})
        last = c


ACT_TURNS = (('inciting', 'escalation'), ('climax', 'reveal'), ('payoff', 'button'))


def _act_turn_sections(m, plan, F):
    """A plan with one music cue gets a new section of that cue at each act turn (the first beat of the
    escalation, the climax or reveal, and the payoff), so the bed changes where the story turns."""
    cue = m['music'][0]['cue']
    if cue == 'none':
        return
    beats = sorted((b for b in (plan.get('beats') or []) if isinstance(b, dict)),
                   key=lambda b: _num(b.get('start_s', b.get('t', 0))))
    after, section = m['music'][0]['frame'], 0
    for group in ACT_TURNS:
        f = next((F(b.get('start_s', b.get('t', 0))) for b in beats if b.get('purpose') in group
                  and F(b.get('start_s', b.get('t', 0))) > after), None)
        if f is None or f >= m['duration_frames']:
            continue
        section += 1
        m['music'].append({'frame': f, 'cue': cue, 'section': section, 'auto': 'act_turn'})
        after = f


def _music_dropouts(m, plan, F, fps, pace):
    """The music stops for a moment before the punchline. The plan may place it ('music_dropout':
    {'t': when the music comes back, 'duration_s'}); otherwise it ends where the first line at or after the
    payoff starts. 'style': {'music_dropout': false} or 'music_dropout': false turns it off."""
    md = plan.get('music_dropout')
    if md is False or not m['style']['music_dropout']:
        return []
    if isinstance(md, dict) and md.get('t') is not None:
        end, src = F(md['t']), 'plan'
        dur = fr(_num(md.get('duration_s'), S.MUSIC_DROPOUT_S) / pace, fps)
    elif (plan.get('payoff') or {}).get('t_start') is not None:
        p = m['payoff']['start_frame']
        end = next((ln['start_frame'] for ln in m['lines'] if ln['start_frame'] >= p), p)
        dur, src = fr(S.MUSIC_DROPOUT_S / pace, fps), 'payoff'
    else:
        return []
    start = max(0, end - max(1, dur))
    if end <= 0 or end > m['duration_frames']:
        return []
    return [{'start_frame': start, 'end_frame': end, 'source': src}]


def _effects(plan, F, N):
    out = []
    for i, e in enumerate(plan.get('effects') or []):
        if not isinstance(e, dict):
            continue
        typ = e.get('type')
        frames = N(int(_num(e.get('frames'), S.EFFECT_DEFAULT_FRAMES.get(typ, 8))))
        eff = {'id': str(e.get('id') or f'e{i + 1}'), 'type': typ, 'frame': F(e.get('t')),
               'frames': max(S.EFFECT_MIN_FRAMES.get(typ, 1), frames)}
        if typ == 'shake':
            eff['strength'] = _num(e.get('strength'), 0.7)
        out.append(eff)
    out.sort(key=lambda e: e['frame'])
    return out


def _keep_jump_plausible(m, a, keys, story_main, fps, pace):
    """A jump's airtime is physics: compressing a plausible jump can make it
    faster than any plausible jump (MAX_SPEED['jump']). Such a jump keeps the
    shortest plausible airtime instead (its landing moves a few frames later,
    noted in the manifest). A jump that was already implausible in story time
    is left for the validator to report."""
    pos = keys[0]['pose']['position'] if keys else S.DEFAULT_POSE['position']
    for k in keys:
        if k['frame'] > a['start_frame']:
            break
        if 'position' in k.get('explicit', ()):
            pos = k['pose']['position']
    try:
        to = a['params']['to']
        dist = math.dist(pos, [_num(to[0]), _num(to[1])])
    except (TypeError, ValueError, IndexError, KeyError):
        return
    limit = S.MAX_SPEED['jump']
    if dist / (max(1, story_main) / fps) > limit or dist / (max(1, a['main_frames']) / fps) <= limit:
        return
    need = max(1, int(math.ceil(dist / limit * fps)) - 1)
    while dist / (need / fps) > limit:  # the validator's exact comparison
        need += 1
    extra = need - a['main_frames']
    if extra <= 0:
        return
    a['main_frames'] = need
    a['end_frame'] += extra
    m['notes'].append(f'{a["character"]}: jump {a["id"]} keeps {need} frames of airtime for {dist:.2f} m at pace '
                      f'{pace:g} (compressed it would be faster than a plausible jump); landing {extra} frame(s) later')


def _walks_to_runs(m, actions, story_main, fps, pace):
    """A walk that was a plausible walking speed in story time but is faster
    than a plausible walk once compressed is performed as a run, if a run at
    that speed is plausible (noted in the manifest). Anything faster stays a
    walk so the validator reports it."""
    walk_max, run_max = S.MAX_SPEED['walk'], S.MAX_SPEED['run']
    for a in actions:
        if a['type'] != 'walk' or a['params'].get('to') is None or a['params'].get('from') is None:
            continue
        try:
            to = a['params']['to']
            dist = math.dist(a['params']['from'], [_num(to[0]), _num(to[1])])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        story_secs = max(1, story_main.get(id(a), a['main_frames'])) / fps
        secs = max(1, a['main_frames']) / fps
        speed = dist / secs
        if dist / story_secs <= walk_max < speed <= run_max:
            a['type'] = 'run'
            a['converted_from'] = 'walk'
            m['notes'].append(f'{a["character"]}: walk {a["id"]} covers {dist:.2f} m in {secs:.2f}s at pace {pace:g} '
                              f'({speed:.1f} m/s, above a plausible walk); performed as a run')


def _shot_at(m, f):
    for s in m['shots']:
        if s['start_frame'] <= f < s['end_frame']:
            return s
    return None


def camera_state(shot, f):
    span = max(1, shot['end_frame'] - shot['start_frame'])
    cam = shot['camera']
    if cam.get('move') == 'zoom_punch':
        # Hold the opening framing, snap in over punch_frames (fast start, soft landing), then hold.
        p0 = cam.get('punch_frame', shot['start_frame'])
        u = _ease((f - p0) / max(1, cam.get('punch_frames', S.ZOOM_PUNCH_FRAMES)), 'out')
    else:
        u = _ease((f - shot['start_frame']) / span, cam['ease'])
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
    """One beat per second of story time (one video second at pace 1, two
    thirds of a second at pace 1.5), split at cuts. An authored beat applies to
    the story second its ``start_s`` falls in."""
    fps, D = m['fps'], m['duration_frames']
    pace = manifest_pace(m)
    sec_starts = []
    while True:
        f = fr(len(sec_starts) / pace, fps)
        if f >= D:
            break
        sec_starts.append(f)
    cuts = sorted({s['start_frame'] for s in m['shots']} | {s['end_frame'] for s in m['shots']})
    bounds = sorted(set(sec_starts + [c for c in cuts if 0 < c < D] + [D]))
    by_sec = {}
    for b in authored:
        if isinstance(b, dict):
            by_sec[int(_num(b.get('start_s', b.get('t', 0))))] = b
    beats = []
    last_auth = {}
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        sec = bisect.bisect_right(sec_starts, a) - 1
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
                beat['vocal'].append({'line': ln['id'], 'speaker': ln['speaker'], 'kind': ln.get('kind', 'dialogue'),
                                      'emotion': ln['emotion'],
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
