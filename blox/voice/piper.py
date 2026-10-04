"""Free, offline voices with Piper (no account, no per-use cost).

Piper (piper-tts, GPL-3.0) runs as a separate process; Blox never imports it, and the audio it
produces is not covered by its licence. Only voice models whose training data allows commercial use
are listed in ``VOICES``; their licence and attribution are recorded with every line and added to
the video description.

Piper has no word timestamps, so alignment is estimated from the audio's voiced regions (labelled
``estimated``) unless a speech-recognition provider is connected. Emotion is approximated through
pace and variation only: these are calm reading voices, less expressive than instruction-following
cloud voices.
"""
import hashlib
import json
import math
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

from .. import config, media, netsafe
from ..util import Blocked

VOICES = {
    'en-us-libritts-high': {
        'url': 'https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-libritts-high.tar.gz',
        'sha256': '328e3e9cb573a43a6c5e1aeca386e971232bdb1418a74d4674cf726c973a0ea8',
        'files': ['en-us-libritts-high.onnx', 'en-us-libritts-high.onnx.json', 'MODEL_CARD'],
        'speakers': 904,
        'license': 'CC BY 4.0',
        'dataset': 'LibriTTS (openslr.org/60)',
        'attribution': 'Voices generated with Piper TTS using a model trained on LibriTTS (CC BY 4.0, '
                       'openslr.org/60).',
    },
}
DEFAULT_MODEL = 'en-us-libritts-high'
# Defaults for the built-in cast, auditioned by measured pitch, pitch spread, brightness and natural rate (no
# listening was possible; see blox/demo.py for the measurements). Change them on the Characters page.
DEFAULT_SPEAKERS = {'ch_bloxy': 744, 'ch_pip': 288, 'ch_rook': 456, 'ch_dot': 408, 'ch_tally': 504}

PACE = {'slow': 1.12, 'normal': 1.0, 'fast': 0.9}
LIVELY = {'excited', 'startled', 'angry', 'laughing', 'scared', 'happy', 'proud'}
SUBDUED = {'sad', 'bored', 'embarrassed', 'tired', 'worried', 'disappointed'}
# Piper's neutral variation (noise_scale, noise_w) and the shifts applied for lively and subdued emotions. A
# character's ``piper_noise_scale`` / ``piper_noise_w`` replace the neutral values; the shifts still apply.
NOISE = (0.667, 0.8)
LIVELY_SHIFT = (0.083, 0.1)
SUBDUED_SHIFT = (-0.167, -0.2)

# Speed calibration. Piper's length_scale stretches the predicted phoneme durations, but part of every line does
# not scale with it (durations are rounded up to whole frames, the inserted blanks and the edges of the line), so
# length_scale 1/1.3 = 0.769 delivered only about 1.12x. A line's duration relative to length_scale 1 is modelled as
#     duration_ratio = FIXED + (1 - FIXED) * length_scale
# so the length_scale that delivers a speed-up s is (1/s - FIXED) / (1 - FIXED).
# Measured (trimmed the way the pipeline trims): over 12 lines x 5 cast speakers x 2 takes at length_scale
# 0.9/0.8/0.7/0.62/0.55/0.5 the speed-up was 1.056/1.143/1.250/1.361/1.460/1.536x (least squares FIXED = 0.31);
# through the full voice chain with each character's own settings (12 lines x 3 takes x 5 speakers, speech_rate
# 1.0 vs 1.3) FIXED = 0.31 delivered 1.266x and FIXED = 0.38 delivered 1.301x and 1.317x in two runs (per speaker
# 1.24-1.37x), so 0.38 is used: length_scale 0.628 for 1.3x.
LENGTH_FIXED = 0.38
LENGTH_RANGE = (0.5, 1.6)

# Per-character overrides read from the character's voice config, with their allowed ranges.
VOICE_LIMITS = {'piper_length_scale': (0.7, 1.4), 'piper_noise_scale': (0.1, 1.0), 'piper_noise_w': (0.1, 1.2),
                'narration_length_scale': (0.8, 1.2), 'pitch_semitones': (-6.0, 6.0)}
PITCH_FORMANTS = ('preserve', 'shift')


def voices_dir():
    return Path(os.environ.get('BLOX_VOICES_DIR') or (config.DATA_DIR / 'voices'))


def model_paths(name=DEFAULT_MODEL):
    if name not in VOICES:
        raise Blocked(f'Unknown Piper voice model {name!r}', state='needs_review')
    base = voices_dir() / name
    return base / f'{name}.onnx', base / f'{name}.onnx.json'


def installed(name=DEFAULT_MODEL):
    onnx, cfg = model_paths(name)
    return onnx.exists() and cfg.exists()


def engine_available():
    try:
        media.run([sys.executable, '-m', 'piper', '--help'], timeout=60)
        return True
    except media.MediaError:
        return False


