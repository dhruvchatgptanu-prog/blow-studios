"""Every story in the mainstream batch solves cleanly at the production pace: cameras clear, feet planted."""
import json
import os

import numpy as np
import pytest

from blox import demo, prefs as P
from blox.animation import rig as R, solver as SV
from blox.manifest import compile as C, validate as V

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BATCH = os.path.join(ROOT, 'stories', 'batch-2026-10-11-mainstream.json')
with open(BATCH) as _f:
    PLANS = json.load(_f)
TITLES = [p['title'] for p in PLANS]


def _compiled(plan):
    p = P.DEFAULTS
    m = C.compile_plan(plan, fps=30, width=540, height=960, **C.pace_kwargs(p))
    bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id']) for c in m['cast']}
    return m, bibles


def test_batch_has_twelve_distinct_titles_and_validates():
    assert len(PLANS) == 12 and len(set(TITLES)) == 12
    rows = {c['id']: {'bible': c['bible']} for c in demo.CHARACTERS}
    for plan in PLANS:
        m, _ = _compiled(plan)
        rep = V.validate(m, P.DEFAULTS, characters=rows)
        assert not rep['errors'], (plan['title'], rep['errors'][:3])
        assert 'roblox' not in plan['title'].lower()


@pytest.mark.parametrize('title', TITLES)
def test_cameras_never_inside_or_blocked(title):
    plan = next(p for p in PLANS if p['title'] == title)
    m, bibles = _compiled(plan)
    chars = {c['id']: SV.CharacterSolver(m, c['id'], bibles.get(c['id']), m['duration_frames']) for c in m['cast']}
    cams = SV.solve_camera(m, chars, m['duration_frames'])
    for s in m['shots']:
        subj = s['camera']['subject']
        if subj not in chars:
            continue
        frames = cams[s['start_frame']:s['end_frame']]
        for i, fr in enumerate(frames):
            f = s['start_frame'] + i
            for oid, o in chars.items():
                d = np.hypot(fr['location'][0] - o.root_xy[f][0], fr['location'][1] - o.root_xy[f][1])
                assert oid == subj or d > 0.42 * o.scale, (title, s['id'], f, oid)
        blocked = SV._blocked_frames(frames, chars, subj, s['start_frame'])
        assert len(blocked) <= max(15, 0.25 * len(frames)), (title, s['id'], len(blocked))


@pytest.mark.parametrize('title', TITLES)
def test_planted_feet_neither_float_nor_slide(title):
    plan = next(p for p in PLANS if p['title'] == title)
    m, bibles = _compiled(plan)
    res = SV.solve(m, bibles)
    for cid, frames in res['characters'].items():
        sc = res['scales'][cid]
        prev, floats, slides = None, 0, 0
        for fr in frames:
            k = SV.fk({'root': fr['root'], 'yaw': fr['yaw'], 'rot': fr['rot'], 'loc': fr['loc']}, sc)
            cur = {}
            for side in 'lr':
                if not fr['feet'][side]['contact_expected']:
                    cur[side] = None
                    continue
                ank = k['ankle_' + side][1]
                ground = fr['feet'][side].get('ground_z', 0.0)
                if abs(ank[2] - ground - R.ANKLE_HEIGHT * sc) > 0.012:
                    floats += 1
                if prev and prev.get(side) is not None and np.linalg.norm(ank[:2] - prev[side][:2]) > 0.006:
                    slides += 1
                cur[side] = ank
            prev = cur
        assert floats == 0 and slides == 0, (title, cid, floats, slides)
