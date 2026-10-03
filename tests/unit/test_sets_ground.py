"""Set ground dressing: flush with the planted feet, no z-fighting layers, scattered specks where drawn."""
import itertools
import json
import math
import os

import numpy as np
import pytest

from blox import demo
from blox.animation import sets as SETS
from blox.manifest import compile as C

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BATCH = os.path.join(ROOT, 'stories', 'batch-2026-10-03-claude.json')
SCALES = {'bloxy': 1.0, 'pip': 0.9, 'hero': 1.0}
GROUNDED = ('town_street', 'night_forest', 'classroom', 'bedroom', 'studio')   # obbies stand on story platforms


def _plans():
    with open(BATCH) as f:
        return json.load(f) + [demo.plan()]


def _layout(i):
    m = C.compile_plan(_plans()[i], fps=30, width=540, height=960)
    return m, SETS.layout(m, SCALES)


def _ground_parts(lay, max_top=0.05):
    """World-space ground-level decal parts: (corners (4, 2), top z, colour)."""
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
    """Point inside a convex quad (either winding)."""
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


@pytest.mark.parametrize('i', range(len(_plans())))
def test_walkable_ground_under_the_action_is_flush_with_planted_feet(i):
    """Feet are planted at z = 0: the visible surface under any foot-sized spot of the action area must be
    within a couple of millimetres of 0 (not 8-14 mm of stacked paving or tiles that would swallow the soles,
    and not a slab sunk below the feet)."""
    m, lay = _layout(i)
    if lay['preset'] not in GROUNDED:
        pytest.skip('obby characters stand on story platforms')
    parts = _ground_parts(lay)
    core = lay['area']['core']
    xs = np.arange(core[0], core[2] + 1e-6, 0.3)
    ys = np.arange(core[1], core[3] + 1e-6, 0.3)
    worst_hi, worst_lo = -1.0, 1.0
    for x, y in itertools.product(xs, ys):
        # A sole is ~0.2 m long: the floor it rests on is the highest surface under any of these points.
        spot = [(x + dx, y + dy) for dx, dy in ((0, 0), (-0.1, -0.1), (0.1, -0.1), (-0.1, 0.1), (0.1, 0.1))]
        tops = [max([t for cn, t, _ in parts if _covers(cn, q)], default=None) for q in spot]
        assert all(t is not None for t in tops), (m['title'], x, y)
        worst_hi = max(worst_hi, max(tops))
        worst_lo = min(worst_lo, max(tops))
    assert worst_hi <= SETS.WALK_TOP_MAX, (m['title'], worst_hi)
    assert worst_lo >= -0.0025, (m['title'], worst_lo)


@pytest.mark.parametrize('i', range(len(_plans())))
def test_ground_layers_never_share_a_height_where_they_overlap(i):
    """Two flat parts with overlapping footprints at the same height z-fight (flicker) unless they are the
    same colour."""
    m, lay = _layout(i)
    parts = _ground_parts(lay)
    if not parts:
        return
    lo = np.array([cn.min(axis=0) for cn, _, _ in parts])
    hi = np.array([cn.max(axis=0) for cn, _, _ in parts])
    tops = np.array([t for _, t, _ in parts])
    for a in range(len(parts)):
        cand = np.nonzero((np.abs(tops[a + 1:] - tops[a]) < 0.001) &
                          np.all(lo[a + 1:] < hi[a] - 0.002, axis=1) & np.all(hi[a + 1:] > lo[a] + 0.002, axis=1))[0]
        for b in cand + a + 1:
            if parts[a][2] == parts[b][2]:
                continue
            # Shrink both by a millimetre so parts that only touch along an edge do not count.
            pa = [tuple(v) for v in parts[a][0] + (parts[a][0].mean(axis=0) - parts[a][0]) * 0.002]
            pb = [tuple(v) for v in parts[b][0] + (parts[b][0].mean(axis=0) - parts[b][0]) * 0.002]
            assert not SETS._overlap(pa, pb), (m['title'], tops[a], parts[a][2], parts[b][2])


@pytest.mark.parametrize('i', range(len(_plans())))
def test_scattered_specks_stay_out_of_the_camera_clearance(i):
    """Fireflies, coins and embers are decals (never occluders), so none may float inside the clearance
    box where the cameras and faces are; they must land exactly where they were drawn."""
    m, lay = _layout(i)
    c, band = lay['area']['clear'], lay['area']['band']
    for p in lay['pieces']:
        t = lay['templates'][p['template']]
        if not t['decal']:
            continue
        yaw, s = math.radians(p['rot']), p['scale']
        for part in t['parts']:
            lo, hi = SETS.part_bounds(part)
            if p['pos'][2] + s * hi[2] <= 0.05:
                continue  # ground dressing and slabs (feet stand on them)
            z0, z1 = p['pos'][2] + s * lo[2], p['pos'][2] + s * hi[2]
            if z1 < band[0] or z0 > band[1]:
                continue
            cx = p['pos'][0] + s * (math.cos(yaw) * part[1] - math.sin(yaw) * part[2])
            cy = p['pos'][1] + s * (math.sin(yaw) * part[1] + math.cos(yaw) * part[2])
            r = 0.5 * s * math.hypot(hi[0] - lo[0], hi[1] - lo[1])
            inside = c[0] - r < cx < c[2] + r and c[1] - r < cy < c[3] + r
            assert not inside, (m['title'], p['kind'], round(cx, 2), round(cy, 2))


def test_put_drawn_keeps_parts_where_they_were_drawn():
    m, lay = _layout(0)
    dr = SETS.Dresser(m, lay['area'], __import__('random').Random(1))
    parts = [SETS.P('box', 7.0, 1.0, 2.0, 0.05, 0.05, 0.05, '#FFFFFF', 'emit'),
             SETS.P('box', 9.0, -3.0, 0.5, 0.05, 0.05, 0.05, '#FFFFFF', 'emit')]
    dr.put_drawn('specks', parts, 10.0, 20.0, 0.0, decal=True, shadow=False)
    p = dr.pieces[-1]
    t = dr.templates[p['template']]
    world = sorted((round(p['pos'][0] + q[1], 3), round(p['pos'][1] + q[2], 3), round(p['pos'][2] + q[3], 3))
                   for q in t['parts'])
    assert world == [(17.0, 21.0, 2.0), (19.0, 17.0, 0.5)]
