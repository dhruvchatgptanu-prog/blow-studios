"""The 'home' set: a preset everywhere it must be, deterministic, clear of the action and the cameras, scenery
in every direction, ground flush under the feet, stairs as scenery, and time-of-day light through the windows."""
import copy
import itertools
import json
import math

import numpy as np
import pytest

from blox import demo, prefs as PR
from blox.animation import sets as SETS
from blox.manifest import compile as C, schema as S, validate as V

SCALES = {'bloxy': 1.0, 'pip': 0.9, 'dot': 0.9}
TODS = ['morning', 'noon', 'sunset', 'night']


def _manifest(tod='sunset', props=None, cast=None):
    plan = demo.home_plan()
    plan['setting']['time_of_day'] = tod
    if props is not None:
        plan['setting']['props'] = props
    if cast is not None:
        plan['cast'] = [c for c in plan['cast'] if c['id'] in cast]
        plan['performance'] = {k: v for k, v in plan['performance'].items() if k in cast}
        plan['actions'] = [a for a in plan['actions'] if a['character'] in cast]
        plan['lines'] = [ln for ln in plan['lines'] if ln['speaker'] in cast]
        plan['shots'] = [s for s in plan['shots'] if s['camera']['subject'] in cast or s['camera']['subject'] == 'two_shot']
    return C.compile_plan(plan, fps=30, width=540, height=960)


def _layout(tod='sunset', **kw):
    m = _manifest(tod, **kw)
    return m, SETS.layout(m, SCALES)


def test_home_is_registered_everywhere_a_preset_must_be():
    assert 'home' in S.SETTING_PRESETS and 'skit' in S.SETTING_PRESETS['home']
    assert 'home' in V.GROUND_PRESETS          # characters walk the ground floor anywhere in the house
    assert 'home' in SETS.PRESETS and 'home' in SETS.GROUND_HEX
    assert SETS.sky_and_lights('home', 'noon', 'natural')[0]['haze']['max'] == 0.0   # indoor: no aerial haze
    assert not SETS.sky_and_lights('home', 'night', 'natural')[0]['stars']


def test_fixture_story_validates_at_the_production_pace():
    p = PR.DEFAULTS
    m = C.compile_plan(demo.home_plan(), fps=30, width=540, height=960, **C.pace_kwargs(p))
    rows = {c['id']: {'bible': c['bible']} for c in demo.CHARACTERS}
    rep = V.validate(m, p, characters=rows)
    assert not rep['errors'], rep['errors'][:3]
    assert m['setting']['preset'] == 'home' and len(m['cast']) == 3


@pytest.mark.parametrize('tod', TODS)
def test_layout_is_deterministic_and_json_safe(tod):
    m = _manifest(tod)
    a = SETS.layout(m, SCALES)
    b = SETS.layout(copy.deepcopy(m), dict(SCALES))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert json.loads(json.dumps(a)) == a
    assert a['preset'] == 'home' and a['time_of_day'] == tod and len(a['pieces']) >= 40
    for p in a['pieces']:
        t = a['templates'][p['template']]
        for part in t['parts']:
            assert part[0] in ('box', 'wedge', 'pyr', 'cyl', 'ball') and len(part) == 13
            assert part[11] in {'matte', 'gloss', 'glow', 'emit', 'stud', 'cloud', 'lava', 'pane'}
        assert p['size'] == [round(v * p['scale'], 3) for v in t['size']]


def test_the_house_has_every_room_and_its_furniture():
    _, lay = _layout()
    kinds = {p['kind'] for p in lay['pieces']}
    living = {'wall', 'ceiling', 'planks', 'rug', 'sofa', 'armchair', 'end_table', 'dining', 'tv', 'bookcase',
              'shelf', 'plant', 'hanging_plant', 'frame', 'floor_lamp', 'side_table', 'window', 'clock', 'pendant',
              'ceiling_light'}
    hall = {'halltiles', 'front_door', 'door_mat', 'stairs', 'coat_rack', 'shoe_rack', 'threshold'}
    kitchen = {'kitchentiles', 'counter', 'cabinets', 'fridge'}
    assert living <= kinds and hall <= kinds and kitchen <= kinds, kinds
    assert sum(p['kind'] == 'window' for p in lay['pieces']) >= 3
    # Repeated furniture shares a template (linked duplicates in Blender).
    assert len(lay['templates']) < len(lay['pieces'])
    # The screen shows abstract shapes only: glowing boxes and discs, nothing else.
    tv = next(lay['templates'][p['template']] for p in lay['pieces'] if p['kind'] == 'tv')
    assert {part[0] for part in tv['parts'] if part[11] == 'glow'} <= {'box', 'cyl'}


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


