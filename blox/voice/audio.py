"""PCM helpers built on FFmpeg + numpy (decode, measure, trim, normalise, pitch-shift, write)."""
import functools
import json
import math
import re
import wave

import numpy as np

from .. import config, media

SR = 48000


def decode(path, sr=SR, channels=1):
    out, _ = media.run([config.FFMPEG_BIN, '-v', 'error', '-i', str(path), '-f', 's16le', '-acodec', 'pcm_s16le',
                        '-ac', str(channels), '-ar', str(sr), '-'], timeout=300)
    a = np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        a = a.reshape(-1, channels)
    return a


def write_wav(path, samples, sr=SR):
    s = np.asarray(samples, dtype=np.float32)
    s = np.clip(s, -1.0, 1.0)
    ch = 1 if s.ndim == 1 else s.shape[1]
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((s * 32767).astype('<i2').tobytes())
    return path


def rms_envelope(a, sr=SR, win_s=0.02):
    n = max(1, int(sr * win_s))
    frames = len(a) // n
    if frames == 0:
        return np.zeros(0)
    x = a[:frames * n].reshape(frames, n)
    return np.sqrt((x ** 2).mean(axis=1) + 1e-12)


def db(x):
    return 20 * np.log10(np.maximum(x, 1e-9))


def speech_regions(a, sr=SR, threshold_db=-38.0, min_gap_s=0.12, win_s=0.02):
    """[(start_s, end_s)] of voiced regions from the RMS envelope."""
    env = db(rms_envelope(a, sr, win_s))
    voiced = env > threshold_db
    regions = []
    start = None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            regions.append([start * win_s, i * win_s])
            start = None
    if start is not None:
        regions.append([start * win_s, len(voiced) * win_s])
    merged = []
    for r in regions:
        if merged and r[0] - merged[-1][1] < min_gap_s:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [(round(a_, 3), round(b_, 3)) for a_, b_ in merged if b_ - a_ > 0.04]


def trim_silence(a, sr=SR, threshold_db=-45.0, pad_s=0.06):
    """Remove leading/trailing silence; returns (trimmed, leading_seconds_removed)."""
    env = db(rms_envelope(a, sr, 0.01))
    idx = np.where(env > threshold_db)[0]
    if len(idx) == 0:
        return a, 0.0
    s = max(0, int((idx[0] * 0.01 - pad_s) * sr))
    e = min(len(a), int(((idx[-1] + 1) * 0.01 + pad_s) * sr))
    return a[s:e], s / sr


def loudness(path):
    """Integrated LUFS, true peak dBTP and LRA via FFmpeg's EBU R128 meter."""
    _, err = media.run([config.FFMPEG_BIN, '-hide_banner', '-nostats', '-i', str(path), '-filter_complex',
                        'ebur128=peak=true', '-f', 'null', '-'], timeout=300)
    text = err.decode(errors='replace')
    summary = text[text.rfind('Summary:'):]
    def grab(label):
        m = re.search(label + r':\s+(-?[\d.]+|-inf)', summary)
        if not m or m.group(1) == '-inf':
            return None
        return float(m.group(1))
    return {'integrated_lufs': grab('I'), 'true_peak_dbtp': grab('Peak'), 'lra': grab('LRA')}


def loudnorm(src, dst, target=-16.0, tp=-1.5, lra=11.0, sr=SR, channels=1):
    """Two-pass EBU R128 normalisation (measure, then apply linear gain)."""
    _, err = media.run([config.FFMPEG_BIN, '-hide_banner', '-i', str(src), '-af',
                        f'loudnorm=I={target}:TP={tp}:LRA={lra}:print_format=json', '-f', 'null', '-'], timeout=300)
    text = err.decode(errors='replace')
    j = json.loads(text[text.rfind('{'):text.rfind('}') + 1])
    measured = {k: float(j[k]) for k in ('input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset')}
    if not all(math.isfinite(v) for v in measured.values()):
        # Shorter than one 400 ms gating block (an interjection like "Heh."), or silent: R128 has no integrated
        # loudness to work with, so apply a plain RMS gain toward the target and keep the peak under the ceiling.
        a = decode(src)
        rms = float(np.sqrt(np.mean(a ** 2) + 1e-12)) if len(a) else 0.0
        gain_db = min(30.0, max(-30.0, target - db(rms))) if rms > 1e-5 else 0.0
        media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-i', str(src), '-af',
                   f'volume={gain_db:.2f}dB,alimiter=limit={10 ** (tp / 20):.4f}:level=false', '-ar', str(sr), '-ac',
                   str(channels), str(dst)], timeout=300)
        return {'input_i': db(rms) if rms > 1e-5 else -120.0, 'input_tp': peak_dbfs(a), 'short_clip': True}
    flt = (f'loudnorm=I={target}:TP={tp}:LRA={lra}:measured_I={j["input_i"]}:measured_TP={j["input_tp"]}:'
           f'measured_LRA={j["input_lra"]}:measured_thresh={j["input_thresh"]}:offset={j["target_offset"]}:linear=true')
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-i', str(src), '-af', flt, '-ar', str(sr), '-ac',
               str(channels), str(dst)], timeout=300)
    return {'input_i': measured['input_i'], 'input_tp': measured['input_tp']}


