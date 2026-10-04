"""Story, dialogue, caption and compliance checks."""
import re

import numpy as np

from .. import paid, vault
from ..manifest import validate as V
from ..voice import audio as A
from . import motion as MO

CLAIM_PATTERNS = [
    (r'\bfree\s+robux\b', 'promises free Robux'),
    (r'\bguarantee[sd]?\b', 'makes a guarantee'),
    (r'\bofficial(ly)?\b', 'claims something is official'),
    (r'\b(roblox|youtube)\s+(will|is going to|is gonna)\s+(delete|ban|shut|remove|close)', 'predicts platform action'),
    (r'\b\d+(\.\d+)?\s*(%|percent|million|billion)\b', 'states a statistic'),
    (r'\b(hack|exploit|cheat)s?\b', 'mentions hacks or exploits'),
    (r'\b(real|true) story\b', 'claims to be true'),
    (r'\bcaptured gameplay\b|\breal gameplay\b', 'claims to be real gameplay'),
]


def norm_words(s):
    return re.findall(r"[a-z0-9']+", (s or '').lower().replace('’', "'"))


def wer(ref, hyp):
    r, h = norm_words(ref), norm_words(hyp)
    if not r:
        return 0.0 if not h else 1.0
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=int)
    d[:, 0] = np.arange(len(r) + 1)
    d[0, :] = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return float(d[len(r), len(h)]) / len(r)


def transcribe_final(final, prefs, video_id, render_id):
    """ASR of the final mix (paid, small). Returns (text, words) or None."""
    if not vault.configured('OPENAI_API_KEY'):
        return None
    from ..voice.tts import asr_words
    dur = A.decode(final).shape[0] / A.SR
    est = max(0.001, dur / 60 * prefs['budget']['prices']['openai_asr_per_min'])
    tmp = final.replace('.mp4', '_asr.wav')
    from .. import config, media
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-i', final, '-ac', '1', '-ar', '16000', tmp])
    res = paid.run(f'qa_asr:{video_id}:{render_id}', provider='openai', operation='qa_transcription', category='asr',
                   estimate=est, video_id=video_id, fn=lambda: asr_words(tmp, prefs['production']['asr_model']))
    words, text = res
    return text, words


