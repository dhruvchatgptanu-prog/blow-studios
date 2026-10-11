"""Camera headroom for tall hair and hats: the solver frames a character's visual top, the set clearance
mirrors it, and QA detects a hair or hat top cropped by the frame in a whole-head framing."""
import ast
import copy
import math
from pathlib import Path

import numpy as np
import pytest

from blox import demo
from blox.animation import blender as BL, rig as R, sets as SETS, solver as SV
from blox.manifest import compile as C, schema as S
from blox.qa import motion
from blox.qa.report import Checks

SCENE = Path(BL.SCRIPT).read_text()
PLAIN = {'hair': 'bald', 'hat': None}
BUN = {'hair': 'bun', 'hat': None, 'eyewear': 'glasses'}
CROWN = {'hair': 'short_block', 'hat': 'crown'}
BOW = {'hair': 'pigtails', 'hat': 'bow'}


def _scene_constant(name):
    for node in ast.parse(SCENE).body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


# ----------------------------------------------------------------------------- rig heights
def test_head_group_heights_match_the_blender_builder():
    assert R.HEAD_JOINT_Z == pytest.approx(1.50) and R.HEAD_TOP == pytest.approx(0.60)
    assert R.HAIR_TOP == _scene_constant('HAIR_TOP')
    assert set(R.HAIR_PEAK) == set(R.COSTUME_OPTIONS['hair']) and set(R.HAIR_TOP) == set(R.COSTUME_OPTIONS['hair'])
    assert set(R.HAT_TOP) | set(R.HAT_RISE) == {h for h in R.COSTUME_OPTIONS['hat'] if h}
    assert set(R.HAT_TOP) == R.COVERING_HATS
    # The peaks are the builder's geometry: bun box (0, 0.06, 0.82) of height 0.29; the tallest spike, 0.32 high
    # on its base at z 0.645 tilted 6 degrees; cap box 0.14 high at 0.655; the beanie pompom 0.13 high at 0.775;
    # crown points 0.12 high on a 0.11 band 0.045 above the hair; bow loops 0.19 x 0.22 tilted 20 degrees.
    assert '(0, 0.06, 0.82)' in SCENE and R.HAIR_PEAK['bun'] == pytest.approx(0.82 + 0.29 / 2)
    assert R.HAIR_PEAK['spiky'] == pytest.approx(0.645 + 0.32 * math.cos(math.radians(6)), abs=1e-3)
    assert R.HAT_TOP['cap'] == pytest.approx(0.655 + 0.07) and R.HAT_TOP['beanie'] == pytest.approx(0.775 + 0.065)
    assert R.HAT_RISE['crown'] == pytest.approx(0.045 + 0.055 + 0.12)
    assert R.HAT_RISE['bow'] == pytest.approx(0.04 + 0.03 + 0.11 * math.sin(math.radians(20))
                                              + 0.095 * math.cos(math.radians(20)), abs=1e-3)


@pytest.mark.parametrize('costume,top', [
    (PLAIN, 0.60), ({}, 0.60), (None, 0.60), ({'hair': 'short_block'}, 0.61), ({'hair': 'messy_block'}, 0.695),
    (BUN, 0.965), ({'hair': 'spiky'}, 0.963), ({'hair': 'bun', 'hat': 'cap'}, 0.725),
    ({'hair': 'spiky', 'hat': 'beanie'}, 0.84), (CROWN, 0.82), ({'hair': 'spiky', 'hat': 'crown'}, 0.963),
    (BOW, 0.867), ({'hair': 'bald', 'hat': 'bow'}, 0.797),
])
def test_visual_top_per_costume(costume, top):
    assert R.head_top(costume) == pytest.approx(top, abs=1e-3)
    assert R.visual_top(costume) == pytest.approx(1.5 + top, abs=1e-3)
    assert R.visual_top(costume, 0.72) == pytest.approx((1.5 + top) * 0.72, abs=1e-3)