def peak_dbfs(a):
    return float(db(np.max(np.abs(a)) + 1e-12)) if len(a) else -120.0


def clipped_fraction(a, level=0.999):
    return float(np.mean(np.abs(a) >= level)) if len(a) else 0.0


def squeeze_gaps(a, max_gap_s, sr=SR, threshold_db=-45.0, win_s=0.01):
    """Shorten every internal silence longer than ``max_gap_s`` to ``max_gap_s`` (the cut is made in the middle
    of the silence, so no voiced sample is touched). Returns (samples, seconds_removed)."""
    env = db(rms_envelope(a, sr, win_s))
    n = max(1, int(sr * win_s))
    quiet = env <= threshold_db
    keep_s = int(max_gap_s * sr)
    cuts = []
    i = 0
    while i < len(quiet):
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < len(quiet) and quiet[j]:
            j += 1
        # Only silences with voice on both sides (leading/trailing silence is trim_silence's job).
        if i > 0 and j < len(quiet) and (j - i) * n > keep_s:
            s0 = i * n + keep_s // 2
            cuts.append((s0, s0 + (j - i) * n - keep_s))
        i = j
    if not cuts:
        return a, 0.0
    parts, prev = [], 0
    for s0, s1 in cuts:
        parts.append(a[prev:s0])
        prev = s1
    parts.append(a[prev:])
    return np.concatenate(parts), round(sum(s1 - s0 for s0, s1 in cuts) / sr, 3)


@functools.lru_cache(maxsize=None)
def ffmpeg_filters():
    """Names of the audio/video filters this FFmpeg build provides."""
    out, _ = media.run([config.FFMPEG_BIN, '-hide_banner', '-filters'], timeout=60)
    return frozenset(re.findall(r'^\s*[.A-Z|]{3}\s+(\w+)\s', out.decode(errors='replace'), re.M))


def has_filter(name):
    try:
        return name in ffmpeg_filters()
    except media.MediaError:
        return False


# Recorded on a voiced line when its pitch shift was left out (see pitch_filter's resample_fallback).
PITCH_SKIPPED = ('Pitch shift skipped: this FFmpeg has no rubberband filter, and the other way to shift pitch '
                 'changes the speed of the voice, which is turned off (time stretch speech).')


def pitch_filter(semitones, formants='preserve', sr=SR, resample_fallback=True):
    """FFmpeg filter chain that shifts pitch by ``semitones`` and keeps the duration.

    With librubberband (``rubberband`` filter) the formants are preserved by default, so a shifted voice keeps its
    natural timbre; ``formants='shift'`` moves them with the pitch (a smaller, younger-sounding speaker). The
    rubberband filter changes the pitch only (its tempo stays 1), so the speech is not time-stretched. Without it,
    the fallback resamples: asetrate raises the pitch and the formants together (the "chipmunk" effect),
    aresample restores the sample rate and atempo restores the duration. That fallback cannot preserve formants,
    so it sounds natural only within about +/-2 semitones, and it time-stretches the voice: with
    ``resample_fallback=False`` (the owner's no-time-stretch rule) it is never used and '' is returned instead."""
    r = 2.0 ** (float(semitones) / 12.0)
    if has_filter('rubberband'):
        return (f'rubberband=pitch={r:.6f}:formant={"shifted" if formants == "shift" else "preserved"}'
                ':pitchq=quality')
    if not resample_fallback:
        return ''
    return f'aresample={sr},asetrate={sr * r:.3f},aresample={sr},atempo={1.0 / r:.6f}'


def apply_filter(src, dst, af, sr=SR):
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-i', str(src), '-af', af, '-ar', str(sr), '-ac', '1',
               str(dst)], timeout=300)
    return dst
