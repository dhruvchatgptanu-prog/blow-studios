"""Idle life, speech gestures and listening: automatic, deterministic, and authored motion always wins."""
import json
import os

import numpy as np
import pytest

from blox import demo
from blox.animation import life as LF, rig as R, solver as SV
from blox.manifest import compile as C

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BATCH = os.path.join(ROOT, 'stories', 'batch-2026-10-03-claude.json')
FPS = 30


def bibles(m):
    return {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id']) for c in m['cast']}


def world(fr, sc):
    return SV.fk({'root': fr['root'], 'yaw': fr['yaw'], 'rot': fr['rot'], 'loc': fr['loc']}, sc)


def conversation(lines=True, actions=(), bloxy_keys=()):
    plan = {'title': 'Conversation test', 'duration_s': 12.0,
            'setting': {'preset': 'studio', 'props': []},
            'cast': [{'id': 'bloxy', 'character_id': 'ch_bloxy'}, {'id': 'pip', 'character_id': 'ch_pip'}],
            'shots': [{'id': 's1', 'start_s': 0.0, 'end_s': 12.0,
                       'camera': {'subject': 'two_shot', 'framing_start': 'full', 'side': 'front'}}],
            'performance': {
                'bloxy': [{'t': 0.0, 'position': [-0.55, 0.0], 'facing': 40, 'expression': 'excited',
                           'eye_target': {'kind': 'character', 'id': 'pip'}}] + list(bloxy_keys),
                'pip': [{'t': 0.0, 'position': [0.55, 0.1], 'facing': -40, 'expression': 'smug',
                         'eye_target': {'kind': 'character', 'id': 'bloxy'}}]},
            'actions': list(actions), 'lines': []}
    if lines:
        plan['lines'] = [
            {'id': 'l1', 'speaker': 'bloxy', 'text': 'Okay, here is the plan. We go left, then we JUMP!', 't': 0.5,
             'emotion': 'excited', 'pace': 'normal', 'volume': 'loud'},
            {'id': 'l2', 'speaker': 'pip', 'text': 'That is the worst plan I have ever heard.', 't': 4.6,
             'emotion': 'confused', 'pace': 'normal', 'volume': 'normal'}]
    return plan


def envelope_for(m, speaker):
    """A voice-like amplitude envelope: one bump per syllable-ish word chunk of the speaker's lines."""
    env = np.zeros(m['duration_frames'])
    for ln in m['lines']:
        if ln['speaker'] != speaker:
            continue
        for i, f in enumerate(range(ln['start_frame'] + 2, ln['est_end_frame'] - 2, 6)):
            amp = 0.5 + 0.5 * ((i * 7) % 5) / 4.0
            for g in range(f, min(len(env), f + 6)):
                env[g] = max(env[g], amp * np.sin(np.pi * (g - f) / 6.0))
    return env


@pytest.fixture(scope='module')
def talk():
    m = C.compile_plan(conversation(), fps=FPS, width=540, height=960)
    env = {cid: envelope_for(m, cid) for cid in ('bloxy', 'pip')}
    return m, SV.solve(m, bibles(m), envelopes=env), env


def line(m, lid):
    return next(x for x in m['lines'] if x['id'] == lid)


def palm(fr, side, sc):
    k = world(fr, sc)
    return SV.local_point(k, 'wrist_' + side, (0, 0, -R.HAND_REACH), sc), k


def test_idle_breathes_and_shifts_weight_over_planted_feet():
    m = C.compile_plan(conversation(lines=False), fps=FPS, width=540, height=960)
    res = SV.solve(m, bibles(m))
    for cid, frames in res['characters'].items():
        sc = res['scales'][cid]
        sway = [fr['loc']['pelvis'][0] for fr in frames]
        chest = [fr['rot']['chest'][0] for fr in frames]
        shoulders = [fr['loc']['shoulder_l'][2] for fr in frames]
        head = [fr['rot']['head'][2] for fr in frames]
        assert np.ptp(sway) > 0.03, cid                 # weight moves from foot to foot
        assert np.ptp(chest) > 1.5 and np.ptp(shoulders) > 0.006, cid   # breathing
        assert np.ptp(head) > 1.0, cid                  # head drift
        ankles = [world(fr, sc)['ankle_' + s][1] for fr in frames for s in 'l']
        assert all(fr['feet'][s]['contact_expected'] for fr in frames for s in 'lr')
        assert max(np.linalg.norm(a - ankles[0]) for a in ankles) < 1e-3, 'feet stay exactly planted'
        blinks = [f for f, fr in enumerate(frames) if fr['face']['blink'] > 0.9]
        assert blinks, 'still blinks'
    # Seeded per character: the two do not move in lockstep.
    a = [fr['rot']['head'][2] for fr in res['characters']['bloxy']]
    b = [fr['rot']['head'][2] for fr in res['characters']['pip']]
    assert abs(np.corrcoef(a, b)[0, 1]) < 0.8


def test_speaker_gestures_on_stressed_words_then_rests(talk):
    m, res, env = talk
    ln = line(m, 'l1')
    frames = res['characters']['bloxy']
    sc = res['scales']['bloxy']
    a, b = ln['start_frame'], ln['est_end_frame']

    def reach(f):
        best = 0.0
        for s in 'lr':
            p, k = palm(frames[f], s, sc)
            best = max(best, float(-SV.to_local(k, 'chest', p, sc)[1]))   # forward of the chest
        return best
    during = [reach(f) for f in range(a + 10, b - 5)]
    after = [reach(f) for f in range(b + 45, b + 60)]
    assert np.median(during) > 0.3 and max(after) < 0.2
    # The hand keeps moving with the beats (not a frozen pose) and the head nods on them.
    hz = [palm(frames[f], s, sc)[0][2] for f in range(a + 10, b - 5) for s in 'l']
    assert np.ptp(hz) > 0.03
    pitch = np.array([frames[f]['rot']['head'][0] for f in range(a, b)])
    nods = sum(1 for i in range(1, len(pitch) - 1) if pitch[i] > pitch[i - 1] and pitch[i] >= pitch[i + 1])
    assert nods >= 3
    # Brows rise on emphasis; the excited preset alone is 0.5 inner.
    assert max(frames[f]['face']['brow_l'][0] for f in range(a, b)) > 0.65


def test_hands_never_cover_the_mouth_while_talking(talk):
    m, res, env = talk
    for ln in m['lines']:
        frames = res['characters'][ln['speaker']]
        sc = res['scales'][ln['speaker']]
        for f in range(ln['start_frame'], ln['est_end_frame']):
            k = world(frames[f], sc)
            mouth = SV.local_point(k, 'head', (0, R.FACE_FRONT_Y, R.FACE['mouth_z']), sc)
            for s in 'lr':
                p = SV.local_point(k, 'wrist_' + s, (0, 0, -R.HAND_REACH), sc)
                assert mouth[2] - p[2] > 0.3, (ln['id'], f, s)


def test_listener_nods_holds_eye_contact_and_tilts(talk):
    m, res, env = talk
    quiet_m = C.compile_plan(conversation(lines=False), fps=FPS, width=540, height=960)
    quiet = SV.solve(quiet_m, bibles(quiet_m))['characters']['pip']   # same idle drift, nobody talking
    ln = line(m, 'l1')
    pip = res['characters']['pip']
    a, b = ln['start_frame'], line(m, 'l2')['start_frame']
    nod = [pip[f]['rot']['head'][0] - quiet[f]['rot']['head'][0] for f in range(a, b)]
    tilt = [pip[f]['rot']['head'][1] - quiet[f]['rot']['head'][1] for f in range(a + 20, b)]
    assert max(nod) > 1.5, 'small nods on the speaker\'s stressed words'
    assert abs(np.mean(tilt)) > 1.2, 'an attentive head tilt'
    assert all(pip[f]['face']['eye_open'] == quiet[f]['face']['eye_open'] for f in range(a, b))


def test_saccades_jump_between_fixations_and_listeners_hold_contact():
    n = 600
    for mode in ('idle', 'speak', 'listen'):
        s = LF.saccade_track(n, FPS, 'pip', [mode] * n)
        moves = np.linalg.norm(np.diff(s, axis=0), axis=1)
        jumps = int(np.sum(moves > 1e-6))
        holds = n - 1 - jumps
        assert holds > 4 * jumps, 'mostly still fixations, quick saccades between them'
        assert 0.4 * n / FPS <= jumps <= 3 * n / FPS
    listen = LF.saccade_track(n, FPS, 'pip', ['listen'] * n)
    speak = LF.saccade_track(n, FPS, 'pip', ['speak'] * n)
    assert np.abs(listen).max() < np.abs(speak).max() and np.abs(listen).max() <= 0.1 + 1e-9


def test_speech_and_mouth_untouched_outside_lines(talk):
    m, res, env = talk
    quiet = range(line(m, 'l2')['est_end_frame'] + 20, m['duration_frames'])
    for cid, frames in res['characters'].items():
        assert not any(frames[f]['face']['speaking'] for f in quiet)
        # The expression may hold the mouth open, but nothing flaps it outside the lines.
        assert all(frames[f]['face']['mouth'] == frames[quiet[0]]['face']['mouth'] for f in quiet), cid


def test_authored_arm_pose_wins_over_gestures():
    # Right hand authored on the chest for the whole line: it stays there while the left hand talks.
    keys = [{'t': 0.0, 'arms': {'right': 'chest'}}]
    plan = conversation(bloxy_keys=keys)
    plan['performance']['bloxy'][0]['arms'] = {'right': 'chest'}
    m = C.compile_plan(plan, fps=FPS, width=540, height=960)
    res = SV.solve(m, bibles(m))
    cs = SV.CharacterSolver(m, 'bloxy', bibles(m)['bloxy'], m['duration_frames'])
    ln = line(m, 'l1')
    frames = res['characters']['bloxy']
    for f in range(ln['start_frame'] + 10, ln['est_end_frame']):
        p, k = palm(frames[f], 'r', 1.0)
        tgt = cs.ik_world_target('r', 'chest', k, 1.0)
        assert np.linalg.norm(p - tgt) < 0.03, f
    lp = [palm(frames[f], 'l', 1.0)[1]['wrist_l'][1][1] for f in range(ln['start_frame'], ln['est_end_frame'])]
    assert np.ptp(lp) > 0.05, 'the free hand gestures instead'


def test_explicit_talk_and_idle_actions_still_work():
    talk_only = [{'character': 'bloxy', 'type': 'talk', 't': 6.5, 'anticipation_frames': 2, 'main_frames': 60,
                  'follow_through_frames': 4, 'params': {'hand': 'right'}},
                 {'character': 'pip', 'type': 'idle', 't': 6.5, 'anticipation_frames': 6, 'main_frames': 80,
                  'follow_through_frames': 6}]
    m = C.compile_plan(conversation(lines=False, actions=talk_only), fps=FPS, width=540, height=960)
    res = SV.solve(m, bibles(m))
    base = SV.solve(C.compile_plan(conversation(lines=False), fps=FPS, width=540, height=960), bibles(m))
    a1, a2 = 197, 257
    frames = res['characters']['bloxy']
    rp = [palm(frames[f], 'r', 1.0)[0] for f in range(a1, a2)]
    lp = [palm(frames[f], 'l', 1.0)[0] for f in range(a1, a2)]
    assert np.ptp([p[2] for p in rp]) > 0.03 and np.mean([p[2] for p in rp]) > np.mean([p[2] for p in lp]) + 0.15
    pitch = [frames[f]['rot']['head'][0] for f in range(a1, a2)]
    assert np.ptp(pitch) > 2.0
    sway = lambda r: np.ptp([r['characters']['pip'][f]['loc']['pelvis'][0] for f in range(200, 280)])  # noqa: E731
    assert sway(res) > sway(base)


@pytest.mark.parametrize('title', ['The Golden Coin Trade', 'The Perfect Speedrun'])
def test_story_contacts_stay_on_target_with_life_layers(title):
    # Think rests on the chin within the IK's precision; a facepalm is limited only by arm reach (the head
    # stops tracking its eye target meanwhile, which also keeps the forehead within reach: Pip's facepalm
    # in 'The Perfect Speedrun' used to miss by 15 cm with the head turned 30 degrees away).
    plan = next(p for p in json.load(open(BATCH)) if p['title'] == title)
    m = C.compile_plan(plan, fps=FPS, width=540, height=960)
    res = SV.solve(m, bibles(m))
    for a in m['tracks']['actions']:
        if a['type'] not in ('think', 'facepalm'):
            continue
        side = 'l' if a['params'].get('hand', 'right') == 'left' else 'r'
        sc = res['scales'][a['character']]
        a2 = a['start_frame'] + a['anticipation_frames'] + a['main_frames']
        for f in range(a2, a['end_frame'] - 4):
            fr = res['characters'][a['character']][f]
            p, k = palm(fr, side, sc)
            mirror = -1 if side == 'r' else 1
            pt = R.CHIN_POINT if a['type'] == 'think' else (0.05 * mirror, -0.36, 0.42)
            limit = 0.02 if a['type'] == 'think' else 0.05
            assert np.linalg.norm(p - SV.local_point(k, 'head', pt, sc)) < limit, (a['id'], f)
            assert fr['rot']['wrist_' + side] == [0.0, 0.0, 0.0]


def test_life_layers_are_deterministic(talk):
    m, res, env = talk
    again = SV.solve(m, bibles(m), envelopes=env)
    for cid in res['characters']:
        for f in range(0, m['duration_frames'], 7):
            assert res['characters'][cid][f]['rot'] == again['characters'][cid][f]['rot']
            assert res['characters'][cid][f]['face'] == again['characters'][cid][f]['face']


def test_emphasis_prefers_capitals_exclamations_and_the_voice():
    words = [(10 + 8 * i, 16 + 8 * i, w) for i, w in enumerate('we go left then we JUMP'.split())]
    peaks = LF.emphasis_peaks('We go left, then we JUMP!', words, None, 10, 60, FPS, ('t', 'l'))
    assert peaks and max(peaks, key=lambda p: p[1])[0] >= 50, 'the shouted last word is the strongest beat'
    env = np.zeros(80)
    env[24:28] = [0.3, 1.0, 1.0, 0.3]
    peaks_env = LF.emphasis_peaks('We go left, then we JUMP!', words, env, 10, 60, FPS, ('t', 'l'))
    assert any(abs(p - 25) <= 3 for p, _ in peaks_env), 'a loud syllable in the audio becomes a beat'


def test_gesture_style_is_seeded_per_line():
    styles = [LF.choose_style(('bloxy', f'l{i}'), 'happy', 'Some line here', 2.0) for i in range(40)]
    assert len(set(styles)) >= 4
    assert styles == [LF.choose_style(('bloxy', f'l{i}'), 'happy', 'Some line here', 2.0) for i in range(40)]
