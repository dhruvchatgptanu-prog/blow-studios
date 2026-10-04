"""Livelier sound: the playful bed, act-turn music sections, the pre-punchline dropout, cut whooshes and the
new synthesised effects (no samples)."""
import copy
import shutil

import numpy as np
import pytest

from blox import demo, prefs
from blox.assembly import mix
from blox.manifest import compile as C, schema as S
from blox.manifest.validate import validate
from blox.voice import audio as A, synth

NEW_CUES = ('stinger', 'bonk', 'stomp', 'door_chime', 'crowd_ooh', 'laugh_burst')


def build(plan=None, **kw):
    return C.compile_plan(plan or demo.plan(), fps=30, width=1080, height=1920, **kw)


@pytest.mark.parametrize('cue', S.SFX_CUES)
def test_every_cue_is_synthesised_deterministically(cue):
    x = synth.sfx(cue, seed=7)
    assert len(x) > synth.SR * 0.04 and np.isfinite(x).all() and np.max(np.abs(x)) > 0.05
    assert np.array_equal(x, synth.sfx(cue, seed=7))


def test_new_cues_are_in_the_vocabulary_and_shaped_like_their_names():
    assert set(NEW_CUES) <= set(S.SFX_CUES)
    dur = {c: len(synth.sfx(c)) / synth.SR for c in NEW_CUES}
    assert dur['bonk'] < 0.4 and dur['stomp'] < 0.6 and dur['crowd_ooh'] > 1.0 and dur['door_chime'] > 1.2
    # The chime has two notes: energy returns after the first ding decays.
    env = A.rms_envelope(synth.sfx('door_chime'), synth.SR, 0.01)
    assert env[45:55].max() > 1.5 * env[38:42].min()
    # The laugh is a run of syllables (several separate bursts).
    env = A.rms_envelope(synth.sfx('laugh_burst'), synth.SR, 0.01)
    peaks = [i for i in range(1, len(env) - 1) if env[i] >= env[i - 1] and env[i] > env[i + 1] and env[i] > 0.3 * env.max()]
    assert 4 <= len(peaks) <= 9
    # A heavy stomp lives in the bass; the knock, the chime and the laugh do not.
    def low_share(x):
        f = np.fft.rfftfreq(len(x), 1 / synth.SR)
        s = np.abs(np.fft.rfft(x)) ** 2
        return float(s[f < 250].sum() / s.sum())
    assert low_share(synth.sfx('stomp')) > 0.9
    assert max(low_share(synth.sfx(c)) for c in ('bonk', 'door_chime', 'laugh_burst')) < 0.05


def test_playful_bed_is_fast_and_sections_lift_the_key():
    tempo, root = synth.MOODS['playful'][:2]
    assert 130 <= tempo <= 150
    a = synth.music('playful', 6.0)
    b = synth.music('playful', 6.0, section=1)
    assert np.max(np.abs(a)) == pytest.approx(0.5, abs=0.01) and not np.allclose(a, b)
    assert synth.SECTION_SHIFT[1] != 0
    # The other moods keep their tempo.
    assert synth.MOODS['tension'][0] == 100 and synth.MOODS['sad'][0] == 72


def test_sections_stay_on_the_beat_grid():
    whole = synth.music('playful', 8.0)
    part = synth.music('playful', 4.0, t0=3.0)  # the same groove, joined in at 3 s
    seg = whole[int(3.5 * synth.SR):int(6.5 * synth.SR)]
    cmp = part[int(0.5 * synth.SR):int(3.5 * synth.SR)]
    assert float(np.corrcoef(seg, cmp)[0, 1]) > 0.9
    off = synth.music('playful', 4.0, t0=3.1)[int(0.5 * synth.SR):int(3.5 * synth.SR)]
    assert float(np.corrcoef(seg, off)[0, 1]) < 0.8  # a misaligned grid would not match


def test_music_track_accepts_sections_and_old_pairs():
    t = synth.music_track([(0.0, 'playful'), (2.0, 'playful', 1), (4.0, 'tension', 0)], 6.0)
    assert len(t) == 6 * synth.SR and np.isfinite(t).all()


def test_dropout_silences_the_music_without_clicks():
    t = synth.music_track([(0.0, 'playful')], 4.0)
    d = synth.apply_dropouts(t, [(2.0, 2.4)])
    sr = synth.SR
    assert np.max(np.abs(d[int(2.0 * sr):int(2.4 * sr)])) == 0.0
    assert np.array_equal(d[:int(1.9 * sr)], t[:int(1.9 * sr)]) and np.array_equal(d[int(2.5 * sr):], t[int(2.5 * sr):])
    edge = d[int(1.97 * sr):int(2.0 * sr)]
    assert np.max(np.abs(np.diff(edge))) < 0.2  # faded, not chopped


