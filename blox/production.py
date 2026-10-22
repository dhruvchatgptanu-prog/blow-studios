"""Production steps as plain functions (no database), shared by tasks and the CLI.

voice lines -> retime/fit -> manifest timing update -> solve motion ->
render shots -> assemble. Each step writes its outputs to disk so the queue
can resume or repair a single line/shot.
"""
import copy
import json
import math
import os

from . import config, media
from .animation import blender as BL, solver as SV
from .assembly import assemble
from .manifest import compile as C, schema as S, validate as V
from .util import Blocked, stable_hash
from .voice import tts, voicecfg


def cast_characters(m, characters):
    """cast id -> {'name', 'voice', 'bible'} from character rows keyed by character id."""
    out = {}
    for c in m['cast']:
        ch = characters.get(c['character_id']) or {}
        out[c['id']] = {'id': c['character_id'], 'name': ch.get('name', c['id']), 'voice': ch.get('voice', {}),
                        'bible': ch.get('bible', {})}
    return out


# Voice engines a validation-time measurement may use: free and offline, so validating a story never spends.
FREE_TTS = ('piper', 'local_test')


def measure_lines(m, characters, prefs, work_dir=None):
    """Voice every line through the real line chain and put its exact length on the line.

    Sets ``measured_frames`` (and est_end_frame) like the voice stage does, flagged ``measured_preflight`` so
    the validator still allows the line-fitting tempo on it. Durations are cached by the line's voice spec
    under ``work_dir`` (data/work/measure), so validating the same story again costs nothing. Returns the
    ids of the lines measured; a paid voice provider measures nothing.
    """
    pr = prefs['production']
    if pr['tts_provider'] not in FREE_TTS:
        return []
    cast = cast_characters(m, characters)
    work_dir = work_dir or os.path.join(config.DATA_DIR, 'work', 'measure')
    os.makedirs(work_dir, exist_ok=True)
    index_path = os.path.join(work_dir, 'durations.json')
    try:
        with open(index_path) as f:
            index = json.load(f)
    except (OSError, ValueError):
        index = {}
    measured = {}
    for ln in m['lines']:
        who = cast.get(ln['speaker']) if ln['speaker'] != 'narrator' else {'name': 'Narrator', 'voice': {}}
        if voicecfg.engine_for((who or {}).get('voice'), pr['tts_provider']) not in FREE_TTS + voicecfg.FREE_ENGINES:
            continue  # a character set to a paid engine (e.g. an imported ElevenLabs voice) is never measured
        key = stable_hash([pr['tts_provider'], pr.get('piper_model'), pr['tts_model'], ln['text'], ln['pace'],
                           ln['emotion'], ln['volume'], ln.get('kind', 'dialogue'), ln.get('speech_rate', 1.0),
                           (who or {}).get('voice') or {}, (who or {}).get('id'), pr['pronunciations']])
        if key not in index:
            res = tts.synthesize_line(ln, who, prefs, work_dir, 'measure')
            index[key] = res['duration_s']
            for path in (res['file'], res['raw']):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            with open(index_path, 'w') as f:
                json.dump(index, f)
        measured[ln['id']] = {'duration_s': float(index[key]), 'words': None}
    C.update_line_timing(m, measured)
    for ln in m['lines']:
        if ln['id'] in measured:
            ln['measured_preflight'] = True
    return sorted(measured)


def preflight(m, characters, prefs):
    """Measure the lines at validation time when the owner asked for it (prefs measure_lines_on_validate).

    A missing or failing voice engine never blocks validation: the estimates stand and a note says why."""
    if not prefs['production'].get('measure_lines_on_validate'):
        return m
    try:
        ids = measure_lines(m, characters, prefs)
    except (Blocked, media.MediaError, OSError, ValueError) as e:
        m.setdefault('notes', []).append(f'Lines were not measured at validation ({str(e)[:160]}); estimates used')
        return m
    if ids:
        m.setdefault('notes', []).append(f'{len(ids)} lines measured with the {prefs["production"]["tts_provider"]} '
                                         'voices at validation')
    return m


def speaker_of(ln, cast, prefs):
    """The character voicing a line ({'name', 'voice', ...}); the studio narrator for 'narrator' lines."""
    if ln['speaker'] == 'narrator':
        return {'name': 'Narrator', 'voice': prefs.get('narrator_voice', {})}
    return cast.get(ln['speaker'])


