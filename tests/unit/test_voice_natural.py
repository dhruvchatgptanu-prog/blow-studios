"""Natural voices: speech is never time-stretched (owner's rule, 2026-10-19; pref time_stretch_speech, off).

A slightly long line is voiced again at the engine's own faster speed, bounded by the line-fitting cap; when the
engine has no native speed (or it still does not fit) the next line moves or the line is rewritten. A pitch shift
uses only the rubberband filter, or is skipped with the reason on the line result."""
import copy
import math
import wave

import numpy as np
import pytest

from blox import demo, media, prefs, production
from blox.manifest import compile as C
from blox.voice import audio as A, piper, tts

from ..conftest import have

pytestmark = pytest.mark.skipif(not have('ffmpeg'), reason='needs FFmpeg')

BASE_S = 2.0  # a fake Piper line at length scale 1


def tone(path, seconds, sr=22050):
    """A continuous voiced sound (no silences, so trimming and gap squeezing leave its length alone)."""
    t = np.arange(int(seconds * sr)) / sr
    x = 0.3 * np.sin(2 * np.pi * 160 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


@pytest.fixture
def ff(tmp_path, monkeypatch):
    """Records every command line run through blox.media (FFmpeg and Piper). Piper is faked: its line lasts
    LENGTH_FIXED + (1 - LENGTH_FIXED) * length_scale times BASE_S, the speed model Piper was calibrated with."""
    d = tmp_path / 'voices'
    monkeypatch.setenv('BLOX_VOICES_DIR', str(d))
    mdir = d / piper.DEFAULT_MODEL
    mdir.mkdir(parents=True)
    (mdir / f'{piper.DEFAULT_MODEL}.onnx').write_bytes(b'onnx')
    (mdir / f'{piper.DEFAULT_MODEL}.onnx.json').write_text('{}')
    calls = []
    real = media.run

    def run(args, timeout=None, **kw):
        calls.append([str(a) for a in args])
        if list(args[1:3]) == ['-m', 'piper']:
            ls = float(args[args.index('--length-scale') + 1])
            tone(args[args.index('-f') + 1], BASE_S * (piper.LENGTH_FIXED + (1 - piper.LENGTH_FIXED) * ls))
            return b'', b''
        return real(args, timeout=timeout, **kw)
    monkeypatch.setattr(media, 'run', run)
    return calls


def stretched(calls):
    """Command lines that change the speed of audio (FFmpeg atempo, or asetrate resampling)."""
    return [c for c in calls if any('atempo' in a or 'asetrate' in a for a in c)]


def piper_lengths(calls):
    return [float(c[c.index('--length-scale') + 1]) for c in calls if c[1:3] == ['-m', 'piper']]


def two_lines(pace=1.5, rate=1.3):
    m = C.compile_plan(demo.plan(), fps=30, width=540, height=960, pace=pace, speech_rate=rate)
    m['lines'] = m['lines'][:2]
    m['lines'][0].update(pace='normal', emotion='neutral', volume='normal')
    return m


def make_long(m, first, duration, over):
    """Move the second line so the first one's window is ``duration / over``; returns the window."""
    pause = first.get('pause_after_ms', 0) / 1000.0
    m['lines'][1]['start_frame'] = first['start_frame'] + int(math.ceil((duration / over + pause) * m['fps']))
    return (m['lines'][1]['start_frame'] - first['start_frame']) / m['fps'] - pause


def fake_result(path, secs):
    t = np.arange(int(secs * A.SR)) / A.SR
    A.write_wav(path, 0.3 * np.sin(2 * np.pi * 200 * t))
    return {'file': str(path), 'duration_s': round(len(t) / A.SR, 3), 'spec_hash': 'h0', 'alignment_kind': 'estimated',
            'words': [{'word': 'hey', 'start': 0.0, 'end': secs}]}


# ----------------------------------------------------------------------------- the preference
def test_time_stretching_is_off_by_default_and_validated():
    assert prefs.DEFAULTS['production']['time_stretch_speech'] is False
    p = prefs.validate(copy.deepcopy(prefs.DEFAULTS))
    assert p['production']['time_stretch_speech'] is False
    bad = copy.deepcopy(prefs.DEFAULTS)
    bad['production']['time_stretch_speech'] = 'no'
    with pytest.raises(ValueError):
        prefs.validate(bad)


def test_fit_line_never_touches_the_audio_with_stretching_off(ff, tmp_path):
    res = fake_result(tmp_path / 'l.wav', 2.2)
    ff.clear()
    assert tts.fit_line(res, 2.0, 1.15, str(tmp_path), stretch=False) == (res, 'speed_up')
    assert tts.fit_line(res, 1.5, 1.15, str(tmp_path), stretch=False) == (res, 'too_long'), 'over the cap'
    assert tts.fit_line(res, 3.0, 1.15, str(tmp_path), stretch=False) == (res, 'fits')
    assert ff == [], 'nothing was run'
    fitted, action = tts.fit_line(res, 2.0, 1.15, str(tmp_path), stretch=True)
    assert action == 'tempo' and stretched(ff), 'the old path, only with the preference on'


# ----------------------------------------------------------------------------- native re-voice
def test_slightly_long_line_is_voiced_again_natively_and_never_stretched(ff, tmp_path):
    p = copy.deepcopy(prefs.DEFAULTS)
    m = two_lines()
    first = m['lines'][0]
    cast = production.cast_characters(m, {})
    wd = str(tmp_path / 'voice')
    results = production.voice_lines(m, cast, p, wd, 'vid1', only=[first['id']])
    orig = results[first['id']]
    window = make_long(m, first, orig['duration_s'], 1.08)
    ratio = orig['duration_s'] / window
    cap = C.tempo_cap(m, p['production']['max_tempo'])
    assert 1.0 < ratio < cap
    m2, fitted, report = production.retime(m, dict(results), p, max_shift_s=0.0,
                                           revoice=production.revoicer(m, cast, p, wd, 'vid1'))
    mine = [r for r in report if r['line'] == first['id']]
    assert [r['action'] for r in mine] == ['native_speed'], report
    entry = mine[0]
    assert entry['fits'] and ratio <= entry['speed'] <= cap + 1e-9, entry
    new = fitted[first['id']]
    assert new['native_speed'] == entry['speed'] and new['duration_s'] <= window + 1e-6
    assert new['duration_s'] < orig['duration_s'] and 'tempo' not in new
    assert new['spec_hash'] != orig['spec_hash'], 'the faster take is cached apart from the first one'
    lengths = piper_lengths(ff)
    assert len(lengths) == 2 and lengths[1] < lengths[0], 'Piper itself spoke faster (smaller length scale)'
    assert next(x for x in m2['lines'] if x['id'] == first['id'])['fit_speed'] == entry['speed']
    assert not stretched(ff), 'no atempo or asetrate anywhere in the chain'


def test_the_voice_task_stores_the_natively_faster_take(db, ff, tmp_path):
    from blox import pipeline, repo, videos
    p = prefs.get(db)
    m = two_lines()
    first = m['lines'][0]
    cast = pipeline._cast(m, db)
    probe = tts.synthesize_line(first, cast.get(first['speaker']), p, str(tmp_path / 'probe'), 'probe', manifest=m)
    window = make_long(m, first, probe['duration_s'], 1.08)
    vid = videos.create('Natural voice test', 'autopilot', d=db)
    mid = repo.save_manifest(vid, m, 'test', '', {}, db)

    class Ctx:
        payload = {'manifest_id': mid}
        video_id = vid

        def beat(self, force=False):
            pass
    out = pipeline.voice(Ctx())
    mine = [r for r in out['retime'] if r['line'] == first['id']]
    assert [r['action'] for r in mine] == ['native_speed'] and mine[0]['fits'], out['retime']
    row = next(r for r in repo.lines(mid, db) if r['line_key'] == first['id'])
    assert row['alignment']['native_speed'] == mine[0]['speed'] and row['duration_s'] <= window + 1e-6
    assert row['spec_hash'] != probe['spec_hash'] and row['file'].endswith(f'_{row["spec_hash"]}.wav')
    body = repo.manifest(mid, db)['body']
    assert next(x for x in body['lines'] if x['id'] == first['id'])['fit_speed'] == mine[0]['speed']
    assert not stretched(ff)


def test_native_speed_is_bounded_by_the_fitting_cap(tmp_path):
    m = two_lines()
    first = m['lines'][0]
    p = copy.deepcopy(prefs.DEFAULTS)
    cap = C.tempo_cap(m, p['production']['max_tempo'])
    asked = []

    def revoice(ln, factor):
        asked.append(factor)
        return None
    # Just under the cap: asked for the overrun plus the margin, clipped to the cap.
    window = make_long(m, first, 2.0, cap * 0.995)
    production.retime(m, {first['id']: fake_result(tmp_path / 'a.wav', 2.0)}, p, max_shift_s=0.0, revoice=revoice)
    assert asked and 1.0 < asked[0] <= cap + 1e-9
    # Over the cap: no re-voice is tried at all; the line goes to a rewrite.
    asked.clear()
    make_long(m, first, 2.0, cap * 1.1)
    _, _, report = production.retime(m, {first['id']: fake_result(tmp_path / 'b.wav', 2.0)}, p, max_shift_s=0.0,
                                     revoice=revoice)
    assert asked == [] and [r['action'] for r in report if r['line'] == first['id']] == ['rewrite_needed']
    assert window > 0


def test_without_a_native_speed_it_falls_back_to_shift_or_rewrite(tmp_path, monkeypatch):
    p = copy.deepcopy(prefs.DEFAULTS)
    m = two_lines()
    first = m['lines'][0]
    make_long(m, first, 2.0, 1.05)
    # The FFmpeg test voice has no speed setting: revoicer declines without synthesising anything.
    monkeypatch.setattr(production.tts, 'synthesize_line', lambda *a, **k: pytest.fail('must not re-voice'))
    who = {c: {'id': 'ch_t', 'name': 'T', 'voice': {}} for c in [ln['speaker'] for ln in m['lines']]}
    p_local = copy.deepcopy(p)
    p_local['production']['tts_provider'] = 'local_test'
    rv = production.revoicer(m, who, p_local, str(tmp_path), 'vid1')
    assert rv(first, 1.08) is None
    _, out, report = production.retime(m, {first['id']: fake_result(tmp_path / 'a.wav', 2.0)}, p_local,
                                       max_shift_s=0.0, revoice=rv)
    assert [r['action'] for r in report if r['line'] == first['id']] == ['rewrite_needed']
    assert 'tempo' not in out[first['id']] and out[first['id']]['file'].endswith('a.wav'), 'audio untouched'
    # With room to move the next line, that is used instead (bounded as before).
    _, _, report = production.retime(m, {first['id']: fake_result(tmp_path / 'b.wav', 2.0)}, p_local,
                                     max_shift_s=0.6, revoice=rv)
    assert [r['action'] for r in report if r['line'] == first['id']] == ['shift_next']
    # No re-voice step given at all: the same fallback, never a tempo change.
    _, _, report = production.retime(m, {first['id']: fake_result(tmp_path / 'c.wav', 2.0)}, p, max_shift_s=0.0)
    assert [r['action'] for r in report if r['line'] == first['id']] == ['rewrite_needed']


def test_a_faster_take_that_still_does_not_fit_falls_back_too(tmp_path):
    p = copy.deepcopy(prefs.DEFAULTS)
    m = two_lines()
    first = m['lines'][0]
    window = make_long(m, first, 2.0, 1.05)

    def revoice(ln, factor):
        return dict(fake_result(tmp_path / 'fast.wav', window * 1.02), native_speed=factor)
    _, out, report = production.retime(m, {first['id']: fake_result(tmp_path / 'a.wav', 2.0)}, p, max_shift_s=0.0,
                                       revoice=revoice)
    mine = [r for r in report if r['line'] == first['id']]
    assert [r['action'] for r in mine] == ['native_speed', 'rewrite_needed'] and mine[0]['fits'] is False
    assert out[first['id']]['file'].endswith('fast.wav'), 'the shorter take is kept for the fallback'


def test_engines_report_their_native_speed_and_headroom():
    pr = copy.deepcopy(prefs.DEFAULTS['production'])
    line = {'id': 'l1', 'text': 'Hi there', 'emotion': 'neutral', 'pace': 'normal', 'volume': 'normal',
            'speech_rate': 1.3}

    def who(**voice):
        return {'id': 'ch_x', 'name': 'X', 'voice': voice}
    assert tts.can_speed_up(line, who(), pr, 1.1), 'Piper: length scale'
    assert tts.native_speed(dict(line, fit_speed=1.1), who(), pr) < tts.native_speed(line, who(), pr)
    assert not tts.can_speed_up(dict(line, speech_rate=1.6, pace='fast'), who(), pr, 1.1), 'Piper at its limit'
    assert tts.can_speed_up(line, who(engine='kokoro'), pr, 1.1)
    assert not tts.can_speed_up(line, who(engine='kokoro', kokoro_speed=2.0), pr, 1.1), 'Kokoro at 2.0'
    assert tts.can_speed_up(line, who(engine='speechify'), pr, 1.1)
    assert not tts.can_speed_up(line, who(engine='openai'), pr, 1.1), 'gpt-4o-mini-tts has no speed parameter'
    assert tts.can_speed_up(line, who(engine='openai'), dict(pr, tts_model='tts-1'), 1.1)
    assert not tts.can_speed_up(line, who(), dict(pr, tts_provider='local_test'), 1.1)
    assert not tts.can_speed_up(line, who(engine='elevenlabs'), pr, 1.1), 'already clamped at 1.2'
    assert tts.can_speed_up(dict(line, speech_rate=1.0), who(engine='elevenlabs'), pr, 1.1)
    assert not tts.can_speed_up(line, who(), pr, 1.0)


def test_the_fitting_speed_is_part_of_the_cache_key_and_bounded():
    pr = copy.deepcopy(prefs.DEFAULTS['production'])
    line = {'id': 'l1', 'text': 'Hi there', 'emotion': 'neutral', 'pace': 'normal', 'volume': 'normal',
            'speech_rate': 1.3}
    who = {'id': 'ch_x', 'name': 'X', 'voice': {}}
    base = tts.line_spec(line, who, pr)['hash']
    assert tts.line_spec(dict(line, fit_speed=1.0), who, pr)['hash'] == base, 'lines never re-voiced keep their key'
    assert tts.line_spec(dict(line, fit_speed=1.1), who, pr)['hash'] != base
    for junk in (0.5, 9, 'fast', float('nan'), None):
        assert tts.fit_speed({'fit_speed': junk}) == 1.0
    assert tts.engine_line(dict(line, fit_speed=1.1))['speech_rate'] == pytest.approx(1.43)


# ----------------------------------------------------------------------------- pitch shift
LINE = {'id': 'l1', 'speaker': 'hero', 'text': 'Wait, where is it?', 'emotion': 'neutral', 'pace': 'normal',
        'volume': 'normal'}


def test_pitch_shift_without_rubberband_is_skipped_with_a_reason(ff, tmp_path, monkeypatch):
    monkeypatch.setattr(A, 'has_filter', lambda name: False)
    p = copy.deepcopy(prefs.DEFAULTS)
    who = {'id': 'ch_x', 'name': 'X', 'voice': {'piper_speaker': 5, 'pitch_semitones': 3}}
    res = tts.synthesize_line(LINE, who, p, str(tmp_path / 'a'), 'vid1')
    assert res['post']['pitch_skipped'] == A.PITCH_SKIPPED and res['post']['filter'] == ''
    loud = tts.synthesize_line(dict(LINE, volume='shout'), who, p, str(tmp_path / 'b'), 'vid1')
    assert loud['post']['filter'].startswith('highshelf=') and loud['post']['pitch_skipped']
    plain = tts.synthesize_line(LINE, {'id': 'ch_x', 'name': 'X', 'voice': {'piper_speaker': 5}}, p,
                                str(tmp_path / 'c'), 'vid1')
    assert plain['post']['pitch_skipped'] is None, 'no shift wanted, nothing skipped'
    assert not stretched(ff)
    # With the preference on, the old resampling fallback is still there.
    on = copy.deepcopy(p)
    on['production']['time_stretch_speech'] = True
    old = tts.synthesize_line(LINE, who, on, str(tmp_path / 'd'), 'vid1')
    assert 'asetrate' in old['post']['filter'] and old['post']['pitch_skipped'] is None


def test_pitch_shift_uses_rubberband_for_pitch_only(monkeypatch):
    monkeypatch.setattr(A, 'has_filter', lambda name: name == 'rubberband')
    af = tts.post_filter('piper', {'pitch_semitones': 2}, LINE, resample_fallback=False)
    assert af.startswith('rubberband=pitch=') and 'tempo' not in af
    assert tts.pitch_skipped('piper', {'pitch_semitones': 2}, LINE, resample_fallback=False) is None
    assert A.pitch_filter(2, resample_fallback=False) == af
    monkeypatch.setattr(A, 'has_filter', lambda name: False)
    assert A.pitch_filter(2, resample_fallback=False) == ''
    assert 'atempo' in A.pitch_filter(2), 'the old fallback, kept only for the preference'