def test_stairs_are_tall_scenery_beyond_the_walls_not_a_floor():
    m, lay = _layout()
    stairs = next(p for p in lay['pieces'] if p['kind'] == 'stairs')
    assert stairs['occluder'] and stairs['size'][2] > SETS.LOW_TOP
    poly = SETS.footprint(stairs['pos'][0], stairs['pos'][1], stairs['size'][0] / 2, stairs['size'][1] / 2,
                          stairs['rot'])
    assert SETS.poly_dist(poly, SETS.rect(*lay['area']['clear'])) >= 1.0
    # Every tread top is its own height (no coplanar steps) and the landing is the highest tread.
    tops = sorted(round(part[3] + part[6] / 2, 3) for part in lay['templates'][stairs['template']]['parts']
                  if part[0] == 'box' and part[5] > 0.5 and part[6] > 0.1)
    assert len(tops) == len(set(tops)) == SETS.STAIR_STEPS + 1
    assert tops[-1] == pytest.approx((SETS.STAIR_STEPS + 1) * SETS.STAIR_RISE, abs=1e-3)
    # The validator never lets a plan stand anyone on them: positions are 2-D on the ground floor.
    assert V._ground_ok(m, [stairs['pos'][0], stairs['pos'][1]])


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


def test_walkable_floor_is_flush_with_planted_feet():
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


def test_floor_layers_never_share_a_height_where_they_overlap():
    """Planks, hall and kitchen tiles, the threshold, rug and door mat: flat parts that overlap must sit at
    different heights (or be the same colour) or they would flicker."""
    _, lay = _layout()
    parts = _ground_parts(lay)
    assert len(parts) > 100
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


def test_rooms_beyond_the_living_room_stand_outside_the_clearance():
    """Hall (south) and kitchen (east) pieces sit past the living room walls, so no camera can reach them."""
    _, lay = _layout()
    c = lay['area']['clear']
    walls = [p for p in lay['pieces'] if p['kind'] == 'wall']
    assert len(walls) == 8
    for kind in ('front_door', 'stairs', 'coat_rack', 'shoe_rack', 'door_mat'):
        p = next(q for q in lay['pieces'] if q['kind'] == kind)
        assert p['pos'][1] < c[1] - 1.0, (kind, p['pos'])
    for kind in ('counter', 'fridge', 'cabinets'):
        p = next(q for q in lay['pieces'] if q['kind'] == kind)
        assert p['pos'][0] > c[2] + 1.0, (kind, p['pos'])
    # The window panes are painted views: a sky pane plus ground, far, mid and near layers at distinct depths.
    win = next(lay['templates'][p['template']] for p in lay['pieces'] if p['kind'] == 'window')
    depths = sorted({part[2] for part in win['parts'] if part[11] in ('pane', 'emit')})
    assert len(depths) >= 4 and all(b - a >= 0.004 for a, b in zip(depths, depths[1:]))


@pytest.mark.parametrize('tod', TODS)
def test_time_of_day_light_comes_through_the_windows(tod):
    _, lay = _layout(tod)
    col, energy = SETS.WINDOW_LIGHT[tod]
    windows = [p for p in lay['pieces'] if p['kind'] == 'window']
    spill = [L for L in lay['lights'] if L['type'] == 'POINT' and L['color'] == col and L['energy'] == energy]
    assert len(spill) == len(windows) >= 3
    for L in spill:
        assert min(math.hypot(L['pos'][0] - w['pos'][0], L['pos'][1] - w['pos'][1]) for w in windows) < 0.7
    panes = {part[10] for t in lay['templates'].values() for part in t['parts'] if part[11] == 'pane' and part[6] > 0.5}
    assert SETS.WINDOW_SKY[tod] in panes
    key = lay['lights'][0]
    assert key['type'] == 'SUN'
    if tod == 'night':
        assert key['energy'] < 1.0     # a lamp-lit house: the moon barely reaches in
        mats = {part[11] for t in lay['templates'].values() for part in t['parts']}
        assert 'emit' in mats and sum(L['type'] == 'POINT' for L in lay['lights']) >= len(windows) + 4
    else:
        assert key['color'] == SETS.SUN[tod][2] and key['elev'] == SETS.SUN[tod][0]
        assert sum(L['type'] == 'POINT' for L in lay['lights']) == len(windows)


def test_story_lamp_replaces_the_set_floor_lamp():
    _, lay = _layout(props=[{'id': 'lamp', 'type': 'lamp', 'position': [-3.4, -2.2, 0.0], 'color': '#FFE7B0'}])
    assert 'floor_lamp' not in {p['kind'] for p in lay['pieces']}
    for p in lay['pieces']:
        if p['kind'] in ('sofa', 'coffee_table', 'armchair'):
            assert math.hypot(p['pos'][0] + 3.4, p['pos'][1] + 2.2) > SETS.PROP_RADIUS['lamp']


def test_a_small_cast_gets_the_minimum_cosy_room():
    m, lay = _layout(cast={'bloxy', 'pip'})
    north = max((p for p in lay['pieces'] if p['kind'] == 'wall' and p['rot'] == 0.0), key=lambda p: p['size'][0])
    inner_w = north['size'][0] - 2 * SETS.WALL_T
    assert inner_w >= 9.2 - 1e-6
    assert {p['kind'] for p in lay['pieces']} >= {'sofa', 'tv', 'stairs', 'counter'}
