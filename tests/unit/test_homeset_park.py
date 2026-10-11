"""The 'park' set: registered everywhere, deterministic, clear of the action and the cameras, scenery in every
direction, the plaza flush under the feet with no z-fighting layers, and the skit's cameras never blocked."""
import copy
import itertools
import json
import math

import numpy as np
import pytest

from blox import demo, prefs as PR
from blox.animation import sets as SETS, solver as SV
from blox.manifest import compile as C, schema as S, validate as V

SCALES = {'bloxy': 1.0, 'pip': 0.9, 'dot': 0.9}


def _manifest(tod='noon'):
    plan = demo.park_plan()
    plan['setting']['time_of_day'] = tod
    return C.compile_plan(plan, fps=30, width=540, height=960)


def _layout(tod='noon'):
    m = _manifest(tod)
    return m, SETS.layout(m, SCALES)


def test_park_is_registered_everywhere_a_preset_must_be():
    assert 'park' in S.SETTING_PRESETS and 'park' in V.GROUND_PRESETS
    assert 'park' in SETS.PRESETS and 'park' in SETS.GROUND_HEX
    sky, lights = SETS.sky_and_lights('park', 'night', 'natural')
    assert sky['stars'] and sky['haze']['max'] > 0      # outdoors: stars at night, aerial haze by day
    p = PR.DEFAULTS
    m = C.compile_plan(demo.park_plan(), fps=30, width=540, height=960, **C.pace_kwargs(p))
    rows = {c['id']: {'bible': c['bible']} for c in demo.CHARACTERS}
    rep = V.validate(m, p, characters=rows)
    assert not rep['errors'], rep['errors'][:3]


@pytest.mark.parametrize('tod', ['noon', 'night'])
def test_layout_is_deterministic_and_json_safe(tod):
    m = _manifest(tod)
    a = SETS.layout(m, SCALES)
    b = SETS.layout(copy.deepcopy(m), dict(SCALES))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert json.loads(json.dumps(a)) == a
    assert a['preset'] == 'park' and len(a['pieces']) >= 150
    kinds = {p['kind'] for p in a['pieces']}
    assert {'plaza', 'path', 'fountain', 'bench', 'planter', 'bin', 'lamp', 'tree', 'bush', 'flowerbed', 'pond',
            'playground', 'sandbox', 'fence', 'house', 'hill', 'tower'} <= kinds, kinds
    assert len(a['templates']) < len(a['pieces'])
    if tod == 'night':
        assert sum(L['type'] == 'POINT' for L in a['lights']) == 4     # the plaza's four lamp posts
        assert 'emit' in {part[11] for t in a['templates'].values() for part in t['parts']}
    else:
        assert 'cloud' in kinds


def test_scenery_keeps_out_of_the_action_and_the_camera_reach():
    m, lay = _layout()
    area = lay['area']
    core = area['core']
    core_poly = SETS.rect(core[0] - SETS.CORE_PAD, core[1] - SETS.CORE_PAD, core[2] + SETS.CORE_PAD,
                          core[3] + SETS.CORE_PAD)
    clear_poly = SETS.rect(*area['clear'])
    band = area['band']
    for p in lay['pieces']:
        t = lay['templates'][p['template']]
        if t['decal'] or p['size'][2] <= SETS.DECAL_TOP:
            continue
        z0, top = p['pos'][2], p['pos'][2] + p['size'][2]
        if top < band[0] or z0 > band[1]:
            continue
        poly = SETS.footprint(p['pos'][0], p['pos'][1], p['size'][0] / 2, p['size'][1] / 2, p['rot'])
        assert SETS.poly_dist(poly, core_poly) > 0, p['id']
        if top > SETS.LOW_TOP:
            assert SETS.poly_dist(poly, clear_poly) >= SETS.TALL_MARGIN - 1e-6, p['id']
        else:
            for zn in area['zones']:
                if top > zn['low_top']:
                    assert SETS.poly_dist(poly, SETS.rect(*zn['box'])) >= zn['reach'], (p['id'], zn)


def test_every_direction_has_background():
    _, lay = _layout()
    boxes = SETS.box_arrays(SETS.piece_boxes(lay, everything=True))
    core = lay['area']['core']
    o = [(core[0] + core[2]) / 2, (core[1] + core[3]) / 2, 1.5]
    for k in range(12):
        dists = []
        for da in (-15, -7.5, 0, 7.5, 15):
            for el in (-10, 0, 10, 20):
                a, e = math.radians(k * 30 + da), math.radians(el)
                dists.append(SETS.ray_distance(boxes, o, [math.cos(a) * math.cos(e), math.sin(a) * math.cos(e),
                                                          math.sin(e)], 45.0))
        hits = [d for d in dists if d < 45.0]
        assert len(hits) >= 4 and min(hits) <= 20.0, (k * 30, len(hits))


