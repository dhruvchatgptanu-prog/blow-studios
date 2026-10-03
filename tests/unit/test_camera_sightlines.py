"""Cameras never sit inside, or look through, another character; QA catches it if they do."""
import json
import os

import numpy as np
import pytest

from blox import demo
from blox.animation import solver as SV
from blox.manifest import compile as C
from blox.qa import motion
from blox.qa.report import Checks

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BATCH = os.path.join(ROOT, 'stories', 'batch-2026-10-03-claude.json')


def _bibles(m):
    return {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id']) for c in m['cast']}


def _stories():
    with open(BATCH) as f:
        return json.load(f)


@pytest.mark.parametrize('i', range(12))
def test_no_shot_subject_is_hidden_in_the_story_batch(i):
    plan = _stories()[i]
    m = C.compile_plan(plan, fps=30, width=540, height=960)
    bibles = _bibles(m)
    chars = {c['id']: SV.CharacterSolver(m, c['id'], bibles.get(c['id']), m['duration_frames']) for c in m['cast']}
    cams = SV.solve_camera(m, chars, m['duration_frames'])
    for s in m['shots']:
        subj = s['camera']['subject']
        if subj not in chars:
            continue
        frames = cams[s['start_frame']:s['end_frame']]
        for i, fr in enumerate(frames):  # never inside anyone
            f = s['start_frame'] + i
            for oid, o in chars.items():
                d = np.hypot(fr['location'][0] - o.root_xy[f][0], fr['location'][1] - o.root_xy[f][1])
                assert oid == subj or d > 0.42 * o.scale, (plan['title'], s['id'], f, oid)
        blocked = SV._blocked_frames(frames, chars, subj, s['start_frame'])
        assert len(blocked) <= max(15, 0.25 * len(frames)), (plan['title'], s['id'], len(blocked))


def test_front_camera_behind_the_other_character_swings_around():
    plan = next(p for p in _stories() if p['title'] == 'The Golden Coin Trade')
    m = C.compile_plan(plan, fps=30, width=540, height=960)
    solved = SV.solve(m, _bibles(m))
    s4 = next(s for s in m['shots'] if s['id'] == 's4')
    assert s4['camera']['side'] == 'front'
    cam = solved['camera'][s4['start_frame'] + 10]
    assert cam.get('occlusion_avoided_deg')
    pip = solved['characters']['pip'][s4['start_frame'] + 10]['root']
    assert np.hypot(cam['location'][0] - pip[0], cam['location'][1] - pip[1]) > 0.5


def _tele(cam_xy):
    rec = lambda root, face: {'root': root, 'face': face}
    return {f: {'camera': {'location': [cam_xy[0], cam_xy[1], 1.7]},
                'characters': {'hero': rec([-0.4, 0, 0], [-0.1, 0, 1.75]), 'pal': rec([1.1, 0, 0], [0.8, 0, 1.6])}}
            for f in range(30)}


def test_qa_flags_a_camera_inside_another_character():
    m = {'shots': [{'id': 's1', 'start_frame': 0, 'end_frame': 30, 'camera': {'subject': 'hero'}}]}
    ck = Checks(30)
    motion._sightlines(ck, m, _tele((1.15, 0.0)), {'scales': {'pal': 0.9}})
    c = ck.items[0]
    assert c['status'] == 'fail' and c['severity'] == 'critical' and c['evidence']['camera_inside_character']
    assert c['repair']['params'] == {'camera_clear': True}
    ck = Checks(30)
    motion._sightlines(ck, m, _tele((0.75, 0.8)), {'scales': {'pal': 0.9}})
    assert ck.items[0]['status'] == 'pass'
