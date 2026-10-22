"""Kokoro: a second free, offline voice engine (no account, no per-use cost).

Kokoro-82M weights are released under Apache-2.0 (hexgrad/kokoro README: "With Apache-licensed weights, Kokoro
can be deployed anywhere"); the ONNX export and the ``kokoro-onnx`` package come from github.com/thewh1teagle/
kokoro-onnx (code MIT; its README states "kokoro model: Apache 2.0"). Text is turned into phonemes by
``phonemizer-fork`` on top of the espeak-ng shared library (``espeakng-loader``): both are GPL-3.0, so, exactly
like Piper, they only ever run inside a separate process (``python -m blox.voice.kokoro_runner``) started here with
files in and a WAV out; Blox itself never imports ``kokoro_onnx``, ``phonemizer`` or ``espeakng_loader``. The
audio an engine produces is not covered by the engine's licence.

Model files are installed through the same checksum-pinned installer as Piper: GitHub release URLs only, the
sha256 observed on 2026-10-11 is pinned below, and a mismatch leaves nothing installed.

kokoro-onnx 0.4.9 returns audio only (no word timestamps), so caption timing for Kokoro lines is estimated from
the audio, like Piper. The 54 voice presets in voices-v1.0.bin are synthetic style vectors shipped with the
model; the English ones whose names do not coincide with a commercial catalogue voice are listed as presets
(``PRESETS``); the owner may still choose any other preset by name except those in ``EXCLUDED``.
"""
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

from .. import config, media, netsafe
from ..util import Blocked
from .piper import PACE, speech_rate, voices_dir

RELEASE = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/'
MODELS = {
    'kokoro-v1.0': {
        'files': {
            'kokoro-v1.0.onnx': {'url': RELEASE + 'kokoro-v1.0.onnx', 'bytes': 325532387,
                                 'sha256': '7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5'},
            'voices-v1.0.bin': {'url': RELEASE + 'voices-v1.0.bin', 'bytes': 28214398,
                                'sha256': 'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d'},
        },
        'license': 'Apache-2.0 (Kokoro-82M weights); kokoro-onnx MIT',
        'attribution': 'Voices generated with Kokoro-82M (Apache-2.0) through kokoro-onnx (MIT).',
        'sample_rate': 24000,
    },
}
DEFAULT_MODEL = 'kokoro-v1.0'
SAMPLE_RATE = 24000

# English presets offered in the bake-off. Prefix: a = American English, b = British English; f/m = the style's
# register. Names that coincide with a voice in a commercial catalogue (OpenAI's alloy/echo/fable/onyx/nova,
# "sky", "puck", "santa") are left out: their provenance cannot be checked from here and Blox never sets out to
# sound like another product's voice. Qualities only; see the bake-off for measured pitch, pace and energy.
PRESETS = ['af_bella', 'af_heart', 'af_nicole', 'af_sarah', 'af_kore', 'af_aoede', 'af_river',
           'am_michael', 'am_fenrir', 'am_adam', 'am_eric', 'am_liam',
           'bf_emma', 'bf_isabella', 'bf_alice', 'bm_george', 'bm_lewis', 'bm_daniel']
EXCLUDED = {'af_alloy', 'af_nova', 'af_sky', 'am_echo', 'am_onyx', 'am_puck', 'am_santa', 'bm_fable', 'em_santa',
            'pm_santa'}
# Starting points for the built-in cast (an owner's "Use this voice" in the bake-off replaces them).
DEFAULT_VOICES = {'ch_bloxy': 'am_michael', 'ch_pip': 'bm_lewis', 'ch_rook': 'am_fenrir', 'ch_dot': 'af_heart',
                  'ch_tally': 'bf_emma'}
SPEED_RANGE = (0.5, 2.0)
VOICE_RE = r'[a-z]{2}_[a-z]+'


def model_dir(name=DEFAULT_MODEL):
    if name not in MODELS:
        raise Blocked(f'Unknown Kokoro model {name!r}', state='needs_review')
    return voices_dir() / name


def model_paths(name=DEFAULT_MODEL):
    base = model_dir(name)
    onnx, voices = [base / f for f in MODELS[name]['files']]
    return onnx, voices


def installed(name=DEFAULT_MODEL):
    try:
        onnx, voices = model_paths(name)
    except Blocked:
        return False
    return onnx.exists() and voices.exists()


def engine_available():
    """The kokoro-onnx package is importable in a child process (checked without importing it here)."""
    import importlib.util
    return importlib.util.find_spec('kokoro_onnx') is not None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def install(name=DEFAULT_MODEL):
    """Download the model and voices files from the pinned GitHub release URLs, verify each sha256 and only then
    move them into place."""
    spec = MODELS[name]
    dest = model_dir(name)
    if installed(name):
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        staged = []
        for fname, f in spec['files'].items():
            path = os.path.join(tmp, fname)
            netsafe.fetch(f['url'], 'kokoro_models', dest=path, allow_hosts=['github.com', 'githubusercontent.com'],
                          max_bytes=f['bytes'] + 1024 * 1024)
            if _sha256(path) != f['sha256']:
                raise Blocked(f'Checksum mismatch for Kokoro file {fname}; not installed', state='blocked')
            staged.append((path, dest / fname))
        for src, out in staged:
            os.replace(src, out)
    if not installed(name):
        raise Blocked(f'Kokoro model {name} did not install completely', state='blocked')
    return dest


