"""The camera never starts inside scenery or looks through it at the subject; QA checks the same boxes."""
import copy
import json
import math
import os

import numpy as np
import pytest

from blox import demo
from blox.animation import sets as SETS, solver as SV
from blox.manifest import compile as C
from blox.qa import motion
from blox.qa.report import Checks

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BATCH = os.path.join(ROOT, 'stories', 'batch-2026-10-03-claude.json')


def _plans():
    with open(BATCH) as f:
        return json.load(f) + [demo.plan()]


def _setup(plan):
    m = C.compile_plan(plan, fps=30, width=540, height=960)
    bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id'])
              for c in m['cast']}
    chars = {c['id']: SV.CharacterSolver(m, c['id'], bibles.get(c['id']), m['duration_frames']) for c in m['cast']}
    lay = SETS.layout(m, {cid: c.scale for cid, c in chars.items()})
    return m, chars, lay


@pytest.mark.parametrize('i', range(len(_plans())))
def test_no_camera_inside_or_blocked_by_scenery(i):
    m, chars, lay = _setup(_plans()[i])
    cams = SV.solve_camera(m, chars, m['duration_frames'], set_layout=lay)
    everything = SETS.box_arrays(SETS.piece_boxes(lay) + SETS.prop_boxes(m))
    set_only = SETS.box_arrays(SETS.piece_boxes(lay))
    for s in m['shots']:
        subj = s['camera']['subject']
        frames = cams[s['start_frame']:s['end_frame']]
        locs = np.array([fr['location'] for fr in frames])
        assert not SETS.inside(everything, locs).any(), (m['title'], s['id'])
        # A prop subject may sit on or in another prop (a phone on a rock, a key in a chest), so only set
        # pieces count as blockers for it, as in the solver and QA.
        boxes = everything if (subj in chars or subj == 'two_shot') else set_only
        blocked = SV._scenery_blocked_frames(m, frames, chars, subj, s['start_frame'], boxes)
        assert blocked == [], (m['title'], s['id'], blocked[:5])


@pytest.mark.parametrize('i', range(len(_plans())))
def test_camera_zones_bound_where_the_solver_puts_the_camera(i):
    """The clearance is computed from the manifest alone; it must contain every solved camera."""
    m, chars, lay = _setup(_plans()[i])
    cams = SV.solve_camera(m, chars, m['duration_frames'], set_layout=lay)
    zones = {z['shot']: z for z in lay['area']['zones']}
    for s in m['shots']:
        zn = zones[s['id']]
        box = SETS.rect(*zn['box'])
        for fr in cams[s['start_frame']:s['end_frame']]:
            x, y, z = fr['location']
            assert SETS.poly_dist([(x, y)] * 4, box) <= zn['reach'] + 1e-6, (m['title'], s['id'])
            assert z >= zn['z'][0] - 1e-6 and z <= zn['z'][1] + 1e-6, (m['title'], s['id'], z, zn['z'])


def _with_wall(lay, centre, yaw, size=(3.0, 0.4, 4.0)):
    lay = copy.deepcopy(lay)
    lay['templates']['testwall'] = {'parts': [SETS.P('box', 0, 0, size[2] / 2, *size, '#888888')],
                                    'size': list(size), 'shadow': True, 'decal': False}
    lay['pieces'].append({'id': 'testwall', 'kind': 'wall', 'template': 'testwall',
                          'pos': [centre[0], centre[1], 0.0], 'rot': yaw, 'scale': 1.0, 'size': list(size),
                          'occluder': True})
    return lay


