"""Production pace: voices speak at the speech rate natively; line fitting never goes above 1.5x in total."""
import copy
import itertools
import wave

import numpy as np
import pytest

from blox import demo, prefs, production
from blox.manifest import compile as C
from blox.voice import audio as A, piper, tts

from ..conftest import have


def fake_wav(path, seconds=1.2, sr=22050):
    t = np.arange(int(seconds * sr)) / sr
    x = (0.3 * np.sin(2 * np.pi * 160 * t) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 4 * t))) * 32767).astype(np.int16)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


@pytest.fixture
def piper_calls(tmp_path, monkeypatch):
    d = tmp_path / 'voices'
    monkeypatch.setenv('BLOX_VOICES_DIR', str(d))
    m = d / piper.DEFAULT_MODEL
    m.mkdir(parents=True)
    (m / f'{piper.DEFAULT_MODEL}.onnx').write_bytes(b'onnx')
    (m / f'{piper.DEFAULT_MODEL}.onnx.json').write_text('{}')
    calls = []
    real_run = piper.media.run

    def run(args, timeout=None, **kw):
        if args[1:3] == ['-m', 'piper']:
            calls.append(args)
            fake_wav(args[args.index('-f') + 1])
            return b'', b''
        return real_run(args, timeout=timeout, **kw)
    monkeypatch.setattr(piper.media, 'run', run)
    return calls


LINE = {'id': 'l1', 'speaker': 'hero', 'text': 'Wait... where is it?', 'emotion': 'neutral', 'pace': 'normal',
        'volume': 'normal'}


def test_piper_speaks_faster_through_length_scale():
    assert piper.delivery(LINE)[0] == 1.0
    assert piper.delivery(dict(LINE, speech_rate=1.3))[0] == round(1 / 1.3, 3)
    fast = dict(LINE, pace='fast', emotion='sad', speech_rate=1.3)
    assert piper.delivery(fast)[0] == round(0.9 * 1.04 / 1.3, 3)
    assert piper.delivery(dict(LINE, speech_rate='bogus'))[0] == 1.0


def test_synthesis_passes_the_rate_and_keeps_old_cache_keys(db, piper_calls, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    who = {'id': 'ch_bloxy', 'name': 'Bloxy', 'voice': {}}
    old = tts.synthesize_line(LINE, who, p, str(tmp_path / 'v'), 'vid1')
    same = tts.synthesize_line(dict(LINE, speech_rate=1.0), who, p, str(tmp_path / 'v'), 'vid1')
    assert same['spec_hash'] == old['spec_hash'], 'real-time lines keep their cache key'
    quick = tts.synthesize_line(dict(LINE, speech_rate=1.3), who, p, str(tmp_path / 'v'), 'vid1')
    assert quick['spec_hash'] != old['spec_hash']
    args = piper_calls[-1]
    assert float(args[args.index('--length-scale') + 1]) == pytest.approx(1 / 1.3, abs=1e-3)
    assert quick['voice_meta']['controls']['length_scale'] == pytest.approx(1 / 1.3, abs=1e-3)


@pytest.fixture
def sent(monkeypatch, tmp_path):
    calls = []

    class Resp:
        content = b'\0' * 4000

        @staticmethod
        def json():
            return {'audio_base64': '', 'alignment': None}

    def request(provider, method, url, **kw):
        calls.append(kw.get('json'))
        return Resp()
    monkeypatch.setattr(tts, 'request', request)
    monkeypatch.setattr(tts.vault, 'get', lambda k: 'test-key')
    return calls


@pytest.mark.parametrize('model,native', [('tts-1', True), ('tts-1-hd', True), ('gpt-4o-mini-tts', False)])
def test_openai_uses_native_speed_where_the_model_supports_it(sent, tmp_path, model, native):
    line = dict(LINE, speech_rate=1.3)
    instr = tts.instructions(line, 'Bloxy', {})
    tts._openai_tts('Hi', 'alloy', instr, model, str(tmp_path / 'o.wav'), speed=tts.line_rate(line))
    body = sent[-1]
    assert body.get('speed') == (1.3 if native else None)
    assert '1.3 times faster' in body['instructions']
    tts._openai_tts('Hi', 'alloy', tts.instructions(LINE, 'Bloxy', {}), model, str(tmp_path / 'o.wav'))
    assert 'speed' not in sent[-1] and 'faster' not in sent[-1]['instructions']


def test_elevenlabs_speed_is_clamped_to_its_range(sent, tmp_path):
    for rate, expect in ((1.3, 1.2), (1.1, 1.1), (0.8, 0.8), (1.0, None)):
        tts._elevenlabs_tts('Hi', 'abcdefgh12', dict(LINE, speech_rate=rate), 'eleven_multilingual_v2',
                            str(tmp_path / 'e.mp3'))
        assert sent[-1]['voice_settings'].get('speed') == expect


def manifest(pace, rate):
    return {'pace': {'timeline': pace, 'speech_rate': rate}}


def test_tempo_cap_tops_up_to_the_pace_and_never_exceeds_one_and_a_half():
    assert C.tempo_cap({}, 1.06) == 1.06  # manifests from before pace existed
    assert C.tempo_cap(manifest(1.0, 1.0), 1.06) == 1.06
    assert C.tempo_cap(manifest(1.5, 1.3), 1.06) == pytest.approx(1.5 / 1.3)
    assert C.tempo_cap(manifest(1.2, 1.3), 1.06) == 1.06
    for pace, rate, mt in itertools.product((1.0, 1.25, 1.5, 1.75, 2.0), (0.8, 1.0, 1.3, 1.5, 1.6), (1.0, 1.06, 1.15)):
        cap = C.tempo_cap(manifest(pace, rate), mt)
        assert cap >= 1.0 and (rate * cap <= 1.5 + 1e-9 or cap == 1.0)


@pytest.mark.skipif(not have('ffmpeg'), reason='needs FFmpeg')
@pytest.mark.parametrize('pace,rate,over,action', [(1.5, 1.3, 1.12, 'tempo'), (1.5, 1.3, 1.2, 'rewrite_needed'),
                                                   (1.0, 1.0, 1.12, 'rewrite_needed')])
def test_retime_fits_with_tempo_only_within_the_cap(tmp_path, pace, rate, over, action):
    m = C.compile_plan(demo.plan(), fps=30, width=540, height=960, pace=pace, speech_rate=rate)
    m['lines'] = m['lines'][:2]
    first, second = m['lines']
    window = (second['start_frame'] - first['start_frame']) / m['fps']
    secs = window * over
    path = str(tmp_path / 'l1.wav')
    t = np.arange(int(secs * A.SR)) / A.SR
    A.write_wav(path, 0.3 * np.sin(2 * np.pi * 200 * t))
    res = {'file': path, 'duration_s': round(len(t) / A.SR, 3), 'words': [{'word': 'hey', 'start': 0.0, 'end': secs}],
           'alignment_kind': 'estimated'}
    p = copy.deepcopy(prefs.DEFAULTS)
    m2, results, report = production.retime(m, {first['id']: res}, p, max_shift_s=0.0)
    got = [r for r in report if r['line'] == first['id']]
    assert got and got[0]['action'] == action, report
    if action == 'tempo':
        assert got[0]['ratio'] <= 1.5 / rate + 1e-6
        assert results[first['id']]['duration_s'] <= window + 0.02