def run(ck, m, line_audio, assembly, prefs, video_id=None, render_id='r', asr=None):
    fps = m['fps']
    D = m['duration_frames']
    q = prefs['qa']
    # Hook and payoff structure
    hook_beats = [b for b in m['beats'] if b['purpose'] == 'hook']
    first_event = min([ln['start_frame'] for ln in m['lines']] + [a['start_frame'] for a in m['tracks']['actions']] + [D])
    dialog = A.decode(assembly['mix']['paths']['dialog'])
    early = dialog[:int(3.0 * A.SR)]
    early_speech = bool(len(early)) and A.peak_dbfs(early) > -30
    hook_ok = bool(hook_beats) and hook_beats[0]['start_frame'] == 0 and first_event <= int(1.5 * fps)
    ck.add('hook', 'Hook lands in the first seconds', 'story', 'pass' if hook_ok else 'fail', 'major',
           {'hook': m['hook']['text'], 'first_event_s': round(first_event / fps, 2), 'speech_in_first_3s': early_speech},
           0.75, 'manifest+audio', frames=(0, min(D, 3 * fps)),
           repair=None if hook_ok else {'action': 'rewrite_script', 'reason': 'hook arrives late'})
    payoff_beats = [b for b in m['beats'] if b['purpose'] in ('payoff', 'button')]
    payoff_ok = bool(payoff_beats) and payoff_beats[-1]['end_frame'] >= 0.9 * D and bool(m['payoff']['text'])
    ck.add('payoff', 'Ending pays off the hook (structure)', 'story', 'pass' if payoff_ok else 'fail', 'major',
           {'payoff': m['payoff']['text'], 'resolves': m['payoff'].get('resolves'),
            'note': 'Structural check; the semantic link is judged by the script review when an LLM is connected.'},
           0.7, 'manifest', frames=(m['payoff']['start_frame'], D),
           repair=None if payoff_ok else {'action': 'rewrite_script', 'reason': 'missing payoff'})
    # Lines: each placed once, audible in its window, generated from the approved text.
    placements = {p['line']: p for p in assembly['mix']['placements']}
    missing, quiet, test_voice, mismatched = [], [], [], []
    for ln in m['lines']:
        la = line_audio.get(ln['id'])
        if not la or ln['id'] not in placements:
            missing.append(ln['id'])
            continue
        p = placements[ln['id']]
        seg = dialog[int(p['start_s'] * A.SR):int(p['end_s'] * A.SR)]
        if not len(seg) or A.peak_dbfs(seg) < -35:
            quiet.append(ln['id'])
        if la.get('test_voice'):
            test_voice.append(ln['id'])
        if la.get('asr_text') and wer(ln['text'], la['asr_text']) > 0.34:
            mismatched.append({'line': ln['id'], 'asr': la['asr_text'][:120]})
    dupes = len(assembly['mix']['placements']) - len(placements)
    status = 'fail' if (missing or quiet or dupes) else 'pass'
    ck.add('lines_complete', 'Every scripted line plays once and is audible', 'story', status, 'critical',
           {'missing': missing, 'inaudible': quiet, 'duplicate_placements': dupes, 'lines': len(m['lines'])}, 0.9,
           'pipeline+audio', target={'kind': 'line', 'id': (missing + quiet or [None])[0]},
           repair={'action': 'revoice_line', 'line': (missing + quiet)[0]} if (missing or quiet) else None)
    if test_voice:
        ck.add('voice_quality', 'Natural voice provider used', 'audio', 'fail', 'critical',
               {'lines_with_test_voice': test_voice,
                'note': 'The local test voice (flite) is robotic and only for pipeline tests. Connect OpenAI or '
                        'ElevenLabs and choose them as the voice provider before publishing.'}, 0.99, 'pipeline',
               target={'kind': 'line', 'id': test_voice[0]})
    else:
        ck.add('voice_quality', 'Natural voice provider used', 'audio', 'pass', 'critical',
               {'providers': sorted({line_audio[l]['provider'] for l in line_audio})}, 0.9, 'pipeline')
    # Spoken content vs script
    if asr:
        text, _words = asr
        script = ' '.join(ln['text'] for ln in m['lines'])
        e = wer(script, text)
        ck.add('dialogue_matches_script', 'Spoken dialogue matches the approved script', 'story',
               'pass' if e <= 0.2 and not mismatched else 'fail', 'major',
               {'word_error_rate': round(e, 3), 'asr_text': text[:600], 'per_line_mismatches': mismatched}, 0.8,
               'asr', repair={'action': 'revoice_line', 'line': mismatched[0]['line']} if mismatched else None)
    else:
        status = 'uncertain' if q['require_asr'] else 'pass'
        ck.add('dialogue_matches_script', 'Spoken dialogue matches the approved script', 'story', status,
               'major' if q['require_asr'] else 'minor',
               {'method': 'provenance', 'note': 'Each line was synthesised from its approved text and placed once. '
                'Speech recognition of the final mix was not run, so the spoken words are not independently verified.'},
               0.55, 'pipeline')
    # Captions: text matches the script; timing inside the spoken words; safe area.
    by_line = {}
    for c in m['captions']:
        by_line.setdefault(c['line_id'], []).append(c['text'])
    cap_mismatch = [ln['id'] for ln in m['lines'] if prefs['production']['captions'] and
                    norm_words(' '.join(by_line.get(ln['id'], []))) != norm_words(ln['text'])]
    ck.add('captions_text', 'Captions match the spoken script', 'captions', 'fail' if cap_mismatch else 'pass', 'major',
           {'mismatched_lines': cap_mismatch}, 0.95, 'deterministic',
           repair={'action': 'rebuild_captions'} if cap_mismatch else None)
    timing_bad, estimated = [], 0
    lines = {ln['id']: ln for ln in m['lines']}
    for c in m['captions']:
        la = line_audio.get(c['line_id'])
        ln = lines.get(c['line_id'])
        if c['timing'] != 'aligned':
            estimated += 1
        if not la or not ln:
            continue
        s0 = ln['start_frame'] / fps + la['words'][0]['start'] if la.get('words') else None
        s1 = ln['start_frame'] / fps + la['words'][-1]['end'] if la.get('words') else None
        if s0 is None:
            continue
        cs, ce = c['start_frame'] / fps, c['end_frame'] / fps
        if cs < s0 - 0.3 or cs > s1 or ce < s0:
            timing_bad.append({'caption': c['id'], 'shown_s': [round(cs, 2), round(ce, 2)],
                               'speech_s': [round(s0, 2), round(s1, 2)]})
    kinds = sorted({la['alignment_kind'] for la in line_audio.values()})
    status = 'fail' if timing_bad else ('uncertain' if estimated else 'pass')
    ck.add('captions_timing', 'Caption timing follows the speech', 'captions', status,
           'major' if timing_bad else 'minor',
           {'out_of_window': timing_bad[:6], 'captions_with_estimated_timing': estimated,
            'alignment_sources': kinds}, 0.85 if 'measured' in ''.join(kinds) else 0.6, 'deterministic',
           repair={'action': 'rebuild_captions'} if timing_bad else None)
    _caption_pixels(ck, m, assembly, prefs)
    # Continuity and contradictions as encoded in the manifest
    rep = V.validate(m, prefs, measured=True)
    ck.add('manifest_consistency', 'No contradictory events or continuity errors in the plan', 'story',
           'pass' if rep['ok'] else 'fail', 'major',
           {'errors': [e['message'] for e in rep['errors']][:8], 'warnings': [w['message'] for w in rep['warnings']][:8]},
           0.8, 'manifest', repair={'action': 'rewrite_script'} if not rep['ok'] else None)
    # Unsupported claims screen
    text = ' '.join([m.get('title', ''), m.get('logline', '')] + [ln['text'] for ln in m['lines']] +
                    [str((m.get('metadata') or {}).get('description', ''))])
    hits = [{'pattern': label, 'match': mm.group(0)} for rx, label in CLAIM_PATTERNS
            for mm in re.finditer(rx, text, re.I)]
    ck.add('claims', 'No unsupported factual claims', 'story', 'uncertain' if hits else 'pass',
           'major' if hits else 'minor', {'flags': hits[:8], 'note': 'Keyword screen; a person should confirm flagged text.'},
           0.6, 'heuristic')


