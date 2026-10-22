"""Slow, local integration test: motion QA on Blender's evaluated scene for a walk and a dialogue.

The life layers (idle, gestures, listening) and the gait planner must survive the round trip through
Blender: planted soles do not slide or float, the walk covers its distance, and the speaker's mouth
still follows the voice. Telemetry only (no pixels are rendered). Skipped when Blender is not installed.
"""
import shutil

import pytest

from blox import config, prefs
from blox.animation import blender as BL, solver as SV
from blox.manifest import compile as C
from blox.qa import motion as MO
from blox.qa.report import Checks
from tests.unit.test_motion_gait import bibles, walk_plan
from tests.unit.test_motion_life import conversation, envelope_for

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not shutil.which(config.BLENDER_BIN), reason='Blender not installed')]


def _tele(m, solved, f0, f1, tmp_path):
    res = BL.render_range(m, bibles(m), solved, f0, f1, str(tmp_path), prefs.DEFAULTS, quality='preview',
                          telemetry_only=True)
    return MO.load_telemetry([res['telemetry']])


def test_walk_feet_and_action_pass_qa_on_the_evaluated_scene(tmp_path):
    m = C.compile_plan(walk_plan(run=False), fps=30, width=270, height=480)
    solved = SV.solve(m, bibles(m))
    walk = next(a for a in m['tracks']['actions'] if a['type'] == 'walk')
    tele = _tele(m, solved, walk['start_frame'], walk['end_frame'] + 10, tmp_path)
    ck = Checks(30)
    recs = {f: tele[f]['characters']['bloxy'] for f in tele}
    MO._feet(ck, m, 'bloxy', recs, solved['characters']['bloxy'], prefs.DEFAULTS['qa'], 1.0)
    MO._actions(ck, m, 'bloxy', recs, solved['characters']['bloxy'], tele, 1.0)
    items = {i['id']: i for i in ck.items}
    assert items['feet:bloxy']['status'] == 'pass', items['feet:bloxy']['evidence']
    assert items['feet:bloxy']['evidence']['max_slide_mm_per_frame'] < 1.0
    assert items['action:' + walk['id']]['status'] == 'pass', items['action:' + walk['id']]['evidence']


def test_dialogue_with_gestures_keeps_lip_sync_and_grounded_feet(tmp_path):
    m = C.compile_plan(conversation(), fps=30, width=270, height=480)
    env = {cid: envelope_for(m, cid) for cid in ('bloxy', 'pip')}
    solved = SV.solve(m, bibles(m), envelopes=env)
    ln = next(x for x in m['lines'] if x['id'] == 'l1')
    tele = _tele(m, solved, ln['start_frame'], ln['est_end_frame'], tmp_path)
    ck = Checks(30)
    dialog = {ln['id']: env['bloxy'][ln['start_frame']:ln['est_end_frame']]}
    for cid in ('bloxy', 'pip'):
        recs = {f: tele[f]['characters'][cid] for f in tele}
        MO._feet(ck, m, cid, recs, solved['characters'][cid], prefs.DEFAULTS['qa'], solved['scales'][cid])
    recs = {f: tele[f]['characters']['bloxy'] for f in tele}
    m_one = dict(m, lines=[ln])
    MO._lipsync(ck, m_one, 'bloxy', recs, None, dialog)
    items = {i['id']: i for i in ck.items}
    assert items['feet:bloxy']['status'] == 'pass' and items['feet:pip']['status'] == 'pass'
    lip = items['lipsync:l1']
    assert lip['status'] == 'pass', lip['evidence']
    # Beat gestures stay in front of the body, never across the mouth (QA's own occlusion test).
    assert not any(MO._hand_over_mouth(r, m['width'] / m['height']) for r in recs.values())
