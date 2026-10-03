"""Comedy-edit effects: zoom punch, impact shake and freeze frames, from plan to QA."""
import copy
import math
import os
import shutil

import numpy as np
import pytest

from blox import config, demo, media, prefs
from blox.animation import solver as SV
from blox.assembly import apply_freezes, freeze_graph
from blox.manifest import compile as C, director, schema as S
from blox.manifest.validate import validate
from blox.qa import motion as MO, technical
from blox.qa.report import Checks
from blox.story import generate as G


def P():
    return copy.deepcopy(prefs.DEFAULTS)


def build(plan, **kw):
    return C.compile_plan(plan, fps=30, width=1080, height=1920, **kw)


def codes(rep):
    return {e['code'] for e in rep['errors']}


def punch_plan(**cam):
    p = demo.plan()
    p['shots'][0]['camera'].update({'move': 'zoom_punch', 'framing_start': 'medium', 'framing_end': 'close_up', **cam})
    return p


def bibles(m):
    return {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id']) for c in m['cast']}


def telemetry_from(solved):
    tele = {}
    for f, c in enumerate(solved['camera']):
        d = [b - a for a, b in zip(c['location'], c['look_at'])]
        n = math.sqrt(sum(x * x for x in d))
        tele[f] = {'camera': {'location': c['location'], 'forward': [x / n for x in d], 'lens': c['lens']}}
    return tele


def camera_check(m, tele):
    ck = Checks(30)
    MO._camera(ck, m, tele)
    return ck.items[0]


def test_vocabulary():
    assert 'zoom_punch' in S.CAMERA_MOVES and set(S.EFFECTS) == {'shake', 'freeze'}


def test_zoom_punch_compiles_to_a_short_snap_that_holds():
    m = build(punch_plan())
    s = m['shots'][0]
    cam = s['camera']
    span = s['end_frame'] - s['start_frame']
    assert cam['punch_frame'] == s['start_frame'] + round(0.3 * span) and cam['punch_frames'] == S.ZOOM_PUNCH_FRAMES
    p0, n = cam['punch_frame'], cam['punch_frames']
    u = [C.camera_state(s, f)['progress'] for f in range(s['start_frame'], s['end_frame'])]
    assert set(u[:p0 - s['start_frame'] + 1]) == {0.0} and set(u[p0 + n - s['start_frame']:]) == {1.0}
    assert u[p0 + 1 - s['start_frame']] > 1 / n  # fast start: more than an even share in the first frame
    assert C.camera_state(s, s['end_frame'] - 1)['framing'] == 'close_up'
    m2 = build(punch_plan(punch_t=2.0, punch_frames=4))
    assert m2['shots'][0]['camera']['punch_frame'] == 60 and m2['shots'][0]['camera']['punch_frames'] == 4


@pytest.mark.parametrize('pace', [1.5, 2.0])
def test_zoom_punch_follows_the_pace_but_stays_a_zoom(pace):
    a, b = build(punch_plan()), build(punch_plan(), pace=pace)
    ca, cb = a['shots'][0]['camera'], b['shots'][0]['camera']
    assert abs(cb['punch_frame'] - ca['punch_frame'] / pace) <= 1.5
    assert cb['punch_frames'] >= S.ZOOM_PUNCH_MIN_FRAMES and abs(cb['punch_frames'] - ca['punch_frames'] / pace) <= 1.5


def test_zoom_punch_validation():
    assert validate(build(punch_plan()), P())['ok']
    assert 'zoom_punch_framing' in codes(validate(build(punch_plan(framing_end='medium')), P()))
    assert 'zoom_punch_timing' in codes(validate(build(punch_plan(punch_t=3.4)), P()))


def test_zoom_punch_is_an_optical_zoom_and_qa_treats_it_as_intended():
    m = build(punch_plan())
    solved = SV.solve(m, bibles(m))
    s = m['shots'][0]
    frames = solved['camera'][s['start_frame']:s['end_frame']]
    lenses = [c['lens'] for c in frames]
    assert lenses[0] == SV.LENS_BY_FRAMING['medium'] and lenses == sorted(lenses)
    ratio = S.FRAMING_HEIGHT['medium'] / S.FRAMING_HEIGHT['close_up']
    assert lenses[-1] == pytest.approx(SV.LENS_BY_FRAMING['medium'] * ratio, rel=0.01)
    assert max(math.dist(c['location'], frames[0]['location']) for c in frames) < 1e-6  # the camera holds still
    item = camera_check(m, telemetry_from(solved))
    assert item['status'] == 'pass', item['evidence']
    assert item['evidence']['zoom_punch_frames'] == m['shots'][0]['camera']['punch_frames']


def test_an_unplanned_lens_jump_is_still_a_jump():
    m = build(demo.plan())
    solved = SV.solve(m, bibles(m))
    tele = telemetry_from(solved)
    s = m['shots'][2]
    f = (s['start_frame'] + s['end_frame']) // 2
    tele[f]['camera']['lens'] *= 2
    item = camera_check(m, tele)
    assert item['status'] == 'fail' and item['evidence']['jumps'][0]['frame'] == f


def test_impact_shake_jolts_then_settles_within_the_camera_limits():
    p = demo.plan()
    p['effects'] = [{'type': 'shake', 't': 13.2, 'strength': 1.0}]
    m = build(p)
    e = m['effects'][0]
    assert e['frames'] == S.EFFECT_DEFAULT_FRAMES['shake'] and e['strength'] == 1.0
    assert SV.shake_offset(m['effects'], e['frame'] - 1) == (0.0, 0.0)
    assert SV.shake_offset(m['effects'], e['frame'] + e['frames']) == (0.0, 0.0)
    mags = [math.hypot(*SV.shake_offset(m['effects'], e['frame'] + i)) for i in range(e['frames'])]
    assert max(mags[:3]) > 0.5 and mags[-1] < 0.1
    shaken, still = SV.solve(m, bibles(m))['camera'], SV.solve(build(demo.plan()), bibles(m))['camera']
    f = e['frame'] + 1
    assert math.dist(shaken[f]['look_at'], still[f]['look_at']) > 0.01
    assert shaken[e['frame'] + e['frames'] + 2]['look_at'] == still[e['frame'] + e['frames'] + 2]['look_at']
    assert camera_check(m, telemetry_from({'camera': shaken}))['status'] == 'pass'


def test_effects_compile_with_pace_and_minimums():
    p = demo.plan()
    p['effects'] = [{'type': 'freeze', 't': 23.0, 'frames': 15}, {'type': 'shake', 't': 13.2, 'frames': 6}]
    a, b = build(copy.deepcopy(p)), build(copy.deepcopy(p), pace=2.0)
    assert [e['type'] for e in a['effects']] == ['shake', 'freeze']
    for x, y in zip(a['effects'], b['effects']):
        assert abs(y['frame'] - x['frame'] / 2.0) <= 1.5
        assert y['frames'] == max(S.EFFECT_MIN_FRAMES[x['type']], round(x['frames'] / 2.0))
    assert build(demo.plan())['effects'] == []


def _freeze_plan(t, frames=12):
    p = demo.plan()
    p['effects'] = [{'type': 'freeze', 't': t, 'frames': frames}]
    return p


def test_freeze_rules():
    assert validate(build(_freeze_plan(23.0)), P())['ok']                       # a pause between lines
    assert 'freeze_over_dialogue' in codes(validate(build(_freeze_plan(4.2)), P()))
    assert 'freeze_hides_expression' in codes(validate(build(_freeze_plan(24.0)), P()))
    assert 'freeze_across_cut' in codes(validate(build(_freeze_plan(20.8, 15)), P()))
    assert 'effect_timing' in codes(validate(build(_freeze_plan(29.9, 15)), P()))
    bad = demo.plan()
    bad['effects'] = [{'type': 'slowmo', 't': 3}]
    rep = validate(build(bad), P())
    assert 'unsupported_effect' in codes(rep) and {'kind': 'effect', 'value': 'slowmo'} in rep['unsupported']
    bad['effects'] = [{'type': 'shake', 't': 13, 'strength': 3}]
    assert 'out_of_range' in codes(validate(build(bad), P()))


def test_planned_freezes_are_not_unexpected():
    m = build(_freeze_plan(23.0, 45))
    e = m['effects'][0]
    a, b = e['frame'] / 30, (e['frame'] + e['frames']) / 30
    moving, intended = technical.judge_freezes(m, [(a, b)], 1.0)
    assert moving == [] and intended == [[round(a, 2), round(b, 2)]]
    # The same stretch without the effect would be judged (it overlaps planned motion here).
    plain = build(demo.plan())
    line = plain['lines'][0]
    moving, _ = technical.judge_freezes(plain, [(line['start_frame'] / 30, line['est_end_frame'] / 30)], 0.5)
    assert moving and moving[0]['speech'] == [line['id']]


def test_freeze_filter_graph():
    g, out = freeze_graph([{'frame': 30, 'frames': 10}])
    assert g == '[0:v][1:v]freezeframes=first=31:last=39:replace=30[v0]' and out == '[v0]'
    g, out = freeze_graph([{'frame': 50, 'frames': 6}, {'frame': 10, 'frames': 8}])
    assert g.startswith('[1:v]split=2[r0][r1]') and 'first=11:last=17:replace=10[v0]' in g
    assert '[v0][r1]freezeframes=first=51:last=55:replace=50[v1]' in g and out == '[v1]'


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg not installed')
def test_freeze_holds_the_picture_without_changing_the_frame_count(tmp_path):
    src = str(tmp_path / 'cut.mp4')
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x160:rate=30',
               '-frames:v', '40', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', src])
    apply_freezes([{'frame': 10, 'frames': 8}], str(tmp_path), 'cut.mp4', 'frozen.mp4')
    g = MO.decode_gray(os.path.join(tmp_path, 'frozen.mp4'), 96, 160)
    assert len(g) == 40
    diffs = [float(np.abs(g[f] - g[10]).mean()) for f in range(8, 21)]
    assert max(diffs[2:10]) < 0.5            # frames 10..17 show frame 10
    assert diffs[0] > 2 and diffs[-1] > 2    # and the picture moves before and after


def test_director_script_and_llm_schema_know_the_effects():
    p = _freeze_plan(23.0)
    p['shots'][0]['camera']['move'] = 'zoom_punch'
    text = director.script(build(p), {'hero': 'Bloxy', 'pip': 'Pip'})
    assert 'Zoom punch at' in text and 'Effect: freeze frame' in text and 'Music drops out' in text
    sch = G.plan_schema(['hero', 'pip'], ['ch_bloxy', 'ch_pip'])['properties']
    assert sch['effects']['items']['properties']['type']['enum'] == list(S.EFFECTS)
    assert sch['style']['properties']['expression_snap']['enum'] == S.EXPRESSION_SNAP
    assert 'punch_t' in sch['shots']['items']['properties']['camera']['properties']
    llm = {'effects': [{'type': 'shake', 't': 3, 'frames': None, 'strength': None}], 'performance': [], 'actions': [],
           'shots': [{'id': 's1', 'camera': {'move': 'static', 'punch_t': None}}], 'setting': {'props': []}}
    plan = G.to_plan(llm)
    assert plan['effects'] == [{'type': 'shake', 't': 3}] and 'punch_t' not in plan['shots'][0]['camera']
