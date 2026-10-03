"""Production steps as plain functions (no database), shared by tasks and the CLI.

voice lines -> retime/fit -> manifest timing update -> solve motion ->
render shots -> assemble. Each step writes its outputs to disk so the queue
can resume or repair a single line/shot.
"""
import copy
import json
import math
import os

from .animation import blender as BL, solver as SV
from .assembly import assemble
from .manifest import compile as C, schema as S, validate as V
from .voice import tts


def cast_characters(m, characters):
    """cast id -> {'name', 'voice', 'bible'} from character rows keyed by character id."""
    out = {}
    for c in m['cast']:
        ch = characters.get(c['character_id']) or {}
        out[c['id']] = {'id': c['character_id'], 'name': ch.get('name', c['id']), 'voice': ch.get('voice', {}),
                        'bible': ch.get('bible', {})}
    return out


def voice_lines(m, cast, prefs, work_dir, video_id, only=None, attempts=None, on_line=None):
    results = {}
    for ln in m['lines']:
        if only and ln['id'] not in only:
            continue
        who = cast.get(ln['speaker']) if ln['speaker'] != 'narrator' else {'name': 'Narrator', 'voice': prefs.get('narrator_voice', {})}
        results[ln['id']] = tts.synthesize_line(ln, who, prefs, work_dir, video_id,
                                                attempt=(attempts or {}).get(ln['id'], 0))
        if on_line:
            on_line(ln['id'])
    return results


def retime(m, results, prefs, max_shift_s=0.6):
    """Fit measured line durations into the timeline.

    Order of preference: (1) it fits; (2) a tempo change (<= max_tempo, or at a
    production pace up to the pace, never above 1.5x with the speech rate);
    (3) start the following line later (bounded, never past the end);
    (4) report the line as too long so it is rewritten and re-voiced.
    Returns (manifest, results, report).
    """
    m = copy.deepcopy(m)
    fps = m['fps']
    D = m['duration_frames']
    report = []
    lines = m['lines']
    # Gentle tempo (max_tempo), or at a production pace up to the pace, never
    # above MAX_LINE_SPEEDUP in total with the native speech rate.
    max_tempo = C.tempo_cap(m, prefs['production']['max_tempo'])
    for i, ln in enumerate(lines):
        r = results.get(ln['id'])
        if not r:
            continue
        nxt = lines[i + 1]['start_frame'] if i + 1 < len(lines) else D
        window = (nxt - ln['start_frame']) / fps - (ln.get('pause_after_ms', 0) / 1000.0)
        fitted, action = tts.fit_line(r, window, max_tempo, os.path.dirname(r['file']))
        if action == 'too_long' and i + 1 < len(lines):
            need = r['duration_s'] - window
            cap = lines[i + 2]['start_frame'] if i + 2 < len(lines) else D
            room = (cap - nxt) / fps - (results.get(lines[i + 1]['id'], {}).get('duration_s', 0) or
                                        lines[i + 1]['est_frames'] / fps)
            if need <= max_shift_s and need <= room - 0.1:
                shift = int(math.ceil(need * fps)) + 2
                lines[i + 1]['start_frame'] += shift
                report.append({'line': ln['id'], 'action': 'shift_next', 'frames': shift})
                action = 'fits'
                fitted = r
        elif action == 'too_long' and i + 1 == len(lines):
            if ln['start_frame'] + r['duration_s'] * fps <= D:
                action = 'fits'
                fitted = r
        if action == 'tempo':
            report.append({'line': ln['id'], 'action': 'tempo', 'ratio': fitted.get('tempo')})
        if action == 'too_long':
            report.append({'line': ln['id'], 'action': 'rewrite_needed', 'needed_s': r['duration_s'],
                           'window_s': round(window, 3)})
        results[ln['id']] = fitted
    for i, ln in enumerate(lines):
        ln['window_end_frame'] = lines[i + 1]['start_frame'] if i + 1 < len(lines) else D
    measured = {lid: {'duration_s': r['duration_s'], 'words': [
        {'word': w.get('caption_word', w['word']), 'start': w['start'], 'end': w['end']} for w in r['words']]}
        for lid, r in results.items()}
    C.update_line_timing(m, measured)
    return m, results, report


def alignments(results):
    return {lid: {'words': r['words'], 'kind': r['alignment_kind']} for lid, r in results.items()}


