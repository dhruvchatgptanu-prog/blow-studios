"""Voice profiles: measure what a voice sounds like, store the numbers, compare candidates.

A profile is a set of measurements over a set of clips: median pitch and its p10-p90 spread, speaking rate
(words per second of speech when the text is known, syllable nuclei per second always), pauses, loudness
dynamics (EBU R128 momentary loudness of the loud versus the quiet quartile, via FFmpeg) and spectral brightness.
The same code measures reference clips the owner uploads and the lines our own engines produce, so profiles are
comparable, and ``match_score`` turns the distance between two profiles into a 0-100 number.

Reference clips are untrusted data from a commercial voice product: they are decoded with FFmpeg, measured and
deleted. They are never fed to a voice engine, never copied into the repository and never used to make a voice
sound like a particular person or product; the numbers only tell the owner which of our own catalogue voices sits
closest in pitch, pace, pauses and energy.

Pitch is estimated with YIN (difference function via FFT autocorrelation, cumulative mean normalisation, absolute
threshold, parabolic interpolation) in numpy, with a median filter and an octave check afterwards; librosa is not
a dependency.
"""
import csv
import io
import json
import math
import os
import re
import shutil
import tempfile
import zipfile

import numpy as np

from .. import config, db as dbmod, media
from ..util import new_id, now
from . import audio as A

SR = 16000
VERSION = 1
FRAME_S, HOP_S = 0.04, 0.01
F0_MIN, F0_MAX = 60.0, 500.0
YIN_THRESHOLD = 0.15
GATE_DB = -42.0
AUDIO_EXT = ('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.aac', '.opus', '.webm')
# Upload limits for "Measure reference clips": enough for a few hundred lines, not a library.
MAX_CLIPS, MAX_TOTAL_BYTES, MAX_CLIP_BYTES = 400, 300 * 1024 * 1024, 30 * 1024 * 1024
WORD_RE = re.compile(r"[A-Za-z0-9']+")
SENTENCE_RE = re.compile(r'[.!?]+')

# How a candidate's distance from the target is scored: (weight, tolerance) per measurement. A component scores
# 1 at zero distance and 0 at the tolerance; the score is the weighted mean of the components both profiles have.
SCORE = {
    'f0_median_st': (0.30, 8.0),      # semitones between median pitches
    'f0_range_st': (0.15, 6.0),       # difference of the p10-p90 spreads, semitones
    'rate': (0.20, 0.5),              # relative difference of words/s (or syllables/s when a text is missing)
    'pauses_per_sentence': (0.10, 1.5),
    'dynamics_lu': (0.10, 8.0),       # loud quartile minus quiet quartile, LU
    'centroid_st': (0.15, 12.0),      # semitones between spectral centroids
}