def elevenlabs_characters(m, cast, prefs, only=None, video_id=None, attempts=None):
    """Characters the video's ElevenLabs lines will bill (only the lines in ``only`` when given). With
    ``video_id``, a take already paid for (complete in the ledger, so it is reused) does not count."""
    pr = prefs['production']
    total = 0
    for ln in m['lines']:
        if only is not None and ln['id'] not in only:
            continue
        total += tts.billed_characters(ln, speaker_of(ln, cast, prefs), pr, video_id,
                                       (attempts or {}).get(ln['id'], 0), m)
    return total


class CreditBudget:
    """The ElevenLabs characters one video may use: a quarter of what was left on the owner's plan when it started
    (tts.credit_allowance), or everything left once the owner approved going on after a stop.

    ``already`` is what the video used before this run (tts.elevenlabs_sent). The first takes are checked as a
    whole before any is voiced (``start``); every re-voice to fit a slot is counted too (``take``) and is skipped
    when it would go over the allowance, so the line moves or is rewritten instead. Characters are counted from
    the text of each request; a take already paid for is not counted again."""

    def __init__(self, already=0, approved=False):
        self.already = max(0, int(already or 0))
        self.approved = bool(approved)
        self.spent = 0          # characters counted in this run
        self.allowed = None     # what the video may use in all; known after the first credit check

    def start(self, need):
        """Check the credits left before voicing ``need`` characters of first takes (tts.CreditStop when that is
        more than the video may use) and count them."""
        credits = tts.check_elevenlabs_credits(need, already=self.already + self.spent, approved=self.approved)
        self.allowed = credits['allowed']
        self.spent += need
        return credits

    def take(self, n):
        """Count a re-voice of ``n`` characters. False, with nothing counted, when it would take the video past
        what it may use. The credits are read first when this run has not checked them yet."""
        if self.allowed is None:
            self.allowed = tts.credit_allowance(tts.elevenlabs_credits()['remaining'], self.already, self.approved)
        if self.already + self.spent + n > self.allowed:
            return False
        self.spent += n
        return True


def credit_guard(m, cast, prefs, only=None, budget=None, video_id=None, attempts=None):
    """Before the first paid ElevenLabs line of a video: check the characters left on the owner's plan and stop
    for the owner (tts.CreditStop, needs_review) when this video would use more than a quarter of what was left
    (``budget``: a CreditBudget, which then also covers this run's re-voices). Free and other engines need no
    check. Returns the characters the video's first takes bill (0 when it uses no ElevenLabs voice)."""
    need = elevenlabs_characters(m, cast, prefs, only, video_id, attempts)
    if need:
        (budget if budget is not None else CreditBudget()).start(need)
    return need


def voice_lines(m, cast, prefs, work_dir, video_id, only=None, attempts=None, on_line=None, budget=None):
    credit_guard(m, cast, prefs, only=only, budget=budget, video_id=video_id, attempts=attempts)
    results = {}
    for ln in m['lines']:
        if only and ln['id'] not in only:
            continue
        who = speaker_of(ln, cast, prefs)
        results[ln['id']] = tts.synthesize_line(ln, who, prefs, work_dir, video_id,
                                                attempt=(attempts or {}).get(ln['id'], 0), manifest=m)
        if on_line:
            on_line(ln['id'])
    return results


# A line re-voiced to fit is asked for a little more than its overrun (native speed controls are not exact), never
# more than the line-fitting cap.
NATIVE_FIT_MARGIN = 1.03


def revoicer(m, cast, prefs, work_dir, video_id, attempts=None, budget=None):
    """The re-voice step retime uses when time stretching is off: ``revoice(line, factor)`` voices the line again
    with its engine's own speed raised ``factor`` times (the line's ``fit_speed``), or returns None when the
    engine has no native speed control or is already at the end of its range.

    A paid ElevenLabs re-voice is counted against the video's credit ``budget`` (a CreditBudget; the one the
    first takes were checked with, so the quarter covers both). When it would go over, nothing is sent and
    {'skipped': 'credits'} is returned: the line then moves or is rewritten like any line that does not fit."""
    pr = prefs['production']
    budget = budget if budget is not None else CreditBudget()

    def revoice(ln, factor):
        who = speaker_of(ln, cast, prefs)
        if not tts.can_speed_up(ln, who, pr, factor, manifest=m):
            return None
        fast, attempt = dict(ln, fit_speed=factor), (attempts or {}).get(ln['id'], 0)
        n = tts.billed_characters(fast, who, pr, video_id, attempt, m)
        if n and not budget.take(n):
            return {'skipped': 'credits'}
        res = tts.synthesize_line(fast, who, prefs, work_dir, video_id, attempt=attempt, manifest=m)
        return dict(res, native_speed=factor)
    return revoice