def test_demo_cast_tops_follow_their_costumes_and_scales():
    cast = {c['id']: c['bible'] for c in demo.CHARACTERS}
    tops = {cid: R.visual_top(b['costume'], b['scale']) for cid, b in cast.items()}
    # Tally: a bun at scale 1.12; Dot: a bow on pigtails at 0.72; Rook: spikes hidden under a backwards cap.
    assert tops['ch_tally'] == pytest.approx((1.5 + 0.965) * 1.12, abs=1e-3)
    assert tops['ch_dot'] == pytest.approx((1.5 + 0.867) * 0.72, abs=1e-3)
    assert tops['ch_rook'] == pytest.approx((1.5 + 0.725) * 1.05, abs=1e-3)
    assert tops['ch_bloxy'] == pytest.approx(1.5 + 0.695, abs=1e-3)
    for cid, b in cast.items():
        assert tops[cid] >= R.HEIGHT * b['scale'] - 1e-9


# ----------------------------------------------------------------------------- composition maths
def _frame_edges(visible, aim):
    return aim - visible / 2, aim + visible / 2


def test_plain_head_composition_is_the_classic_one():
    top = R.visual_top(PLAIN)
    height = SV.framed_height(top)
    for framing, ratio in S.FRAMING_HEIGHT.items():
        v0 = ratio * height
        v, aim = SV.compose(v0, top, 1.0, face=True, whole_head=framing != 'extreme_close_up')
        assert v == v0, framing
        classic = height - 0.38 * v0
        if v0 < 0.8 * height:
            classic = min(classic, R.FACE_CENTER_Z + 0.06)
        classic = max(classic, min(height * 0.5, v0 * 0.32))
        assert aim == pytest.approx(classic), framing
    # Wide framings keep the same headroom fraction above the framed height.
    v, aim = SV.compose(S.FRAMING_HEIGHT['full'] * height, top)
    assert _frame_edges(v, aim)[1] == pytest.approx(height + SV.HEADROOM * v)


@pytest.mark.parametrize('costume', [BUN, CROWN, BOW, {'hair': 'spiky'}, {'hair': 'bald', 'hat': 'beanie'}])
@pytest.mark.parametrize('scale', [1.0, 0.72])
def test_tall_costumes_keep_the_top_in_frame_with_headroom(costume, scale):
    top = R.visual_top(costume, scale)
    height = SV.framed_height(top, scale)
    plain_height = SV.framed_height(R.visual_top(PLAIN, scale), scale)
    for framing in S.WHOLE_HEAD_FRAMINGS:
        v, aim = SV.compose(S.FRAMING_HEIGHT[framing] * height, top, scale)
        bottom, frame_top = _frame_edges(v, aim)
        tight = v < SV.TIGHT_RATIO * height
        margin = (SV.TIGHT_HEADROOM if tight else SV.HEADROOM) * v
        assert frame_top >= top + margin - 1e-9, (framing, costume)
        if tight:
            assert bottom <= SV.CHIN_FLOOR * scale + 1e-9, (framing, costume)  # the chin stays in
            # Tight framings still read the face large: at most 45% wider than the plain-head framing.
            assert v <= 1.45 * S.FRAMING_HEIGHT[framing] * plain_height, (framing, costume)
        else:
            assert v == pytest.approx(S.FRAMING_HEIGHT[framing] * height)
    # An extreme close-up crops the hair on purpose: the face-centred composition is untouched.
    v0 = S.FRAMING_HEIGHT['extreme_close_up'] * height
    v, aim = SV.compose(v0, top, scale, whole_head=False)
    assert v == v0 and aim == pytest.approx(R.FACE_CENTER_Z * scale + 0.06)


@pytest.mark.parametrize('costume', [{'hair': 'messy_block'}, {'hair': 'long_block'}, {'hair': 'short_block', 'hat': 'cap'},
                                     {'hair': 'bun', 'hat': 'cap_backwards'}, {'hair': 'pigtails'}])