def _ground_parts(lay, max_top=0.05):
    out = []
    for p in lay['pieces']:
        t = lay['templates'][p['template']]
        if not (t['decal'] or p['size'][2] <= SETS.DECAL_TOP):
            continue
        yaw, s = math.radians(p['rot']), p['scale']
        c, si = math.cos(yaw), math.sin(yaw)
        for part in t['parts']:
            shape, x, y, z, sx, sy, sz, rx, ry, rz = part[:10]
            top = p['pos'][2] + s * (z + sz / 2)
            if shape != 'box' or rx or ry or top > max_top:
                continue
            local = SETS.footprint(x, y, sx / 2, sy / 2, rz)
            corners = [(p['pos'][0] + s * (c * u - si * v), p['pos'][1] + s * (si * u + c * v)) for u, v in local]
            out.append((np.array(corners), round(top, 4), part[10]))
    return out


def _covers(corners, pt):
    sign = 0
    for i in range(4):
        (x0, y0), (x1, y1) = corners[i], corners[(i + 1) % 4]
        cr = (x1 - x0) * (pt[1] - y0) - (y1 - y0) * (pt[0] - x0)
        if abs(cr) < 1e-12:
            continue
        if sign == 0:
            sign = 1 if cr > 0 else -1
        elif (cr > 0) != (sign > 0):
            return False
    return True


def test_plaza_paving_is_flush_with_planted_feet_and_never_z_fights():
    _, lay = _layout()
    parts = _ground_parts(lay)
    core = lay['area']['core']
    xs = np.arange(core[0], core[2] + 1e-6, 0.3)
    ys = np.arange(core[1], core[3] + 1e-6, 0.3)
    for x, y in itertools.product(xs, ys):
        spot = [(x + dx, y + dy) for dx, dy in ((0, 0), (-0.1, -0.1), (0.1, -0.1), (-0.1, 0.1), (0.1, 0.1))]
        tops = [max([t for cn, t, _ in parts if _covers(cn, q)], default=None) for q in spot]
        assert all(t is not None for t in tops), (x, y)
        assert -0.0025 <= max(tops) <= SETS.WALK_TOP_MAX, (x, y, tops)
    lo = np.array([cn.min(axis=0) for cn, _, _ in parts])
    hi = np.array([cn.max(axis=0) for cn, _, _ in parts])
    tops = np.array([t for _, t, _ in parts])
    for a in range(len(parts)):
        cand = np.nonzero((np.abs(tops[a + 1:] - tops[a]) < 0.001) &
                          np.all(lo[a + 1:] < hi[a] - 0.002, axis=1) & np.all(hi[a + 1:] > lo[a] + 0.002, axis=1))[0]
        for b in cand + a + 1:
            if parts[a][2] == parts[b][2]:
                continue
            pa = [tuple(v) for v in parts[a][0] + (parts[a][0].mean(axis=0) - parts[a][0]) * 0.002]
            pb = [tuple(v) for v in parts[b][0] + (parts[b][0].mean(axis=0) - parts[b][0]) * 0.002]
            assert not SETS._overlap(pa, pb), (tops[a], parts[a][2], parts[b][2])


def test_skit_cameras_never_inside_or_blocked_by_park_scenery():
    plan = demo.park_plan()
    m = C.compile_plan(plan, fps=30, width=540, height=960)
    bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id'])
              for c in m['cast']}
    chars = {c['id']: SV.CharacterSolver(m, c['id'], bibles.get(c['id']), m['duration_frames']) for c in m['cast']}
    lay = SETS.layout(m, {cid: c.scale for cid, c in chars.items()})
    cams = SV.solve_camera(m, chars, m['duration_frames'], set_layout=lay)
    boxes = SETS.box_arrays(SETS.piece_boxes(lay) + SETS.prop_boxes(m))
    zones = {z['shot']: z for z in lay['area']['zones']}
    for s in m['shots']:
        frames = cams[s['start_frame']:s['end_frame']]
        assert not SETS.inside(boxes, np.array([fr['location'] for fr in frames])).any(), s['id']
        assert SV._scenery_blocked_frames(m, frames, chars, s['camera']['subject'], s['start_frame'], boxes) == []
        zn = zones[s['id']]
        for fr in frames:
            assert SETS.poly_dist([tuple(fr['location'][:2])] * 4, SETS.rect(*zn['box'])) <= zn['reach'] + 1e-6