def install(name=DEFAULT_MODEL):
    """Download a listed voice model, verify its checksum and unpack only the expected files."""
    spec = VOICES[name]
    dest = voices_dir() / name
    if installed(name):
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        archive = os.path.join(tmp, 'voice.tar.gz')
        netsafe.fetch(spec['url'], 'piper_voices', dest=archive, allow_hosts=['github.com', 'githubusercontent.com'],
                      max_bytes=400 * 1024 * 1024)
        h = hashlib.sha256()
        with open(archive, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        if h.hexdigest() != spec['sha256']:
            raise Blocked(f'Checksum mismatch for voice model {name}; not installed', state='blocked')
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                base = os.path.basename(member.name)
                if member.isfile() and base in spec['files']:
                    with tar.extractfile(member) as src, open(dest / base, 'wb') as out:
                        shutil.copyfileobj(src, out)
    if not installed(name):
        raise Blocked(f'Voice model {name} archive did not contain the expected files', state='blocked')
    return dest


def speaker_for(character_id, voice):
    sp = voice.get('piper_speaker')
    if sp is None or sp == '':
        sp = DEFAULT_SPEAKERS.get(character_id, int(hashlib.sha256(str(character_id).encode()).hexdigest(), 16) % 904)
    return int(sp)


def speech_rate(line):
    """Native speaking speed for a line (compiled from the owner's speech_rate; 1.0 = normal)."""
    try:
        rate = float(line.get('speech_rate', 1.0))
    except (TypeError, ValueError):
        return 1.0
    return rate if 0.5 <= rate <= 2.0 else 1.0


def voice_settings(voice):
    """A character's Piper delivery overrides, validated and clamped (missing or invalid values use defaults).

    piper_length_scale: multiplier on phoneme length (above 1 is slower); piper_noise_scale / piper_noise_w: the
    neutral variation; narration_length_scale: extra multiplier for the character's narration lines;
    pitch_semitones and pitch_formants ('preserve' or 'shift'): the post-process pitch shift."""
    voice = voice or {}
    out = {}
    for key, (lo, hi) in VOICE_LIMITS.items():
        try:
            x = float(voice.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(x):
            out[key] = min(hi, max(lo, x))
    f = voice.get('pitch_formants')
    out['pitch_formants'] = f if f in PITCH_FORMANTS else 'preserve'
    return out


def calibrated_length(speedup):
    """length_scale that makes Piper deliver ``speedup`` times the speed of length_scale 1 (see LENGTH_FIXED)."""
    length = (1.0 / max(0.25, float(speedup)) - LENGTH_FIXED) / (1.0 - LENGTH_FIXED)
    return min(LENGTH_RANGE[1], max(LENGTH_RANGE[0], length))


def delivery(line, voice=None):
    """Map the line's direction and the character's voice settings onto Piper's controls (speed and variation).

    The production speech rate is applied natively through length_scale (Piper's phoneme durations, pauses
    included), so faster speech is synthesised faster rather than time-stretched afterwards. The requested speed
    (pace, emotion, character and speech rate together) is converted with the measured calibration, so a line asked
    to be 1.3x faster is delivered about 1.3x faster."""
    vs = voice_settings(voice)
    length = PACE.get(line.get('pace', 'normal'), 1.0) * vs.get('piper_length_scale', 1.0)
    noise, noise_w = vs.get('piper_noise_scale', NOISE[0]), vs.get('piper_noise_w', NOISE[1])
    emo = line.get('emotion', 'neutral')
    if emo in LIVELY:
        noise, noise_w = noise + LIVELY_SHIFT[0], noise_w + LIVELY_SHIFT[1]
    elif emo in SUBDUED:
        noise, noise_w = noise + SUBDUED_SHIFT[0], noise_w + SUBDUED_SHIFT[1]
        length *= 1.04
    if line.get('kind') == 'narration':
        length *= vs.get('narration_length_scale', 1.0)
    length = calibrated_length(speech_rate(line) / length)
    noise = min(1.0, max(0.1, noise))
    noise_w = min(1.2, max(0.1, noise_w))
    return round(length, 3), round(noise, 3), round(noise_w, 3)


def synthesize(text, out_path, speaker, line, model=DEFAULT_MODEL, voice=None):
    onnx, cfg = model_paths(model)
    if not installed(model):
        raise Blocked('The free Piper voice model is not installed. Run: python -m blox.cli install-voice',
                      state='blocked')
    n = VOICES[model]['speakers']
    if not 0 <= speaker < n:
        raise Blocked(f'Piper speaker {speaker} is outside 0-{n - 1}', state='needs_review')
    length, noise, noise_w = delivery(line, voice)
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, dir=config.WORK_DIR, encoding='utf-8') as tf:
        tf.write(text)
        tpath = tf.name
    try:
        media.run([sys.executable, '-m', 'piper', '-m', str(onnx), '-c', str(cfg), '-s', str(speaker),
                   '--length-scale', str(length), '--noise-scale', str(noise), '--noise-w-scale', str(noise_w),
                   '-i', tpath, '-f', out_path], timeout=300)
    finally:
        os.unlink(tpath)
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 2000:
        raise media.MediaError('Piper produced no audio')
    return {'path': out_path, 'model': model, 'speaker': speaker,
            'license': VOICES[model]['license'], 'attribution': VOICES[model]['attribution'],
            'controls': {'length_scale': length, 'noise_scale': noise, 'noise_w': noise_w}}


def status():
    return {'models': {k: {'installed': installed(k), 'license': v['license'], 'dataset': v['dataset'],
                           'speakers': v['speakers']} for k, v in VOICES.items()},
            'dir': str(voices_dir())}


def describe():
    return json.dumps(status())