def test_hair_within_the_classic_allowance_keeps_its_nominal_framing(costume):
    """Only genuinely tall styles widen a close-up: a zoom punch on Bloxy still lands on the nominal ratio."""
    top = R.visual_top(costume)
    assert top <= R.HEIGHT + 0.125
    height = SV.framed_height(top)
    for framing in S.WHOLE_HEAD_FRAMINGS:
        v, aim = SV.compose(S.FRAMING_HEIGHT[framing] * height, top)
        assert v == S.FRAMING_HEIGHT[framing] * height, (costume, framing)
        assert aim + v / 2 >= top + SV.TIGHT_HEADROOM * v - 1e-9


def test_composition_is_continuous_through_a_push_in():
    """A push-in from medium to close-up on a bun must not jump where the whole-head rule takes over."""
    top = R.visual_top(BUN)
    height = SV.framed_height(top)
    prev = None
    for i in range(201):
        ratio = S.FRAMING_HEIGHT['medium'] + (S.FRAMING_HEIGHT['close_up'] - S.FRAMING_HEIGHT['medium']) * i / 200
        v, aim = SV.compose(ratio * height, top)
        if prev:
            assert abs(v - prev[0]) < 0.01 and abs(aim - prev[1]) < 0.01
        prev = (v, aim)


# ----------------------------------------------------------------------------- the solved camera
def _plan(cast, shots, positions=None):
    p = demo.plan()
    p.update(title='Headroom stills', duration_s=float(max(s['end_s'] for s in shots)), cast=cast, shots=shots,
             actions=[], lines=[], sfx=[], music=[{'t': 0, 'cue': 'none'}],
             beats=[{'start_s': 0, 'purpose': 'hook', 'description': 'Everyone stands and looks at the camera.'}],
             performance={c['id']: [{'t': 0, 'position': (positions or {}).get(c['id'], [0.0, 0.0]), 'facing': 0}]
                          for c in cast})
    p['setting'] = {'preset': 'studio', 'time_of_day': 'noon', 'lighting': 'natural', 'props': []}
    return p


def _shot(sid, t0, t1, subject, framing, move='static', side='front'):
    return {'id': sid, 'start_s': t0, 'end_s': t1, 'camera': {'subject': subject, 'framing_start': framing,
                                                             'framing_end': framing, 'move': move, 'side': side}}


