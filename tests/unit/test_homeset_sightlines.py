"""In the home set the cameras of a three-character skit never start inside scenery or look through it, the
clearance bounds every solved camera, and the scenery QA passes on the solved result."""
import copy

import numpy as np
import pytest

from blox import demo
from blox.animation import sets as SETS, solver as SV
from blox.manifest import compile as C
from blox.qa import motion
from blox.qa.report import Checks

TODS = ['noon', 'night']


def _setup(tod='sunset', cast=None):
    plan = demo.home_plan()
    plan['setting']['time_of_day'] = tod
    if cast:
        plan['cast'] = [c for c in plan['cast'] if c['id'] in cast]
        plan['performance'] = {k: v for k, v in plan['performance'].items() if k in cast}
        plan['actions'] = [a for a in plan['actions'] if a['character'] in cast]
        plan['lines'] = [ln for ln in plan['lines'] if ln['speaker'] in cast]
        plan['shots'] = [s for s in plan['shots'] if s['camera']['subject'] in cast or s['camera']['subject'] == 'two_shot']
    m = C.compile_plan(plan, fps=30, width=540, height=960)
    bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id'])
              for c in m['cast']}
    chars = {c['id']: SV.CharacterSolver(m, c['id'], bibles.get(c['id']), m['duration_frames']) for c in m['cast']}
    tops = {cid: getattr(c, 'visual_top', None) for cid, c in chars.items()}  # as solve() passes them
    lay = SETS.layout(m, {cid: c.scale for cid, c in chars.items()}, tops)
    return m, bibles, chars, lay


@pytest.mark.parametrize('tod', TODS)
def test_no_camera_inside_or_blocked_by_scenery(tod):
    m, _, chars, lay = _setup(tod)
    assert lay['preset'] == 'home' and len(chars) == 3
    cams = SV.solve_camera(m, chars, m['duration_frames'], set_layout=lay)
    everything = SETS.box_arrays(SETS.piece_boxes(lay) + SETS.prop_boxes(m))
    assert everything[0].shape[0] >= 40   # walls, furniture, stairs, kitchen... all count as occluders
    for s in m['shots']:
        frames = cams[s['start_frame']:s['end_frame']]
        locs = np.array([fr['location'] for fr in frames])
        assert not SETS.inside(everything, locs).any(), s['id']
        blocked = SV._scenery_blocked_frames(m, frames, chars, s['camera']['subject'], s['start_frame'], everything)
        assert blocked == [], (s['id'], blocked[:5])


def test_camera_zones_bound_where_the_solver_puts_the_camera():
    m, _, chars, lay = _setup()
    cams = SV.solve_camera(m, chars, m['duration_frames'], set_layout=lay)
    zones = {z['shot']: z for z in lay['area']['zones']}
    for s in m['shots']:
        zn = zones[s['id']]
        box = SETS.rect(*zn['box'])
        for fr in cams[s['start_frame']:s['end_frame']]:
            x, y, z = fr['location']
            assert SETS.poly_dist([(x, y)] * 4, box) <= zn['reach'] + 1e-6, s['id']
            assert zn['z'][0] - 1e-6 <= z <= zn['z'][1] + 1e-6, (s['id'], z, zn['z'])
    # Every camera stays inside the living room walls, never in the hall or the kitchen.
    walls = [p for p in lay['pieces'] if p['kind'] == 'wall']
    x_w = min(p['pos'][0] for p in walls)
    for fr in cams:
        assert fr['location'][0] > x_w + SETS.WALL_T


def test_low_furniture_near_the_action_stays_under_every_camera():
    """The sofa and armchair may stand inside camera zones only because every camera there looks over them."""
    m, _, chars, lay = _setup()
    cams = SV.solve_camera(m, chars, m['duration_frames'], set_layout=lay)
    low = [p for p in lay['pieces'] if p['kind'] in ('sofa', 'armchair', 'dining', 'end_table', 'beanbag', 'toy_box')]
    assert {p['kind'] for p in low} >= {'sofa', 'armchair', 'dining'}
    for p in low:
        top = p['pos'][2] + p['size'][2]
        assert top <= SETS.LOW_TOP + 1e-6, (p['kind'], top)
        poly = SETS.footprint(p['pos'][0], p['pos'][1], p['size'][0] / 2, p['size'][1] / 2, p['rot'])
        for zn in lay['area']['zones']:
            if top > zn['low_top'] and SETS.poly_dist(poly, SETS.rect(*zn['box'])) < zn['reach']:
                pytest.fail(f'{p["kind"]} stands inside zone {zn["shot"]} whose cameras may dip below it')
    # And indeed no solved camera ever comes down to the sofa's top while above its footprint.
    sofa = next(p for p in low if p['kind'] == 'sofa')
    box = SETS.box_arrays([{'c': [sofa['pos'][0], sofa['pos'][1], 0.0], 'h': [sofa['size'][0] / 2 + 0.3,
                                                                              sofa['size'][1] / 2 + 0.3, 50.0],
                            'yaw': sofa['rot']}])
    above = SETS.inside(box, np.array([fr['location'] for fr in cams]))[:, 0]
    heights = np.array([fr['location'][2] for fr in cams])[above]
    assert heights.size == 0 or heights.min() > sofa['size'][2] + SETS.LOW_CAM_GAP - 1e-6