# ------------------------------------------------------------------ signal measurements
def _frames(x, frame, hop):
    n = 1 + max(0, (len(x) - frame) // hop)
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    return x[idx]


def yin_f0(x, sr=SR, frame_s=FRAME_S, hop_s=HOP_S, fmin=F0_MIN, fmax=F0_MAX, threshold=YIN_THRESHOLD):
    """Per-frame fundamental frequency (Hz, NaN when unvoiced) and the frame times.

    Each frame of ``frame_s`` is compared with itself shifted by up to tau_max samples; the difference function is
    computed from the FFT autocorrelation, normalised by its cumulative mean, and the first dip under ``threshold``
    (walked down to its local minimum, then refined parabolically) gives the period. Frames below the energy gate or
    without a dip under 0.5 are unvoiced. A 5-frame median filter and a one-octave check against the clip's median
    remove the remaining octave errors."""
    x = np.asarray(x, dtype=np.float64)
    frame, hop = int(sr * frame_s), int(sr * hop_s)
    tau_min, tau_max = int(sr / fmax), int(math.ceil(sr / fmin))
    win = frame + tau_max
    if len(x) < win + 1:
        return np.zeros(0), np.zeros(0)
    F = _frames(x, win, hop)
    n = F.shape[0]
    times = (np.arange(n) * hop + frame / 2) / sr
    energy_db = 20 * np.log10(np.sqrt((F[:, :frame] ** 2).mean(axis=1)) + 1e-9)
    nfft = 1 << int(math.ceil(math.log2(2 * win)))
    spec = np.fft.rfft(F, nfft, axis=1)
    acf = np.fft.irfft(spec * np.conj(spec), nfft, axis=1)[:, :tau_max + 1]
    sq = F ** 2
    csum = np.concatenate([np.zeros((n, 1)), np.cumsum(sq, axis=1)], axis=1)
    taus = np.arange(tau_max + 1)
    e_head = csum[:, frame]                                   # sum x[j]^2, j < frame
    e_tail = csum[:, frame + taus] - csum[:, taus]            # sum x[j+tau]^2, j < frame
    d = e_head[:, None] + e_tail - 2 * acf
    d[:, 0] = 0.0
    cum = np.cumsum(d[:, 1:], axis=1)
    cmnd = np.ones_like(d)
    cmnd[:, 1:] = d[:, 1:] * taus[1:] / np.maximum(cum, 1e-12)
    f0 = np.full(n, np.nan)
    for i in range(n):
        if energy_db[i] < GATE_DB:
            continue
        row = cmnd[i]
        tau = -1
        for t in range(tau_min, tau_max):
            if row[t] < threshold:
                while t + 1 < tau_max and row[t + 1] < row[t]:
                    t += 1
                tau = t
                break
        if tau < 0:
            t = int(np.argmin(row[tau_min:tau_max])) + tau_min
            if row[t] < 0.5:
                tau = t
        if tau <= 0 or tau >= tau_max:
            continue
        # Half-period dips: when the fundamental is weak (band-limited or compressed speech) the first dip under
        # the threshold can sit at T/2. The true period always has the deepest dip, so a clearly deeper dip near
        # 2*tau means tau was the octave above; take the deeper one.
        lo2, hi2 = int(2 * tau * 0.94), min(tau_max - 1, int(2 * tau * 1.06) + 1)
        if hi2 > lo2 and row[tau] > 0.04:
            t2 = int(np.argmin(row[lo2:hi2])) + lo2
            if row[t2] < 0.6 * row[tau]:
                tau = t2
        a, b, c = row[tau - 1], row[tau], row[tau + 1]
        denom = a - 2 * b + c
        shift = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
        f0[i] = sr / (tau + max(-1.0, min(1.0, shift)))
    # Median filter (5 frames) over the voiced runs, then drop frames a full octave away from the clip's median.
    voiced = np.isfinite(f0)
    if voiced.sum() >= 3:
        smooth = f0.copy()
        idx = np.where(voiced)[0]
        vals = f0[idx]
        for k, i in enumerate(idx):
            lo, hi = max(0, k - 2), min(len(vals), k + 3)
            smooth[i] = np.median(vals[lo:hi])
        med = np.median(vals)
        bad = voiced & (np.abs(np.log2(np.where(voiced, smooth, med) / med)) > 0.9)
        smooth[bad] = np.nan
        f0 = smooth
    return times, f0


def spectral_centroid(x, sr=SR, frame_s=0.025, hop_s=HOP_S):
    """Energy-weighted spectral centroid (Hz) of the frames above the gate, and the per-frame values."""
    frame, hop = int(sr * frame_s), int(sr * hop_s)
    if len(x) < frame:
        return None, np.zeros(0)
    F = _frames(np.asarray(x, dtype=np.float64), frame, hop) * np.hanning(frame)[None, :]
    mag = np.abs(np.fft.rfft(F, axis=1)) ** 2
    freqs = np.fft.rfftfreq(frame, 1.0 / sr)
    power = mag.sum(axis=1)
    cent = (mag * freqs[None, :]).sum(axis=1) / np.maximum(power, 1e-12)
    keep = 10 * np.log10(power + 1e-12) > (10 * np.log10(power.max() + 1e-12) - 30)
    if not keep.any():
        return None, cent
    return float((cent[keep] * power[keep]).sum() / power[keep].sum()), cent


def syllable_peaks(x, sr=SR):
    """Syllable nuclei: peaks of the 300-2500 Hz energy envelope at least 100 ms apart and above a quarter of
    the clip's loudest peak. Speaking rate without a transcript."""
    frame, hop = int(sr * 0.025), int(sr * HOP_S)
    if len(x) < frame:
        return 0
    F = _frames(np.asarray(x, dtype=np.float64), frame, hop) * np.hanning(frame)[None, :]
    mag = np.abs(np.fft.rfft(F, axis=1)) ** 2
    freqs = np.fft.rfftfreq(frame, 1.0 / sr)
    band = mag[:, (freqs >= 300) & (freqs <= 2500)].sum(axis=1)
    env = np.convolve(np.sqrt(band), np.ones(5) / 5, mode='same')
    if env.max() <= 0:
        return 0
    floor = env.max() * 0.25
    peaks, last = 0, -10
    for i in range(1, len(env) - 1):
        if env[i] >= floor and env[i] >= env[i - 1] and env[i] > env[i + 1] and i - last >= 10:
            peaks += 1
            last = i
    return peaks


def momentary_loudness(path):
    """EBU R128 momentary loudness values (LUFS, 100 ms steps) from FFmpeg's ebur128 meter."""
    _, err = media.run([config.FFMPEG_BIN, '-hide_banner', '-nostats', '-i', str(path), '-filter_complex', 'ebur128',
                        '-f', 'null', '-'], timeout=300)
    vals = [float(m) for m in re.findall(r'\bM:\s*(-?\d+(?:\.\d+)?)', err.decode(errors='replace'))]
    return [v for v in vals if v > -70.0]


def measure_clip(path, text=None):
    """Measurements of one clip (see module docstring). ``text`` is what is spoken, when known."""
    x = A.decode(path, sr=SR)
    duration = len(x) / SR
    regions = A.speech_regions(x, sr=SR)
    speech_s = sum(b - a for a, b in regions)
    pauses = [b[0] - a[1] for a, b in zip(regions, regions[1:])]
    _, f0 = yin_f0(x)
    voiced = f0[np.isfinite(f0)]
    centroid, _ = spectral_centroid(x)
    words = len(WORD_RE.findall(text)) if text else None
    sentences = max(1, len(SENTENCE_RE.findall(text))) if text else None
    try:
        loud = momentary_loudness(path)
    except media.MediaError:
        loud = []
    return {'path': str(path), 'duration_s': round(duration, 3), 'speech_s': round(speech_s, 3),
            'regions': len(regions), 'pauses': [round(p, 3) for p in pauses],
            'f0_voiced': [round(float(v), 2) for v in voiced], 'centroid_hz': centroid,
            'syllables': syllable_peaks(x), 'words': words, 'sentences': sentences, 'loudness_m': loud}


def summarise(clips):
    """Pool per-clip measurements into one profile."""
    clips = [c for c in clips if c['speech_s'] > 0]
    out = {'version': VERSION, 'clips': len(clips)}
    if not clips:
        return out
    f0 = np.array([v for c in clips for v in c['f0_voiced']], dtype=np.float64)
    if len(f0) >= 5:
        p10, p50, p90 = np.percentile(f0, [10, 50, 90])
        out.update(f0_median_hz=round(float(p50), 1), f0_p10_hz=round(float(p10), 1), f0_p90_hz=round(float(p90), 1),
                   f0_range_st=round(float(12 * np.log2(p90 / p10)), 2))
    speech = sum(c['speech_s'] for c in clips)
    out['speech_s'] = round(speech, 2)
    out['syllables_per_s'] = round(sum(c['syllables'] for c in clips) / speech, 2)
    if all(c['words'] is not None for c in clips):
        words = sum(c['words'] for c in clips)
        sentences = sum(c['sentences'] for c in clips)
        out.update(words=words, sentences=sentences, words_per_s=round(words / speech, 2))
        out['pauses_per_sentence'] = round(sum(len(c['pauses']) for c in clips) / sentences, 2)
    pauses = [p for c in clips for p in c['pauses']]
    out['pause_count'] = len(pauses)
    out['pause_mean_s'] = round(float(np.mean(pauses)), 3) if pauses else 0.0
    cents = [(c['centroid_hz'], c['speech_s']) for c in clips if c['centroid_hz']]
    if cents:
        out['centroid_hz'] = round(sum(h * w for h, w in cents) / sum(w for _, w in cents), 0)
    loud = np.array([v for c in clips for v in c['loudness_m']], dtype=np.float64)
    if len(loud) >= 8:
        gate = loud.max() - 25.0
        loud = np.sort(loud[loud >= gate])
        q = max(1, len(loud) // 4)
        quiet, top = float(loud[:q].mean()), float(loud[-q:].mean())
        out.update(loud_lufs=round(top, 1), quiet_lufs=round(quiet, 1), dynamics_lu=round(top - quiet, 1))
    return out


def measure_files(items):
    """items: [(path, text_or_None)] -> profile measurements. Clips FFmpeg cannot decode are skipped and counted
    (uploads are untrusted data; one bad file must not lose the rest)."""
    clips, skipped = [], 0
    for p, t in items:
        try:
            clips.append(measure_clip(p, t))
        except media.MediaError:
            skipped += 1
    out = summarise(clips)
    if skipped:
        out['skipped'] = skipped
    return out


# ------------------------------------------------------------------ comparison
def _st(a, b):
    return abs(12 * math.log2(a / b)) if a and b and a > 0 and b > 0 else None


def match_score(candidate, target):
    """0-100: how close a candidate voice's measurements are to the target's (100 = identical on every
    measurement both have). Returns (score, components)."""
    comps = {}
    dist = {'f0_median_st': _st(candidate.get('f0_median_hz'), target.get('f0_median_hz')),
            'centroid_st': _st(candidate.get('centroid_hz'), target.get('centroid_hz'))}
    for k in ('f0_range_st', 'pauses_per_sentence', 'dynamics_lu'):
        a, b = candidate.get(k), target.get(k)
        dist[k] = abs(a - b) if a is not None and b is not None else None
    rate_key = 'words_per_s' if candidate.get('words_per_s') and target.get('words_per_s') else 'syllables_per_s'
    a, b = candidate.get(rate_key), target.get(rate_key)
    dist['rate'] = abs(a - b) / b if a and b else None
    total, wsum = 0.0, 0.0
    for k, (w, tol) in SCORE.items():
        if dist[k] is None:
            continue
        s = max(0.0, 1.0 - dist[k] / tol)
        comps[k] = {'distance': round(dist[k], 3), 'score': round(s, 3)}
        total += w * s
        wsum += w
    if not wsum:
        return None, comps
    return int(round(100 * total / wsum)), comps


# ------------------------------------------------------------------ storage
def save(name, source, measured, d=None):
    d = d or dbmod.get()
    pid = new_id('vp_')
    d.execute('INSERT INTO voice_profiles(id, name, source, measured, created_at) VALUES (?,?,?,?,?)',
              (pid, str(name)[:80], str(source)[:600], json.dumps(measured, sort_keys=True), now()))
    return pid


def get(pid, d=None):
    d = d or dbmod.get()
    r = d.one('SELECT * FROM voice_profiles WHERE id=?', (pid,))
    if r:
        r['measured'] = json.loads(r['measured'])
    return r


def list_profiles(d=None):
    d = d or dbmod.get()
    rows = d.query('SELECT * FROM voice_profiles ORDER BY created_at')
    for r in rows:
        r['measured'] = json.loads(r['measured'])
    return rows


def delete(pid, d=None):
    d = d or dbmod.get()
    d.execute('DELETE FROM voice_profiles WHERE id=?', (pid,))


# ------------------------------------------------------------------ owner uploads
def _safe_name(name):
    base = os.path.basename(name.replace('\\', '/'))
    return re.sub(r'[^A-Za-z0-9._-]', '_', base)[:120]


def unpack_upload(uploads, dest):
    """Write uploaded files (a zip or loose audio files) into ``dest``. Returns [(local_path, original_name)].
    Zip members are flattened to their base names; only audio extensions and an optional index.csv are kept."""
    out, total = [], 0
    for name, data in uploads:
        if len(data) > MAX_CLIP_BYTES and not name.lower().endswith('.zip'):
            raise ValueError(f'{name}: clips are limited to {MAX_CLIP_BYTES // (1024 * 1024)} MB each')
        if name.lower().endswith('.zip'):
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                raise ValueError(f'{name} is not a zip file')
            for info in zf.infolist():
                if info.is_dir():
                    continue
                base = _safe_name(info.filename)
                low = base.lower()
                if not (low.endswith(AUDIO_EXT) or low == 'index.csv'):
                    continue
                if info.file_size > MAX_CLIP_BYTES:
                    raise ValueError(f'{base}: clips are limited to {MAX_CLIP_BYTES // (1024 * 1024)} MB each')
                total += info.file_size
                if total > MAX_TOTAL_BYTES or len(out) >= MAX_CLIPS:
                    raise ValueError('Too much audio in one upload')
                path = os.path.join(dest, base)
                with zf.open(info) as src, open(path, 'wb') as fh:
                    shutil.copyfileobj(src, fh, 1 << 20)
                out.append((path, info.filename))
            continue
        base = _safe_name(name)
        if not (base.lower().endswith(AUDIO_EXT) or base.lower() == 'index.csv'):
            continue
        total += len(data)
        if total > MAX_TOTAL_BYTES or len(out) >= MAX_CLIPS:
            raise ValueError('Too much audio in one upload')
        path = os.path.join(dest, base)
        with open(path, 'wb') as fh:
            fh.write(data)
        out.append((path, name))
    return out


def read_index(path):
    """index.csv rows: file -> (text, group). Columns accepted: file; line/text; voice/group/speechify_voice."""
    rows = {}
    with open(path, encoding='utf-8', errors='replace', newline='') as fh:
        for r in csv.DictReader(fh):
            f = (r.get('file') or r.get('filename') or '').strip()
            if not f:
                continue
            text = (r.get('line') or r.get('text') or '').strip() or None
            group = (r.get('voice') or r.get('group') or r.get('speechify_voice') or '').strip() or None
            rows[_safe_name(f)] = (text, group)
    return rows


def import_upload(uploads, name, source, d=None):
    """Measure uploaded clips into one profile (or one per group named in index.csv), then delete the audio.
    Returns the created profiles. Only measurements and the provenance text are kept."""
    d = d or dbmod.get()
    name = str(name or '').strip()[:80] or 'Reference voice'
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='clips_', dir=config.WORK_DIR)
    try:
        files = unpack_upload(uploads, tmp)
        index = {}
        audio = []
        for path, original in files:
            if os.path.basename(path).lower() == 'index.csv':
                index.update(read_index(path))
            else:
                audio.append((path, original))
        if not audio:
            raise ValueError('No audio clips found (wav, mp3, m4a, ogg, flac, aac, opus or a zip of them)')
        groups = {}
        for path, original in audio:
            text, group = index.get(os.path.basename(path), (None, None))
            groups.setdefault(group or name, []).append((path, text))
        created = []
        for gname, items in sorted(groups.items()):
            measured = measure_files(items)
            if not measured.get('clips'):
                continue  # nothing decodable (or silent) in this group
            texts = sum(1 for _, t in items if t)
            prov = (f'{source or "owner upload"}; {len(items)} clips measured on {now_iso()}'
                    f'{", transcripts for " + str(texts) if texts else ", no transcripts"}; audio deleted after measuring')
            pid = save(gname if len(groups) > 1 else name, prov, measured, d)
            created.append(get(pid, d))
        if not created:
            raise ValueError('None of the clips could be decoded as audio')
        return created
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def now_iso():
    import datetime
    return datetime.datetime.fromtimestamp(now(), datetime.timezone.utc).strftime('%Y-%m-%d')
