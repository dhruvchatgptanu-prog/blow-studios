"""Set layouts: deterministic, JSON-safe, clear of the action and the cameras, and busy all around."""
import copy
import json
import math
import os

import pytest

from blox import demo
from blox.animation import sets as SETS
from blox.manifest import compile as C, schema as S

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BATCH = os.path.join(ROOT, 'stories', 'batch-2026-10-03-claude.json')
SCALES = {'bloxy': 1.0, 'pip': 0.9, 'hero': 1.0}


def _plans():
    with open(BATCH) as f:
        return json.load(f) + [demo.plan()]


def _manifest(i):
    return C.compile_plan(_plans()[i], fps=30, width=540, height=960)


ALL = range(len(_plans()))


def test_every_preset_has_a_story_to_test_with():
    assert {p['setting']['preset'] for p in _plans()} == set(S.SETTING_PRESETS)


@pytest.mark.parametrize('i', ALL)
def test_layout_is_deterministic_and_json_safe(i):
    m = _manifest(i)
    a = SETS.layout(m, SCALES)
    b = SETS.layout(copy.deepcopy(m), dict(SCALES))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert json.loads(json.dumps(a)) == a
    assert a['preset'] == m['setting']['preset'] and len(a['pieces']) >= 25
    for p in a['pieces']:
        t = a['templates'][p['template']]
        assert t['parts'], p
        for part in t['parts']:
            assert part[0] in ('box', 'wedge', 'pyr', 'cyl', 'ball') and len(part) == 13
            assert part[11] in SET_MATERIALS
            assert part[10].startswith('#') and len(part[10]) == 7
        assert p['size'] == [round(v * p['scale'], 3) for v in t['size']]


SET_MATERIALS = {'matte', 'gloss', 'glow', 'emit', 'stud', 'cloud', 'lava', 'pane'}


def test_variation_comes_from_the_title_seed_only():
    plan = next(p for p in _plans() if p['title'] == 'The Golden Coin Trade')
    m = C.compile_plan(plan, fps=30, width=540, height=960)
    other = copy.deepcopy(plan)
    other['title'] = 'A Different Coin'
    m2 = C.compile_plan(other, fps=30, width=540, height=960)
    a, b = SETS.layout(m, SCALES), SETS.layout(m2, SCALES)
    assert a['seed'] != b['seed']
    assert [p['template'] for p in a['pieces']] != [p['template'] for p in b['pieces']] or \
        a['templates'] != b['templates']
    # Same clearance: the layout is built around the same action and cameras.
    assert a['area'] == b['area']


@pytest.mark.parametrize('i', ALL)
def test_scenery_keeps_out_of_the_action_and_the_camera_reach(i):
    m = _manifest(i)
    lay = SETS.layout(m, SCALES)
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
            continue  # entirely below or above every camera and face (cloud seas, ceilings)
        poly = SETS.footprint(p['pos'][0], p['pos'][1], p['size'][0] / 2, p['size'][1] / 2, p['rot'])
        assert SETS.poly_dist(poly, core_poly) > 0, (m['title'], p['id'])
        if top > SETS.LOW_TOP:
            assert SETS.poly_dist(poly, clear_poly) >= SETS.TALL_MARGIN - 1e-6, (m['title'], p['id'])
        else:
            for zn in area['zones']:
                if top > zn['low_top']:
                    assert SETS.poly_dist(poly, SETS.rect(*zn['box'])) >= zn['reach'], (m['title'], p['id'], zn)


@pytest.mark.parametrize('i', ALL)
def test_every_direction_has_background(i):
    """From the middle of the action, every 30-degree sector shows scenery within 20 m (fan of rays at
    eye height from 10 degrees down to 20 degrees up)."""
    m = _manifest(i)
    lay = SETS.layout(m, SCALES)
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
        assert len(hits) >= 4 and min(hits) <= 20.0, (m['title'], k * 30, len(hits))


def test_presets_dress_their_own_vocabulary():
    kinds = {}
    for i in ALL:
        m = _manifest(i)
        lay = SETS.layout(m, SCALES)
        kinds.setdefault(lay['preset'], set()).update(p['kind'] for p in lay['pieces'])
    assert {'house', 'road', 'markings', 'kerb', 'pavement', 'tree', 'fence', 'lamp', 'mailbox', 'car', 'hill',
            'cloud', 'apartment'} <= kinds['town_street']
    assert {'wall', 'board', 'window', 'student_desk', 'bookcase', 'poster', 'door', 'clock'} <= kinds['classroom']
    assert {'wall', 'window', 'rug', 'poster', 'shelf', 'floor_lamp', 'wardrobe'} <= kinds['bedroom']
    assert {'tree', 'rock', 'bush', 'fireflies', 'moon'} <= kinds['night_forest']
    assert {'platform', 'tower', 'cloud', 'cloudsea'} <= kinds['sky_obby']
    assert {'platform', 'pillar', 'lava', 'killbrick', 'cliff'} <= kinds['lava_obby']
    assert kinds['studio'] == {'floor', 'cyc'}


def test_story_bed_and_desk_are_not_duplicated_in_a_bedroom():
    plan = next(p for p in _plans() if p['title'] == 'The Last Slice')
    lay = SETS.layout(C.compile_plan(plan, fps=30, width=540, height=960), SCALES)
    kinds = {p['kind'] for p in lay['pieces']}
    assert 'bed' not in kinds and 'desk' not in kinds


def test_night_sets_have_lit_windows_or_lamps_and_stars():
    plan = next(p for p in _plans() if p['title'] == 'The Scariest Campfire Story')
    lay = SETS.layout(C.compile_plan(plan, fps=30, width=540, height=960), SCALES)
    assert lay['sky']['stars'] and lay['time_of_day'] == 'night'
    mats = {part[11] for t in lay['templates'].values() for part in t['parts']}
    assert 'emit' in mats


def test_box_geometry_helpers():
    boxes = SETS.box_arrays([{'id': 'a', 'kind': 'wall', 'c': [0, 0, 1], 'h': [1, 0.1, 1], 'yaw': 0},
                             {'id': 'b', 'kind': 'wall', 'c': [5, 0, 1], 'h': [1, 0.1, 1], 'yaw': 90}])
    assert SETS.inside(boxes, [[0, 0, 1], [5, 0.9, 1], [5, 0, 3]]).tolist() == [[True, False], [False, True],
                                                                                [False, False]]
    hits = SETS.segment_hits(boxes, [[0, -2, 1], [0, -2, 2.5], [4, -2, 1]], [[0, 2, 1], [0, 2, 2.1], [6, 2, 1]])
    assert hits.tolist() == [[True, False], [False, False], [False, True]]
    assert abs(SETS.ray_distance(boxes, [0, -3, 1], [0, 1, 0], 10) - 2.9) < 1e-6
    assert SETS.ray_distance(boxes, [0, -3, 3], [0, 1, 0], 10) == float('inf')
    assert SETS.poly_dist(SETS.rect(0, 0, 1, 1), SETS.rect(2, 0, 3, 1)) == pytest.approx(1.0)
    assert SETS.poly_dist(SETS.footprint(0.5, 0.5, 1, 0.1, 45), SETS.rect(0, 0, 1, 1)) == 0.0