def _native_fit(ln, r, window, cap, revoice, report):
    """Re-voice a slightly long line once at a natively faster speed (never time-stretched). Returns
    (result, action): 'fits' with the re-voiced line, or 'too_long' with the shorter of the two takes so the
    caller moves the next line or asks for a rewrite."""
    factor = round(min(cap, max(1.0, r['duration_s'] / max(0.05, window)) * NATIVE_FIT_MARGIN), 4)
    new = revoice(ln, factor) if revoice and factor > 1.0 else None
    if new and new.get('skipped'):
        # Not re-voiced: it would take the video past the ElevenLabs characters it may use.
        report.append({'line': ln['id'], 'action': 'native_speed_skipped', 'speed': factor,
                       'reason': new['skipped']})
        return r, 'too_long'
    if not new:
        return r, 'too_long'
    fits = new['duration_s'] <= window
    report.append({'line': ln['id'], 'action': 'native_speed', 'speed': factor, 'before_s': r['duration_s'],
                   'after_s': new['duration_s'], 'window_s': round(window, 3), 'fits': fits})
    if fits:
        return new, 'fits'
    return (new if new['duration_s'] < r['duration_s'] else r), 'too_long'


def retime(m, results, prefs, max_shift_s=0.6, revoice=None):
    """Fit measured line durations into the timeline.

    Order of preference: (1) it fits; (2) a slightly long line speeds up within the cap (max_tempo, or at a
    production pace up to the pace, never above 1.5x with the speech rate): with time stretching off (the
    default, the owner's rule) it is voiced once more at the engine's own faster speed through ``revoice``
    (see revoicer; reported as 'native_speed'); with it on, a gentle FFmpeg tempo change ('tempo');
    (3) start the following line later (bounded, never past the end);
    (4) report the line as too long so it is rewritten and re-voiced.
    Without ``revoice``, or when the engine has no native speed, step (2) is skipped when stretching is off.
    Returns (manifest, results, report).
    """
    m = copy.deepcopy(m)
    fps = m['fps']
    D = m['duration_frames']
    report = []
    lines = m['lines']
    stretch = bool(prefs['production'].get('time_stretch_speech', False))
    # Gentle tempo (max_tempo), or at a production pace up to the pace, never
    # above MAX_LINE_SPEEDUP in total with the native speech rate.
    max_tempo = C.tempo_cap(m, prefs['production']['max_tempo'])
    for i, ln in enumerate(lines):
        r = results.get(ln['id'])
        if not r:
            continue
        nxt = lines[i + 1]['start_frame'] if i + 1 < len(lines) else D
        window = (nxt - ln['start_frame']) / fps - (ln.get('pause_after_ms', 0) / 1000.0)
        fitted, action = tts.fit_line(r, window, max_tempo, os.path.dirname(r['file']), stretch=stretch)
        if action == 'speed_up':
            fitted, action = _native_fit(ln, r, window, max_tempo, revoice, report)
            r = fitted
            if fitted.get('native_speed'):
                # Kept on the line, so a later re-voice of it (a repair) speaks at the same native speed.
                ln['fit_speed'] = fitted['native_speed']
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
    m = C.compile_plan(plan, fps=pr['fps'], width=pr['width'], height=pr['height'], characters=characters,
                       **C.pace_kwargs(prefs))
    rep = V.validate(m, prefs)
    if not rep['ok']:
        raise ValueError('Manifest invalid: ' + '; '.join(e['message'] for e in rep['errors'][:5]))
    cast = cast_characters(m, characters)
    os.makedirs(work_dir, exist_ok=True)
    log('voicing lines')
    credits = CreditBudget()  # one ElevenLabs allowance for the first takes and the re-voices to fit
    results = voice_lines(m, cast, prefs, os.path.join(work_dir, 'voice'), video_id, budget=credits)
    m, results, retime_report = retime(m, results, prefs,
                                       revoice=revoicer(m, cast, prefs, os.path.join(work_dir, 'voice'), video_id,
                                                        budget=credits))
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
