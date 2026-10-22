"""Locomotion and body mechanics: footsteps stay exact, arms swing against the legs, heel-toe roll, springs."""
import json
import math
import os

import numpy as np
import pytest

from blox import demo
from blox.animation import rig as R, solver as SV
from blox.manifest import compile as C
from blox.manifest.geometry import direction

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BATCH = os.path.join(ROOT, 'stories', 'batch-2026-10-03-claude.json')


def bibles(m):
    return {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id']) for c in m['cast']}


def world(fr, sc):
    return SV.fk({'root': fr['root'], 'yaw': fr['yaw'], 'rot': fr['rot'], 'loc': fr['loc']}, sc)


def walk_plan(walk_to=(-3.2, 1.4), walk_frames=80, run=True):
    """A long, fairly fast walk that starts with a half turn, then a run and a turn back to camera."""
    actions = [{'character': 'bloxy', 'type': 'walk', 't': 1.0, 'anticipation_frames': 6, 'main_frames': walk_frames,
                'follow_through_frames': 8, 'params': {'to': list(walk_to)}}]
    if run:
        actions += [{'character': 'bloxy', 'type': 'run', 't': 5.0, 'anticipation_frames': 6, 'main_frames': 36,
                     'follow_through_frames': 8, 'params': {'to': [-0.6, 2.4]}},
                    {'character': 'bloxy', 'type': 'turn', 't': 6.9, 'anticipation_frames': 4, 'main_frames': 14,
                     'follow_through_frames': 6, 'params': {'to_facing': 0}}]
    return {'title': 'Gait test', 'duration_s': 8.5,
            'setting': {'preset': 'studio', 'props': []},
            'cast': [{'id': 'bloxy', 'character_id': 'ch_bloxy'}, {'id': 'pip', 'character_id': 'ch_pip'}],
            'shots': [{'id': 's1', 'start_s': 0.0, 'end_s': 8.5,
                       'camera': {'subject': 'bloxy', 'framing_start': 'full', 'move': 'follow', 'side': 'front'}}],
            'performance': {'bloxy': [{'t': 0.0, 'position': [-0.55, 0.0], 'facing': 55}],
                            'pip': [{'t': 0.0, 'position': [0.55, 0.1], 'facing': -55}]},
            'actions': actions, 'lines': []}


def check_planted(frames, sc):
    """(frames with a planted-foot IK error, floating, sliding) like the QA and test_animation measure them."""
    errs = floats = slides = 0
    prev = None
    for fr in frames:
        k = world(fr, sc)
        cur = {}
        for s in 'lr':
            if not fr['feet'][s]['contact_expected']:
                cur[s] = None
                continue
            errs += fr['feet'][s]['ik_error_m'] > 0.001
            ank = k['ankle_' + s][1]
            floats += abs(ank[2] - R.ANKLE_HEIGHT * sc) > 0.012
            if prev and prev.get(s) is not None and np.linalg.norm(ank[:2] - prev[s][:2]) > 0.006:
                slides += 1
            cur[s] = ank
        prev = cur
    return errs, floats, slides


@pytest.fixture(scope='module')
def gait():
    m = C.compile_plan(walk_plan(), fps=30, width=540, height=960)
    return m, SV.solve(m, bibles(m))


def test_fast_walk_and_run_keep_planted_feet_exact(gait):
    m, res = gait
    for cid, frames in res['characters'].items():
        assert check_planted(frames, res['scales'][cid]) == (0, 0, 0), cid


@pytest.mark.parametrize('title', ['The Spring Pad Shortcut', 'Keys All The Way Down', 'The Lava Dare'])
def test_story_walks_keep_planted_feet_exact(title):
    # These stories had walking feet that lifted off (up to 8.6 cm) or slid while planted before the gait
    # planner; Homework Panic and Something Is Under The Bed did too, more mildly.
    plan = next(p for p in json.load(open(BATCH)) if p['title'] == title)
    m = C.compile_plan(plan, fps=30, width=540, height=960)
    res = SV.solve(m, bibles(m))
    for cid, frames in res['characters'].items():
        assert check_planted(frames, res['scales'][cid]) == (0, 0, 0), (title, cid)


def test_stand_up_does_not_drop_the_hips_through_the_floor():
    # A crouch held until stand_up used to add its drop on top of stand_up's own (hips at ankle height).
    plan = next(p for p in json.load(open(BATCH)) if p['title'] == 'The Spring Pad Shortcut')
    m = C.compile_plan(plan, fps=30, width=540, height=960)
    res = SV.solve(m, bibles(m))
    stand = next(a for a in m['tracks']['actions'] if a['type'] == 'stand_up' and a['character'] == 'bloxy')
    zs = [res['characters']['bloxy'][f]['loc']['pelvis'][2] for f in range(stand['start_frame'] - 10, stand['end_frame'])]
    assert min(zs) > -0.5
    assert all(abs(b - a) < 0.06 for a, b in zip(zs, zs[1:])), 'no pop when stand_up takes over'


def _main(m, kind):
    a = next(x for x in m['tracks']['actions'] if x['type'] == kind)
    a1 = a['start_frame'] + a['anticipation_frames']
    return a, a1, a1 + a['main_frames']


def test_arms_swing_against_the_legs(gait):
    m, res = gait
    a, a1, a2 = _main(m, 'walk')
    frames = res['characters']['bloxy']
    arm = {'l': [], 'r': []}
    leg = {'l': [], 'r': []}
    for f in range(a1 + 10, a2 - 10):
        fr = frames[f]
        fwd = np.array(direction(fr['yaw']))
        for s in 'lr':
            leg[s].append(float(np.dot(np.array(fr['feet'][s]['planned'][:2]) - np.array(fr['root'][:2]), fwd)))
            arm[s].append(fr['rot']['shoulder_' + s][0])   # + swings the arm back
    for s in 'lr':
        # The left arm swings back as the left leg reaches forward, and vice versa.
        assert np.corrcoef(arm[s], leg[s])[0, 1] > 0.6, s
        assert np.ptp(arm[s]) > 20


def test_walk_and_run_lean_forward_and_bob(gait):
    m, res = gait
    frames = res['characters']['bloxy']
    leans = {}
    for kind in ('walk', 'run'):
        a, a1, a2 = _main(m, kind)
        mid = range(a1 + (a2 - a1) // 3, a2 - (a2 - a1) // 3)
        leans[kind] = np.mean([frames[f]['rot']['spine'][0] + frames[f]['rot']['chest'][0] for f in mid])
        z = np.array([frames[f]['loc']['pelvis'][2] for f in range(a1, a2)])
        dips = sum(1 for i in range(1, len(z) - 1) if z[i] < z[i - 1] and z[i] <= z[i + 1])
        assert dips >= 3 and np.ptp(z) > 0.015, kind
    # Positive spine/chest X tips the upper body toward the facing direction.
    assert leans['walk'] > 1.5 and leans['run'] > leans['walk'] + 3


def test_steps_roll_heel_to_toe_with_fixed_pivots(gait):
    m, res = gait
    a, a1, a2 = _main(m, 'walk')
    cs = SV.CharacterSolver(m, 'bloxy', bibles(m)['bloxy'], m['duration_frames'])
    foot = cs.feet['l']
    sw = next(s for s in foot.swings if a1 + 10 <= s[0] < a2 - 20)
    f0, f1 = sw[0], sw[1]
    pitches = [foot.pose(f)[3] for f in range(f0, f1)]
    assert max(pitches) > 15 and min(pitches) < -8, 'toe-off then heel strike'
    assert foot.pose(f1)[3] == 0.0 and foot.pose(f1)[2], 'lands flat and planted'

    def edge(f, local):
        ankle, yaw, _, pitch = foot.pose(f)
        return ankle + SV.rz(yaw * SV.D2R) @ SV.rx(pitch * SV.D2R) @ (np.array(local) * cs.scale)
    t_toe, t_heel = sw[7][2], sw[7][3]
    u = {f: (f - f0) / (f1 - f0) for f in range(f0, f1)}
    toe = [edge(f, SV.TOE_PIVOT) for f in range(f0, f1) if u[f] < t_toe]
    heel = [edge(f, SV.HEEL_PIVOT) for f in range(f0, f1) if u[f] >= 1 - t_heel]
    assert len(toe) >= 2 and len(heel) >= 2
    for pts in (toe, heel):
        assert max(np.linalg.norm(p - pts[0]) for p in pts) < 1e-6
        assert abs(pts[0][2]) < 1e-6, 'the pivot edge rests on the ground'


def test_cadence_rises_with_speed():
    counts = {}
    for dist in (1.0, 3.0):
        m = C.compile_plan(walk_plan(walk_to=(-0.55 - dist, 0.0), run=False), fps=30, width=540, height=960)
        cs = SV.CharacterSolver(m, 'bloxy', bibles(m)['bloxy'], m['duration_frames'])
        counts[dist] = len(cs.feet['l'].swings) + len(cs.feet['r'].swings)
    assert counts[3.0] > counts[1.0] >= 3
    # Natural walking cadence range: about 1.5 to 3.2 steps per second over the 80-frame walk.
    assert counts[3.0] - 1 <= 3.2 * 80 / 30 + 1


def test_springs_ease_overshoot_slightly_and_settle():
    rise = {}
    for name, (k, c) in SV.BODY_SPRINGS.items():
        sp = SV.Spring(k, c)
        sp.step([0.0])
        xs = [float(sp.step([1.0])[0]) for _ in range(40)]
        assert 1.01 < max(xs) < 1.15, (name, max(xs))
        assert all(abs(x - 1) < 0.02 for x in xs[25:]), name
        rise[name] = next(i for i, x in enumerate(xs) if x >= 0.5)
    # Overlapping action: the head trails the pelvis.
    assert rise['head'] > rise['pelvis']


def test_lean_forward_moves_the_head_toward_the_facing_direction():
    plan = walk_plan(run=False)
    plan['actions'] = []
    plan['performance']['bloxy'] = [{'t': 0.0, 'position': [0.0, 0.0], 'facing': 0},
                                    {'t': 2.0, 'torso': {'lean_forward': 20}}]
    m = C.compile_plan(plan, fps=30, width=540, height=960)
    res = SV.solve(m, bibles(m))
    head0 = world(res['characters']['bloxy'][10], 1.0)['head'][1]
    head1 = world(res['characters']['bloxy'][90], 1.0)['head'][1]
    assert head1[1] < head0[1] - 0.1, 'facing 0 is -y: a forward lean moves the head toward -y'


def test_camera_does_not_depend_on_the_body_layers(gait):
    m, res = gait
    chars = {c['id']: SV.CharacterSolver(m, c['id'], bibles(m).get(c['id']), m['duration_frames']) for c in m['cast']}
    assert SV.solve_camera(m, chars, m['duration_frames']) == res['camera']
    assert math.isfinite(res['camera'][0]['lens'])
