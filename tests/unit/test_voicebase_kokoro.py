"""Kokoro engine: adapter (mocked runner), speed mapping, install verification, runner batch mode with a fake
package, and the real engine when it is installed."""
import copy
import hashlib
import json
import sys
import types
import wave

import numpy as np
import pytest

from blox import prefs
from blox.util import Blocked
from blox.voice import kokoro, kokoro_runner, tts


def fake_wav(path, seconds=1.0, sr=24000, f0=150.0):
    t = np.arange(int(seconds * sr)) / sr
    x = (0.3 * np.sin(2 * np.pi * f0 * t) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 4 * t))) * 32767).astype(np.int16)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


@pytest.fixture
def kokoro_files(tmp_path, monkeypatch):
    """A fake installed model (files exist) and a mocked runner that writes a tone for every batch item."""
    d = tmp_path / 'voices'
    monkeypatch.setenv('BLOX_VOICES_DIR', str(d))
    m = d / kokoro.DEFAULT_MODEL
    m.mkdir(parents=True)
    for f in kokoro.MODELS[kokoro.DEFAULT_MODEL]['files']:
        (m / f).write_bytes(b'x')
    monkeypatch.setattr(kokoro, 'engine_available', lambda: True)
    calls = []
    real_run = kokoro.media.run

    def run(args, timeout=None, **kw):
        if args[1:3] == ['-m', 'blox.voice.kokoro_runner']:
            with open(args[args.index('--batch') + 1], encoding='utf-8') as fh:
                items = json.load(fh)
            calls.append(items)
            for it in items:
                fake_wav(it['output'])
            return b'[]', b''
        return real_run(args, timeout=timeout, **kw)
    monkeypatch.setattr(kokoro.media, 'run', run)
    return calls


def test_speed_mapping_and_voice_defaults():
    base = {'pace': 'normal', 'emotion': 'neutral'}
    assert kokoro.speed_for(base) == 1.0
    assert kokoro.speed_for(dict(base, pace='fast')) > 1.0 > kokoro.speed_for(dict(base, pace='slow'))
    assert kokoro.speed_for(dict(base, speech_rate=1.3)) == pytest.approx(1.3, abs=0.01)
    assert kokoro.speed_for(dict(base, speech_rate=1.3), {'kokoro_speed': 1.9}) == 2.0, 'clamped to the model range'
    assert kokoro.speed_for(base, {'kokoro_speed': 'nonsense'}) == 1.0
    assert kokoro.voice_for('ch_bloxy', {}) == kokoro.DEFAULT_VOICES['ch_bloxy']
    assert kokoro.voice_for('ch_bloxy', {'kokoro_voice': 'bf_emma'}) == 'bf_emma'
    assert kokoro.voice_for('ch_new', {}) in kokoro.PRESETS
    assert kokoro.lang_for('bm_george') == 'en-gb' and kokoro.lang_for('af_heart') == 'en-us'
    assert not set(kokoro.PRESETS) & kokoro.EXCLUDED
    assert not {p.split('_', 1)[1] for p in kokoro.PRESETS} & set(tts.OPENAI_VOICES), \
        'no preset shares a name with the OpenAI catalogue'


def test_adapter_runs_the_runner_in_a_subprocess(db, kokoro_files, tmp_path):
    line = {'id': 'l1', 'text': 'Fixed it. Took four blocks.', 'emotion': 'proud', 'pace': 'normal', 'volume': 'normal',
            'speech_rate': 1.3}
    meta = kokoro.synthesize('Fixed it. Took four blocks.', str(tmp_path / 'k.wav'), 'af_heart', line)
    assert meta['license'].startswith('Apache-2.0') and meta['words'] is None
    items = kokoro_files[-1]
    assert items[0]['voice'] == 'af_heart' and items[0]['lang'] == 'en-us' and items[0]['speed'] == pytest.approx(1.3, abs=0.01)
    with pytest.raises(Blocked):
        kokoro.synthesize('hi', str(tmp_path / 'x.wav'), 'af_alloy', line), 'excluded preset'
    with pytest.raises(Blocked):
        kokoro.synthesize('hi', str(tmp_path / 'x.wav'), 'not a voice', line)