def _caption_pixels(ck, m, assembly, prefs):
    """Locate caption pixels in the rendered video (final minus captionless)."""
    caps = assembly.get('captions') or []
    if not caps:
        ck.add('captions_safe_area', 'Captions inside the safe area', 'captions', 'skipped', 'info',
               {'reason': 'Captions disabled'}, 1.0, 'pixel')
        return
    sa = prefs['production']['safe_area']
    fps = m['fps']
    final, joined = assembly['final'], assembly['joined']
    w, h = assembly['width'], assembly['height']
    bad, measured = [], []
    for c in caps[:: max(1, len(caps) // 8)] or caps:
        f = (c['start_frame'] + c['end_frame']) // 2
        try:
            a = MO.decode_rgb_frame(final, f, fps).astype(np.int16)
            b = MO.decode_rgb_frame(joined, f, fps).astype(np.int16)
        except Exception:
            continue
        diff = np.abs(a - b).max(axis=2) > 60
        ys, xs = np.where(diff)
        if len(xs) < 20:
            bad.append({'caption': c['id'], 'issue': 'caption pixels not found in the rendered frame', 'frame': f})
            continue
        x0, x1 = np.percentile(xs, 0.5) / w, np.percentile(xs, 99.5) / w
        y0, y1 = np.percentile(ys, 0.5) / h, np.percentile(ys, 99.5) / h
        box = [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]
        measured.append({'caption': c['id'], 'box': box, 'frame': f})
        tol = 0.015
        if x0 < sa['left'] - tol or x1 > sa['right'] + tol or y0 < sa['top'] - tol or y1 > sa['bottom'] + tol:
            bad.append({'caption': c['id'], 'box': box, 'frame': f})
    ck.add('captions_safe_area', 'Captions inside the safe area (measured in pixels)', 'captions',
           'fail' if bad else 'pass', 'major', {'safe_area': sa, 'measured': measured[:8], 'problems': bad[:6]}, 0.9,
           'pixel', frames=(bad[0]['frame'], bad[0]['frame'] + 1) if bad else None,
           repair={'action': 'rebuild_captions'} if bad else None)