def test_camera_swings_around_scenery_that_blocks_the_subject():
    plan = next(p for p in _plans() if p['title'] == 'The Golden Coin Trade')
    m, chars, lay = _setup(plan)
    s2 = next(s for s in m['shots'] if s['id'] == 's2')
    f = s2['start_frame'] + 10
    base = SV.solve_camera(m, chars, m['duration_frames'], set_layout=lay)
    assert base[f].get('occlusion_avoided_deg') is None
    cam = np.array(base[f]['location'])
    face = SV.view_targets(m, chars, 'bloxy', f)[0]
    mid = (cam + face) / 2
    yaw = math.degrees(math.atan2(face[1] - cam[1], face[0] - cam[0]))  # wall across the line of sight
    walled = _with_wall(lay, mid[:2], yaw - 90.0, size=(1.2, 0.2, 3.0))
    cams = SV.solve_camera(m, chars, m['duration_frames'], set_layout=walled)
    frames = cams[s2['start_frame']:s2['end_frame']]
    assert frames[0].get('occlusion_avoided_deg')
    boxes = SETS.box_arrays(SETS.piece_boxes(walled))
    assert SV._scenery_blocked_frames(m, frames, chars, 'bloxy', s2['start_frame'], boxes) == []
    # Shots the wall does not affect keep exactly the camera they had.
    wall_only = SETS.box_arrays(SETS.piece_boxes(walled)[-1:])
    untouched = 0
    for s in m['shots']:
        old = base[s['start_frame']:s['end_frame']]
        if not SV._scenery_blocked_frames(m, old, chars, s['camera']['subject'], s['start_frame'], wall_only):
            assert cams[s['start_frame']:s['end_frame']] == old, s['id']
            untouched += 1
    assert untouched >= 1


def test_solved_result_carries_the_layout_it_avoided():
    plan = next(p for p in _plans() if p['title'] == 'The Last Slice')
    m = C.compile_plan(plan, fps=30, width=540, height=960)
    bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id'])
              for c in m['cast']}
    solved = SV.solve(m, bibles)
    assert solved['set'] == SETS.layout(m, solved['scales'])
    assert json.loads(json.dumps(solved['set'])) == solved['set']


def _qa_case(cam_xy, lay):
    m = {'shots': [{'id': 's1', 'start_frame': 0, 'end_frame': 30, 'camera': {'subject': 'hero'}}],
         'setting': {'preset': 'studio', 'props': []}}
    tele = {f: {'camera': {'location': [cam_xy[0], cam_xy[1], 1.7]},
                'characters': {'hero': {'root': [0, 0, 0], 'face': [0, -0.3, 1.73]}}, 'props': {}}
            for f in range(30)}
    ck = Checks(30)
    motion._sightlines(ck, m, tele, {'scales': {}, 'set': lay})
    return [c for c in ck.items if c['id'] == 'scenery:s1'][0]


def test_qa_flags_a_camera_inside_scenery_and_a_hidden_face():
    lay = {'pieces': [], 'templates': {}}
    walled = _with_wall(lay, (0.0, -1.5), 0.0, size=(2.0, 0.3, 3.0))
    c = _qa_case((0.0, -1.5), walled)          # camera inside the wall
    assert c['status'] == 'fail' and c['severity'] == 'critical' and c['evidence']['frames_camera_inside'] == 30
    assert c['repair']['params'] == {'camera_clear': True}
    c = _qa_case((0.0, -3.0), walled)          # the wall stands between camera and face
    assert c['status'] == 'fail' and c['evidence']['frames_face_hidden'] == 30
    c = _qa_case((2.5, -1.0), walled)          # clear view past the wall's end
    assert c['status'] == 'pass' and c['evidence']['frames_face_hidden'] == 0


def test_qa_without_a_set_layout_or_setting_adds_no_scenery_check():
    m = {'shots': [{'id': 's1', 'start_frame': 0, 'end_frame': 5, 'camera': {'subject': 'hero'}}]}
    tele = {f: {'camera': {'location': [0, -2, 1.7]}, 'characters': {'hero': {'root': [0, 0, 0],
                                                                             'face': [0, -0.3, 1.7]}}}
            for f in range(5)}
    ck = Checks(5)
    motion._sightlines(ck, m, tele, {'scales': {}})
    assert [c['id'] for c in ck.items] == ['sightline:s1']