def envelopes(m, results):
    """Per-speaker, per-frame speech amplitude (0..1) from the voiced files.

    Mouth opening follows this measured envelope, so lip motion is tied to the
    actual audio rather than to estimated word timing alone.
    """
    import numpy as np
    from .voice import audio as A
    fps, n = m['fps'], m['duration_frames']
    out = {}
    for ln in m['lines']:
        if not S.lip_synced(ln):
            continue  # narration is voice-over: the narrator's jaw does not follow it
        r = (results or {}).get(ln['id'])
        if not r or not r.get('file') or not os.path.exists(r['file']):
            continue
        s = A.decode(r['file'])
        hop = A.SR / fps
        k = int(len(s) / hop)
        env = np.array([np.sqrt(np.mean(s[int(i * hop):int((i + 1) * hop)] ** 2) + 1e-12) for i in range(k)])
        if not len(env):
            continue
        ref = np.percentile(env, 95) or 1.0
        env = np.clip(env / ref, 0, 1) ** 0.8
        # Light attack/release smoothing so the jaw does not chatter.
        sm = np.zeros_like(env)
        g = 0.0
        for i, x in enumerate(env):
            g += (x - g) * (0.75 if x > g else 0.45)
            sm[i] = g
        arr = out.setdefault(ln['speaker'], np.zeros(n))
        a0 = ln['start_frame']
        b0 = min(n, a0 + len(sm))
        arr[a0:b0] = np.maximum(arr[a0:b0], sm[:b0 - a0])
    return out


def solve(m, cast, results=None):
    bibles = {cid: c['bible'] for cid, c in cast.items()}
    return SV.solve(m, bibles, alignments(results or {}), envelopes=envelopes(m, results))


def render_shots(m, cast, solved, prefs, work_dir, quality='final', only=None, on_progress=None, should_stop=None):
    bibles = {cid: c['bible'] for cid, c in cast.items()}
    out = {}
    for s in m['shots']:
        if only and s['id'] not in only:
            continue
        if s['renderer'] != 'blender':
            continue
        sd = os.path.join(work_dir, 'shots', s['id'])
        out[s['id']] = BL.render_range(m, bibles, solved, s['start_frame'], s['end_frame'], sd, prefs, quality,
                                       on_progress=(lambda msg, sid=s['id']: on_progress(sid, msg)) if on_progress else None,
                                       should_stop=should_stop)
    return out


def produce_local(plan, characters, prefs, work_dir, video_id='local', quality='preview', log=print):
    """One-shot local production of a plan (used by the demo CLI and tests)."""
    pr = prefs['production']
    m = C.compile_plan(plan, fps=pr['fps'], width=pr['width'], height=pr['height'], **C.pace_kwargs(prefs))
    rep = V.validate(m, prefs)
    if not rep['ok']:
        raise ValueError('Manifest invalid: ' + '; '.join(e['message'] for e in rep['errors'][:5]))
    cast = cast_characters(m, characters)
    os.makedirs(work_dir, exist_ok=True)
    log('voicing lines')
    results = voice_lines(m, cast, prefs, os.path.join(work_dir, 'voice'), video_id)
    m, results, retime_report = retime(m, results, prefs)
    rep2 = V.validate(m, prefs, measured=True)
    log('solving motion')
    solved = solve(m, cast, results)
    log('rendering shots')
    shots = render_shots(m, cast, solved, prefs, work_dir, quality,
                         on_progress=lambda sid, msg: log(f'{sid}: {msg}') if 'progress' in msg and msg.endswith('s') and '1/' in msg else None)
    shot_files = {sid: r['video'] for sid, r in shots.items()}
    log('assembling')
    out = assemble(m, shot_files, results, prefs, os.path.join(work_dir, 'render'), m['title'],
                   telemetry_paths=[r['telemetry'] for r in shots.values()])
    with open(os.path.join(work_dir, 'manifest.json'), 'w') as f:
        json.dump(m, f, indent=1)
    with open(os.path.join(work_dir, 'solved.json'), 'w') as f:
        json.dump(solved, f)
    return {'manifest': m, 'validation': rep2, 'retime': retime_report, 'lines': results, 'shots': shots,
            'assembly': out, 'solved_path': os.path.join(work_dir, 'solved.json')}
