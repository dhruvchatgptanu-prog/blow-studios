"""Slow, local integration test: the real Blender renderer (no credentials, no network).

Renders a few frames of the demo through Blender headless, then checks the evaluated telemetry that
QA relies on. Skipped automatically when Blender is not installed.
"""
import os
import shutil

import pytest

from blox import config, demo, prefs
from blox.animation import blender as BL, solver as SV
from blox.manifest import compile as C

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not shutil.which(config.BLENDER_BIN), reason='Blender not installed')]


def test_blender_renders_frames_and_telemetry(tmp_path):
    m = C.compile_plan(demo.plan(), fps=30, width=270, height=480)
    bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id']) for c in m['cast']}
    solved = SV.solve(m, bibles)
    p = prefs.DEFAULTS
    a = next(x for x in m['tracks']['actions'] if x['type'] == 'facepalm')
    f0 = a['start_frame'] + a['anticipation_frames'] + a['main_frames']
    res = BL.render_range(m, bibles, solved, f0, f0 + 4, str(tmp_path), dict(p, production=dict(
        p['production'], width=270, height=480)), quality='preview')
    assert os.path.getsize(res['video']) > 1000 and os.path.exists(res['preview'])
    from blox.qa.motion import load_telemetry
    tele = load_telemetry([res['telemetry']])
    assert sorted(tele) == list(range(f0, f0 + 4))
    rec = tele[f0]
    hero = rec['characters']['hero']
    assert set(hero['parts_visible']) >= {'head', 'torso'}
    assert hero['face_dot'] > 0.2, 'hero should face the camera in this shot'
    # Evaluated scene agrees with the host-side solver within a few millimetres.
    import numpy as np
    assert np.linalg.norm(np.array(hero['root'][:2]) - np.array(solved['characters']['hero'][f0]['root'][:2])) < 0.005
    # The chin point QA uses for "think" matches the solver's chin target (the hand's IK goal).
    from blox.animation import rig as R
    sc = solved['scales']['hero']
    fr = solved['characters']['hero'][f0]
    k = SV.fk({'root': fr['root'], 'yaw': fr['yaw'], 'rot': fr['rot'], 'loc': fr['loc']}, sc)
    chin = SV.local_point(k, 'head', R.CHIN_POINT, sc)
    assert np.linalg.norm(np.array(hero['chin']) - chin) < 0.01
