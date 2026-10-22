"""Slow, local: the real Blender renderer with the new costumes, a zoom punch and a big-reaction face.

Skipped when Blender is not installed. Renders a handful of small preview frames around the punch and checks
what QA relies on: every costume piece is built and reported in the part inventory, the integrity check
accepts it, and the punch is an optical zoom the camera-jump check treats as intended.
"""
import copy
import math
import os
import shutil

import pytest

from blox import config, prefs
from blox.animation import blender as BL, rig as R, solver as SV
from blox.manifest import compile as C
from blox.qa import motion as MO
from blox.qa.report import Checks

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not shutil.which(config.BLENDER_BIN), reason='Blender not installed')]

BIBLES = {
    'king': {'scale': 1.0, 'costume': {'hair': 'spiky', 'hat': 'crown', 'eyewear': 'sunglasses', 'top': 'jacket',
                                       'tie': True, 'badge': None},
             'palette': {'top': '#FFFFFF', 'top2': '#B23A48', 'hair': '#3B2A20', 'accessory': '#F5C242'}},
    'kid': {'scale': 0.75, 'costume': {'hair': 'pigtails', 'hat': 'bow', 'eyewear': 'glasses', 'top': 'cardigan',
                                       'tie': False, 'badge': 'diamond'},
            'palette': {'top': '#FFB703', 'top2': '#219EBC', 'accessory': '#FF4D8D'}},
}


def plan():
    return {
        'title': 'look', 'duration_s': 3.0,
        'setting': {'preset': 'studio', 'time_of_day': 'noon', 'lighting': 'soft_overcast', 'props': []},
        'cast': [{'id': 'king', 'character_id': 'ch_king'}, {'id': 'kid', 'character_id': 'ch_kid'}],
        'shots': [{'id': 's1', 'start_s': 0.0, 'end_s': 3.0,
                   'camera': {'subject': 'king', 'framing_start': 'medium', 'framing_end': 'close_up',
                              'move': 'zoom_punch', 'punch_t': 1.0, 'side': 'front'}}],
        'beats': [{'start_s': 0, 'purpose': 'hook'}, {'start_s': 2, 'purpose': 'payoff'}],
        'performance': {
            'king': [{'t': 0, 'position': [-0.8, 0.0], 'facing': 0, 'expression': 'smug_max',
                      'eye_target': {'kind': 'camera'}},
                     {'t': 1.0, 'expression': 'screaming', 'snap': True}],
            'kid': [{'t': 0, 'position': [0.9, 0.3], 'facing': -20, 'expression': 'mischief',
                     'eye_target': {'kind': 'character', 'id': 'king'}}],
        },
        'actions': [], 'lines': [],
        'hook': {'text': 'x', 't_end': 1.0}, 'payoff': {'text': 'y', 't_start': 2.0},
    }


def test_costumes_render_and_the_zoom_punch_is_intended(tmp_path):
    m = C.compile_plan(plan(), fps=30, width=270, height=480)
    solved = SV.solve(m, BIBLES)
    cam = m['shots'][0]['camera']
    f0, f1 = cam['punch_frame'] - 1, cam['punch_frame'] + cam['punch_frames'] + 2
    p = copy.deepcopy(prefs.DEFAULTS)
    p['production'].update(width=270, height=480)
    res = BL.render_range(m, BIBLES, solved, f0, f1, str(tmp_path), p, quality='preview')
    assert os.path.getsize(res['video']) > 1000 and os.path.exists(res['preview'])
    tele = MO.load_telemetry([res['telemetry']])
    assert sorted(tele) == list(range(f0, f1))
    for cid, bible in BIBLES.items():
        recs = {f: tele[f]['characters'][cid] for f in tele}
        want = set(R.costume_parts(bible['costume']))
        for r in recs.values():
            seen = set(r['parts_visible'])
            assert set(R.REQUIRED_PARTS) <= seen and (seen - set(R.REQUIRED_PARTS)) == want, (cid, seen)
        ck = Checks(30)
        MO._integrity(ck, m, cid, recs)
        assert ck.items[0]['status'] == 'pass', ck.items[0]['evidence']
    # Optical snap zoom: the lens narrows across the punch while the camera body stays put.
    cams = [tele[f]['camera'] for f in range(f0, f1)]
    assert cams[-1]['lens'] > 1.5 * cams[0]['lens']
    assert max(math.dist(c['location'], cams[0]['location']) for c in cams) < 1e-3
    ck = Checks(30)
    MO._camera(ck, m, tele)
    assert ck.items[0]['status'] == 'pass' and ck.items[0]['evidence']['zoom_punch_frames'] >= 3
