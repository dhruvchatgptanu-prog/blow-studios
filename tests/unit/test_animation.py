"""Deterministic motion solver: IK, foot contacts, contact targets, determinism and visemes."""
import math

import numpy as np
import pytest

from blox import demo
from blox.animation import rig as R, solver as SV, visemes
from blox.manifest import compile as C


@pytest.fixture(scope='module')
def solved():
    m = C.compile_plan(demo.plan(), fps=30, width=1080, height=1920)
    bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id']) for c in m['cast']}
    return m, SV.solve(m, bibles)


def world(fr, sc):
    return SV.fk({'root': fr['root'], 'yaw': fr['yaw'], 'rot': fr['rot'], 'loc': fr['loc']}, sc)


@pytest.mark.parametrize('target', [(0.1, -0.2, -0.5), (0.0, 0.1, -0.7), (0.2, 0.0, -0.4)])
def test_two_bone_ik_reaches_reachable_targets(target):
    root = np.zeros(3)
    upper, angle, err, _ = SV.two_bone(root, np.array(target), R.THIGH, R.SHIN, np.array([0, -1.0, 0]), +1)
    assert err < 1e-3
    assert 0 <= abs(angle) <= 180


def test_two_bone_ik_reports_unreachable():
    _, _, err, _ = SV.two_bone(np.zeros(3), np.array([0, 0, -2.0]), R.THIGH, R.SHIN, np.array([0, -1.0, 0]), +1)
    assert err > 0.5


def test_planted_feet_neither_float_nor_slide(solved):
    m, res = solved
    for cid, frames in res['characters'].items():
        sc = res['scales'][cid]
        prev = None
        floats = slides = 0
        for fr in frames:
            k = world(fr, sc)
            cur = {}
            for side in 'lr':
                if not fr['feet'][side]['contact_expected']:
                    cur[side] = None
                    continue
                ank = k['ankle_' + side][1]
                ground = fr['feet'][side].get('ground_z', 0.0) if isinstance(fr['feet'][side], dict) else 0.0
                if abs(ank[2] - ground - R.ANKLE_HEIGHT * sc) > 0.012:
                    floats += 1
                if prev and prev.get(side) is not None and np.linalg.norm(ank[:2] - prev[side][:2]) > 0.006:
                    slides += 1
                cur[side] = ank
            prev = cur
        assert floats == 0, f'{cid}: planted foot off the ground in {floats} frames'
        assert slides == 0, f'{cid}: planted foot slid in {slides} frames'


def test_facepalm_hand_reaches_face(solved):
    m, res = solved
    a = next(x for x in m['tracks']['actions'] if x['type'] == 'facepalm')
    f = a['start_frame'] + a['anticipation_frames'] + a['main_frames'] + 2
    fr = res['characters']['hero'][f]
    sc = res['scales']['hero']
    k = world(fr, sc)
    mirror = -1 if a['params'].get('hand', 'right') == 'right' else 1
    palm = SV.local_point(k, 'wrist_r', (0, 0, -R.HAND_REACH), sc)
    face = SV.local_point(k, 'head', (0.05 * mirror, -0.36, 0.42), sc)
    assert np.linalg.norm(palm - face) < 0.06


def test_wave_raises_hand_above_shoulder(solved):
    m, res = solved
    a = next(x for x in m['tracks']['actions'] if x['type'] == 'wave')
    f = a['start_frame'] + a['anticipation_frames'] + a['main_frames'] // 2
    cid = a['character']
    fr = res['characters'][cid][f]
    k = world(fr, res['scales'][cid])
    side = 'l' if a['params']['hand'] == 'left' else 'r'
    assert k['wrist_' + side][1][2] > k['shoulder_' + side][1][2]


def test_jump_lands_at_target(solved):
    m, res = solved
    a = next(x for x in m['tracks']['actions'] if x['type'] == 'jump')
    fr = res['characters']['hero'][a['end_frame'] + 2]
    assert math.dist(fr['root'][:2], a['params']['to']) < 0.05


def test_solver_is_deterministic(solved):
    m, res = solved
    bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id']) for c in m['cast']}
    again = SV.solve(m, bibles)
    for cid in res['characters']:
        for f in (0, 200, 450, 899):
            assert res['characters'][cid][f]['rot'] == again['characters'][cid][f]['rot']
    assert res['camera'][300] == again['camera'][300]


def test_every_frame_has_camera_and_both_characters(solved):
    m, res = solved
    assert len(res['camera']) == m['duration_frames']
    for cid in ('hero', 'pip'):
        assert len(res['characters'][cid]) == m['duration_frames']


def test_mouth_moves_only_during_dialogue(solved):
    m, res = solved
    hero = res['characters']['hero']
    l1 = next(x for x in m['lines'] if x['id'] == 'l1')

    def vis(fr):
        return sum(v for k, v in fr['face']['mouth'].items() if k.startswith('vis:'))
    inside = [hero[f] for f in range(l1['start_frame'], l1['est_end_frame'])]
    assert sum(fr['face']['speaking'] for fr in inside) > 0.8 * len(inside)
    assert max(vis(fr) for fr in inside) > 0.3
    talking = {f for ln in m['lines'] if ln['speaker'] == 'hero' for f in range(ln['start_frame'] - 3, ln['est_end_frame'] + 6)}
    quiet = [f for f in range(1, m['duration_frames']) if f not in talking and f - 1 not in talking]
    assert quiet and not any(hero[f]['face']['speaking'] for f in quiet)
    # Expressions may hold an open mouth ("o" when startled), but there is no lip flapping without speech.
    jumps = [f for f in quiet if abs(vis(hero[f]) - vis(hero[f - 1])) > 0.08]
    assert len(jumps) <= 2, jumps


def test_viseme_rules_cover_text():
    words = [{'word': w, 'start': 0.25 * i, 'end': 0.25 * i + 0.2} for i, w in enumerate(['Wait', 'where', 'is', 'it'])]
    keys = visemes.viseme_keys(words, 1.0)
    assert keys and all(1.0 <= k['t'] <= 2.1 for k in keys)
    assert {k['viseme'] for k in keys} <= set(R.VISEMES) | {'rest', 'REST'}


def test_camera_frames_the_subject_face(solved):
    m, res = solved
    from blox.manifest import geometry as G
    s = m['shots'][0]
    cam = res['camera'][s['start_frame'] + 10]
    hero = res['characters']['hero'][s['start_frame'] + 10]
    k = world(hero, res['scales']['hero'])
    head = k['head'][1]
    fwd = np.array(cam['look_at']) - np.array(cam['location'])
    fwd /= np.linalg.norm(fwd)
    to_head = head - np.array(cam['location'])
    cosang = float(fwd @ to_head / np.linalg.norm(to_head))
    assert cosang > math.cos(math.radians(20)), 'head should be near the optical axis'
    assert G.CAMERA_SIDE_DEG['front'] == 0