def _bibles(m):
    return {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id']) for c in m['cast']}


def _top_in_frame(fr, point, aspect):
    """Where a world point lands vertically in the frame of a solved camera: 0 = top edge, 1 = bottom edge."""
    loc, look = np.array(fr['location']), np.array(fr['look_at'])
    fwd = look - loc
    fwd /= np.linalg.norm(fwd)
    up = np.array([0.0, 0.0, 1.0])
    right = np.cross(fwd, up)
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    d = np.array(point) - loc
    depth = float(d @ fwd)
    sensor_v = fr['sensor_width'] * aspect if aspect > 1 else fr['sensor_width']
    half = depth * sensor_v / (2 * fr['lens'])
    return 0.5 - float(d @ up) / (2 * half)


CAST3 = [{'id': 'tally', 'character_id': 'ch_tally'}, {'id': 'rook', 'character_id': 'ch_rook'},
         {'id': 'dot', 'character_id': 'ch_dot'}]
POS3 = {'tally': [-1.6, 0.0], 'rook': [0.0, 0.0], 'dot': [1.6, 0.0]}


def _rook_with_crown(bibles):
    bibles['rook'] = copy.deepcopy(bibles['rook'])
    bibles['rook']['costume'] = dict(bibles['rook']['costume'], hat='crown')
    return bibles


@pytest.mark.parametrize('framing', ['close_up', 'medium_close', 'medium', 'full'])
def test_solved_camera_keeps_bun_crown_and_bow_in_frame(framing):
    shots = [_shot(f's{i}', i * 0.5, i * 0.5 + 0.5, cid, framing) for i, cid in enumerate(('tally', 'rook', 'dot'))]
    m = C.compile_plan(_plan(CAST3, shots, POS3), fps=30, width=540, height=960)
    bibles = _rook_with_crown(_bibles(m))
    solved = SV.solve(m, bibles)
    aspect = m['height'] / m['width']
    for s in m['shots']:
        cid = s['camera']['subject']
        top = solved['visual_tops'][cid]
        sc = solved['scales'][cid]
        assert top > R.HEIGHT * sc + 0.1, 'these three all wear something tall'
        fr = solved['camera'][s['start_frame']]
        root = solved['characters'][cid][s['start_frame']]['root']
        y_top = _top_in_frame(fr, [root[0], root[1], top], aspect)
        y_face = _top_in_frame(fr, [root[0], root[1], R.FACE_CENTER_Z * sc], aspect)
        y_chin = _top_in_frame(fr, [root[0], root[1], (R.HEAD_JOINT_Z - 0.02) * sc], aspect)
        assert 0.02 <= y_top <= 0.5, (cid, framing, y_top)       # hair or hat top inside, with headroom
        assert y_chin < 1.0 - 0.01, (cid, framing, y_chin)      # the chin too
        assert 0.2 < y_face < 0.85, (cid, framing, y_face)      # and the face reads
        if framing == 'close_up':
            assert (y_chin - y_top) > 0.5, 'a close-up still fills the frame with the head'


def test_solved_camera_differs_from_plain_head_framing_only_where_it_must():
    shots = [_shot('s0', 0.0, 0.5, 'tally', 'close_up'), _shot('s1', 0.5, 1.0, 'tally', 'full'),
             _shot('s2', 1.0, 1.5, 'tally', 'extreme_close_up')]
    plan = _plan([{'id': 'tally', 'character_id': 'ch_tally'}], shots)
    m = C.compile_plan(plan, fps=30, width=540, height=960)
    tall = SV.solve(m, _bibles(m))
    bald = copy.deepcopy(_bibles(m))
    bald['tally']['costume'] = dict(bald['tally']['costume'], hair='bald', hat=None)
    plain = SV.solve(m, bald)
    cam_t, cam_p = tall['camera'], plain['camera']
    f_cu, f_full, f_ecu = (s['start_frame'] for s in m['shots'])
    # Close-up and full: the bun needs a higher aim and a bit more distance.
    assert cam_t[f_cu]['look_at'][2] > cam_p[f_cu]['look_at'][2] + 0.1
    assert np.linalg.norm(np.array(cam_t[f_cu]['location'])[:2]) > np.linalg.norm(np.array(cam_p[f_cu]['location'])[:2])
    assert cam_t[f_full]['look_at'][2] > cam_p[f_full]['look_at'][2]
    # Extreme close-up: identical composition (the crop is deliberate).
    assert cam_t[f_ecu]['look_at'] == cam_p[f_ecu]['look_at'] and cam_t[f_ecu]['location'] == cam_p[f_ecu]['location']
    assert tall['visual_tops']['tally'] > plain['visual_tops']['tally']


# ----------------------------------------------------------------------------- set clearance mirrors the solver
def test_camera_zones_cover_the_taller_framing_and_the_layout_follows():
    shots = [_shot('s0', 0.0, 1.0, 'tally', 'close_up'), _shot('s1', 1.0, 2.0, 'tally', 'full', 'crane_up')]
    m = C.compile_plan(_plan([{'id': 'tally', 'character_id': 'ch_tally'}], shots), fps=30, width=540, height=960)
    bibles = _bibles(m)
    solved = SV.solve(m, bibles)
    tops, scales = solved['visual_tops'], solved['scales']
    with_tops = SETS.camera_zones(m, scales, tops)
    plain = SETS.camera_zones(m, scales)
    for zt, zp, s in zip(with_tops, plain, m['shots']):
        assert zt['reach'] > zp['reach'] and zt['z'][1] > zp['z'][1], s['id']
        for f in range(s['start_frame'], s['end_frame']):
            x, y, z = solved['camera'][f]['location']
            assert SETS.poly_dist([(x, y)] * 4, SETS.rect(*zt['box'])) <= zt['reach'] + 1e-6
            assert zt['z'][0] - 1e-6 <= z <= zt['z'][1] + 1e-6
    assert solved['set'] == SETS.layout(m, scales, tops)
    assert SETS.layout(m, scales, tops)['area']['zones'] != SETS.layout(m, scales)['area']['zones']


# ----------------------------------------------------------------------------- QA: cropped head group
def _tele(m, head_y0, root_z=0.0, in_front=True, x0=0.3):
    out = {}
    for f in range(m['duration_frames']):
        out[f] = {'camera': {'location': [0, -3, 1.7]}, 'characters': {
            c['id']: {'root': [0, 0, root_z], 'head2d': [x0, head_y0, x0 + 0.4, 0.6], 'in_front_of_camera': in_front,
                      'bbox2d': [0.2, head_y0, 0.8, 1.0]} for c in m['cast']}}
    return out


def _check(m, tele, solved=None):
    ck = Checks(m['fps'])
    motion._head_crop(ck, m, tele, solved or {'scales': {c['id']: 1.0 for c in m['cast']}})
    return next(c for c in ck.items if c['id'] == 'head_in_frame')


def test_qa_flags_a_cropped_hair_top_in_a_close_framing():
    shots = [_shot('s0', 0.0, 1.0, 'tally', 'close_up')]
    m = C.compile_plan(_plan([{'id': 'tally', 'character_id': 'ch_tally'}], shots), fps=30, width=540, height=960)
    bad = _check(m, _tele(m, -0.03))
    assert bad['status'] == 'fail' and bad['severity'] == 'major'
    assert bad['evidence']['worst']['character'] == 'tally' and bad['evidence']['worst']['framing'] == 'close_up'
    assert bad['repair'] == {'action': 're_render_shot', 'shot': 's0', 'params': {'camera_wider': True}}
    assert bad['target'] == {'kind': 'shot', 'id': 's0'} and bad['frames'][0] == 0
    good = _check(m, _tele(m, 0.02))
    assert good['status'] == 'pass' and good['evidence']['frames_checked'] == m['duration_frames']
    # Rounding on a bevelled corner is tolerated, a real crop is not.
    assert _check(m, _tele(m, -motion.HEAD_CROP_TOL / 2))['status'] == 'pass'
    assert _check(m, _tele(m, -motion.HEAD_CROP_TOL * 2))['status'] == 'fail'


def test_qa_head_crop_skips_deliberate_crops_jumps_and_offscreen():
    shots = [_shot('s0', 0.0, 1.0, 'tally', 'extreme_close_up'), _shot('s1', 1.0, 2.0, 'tally', 'close_up'),
             _shot('s2', 2.0, 3.0, 'prop:none', 'close_up')]
    m = C.compile_plan(_plan([{'id': 'tally', 'character_id': 'ch_tally'}], shots), fps=30, width=540, height=960)
    tele = _tele(m, -0.1)
    r = _check(m, tele)
    assert r['status'] == 'fail' and r['evidence']['frames_checked'] == 30, 'only the close-up frames count'
    assert r['evidence']['worst']['shot'] == 's1'
    assert _check(m, _tele(m, -0.1, root_z=0.3))['status'] == 'pass'           # airborne: the follow move's job
    assert _check(m, _tele(m, -0.1, in_front=False))['status'] == 'pass'       # behind the camera
    assert _check(m, _tele(m, -0.1, x0=1.2))['status'] == 'pass'               # walked out of the side
    old = {f: {'camera': r_['camera'], 'characters': {k: {x: v for x, v in c.items() if x != 'head2d'}
                                                      for k, c in r_['characters'].items()}} for f, r_ in tele.items()}
    assert _check(m, old)['evidence']['frames_checked'] == 0, 'older telemetry without head2d is not judged'


def test_two_shot_checks_every_cast_member():
    shots = [_shot('s0', 0.0, 1.0, 'two_shot', 'medium')]
    m = C.compile_plan(_plan(CAST3, shots, POS3), fps=30, width=540, height=960)
    tele = _tele(m, 0.1)
    for f in tele:
        tele[f]['characters']['dot']['head2d'][1] = -0.05
    r = _check(m, tele)
    assert r['status'] == 'fail' and r['evidence']['worst']['character'] == 'dot'
    assert r['evidence']['frames_checked'] == 3 * m['duration_frames']


def test_telemetry_writer_reports_the_head_group_box():
    assert "rec['head2d']" in SCENE and "('hair', 'hat', 'eyewear')" in SCENE