def voice_for(character_id, voice):
    v = (voice or {}).get('kokoro_voice') or DEFAULT_VOICES.get(character_id)
    if not v:
        v = PRESETS[int(hashlib.sha256(str(character_id).encode()).hexdigest(), 16) % len(PRESETS)]
    return str(v)


def lang_for(voice_name):
    return 'en-gb' if voice_name.startswith('b') else 'en-us'


def speed_for(line, voice=None):
    """Kokoro's native speed (1.0 = the voice's own pace): the character's kokoro_speed times the line's pace and
    the production speech rate, clamped to what the model accepts."""
    try:
        base = float((voice or {}).get('kokoro_speed', 1.0))
    except (TypeError, ValueError):
        base = 1.0
    if not math.isfinite(base):
        base = 1.0
    base = min(SPEED_RANGE[1], max(SPEED_RANGE[0], base))
    pace = 1.0 / PACE.get(line.get('pace', 'normal'), 1.0)
    speed = base * pace * speech_rate(line)
    if line.get('emotion') in ('sad', 'bored', 'tired'):
        speed *= 0.96
    return round(min(SPEED_RANGE[1], max(SPEED_RANGE[0], speed)), 3)


def plan(text, out_path, voice_name, line, model=DEFAULT_MODEL, voice=None):
    """Validate a request and return (runner item, result metadata) without running anything."""
    import re
    if not installed(model):
        raise Blocked('The free Kokoro voice model is not installed. Run: python -m blox.cli install-voice '
                      '--engine kokoro', state='blocked')
    if not engine_available():
        raise Blocked('The kokoro-onnx package is not installed (pip install -r requirements.lock)', state='blocked')
    if not re.fullmatch(VOICE_RE, voice_name or '') or voice_name in EXCLUDED:
        raise Blocked(f'Kokoro voice {voice_name!r} is not available', state='needs_review')
    speed = speed_for(line, voice)
    item = {'text': text, 'output': str(out_path), 'voice': voice_name, 'speed': speed, 'lang': lang_for(voice_name)}
    meta = {'path': str(out_path), 'model': model, 'voice': voice_name, 'license': MODELS[model]['license'],
            'attribution': MODELS[model]['attribution'], 'controls': {'speed': speed, 'lang': lang_for(voice_name)},
            # kokoro-onnx 0.4.9 returns audio only; word timing is estimated downstream.
            'words': None}
    return item, meta


def _run_batch(items, model):
    onnx, voices = model_paths(model)
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, dir=config.WORK_DIR, encoding='utf-8') as tf:
        json.dump(items, tf)
        bpath = tf.name
    try:
        media.run([sys.executable, '-m', 'blox.voice.kokoro_runner', '--model', str(onnx), '--voices', str(voices),
                   '--batch', bpath], timeout=600 + 60 * len(items), cwd=str(config.APP_ROOT))
    finally:
        os.unlink(bpath)
    for it in items:
        if not os.path.exists(it['output']) or os.path.getsize(it['output']) < 2000:
            raise media.MediaError('Kokoro produced no audio')


def synthesize(text, out_path, voice_name, line, model=DEFAULT_MODEL, voice=None):
    item, meta = plan(text, out_path, voice_name, line, model, voice)
    _run_batch([item], model)
    return meta


def synthesize_batch(requests, model=DEFAULT_MODEL):
    """Several lines in one runner process (the model loads once). requests: [(text, out_path, voice_name, line,
    voice_cfg)]; returns the metadata list. Outputs that already exist are skipped."""
    items, metas = [], []
    for text, out_path, voice_name, line, voice in requests:
        item, meta = plan(text, out_path, voice_name, line, model, voice)
        metas.append(meta)
        if not (os.path.exists(out_path) and os.path.getsize(out_path) > 2000):
            items.append(item)
    if items:
        _run_batch(items, model)
    return metas


def list_voices(model=DEFAULT_MODEL):
    """Every preset in the installed voices file (minus EXCLUDED), via the runner; PRESETS when not installed."""
    if not installed(model) or not engine_available():
        return list(PRESETS)
    _, voices = model_paths(model)
    out, _ = media.run([sys.executable, '-m', 'blox.voice.kokoro_runner', '--voices', str(voices), '--list-voices'],
                       timeout=120, cwd=str(config.APP_ROOT))
    names = json.loads(out.decode() or '[]')
    return [n for n in names if n not in EXCLUDED]


def status():
    return {'models': {k: {'installed': installed(k), 'license': v['license'], 'files': list(v['files'])}
                       for k, v in MODELS.items()},
            'engine_available': engine_available(), 'presets': list(PRESETS), 'dir': str(voices_dir())}


def describe():
    return json.dumps(status())


def expected_dir():
    return Path(voices_dir())