def test_scenery_qa_passes_on_the_solved_skit():
    m, bibles, chars, lay = _setup()
    solved = SV.solve(m, bibles)
    assert solved['set'] == lay
    tele = {}
    for f in range(m['duration_frames']):
        tele[f] = {'camera': {'location': list(solved['camera'][f]['location'])},
                   'characters': {cid: {'root': [float(v) for v in c.root_xy[f]] + [0.0],
                                        'face': [float(v) for v in SV.view_targets(m, chars, cid, f)[0]]}
                                  for cid, c in chars.items()},
                   'props': {}}
    ck = Checks(m['duration_frames'])
    motion._sightlines(ck, m, tele, solved)
    scenery = [c for c in ck.items if c['id'].startswith('scenery:')]
    assert len(scenery) == len(m['shots'])
    for c in scenery:
        assert c['status'] == 'pass', (c['id'], c['evidence'])
        assert c['evidence']['frames_camera_inside'] == 0 and c['evidence']['frames_face_hidden'] == 0


def test_a_wall_dropped_across_the_sightline_is_caught_and_avoided():
    """The same boxes QA checks are the ones the solver avoids: a test wall across shot s4 makes QA fail on
    the old camera, and the solver's camera, given that layout, swings clear of it."""
    m, bibles, chars, lay = _setup()
    base = SV.solve_camera(m, chars, m['duration_frames'], set_layout=lay)
    s4 = next(s for s in m['shots'] if s['id'] == 's4')
    f = s4['start_frame'] + 10
    cam = np.array(base[f]['location'])
    face = SV.view_targets(m, chars, 'bloxy', f)[0]
    mid = (cam + face) / 2
    yaw = np.degrees(np.arctan2(face[1] - cam[1], face[0] - cam[0]))
    walled = copy.deepcopy(lay)
    size = [1.2, 0.2, 3.0]
    walled['templates']['testwall'] = {'parts': [SETS.P('box', 0, 0, 1.5, *size, '#888888')], 'size': size,
                                       'shadow': True, 'decal': False}
    walled['pieces'].append({'id': 'testwall', 'kind': 'wall', 'template': 'testwall',
                             'pos': [float(mid[0]), float(mid[1]), 0.0], 'rot': float(yaw - 90.0), 'scale': 1.0,
                             'size': size, 'occluder': True})
    tele = {g: {'camera': {'location': list(base[g]['location'])},
                'characters': {'bloxy': {'root': [0, 0, 0], 'face': [float(v) for v in SV.view_targets(m, chars, 'bloxy', g)[0]]}},
                'props': {}} for g in range(s4['start_frame'], s4['end_frame'])}
    ck = Checks(m['duration_frames'])
    motion._sightlines(ck, m, tele, {'scales': {}, 'set': walled})
    c = next(x for x in ck.items if x['id'] == 'scenery:s4')
    assert c['status'] == 'fail' and c['evidence']['frames_face_hidden'] > c['evidence']['tolerance_frames']
    cams = SV.solve_camera(m, chars, m['duration_frames'], set_layout=walled)
    boxes = SETS.box_arrays(SETS.piece_boxes(walled))
    frames = cams[s4['start_frame']:s4['end_frame']]
    assert frames[10].get('occlusion_avoided_deg')
    assert SV._scenery_blocked_frames(m, frames, chars, 'bloxy', s4['start_frame'], boxes) == []


def test_two_character_skit_solves_as_cleanly():
    m, _, chars, lay = _setup('morning', cast={'bloxy', 'pip'})
    assert len(chars) == 2 and len(m['shots']) >= 3
    cams = SV.solve_camera(m, chars, m['duration_frames'], set_layout=lay)
    boxes = SETS.box_arrays(SETS.piece_boxes(lay))
    for s in m['shots']:
        frames = cams[s['start_frame']:s['end_frame']]
        assert not SETS.inside(boxes, np.array([fr['location'] for fr in frames])).any(), s['id']
        assert SV._scenery_blocked_frames(m, frames, chars, s['camera']['subject'], s['start_frame'], boxes) == []