def test_one_cue_plans_get_a_new_section_at_each_act_turn():
    p = demo.plan()
    p['music'] = [{'t': 0, 'cue': 'playful'}]
    m = build(p)
    secs = [x for x in m['music'] if x.get('auto') == 'act_turn']
    starts = {b['start_s']: b['purpose'] for b in p['beats']}
    assert [x['section'] for x in secs] == [1, 2, 3] and all(x['cue'] == 'playful' for x in secs)
    assert [starts[x['frame'] // 30] for x in secs] == ['escalation', 'climax', 'payoff']
    assert len(build()['music']) == len(demo.plan()['music'])  # authored cue changes are kept as written
    p['music'] = [{'t': 0, 'cue': 'none'}]
    assert len(build(p)['music']) == 1
    assert validate(build(p), copy.deepcopy(prefs.DEFAULTS))['ok']


@pytest.mark.parametrize('pace', [1.0, 1.5])
def test_dropout_comes_just_before_the_punchline(pace):
    m = build(pace=pace)
    d = m['music_dropouts']
    assert len(d) == 1 and d[0]['source'] == 'payoff'
    punch = next(ln for ln in m['lines'] if ln['start_frame'] >= m['payoff']['start_frame'])
    assert d[0]['end_frame'] == punch['start_frame']
    assert d[0]['end_frame'] - d[0]['start_frame'] == C.fr(S.MUSIC_DROPOUT_S / pace, 30)


def test_dropout_can_be_placed_or_turned_off():
    p = demo.plan()
    p['music_dropout'] = {'t': 12.0, 'duration_s': 0.5}
    assert build(p)['music_dropouts'] == [{'start_frame': 345, 'end_frame': 360, 'source': 'plan'}]
    p['music_dropout'] = False
    assert build(p)['music_dropouts'] == []
    p = demo.plan()
    p['style'] = {'music_dropout': False}
    assert build(p)['music_dropouts'] == []


def test_whooshes_on_cuts():
    p = demo.plan()
    p['style'] = {'whoosh_on_cuts': True}
    m = build(p)
    auto = [x for x in m['sfx'] if x.get('auto') == 'cut']
    cuts = [s['start_frame'] for s in m['shots'] if s['start_frame'] > 0]
    authored = [x['frame'] for x in m['sfx'] if not x.get('auto')]
    assert auto and all(x['frame'] in cuts and x['cue'] == 'whoosh' and x['align'] == 'peak' for x in auto)
    assert all(min(abs(a - x['frame']) for a in authored) > 9 for x in auto)  # not on top of an authored sound
    assert all(b['frame'] - a['frame'] >= 18 for a, b in zip(auto, auto[1:]))
    assert not any(x.get('auto') for x in build()['sfx'])
    assert validate(m, copy.deepcopy(prefs.DEFAULTS))['ok']


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg not installed')
def test_mix_places_whooshes_on_the_cut_drops_the_music_and_keeps_loudness(tmp_path):
    p = demo.plan()
    p['style'] = {'whoosh_on_cuts': True}
    p['music'] = [{'t': 0, 'cue': 'playful'}]
    m = build(p)
    P = copy.deepcopy(prefs.DEFAULTS)
    line_audio = {}
    for ln in m['lines']:
        dur = (ln['est_end_frame'] - ln['start_frame']) / 30 * 0.9
        t = np.arange(int(dur * A.SR)) / A.SR
        path = str(tmp_path / f'{ln["id"]}.wav')
        A.write_wav(path, 0.3 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 4 * t))))
        line_audio[ln['id']] = {'file': path, 'duration_s': dur}
    out = mix(m, line_audio, P, str(tmp_path / 'mix'))
    assert out['auto_sfx'] == sum(1 for x in m['sfx'] if x.get('auto'))
    sfx = A.decode(out['paths']['sfx'])
    w = next(x for x in m['sfx'] if x.get('auto'))
    region = np.abs(sfx[int((w['frame'] / 30 - 0.4) * A.SR):int((w['frame'] / 30 + 0.4) * A.SR)])
    assert abs(int(np.argmax(region)) / A.SR - 0.4) < 0.03  # the whoosh peaks on the cut
    music = A.decode(out['paths']['music'])
    d = m['music_dropouts'][0]
    gap = music[int((d['start_frame'] / 30 + 0.03) * A.SR):int((d['end_frame'] / 30 - 0.01) * A.SR)]
    assert np.max(np.abs(gap)) < 1e-6 and out['music_dropouts_s']
    lufs = A.loudness(out['paths']['master'])
    assert abs(lufs['integrated_lufs'] - P['qa']['loudness_target_lufs']) < 1.5
    assert lufs['true_peak_dbtp'] <= P['qa']['true_peak_max_dbtp'] + 0.2
    # Ducking still pulls the bed down under the dialogue.
    dialog = A.decode(out['paths']['dialog'])
    env, menv = A.rms_envelope(dialog), A.rms_envelope(music)
    n = min(len(env), len(menv))
    speech, quiet = env[:n] > 0.02, (env[:n] < 0.005) & (menv[:n] > 0)
    assert np.mean(menv[:n][speech]) < 0.6 * np.mean(menv[:n][quiet])