def test_character_engine_override_uses_kokoro_and_estimated_timing(db, kokoro_files, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    assert p['production']['tts_provider'] == 'piper', 'the studio default stays Piper'
    line = {'id': 'l2', 'text': 'Wait. Was that door always there?', 'emotion': 'startled', 'pace': 'fast',
            'volume': 'loud'}
    char = {'id': 'ch_dot', 'name': 'Dot', 'voice': {'engine': 'kokoro', 'kokoro_voice': 'af_heart', 'kokoro_speed': 1.1}}
    res = tts.synthesize_line(line, char, p, str(tmp_path / 'v'), 'vid1')
    assert res['provider'] == 'kokoro' and res['test_voice'] is False
    assert res['alignment_kind'] == 'estimated' and len(res['words']) == 6
    assert res['voice_meta']['voice'] == 'af_heart' and 'Kokoro' in res['voice_meta']['attribution']
    assert 'highshelf' in res['post']['filter'], 'loud lines get the raised-voice colour like Piper'
    assert tts.estimate_cost(line['text'], 'kokoro', p['budget']['prices']) == 0.0
    # The same spec reuses the raw file (no second runner call); a different voice renders again.
    n = len(kokoro_files)
    tts.synthesize_line(line, char, p, str(tmp_path / 'v'), 'vid1')
    assert len(kokoro_files) == n
    char2 = dict(char, voice=dict(char['voice'], kokoro_voice='bf_emma'))
    tts.synthesize_line(line, char2, p, str(tmp_path / 'v'), 'vid1')
    assert len(kokoro_files) == n + 1


def test_batch_renders_once_per_script(db, kokoro_files, tmp_path):
    line = {'id': 'x', 'text': 'a', 'emotion': 'neutral', 'pace': 'normal', 'volume': 'normal'}
    reqs = [(f'line {i}', str(tmp_path / f'{i}.wav'), 'am_michael', dict(line, id=str(i)), {}) for i in range(3)]
    metas = kokoro.synthesize_batch(reqs)
    assert len(metas) == 3 and len(kokoro_files) == 1 and len(kokoro_files[0]) == 3
    kokoro.synthesize_batch(reqs)
    assert len(kokoro_files) == 1, 'existing outputs are not rendered again'


def test_missing_model_blocks_with_install_hint(tmp_path, monkeypatch):
    monkeypatch.setenv('BLOX_VOICES_DIR', str(tmp_path / 'none'))
    with pytest.raises(Blocked) as e:
        kokoro.synthesize('hi', str(tmp_path / 'x.wav'), 'af_heart', {'pace': 'normal', 'emotion': 'neutral'})
    assert '--engine kokoro' in str(e.value)
    assert kokoro.status()['models'][kokoro.DEFAULT_MODEL]['installed'] is False


def test_install_verifies_each_checksum_from_github_only(tmp_path, monkeypatch):
    monkeypatch.setenv('BLOX_VOICES_DIR', str(tmp_path / 'v'))
    payload = {'kokoro-v1.0.onnx': b'model-bytes', 'voices-v1.0.bin': b'voices-bytes'}
    spec = copy.deepcopy(kokoro.MODELS)
    for name, data in payload.items():
        spec[kokoro.DEFAULT_MODEL]['files'][name]['sha256'] = hashlib.sha256(data).hexdigest()
        assert spec[kokoro.DEFAULT_MODEL]['files'][name]['url'].startswith('https://github.com/')
    monkeypatch.setattr(kokoro, 'MODELS', spec)
    fetched = []

    def fetch(url, provider, dest=None, allow_hosts=None, max_bytes=None, **kw):
        fetched.append((url, allow_hosts))
        with open(dest, 'wb') as f:
            f.write(payload[url.rsplit('/', 1)[1]])
    monkeypatch.setattr(kokoro.netsafe, 'fetch', fetch)
    kokoro.install()
    assert kokoro.installed() and len(fetched) == 2
    assert all(hosts == ['github.com', 'githubusercontent.com'] for _, hosts in fetched)
    # A tampered download leaves nothing installed.
    monkeypatch.setenv('BLOX_VOICES_DIR', str(tmp_path / 'w'))
    payload['voices-v1.0.bin'] = b'tampered'
    with pytest.raises(Blocked):
        kokoro.install()
    assert not kokoro.installed()
    assert not (tmp_path / 'w' / kokoro.DEFAULT_MODEL / 'voices-v1.0.bin').exists()


def test_runner_batch_mode_with_fake_package(tmp_path, monkeypatch):
    """The runner writes one WAV per item from the package's documented create() API (audio, sample rate)."""
    created = []

    class FakeKokoro:
        def __init__(self, model, voices):
            created.append((model, voices))

        def create(self, text, voice, speed=1.0, lang='en-us'):
            n = int(24000 * 0.3 * max(1, len(text.split())))
            return np.sin(2 * np.pi * 150 * np.arange(n) / 24000).astype(np.float32) * 0.5, 24000

    monkeypatch.setitem(sys.modules, 'kokoro_onnx', types.SimpleNamespace(Kokoro=FakeKokoro))
    batch = tmp_path / 'b.json'
    outs = [str(tmp_path / f'{i}.wav') for i in range(2)]
    batch.write_text(json.dumps([{'text': 'one two', 'output': outs[0], 'voice': 'af_heart', 'speed': 1.2, 'lang': 'en-us'},
                                 {'text': 'three', 'output': outs[1], 'voice': 'bm_george', 'speed': 0.9, 'lang': 'en-gb'}]))
    assert kokoro_runner.main(['--model', 'm.onnx', '--voices', 'v.bin', '--batch', str(batch)]) == 0
    assert created == [('m.onnx', 'v.bin')], 'the model loads once for the whole batch'
    for o in outs:
        with wave.open(o, 'rb') as w:
            assert w.getframerate() == 24000 and w.getnchannels() == 1 and w.getnframes() > 1000
    # single-line mode
    (tmp_path / 'in.txt').write_text('hello')
    assert kokoro_runner.main(['--model', 'm.onnx', '--voices', 'v.bin', '--voice', 'af_heart', '-i', str(tmp_path / 'in.txt'),
                               '-f', str(tmp_path / 's.wav')]) == 0
    assert (tmp_path / 's.wav').exists() and not (tmp_path / 's.wav.part').exists()


def test_blox_never_imports_the_gpl_phonemiser():
    """Only the runner (a separate process) may import kokoro_onnx / phonemizer / espeakng_loader."""
    import pathlib
    root = pathlib.Path(kokoro.__file__).resolve().parents[1]
    for f in root.rglob('*.py'):
        src = f.read_text(encoding='utf-8')
        if f.name == 'kokoro_runner.py':
            continue
        for mod in ('kokoro_onnx', 'phonemizer', 'espeakng_loader'):
            assert f'import {mod}' not in src and f'from {mod}' not in src, f'{f} imports {mod}'


@pytest.mark.skipif(not (kokoro.installed() and kokoro.engine_available()), reason='Kokoro model not installed')
def test_real_kokoro_reads_a_short_line(db, tmp_path):
    from blox.voice import profiles as P
    line = {'id': 'real', 'text': 'Nine out of ten.', 'emotion': 'neutral', 'pace': 'normal', 'volume': 'normal'}
    meta = kokoro.synthesize(line['text'], str(tmp_path / 'real.wav'), 'am_michael', line)
    m = P.measure_clip(meta['path'], line['text'])
    assert 0.5 < m['duration_s'] < 4.0 and m['speech_s'] > 0.3
    prof = P.summarise([m])
    assert 60 < prof['f0_median_hz'] < 300, prof
