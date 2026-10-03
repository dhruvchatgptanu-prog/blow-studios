"""Deterministic technical checks on the final file (FFmpeg/ffprobe + numpy)."""
import re

import numpy as np

from .. import config, media
from ..voice import audio as A


def _filter_log(path, vf=None, af=None, timeout=900):
    args = [config.FFMPEG_BIN, '-hide_banner', '-nostats', '-i', str(path)]
    if vf:
        args += ['-vf', vf, '-an']
    if af:
        args += ['-af', af, '-vn']
    args += ['-f', 'null', '-']
    _, err = media.run(args, timeout=timeout)
    return err.decode(errors='replace')


def decode_frames_gray(path, w=135, h=240):
    out, _ = media.run([config.FFMPEG_BIN, '-v', 'error', '-i', str(path), '-vf', f'scale={w}:{h},format=gray',
                        '-f', 'rawvideo', '-'], timeout=900)
    a = np.frombuffer(out, dtype=np.uint8)
    n = len(a) // (w * h)
    return a[:n * w * h].reshape(n, h, w)


def run(ck, m, final, prefs, assembly, dialog_wav=None):
    fps = m['fps']
    D = m['duration_frames']
    q = prefs['qa']
    target_video = {'kind': 'video', 'id': 'final'}
    # 1. Decodes completely
    try:
        _, err = media.run([config.FFMPEG_BIN, '-v', 'error', '-i', str(final), '-f', 'null', '-'], timeout=900)
        errs = err.decode(errors='replace').strip()
        ck.add('decode', 'File decodes completely', 'technical', 'fail' if errs else 'pass',
               'critical', errs[-400:] or 'FFmpeg decoded every packet without errors', 0.99, 'deterministic',
               target=target_video, repair={'action': 're_encode'} if errs else None)
    except media.MediaError as e:
        ck.add('decode', 'File decodes completely', 'technical', 'fail', 'critical', str(e)[-400:], 0.99,
               'deterministic', target=target_video, repair={'action': 're_encode'})
        return {}
    info = media.probe(final)
    vs, au = media.video_stream(info), media.audio_stream(info)
    exp_w, exp_h = m['width'], m['height']
    w, h = int(vs['width']), int(vs['height'])
    native = assembly.get('native_render_size') or [w, h]
    ck.add('dimensions', 'Expected dimensions', 'technical',
           'pass' if (w, h) == (exp_w, exp_h) else 'fail', 'major' if (w, h) != (exp_w, exp_h) else 'info',
           {'actual': [w, h], 'expected': [exp_w, exp_h], 'native_render': native}, 0.99, 'deterministic',
           target=target_video, repair={'action': 're_render_all_final_quality'} if (w, h) != (exp_w, exp_h) else None)
    rate = media.fraction(vs.get('avg_frame_rate'))
    ck.add('fps', 'Correct frame rate (constant)', 'technical', 'pass' if abs(rate - fps) < 0.01 else 'fail',
           'major', {'avg_frame_rate': vs.get('avg_frame_rate'), 'r_frame_rate': vs.get('r_frame_rate'), 'expected': fps},
           0.99, 'deterministic', target=target_video, repair={'action': 're_encode'} if abs(rate - fps) >= 0.01 else None)
    vdur = float(vs.get('duration') or info['format'].get('duration') or 0)
    expected = D / fps
    tol = 1.0 / fps + 0.05
    ck.add('duration', 'Expected duration', 'technical', 'pass' if abs(vdur - expected) <= tol else 'fail', 'major',
           {'video_s': round(vdur, 3), 'expected_s': round(expected, 3)}, 0.99, 'deterministic', target=target_video,
           repair={'action': 're_encode'} if abs(vdur - expected) > tol else None)
    try:
        out, _ = media.run([config.FFPROBE_BIN, '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries',
                            'stream=nb_read_frames', '-of', 'csv=p=0', str(final)], timeout=900)
        nframes = int(out.decode().strip().split(',')[0])
    except (ValueError, media.MediaError):
        nframes = -1
    ck.add('completeness', 'Render completeness (frame count)', 'technical',
           'pass' if abs(nframes - D) <= 1 else 'fail', 'critical', {'decoded_frames': nframes, 'expected': D}, 0.99,
           'deterministic', target=target_video, repair={'action': 're_assemble'} if abs(nframes - D) > 1 else None)
    # Audio presence and drift
    if not au:
        ck.add('audio_present', 'Audio track present', 'technical', 'fail', 'critical', 'No audio stream', 0.99,
               'deterministic', target=target_video, repair={'action': 're_assemble'})
        return {'frames': nframes}
    adur = float(au.get('duration') or 0)
    start_diff = abs(float(au.get('start_time') or 0) - float(vs.get('start_time') or 0))
    ck.add('audio_present', 'Audio track present', 'technical', 'pass', 'critical',
           {'codec': au.get('codec_name'), 'sample_rate': au.get('sample_rate'), 'channels': au.get('channels')},
           0.99, 'deterministic', target=target_video)
    drift_ms = max(abs(adur - vdur), start_diff) * 1000
    ck.add('av_stream_drift', 'Audio/video stream alignment', 'technical',
           'pass' if drift_ms <= q['max_av_drift_ms'] else 'fail', 'major',
           {'audio_s': round(adur, 3), 'video_s': round(vdur, 3), 'start_offset_ms': round(start_diff * 1000, 1)},
           0.95, 'deterministic', target=target_video,
           repair={'action': 're_assemble'} if drift_ms > q['max_av_drift_ms'] else None)
    mix = A.decode(final, channels=1)
    if dialog_wav:
        dlg = A.decode(dialog_wav)
        lag = env_lag_ms(dlg, mix)
        ck.add('dialog_offset', 'Dialogue position in final mix', 'technical',
               'pass' if lag is not None and abs(lag) <= q['max_av_drift_ms'] else ('uncertain' if lag is None else 'fail'),
               'major', {'measured_offset_ms': lag, 'method': 'envelope cross-correlation of dialogue stem vs final mix'},
               0.85 if lag is not None else 0.3, 'deterministic', target=target_video,
               repair={'action': 're_assemble'} if lag is not None and abs(lag) > q['max_av_drift_ms'] else None)
    # Loudness, peak, clipping
    loud = A.loudness(final)
    li = loud['integrated_lufs']
    ok = li is not None and abs(li - q['loudness_target_lufs']) <= q['loudness_tolerance']
    ck.add('loudness', 'Integrated loudness', 'technical', 'pass' if ok else 'fail', 'major',
           {**loud, 'target_lufs': q['loudness_target_lufs'], 'tolerance': q['loudness_tolerance']}, 0.95,
           'deterministic', target={'kind': 'mix', 'id': 'master'}, repair=None if ok else {'action': 'remix'})
    tp = loud['true_peak_dbtp']
    clip = A.clipped_fraction(mix)
    ok = (tp is None or tp <= q['true_peak_max_dbtp'] + 0.3) and clip < 1e-4
    ck.add('clipping', 'Clipping and true peak', 'technical', 'pass' if ok else 'fail', 'major',
           {'true_peak_dbtp': tp, 'clipped_sample_fraction': clip, 'limit_dbtp': q['true_peak_max_dbtp']}, 0.95,
           'deterministic', target={'kind': 'mix', 'id': 'master'}, repair=None if ok else {'action': 'remix'})
    # Silence: long silences anywhere, and any silence inside a planned line
    log = _filter_log(final, af=f'silencedetect=n=-48dB:d={q["max_silence_s"]}')
    sil = [(float(a), float(b)) for a, b in zip(re.findall(r'silence_start: (-?[\d.]+)', log),
                                                 re.findall(r'silence_end: (-?[\d.]+)', log))]
    bad = []
    for ln in m['lines']:
        ls, le = ln['start_frame'] / fps, ln['est_end_frame'] / fps
        for a, b in sil:
            if a < le - 0.1 and b > ls + 0.1:
                bad.append({'line': ln['id'], 'silence': [round(a, 2), round(b, 2)]})
    ck.add('silence', 'No accidental silence', 'audio', 'fail' if bad else 'pass', 'major',
           {'silences_s': [[round(a, 2), round(b, 2)] for a, b in sil], 'inside_lines': bad}, 0.9, 'deterministic',
           frames=(int(bad and bad[0]['silence'][0] * fps or 0), int(bad and bad[0]['silence'][1] * fps or 0)) if bad else None,
           target={'kind': 'line', 'id': bad[0]['line']} if bad else None,
           repair={'action': 'revoice_line', 'line': bad[0]['line']} if bad else None)
    # Black frames
    log = _filter_log(final, vf='blackdetect=d=0.08:pix_th=0.08')
    blacks = [(float(a), float(b)) for a, b in zip(re.findall(r'black_start:([\d.]+)', log),
                                                   re.findall(r'black_end:([\d.]+)', log))]
    allowed = [(s['start_frame'] / fps, s['start_frame'] / fps + 0.35) for s in m['shots'] if s.get('transition_in') == 'fade_in']
    unexpected = [b for b in blacks if not any(a0 - 0.05 <= b[0] and b[1] <= a1 + 0.05 for a0, a1 in allowed)]
    shot_of = lambda t: next((s['id'] for s in m['shots'] if s['start_frame'] <= t * fps < s['end_frame']), None)
    ck.add('black_frames', 'No missing or black frames', 'technical', 'fail' if unexpected else 'pass', 'critical',
           {'black_segments_s': unexpected, 'allowed_fades': allowed}, 0.95, 'deterministic',
           frames=(int(unexpected[0][0] * fps), int(unexpected[0][1] * fps)) if unexpected else None,
           target={'kind': 'shot', 'id': shot_of(unexpected[0][0])} if unexpected else None,
           repair={'action': 're_render_shot', 'shot': shot_of(unexpected[0][0])} if unexpected else None)
    # Freezes: compare against what the plan expects to move.
    log = _filter_log(final, vf=f'freezedetect=n=-62dB:d={q["max_freeze_s"]}')
    freezes = [(float(a), float(b)) for a, b in zip(re.findall(r'freeze_start: ([\d.]+)', log),
                                                    re.findall(r'freeze_end: ([\d.]+)', log))]
    moving = []
    for a, b in freezes:
        fa, fb = int(a * fps), int(b * fps)
        acts = [x for x in m['tracks']['actions'] if x['start_frame'] < fb and x['end_frame'] > fa
                and x['type'] not in ('idle',)]
        speech = [ln for ln in m['lines'] if ln['start_frame'] < fb and ln['est_end_frame'] > fa]
        if acts or speech:
            moving.append({'segment_s': [round(a, 2), round(b, 2)], 'expected_motion': [x['type'] for x in acts],
                           'speech': [l['id'] for l in speech]})
    ck.add('freeze', 'No unexpected freezes', 'technical', 'fail' if moving else 'pass', 'major',
           {'freezes_s': [[round(a, 2), round(b, 2)] for a, b in freezes], 'during_planned_motion': moving}, 0.85,
           'deterministic', frames=(int(moving[0]['segment_s'][0] * fps), int(moving[0]['segment_s'][1] * fps)) if moving else None,
           target={'kind': 'shot', 'id': shot_of(moving[0]['segment_s'][0])} if moving else None,
           repair={'action': 're_render_shot', 'shot': shot_of(moving[0]['segment_s'][0])} if moving else None)
    return {'frames': nframes, 'silences': sil, 'freezes': freezes, 'loudness': loud}


def env_lag_ms(ref, test, sr=A.SR, max_lag_s=0.4):
    """Lag (ms) of ``test`` relative to ``ref`` from RMS-envelope cross-correlation."""
    e1 = A.rms_envelope(ref, sr, 0.005)
    e2 = A.rms_envelope(test, sr, 0.005)
    n = min(len(e1), len(e2))
    if n < 50 or e1[:n].std() < 1e-5 or e2[:n].std() < 1e-5:
        return None
    a = (e1[:n] - e1[:n].mean()) / e1[:n].std()
    b = (e2[:n] - e2[:n].mean()) / e2[:n].std()
    maxlag = int(max_lag_s / 0.005)
    best, best_lag = -1e9, 0
    for lag in range(-maxlag, maxlag + 1):
        if lag >= 0:
            c = float(np.dot(a[:n - lag], b[lag:])) / (n - lag)
        else:
            c = float(np.dot(a[-lag:], b[:n + lag])) / (n + lag)
        if c > best:
            best, best_lag = c, lag
    return round(best_lag * 5.0, 1)
