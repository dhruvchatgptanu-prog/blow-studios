"""Per-character voices: pitch shift, Piper delivery overrides, speed calibration, gap squeeze, loud lines."""
import copy
import wave

import numpy as np
import pytest

from blox import prefs
from blox.voice import audio as A, piper, tts

SR = 48000


def voiced(seconds=1.2, f0=140.0, sr=SR):
    """A harmonic 'voice' with syllable-like amplitude: pitch is unambiguous."""
    t = np.arange(int(seconds * sr)) / sr
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 8))
    return 0.25 * x * (0.55 + 0.45 * np.sin(2 * np.pi * 3 * t))


def f0_of(x, sr=SR):
    seg = x[len(x) // 4:len(x) // 4 + int(0.25 * sr)]
    seg = seg - seg.mean()
    ac = np.fft.irfft(np.abs(np.fft.rfft(seg, 2 * len(seg))) ** 2)[:len(seg)]
    lo, hi = int(sr / 500), int(sr / 60)
    lag = lo + int(np.argmax(ac[lo:hi]))
    # Parabolic interpolation for sub-sample accuracy.
    a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
    return sr / (lag + 0.5 * (a - c) / (a - 2 * b + c))


@pytest.mark.parametrize('engine', ['rubberband', 'fallback'])
@pytest.mark.parametrize('semitones', [4.0, -2.0])
def test_pitch_shift_moves_pitch_and_keeps_duration(tmp_path, monkeypatch, engine, semitones):
    if engine == 'rubberband' and not A.has_filter('rubberband'):
        pytest.skip('this FFmpeg build has no librubberband')
    if engine == 'fallback':
        monkeypatch.setattr(A, 'has_filter', lambda name: False)
    af = A.pitch_filter(semitones)
    assert af.startswith('rubberband=') == (engine == 'rubberband')
    src = A.write_wav(tmp_path / 'in.wav', voiced())
    out = A.apply_filter(src, tmp_path / 'out.wav', af)
    x, y = A.decode(src), A.decode(out)
    measured = 12 * np.log2(f0_of(y) / f0_of(x))
    assert measured == pytest.approx(semitones, abs=0.25)
    assert len(y) / len(x) == pytest.approx(1.0, abs=0.03), 'the duration is kept'


def test_post_filter_combines_character_pitch_and_loud_colour():
    line = {'volume': 'normal'}
    assert tts.post_filter('piper', {}, line) == ''
    assert tts.post_filter('openai', {'pitch_semitones': 0}, {'volume': 'shout'}) == ''
    up = tts.post_filter('openai', {'pitch_semitones': 2, 'pitch_formants': 'shift'}, line)
    assert up == A.pitch_filter(2, 'shift')
    shout = tts.post_filter('piper', {'pitch_semitones': -2}, {'volume': 'shout'})
    assert shout.startswith(A.pitch_filter(-1.0)) and 'highshelf' in shout


LINE = {'id': 'l1', 'speaker': 'hero', 'text': 'Wait... where is it?', 'emotion': 'neutral', 'pace': 'normal',
        'volume': 'normal'}


def test_delivery_applies_character_overrides():
    base = piper.delivery(LINE)
    assert base == (1.0, 0.667, 0.8)
    slow = piper.delivery(LINE, {'piper_length_scale': 1.1, 'piper_noise_scale': 0.5, 'piper_noise_w': 0.6})
    assert slow[0] > 1.1 and slow[1:] == (0.5, 0.6), 'slower, steadier'
    lively = piper.delivery(dict(LINE, emotion='excited'), {'piper_noise_scale': 0.72, 'piper_noise_w': 0.88})
    assert lively[1:] == (round(0.72 + 0.083, 3), round(0.88 + 0.1, 3))
    v = {'narration_length_scale': 0.95}
    assert piper.delivery(dict(LINE, kind='narration'), v)[0] < piper.delivery(LINE, v)[0] == 1.0
    junk = {'piper_length_scale': 'fast', 'piper_noise_scale': float('nan'), 'pitch_formants': 'squeaky'}
    assert piper.delivery(LINE, junk) == base and piper.voice_settings(junk) == {'pitch_formants': 'preserve'}
    assert piper.voice_settings({'pitch_semitones': 40})['pitch_semitones'] == 6.0, 'clamped'


def test_speed_calibration_matches_measurement():
    assert piper.calibrated_length(1.0) == pytest.approx(1.0)
    # Measured through the voice chain: length_scale 0.628 delivers 1.30x (1/1.3 = 0.769 delivered only ~1.12x).
    assert piper.calibrated_length(1.3) == pytest.approx(0.628, abs=0.002)
    assert piper.calibrated_length(1.3) < 1 / 1.3 - 0.1
    ls = [piper.calibrated_length(s) for s in (0.8, 1.0, 1.2, 1.4, 1.6, 3.0)]
    assert ls == sorted(ls, reverse=True) and ls[-1] == piper.LENGTH_RANGE[0]


def test_squeeze_gaps_shortens_long_internal_silences_only():
    tone = voiced(0.3)
    x = np.concatenate([np.zeros(int(0.2 * SR)), tone, np.zeros(int(0.6 * SR)), tone, np.zeros(int(0.2 * SR))])
    y, cut = A.squeeze_gaps(x, 0.15)
    assert cut == pytest.approx(0.45, abs=0.02)
    gaps = [b[0] - a[1] for a, b in zip(A.speech_regions(y), A.speech_regions(y)[1:])]
    assert len(gaps) == 1 and gaps[0] == pytest.approx(0.15, abs=0.03)
    assert np.allclose(y[:int(0.2 * SR)], 0), 'leading silence is left to trim_silence'
    same, none = A.squeeze_gaps(x, 1.0)
    assert none == 0.0 and len(same) == len(x)


@pytest.fixture
def fake_piper(tmp_path, monkeypatch):
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
            x = voiced(1.2, 140.0, 22050)
            gap = np.zeros(int(0.5 * 22050))
            x = np.concatenate([x, gap, x])  # two "sentences" with a long pause between them
            with wave.open(args[args.index('-f') + 1], 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes((x * 32767).astype(np.int16).tobytes())
            return b'', b''
        return real_run(args, timeout=timeout, **kw)
    monkeypatch.setattr(piper.media, 'run', run)
    return calls


def test_synthesized_line_is_pitch_shifted_squeezed_and_passes_overrides(db, fake_piper, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    plain = tts.synthesize_line(LINE, {'id': 'ch_x', 'name': 'X', 'voice': {'piper_speaker': 5}}, p,
                                str(tmp_path / 'a'), 'v1')
    dot = {'piper_speaker': 408, 'pitch_semitones': 4, 'pitch_formants': 'shift', 'piper_noise_scale': 0.75,
           'piper_noise_w': 0.9, 'piper_length_scale': 0.97}
    up = tts.synthesize_line(LINE, {'id': 'ch_dot', 'name': 'Dot', 'voice': dot}, p, str(tmp_path / 'b'), 'v1')
    shift = 12 * np.log2(f0_of(A.decode(up['file'])) / f0_of(A.decode(plain['file'])))
    assert shift == pytest.approx(4.0, abs=0.3)
    args = fake_piper[-1]
    assert args[args.index('-s') + 1] == '408'
    assert float(args[args.index('--noise-scale') + 1]) == 0.75 and float(args[args.index('--noise-w-scale') + 1]) == 0.9
    assert float(args[args.index('--length-scale') + 1]) == piper.delivery(LINE, dot)[0]
    # The 0.5 s pause between the two sentences is squeezed to the storytime maximum.
    assert plain['post']['gaps_removed_s'] == pytest.approx(0.5 - tts.PIPER_MAX_GAP_S, abs=0.03)
    assert plain['internal_silence_s'] <= tts.PIPER_MAX_GAP_S + 0.03


def test_loud_and_shouted_lines_are_louder(db, fake_piper, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    who = {'id': 'ch_x', 'name': 'X', 'voice': {'piper_speaker': 5}}
    lufs = {}
    for vol in ('normal', 'loud', 'shout'):
        r = tts.synthesize_line(dict(LINE, volume=vol), who, p, str(tmp_path / vol), 'v1')
        lufs[vol] = A.loudness(r['file'])['integrated_lufs']
    assert lufs['loud'] > lufs['normal'] + 0.8 and lufs['shout'] > lufs['loud'] + 0.8


def test_narration_has_its_own_cache_key_and_dialogue_keeps_the_old_one(db, fake_piper, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    who = {'id': 'ch_bloxy', 'name': 'Bloxy', 'voice': {}}
    old = tts.synthesize_line(LINE, who, p, str(tmp_path / 'v'), 'v1')
    same = tts.synthesize_line(dict(LINE, kind='dialogue'), who, p, str(tmp_path / 'v'), 'v1')
    narr = tts.synthesize_line(dict(LINE, kind='narration'), who, p, str(tmp_path / 'v'), 'v1')
    assert same['spec_hash'] == old['spec_hash'] != narr['spec_hash']
