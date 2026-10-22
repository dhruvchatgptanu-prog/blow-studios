"""Set dressing: a deterministic, Roblox-style 360-degree environment for every setting preset.

Host side and plain Python (no Blender). The layout is computed once from the manifest by the solver,
stored in the solved result, built by ``blender_scene.py`` and used as occluders by the camera solver and
by QA, so what is rendered, what the camera avoids and what QA checks are the same boxes.

How the space is kept clear for the cameras:

* ACTION AREA (``core``): bounding box of every character key position, locomotion target and nearby
  story prop (with the prop's footprint), padded by a body radius.
* CAMERA ZONES: for each shot, the box of the subject's positions widened by the furthest the solver
  can put the camera (framing, lens, truck moves and one framing step wider for QA repairs), in every
  direction, because the solver may swing the camera around the subject to avoid occlusion. Each zone
  also records the lowest camera height of the shot.
* Tall scenery (anything with a top above ``LOW_TOP``) stays outside ``clear``, the box holding every
  camera zone and the action area, so no camera starts inside or behind it. Low furniture (desks,
  fences, bushes) may stand inside a camera zone only when every camera there is well above it and the
  shot looks at a face; it never enters the action area. Flat decals (roads, rugs) go anywhere, and
  pieces entirely above or below the band of camera and face heights (cloud seas, ceilings) too.

Pieces reference templates: lists of primitive parts in the piece's local frame (origin at the bottom
centre of the template's bounding box, front facing -y like a character with facing 0). Repeated
pieces share a template and become linked duplicates in Blender, which keeps both the JSON and the
render cheap. Part format::

    [shape, x, y, z, sx, sy, sz, rx, ry, rz, '#rrggbb', material, bevel]

shape is box | wedge (gable prism, ridge along x) | pyr (square pyramid) | cyl | ball; (x, y, z) is the
part centre, (sx, sy, sz) its full size, rotations are Euler XYZ degrees. Materials are the few shared
set materials of the Blender builder (matte, gloss, glow, emit, stud, cloud, lava, pane).

This is stylised block scenery for animated shorts, not a captured game world. No brands, logos or
readable text: posters and chalkboards carry abstract shapes and scribbles only.
"""
import colorsys
import json
import math
import random
import zlib

from . import rig as R
from . import solver as SV
from ..manifest import schema as S
from ..manifest.compile import pose_at
from ..manifest.geometry import ANGLE_PITCH

VERSION = 1
LOW_TOP = 0.80       # furniture at or below this may stand inside a camera zone (faces sit above ~0.85 m)
DECAL_TOP = 0.02     # flat ground dressing (roads, rugs, markings) never occludes
CORE_PAD = 0.75      # body radius plus reach around the action area
TALL_MARGIN = 0.3    # gap between tall scenery and the camera clearance box
LOW_CAM_GAP = 0.25   # low furniture must sit this far below every camera of a zone
OCCLUDER_RANGE = 60.0  # pieces further than this from the action area are background only
SHOTS_PER_ZONE_STEP = 6
# Feet are planted at z = 0, so the walkable dressing under the action (paving, floor tiles, rugs, ground
# patches) ends flush with z = 0, a seam, marking or rug at most a few millimetres proud. Stacked layers keep
# 2 mm steps so they do not z-fight; under paved or tiled ground the big slab lies below them all (town
# pieces standing on the grass sit 4 mm above it, well under a pixel at their distance).
SLAB_TOP = -0.004
WALK_TOP_MAX = 0.004

GROUND_HEX = {'town_street': '#6DBE45', 'night_forest': '#3A6640', 'classroom': '#D9B98C',
              'bedroom': '#B98A5E', 'studio': '#D9DCE3', 'home': '#B98A5E', 'park': '#6DBE45'}

# Story props that are big, never held and so can hide a face: local boxes (cx, cy, cz, sx, sy, sz)
# matching blender_scene.build_prop at scale 1.
PROP_BOXES = {
    'house': [(0, 0, 1.95, 3.4, 3.4, 3.9)],
    'tree': [(0, 0, 1.0, 0.4, 0.4, 2.0), (0, 0, 2.6, 1.8, 1.8, 1.6)],
    'door': [(0, 0, 1.19, 1.18, 0.2, 2.38)],
    'bed': [(0, 0, 0.365, 1.2, 2.2, 0.73)],
    'desk': [(0, 0, 0.75, 1.2, 0.6, 0.06)],
    'chair': [(0, 0, 0.49, 0.5, 0.5, 0.98)],
    'crate': [(0, 0, 0.35, 0.7, 0.7, 0.7)],
    'chest': [(0, 0, 0.325, 0.82, 0.57, 0.65)],
    'lava_block': [(0, 0, -0.2, 1.0, 1.0, 0.4)],
    'rock': [(0, 0, 0.32, 0.8, 0.8, 0.65)],
}
# Footprint radius of story props (for keeping set pieces off them).
PROP_RADIUS = {'house': 1.9, 'tree': 1.0, 'bed': 1.3, 'desk': 0.7, 'door': 0.7, 'campfire': 0.6, 'crate': 0.5,
               'chest': 0.5, 'chair': 0.4, 'lamp': 0.3, 'rock': 0.5, 'sign': 0.5, 'checkpoint_flag': 0.3,
               'spring_pad': 0.5, 'lava_block': 0.7, 'button': 0.3}


# ---------------------------------------------------------------- small helpers
def _r(v, nd=3):
    return round(float(v), nd)


def _seed(m):
    st = m.get('setting') or {}
    ident = (m.get('title') or '').strip() or json.dumps(
        {k: st.get(k) for k in ('preset', 'time_of_day', 'props')}, sort_keys=True)
    return zlib.crc32(f'{st.get("preset", "sky_obby")}|{ident}'.encode())


def _hex(rgb):
    return '#' + ''.join(f'{max(0, min(255, int(round(c * 255)))):02X}' for c in rgb)


def _rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def jitter(h, rnd, dv=0.05, ds=0.05, dh=0.012):
    """Subtle colour variety around a palette colour (hue, saturation and value nudges)."""
    hh, s, v = colorsys.rgb_to_hsv(*_rgb(h))
    hh = (hh + rnd.uniform(-dh, dh)) % 1.0
    s = max(0.0, min(1.0, s * (1 + rnd.uniform(-ds, ds))))
    v = max(0.0, min(1.0, v * (1 + rnd.uniform(-dv, dv))))
    return _hex(colorsys.hsv_to_rgb(hh, s, v))


def mix(a, b, t):
    ca, cb = _rgb(a), _rgb(b)
    return _hex(tuple(x + (y - x) * t for x, y in zip(ca, cb)))


def shade(h, k):
    """k < 1 darkens, k > 1 lightens toward white."""
    return mix(h, '#000000', 1 - k) if k < 1 else mix(h, '#FFFFFF', k - 1)


def _rot(rx, ry, rz):
    """Euler XYZ (degrees) -> 3x3 matrix, Blender convention R = Rz * Ry * Rx."""
    a, b, c = (math.radians(v) for v in (rx, ry, rz))
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    return ((cc * cb, cc * sb * sa - sc * ca, cc * sb * ca + sc * sa),
            (sc * cb, sc * sb * sa + cc * ca, sc * sb * ca - cc * sa),
            (-sb, cb * sa, cb * ca))


def P(shape, x, y, z, sx, sy, sz, col, mat='matte', bevel=0.0, rx=0.0, ry=0.0, rz=0.0):
    """One primitive part (see the module docstring for the format)."""
    return [shape, _r(x), _r(y), _r(z), _r(sx), _r(sy), _r(sz), _r(rx, 1), _r(ry, 1), _r(rz, 1), col, mat,
            _r(bevel)]


def part_bounds(p):
    _, x, y, z, sx, sy, sz, rx, ry, rz = p[:10]
    M = _rot(rx, ry, rz)
    lo, hi = [1e9] * 3, [-1e9] * 3
    for dx in (-sx / 2, sx / 2):
        for dy in (-sy / 2, sy / 2):
            for dz in (-sz / 2, sz / 2):
                w = [M[i][0] * dx + M[i][1] * dy + M[i][2] * dz + (x, y, z)[i] for i in range(3)]
                lo = [min(a, b) for a, b in zip(lo, w)]
                hi = [max(a, b) for a, b in zip(hi, w)]
    return lo, hi


# ---------------------------------------------------------------- 2-D footprint geometry
def footprint(x, y, hx, hy, yaw):
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return [(x + c * px - s * py, y + s * px + c * py) for px, py in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]


def _proj(poly, ax):
    vals = [p[0] * ax[0] + p[1] * ax[1] for p in poly]
    return min(vals), max(vals)


def _overlap(pa, pb):
    """Separating-axis test for two convex quads."""
    for poly in (pa, pb):
        for i in range(len(poly)):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % len(poly)]
            ax = (y0 - y1, x1 - x0)
            a0, a1 = _proj(pa, ax)
            b0, b1 = _proj(pb, ax)
            if a1 < b0 or b1 < a0:
                return False
    return True


def _pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def poly_dist(pa, pb):
    """Distance between two convex polygons (0 when they overlap)."""
    if _overlap(pa, pb):
        return 0.0
    best = 1e9
    for P_, Q in ((pa, pb), (pb, pa)):
        for p in P_:
            for i in range(len(Q)):
                best = min(best, _pt_seg(p, Q[i], Q[(i + 1) % len(Q)]))
    return best


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ---------------------------------------------------------------- the action area and camera zones
def _char_points(m, cid, f0, f1):
    tr = (m.get('tracks') or {}).get('characters', {}).get(cid)
    if not tr:
        return []
    keys = tr['keys']
    pts = [pose_at(keys, f)['position'] for f in range(f0, max(f0 + 1, f1), SHOTS_PER_ZONE_STEP)]
    pts.append(pose_at(keys, max(f0, f1 - 1))['position'])
    for a in m['tracks'].get('actions', []):
        if a['character'] == cid and a['start_frame'] < f1 and a['end_frame'] > f0:
            for k in ('from', 'to'):
                if a['params'].get(k) is not None:
                    pts.append(a['params'][k])
    return [(float(p[0]), float(p[1])) for p in pts]


def _box_of(pts, pad=0.0):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]


def _union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def action_area(m):
    """Box of all character positions (keys, locomotion targets) and nearby props with their footprints."""
    D = int(m.get('duration_frames') or 1)
    pts = []
    for c in m.get('cast', []):
        pts += _char_points(m, c['id'], 0, D)
    if not pts:
        pts = [(0.0, 0.0)]
    chars = _box_of(pts)
    box = list(chars)
    for p in (m.get('setting') or {}).get('props', []):
        x, y = p['position'][0], p['position'][1]
        # A prop far from everyone is part of the background, not of the action.
        if poly_dist([(x, y)] * 4, rect(*chars)) > 6.0:
            continue
        r = _prop_radius(p)
        box = _union(box, [x - r, y - r, x + r, y + r])
    return box


def _prop_radius(p):
    sc = float(p.get('scale', 1) or 1)
    if p.get('type') == 'platform':
        sx, sy = (p.get('size') or [2.0, 2.0])[:2]
        return 0.5 * math.hypot(sx, sy) * sc
    return PROP_RADIUS.get(p.get('type'), 0.3) * sc


def camera_zones(m, scales=None, tops=None):
    """Per shot: the subject's box, how far the camera can be from it (any azimuth) and its height range.

    Mirrors solver.solve_camera's framing maths (distance from framing and lens, aim height, pitch,
    crane, truck and follow moves) and also covers the one-step-wider framing a QA repair may ask for.
    scales / tops: cast id -> scale / visual top (rig.visual_top, metres); a missing top is a bare head.
    """
    scales = scales or {}
    tops = tops or {}
    aspect = m['height'] / m['width']
    sensor_v = SV.SENSOR_W * aspect if aspect > 1 else SV.SENSOR_W
    chars = {c['id']: float(scales.get(c['id'], 1.0)) for c in m.get('cast', [])}
    char_tops = {cid: float(tops.get(cid) or R.visual_top(None, sc)) for cid, sc in chars.items()}
    props = {p['id']: p for p in (m.get('setting') or {}).get('props', [])}
    out = []
    for shot in m.get('shots', []):
        subj = shot['camera']['subject']
        f0, f1 = shot['start_frame'], max(shot['start_frame'] + 1, shot['end_frame'])
        sc = chars.get(subj, 1.0)
        face_target = subj in chars or subj == 'two_shot'
        if subj in chars:
            pts = _char_points(m, subj, f0, f1)
            top = char_tops[subj]
        elif subj == 'two_shot':
            per = {cid: _char_points(m, cid, f0, f1) for cid in chars}
            pts = [p for v in per.values() for p in v]
            spread = 0.0
            for f in range(f0, f1, SHOTS_PER_ZONE_STEP):
                now = [pose_at(m['tracks']['characters'][cid]['keys'], f)['position'] for cid in chars]
                spread = max([spread] + [math.dist(a, b) for a in now for b in now])
            top = max(char_tops.values(), default=R.HEIGHT) + spread * 1.1
        elif isinstance(subj, str) and subj.startswith('prop:') and subj[5:] in props:
            p = props[subj[5:]]
            pts = [(p['position'][0], p['position'][1])]
            top = 0.9 * float(p.get('scale', 1) or 1) - SV.FRAME_PAD
        else:
            pts, top = [(0.0, 0.0)], R.HEIGHT
        pts = pts or [(0.0, 0.0)]
        reach, z_lo, z_hi = 0.0, 1e9, -1e9
        for variant in (shot, SV.apply_shot_repairs(shot, {'camera_wider': True})):
            cam = variant['camera']
            lens = SV.shot_lens(variant)
            vfov = 2 * math.atan(sensor_v / (2 * lens))
            p0 = ANGLE_PITCH.get(cam['angle'], 0.0)
            pitches = [p0] + ([p0 + 14] if cam['move'] == 'crane_up' else []) + \
                      ([p0 - 14] if cam['move'] == 'crane_down' else [])
            whole_head = SV.whole_head_shot(variant)
            top_v = min(top, R.visual_top(None, sc)) if subj in chars and not whole_head else top
            height = SV.framed_height(top_v, sc)
            for fr_name in (cam['framing_start'], cam['framing_end']):
                visible, aim = SV.compose(S.FRAMING_HEIGHT.get(fr_name, 0.72) * height, top_v, sc,
                                          face=subj in chars, whole_head=whole_head)
                dist = (visible / 2) / math.tan(vfov / 2)
                if subj in chars and visible < height:
                    dist += R.HEAD_HALF_DEPTH * sc
                reach = max(reach, dist)
                for pt in pitches:
                    z = aim + dist * math.sin(math.radians(pt))
                    z_lo, z_hi = min(z_lo, z), max(z_hi, z)
        if shot['camera']['move'] in ('truck_left', 'truck_right'):
            reach += 0.9
        if shot['camera']['move'] == 'follow':
            # The camera rises with jumps, and its height spring can undershoot a little on landing.
            z_hi += 1.2
            z_lo -= 0.15
        reach += 0.35  # near clip, rounding and the follow spring lag
        low_top = min(LOW_TOP, z_lo - LOW_CAM_GAP) if face_target else -1.0
        out.append({'shot': shot['id'], 'box': [_r(v) for v in _box_of(pts)], 'reach': _r(reach),
                    'z': [_r(z_lo - 0.01), _r(z_hi + 0.01)], 'low_top': _r(low_top)})
    return out


def clearance(m, scales=None, tops=None):
    core = action_area(m)
    zones = camera_zones(m, scales, tops)
    clear = [core[0] - CORE_PAD, core[1] - CORE_PAD, core[2] + CORE_PAD, core[3] + CORE_PAD]
    for z in zones:
        b, r = z['box'], z['reach']
        clear = _union(clear, [b[0] - r, b[1] - r, b[2] + r, b[3] + r])
    zl = min([z['z'][0] for z in zones] + [0.4])
    zh = max([z['z'][1] for z in zones] + [3.0])
    return {'core': [_r(v) for v in core], 'clear': [_r(v) for v in clear], 'zones': zones,
            'band': [_r(zl - 0.3), _r(zh + 0.3)]}


# ---------------------------------------------------------------- the dresser: templates + checked placement
class Dresser:
    def __init__(self, m, area, rnd):
        self.m = m
        self.area = area
        self.rnd = rnd
        self.templates = {}
        self._sig = {}
        self.pieces = []
        self._solid = []   # (cx, cy, radius, zlo, zhi, poly)
        self.counts = {}
        core = area['core']
        self.core_poly = rect(core[0] - CORE_PAD, core[1] - CORE_PAD, core[2] + CORE_PAD, core[3] + CORE_PAD)
        self.clear_poly = rect(*area['clear'])
        self.props = [(p['position'][0], p['position'][1], _prop_radius(p) + 0.25)
                      for p in (m.get('setting') or {}).get('props', [])]
        c = area['clear']
        self.cx, self.cy = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2

    @staticmethod
    def _bounds(parts):
        lo, hi = [1e9] * 3, [-1e9] * 3
        for p in parts:
            a, b = part_bounds(p)
            lo = [min(u, v) for u, v in zip(lo, a)]
            hi = [max(u, v) for u, v in zip(hi, b)]
        return lo, hi

    def tpl(self, kind, parts, shadow=True, decal=False):
        """Register a template (parts normalised to a bottom-centre origin); identical ones are shared."""
        lo, hi = self._bounds(parts)
        ox, oy, oz = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]
        norm = [[p[0], _r(p[1] - ox), _r(p[2] - oy), _r(p[3] - oz)] + p[4:] for p in parts]
        size = [_r(hi[0] - lo[0]), _r(hi[1] - lo[1]), _r(hi[2] - lo[2])]
        body = {'parts': norm, 'size': size, 'shadow': bool(shadow), 'decal': bool(decal)}
        sig = json.dumps(body, sort_keys=True)
        if sig in self._sig:
            return self._sig[sig]
        n = self.counts.get(kind, 0)
        self.counts[kind] = n + 1
        key = f'{kind}{n}'
        self.templates[key] = body
        self._sig[sig] = key
        return key

    def fits(self, key, x, y, z=0.0, rot=0.0, scale=1.0, gap=0.15, solid=True):
        t = self.templates[key]
        sx, sy, sz = (v * scale for v in t['size'])
        top = z + sz
        if t['decal'] or sz <= DECAL_TOP:
            return True
        poly = footprint(x, y, sx / 2, sy / 2, rot)
        band = self.area['band']
        in_band = not (top < band[0] or z > band[1])
        if in_band:
            if top > LOW_TOP:
                if poly_dist(poly, self.clear_poly) < TALL_MARGIN:
                    return False
            else:
                if poly_dist(poly, self.core_poly) <= 0.0:
                    return False
                for zn in self.area['zones']:
                    if top > zn['low_top']:
                        b = zn['box']
                        if poly_dist(poly, rect(*b)) < zn['reach'] + 0.2:
                            return False
            for px, py, pr in self.props:
                if poly_dist(poly, [(px, py)] * 4) < pr:
                    return False
        if solid:
            rad = 0.5 * math.hypot(sx, sy)
            for cx, cy, cr, zl, zh, other in self._solid:
                if zh < z or zl > top or math.hypot(cx - x, cy - y) > rad + cr + gap:
                    continue
                if poly_dist(poly, other) < gap:
                    return False
        return True

    def put(self, kind, key, x, y, z=0.0, rot=0.0, scale=1.0, check=True, gap=0.15, solid=True):
        """Place a piece; with check, refuse spots that break the clearance rules or overlap other pieces."""
        scale = _r(scale)
        if check and not self.fits(key, x, y, z, rot, scale, gap, solid):
            return False
        t = self.templates[key]
        size = [_r(v * scale) for v in t['size']]
        decal = t['decal'] or size[2] <= DECAL_TOP
        far = math.hypot(x - self.cx, y - self.cy) > OCCLUDER_RANGE
        self.pieces.append({'id': f'{kind}_{len(self.pieces)}', 'kind': kind, 'template': key,
                            'pos': [_r(x), _r(y), _r(z)], 'rot': _r(rot % 360.0, 2), 'scale': _r(scale),
                            'size': size, 'occluder': not decal and not far})
        if solid and not decal:
            poly = footprint(x, y, size[0] / 2, size[1] / 2, rot)
            self._solid.append((x, y, 0.5 * math.hypot(size[0], size[1]), z, z + size[2], poly))
        return True

    def put_drawn(self, kind, parts, x, y, z=0.0, **tpl_kw):
        """Register parts drawn relative to the point (x, y, z) and place them exactly where they were drawn.

        tpl() re-centres every template on its bounding box, so a scattered cloud (fireflies, coins,
        embers, ground patches) placed at its drawing origin would shift by the centre of its random
        spread, possibly into the camera clearance; this adds that offset back. Unchecked (callers keep
        such clouds out of the clearance box themselves).
        """
        lo, hi = self._bounds(parts)
        key = self.tpl(kind, parts, **tpl_kw)
        return self.put(kind, key, x + (lo[0] + hi[0]) / 2, y + (lo[1] + hi[1]) / 2, z + lo[2], check=False)

    def scatter(self, kind, keys, n, r0, r1, z=0.0, scale=(1.0, 1.0), tries=6, sectors=None, gap=0.3, rot=None,
                zfn=None):
        """Place up to n pieces around the clearance box, between r0 and r1 metres outside it.

        With sectors, positions are stratified by azimuth so every direction gets some.
        """
        rnd = self.rnd
        c = self.area['clear']
        hx, hy = (c[2] - c[0]) / 2, (c[3] - c[1]) / 2
        placed = 0
        for i in range(n):
            for _ in range(tries):
                if sectors:
                    a = 2 * math.pi * (i % sectors + rnd.uniform(0.1, 0.9)) / sectors
                else:
                    a = rnd.uniform(0, 2 * math.pi)
                d = rnd.uniform(r0, r1)
                # Distance measured from the box edge along the ray, so the ring hugs the clear box.
                ex = abs(math.cos(a)) / max(hx, 1e-3)
                ey = abs(math.sin(a)) / max(hy, 1e-3)
                edge = 1.0 / max(ex, ey)
                x = self.cx + math.cos(a) * (edge + d)
                y = self.cy + math.sin(a) * (edge + d)
                key = rnd.choice(keys) if isinstance(keys, list) else keys
                s = rnd.uniform(*scale)
                zz = zfn(d) if zfn else z
                if self.put(kind, key, x, y, zz, rnd.uniform(0, 360) if rot is None else rot, s, gap=gap):
                    placed += 1
                    break
        return placed


# ---------------------------------------------------------------- skies and light
SKY = {
    'noon': ['#C9DDEB', '#D8EDFF', '#8CC4F2', '#3F86E0'],
    'morning': ['#E9D3C3', '#FFE3C4', '#A9CBEB', '#5A8FD6'],
    'sunset': ['#E79C74', '#FFB46E', '#D27C9C', '#47478F'],
    'night': ['#0B1022', '#1B2547', '#0F1733', '#050915'],
}
SKY_LAVA = {
    'noon': ['#C77A5A', '#F2A27A', '#C9705A', '#5A2E3A'],
    'morning': ['#C9806A', '#F5B48A', '#B0606A', '#4A2A3E'],
    'sunset': ['#D2603A', '#FF8A4A', '#B4553E', '#3A1A22'],
    'night': ['#2A0E0A', '#5A1E14', '#2A0E14', '#0A0408'],
}
SKY_INDOOR = ['#E8E2D8', '#F2EEE6', '#E6EAF0', '#D8DFEA']
# elevation (deg), energy, colour. Night is moonlight: dim and blue but strong enough to read silhouettes.
SUN = {'noon': (62, 3.2, '#FFF7EB'), 'morning': (24, 2.8, '#FFD9B3'), 'sunset': (14, 3.0, '#FF9E61'),
       'night': (40, 1.5, '#9DB4FF')}


def sky_and_lights(preset, tod, lighting):
    tod = tod if tod in SKY else 'noon'
    stops = (SKY_LAVA if preset == 'lava_obby' else SKY)[tod]
    if preset == 'night_forest' and tod != 'night':
        stops = [mix(s, '#1B2547', 0.25) for s in stops]
    indoor = preset in ('classroom', 'bedroom', 'studio', 'home')
    if indoor:
        stops = SKY_INDOOR if tod != 'night' else ['#2A2E3E', '#3A3F55', '#2E3448', '#22273A']
    elev, energy, col = SUN[tod]
    az = 35.0
    angle = 0.12
    fill_k, fill_col = 0.28, '#8CB3FF'
    world = 1.0 if tod != 'night' else 1.3
    extra = []
    if indoor and tod == 'night':
        # A lamp-lit room: warm light from the ceiling fixture (the set adds the light itself).
        energy, col = 0.35, '#9DB7FF'
    if lighting == 'warm_key_cool_fill':
        col, fill_k, fill_col = mix(col, '#FFB070', 0.25), 0.36, '#7FA6FF'
    elif lighting == 'dramatic_rim':
        fill_k = 0.18
        extra.append({'type': 'SUN', 'elev': max(elev, 20), 'az': az + 180, 'energy': energy * 0.8,
                      'color': '#FFF1D6', 'shadow': False})
    elif lighting == 'soft_overcast':
        angle, energy, world = 0.6, energy * 0.7, world * 1.25
    elif lighting == 'spooky_low':
        elev, col, energy = 9, mix(col, '#9DB7FF', 0.5), energy * 0.6
    to_sun = [math.cos(math.radians(elev)) * math.sin(math.radians(az)),
              -math.cos(math.radians(elev)) * math.cos(math.radians(az)), math.sin(math.radians(elev))]
    sky = {'stops': stops, 'strength': world, 'stars': tod == 'night' and not indoor,
           'sun_dir': [_r(v) for v in to_sun], 'sun_glow': None if (indoor or tod == 'night') else mix(col, '#FFFFFF', 0.4),
           'haze': {'color': stops[1], 'start': 22.0, 'end': 150.0,
                    'max': 0.0 if indoor else (0.45 if tod == 'night' else 0.55)}}
    if preset == 'night_forest':
        # Woods: a closer, bluish mist so rows of trees separate into layers instead of one dark mass.
        sky['haze'] = {'color': mix(stops[1], '#8FA8D0', 0.42 if tod == 'night' else 0.15), 'start': 3.0,
                       'end': 45.0, 'max': 0.6 if tod == 'night' else 0.45}
    lights = [{'type': 'SUN', 'elev': elev, 'az': az, 'energy': energy, 'color': col, 'angle': angle, 'shadow': True},
              {'type': 'SUN', 'elev': 60, 'az': az - 185, 'energy': energy * fill_k, 'color': fill_col,
               'shadow': False}] + extra
    if preset == 'lava_obby':
        # Warm under-light from the lava.
        lights.append({'type': 'SUN', 'elev': -55, 'az': 0, 'energy': 1.1 if tod != 'night' else 0.9,
                       'color': '#FF7A33', 'shadow': False})
    return sky, lights


# ---------------------------------------------------------------- shared art (templates as part lists)
TRUNK = '#7A5230'
WHITE = '#F6F4EE'


def cloud_parts(rnd, col='#FFFFFF', k=1.0):
    parts = [P('box', 0, 0, 0, 6 * k, 4 * k, 1.6 * k, col, 'cloud', 0.45 * k)]
    for _ in range(rnd.randint(2, 4)):
        parts.append(P('box', rnd.uniform(-2.6, 2.6) * k, rnd.uniform(-1.0, 1.0) * k, rnd.uniform(0.2, 1.0) * k,
                       rnd.uniform(2.6, 4.2) * k, rnd.uniform(2.2, 3.2) * k, rnd.uniform(1.2, 1.9) * k, col, 'cloud',
                       0.4 * k))
    return parts


def tree_parts(style, rnd, leaf, trunk=TRUNK, k=1.0):
    l2, l3 = shade(leaf, 1.12), shade(leaf, 0.88)
    if style == 'round':
        return [P('box', 0, 0, 1.1 * k, 0.5 * k, 0.5 * k, 2.2 * k, trunk, bevel=0.05 * k),
                P('box', 0, 0, 2.9 * k, 2.8 * k, 2.8 * k, 2.0 * k, leaf, bevel=0.22 * k),
                P('box', 0.3 * k, -0.25 * k, 4.15 * k, 2.0 * k, 2.0 * k, 1.1 * k, l2, bevel=0.18 * k),
                P('box', -0.7 * k, 0.5 * k, 2.55 * k, 1.5 * k, 1.5 * k, 1.3 * k, l3, bevel=0.16 * k)]
    if style == 'pine':
        parts = [P('box', 0, 0, 0.7 * k, 0.45 * k, 0.45 * k, 1.4 * k, trunk, bevel=0.04 * k)]
        for i, (w, z) in enumerate([(3.0, 1.9), (2.4, 3.0), (1.8, 4.0), (1.1, 4.9)]):
            parts.append(P('pyr', 0, 0, z * k, w * k, w * k, 1.6 * k, (leaf, l2, leaf, l3)[i], rz=i * 17))
        return parts
    if style == 'blockpine':
        parts = [P('box', 0, 0, 0.6 * k, 0.45 * k, 0.45 * k, 1.2 * k, trunk, bevel=0.04 * k)]
        for i, (w, z) in enumerate([(2.6, 1.6), (2.0, 2.5), (1.4, 3.35), (0.8, 4.1)]):
            parts.append(P('box', 0, 0, z * k, w * k, w * k, 0.9 * k, (leaf, l3, leaf, l2)[i], bevel=0.1 * k,
                           rz=i * 22))
        return parts
    if style == 'poplar':
        return [P('box', 0, 0, 0.8 * k, 0.4 * k, 0.4 * k, 1.6 * k, trunk, bevel=0.04 * k),
                P('box', 0, 0, 3.2 * k, 1.5 * k, 1.5 * k, 3.6 * k, leaf, bevel=0.25 * k),
                P('box', 0.1 * k, -0.1 * k, 5.2 * k, 1.0 * k, 1.0 * k, 0.8 * k, l2, bevel=0.15 * k)]
    # bush (low)
    return [P('box', 0, 0, 0.33 * k, 1.1 * k, 0.85 * k, 0.66 * k, leaf, bevel=0.14 * k),
            P('box', 0.5 * k, 0.15 * k, 0.29 * k, 0.75 * k, 0.7 * k, 0.58 * k, l2, bevel=0.12 * k),
            P('box', -0.45 * k, -0.1 * k, 0.26 * k, 0.6 * k, 0.6 * k, 0.52 * k, l3, bevel=0.1 * k)]


def rock_parts(rnd, col):
    parts = [P('box', 0, 0, 0.35, 1.1, 0.9, 0.7, col, bevel=0.12, rx=rnd.uniform(-8, 8), ry=rnd.uniform(-8, 8),
               rz=rnd.uniform(0, 90))]
    parts.append(P('box', rnd.uniform(-0.5, 0.5), rnd.uniform(-0.4, 0.4), 0.22, 0.7, 0.6, 0.44, shade(col, 1.1),
                   bevel=0.08, rz=rnd.uniform(0, 90)))
    return parts


def hill_parts(rnd, cols, w, d, h):
    parts = []
    z = 0.0
    for i, (kw, kd, kh) in enumerate([(1.0, 1.0, 0.45), (0.68, 0.62, 0.35), (0.38, 0.32, 0.25)]):
        hh = h * kh
        parts.append(P('box', rnd.uniform(-0.06, 0.06) * w * i, rnd.uniform(-0.06, 0.06) * d * i, z + hh / 2,
                       w * kw, d * kd, hh, cols[i % len(cols)], bevel=min(1.2, hh * 0.3)))
        z += hh
    return parts


def platform_parts(w, d, col, h=0.8, under=None):
    parts = [P('box', 0, 0, 0, w, d, h, col, 'stud', 0.07)]
    if under:
        # A rocky underside makes it a floating island.
        parts.append(P('pyr', 0, 0, -h / 2 - 0.9, w * 0.8, d * 0.8, 1.8, under, rx=180))
    return parts


def flag_parts(col, pole=2.2):
    return [P('box', 0, 0, 0.06, 0.6, 0.6, 0.12, '#5A5F69', bevel=0.03),
            P('cyl', 0, 0, pole / 2, 0.08, 0.08, pole, '#E6E6E6', 'gloss'),
            P('box', 0.38, 0, pole - 0.3, 0.7, 0.04, 0.45, col, bevel=0.01)]


# ---------------------------------------------------------------- town street
HOUSE_WALLS = ['#F4E3C3', '#F7C59F', '#A8D5E2', '#F9F3E6', '#C8E6A0', '#F2B5B5', '#D7C4F0', '#FFE08A', '#B5D8F7',
               '#E8D5B5']
ROOFS = ['#B5523B', '#8C4A3B', '#4F5D75', '#3E6E8E', '#7A5C99', '#A23B3B', '#5B7F4A', '#6B5B53']
DOORS = ['#8B5A2B', '#3D5A80', '#C0392B', '#2E7D5B', '#6D4C41', '#E0A030']
LEAVES = ['#3FA34D', '#2E8B57', '#57B05A', '#4C9A2A', '#5DAE3E']
CARS = ['#E74C3C', '#3498DB', '#F1C40F', '#2ECC71', '#ECF0F1', '#9B59B6', '#E67E22', '#1ABC9C']


def window_parts(x, y, z, face, trim, glass, gmat, w=0.9, h=1.0):
    """Framed window with a cross mullion on a wall plane; face is the outward direction ('-y', '+x', ...)."""
    sgn = -1 if face[0] == '-' else 1
    ax = face[1]

    def box(off, a, b, c, col, mat='matte', bev=0.01):
        # a: width along the wall, b: depth out of the wall, c: height
        if ax == 'y':
            return P('box', x, y + sgn * off, z, a, b, c, col, mat, bev)
        return P('box', x + sgn * off, y, z, b, a, c, col, mat, bev)
    return [box(0.03, w + 0.2, 0.06, h + 0.2, trim), box(0.06, w, 0.04, h, glass, gmat, 0.0),
            box(0.085, 0.07, 0.03, h, trim), box(0.085, w, 0.03, 0.07, trim),
            box(0.1, w + 0.3, 0.2, 0.08, trim, bev=0.02)]


def house_parts(rnd, night, wall=None, roof=None):
    w = rnd.choice([5.0, 5.6, 6.2, 7.0])
    d = rnd.choice([4.6, 5.2, 5.8])
    storeys = 2 if rnd.random() < 0.7 else 1
    sh = 2.7
    h = storeys * sh
    wall = wall or jitter(rnd.choice(HOUSE_WALLS), rnd)
    roof = roof or jitter(rnd.choice(ROOFS), rnd, 0.06)
    trim = rnd.choice([WHITE, '#FFFFFF', '#EFE6D8'])
    style = rnd.choices(['gable', 'hip', 'flat'], [0.6, 0.25, 0.15])[0]
    z0 = 0.3
    parts = [P('box', 0, 0, 0.15, w + 0.3, d + 0.3, 0.3, '#A3A7AF', bevel=0.03),
             P('box', 0, 0, z0 + h / 2, w, d, h, wall, bevel=0.06)]
    if storeys == 2:
        parts.append(P('box', 0, 0, z0 + sh, w + 0.08, d + 0.08, 0.14, trim, bevel=0.02))
    top = z0 + h
    rh = rnd.uniform(1.5, 2.3)
    if style == 'gable':
        parts += [P('box', 0, 0, top + 0.06, w + 0.5, d + 0.5, 0.12, trim, bevel=0.02),
                  P('wedge', 0, 0, top + 0.12 + rh / 2, w + 0.6, d + 0.7, rh, roof),
                  P('box', 0, 0, top + 0.12 + rh - 0.02, w + 0.7, 0.26, 0.14, shade(roof, 0.8), bevel=0.03)]
    elif style == 'hip':
        parts += [P('box', 0, 0, top + 0.06, w + 0.5, d + 0.5, 0.12, trim, bevel=0.02),
                  P('pyr', 0, 0, top + 0.12 + rh / 2, w + 0.6, d + 0.6, rh, roof)]
    else:
        rh = 0.4
        parts += [P('box', 0, 0, top + 0.2, w + 0.2, d + 0.2, 0.4, trim, bevel=0.04),
                  P('box', 0, 0, top + 0.36, w - 0.3, d - 0.3, 0.1, shade(roof, 0.85)),
                  P('box', w * 0.2, d * 0.15, top + 0.75, 1.0, 0.8, 0.7, '#B8BEC6', 'gloss', 0.05)]
    if style != 'flat' and rnd.random() < 0.75:
        cxh = rnd.choice([-1, 1]) * (w / 2 - 0.9)
        parts += [P('box', cxh, d / 5, top + (rh + 0.6) / 2, 0.6, 0.6, rh + 0.6, '#A0522D', bevel=0.03),
                  P('box', cxh, d / 5, top + rh + 0.65, 0.75, 0.75, 0.14, '#5E5E66', bevel=0.02)]
    # Door, step and a little awning.
    xd = rnd.choice([-w / 4, 0.0, w / 4])
    door = rnd.choice(DOORS)
    fy = -d / 2
    parts += [P('box', xd, fy - 0.02, z0 + 1.12, 1.24, 0.06, 2.3, trim, bevel=0.01),
              P('box', xd, fy - 0.05, z0 + 1.05, 1.0, 0.08, 2.1, door, bevel=0.02),
              P('box', xd + 0.33, fy - 0.11, z0 + 1.0, 0.08, 0.06, 0.08, '#E8C547', 'gloss'),
              P('box', xd, fy - 0.45, 0.12, 1.6, 0.8, 0.24, '#B9BCC4', bevel=0.03),
              P('box', xd, fy - 0.38, z0 + 2.5, 1.6, 0.76, 0.1, roof, bevel=0.02)]
    lit = night
    for k in range(storeys):
        zc = z0 + k * sh + 1.45
        n = max(1, int((w - 1.0) / 1.7))
        xs = [(-w / 2 + 0.5) + (w - 1.0) * (i + 0.5) / n for i in range(n)]
        for x in xs:
            if k == 0 and abs(x - xd) < 1.25:
                continue
            on = lit and rnd.random() < 0.75
            parts += window_parts(x, fy, zc, '-y', trim, '#FFD27A' if on else ('#2B3A55' if lit else '#A7D8F2'),
                                  'glow' if on else 'gloss')
            if k == 0 and rnd.random() < 0.35:
                fcol = rnd.choice(['#E84A5F', '#FFCE54', '#FC6E51', '#AC92EC', '#FFFFFF'])
                parts += [P('box', x, fy - 0.2, zc - 0.68, 1.0, 0.26, 0.22, '#8B5A2B', bevel=0.02),
                          P('box', x, fy - 0.2, zc - 0.52, 0.86, 0.18, 0.12, fcol, bevel=0.03)]
        for sx in (-1, 1):
            on = lit and rnd.random() < 0.6
            parts += window_parts(sx * w / 2, 0.0, zc, ('+' if sx > 0 else '-') + 'x', trim,
                                  '#FFD27A' if on else ('#2B3A55' if lit else '#A7D8F2'), 'glow' if on else 'gloss')
    if storeys == 1 and rnd.random() < 0.35:
        gx = (w / 2 + 1.45) * rnd.choice([-1, 1])
        parts += [P('box', gx, 0.2, z0 + 1.3, 2.9, d * 0.85, 2.6, shade(wall, 0.95), bevel=0.05),
                  P('box', gx, 0.2, z0 + 2.66, 3.1, d * 0.85 + 0.2, 0.14, trim, bevel=0.02),
                  P('box', gx, 0.2 - d * 0.425 - 0.04, z0 + 1.1, 2.3, 0.08, 2.2, '#E3E5E8', bevel=0.01)]
        for i in range(4):
            parts.append(P('box', gx, 0.2 - d * 0.425 - 0.09, z0 + 0.35 + i * 0.52, 2.3, 0.02, 0.04, '#B5B9C0'))
    return parts


def lamp_parts(night):
    bulb = '#FFF1C1'
    return [P('box', 0, 0, 0.15, 0.36, 0.36, 0.3, '#3B3F47', bevel=0.03),
            P('box', 0, 0, 1.9, 0.16, 0.16, 3.6, '#3B3F47', bevel=0.02),
            P('box', 0, -0.45, 3.62, 0.1, 0.9, 0.1, '#3B3F47', bevel=0.01),
            P('box', 0, -0.88, 3.54, 0.44, 0.56, 0.18, '#3B3F47', bevel=0.03),
            P('box', 0, -0.88, 3.43, 0.34, 0.44, 0.05, bulb, 'emit' if night else 'glow')]


def car_parts(col):
    glass = '#33445A'
    parts = [P('box', 0, 0, 0.64, 1.8, 3.9, 0.62, col, 'gloss', 0.13),
             P('box', 0, 0.25, 1.22, 1.56, 2.0, 0.56, glass, 'gloss', 0.1),
             P('box', 0, 0.25, 1.52, 1.62, 1.9, 0.08, col, 'gloss', 0.03),
             P('box', 0, -1.97, 0.42, 1.7, 0.1, 0.16, '#BFC3C9', 'gloss', 0.03),
             P('box', 0, 1.97, 0.42, 1.7, 0.1, 0.16, '#BFC3C9', 'gloss', 0.03)]
    for sx in (-1, 1):
        for sy in (-1, 1):
            parts.append(P('cyl', sx * 0.86, sy * 1.25, 0.36, 0.72, 0.72, 0.3, '#23252B', ry=90))
            parts.append(P('cyl', sx * 1.0, sy * 1.25, 0.36, 0.36, 0.36, 0.04, '#C9CDD3', 'gloss', ry=90))
        parts.append(P('box', sx * 0.58, -1.96, 0.74, 0.38, 0.04, 0.16, '#FFF6D5', 'glow'))
        parts.append(P('box', sx * 0.62, 1.96, 0.74, 0.32, 0.04, 0.14, '#E53935', 'glow'))
    return parts


def fence_parts(L, col):
    parts = [P('box', -L / 2 + 0.06, 0, 0.39, 0.12, 0.12, 0.78, col, bevel=0.02),
             P('box', L / 2 - 0.06, 0, 0.39, 0.12, 0.12, 0.78, col, bevel=0.02),
             P('box', 0, 0.03, 0.24, L, 0.05, 0.07, col), P('box', 0, 0.03, 0.54, L, 0.05, 0.07, col)]
    n = max(2, int(L / 0.24))
    for i in range(n):
        x = -L / 2 + 0.2 + (L - 0.4) * i / (n - 1)
        parts.append(P('box', x, -0.02, 0.36, 0.09, 0.04, 0.68, col, bevel=0.01))
    return parts


def mailbox_parts(col):
    return [P('box', 0, 0, 0.5, 0.1, 0.1, 1.0, '#6B4A2E'),
            P('box', 0, 0, 1.13, 0.34, 0.52, 0.3, col, 'gloss', 0.08),
            P('box', 0.19, 0.1, 1.24, 0.03, 0.06, 0.26, '#E53935')]


def apartment_parts(rnd, night):
    """Taller second-row block: shop front on the ground floor, flats above, parapet and a roof tank."""
    w, d = rnd.choice([8.0, 10.0, 12.0]), rnd.choice([7.0, 8.0])
    floors, fh = rnd.randint(3, 5), 2.8
    H = 0.6 + floors * fh
    wall = jitter(rnd.choice(['#E9D8C4', '#C9DCEB', '#F0C9A8', '#D9E6C6', '#E7C6D3', '#D4D0E8', '#F2E2B0']), rnd)
    trim = rnd.choice([WHITE, '#E3E0D8', shade(wall, 0.8)])
    parts = [P('box', 0, 0, H / 2, w, d, H, wall, bevel=0.08),
             P('box', 0, 0, H + 0.25, w + 0.2, d + 0.2, 0.5, trim, bevel=0.04),
             P('box', 0, 0, H + 0.42, w - 0.4, d - 0.4, 0.1, '#7D828C'),
             P('cyl', w * 0.25, d * 0.1, H + 1.3, 1.4, 1.4, 1.4, '#9AA0A8', 'gloss'),
             P('box', -w * 0.3, -d * 0.15, H + 0.8, 1.2, 0.9, 0.7, '#B8BEC6', 'gloss', 0.05)]
    fy = -d / 2
    # Shop front: glass, a coloured fascia (no lettering) and a striped awning.
    fascia = rnd.choice(['#E84A5F', '#3BAFDA', '#37BC9B', '#F6BB42', '#967ADC'])
    parts += [P('box', 0, fy - 0.04, 1.4, w - 1.0, 0.06, 1.9, '#9FD3F0' if not night else '#FFD27A',
                'gloss' if not night else 'glow'),
              P('box', 0, fy - 0.06, 2.65, w - 0.6, 0.12, 0.5, fascia, bevel=0.02),
              P('box', w * 0.3, fy - 0.07, 1.1, 1.0, 0.04, 2.1, '#6D4C41', bevel=0.02)]
    stripes = int((w - 0.6) / 0.6)
    for i in range(stripes):
        parts.append(P('box', -w / 2 + 0.6 + i * 0.6, fy - 0.6, 3.15, 0.6, 1.1, 0.08,
                       fascia if i % 2 else WHITE, rx=-18))
    for k in range(1, floors):
        z = 0.6 + k * fh + 1.3
        parts.append(P('box', 0, fy - 0.03, 0.6 + k * fh, w + 0.06, 0.08, 0.12, trim))
        n = max(2, int((w - 1.0) / 2.0))
        for i in range(n):
            x = -w / 2 + 0.5 + (w - 1.0) * (i + 0.5) / n
            on = night and rnd.random() < 0.6
            parts += [P('box', x, fy - 0.02, z, 1.2, 0.05, 1.3, trim),
                      P('box', x, fy - 0.05, z, 1.0, 0.03, 1.1, '#FFD27A' if on else ('#2B3A55' if night else '#A7D8F2'),
                        'glow' if on else 'gloss')]
            if rnd.random() < 0.2:
                parts.append(P('box', x, fy - 0.35, z - 0.65, 1.3, 0.7, 0.08, trim))  # balcony
        for sx in (-1, 1):
            on = night and rnd.random() < 0.5
            parts.append(P('box', sx * (w / 2 + 0.03), 0.0, z, 0.05, 1.0, 1.1,
                           '#FFD27A' if on else ('#2B3A55' if night else '#A7D8F2'), 'glow' if on else 'gloss'))
    return parts


def _row(dr, kind, make, start, end, front, axis, facing, rnd, gap=(3.0, 4.4), between=None):
    """A row of buildings along an axis ('x' or 'y') with their fronts on a line, facing a direction.

    facing is the yaw of the building fronts (0 faces -y, 90 faces +x ...). Returns [(centre, width)].
    between(c0, c1) dresses the gap after each building.
    """
    placed = []
    s = start + rnd.uniform(0.0, 2.0)
    out = (math.sin(math.radians(facing)), -math.cos(math.radians(facing)))   # front normal
    while s < end:
        key = dr.tpl(kind, make(), shadow=True)
        w, d, _ = dr.templates[key]['size']
        g = rnd.uniform(*gap)
        along = s + w / 2
        # Centre sits half a depth behind the front line.
        if axis == 'x':
            x, y = along, front - out[1] * d / 2
        else:
            x, y = front - out[0] * d / 2, along
        if dr.put(kind, key, x, y, 0.0, facing, gap=0.4):
            placed.append(((x, y), w))
            if between:
                between(s + w, s + w + g)
            s += w + g
        else:
            s += 1.5
    return placed


def town_street(dr, night):
    """A street that ends in a small paved square: houses close on three sides of the action, the street
    (lane markings, kerbs, pavements, lamps, parked cars) running away on the fourth, taller blocks
    behind, a cross street at the far end, then hills, a hazy skyline and clouds."""
    rnd = dr.rnd
    c = dr.area['clear']
    cx = dr.cx
    road_half, pave = 3.2, 1.8
    yard = 1.6
    xw = min(c[0] - TALL_MARGIN - 0.2, cx - road_half - pave - yard)   # west row front (faces +x)
    xe = max(c[2] + TALL_MARGIN + 0.2, cx + road_half + pave + yard)   # east row front (faces -x)
    yn = c[3] + TALL_MARGIN + 0.2                                       # north row front (faces -y)
    y_end = c[1] - 44.0                                                 # the cross street
    y_sq = c[1] + 0.2                                                   # where the road meets the square
    L = y_sq - y_end
    dr.put('ground', dr.tpl('ground', [P('box', 0, 0, -0.1, 360, 360, 0.2, GROUND_HEX['town_street'])], decal=True,
                            shadow=False), dr.cx, dr.cy, SLAB_TOP - 0.2, check=False)
    # Road along y with dashed centre line and edge lines; pavements and kerbs on both sides.
    ym = (y_sq + y_end) / 2
    dr.put('road', dr.tpl('road', [P('box', 0, 0, 0.004, 2 * road_half, L, 0.008, '#4A4E58')], decal=True,
                          shadow=False), cx, ym, -0.010, check=False)
    dash = [P('box', 0, -L / 2 + 1.5 + i * 4.0, 0.002, 0.16, 2.0, 0.004, '#F2C94C') for i in range(int(L / 4.0))]
    edges = [P('box', s * (road_half - 0.35), 0, 0.002, 0.1, L, 0.004, '#EDEBE4') for s in (-1, 1)]
    dr.put('markings', dr.tpl('markings', dash + edges, decal=True, shadow=False), cx, ym, -0.002, check=False)
    kt = dr.tpl('kerb', [P('box', 0, 0, 0.07, 0.24, 3.96, 0.14, '#A9ADB6', bevel=0.02)])
    for side in (-1, 1):
        kx = cx + side * road_half
        slabs = [P('box', 0, -L / 2 + 1.0 + i * 2.0, 0.005, pave, 1.96, 0.01, '#C9CCD3' if i % 2 else '#C2C5CC')
                 for i in range(int(L / 2.0))]
        dr.put('pavement', dr.tpl('pavement', slabs, decal=True, shadow=False), kx + side * pave / 2, ym, -0.010,
               check=False)
        for i in range(int(L / 4.0)):
            dr.put('kerb', kt, kx + side * 0.12, y_end + 2.0 + i * 4.0, 0.0, solid=False)
    # The square: light paving with a darker border.
    sq_w, sq_d = xe - xw, yn - y_sq
    tiles = [P('box', 0, 0, 0.004, sq_w, sq_d, 0.008, '#D5CFC2'),
             P('box', 0, 0, 0.006, sq_w - 1.2, sq_d - 1.2, 0.008, '#E3DDD0')]
    for i in range(int(sq_w / 1.5)):
        tiles.append(P('box', -sq_w / 2 + 0.75 + i * 1.5, 0, 0.009, 0.05, sq_d - 1.2, 0.006, '#CFC8BA'))
    # Border top 2 mm below the paving the characters stand on (flush with z = 0); seams 2 mm proud.
    dr.put('square', dr.tpl('square', tiles, decal=True, shadow=False), (xw + xe) / 2, (y_sq + yn) / 2, -0.010,
           check=False)

    tree_keys = [dr.tpl('tree', tree_parts(s, rnd, rnd.choice(LEAVES))) for s in ('round', 'round', 'poplar', 'pine')]
    bush_keys = [dr.tpl('bush', tree_parts('bush', rnd, rnd.choice(LEAVES))) for _ in range(2)]
    mail_key = dr.tpl('mailbox', mailbox_parts(rnd.choice(['#2F5DA8', '#C0392B', '#3B3F47'])))
    fence_col = rnd.choice([WHITE, '#F3EBDD'])

    def house():
        return house_parts(rnd, night)

    def gap_tree(axis, line, outward):
        def dress(a, b):
            m = (a + b) / 2
            off = outward * rnd.uniform(0.8, 2.4)
            x, y = (m, line + off) if axis == 'x' else (line + off, m)
            dr.put('tree', rnd.choice(tree_keys), x, y, 0.0, rnd.uniform(0, 360), rnd.uniform(0.8, 1.0), gap=0.1)
        return dress

    # West and east rows along the street and the square, the north row closing the square.
    rows = [('y', xw, 90.0, y_end + 6, yn - 1.0, -1), ('y', xe, -90.0, y_end + 6, yn - 1.0, 1),
            ('x', yn, 0.0, xw - 7.0, xe + 7.0, 1)]
    for axis, front, facing, a, b, outward in rows:
        homes = _row(dr, 'house', house, a, b, front, axis, facing, rnd, between=gap_tree(axis, front, outward))
        for (hx, hy), w in homes:
            # Yard dressing on the street side of each house: fence with a gate, path, bushes, mailbox.
            if axis == 'y':
                edge = cx + outward * (road_half + pave)
                depth = abs(front - edge)
                if depth > 1.2 and hy < y_sq:
                    fx = edge + outward * 0.3
                    for s0, s1 in ((hy - w / 2 - 0.2, hy - 0.6), (hy + 0.6, hy + w / 2 + 1.4)):
                        seg = dr.tpl('fence', fence_parts(round(s1 - s0, 1), fence_col))
                        dr.put('fence', seg, fx, (s0 + s1) / 2, 0.0, facing, gap=0.05)
                    path = dr.tpl('path', [P('box', 0, 0, 0.004, 1.2, depth, 0.008, '#D8D2C4')], decal=True,
                                  shadow=False)
                    dr.put('path', path, (front + edge) / 2, hy, -0.009, facing, check=False)
                    dr.put('mailbox', mail_key, edge - outward * 0.35, hy + 1.0, 0.0, facing + 90)
                for by in (hy - 1.5, hy + 1.5):
                    dr.put('bush', rnd.choice(bush_keys), front - outward * 0.6, by, 0.0, rnd.uniform(-10, 10),
                           rnd.uniform(0.75, 1.0))
            else:
                for bx in (hx - 1.5, hx + 1.5):
                    dr.put('bush', rnd.choice(bush_keys), bx, front - 0.6, 0.0, rnd.uniform(-10, 10),
                           rnd.uniform(0.75, 1.0))
        # Taller blocks behind each row fill the sky above the roofs.
        back = front + outward * rnd.uniform(12.0, 14.0)
        _row(dr, 'apartment', lambda: apartment_parts(rnd, night), a - 8, b + 8, back, axis, facing, rnd,
             gap=(1.5, 4.0))
    # Street lamps and street trees on both pavements, parked cars along both kerbs.
    lamp_key = dr.tpl('lamp', lamp_parts(night))
    for side in (-1, 1):
        kx = cx + side * road_half
        y = y_sq - 2.0 - (0 if side > 0 else 5.0)
        while y > y_end + 3:
            dr.put('lamp', lamp_key, kx + side * 0.45, y, 0.0, 90.0 * side)
            dr.put('tree', rnd.choice(tree_keys), kx + side * 1.1, y - 5.0, 0.0, rnd.uniform(0, 360),
                   rnd.uniform(0.7, 0.85), gap=0.1)
            y -= 10.0
    car_keys = [dr.tpl('car', car_parts(col)) for col in rnd.sample(CARS, 4)]
    for side in (-1, 1):
        y = y_sq - rnd.uniform(2, 6)
        while y > y_end + 4:
            if rnd.random() < 0.65:
                dr.put('car', rnd.choice(car_keys), cx + side * (road_half - 1.35), y, 0.0, 0.0 if side < 0 else 180.0,
                       gap=0.05)
            y -= rnd.uniform(6.0, 9.0)
    hyd = dr.tpl('hydrant', [P('cyl', 0, 0, 0.3, 0.3, 0.3, 0.6, '#E53935', 'gloss'),
                             P('cyl', 0, 0, 0.64, 0.36, 0.36, 0.1, '#C62828', 'gloss'),
                             P('box', 0, 0, 0.42, 0.5, 0.12, 0.12, '#C62828', 'gloss', 0.03)])
    bench = dr.tpl('bench', [P('box', 0, 0, 0.45, 1.6, 0.5, 0.08, '#B07A45', bevel=0.02),
                             P('box', 0, 0.22, 0.76, 1.6, 0.06, 0.42, '#B07A45', bevel=0.02),
                             P('box', -0.7, 0, 0.21, 0.08, 0.45, 0.42, '#3B3F47'),
                             P('box', 0.7, 0, 0.21, 0.08, 0.45, 0.42, '#3B3F47')])
    planter = dr.tpl('planter', [P('box', 0, 0, 0.25, 1.4, 0.7, 0.5, '#B9A48A', bevel=0.04),
                                 P('box', -0.35, 0, 0.6, 0.5, 0.5, 0.3, '#E84A5F', bevel=0.1),
                                 P('box', 0.3, 0.05, 0.62, 0.55, 0.5, 0.32, '#FFCE54', bevel=0.1)])
    for side in (-1, 1):
        for _ in range(3):
            dr.put('hydrant', hyd, cx + side * (road_half + 0.5), rnd.uniform(y_end + 5, y_sq - 3), 0.0, 0.0)
            dr.put('bench', bench, cx + side * (road_half + pave - 0.4), rnd.uniform(y_end + 5, y_sq - 3), 0.0,
                   -90.0 * side)
    # Square furniture: lamps and trees at the corners, planters and benches along the edges.
    for qx, qy in ((xw + 1.0, yn - 1.0), (xe - 1.0, yn - 1.0), (xw + 1.0, y_sq + 1.0), (xe - 1.0, y_sq + 1.0)):
        dr.put('lamp', lamp_key, qx, qy, 0.0, rnd.uniform(0, 360))
        dr.put('tree', rnd.choice(tree_keys), qx + rnd.uniform(-1, 1), qy + rnd.uniform(-1, 1), 0.0,
               rnd.uniform(0, 360), 0.8, gap=0.1)
    for _ in range(10):
        edge = rnd.choice(['w', 'e', 'n'])
        if edge == 'n':
            x, y, r = rnd.uniform(xw + 1.5, xe - 1.5), yn - 0.7, 0.0
        else:
            x, y, r = (xw + 0.7, rnd.uniform(y_sq + 1, yn - 1), 90.0) if edge == 'w' else \
                (xe - 0.7, rnd.uniform(y_sq + 1, yn - 1), -90.0)
        dr.put('planter' if rnd.random() < 0.6 else 'bench', planter if rnd.random() < 0.6 else bench, x, y, 0.0, r)
    # The cross street at the far end, lined with houses facing up the street.
    cross = dr.tpl('road', [P('box', 0, 0, 0.004, 90, 2 * road_half, 0.008, '#4A4E58')], decal=True, shadow=False)
    dr.put('road', cross, cx, y_end, -0.010, check=False)
    _row(dr, 'house', house, cx - 34, cx + 34, y_end - road_half - pave - 0.8, 'x', 180.0, rnd,
         between=gap_tree('x', y_end - road_half - pave - 0.8, -1))
    _far_ring(dr, 'town', night)


def _far_ring(dr, style, night):
    """Distant hills (and a hazy skyline for the town) all around, plus clouds."""
    rnd = dr.rnd
    if style == 'town':
        cols = ['#7CC46A', '#68B85A', '#8FD07A']
    elif style == 'forest':
        cols = ['#1C3324', '#22402B', '#1A2E22']
    else:
        cols = ['#3A2A2A', '#4A3030', '#2E2222']
    for i in range(14):
        a = 2 * math.pi * (i + rnd.uniform(0.0, 0.6)) / 14
        d = rnd.uniform(95, 140)
        w, dd, h = rnd.uniform(40, 70), rnd.uniform(25, 40), rnd.uniform(12, 26)
        key = dr.tpl('hill', hill_parts(rnd, [jitter(c, rnd) for c in cols], w, dd, h), shadow=False)
        dr.put('hill', key, dr.cx + d * math.cos(a), dr.cy + d * math.sin(a), -0.3, math.degrees(a) + 90,
               check=False)
    if style == 'town':
        for i in range(10):
            a = math.radians(70 + i * 4.5 + rnd.uniform(-1, 1))
            d = rnd.uniform(78, 90)
            H = rnd.uniform(14, 34)
            col = jitter('#A9BCCF', rnd, 0.04)
            parts = [P('box', 0, 0, H / 2, 7, 7, H, col, bevel=0.2)]
            for k in range(int(H / 3.2)):
                parts.append(P('box', 0, -3.53, 2.0 + k * 3.2, 6.0, 0.06, 1.1, shade(col, 0.82)))
            dr.put('tower', dr.tpl('tower', parts, shadow=False), dr.cx + d * math.cos(a), dr.cy + d * math.sin(a),
                   0.0, math.degrees(a) + 90, check=False)
    if not night:
        _clouds(dr, 16, 45, 170, (24, 46))


def _clouds(dr, n, r0, r1, zr, col='#FFFFFF', k=(1.0, 2.2)):
    rnd = dr.rnd
    for i in range(n):
        a = 2 * math.pi * (i + rnd.uniform(0, 1)) / n
        d = rnd.uniform(r0, r1)
        key = dr.tpl('cloud', cloud_parts(rnd, col, rnd.uniform(*k)), shadow=False)
        dr.put('cloud', key, dr.cx + d * math.cos(a), dr.cy + d * math.sin(a), rnd.uniform(*zr),
               rnd.uniform(0, 360), check=False)


# ---------------------------------------------------------------- night forest
def night_forest(dr, night):
    rnd = dr.rnd
    has_fire = any(p.get('type') == 'campfire' for p in dr.m['setting'].get('props', []))
    dr.put('ground', dr.tpl('ground', [P('box', 0, 0, -0.1, 360, 360, 0.2, GROUND_HEX['night_forest'])],
                            decal=True, shadow=False), dr.cx, dr.cy, -0.2, check=False)
    patches = []
    spots = []
    for _ in range(40):
        a, d = rnd.uniform(0, 2 * math.pi), rnd.uniform(2, 30)
        q = P('box', d * math.cos(a), d * math.sin(a), 0.001, rnd.uniform(1.5, 4), rnd.uniform(1.5, 4), 0.002,
              rnd.choice(['#244028', '#30552F', '#203824', '#3A4A2A']), rz=rnd.uniform(0, 90))
        # Patches share one height, so overlapping ones would z-fight (flicker): keep them apart.
        fp = footprint(q[1], q[2], q[4] / 2, q[5] / 2, q[9])
        if not any(_overlap(fp, o) for o in spots):
            spots.append(fp)
            patches.append(q)
    dr.put_drawn('patches', patches, dr.cx, dr.cy, 0.0, decal=True, shadow=False)
    pines = ['#2B6643', '#33774D', '#3F8A55', '#2A5E40']
    keys = [dr.tpl('tree', tree_parts(s, rnd, rnd.choice(pines), '#7A5230'))
            for s in ('pine', 'pine', 'blockpine', 'round', 'pine', 'blockpine')]
    # Dense rings of trees: an inner ring stratified by azimuth, then thicker woods behind it.
    dr.scatter('tree', keys, 24, 0.4, 3.5, scale=(0.9, 1.25), sectors=24, gap=0.35)
    dr.scatter('tree', keys, 40, 3.5, 12.0, scale=(0.9, 1.4), sectors=20, gap=0.4)
    dr.scatter('tree', keys, 60, 12.0, 38.0, scale=(1.0, 1.6), sectors=30, gap=0.5)
    bush = [dr.tpl('bush', tree_parts('bush', rnd, c)) for c in ('#2A5233', '#335E37', '#26492D')]
    dr.scatter('bush', bush, 26, -1.0, 6.0, scale=(0.8, 1.2), gap=0.2)
    rocks = [dr.tpl('rock', rock_parts(rnd, c)) for c in ('#6B7078', '#5D626B', '#777C84')]
    dr.scatter('rock', rocks, 16, -1.5, 8.0, scale=(0.6, 1.4), gap=0.2)
    log = dr.tpl('log', [P('cyl', 0, 0, 0.25, 0.5, 0.5, 2.4, '#5A3A22', rx=90),
                         P('cyl', 0, -1.21, 0.25, 0.42, 0.42, 0.02, '#B08A5A', rx=90)])
    dr.scatter('log', log, 5, 0.0, 6.0, gap=0.3)
    stump = dr.tpl('stump', [P('cyl', 0, 0, 0.25, 0.6, 0.6, 0.5, '#5A3A22'),
                             P('cyl', 0, 0, 0.505, 0.52, 0.52, 0.02, '#B08A5A')])
    dr.scatter('stump', stump, 5, 0.0, 6.0, gap=0.3)
    shroom = dr.tpl('mushrooms', [P('cyl', 0, 0, 0.12, 0.08, 0.08, 0.24, '#EDE6D6'),
                                  P('cyl', 0, 0, 0.27, 0.3, 0.3, 0.1, '#7FE3FF', 'glow'),
                                  P('cyl', 0.25, 0.1, 0.08, 0.06, 0.06, 0.16, '#EDE6D6'),
                                  P('cyl', 0.25, 0.1, 0.18, 0.2, 0.2, 0.07, '#B4F07A', 'glow')])
    dr.scatter('mushrooms', shroom, 14, -0.5, 7.0, gap=0.1)
    # Fireflies: tiny emissive specks in the woods (never between the camera and a face).
    ff = []
    c = dr.area['clear']
    for _ in range(70):
        a, d = rnd.uniform(0, 2 * math.pi), rnd.uniform(0.6, 14.0)
        hx, hy = (c[2] - c[0]) / 2, (c[3] - c[1]) / 2
        edge = 1.0 / max(abs(math.cos(a)) / max(hx, 1e-3), abs(math.sin(a)) / max(hy, 1e-3))
        ff.append(P('box', math.cos(a) * (edge + d), math.sin(a) * (edge + d), rnd.uniform(0.4, 3.2), 0.05, 0.05, 0.05,
                    rnd.choice(['#E8FF8A', '#FFF59A', '#C8FF7A']), 'emit'))
    dr.put_drawn('fireflies', ff, dr.cx, dr.cy, 0.0, decal=True, shadow=False)
    if not has_fire:
        fire = dr.tpl('campfire', [P('box', 0, 0, 0.08, 0.9, 0.16, 0.16, '#5C3A1E', bevel=0.02, rz=30),
                                   P('box', 0, 0, 0.08, 0.9, 0.16, 0.16, '#5C3A1E', bevel=0.02, rz=-30),
                                   P('pyr', 0, 0, 0.35, 0.4, 0.4, 0.55, '#FF8A1F', 'emit'),
                                   P('pyr', 0, 0, 0.3, 0.22, 0.22, 0.4, '#FFD23F', 'emit')])
        a = rnd.uniform(0, 2 * math.pi)
        for d in (4.0, 6.0, 9.0):
            x, y = dr.cx + math.cos(a) * ((c[2] - c[0]) / 2 + d), dr.cy + math.sin(a) * ((c[3] - c[1]) / 2 + d)
            if dr.put('campfire', fire, x, y, 0.0, 0.0, gap=0.5):
                dr.lights.append({'type': 'POINT', 'pos': [_r(x), _r(y), 0.9], 'energy': 140, 'color': '#FF8C3A'})
                break
    _far_ring(dr, 'forest', night)
    dr.put('moon', dr.tpl('moon', [P('ball', 0, 0, 0, 14, 14, 14, '#FFF4D6', 'emit')], shadow=False),
           dr.cx - 90, dr.cy + 210, 60, check=False)


# ---------------------------------------------------------------- obbies
OBBY = ['#4CAF50', '#2196F3', '#FF9800', '#E91E63', '#9C27B0', '#FFEB3B', '#00BCD4', '#FF5722', '#8BC34A']
LAVA_PLAT = ['#5C5F66', '#7A4E3A', '#8E3B46', '#4B4F8C', '#6B4A8E', '#B5651D', '#3F6E6A']


def obby(dr, night, lava):
    rnd = dr.rnd
    pal = LAVA_PLAT if lava else OBBY
    floor_z = -5.0 if lava else -6.0
    if lava:
        dr.put('lava', dr.tpl('lava', [P('box', 0, 0, 0, 360, 360, 0.4, '#FF5A1F', 'lava')], decal=True, shadow=False),
               dr.cx, dr.cy, floor_z - 0.4, check=False)
    else:
        sea = '#FFE6E0' if dr.m['setting'].get('time_of_day') == 'sunset' else '#F4F8FF'
        dr.put('cloudsea', dr.tpl('cloudsea', [P('box', 0, 0, 0, 360, 360, 0.4, sea, 'cloud')], decal=True,
                                  shadow=False), dr.cx, dr.cy, floor_z - 0.4, check=False)
    band = dr.area['band']

    def plat_key(w, d, col):
        under = None if lava else (rnd.choice(['#8B6B4A', '#7A5C3E']) if rnd.random() < 0.35 else None)
        return dr.tpl('platform', platform_parts(w, d, col, under=under))

    # Floating studded platforms at several depths and heights, stratified by azimuth.
    keys = [plat_key(rnd.choice([2.0, 3.0, 4.0]), rnd.choice([2.0, 3.0, 4.0]), jitter(rnd.choice(pal), rnd, 0.04))
            for _ in range(10)]
    dr.scatter('platform', keys, 34, 0.5, 9.0, sectors=34, gap=0.6, rot=0.0,
               zfn=lambda d: rnd.choice([-2.4, -1.6, -0.8, 0.0, 0.6, 1.4, 2.4, 3.4, 4.4]) - 0.8)
    dr.scatter('platform', keys, 16, 2.0, 12.0, sectors=16, gap=0.6, rot=0.0,
               zfn=lambda d: rnd.uniform(2.5, 6.0))
    dr.scatter('platform', keys, 30, 9.0, 40.0, sectors=30, gap=1.0, rot=0.0,
               zfn=lambda d: rnd.uniform(-4.0, 7.0))
    # Under the stage, platforms below every camera give depth to high shots.
    for _ in range(8):
        c = dr.area['clear']
        x, y = rnd.uniform(c[0], c[2]), rnd.uniform(c[1], c[3])
        dr.put('platform', rnd.choice(keys), x, y, band[0] - rnd.uniform(1.5, 3.0) - 0.8, 0.0, gap=0.5)
    # Tall elements in every direction: checkpoint towers, arches, block stairs.
    sectors = 12
    for i in range(sectors):
        kind = ('tower', 'arch', 'stairs', 'tower', 'island')[i % 5]
        col = jitter(pal[i % len(pal)], rnd)
        col2 = WHITE if not lava else '#2B2626'
        if kind == 'tower':
            hgt = rnd.uniform(8, 13)
            n = int(hgt / 1.4)
            parts = [P('box', 0, 0, floor_z + 0.7 + k * 1.4, 1.6, 1.6, 1.4, col if k % 2 else col2, bevel=0.05)
                     for k in range(n)]
            top = floor_z + n * 1.4
            parts.append(P('box', 0, 0, top + 0.25, 3.2, 3.2, 0.5, col, 'stud', 0.05))
            parts += [[q[0], q[1], q[2], q[3] + top + 0.5] + q[4:] for q in flag_parts(rnd.choice(OBBY))]
        elif kind == 'arch':
            hgt = rnd.uniform(4, 7)
            parts = [P('box', -2.2, 0, hgt / 2, 0.9, 0.9, hgt, col, bevel=0.05),
                     P('box', 2.2, 0, hgt / 2, 0.9, 0.9, hgt, col, bevel=0.05),
                     P('box', 0, 0, hgt + 0.35, 5.4, 1.0, 0.7, jitter(rnd.choice(pal), rnd), bevel=0.05),
                     P('box', 0, 0, -0.4, 6.4, 2.4, 0.8, col2, 'stud', 0.05)]
        elif kind == 'stairs':
            parts = [P('box', math.cos(k * 0.5) * 2.2, math.sin(k * 0.5) * 2.2, k * 0.9, 1.4, 1.4, 0.5,
                       jitter(pal[k % len(pal)], rnd), 'stud', 0.05, rz=math.degrees(k * 0.5)) for k in range(10)]
        else:
            parts = platform_parts(6.0, 5.0, '#6DBE45' if not lava else '#4A4040', 1.2,
                                   under='#7A5C3E' if not lava else '#2B2626')
            parts += [[q[0], q[1] - 1.2, q[2] + 0.8, q[3] + 0.6] + q[4:] for q in
                      tree_parts('round', rnd, rnd.choice(LEAVES) if not lava else '#3A3A3A', k=0.8)]
        key = dr.tpl(kind, parts)
        for _ in range(6):
            a = 2 * math.pi * (i + rnd.uniform(0.15, 0.85)) / sectors
            d = rnd.uniform(9.0, 22.0)
            c = dr.area['clear']
            edge = 1.0 / max(abs(math.cos(a)) / max((c[2] - c[0]) / 2, 1e-3),
                             abs(math.sin(a)) / max((c[3] - c[1]) / 2, 1e-3))
            z = floor_z if kind == 'tower' else rnd.uniform(-2.5, 1.0)
            if dr.put(kind, key, dr.cx + math.cos(a) * (edge + d), dr.cy + math.sin(a) * (edge + d), z,
                      math.degrees(a) + 90, gap=0.8):
                break
    coins = []
    for _ in range(18):
        a, d = rnd.uniform(0, 2 * math.pi), rnd.uniform(7, 20)
        coins.append(P('cyl', d * math.cos(a), d * math.sin(a), rnd.uniform(0.5, 3.5), 0.5, 0.5, 0.08, '#FFC83D',
                       'gloss', rx=90, rz=rnd.uniform(0, 180)))
    if lava:
        # Basalt pillars rising from the lava, glowing kill bricks and distant lava falls.
        basalt = ['#2B2626', '#3A3232', '#332B2B']
        for i in range(20):
            hgt = rnd.uniform(4.0, 10.0)
            parts = [P('box', rnd.uniform(-0.2, 0.2), rnd.uniform(-0.2, 0.2), floor_z + 0.6 + k * 1.2,
                       rnd.uniform(1.4, 2.2), rnd.uniform(1.4, 2.2), 1.2, rnd.choice(basalt), bevel=0.08,
                       rz=rnd.uniform(0, 20)) for k in range(int(hgt / 1.2))]
            parts.append(P('box', 0, -1.0, floor_z + hgt * 0.4, 0.12, 0.06, hgt * 0.5, '#FF7A1F', 'emit'))
            key = dr.tpl('pillar', parts)
            a = 2 * math.pi * (i + rnd.uniform(0.1, 0.9)) / 20
            d = rnd.uniform(2.0, 20.0)
            c = dr.area['clear']
            edge = 1.0 / max(abs(math.cos(a)) / max((c[2] - c[0]) / 2, 1e-3),
                             abs(math.sin(a)) / max((c[3] - c[1]) / 2, 1e-3))
            dr.put('pillar', key, dr.cx + math.cos(a) * (edge + d), dr.cy + math.sin(a) * (edge + d), floor_z,
                   rnd.uniform(0, 90), gap=0.5)
        kill = dr.tpl('killbrick', [P('box', 0, 0, 0, 2.0, 0.6, 0.6, '#FF2D2D', 'emit', 0.04)])
        dr.scatter('killbrick', kill, 10, 1.0, 14.0, gap=0.5, zfn=lambda d: rnd.uniform(-1.5, 2.5))
        for i in range(8):
            a = 2 * math.pi * (i + rnd.uniform(0.2, 0.8)) / 8
            d = rnd.uniform(55, 80)
            H = rnd.uniform(16, 30)
            parts = [P('box', 0, 0, H / 2, rnd.uniform(18, 30), 10, H, rnd.choice(basalt), bevel=0.6),
                     P('box', rnd.uniform(-4, 4), -5.1, H * 0.45, 2.2, 0.3, H * 0.9, '#FF7A1F', 'lava')]
            dr.put('cliff', dr.tpl('cliff', parts, shadow=False), dr.cx + d * math.cos(a), dr.cy + d * math.sin(a),
                   floor_z, math.degrees(a) + 90, check=False)
        embers = [P('box', rnd.uniform(-20, 20), rnd.uniform(-20, 20), rnd.uniform(-3, 7), 0.06, 0.06, 0.06,
                    rnd.choice(['#FFB03A', '#FF7A1F', '#FFD23F']), 'emit') for _ in range(60)]
        embers = [e for e in embers if not (dr.area['clear'][0] - 1 < e[1] + dr.cx < dr.area['clear'][2] + 1 and
                                            dr.area['clear'][1] - 1 < e[2] + dr.cy < dr.area['clear'][3] + 1)]
        dr.put_drawn('embers', embers, dr.cx, dr.cy, 0.0, decal=True, shadow=False)
    else:
        _clouds(dr, 14, 14, 40, (-5.2, -2.5), k=(0.8, 1.4))
        _clouds(dr, 14, 45, 150, (-4, 30), k=(1.4, 2.6))
        for i in range(8):
            a = 2 * math.pi * (i + rnd.uniform(0.2, 0.8)) / 8
            d = rnd.uniform(70, 110)
            parts = platform_parts(rnd.uniform(14, 22), rnd.uniform(10, 16), '#6DBE45', 2.0, under='#7A5C3E')
            for _ in range(4):
                parts += [[q[0], q[1] + rnd.uniform(-5, 5), q[2] + rnd.uniform(-3, 3), q[3] + 1.0] + q[4:]
                          for q in tree_parts('round', rnd, rnd.choice(LEAVES), k=1.3)]
            dr.put('island', dr.tpl('island', parts, shadow=False), dr.cx + d * math.cos(a), dr.cy + d * math.sin(a),
                   rnd.uniform(-6, 8), rnd.uniform(0, 360), check=False)
    # Coins float well away from the stage (decorative, tiny).
    coins = [q for q in coins if not (dr.area['clear'][0] - 1 < q[1] + dr.cx < dr.area['clear'][2] + 1 and
                                      dr.area['clear'][1] - 1 < q[2] + dr.cy < dr.area['clear'][3] + 1)]
    if coins:
        dr.put_drawn('coins', coins, dr.cx, dr.cy, 0.0, decal=True, shadow=False)


# ---------------------------------------------------------------- interiors
def _room(dr, band, min_hx, min_hy, wall_col, low_col, floor_cols, ceiling_col, wall_h, night):
    """Floor, four walls (with wainscot) and a ceiling around the clearance box. Returns the inner box.

    Walls and ceiling cast no shadows so the key light still reaches the room (like light through
    windows); at night warm ceiling lights take over.
    """
    c = dr.area['clear']
    hx = max((c[2] - c[0]) / 2 + band, min_hx)
    hy = max((c[3] - c[1]) / 2 + band, min_hy)
    cx, cy = dr.cx, dr.cy
    inner = [cx - hx, cy - hy, cx + hx, cy + hy]
    if night:
        for lx in ([cx] if hx < 6 else [cx - hx / 2, cx + hx / 2]):
            dr.lights.append({'type': 'POINT', 'pos': [_r(lx), _r(cy), _r(wall_h - 0.5)], 'energy': 450,
                              'color': '#FFD9A6'})
    tiles = []
    nx, ny = int(2 * hx / 1.0) + 2, int(2 * hy / 1.0) + 2
    for i in range(nx):
        for j in range(ny):
            tiles.append(P('box', -hx - 0.5 + i + 0.5, -hy - 0.5 + j + 0.5, 0.004, 0.99, 0.99, 0.008,
                           floor_cols[(i + j) % len(floor_cols)]))
    dr.put('floor', dr.tpl('floor', [P('box', 0, 0, -0.1, 2 * hx + 2, 2 * hy + 2, 0.2, shade(floor_cols[0], 0.8))],
                           decal=True, shadow=False), cx, cy, SLAB_TOP - 0.2, check=False)
    dr.put('floortiles', dr.tpl('floortiles', tiles, decal=True, shadow=False), cx, cy, -0.008, check=False)
    t = 0.3
    for name, x, y, w, rot in (('wall_n', cx, cy + hy + t / 2, 2 * hx + 2 * t, 0.0),
                               ('wall_s', cx, cy - hy - t / 2, 2 * hx + 2 * t, 180.0),
                               ('wall_e', cx + hx + t / 2, cy, 2 * hy, -90.0),
                               ('wall_w', cx - hx - t / 2, cy, 2 * hy, 90.0)):
        parts = [P('box', 0, 0, wall_h / 2, w, t, wall_h, wall_col),
                 P('box', 0, -t / 2 - 0.01, 0.55, w, 0.02, 1.1, low_col),
                 P('box', 0, -t / 2 - 0.03, 1.12, w, 0.05, 0.07, shade(low_col, 0.85)),
                 P('box', 0, -t / 2 - 0.03, 0.06, w, 0.06, 0.12, '#FFFFFF')]
        dr.put('wall', dr.tpl('wall', parts, shadow=False), x, y, 0.0, rot, check=False)
    dr.put('ceiling', dr.tpl('ceiling', [P('box', 0, 0, 0.1, 2 * hx + 0.6, 2 * hy + 0.6, 0.2, ceiling_col)],
                             shadow=False), cx, cy, wall_h, check=False)
    return inner


def _on_wall(dr, inner, wall, along, z, key, depth, kind=None):
    """Place a wall-mounted template (front facing into the room) at 'along' metres from the wall centre.

    Checked like any piece, so furniture against a wall still keeps out of the camera zones.
    """
    x0, y0, x1, y1 = inner
    kind = kind or key.rstrip('0123456789')
    if wall == 'n':
        return dr.put(kind, key, (x0 + x1) / 2 + along, y1 - depth / 2 - 0.01, z, 0.0, gap=0.0)
    if wall == 's':
        return dr.put(kind, key, (x0 + x1) / 2 - along, y0 + depth / 2 + 0.01, z, 180.0, gap=0.0)
    if wall == 'e':
        return dr.put(kind, key, x1 - depth / 2 - 0.01, (y0 + y1) / 2 - along, z, -90.0, gap=0.0)
    return dr.put(kind, key, x0 + depth / 2 + 0.01, (y0 + y1) / 2 + along, z, 90.0, gap=0.0)


def pane_window(w, h, night, trim='#FFFFFF', curtains=None):
    sky = '#1B2440' if night else '#9FD3F0'
    parts = [P('box', 0, 0.02, 0, w + 0.24, 0.1, h + 0.24, trim, bevel=0.02),
             P('box', 0, -0.035, 0, w, 0.02, h, sky, 'pane'),
             P('box', 0, -0.06, 0, 0.07, 0.04, h, trim), P('box', 0, -0.06, 0, w, 0.04, 0.07, trim),
             P('box', 0, -0.1, -h / 2 - 0.08, w + 0.4, 0.22, 0.08, trim, bevel=0.02)]
    if night:
        parts.append(P('cyl', w * 0.22, -0.05, h * 0.2, 0.32, 0.32, 0.01, '#FFF4D6', 'emit', rx=90))
    else:
        parts += [P('box', -w * 0.18, -0.05, h * 0.18, w * 0.32, 0.01, h * 0.12, '#FFFFFF', 'pane'),
                  P('box', -w * 0.08, -0.05, h * 0.26, w * 0.22, 0.01, h * 0.1, '#FFFFFF', 'pane'),
                  P('box', w * 0.0, -0.05, -h * 0.42, w, 0.01, h * 0.16, '#7CC46A', 'pane')]
    if curtains:
        for s in (-1, 1):
            parts.append(P('box', s * (w / 2 + 0.12), -0.12, 0.05, 0.36, 0.08, h + 0.4, curtains, bevel=0.03))
        parts.append(P('box', 0, -0.12, h / 2 + 0.3, w + 0.9, 0.06, 0.06, '#8B8F98'))
    return parts


def poster(rnd, w, h):
    bg = rnd.choice(['#FFFFFF', '#FFF3C4', '#D7ECFF', '#FFE0E6', '#E2F7D7', '#2E3A59'])
    parts = [P('box', 0, 0, 0, w, 0.03, h, bg)]
    for _ in range(rnd.randint(2, 4)):
        col = rnd.choice(['#E84A5F', '#FFCE54', '#4FC1E9', '#A0D468', '#AC92EC', '#FC6E51', '#3BAFDA'])
        shape = rnd.choice(['box', 'cyl', 'pyr'])
        s = rnd.uniform(0.15, 0.4) * min(w, h)
        x, z = rnd.uniform(-w / 3, w / 3), rnd.uniform(-h / 3, h / 3)
        if shape == 'box':
            parts.append(P('box', x, -0.025, z, s * 1.4, 0.01, s, col, rz=0))
        elif shape == 'cyl':
            parts.append(P('cyl', x, -0.025, z, s, s, 0.01, col, rx=90))
        else:
            parts.append(P('pyr', x, -0.025, z, s, 0.01, s, col))
    return parts


def bookcase(rnd, w, h, wood='#A0703F'):
    parts = [P('box', 0, 0, h / 2, w, 0.4, h, wood, bevel=0.02)]
    shelves = max(2, int(h / 0.45))
    for k in range(shelves):
        z = 0.12 + k * (h - 0.2) / shelves
        x = -w / 2 + 0.1
        while x < w / 2 - 0.15:
            bw = rnd.uniform(0.06, 0.14)
            bh = rnd.uniform(0.22, 0.34)
            parts.append(P('box', x + bw / 2, -0.16, z + bh / 2, bw, 0.26, bh,
                           rnd.choice(['#E84A5F', '#4FC1E9', '#FFCE54', '#A0D468', '#AC92EC', '#ED5565', '#F6F7FB',
                                       '#37BC9B']), bevel=0.005))
            x += bw + rnd.uniform(0.0, 0.03)
        parts.append(P('box', 0, -0.05, z - 0.02, w - 0.08, 0.32, 0.03, shade(wood, 0.85)))
    return parts


def chalk_scribbles(rnd, w, h):
    """Abstract chalk marks: zigzags, a few shapes and dashed rows. Nothing readable."""
    parts = []
    col = '#F2F2EA'
    for row in range(4):
        z = h / 2 - 0.3 - row * 0.32
        x = -w / 2 + 0.3
        while x < w / 2 - 0.6:
            L = rnd.uniform(0.1, 0.38)
            if x + L > w / 2 - 0.3:
                break
            parts.append(P('box', x + L / 2, 0, z + rnd.uniform(-0.03, 0.03), L, 0.01, 0.025, col,
                           ry=rnd.uniform(-6, 6)))
            x += L + rnd.uniform(0.08, 0.18)
    cx = rnd.uniform(-0.2, 0.2) * w
    for k in range(6):  # zigzag
        parts.append(P('box', cx + k * 0.18, 0, -h / 2 + 0.35, 0.24, 0.01, 0.025, col, ry=45 if k % 2 else -45))
    for k in range(8):  # an octagon "circle"
        a = k * math.pi / 4
        parts.append(P('box', w * 0.32 + 0.22 * math.cos(a), 0, -0.05 + 0.22 * math.sin(a), 0.17, 0.01, 0.025, col,
                       ry=-math.degrees(a) + 90))
    return parts


def student_desk(rnd, chair_col):
    top, legs = '#D8A86A', '#7D8590'
    parts = [P('box', 0, 0, 0.71, 1.0, 0.6, 0.05, top, bevel=0.015)]
    for sx in (-1, 1):
        parts.append(P('box', sx * 0.44, 0, 0.35, 0.05, 0.5, 0.69, legs))
    parts.append(P('box', rnd.uniform(-0.25, 0.25), rnd.uniform(-0.1, 0.1), 0.75, 0.32, 0.24, 0.03,
                   rnd.choice(['#E84A5F', '#4FC1E9', '#FFCE54', '#A0D468']), rz=rnd.uniform(-20, 20)))
    # Chair behind the desk (local +y), back below LOW_TOP.
    parts += [P('box', 0, 0.55, 0.44, 0.46, 0.44, 0.05, chair_col, 'gloss', 0.02),
              P('box', 0, 0.76, 0.62, 0.46, 0.05, 0.34, chair_col, 'gloss', 0.02)]
    for sx in (-1, 1):
        for sy in (0.37, 0.73):
            parts.append(P('box', sx * 0.2, sy, 0.21, 0.04, 0.04, 0.42, legs))
    return parts


def classroom(dr, night):
    rnd = dr.rnd
    wall_h = max(3.8, dr.area['band'][1] + 0.4)
    walls = rnd.choice([('#F3EAD7', '#9CC5A1'), ('#EEF2F5', '#8FB8DE'), ('#FAF0DC', '#E3B87F')])
    inner = _room(dr, 1.6, 6.5, 5.5, walls[0], walls[1], ['#D9B98C', '#CFAE80'], '#F7F5F0', wall_h, night)
    x0, y0, x1, y1 = inner
    W, H = x1 - x0, y1 - y0
    # Chalkboard with scribbles, clock above, teacher's desk in front (north wall faces the establishing camera).
    bw = min(6.0, W - 3.0)
    board = [P('box', 0, 0.02, 0, bw + 0.24, 0.08, 1.64, '#8B5A2B', bevel=0.02),
             P('box', 0, -0.03, 0, bw, 0.03, 1.4, '#2F4F3A'),
             P('box', 0, -0.12, -0.78, bw, 0.18, 0.05, '#8B5A2B')]
    board += [[q[0], q[1], q[2] - 0.05, q[3]] + q[4:] for q in chalk_scribbles(rnd, bw, 1.4)]
    _on_wall(dr, inner, 'n', 0.0, 1.0, dr.tpl('board', board, shadow=False), 0.2)
    clock = [P('cyl', 0, 0.02, 0, 0.62, 0.62, 0.06, '#C0392B', rx=90), P('cyl', 0, -0.02, 0, 0.52, 0.52, 0.03,
                                                                          '#FFFFFF', rx=90),
             P('box', 0, -0.045, 0.1, 0.03, 0.01, 0.2, '#22252B'), P('box', 0.07, -0.045, 0, 0.14, 0.01, 0.03,
                                                                         '#22252B')]
    _on_wall(dr, inner, 'n', 0.0, min(wall_h - 0.75, 3.0), dr.tpl('clock', clock, shadow=False), 0.1)
    tdesk = [P('box', 0, 0, 0.76, 1.9, 0.9, 0.06, '#A0703F', bevel=0.02),
             P('box', -0.7, 0, 0.37, 0.45, 0.85, 0.74, '#8B5A2B', bevel=0.02),
             P('box', 0.7, 0, 0.37, 0.45, 0.85, 0.74, '#8B5A2B', bevel=0.02),
             P('ball', 0.5, 0.1, 0.87, 0.16, 0.16, 0.16, '#E53935'),
             P('box', -0.3, 0.05, 0.82, 0.4, 0.3, 0.08, '#4FC1E9'),
             P('ball', -0.6, 0.15, 1.08, 0.34, 0.34, 0.34, '#4FC1E9'),
             P('cyl', -0.6, 0.15, 0.85, 0.04, 0.04, 0.2, '#8B8F98')]
    dr.put('teacher_desk', dr.tpl('teacher_desk', tdesk), dr.cx + rnd.uniform(-1, 1), y1 - 1.3, 0.0, 180.0)
    # Windows on the west wall, door and cubbies on the east, shelves along the south wall, posters.
    nwin = max(2, int(H / 2.6))
    for i in range(nwin):
        along = -H / 2 + H * (i + 0.5) / nwin
        _on_wall(dr, inner, 'w', along, 1.0, dr.tpl('window', pane_window(1.6, 1.5, night)), 0.24)
    door = [P('box', 0, 0.02, 1.1, 1.3, 0.08, 2.25, '#FFFFFF', bevel=0.02),
            P('box', 0, -0.03, 1.05, 1.1, 0.06, 2.1, '#8B5A2B', bevel=0.02),
            P('box', 0, -0.07, 1.55, 0.4, 0.02, 0.5, '#9FD3F0', 'gloss'),
            P('box', 0.4, -0.09, 1.0, 0.12, 0.05, 0.05, '#E8C547', 'gloss')]
    _on_wall(dr, inner, 'e', -H / 2 + 1.4, 0.0, dr.tpl('door', door, shadow=False), 0.12)
    cub = [P('box', 0, 0, 0.6, 2.4, 0.45, 1.2, '#F2F2F2', bevel=0.02)]
    for i in range(4):
        for j in range(2):
            cub.append(P('box', -0.9 + i * 0.6, -0.2, 0.32 + j * 0.56, 0.5, 0.06, 0.46,
                         rnd.choice(['#E84A5F', '#4FC1E9', '#FFCE54', '#A0D468', '#AC92EC'])))
    _on_wall(dr, inner, 'e', 1.0, 0.0, dr.tpl('cubbies', cub), 0.45)
    for i, along in enumerate([-W / 3, 0.0, W / 3]):
        _on_wall(dr, inner, 's', along, 0.0, dr.tpl('bookcase', bookcase(rnd, 1.8, 1.9)), 0.4)
    for wall, n in (('n', 2), ('s', 3), ('e', 1), ('w', 2)):
        span = (W if wall in 'ns' else H) - 2.0
        for i in range(n):
            along = -span / 2 + span * (i + 0.5) / n + rnd.uniform(-0.3, 0.3)
            if wall == 'n' and abs(along) < bw / 2 + 0.6:
                along = math.copysign(bw / 2 + 0.9, along if along else 1.0)
            if wall == 'w':
                along = -H / 2 + H * (i + 1) / nwin
            _on_wall(dr, inner, wall, along, 2.35 if wall != 'w' else 2.25,
                     dr.tpl('poster', poster(rnd, rnd.uniform(0.6, 0.9), rnd.uniform(0.7, 1.0)), shadow=False), 0.03)
    # A strip of coloured cards above the board, a pin board and coat hooks with backpacks.
    cards = [P('box', -bw / 2 + 0.25 + i * 0.5, 0, 0, 0.4, 0.02, 0.4,
               ['#E84A5F', '#FFCE54', '#4FC1E9', '#A0D468', '#AC92EC', '#FC6E51'][i % 6], rz=0)
             for i in range(int(bw / 0.5))]
    cards += [P('cyl', -bw / 2 + 0.25 + i * 0.5, -0.015, 0, 0.18, 0.18, 0.01, '#FFFFFF', rx=90)
              for i in range(int(bw / 0.5))]
    _on_wall(dr, inner, 'n', 0.0, 2.25, dr.tpl('cards', cards, shadow=False), 0.04)
    pins = [P('box', 0, 0, 0, 1.6, 0.04, 1.0, '#C9A27A', bevel=0.01)]
    for _ in range(9):
        pins.append(P('box', rnd.uniform(-0.65, 0.65), -0.025, rnd.uniform(-0.35, 0.35), 0.24, 0.01, 0.2,
                      rnd.choice(['#FFF59A', '#FF8FA3', '#8FD3FF', '#B4F07A', '#FFFFFF']), ry=rnd.uniform(-8, 8)))
    _on_wall(dr, inner, 'e', H / 2 - 1.8, 1.5, dr.tpl('pinboard', pins, shadow=False), 0.05)
    hooks = [P('box', 0, 0, 0, 2.8, 0.06, 0.1, '#8B5A2B')]
    for i in range(5):
        hooks.append(P('box', -1.2 + i * 0.6, -0.16, -0.38, 0.36, 0.22, 0.5,
                       rnd.choice(['#E84A5F', '#4FC1E9', '#FFCE54', '#A0D468', '#AC92EC']), bevel=0.06))
    _on_wall(dr, inner, 's', W / 2 - 2.2, 1.6, dr.tpl('backpacks', hooks), 0.3)
    plant = [P('box', 0, 0, 0.25, 0.5, 0.5, 0.5, '#C96F4A', bevel=0.03),
             P('box', 0, 0, 0.8, 0.7, 0.7, 0.7, '#4C9A2A', bevel=0.15), P('box', 0.1, 0, 1.25, 0.45, 0.45, 0.4,
                                                                          '#5DAE3E', bevel=0.1)]
    pk = dr.tpl('plant', plant)
    for (px, py) in ((x0 + 0.5, y1 - 0.5), (x1 - 0.5, y1 - 0.5), (x0 + 0.5, y0 + 0.5)):
        dr.put('plant', pk, px, py, 0.0, 0.0)
    # Rows of student desks with chairs facing the board; low enough to sit inside camera zones.
    chairs = ['#3D7EBF', '#E2574C', '#F2B134', '#4CAF7A']
    dk = [dr.tpl('student_desk', student_desk(rnd, c)) for c in chairs]
    yy = y1 - 3.0
    while yy > y0 + 1.0:
        xx = x0 + 1.4
        while xx < x1 - 1.0:
            dr.put('student_desk', rnd.choice(dk), xx + rnd.uniform(-0.05, 0.05), yy, 0.0, 180.0 + rnd.uniform(-4, 4),
                   gap=0.1)
            xx += 1.7
        yy -= 2.0
    lights = dr.tpl('ceiling_lights', [P('box', i * 3.0, j * 3.0, 0, 1.6, 0.6, 0.04, '#FFFBEA', 'emit')
                                       for i in range(-int(W / 6), int(W / 6) + 1)
                                       for j in range(-int(H / 6), int(H / 6) + 1)], decal=True, shadow=False)
    dr.put('ceiling_lights', lights, dr.cx, dr.cy, wall_h - 0.05, check=False)


def bedroom(dr, night):
    rnd = dr.rnd
    props = {p.get('type') for p in dr.m['setting'].get('props', [])}
    wall_h = max(3.4, dr.area['band'][1] + 0.4)
    walls = rnd.choice([('#BFD7EA', '#8FB3D5'), ('#F6D6E3', '#E3A8BF'), ('#D8F0D2', '#9CC5A1'),
                        ('#FFE9B8', '#E3B87F')])
    inner = _room(dr, 1.3, 4.5, 4.0, walls[0], walls[1], ['#B98A5E', '#A87A50', '#B3845A'], '#FAF7F2', wall_h, night)
    x0, y0, x1, y1 = inner
    W, H = x1 - x0, y1 - y0
    _on_wall(dr, inner, 'n', -W * 0.12, 0.95, dr.tpl('window', pane_window(1.8, 1.4, night, curtains=shade(walls[1], 0.8))),
             0.28)
    # Rug under the action area (flat), posters, shelves.
    # Thin (the characters stand on it): border 1 mm above the floor tiles, centre 3 mm.
    rug = [P('box', 0, 0, 0.001, 3.6, 2.6, 0.002, rnd.choice(['#E3A857', '#7FB3D5', '#C774A0', '#7CC4A0'])),
           P('box', 0, 0, 0.003, 3.1, 2.1, 0.002, rnd.choice(['#F6F0E0', '#FFE7B0', '#E9F2FA']))]
    core = dr.area['core']
    dr.put('rug', dr.tpl('rug', rug, decal=True, shadow=False), (core[0] + core[2]) / 2, (core[1] + core[3]) / 2,
           -0.001, check=False)
    for wall, along, z in (('n', W * 0.25, 1.9), ('e', 0.6, 1.8), ('w', -0.8, 1.9), ('s', 0.4, 1.9), ('s', -1.4, 1.8)):
        _on_wall(dr, inner, wall, along, z, dr.tpl('poster', poster(rnd, rnd.uniform(0.6, 0.9), rnd.uniform(0.7, 1.0)),
                                                   shadow=False), 0.03)
    shelf = [P('box', 0, 0, 0, 1.6, 0.3, 0.05, '#A0703F', bevel=0.01)]
    x = -0.7
    while x < 0.7:
        bw = rnd.uniform(0.06, 0.12)
        shelf.append(P('box', x, -0.02, 0.15, bw, 0.22, rnd.uniform(0.2, 0.28),
                       rnd.choice(['#E84A5F', '#4FC1E9', '#FFCE54', '#A0D468', '#AC92EC']), bevel=0.005))
        x += bw + 0.02
    shelf.append(P('box', 0.55, -0.02, 0.12, 0.2, 0.2, 0.2, '#FFCE54', bevel=0.04))
    _on_wall(dr, inner, 'e', -0.8, 1.55, dr.tpl('shelf', shelf), 0.3)
    _on_wall(dr, inner, 'w', 0.6, 1.45, dr.tpl('shelf', shelf), 0.3)
    _on_wall(dr, inner, 's', -0.2, 0.0, dr.tpl('bookcase', bookcase(rnd, 1.4, 1.7, '#C8A27A')), 0.4)
    wardrobe = [P('box', 0, 0, 1.0, 1.4, 0.6, 2.0, '#C8A27A', bevel=0.03),
                P('box', -0.35, -0.31, 1.0, 0.66, 0.02, 1.86, '#D6B48C'),
                P('box', 0.35, -0.31, 1.0, 0.66, 0.02, 1.86, '#D6B48C'),
                P('box', -0.06, -0.34, 1.0, 0.04, 0.04, 0.24, '#E8C547', 'gloss'),
                P('box', 0.06, -0.34, 1.0, 0.04, 0.04, 0.24, '#E8C547', 'gloss')]
    _on_wall(dr, inner, 's', W / 2 - 1.2, 0.0, dr.tpl('wardrobe', wardrobe), 0.6)
    if 'desk' not in props:
        desk = [P('box', 0, 0, 0.74, 1.5, 0.7, 0.05, '#C8A27A', bevel=0.015),
                P('box', -0.68, 0, 0.36, 0.06, 0.66, 0.72, '#B08A60'), P('box', 0.68, 0, 0.36, 0.06, 0.66, 0.72,
                                                                         '#B08A60'),
                P('box', 0, 0.1, 1.06, 0.8, 0.05, 0.5, '#22252B', 'gloss', 0.02),
                P('box', 0, 0.07, 1.06, 0.72, 0.02, 0.42, '#6EC6FF', 'glow'),
                P('box', -0.15, 0.07, 1.1, 0.25, 0.01, 0.12, '#FFCE54', 'glow'),
                P('box', 0.15, 0.07, 1.0, 0.3, 0.01, 0.1, '#A0D468', 'glow'),
                P('box', 0, 0.15, 0.8, 0.08, 0.08, 0.1, '#22252B'),
                P('box', 0, -0.15, 0.775, 0.6, 0.2, 0.02, '#3B3F47')]
        _on_wall(dr, inner, 'e', 0.9, 0.0, dr.tpl('desk', desk), 0.7)
    if 'bed' not in props:
        sheet = rnd.choice(['#4FC1E9', '#E84A5F', '#A0D468', '#AC92EC'])
        bed = [P('box', 0, 0, 0.2, 1.5, 2.3, 0.4, '#8B5A2B', bevel=0.03),
               P('box', 0, 0, 0.5, 1.4, 2.1, 0.2, '#F6F4EE', bevel=0.05),
               P('box', 0, -0.25, 0.57, 1.44, 1.5, 0.12, sheet, bevel=0.05),
               P('box', 0, 0.8, 0.66, 0.9, 0.4, 0.14, '#FFFFFF', bevel=0.06),
               P('box', 0, 1.12, 0.55, 1.5, 0.1, 1.1, '#8B5A2B', bevel=0.03)]
        _on_wall(dr, inner, 'w', -H * 0.18, 0.0, dr.tpl('bed', bed), 2.4)
    # Eye-level dressing on every wall: string lights, a pin board, a wall screen, frames, dresser, nightstand.
    bulbs = ['#FFE08A', '#FF8FA3', '#8FD3FF', '#B4F07A']
    for wall, span in (('n', W), ('e', H), ('w', H)):
        n = int((span - 0.6) / 0.45)
        lights = [P('box', 0, 0.02, 0, span - 0.4, 0.02, 0.02, '#4A4A4A')]
        lights += [P('ball', -span / 2 + 0.4 + i * 0.45, -0.03, -0.06 - 0.05 * math.sin(i * 1.3), 0.09, 0.09, 0.11,
                     bulbs[i % 4], 'emit' if night else 'glow') for i in range(n)]
        _on_wall(dr, inner, wall, 0.0, wall_h - 0.45, dr.tpl('string_lights', lights, shadow=False), 0.06)
    pins = [P('box', 0, 0, 0, 1.1, 0.04, 0.8, '#C9A27A', bevel=0.01)]
    for _ in range(7):
        pins.append(P('box', rnd.uniform(-0.42, 0.42), -0.025, rnd.uniform(-0.28, 0.28), 0.18, 0.01, 0.16,
                      rnd.choice(['#FFF59A', '#FF8FA3', '#8FD3FF', '#B4F07A', '#FFFFFF']), rz=0, ry=rnd.uniform(-8, 8)))
    _on_wall(dr, inner, 'e', -1.9, 1.5, dr.tpl('pinboard', pins, shadow=False), 0.05)
    screen = [P('box', 0, 0, 0, 1.5, 0.08, 0.9, '#22252B', 'gloss', 0.02),
              P('box', 0, -0.045, 0, 1.38, 0.01, 0.78, '#3D7EBF', 'glow'),
              P('box', -0.3, -0.05, 0.1, 0.5, 0.01, 0.3, '#A0D468', 'glow'),
              P('box', 0.35, -0.05, -0.15, 0.4, 0.01, 0.2, '#FFCE54', 'glow')]
    _on_wall(dr, inner, 'w', 1.9, 1.75, dr.tpl('wall_screen', screen, shadow=False), 0.1)
    for wall, along in (('n', W * 0.33), ('s', -W * 0.3), ('w', -2.4)):
        frame = [P('box', 0, 0, 0, 0.5, 0.04, 0.6, '#3B3F47', bevel=0.01),
                 P('box', 0, -0.025, 0, 0.38, 0.01, 0.48, rnd.choice(['#8FD3FF', '#FFCE54', '#A0D468', '#FF8FA3'])),
                 P('cyl', 0, -0.03, 0.02, 0.2, 0.2, 0.01, rnd.choice(['#FFFFFF', '#E84A5F', '#37BC9B']), rx=90)]
        _on_wall(dr, inner, wall, along, 1.35, dr.tpl('frame', frame, shadow=False), 0.05)
    dresser = [P('box', 0, 0, 0.5, 1.3, 0.5, 1.0, '#C8A27A', bevel=0.03)]
    for k in range(3):
        dresser += [P('box', 0, -0.26, 0.2 + k * 0.3, 1.18, 0.02, 0.26, '#D6B48C', bevel=0.01),
                    P('box', 0, -0.28, 0.2 + k * 0.3, 0.2, 0.03, 0.04, '#E8C547', 'gloss')]
    dresser += [P('box', -0.35, 0, 1.12, 0.3, 0.2, 0.24, rnd.choice(['#E84A5F', '#4FC1E9']), bevel=0.03),
                P('box', 0.3, 0.05, 1.06, 0.4, 0.3, 0.12, '#FFCE54', bevel=0.02)]
    _on_wall(dr, inner, 'n', W * 0.33, 0.0, dr.tpl('dresser', dresser), 0.5)
    stand = [P('box', 0, 0, 0.3, 0.5, 0.45, 0.6, '#C8A27A', bevel=0.02),
             P('cyl', 0, 0, 0.7, 0.08, 0.08, 0.2, '#3B3F47'), P('cyl', 0, 0, 0.88, 0.32, 0.32, 0.22, '#FFE7B0', 'glow')]
    _on_wall(dr, inner, 'w', -H * 0.18 + 1.6, 0.0, dr.tpl('nightstand', stand), 0.45)
    lamp = [P('cyl', 0, 0, 0.03, 0.4, 0.4, 0.06, '#3B3F47'), P('cyl', 0, 0, 0.8, 0.05, 0.05, 1.5, '#3B3F47'),
            P('cyl', 0, 0, 1.62, 0.45, 0.45, 0.35, '#FFE7B0', 'glow')]
    lk = dr.tpl('floor_lamp', lamp)
    for px, py in ((x1 - 0.5, y1 - 0.5), (x0 + 0.5, y1 - 0.5)):
        if dr.put('floor_lamp', lk, px, py, 0.0, 0.0) and night:
            dr.lights.append({'type': 'POINT', 'pos': [_r(px), _r(py), 1.6], 'energy': 60, 'color': '#FFD9A0'})
    plant = [P('box', 0, 0, 0.2, 0.4, 0.4, 0.4, '#C96F4A', bevel=0.03),
             P('box', 0, 0, 0.62, 0.55, 0.55, 0.5, '#4C9A2A', bevel=0.12)]
    dr.put('plant', dr.tpl('plant', plant), x0 + 0.5, y0 + 0.5, 0.0, 0.0)
    toys = [P('box', 0, 0, 0.25, 1.0, 0.55, 0.5, '#E84A5F', bevel=0.04), P('box', 0, 0, 0.53, 1.04, 0.59, 0.08,
                                                                           '#FFCE54', bevel=0.02)]
    dr.scatter('toy_box', dr.tpl('toy_box', toys), 2, -1.0, 0.6, gap=0.3)
    bean = [P('box', 0, 0, 0.25, 0.9, 0.9, 0.5, rnd.choice(['#AC92EC', '#4FC1E9', '#FC6E51']), bevel=0.2)]
    dr.scatter('beanbag', dr.tpl('beanbag', bean), 1, -1.0, 0.6, gap=0.3)
    dr.put('ceiling_light', dr.tpl('ceiling_light', [P('cyl', 0, 0, 0, 0.8, 0.8, 0.06, '#FFFBEA', 'emit')],
                                   decal=True, shadow=False), dr.cx, dr.cy, wall_h - 0.05, check=False)


def studio(dr, night):
    """Plain seamless backdrop all around (kept simple on purpose: it is used for tests and turnarounds)."""
    c = dr.area['clear']
    r = max(6.0, 0.5 * math.hypot(c[2] - c[0], c[3] - c[1]) + 1.0)
    col = GROUND_HEX['studio']
    dr.put('floor', dr.tpl('floor', [P('box', 0, 0, -0.1, 2 * r + 30, 2 * r + 30, 0.2, col)], decal=True,
                           shadow=False), dr.cx, dr.cy, -0.2, check=False)
    n = 28
    seg_w = 2 * math.pi * (r + 0.3) / n + 0.2
    wall = dr.tpl('cyc', [P('box', 0, 0, 4.5, seg_w, 0.3, 9.0, col),
                          P('box', 0, -0.25, 0.25, seg_w, 0.5, 0.5, col, rx=45)], shadow=False)
    for i in range(n):
        a = 2 * math.pi * i / n
        dr.put('cyc', wall, dr.cx + (r + 0.15) * math.cos(a), dr.cy + (r + 0.15) * math.sin(a), 0.0,
               math.degrees(a) + 90, check=False)


# ---------------------------------------------------------------- home (family and sibling skits)
# A ground-floor interior: the living room is built around the clearance box like the other rooms, an open
# archway in its south wall leads to a hallway with the front door, an entry mat and a staircase up to a
# landing, and an opening in the east wall shows a kitchen corner (counter, fridge). Characters walk the
# ground floor (the validator treats the whole preset as supported ground); the stairs and landing are
# scenery beyond the walls, never a surface the plan can stand a character on.
HOME_WALLS = [('#F4EADB', '#D8C2A4'), ('#E9EFF3', '#B7C9D6'), ('#F7E8D3', '#E2B98B'), ('#EAF1E4', '#B3CBA2'),
              ('#F3E5EA', '#D6B2C0')]
HOME_FLOORS = [('#B98A5E', '#AD7F55'), ('#C9A074', '#BD9268'), ('#8F6540', '#866040')]
SOFAS = ['#4A6FA5', '#6B8E5A', '#B5533C', '#8E6BAF', '#C98A3C', '#4F8FA3']
ACCENTS = ['#E84A5F', '#FFCE54', '#4FC1E9', '#A0D468', '#AC92EC', '#FC6E51', '#F6F7FB']
WINDOW_SKY = {'morning': '#F6D9BE', 'noon': '#9FD3F0', 'sunset': '#FFB070', 'night': '#1B2440'}
# Light spilling in through each window: colour and energy by time of day (a point light just inside it).
WINDOW_LIGHT = {'morning': ('#FFD9B3', 160.0), 'noon': ('#FFF7EB', 190.0), 'sunset': ('#FF9E61', 170.0),
                'night': ('#7F93D1', 40.0)}
HALL_DEPTH = 2.8
KITCHEN_DEPTH = 3.2
KITCHEN_HALF_W = 2.4
WALL_T = 0.3
STAIR_STEPS, STAIR_TREAD, STAIR_RISE, STAIR_W = 10, 0.27, 0.19, 0.95


def view_window(rnd, w, h, tod, trim='#FFFFFF', curtains=None, rooftops=False):
    """Framed window whose pane carries a painted outside view (a garden, or rooftops) tinted by the time of
    day. The pane material is opaque, so the 'outside' is drawn on it in layers at distinct depths (sky,
    ground, far, mid, near) that never share a plane."""
    night = tod == 'night'
    sky = WINDOW_SKY.get(tod, WINDOW_SKY['noon'])
    parts = [P('box', 0, 0.02, 0, w + 0.24, 0.1, h + 0.24, trim, bevel=0.02),
             P('box', 0, -0.035, 0, w, 0.01, h, sky, 'pane')]
    SKYD, FAR, MID, NEAR = -0.0445, -0.0505, -0.0565, -0.0625
    gh = h * 0.28
    horizon = -h / 2 + gh
    grass = '#2C4F33' if night else '#7CC46A'
    hedge = '#1F3A2A' if night else '#4C9A2A'
    parts.append(P('box', 0, SKYD, -h / 2 + gh / 2, w, 0.005, gh, grass, 'pane'))
    if rooftops:
        x = -w / 2 + 0.04
        while x < w / 2 - 0.3:
            bw = min(rnd.uniform(0.32, 0.55), w / 2 - x - 0.02)
            bh = rnd.uniform(0.22, 0.42) * h
            col = rnd.choice(['#2E3A59', '#3D4A6B', '#2A3350']) if night else jitter(rnd.choice(HOUSE_WALLS), rnd)
            roof = '#1E2538' if night else rnd.choice(ROOFS)
            parts.append(P('box', x + bw / 2, FAR, horizon + bh / 2 - 0.02, bw, 0.005, bh, col, 'pane'))
            parts.append(P('pyr', x + bw / 2, FAR, horizon + bh - 0.02 + bh * 0.2, bw + 0.06, 0.005, bh * 0.4, roof,
                           'pane'))
            if night:
                parts.append(P('box', x + bw / 2, MID, horizon + bh * 0.45, bw * 0.3, 0.005, bh * 0.25, '#FFD27A',
                               'emit'))
            x += bw + rnd.uniform(0.04, 0.12)
    else:
        parts.append(P('box', 0, FAR, horizon + h * 0.04, w, 0.005, h * 0.08, hedge, 'pane'))
        hx = rnd.choice([-1, 1]) * w * 0.25
        hw, hh = w * 0.3, h * 0.22
        wall = '#2E3A59' if night else jitter(rnd.choice(HOUSE_WALLS), rnd)
        parts += [P('box', hx, MID, horizon + hh / 2 + 0.01, hw, 0.005, hh, wall, 'pane'),
                  P('pyr', hx, MID, horizon + hh + 0.01 + hh * 0.3, hw + 0.08, 0.005, hh * 0.6,
                    '#1E2538' if night else rnd.choice(ROOFS), 'pane'),
                  P('box', hx, NEAR, horizon + hh * 0.45, hw * 0.25, 0.005, hh * 0.3, '#FFD27A' if night else '#A7D8F2',
                    'emit' if night else 'pane')]
        tx = -hx * 1.4
        th = h * 0.45
        parts += [P('box', tx, NEAR, horizon + th * 0.25, w * 0.05, 0.005, th * 0.5, TRUNK, 'pane'),
                  P('cyl', tx, NEAR, horizon + th * 0.68, w * 0.32, w * 0.32, 0.005,
                    '#24422B' if night else rnd.choice(LEAVES), 'pane', rx=90)]
    if night:
        parts.append(P('cyl', -w * 0.3, SKYD, h * 0.3, h * 0.16, h * 0.16, 0.005, '#FFF4D6', 'emit', rx=90))
        for _ in range(5):
            parts.append(P('box', rnd.uniform(-w * 0.45, w * 0.45), SKYD, rnd.uniform(h * 0.1, h * 0.45), 0.02, 0.005,
                           0.02, '#FFFFFF', 'emit'))
    else:
        if tod == 'sunset':
            parts.append(P('cyl', w * 0.28, SKYD, horizon + h * 0.14, h * 0.2, h * 0.2, 0.005, '#FFD27A', 'emit',
                           rx=90))
        for i in range(2):
            cx = (-1 if i else 1) * rnd.uniform(0.15, 0.35) * w
            cz = rnd.uniform(0.15, 0.4) * h
            parts += [P('cyl', cx, SKYD, cz, w * 0.26, h * 0.12, 0.005, '#FFFFFF', 'pane', rx=90),
                      P('cyl', cx + w * 0.08, SKYD, cz + h * 0.04, w * 0.16, h * 0.1, 0.005, '#FFFFFF', 'pane',
                        rx=90)]
    parts += [P('box', 0, -0.07, 0, 0.07, 0.04, h, trim), P('box', 0, -0.07, 0, w, 0.04, 0.07, trim),
              P('box', 0, -0.1, -h / 2 - 0.08, w + 0.4, 0.22, 0.08, trim, bevel=0.02)]
    if curtains:
        for s in (-1, 1):
            parts.append(P('box', s * (w / 2 + 0.16), -0.14, 0.05, 0.36, 0.1, h + 0.4, curtains, bevel=0.03))
        parts.append(P('box', 0, -0.14, h / 2 + 0.3, w + 0.95, 0.06, 0.06, '#8B8F98'))
    return parts


def wall_with_openings(w, h, t, col, low_col, openings=(), skirting=True):
    """Parts of a wall (front facing -y, x along the wall, centred) with doorway openings cut from the floor:
    openings are (x_from, x_to, z_top). Solid segments carry the wainscot and skirting."""
    parts = []
    edges = sorted(openings)
    x = -w / 2
    for a, b, ztop in edges:
        seg = a - x
        if seg > 0.01:
            cx = x + seg / 2
            parts.append(P('box', cx, 0, h / 2, seg, t, h, col))
            if skirting:
                parts += [P('box', cx, -t / 2 - 0.01, 0.55, seg, 0.02, 1.1, low_col),
                          P('box', cx, -t / 2 - 0.03, 1.12, seg, 0.05, 0.07, shade(low_col, 0.85)),
                          P('box', cx, -t / 2 - 0.03, 0.06, seg, 0.06, 0.12, '#FFFFFF')]
        parts.append(P('box', (a + b) / 2, 0, (ztop + h) / 2, b - a, t, h - ztop, col))
        parts.append(P('box', (a + b) / 2, -t / 2 - 0.02, ztop + 0.05, b - a + 0.16, 0.08, 0.1, '#FFFFFF', bevel=0.01))
        x = b
    seg = w / 2 - x
    if seg > 0.01:
        cx = x + seg / 2
        parts.append(P('box', cx, 0, h / 2, seg, t, h, col))
        if skirting:
            parts += [P('box', cx, -t / 2 - 0.01, 0.55, seg, 0.02, 1.1, low_col),
                      P('box', cx, -t / 2 - 0.03, 1.12, seg, 0.05, 0.07, shade(low_col, 0.85)),
                      P('box', cx, -t / 2 - 0.03, 0.06, seg, 0.06, 0.12, '#FFFFFF')]
    if skirting:
        parts.append(P('box', 0, -t / 2 - 0.03, h - 0.07, w, 0.06, 0.14, '#FFFFFF'))   # cornice
    return parts


def sofa_parts(col, w=2.2, accents=('#FFCE54', '#4FC1E9')):
    """Low sofa, front facing -y; everything stays under LOW_TOP so it can sit near a camera."""
    base = shade(col, 0.82)
    parts = [P('box', 0, 0.02, 0.2, w, 0.92, 0.4, base, bevel=0.05),
             P('box', 0, 0.33, 0.57, w, 0.3, 0.42, col, bevel=0.06),
             P('box', -w / 2 + 0.14, -0.04, 0.5, 0.28, 0.84, 0.28, col, bevel=0.06),
             P('box', w / 2 - 0.14, -0.04, 0.5, 0.28, 0.84, 0.28, col, bevel=0.06)]
    n = 2 if w < 2.0 else 3
    cw = (w - 0.56) / n
    for i in range(n):
        parts.append(P('box', -w / 2 + 0.28 + cw * (i + 0.5), -0.1, 0.47, cw - 0.04, 0.7, 0.14, shade(col, 1.08),
                       bevel=0.05))
    parts += [P('box', -w / 2 + 0.6, 0.12, 0.6, 0.4, 0.14, 0.36, accents[0], bevel=0.06, rz=8),
              P('box', w / 2 - 0.6, 0.12, 0.6, 0.4, 0.14, 0.36, accents[1], bevel=0.06, rz=-6)]
    for sx in (-1, 1):
        for sy in (-1, 1):
            parts.append(P('box', sx * (w / 2 - 0.12), sy * 0.38, 0.03, 0.08, 0.08, 0.06, '#3B3F47'))
    return parts


def armchair_parts(col, accent):
    return sofa_parts(col, 1.1, (accent, accent))[:-6] + [
        P('box', 0, 0.12, 0.6, 0.5, 0.14, 0.36, accent, bevel=0.06)]


def coffee_table_parts(rnd, wood):
    parts = [P('box', 0, 0, 0.4, 1.2, 0.6, 0.05, wood, bevel=0.015),
             P('box', 0, 0, 0.12, 1.0, 0.44, 0.04, shade(wood, 0.9))]
    for sx in (-1, 1):
        for sy in (-1, 1):
            parts.append(P('box', sx * 0.52, sy * 0.22, 0.2, 0.06, 0.06, 0.38, shade(wood, 0.8)))
    parts += [P('cyl', -0.25, 0.05, 0.45, 0.3, 0.3, 0.05, rnd.choice(ACCENTS)),
              P('box', 0.3, -0.05, 0.44, 0.3, 0.22, 0.03, rnd.choice(ACCENTS), rz=rnd.uniform(-20, 20)),
              P('box', 0.3, -0.05, 0.47, 0.26, 0.2, 0.03, rnd.choice(ACCENTS), rz=rnd.uniform(-20, 20)),
              P('cyl', 0.0, 0.18, 0.46, 0.09, 0.09, 0.08, '#FFFFFF', 'gloss')]
    return parts


def tv_wall_parts(rnd, wood, w=1.8, h=1.0):
    """Wall screen showing abstract shapes only, on a low media unit. Screen local -y faces the room."""
    parts = [P('box', 0, 0, 0.26, 2.2, 0.45, 0.52, wood, bevel=0.02),
             P('box', -0.55, -0.23, 0.26, 1.0, 0.02, 0.4, shade(wood, 1.1)),
             P('box', 0.55, -0.23, 0.26, 1.0, 0.02, 0.4, shade(wood, 1.1)),
             P('box', 0, -0.25, 0.26, 0.04, 0.02, 0.2, '#B9BCC4', 'gloss'),
             P('box', -0.75, 0.0, 0.6, 0.3, 0.2, 0.12, '#22252B', 'gloss', 0.02),
             P('box', 0.8, 0.0, 0.62, 0.22, 0.22, 0.2, rnd.choice(ACCENTS), bevel=0.04),
             P('box', 0, 0.12, 0.55 + h / 2 + 0.45, w, 0.08, h, '#22252B', 'gloss', 0.02),
             P('box', 0, 0.075, 0.55 + h / 2 + 0.45, w - 0.12, 0.01, h - 0.12, '#1F3A6E', 'glow')]
    zc = 0.55 + h / 2 + 0.45
    for i in range(rnd.randint(3, 5)):
        s = rnd.uniform(0.12, 0.3)
        x, z = rnd.uniform(-w / 2 + 0.3, w / 2 - 0.3), zc + rnd.uniform(-h / 2 + 0.25, h / 2 - 0.25)
        col = rnd.choice(ACCENTS)
        y = 0.068 - i * 0.002
        if rnd.random() < 0.5:
            parts.append(P('box', x, y, z, s * 1.4, 0.005, s, col, 'glow'))
        else:
            parts.append(P('cyl', x, y, z, s, s, 0.005, col, 'glow', rx=90))
    return parts


def picture_frame(rnd, w, h):
    frame = rnd.choice(['#3B3F47', '#8B5A2B', '#F6F4EE', '#C8A27A'])
    parts = [P('box', 0, 0, 0, w, 0.04, h, frame, bevel=0.01),
             P('box', 0, -0.025, 0, w - 0.1, 0.01, h - 0.1, rnd.choice(['#FFFFFF', '#FFF3C4', '#D7ECFF', '#E2F7D7']))]
    for i in range(rnd.randint(1, 3)):
        s = rnd.uniform(0.2, 0.45) * min(w, h)
        x, z = rnd.uniform(-w / 3, w / 3), rnd.uniform(-h / 3, h / 3)
        col = rnd.choice(ACCENTS[:-1])
        if rnd.random() < 0.5:
            parts.append(P('cyl', x, -0.032 - i * 0.002, z, s, s, 0.005, col, rx=90))
        else:
            parts.append(P('box', x, -0.032 - i * 0.002, z, s * 1.3, 0.005, s, col))
    return parts


def plant_parts(rnd, tall=False):
    pot = rnd.choice(['#C96F4A', '#E8E2D8', '#5A6B7A', '#D9B98C'])
    if tall:
        parts = [P('box', 0, 0, 0.25, 0.5, 0.5, 0.5, pot, bevel=0.03), P('cyl', 0, 0, 0.85, 0.08, 0.08, 0.8, '#5A7A3A')]
        for i in range(5):
            a = i * 72 + rnd.uniform(-15, 15)
            parts.append(P('box', 0.3 * math.cos(math.radians(a)), 0.3 * math.sin(math.radians(a)), 1.05 + i * 0.1,
                           0.62, 0.3, 0.05, rnd.choice(['#4C9A2A', '#5DAE3E', '#3F8A55']), bevel=0.02, rz=a,
                           ry=-25))
        return parts
    return [P('box', 0, 0, 0.2, 0.4, 0.4, 0.4, pot, bevel=0.03),
            P('box', 0, 0, 0.62, 0.55, 0.55, 0.5, '#4C9A2A', bevel=0.12),
            P('box', 0.12, -0.05, 0.9, 0.35, 0.35, 0.3, '#5DAE3E', bevel=0.1)]


def floor_lamp_parts(shade_col, night):
    return [P('cyl', 0, 0, 0.03, 0.4, 0.4, 0.06, '#3B3F47'), P('cyl', 0, 0, 0.8, 0.05, 0.05, 1.5, '#3B3F47'),
            P('cyl', 0, 0, 1.62, 0.45, 0.45, 0.35, shade_col, 'emit' if night else 'glow')]


def side_table_parts(wood, shade_col, night):
    return [P('box', 0, 0, 0.52, 0.5, 0.5, 0.04, wood, bevel=0.01),
            P('box', -0.2, -0.2, 0.25, 0.05, 0.05, 0.5, wood), P('box', 0.2, -0.2, 0.25, 0.05, 0.05, 0.5, wood),
            P('box', -0.2, 0.2, 0.25, 0.05, 0.05, 0.5, wood), P('box', 0.2, 0.2, 0.25, 0.05, 0.05, 0.5, wood),
            P('cyl', 0, 0, 0.6, 0.16, 0.16, 0.12, '#3B3F47'), P('cyl', 0, 0, 0.8, 0.04, 0.04, 0.3, '#3B3F47'),
            P('cyl', 0, 0, 1.06, 0.34, 0.34, 0.26, shade_col, 'emit' if night else 'glow')]


def wall_shelf_parts(rnd, wood):
    parts = [P('box', 0, 0, 0, 1.4, 0.26, 0.04, wood, bevel=0.01),
             P('box', -0.5, -0.02, 0.17, 0.2, 0.2, 0.3, '#C96F4A', bevel=0.02),
             P('box', -0.5, -0.02, 0.42, 0.34, 0.3, 0.22, '#5DAE3E', bevel=0.08),
             P('box', 0.45, -0.02, 0.16, 0.36, 0.03, 0.28, '#3B3F47'),
             P('box', 0.45, -0.04, 0.16, 0.3, 0.01, 0.22, rnd.choice(ACCENTS))]
    x = -0.2
    while x < 0.2:
        bw = rnd.uniform(0.05, 0.09)
        parts.append(P('box', x, -0.01, 0.14, bw, 0.2, rnd.uniform(0.18, 0.26), rnd.choice(ACCENTS), bevel=0.005))
        x += bw + 0.015
    return parts


def front_door_parts(col, night):
    return [P('box', 0, 0.02, 1.1, 1.3, 0.08, 2.25, '#FFFFFF', bevel=0.02),
            P('box', 0, -0.03, 1.05, 1.1, 0.06, 2.1, col, bevel=0.02),
            P('box', -0.3, -0.07, 0.6, 0.36, 0.02, 0.7, shade(col, 0.9), bevel=0.01),
            P('box', 0.3, -0.07, 0.6, 0.36, 0.02, 0.7, shade(col, 0.9), bevel=0.01),
            P('box', -0.3, -0.07, 1.5, 0.36, 0.02, 0.7, shade(col, 0.9), bevel=0.01),
            P('box', 0.3, -0.07, 1.5, 0.36, 0.02, 0.7, shade(col, 0.9), bevel=0.01),
            P('box', 0, -0.07, 1.95, 0.8, 0.02, 0.2, '#1B2440' if night else '#9FD3F0', 'pane'),
            P('box', 0.42, -0.1, 1.0, 0.1, 0.05, 0.05, '#E8C547', 'gloss'),
            P('box', 0.42, -0.1, 1.12, 0.04, 0.04, 0.1, '#E8C547', 'gloss')]


def coat_rack_parts(rnd):
    parts = [P('box', 0, 0, 0, 1.4, 0.06, 0.1, '#8B5A2B')]
    for i in range(4):
        x = -0.5 + i * 0.34
        parts.append(P('box', x, -0.06, -0.02, 0.04, 0.08, 0.04, '#B9BCC4', 'gloss'))
        if rnd.random() < 0.8:
            parts.append(P('box', x, -0.14, -0.42, 0.3, 0.16, 0.76, rnd.choice(ACCENTS[:-1] + ['#3B3F47']), bevel=0.05))
    return parts


def shoe_rack_parts(rnd):
    parts = [P('box', 0, 0, 0.16, 0.9, 0.32, 0.32, '#A0703F', bevel=0.01),
             P('box', 0, -0.02, 0.17, 0.86, 0.3, 0.03, shade('#A0703F', 0.85))]
    for i in range(3):
        parts.append(P('box', -0.3 + i * 0.3, -0.03, 0.38, 0.11, 0.26, 0.1, rnd.choice(ACCENTS[:-1]), bevel=0.03))
    return parts


def stairs_parts(wood, carpet, rail='#F6F4EE'):
    """Closed-riser staircase rising along +x to a landing, handrail and balusters on the open (-y) side.
    Every tread top sits at its own height, so nothing shares a plane."""
    n, tr, rs, w = STAIR_STEPS, STAIR_TREAD, STAIR_RISE, STAIR_W
    parts = []
    for k in range(n):
        x, z = k * tr + tr / 2, (k + 1) * rs
        parts += [P('box', x, 0, z / 2, tr, w, z, wood, bevel=0.01),
                  P('box', x, 0.05, z + 0.005, tr + 0.01, w * 0.55, 0.008, carpet),
                  P('box', x, -w / 2 + 0.06, z + 0.45, 0.04, 0.04, 0.9, rail)]
    L = 1.1
    xl, zl = n * tr + L / 2, (n + 1) * rs     # the landing is one riser above the last tread
    parts += [P('box', xl, 0, zl / 2, L, w, zl, wood, bevel=0.01),
              P('box', xl, 0.05, zl + 0.005, L - 0.05, w * 0.55, 0.008, carpet),
              P('box', xl, -w / 2 + 0.06, zl + 0.5, L, 0.06, 0.06, shade(wood, 0.8)),
              P('box', xl, -w / 2 + 0.06, zl + 0.95, L, 0.06, 0.06, shade(wood, 0.8))]
    for k in range(3):
        parts.append(P('box', n * tr + 0.2 + k * 0.35, -w / 2 + 0.06, zl + 0.45, 0.04, 0.04, 0.9, rail))
    run = n * tr
    ang = math.degrees(math.atan2(zl, run))
    parts.append(P('box', run / 2, -w / 2 + 0.06, zl / 2 + 0.95, math.hypot(run, zl) + 0.1, 0.07, 0.07,
                   shade(wood, 0.8), ry=-ang))
    for x, z in ((0.05, 0.0), (run, zl)):
        parts += [P('box', x, -w / 2 + 0.06, z + 0.55, 0.12, 0.12, 1.1, shade(wood, 0.8), bevel=0.02),
                  P('ball', x, -w / 2 + 0.06, z + 1.17, 0.18, 0.18, 0.18, shade(wood, 0.8))]
    return parts


def kitchen_counter_parts(rnd, L, door_col):
    top = '#D9DCE3'
    parts = [P('box', 0, 0, 0.44, L, 0.6, 0.88, '#EDEDE8', bevel=0.01),
             P('box', 0, 0, 0.905, L + 0.04, 0.64, 0.04, top, 'gloss', 0.01)]
    n = max(2, int(L / 0.6))
    dw = (L - 0.1) / n
    for i in range(n):
        x = -L / 2 + 0.05 + dw * (i + 0.5)
        parts += [P('box', x, -0.31, 0.44, dw - 0.04, 0.02, 0.8, door_col, bevel=0.01),
                  P('box', x, -0.335, 0.62, 0.16, 0.02, 0.03, '#B9BCC4', 'gloss')]
    sx, hx = 0.0, L / 4
    parts += [P('box', sx, 0.02, 0.93, 0.6, 0.42, 0.012, '#AEB4BC', 'gloss'),
              P('box', sx, 0.24, 1.03, 0.03, 0.03, 0.2, '#C9CDD3', 'gloss'),
              P('box', sx, 0.15, 1.13, 0.03, 0.2, 0.03, '#C9CDD3', 'gloss')]
    for ax in (-1, 1):
        for ay in (-1, 1):
            parts.append(P('cyl', hx + ax * 0.15, ay * 0.13, 0.932, 0.19, 0.19, 0.012, '#23252B', 'gloss'))
    bx = -L / 3
    parts += [P('cyl', bx, -0.02, 0.955, 0.3, 0.3, 0.06, '#E8C547'),
              P('ball', bx - 0.05, -0.05, 1.02, 0.1, 0.1, 0.1, '#E84A5F'), P('ball', bx + 0.05, 0.02, 1.02, 0.1, 0.1, 0.1,
                                                                            '#A0D468'),
              P('ball', bx, 0.04, 1.1, 0.09, 0.09, 0.09, '#FFCE54'),
              P('cyl', bx - 0.5, 0.1, 1.0, 0.1, 0.1, 0.16, rnd.choice(ACCENTS), 'gloss')]
    return parts


def upper_cabinets_parts(L, door_col):
    parts = [P('box', 0, 0, 0.35, L, 0.36, 0.7, '#EDEDE8', bevel=0.01)]
    n = max(2, int(L / 0.6))
    dw = (L - 0.1) / n
    for i in range(n):
        x = -L / 2 + 0.05 + dw * (i + 0.5)
        parts += [P('box', x, -0.19, 0.35, dw - 0.04, 0.02, 0.62, door_col, bevel=0.01),
                  P('box', x, -0.215, 0.12, 0.16, 0.02, 0.03, '#B9BCC4', 'gloss')]
    return parts


def fridge_parts(col='#E6E9EE'):
    return [P('box', 0, 0, 0.95, 0.76, 0.72, 1.9, col, 'gloss', 0.03),
            P('box', 0, -0.365, 1.35, 0.76, 0.01, 0.015, shade(col, 0.75)),
            P('box', -0.3, -0.39, 1.0, 0.03, 0.04, 0.6, '#B9BCC4', 'gloss'),
            P('box', -0.3, -0.39, 1.6, 0.03, 0.04, 0.4, '#B9BCC4', 'gloss'),
            P('box', 0.2, -0.37, 1.55, 0.14, 0.01, 0.12, '#FFCE54'), P('box', 0.05, -0.37, 1.7, 0.1, 0.01, 0.1, '#4FC1E9')]


def _hang(dr, inner, wall, along, z, kind, parts, **tpl_kw):
    """Register parts and hang them on a wall of the inner box, with the template's own depth."""
    key = dr.tpl(kind, parts, **tpl_kw)
    return _on_wall(dr, inner, wall, along, z, key, dr.templates[key]['size'][1], kind)


def home(dr, night):
    """Living room around the action, an open archway south to a hallway (front door, entry mat, stairs to a
    landing), an opening east into a kitchen corner (counter, fridge). Windows carry painted views and spill
    time-of-day light; at night the lamps, the ceiling lights and the screen take over."""
    rnd = dr.rnd
    tod = dr.m['setting'].get('time_of_day', 'noon')
    tod = tod if tod in WINDOW_SKY else 'noon'
    props = {p.get('type') for p in dr.m['setting'].get('props', [])}
    wall_h = max(3.4, dr.area['band'][1] + 0.4)
    wall_col, low_col = rnd.choice(HOME_WALLS)
    planks = rnd.choice(HOME_FLOORS)
    wood = rnd.choice(['#A0703F', '#C8A27A', '#8B5A2B', '#D8B48A'])
    sofa_col = rnd.choice(SOFAS)
    curtain = rnd.choice(['#E3B87F', '#9CC5A1', '#D6B2C0', '#B7C9D6', '#F2B5B5'])
    t = WALL_T
    c = dr.area['clear']
    band = 1.4
    hx = max((c[2] - c[0]) / 2 + band, 4.6)
    hy = max((c[3] - c[1]) / 2 + band, 4.2)
    cx, cy = dr.cx, dr.cy
    inner = [cx - hx, cy - hy, cx + hx, cy + hy]
    x0, y0, x1, y1 = inner
    W, H = 2 * hx, 2 * hy
    # Rooms beyond the living room walls: hall to the south (through the arch), kitchen to the east.
    yh1, yh0 = y0 - t, y0 - t - HALL_DEPTH
    xk0, xk1 = x1 + t, x1 + t + KITCHEN_DEPTH
    ky0, ky1 = cy - KITCHEN_HALF_W, cy + KITCHEN_HALF_W
    hall = [x0, yh0, x1, yh1]
    kitchen = [xk0, ky0, xk1, ky1]
    arch = (cx - 1.4, cx + 1.4, 2.3)
    kdoor = (cy - 0.9, cy + 1.1, 2.2)
    # Floors: one slab under the house, planks in the living room, tiles in the hall and the kitchen, all
    # flush with z = 0 and never overlapping each other (they meet under the walls).
    fx0, fy0, fx1, fy1 = x0 - t - 1.0, yh0 - t - 1.0, xk1 + t + 1.0, y1 + t + 1.0
    dr.put('floor', dr.tpl('floor', [P('box', 0, 0, -0.1, fx1 - fx0, fy1 - fy0, 0.2, shade(planks[0], 0.7))],
                           decal=True, shadow=False), (fx0 + fx1) / 2, (fy0 + fy1) / 2, SLAB_TOP - 0.2, check=False)
    rows = []
    pw = 0.22
    ny = int(H / pw) + 1
    for j in range(ny):
        y = -H / 2 + pw * (j + 0.5)
        length = W
        rows.append(P('box', 0, y, 0.004, length, pw - 0.01, 0.008, planks[j % 2]))
        rows.append(P('box', rnd.uniform(-W / 3, W / 3), y, 0.006, 0.02, pw - 0.01, 0.008, shade(planks[0], 0.8)))
    rows = [r for r in rows if r[2] <= H / 2 - pw * 0.5 + 1e-6]
    dr.put('planks', dr.tpl('planks', rows, decal=True, shadow=False), cx, cy, -0.008, check=False)
    dr.put('threshold', dr.tpl('threshold', [P('box', 0, 0, 0.004, arch[1] - arch[0] + 0.3, t, 0.008, planks[1])],
                               decal=True, shadow=False), cx, (y0 + yh1) / 2, -0.008, check=False)

    def tiles(x0_, y0_, x1_, y1_, cols, step=0.6):
        out = []
        nx, ny_ = int((x1_ - x0_) / step), int((y1_ - y0_) / step)
        sx, sy = (x1_ - x0_) / max(nx, 1), (y1_ - y0_) / max(ny_, 1)
        for i in range(nx):
            for j in range(ny_):
                out.append(P('box', -(x1_ - x0_) / 2 + sx * (i + 0.5), -(y1_ - y0_) / 2 + sy * (j + 0.5), 0.004,
                             sx - 0.01, sy - 0.01, 0.008, cols[(i + j) % 2]))
        return out
    dr.put('halltiles', dr.tpl('halltiles', tiles(*hall, ('#D9D4C8', '#C9C2B4')), decal=True, shadow=False),
           (hall[0] + hall[2]) / 2, (hall[1] + hall[3]) / 2, -0.008, check=False)
    dr.put('kitchentiles', dr.tpl('kitchentiles', tiles(xk0 - t, ky0, xk1, ky1, ('#EDEDE8', '#B9CBD8'), 0.5),
                                  decal=True, shadow=False), (xk0 - t + xk1) / 2, cy, -0.008, check=False)
    # Walls (none casts shadows so the key light reaches in): the living room's south wall has the arch, its
    # east wall the kitchen opening; the hall and the kitchen close with plain walls.
    def wall(name, x, y, w, rot, openings=(), skirt=True):
        parts = wall_with_openings(w, wall_h, t, wall_col, low_col, openings, skirt)
        dr.put('wall', dr.tpl('wall', parts, shadow=False), x, y, 0.0, rot, check=False)
    wall('n', cx, y1 + t / 2, W + 2 * t, 0.0)
    # South wall: openings are given in the wall's local x, which for rot 180 runs from +x to -x.
    wall('s', cx, y0 - t / 2, W + 2 * t, 180.0, [(cx - arch[1], cx - arch[0], arch[2])])
    wall('w', x0 - t / 2, (yh0 + y1) / 2, y1 - yh0 + 2 * t, 90.0)
    # East wall (rot -90): local x runs from +y to -y, centred on the wall's middle.
    ymid = (yh0 + y1) / 2
    wall('e', x1 + t / 2, ymid, y1 - yh0 + 2 * t, -90.0, [(ymid - kdoor[1], ymid - kdoor[0], kdoor[2])])
    wall('hall_s', cx, yh0 - t / 2, W + 2 * t, 180.0)
    wall('kitchen_n', (xk0 + xk1) / 2, ky1 + t / 2, KITCHEN_DEPTH + t, 0.0, skirt=False)
    wall('kitchen_s', (xk0 + xk1) / 2, ky0 - t / 2, KITCHEN_DEPTH + t, 180.0, skirt=False)
    wall('kitchen_e', xk1 + t / 2, cy, 2 * KITCHEN_HALF_W + 2 * t, -90.0, skirt=False)
    dr.put('ceiling', dr.tpl('ceiling', [P('box', 0, 0, 0.1, fx1 - fx0 - 1.4, fy1 - fy0 - 1.4, 0.2, '#F1ECE3')],
                             shadow=False), (fx0 + fx1) / 2, (fy0 + fy1) / 2, wall_h, check=False)
    # Exposed beams across the living room ceiling (above the camera band by construction, so unchecked).
    beam = dr.tpl('beam', [P('box', 0, 0, 0.07, W + 0.2, 0.18, 0.14, mix(shade(wood, 0.9), '#F1ECE3', 0.3), bevel=0.02)],
                  shadow=False)
    nb = max(3, int(H / 2.2))
    for i in range(nb):
        dr.put('beam', beam, cx, y0 + H * (i + 0.5) / nb, wall_h - 0.16, 0.0, check=False, solid=False)
    # Living room: the screen wall (north), windows west and north, the sofa corner south-west of the arch.
    _hang(dr, inner, 'n', 0.0, 0.0, 'tv', tv_wall_parts(rnd, wood))
    _hang(dr, inner, 'n', -W * 0.27, 1.0, 'window', view_window(rnd, 1.8, 1.4, tod, curtains=curtain))
    _hang(dr, inner, 'n', W * 0.3, 0.0, 'bookcase', bookcase(rnd, 1.6, 1.9, wood))
    _hang(dr, inner, 'n', 1.3, 1.75, 'frame', picture_frame(rnd, 0.55, 0.65), shadow=False)
    _hang(dr, inner, 'n', W * 0.3, 2.4, 'shelf', wall_shelf_parts(rnd, wood))
    _hang(dr, inner, 'n', 0.0, 2.45, 'shelf', wall_shelf_parts(rnd, wood))
    drop = max(0.6, wall_h - 2.1)
    hanging = [P('cyl', 0, 0, 0.8 + drop / 2, 0.02, 0.02, drop, '#3B3F47'),
               P('box', 0, 0, 0.62, 0.42, 0.42, 0.36, '#C96F4A', bevel=0.03),
               P('box', 0, 0, 0.85, 0.7, 0.7, 0.3, '#4C9A2A', bevel=0.12)]
    for i in range(4):
        a = i * 90 + 20
        hanging.append(P('box', 0.28 * math.cos(math.radians(a)), 0.28 * math.sin(math.radians(a)), 0.4, 0.12, 0.12,
                         0.55, '#5DAE3E', bevel=0.04, rz=a))
    hk = dr.tpl('hanging_plant', hanging)
    dr.put('hanging_plant', hk, x1 - 0.6, y1 - 0.6, wall_h - dr.templates[hk]['size'][2] - 0.02, 0.0)
    nwin = 2 if H < 11 else 3
    for i in range(nwin):
        along = -H / 2 + H * (i + 0.5) / nwin
        _hang(dr, inner, 'w', along, 1.0, 'window', view_window(rnd, 1.6, 1.5, tod, curtains=curtain,
                                                                 rooftops=(i == nwin - 1)))
    _hang(dr, inner, 'w', 0.0 if nwin == 2 else -H / 6, 0.0, 'side_table',
          side_table_parts(wood, '#FFE7B0', night))
    _hang(dr, inner, 'w', H / 2 - 0.5, 0.0, 'plant', plant_parts(rnd, tall=True))
    north_of_door = -(hy + (kdoor[1] - cy)) / 2
    _hang(dr, inner, 'e', north_of_door, 1.55, 'frame', picture_frame(rnd, 0.6, 0.8), shadow=False)
    _hang(dr, inner, 'e', north_of_door, 2.4, 'shelf', wall_shelf_parts(rnd, wood))
    _hang(dr, inner, 'e', -(hy - 0.5), 0.0, 'plant', plant_parts(rnd, tall=True))
    clock = [P('cyl', 0, 0.02, 0, 0.5, 0.5, 0.06, '#3B3F47', rx=90), P('cyl', 0, -0.02, 0, 0.42, 0.42, 0.03,
                                                                         '#FFFFFF', rx=90),
             P('box', 0, -0.045, 0.08, 0.025, 0.01, 0.16, '#22252B'), P('box', 0.06, -0.045, 0, 0.12, 0.01, 0.025,
                                                                           '#22252B')]
    # Sofa and armchair face the screen from the south wall, either side of the arch; the coffee table sits in
    # front of the sofa when the cameras leave it room.
    # Lounge: low pieces (under LOW_TOP) may stand close behind the action where every camera looks over them,
    # so the sofa faces the action from the north and the armchair from the east; where a low camera zone
    # forbids that they fall back to the south wall with a coffee table in front.
    core = dr.area['core']
    ccx, ccy = (core[0] + core[2]) / 2, (core[1] + core[3]) / 2
    sofa_x = -(arch[1] - arch[0]) / 2 - 1.5
    sofa_key = dr.tpl('sofa', sofa_parts(sofa_col, 2.2, rnd.sample(ACCENTS, 2)))
    sd = dr.templates[sofa_key]['size'][1]
    island = any(dr.put('sofa', sofa_key, ccx + dx, core[3] + CORE_PAD + sd / 2 + 0.1, 0.0, 0.0, gap=0.1)
                 for dx in (0.0, -1.2, 1.2))
    if not island:
        _on_wall(dr, inner, 's', -sofa_x, 0.0, sofa_key, sd, 'sofa')
        ctable = dr.tpl('coffee_table', coffee_table_parts(rnd, wood))
        for dy in (1.55, 1.75, 1.95):
            if dr.put('coffee_table', ctable, cx + sofa_x, y0 + dy, 0.0, 0.0, gap=0.1):
                break
    if 'lamp' not in props:
        _hang(dr, inner, 's', -(sofa_x - 1.4), 0.0, 'floor_lamp', floor_lamp_parts('#FFE7B0', night))
    chair_x = (arch[1] - arch[0]) / 2 + 1.2
    chair_key = dr.tpl('armchair', armchair_parts(rnd.choice([c_ for c_ in SOFAS if c_ != sofa_col]),
                                                  rnd.choice(ACCENTS)))
    ad = dr.templates[chair_key]['size'][1]
    if not any(dr.put('armchair', chair_key, core[2] + CORE_PAD + ad / 2 + 0.1, ccy + dy, 0.0, -90.0, gap=0.1)
               for dy in (0.0, -1.0, 1.0)):
        _on_wall(dr, inner, 's', -chair_x, 0.0, chair_key, ad, 'armchair')
    side_key = dr.tpl('end_table', coffee_table_parts(rnd, wood)[:6])
    dr.scatter('end_table', side_key, 1, -0.6, 0.8, gap=0.25, rot=0.0)
    bean = dr.tpl('beanbag', [P('box', 0, 0, 0.25, 0.9, 0.9, 0.5, rnd.choice(['#AC92EC', '#4FC1E9', '#FC6E51']),
                               bevel=0.2)])
    dr.scatter('beanbag', bean, 1, -0.8, 0.8, gap=0.3)
    toys = [P('box', 0, 0, 0.25, 1.0, 0.55, 0.5, rnd.choice(ACCENTS[:-1]), bevel=0.04),
            P('box', 0, 0, 0.53, 1.04, 0.59, 0.08, rnd.choice(ACCENTS[:-1]), bevel=0.02)]
    dr.scatter('toy_box', dr.tpl('toy_box', toys), 1, -0.8, 0.8, gap=0.3, rot=0.0)
    _hang(dr, inner, 's', -(chair_x + 1.1), 0.0, 'plant', plant_parts(rnd))
    _hang(dr, inner, 's', -sofa_x, 1.9, 'frame', picture_frame(rnd, 0.8, 0.55), shadow=False)
    _hang(dr, inner, 's', -chair_x, 1.75, 'frame', picture_frame(rnd, 0.6, 0.7), shadow=False)
    _hang(dr, inner, 'e', (hy + (cy - kdoor[0])) / 2, 1.8, 'clock', clock, shadow=False)
    # Dining nook by the kitchen opening: a low table with stools (nothing taller than the table).
    dine = [P('box', 0, 0, 0.7, 1.3, 0.85, 0.05, wood, bevel=0.015),
            P('box', -0.55, 0, 0.34, 0.08, 0.7, 0.68, shade(wood, 0.85)), P('box', 0.55, 0, 0.34, 0.08, 0.7, 0.68,
                                                                             shade(wood, 0.85)),
            P('cyl', 0.1, 0.05, 0.75, 0.3, 0.3, 0.05, rnd.choice(ACCENTS)), P('box', -0.35, -0.1, 0.735, 0.4, 0.28,
                                                                                0.02, '#FFFFFF')]
    for sx in (-0.4, 0.4):
        for sy in (-0.75, 0.75):
            dine += [P('cyl', sx, sy, 0.44, 0.38, 0.38, 0.06, rnd.choice(ACCENTS[:-1])),
                     P('cyl', sx, sy, 0.2, 0.06, 0.06, 0.42, '#3B3F47')]
    dk = dr.tpl('dining', dine)
    for dx in (1.7, 2.3, 2.9):
        if dr.put('dining', dk, x1 - dx, (kdoor[0] + kdoor[1]) / 2 - 0.2, 0.0, 0.0, gap=0.2):
            break
    # Rug under the action, thin layers above the planks (border 1 mm, centre 3 mm).
    rug_col = rnd.choice(['#C98A6A', '#8FA7C4', '#A3B98A', '#D9B27A', '#C4A0B8'])
    rug = [P('box', 0, 0, 0.001, 4.2, 3.0, 0.002, rug_col),
           P('box', 0, 0, 0.003, 3.7, 2.5, 0.002, mix(rug_col, '#F6F0E0', 0.35))]
    dr.put('rug', dr.tpl('rug', rug, decal=True, shadow=False), (core[0] + core[2]) / 2, (core[1] + core[3]) / 2,
           -0.001, check=False)
    # Hallway: front door with the entry mat straight through the arch, coat rack and shoes west of it, the
    # stairs rising east to a landing with a door to the upstairs.
    door_x = cx - 0.5
    _hang(dr, hall, 's', -(door_x - cx), 0.0, 'front_door', front_door_parts(rnd.choice(DOORS), night), shadow=False)
    mat = [P('box', 0, 0, 0.0015, 0.9, 0.55, 0.003, '#7A5230'), P('box', 0, 0, 0.004, 0.7, 0.38, 0.002, '#A0703F')]
    dr.put('door_mat', dr.tpl('door_mat', mat, decal=True, shadow=False), door_x, yh0 + 0.42, 0.0, check=False)
    _hang(dr, hall, 's', -(door_x - 2.0 - cx), 1.6, 'coat_rack', coat_rack_parts(rnd))
    _hang(dr, hall, 's', -(door_x - 2.0 - cx), 0.0, 'shoe_rack', shoe_rack_parts(rnd))
    stairs = dr.tpl('stairs', stairs_parts(wood, rug_col))
    sx0 = cx + 0.5
    s_w, s_d, s_h = dr.templates[stairs]['size']
    dr.put('stairs', stairs, sx0 + s_w / 2, yh0 + s_d / 2 + 0.01, 0.0, 0.0, gap=0.0)
    landing_z = STAIR_STEPS * STAIR_RISE
    _hang(dr, hall, 's', -(sx0 + s_w - 0.55 - cx), min(landing_z + 0.75, wall_h - 0.75), 'frame',
          picture_frame(rnd, 0.6, 0.45), shadow=False)
    _hang(dr, hall, 's', -(sx0 - 0.9 - cx), 1.7, 'frame', picture_frame(rnd, 0.5, 0.6), shadow=False)
    # Kitchen corner seen through the east opening: counter with sink, hob and fruit, cabinets above, fridge.
    kdoor_col = rnd.choice(['#9CC5A1', '#F2B5B5', '#B7C9D6', '#FFE08A', '#D8B48A'])
    _hang(dr, kitchen, 'e', 0.3, 0.0, 'counter', kitchen_counter_parts(rnd, 3.6, kdoor_col))
    _hang(dr, kitchen, 'e', 1.45, 1.5, 'cabinets', upper_cabinets_parts(1.3, kdoor_col))
    _hang(dr, kitchen, 'e', -0.85, 1.5, 'cabinets', upper_cabinets_parts(1.1, kdoor_col))
    _hang(dr, kitchen, 'e', -KITCHEN_HALF_W + 0.45, 0.0, 'fridge', fridge_parts())
    _hang(dr, kitchen, 'e', 0.3, 1.45, 'window', view_window(rnd, 0.9, 0.7, tod), shadow=False)
    _hang(dr, kitchen, 'n', 0.6, 0.0, 'plant', plant_parts(rnd))
    # Ceiling lights (flush discs) in every room; at night they and the lamps light the house.
    lamp = [P('cyl', 0, 0, 0, 0.8, 0.8, 0.06, '#FFFBEA', 'emit')]
    lk = dr.tpl('ceiling_light', lamp, decal=True, shadow=False)
    pendant = [P('cyl', 0, 0, 0.3, 0.025, 0.025, 0.16, '#3B3F47'),
               P('cyl', 0, 0, 0.13, 0.5, 0.5, 0.26, rnd.choice(['#E3B87F', '#9CC5A1', '#F2B5B5', '#B7C9D6']), bevel=0.02),
               P('cyl', 0, 0, 0.004, 0.3, 0.3, 0.008, '#FFF4D6', 'glow')]
    pk = dr.tpl('pendant', pendant, shadow=False)
    spots = [(cx - W / 4, cy), (cx + W / 4, cy), (cx, (yh0 + yh1) / 2), ((xk0 + xk1) / 2, cy)]
    for i, (lx, ly) in enumerate(spots):
        if i < 2:
            dr.put('pendant', pk, lx, ly, wall_h - 0.38, 0.0, check=False)   # above the camera band by construction
        else:
            dr.put('ceiling_light', lk, lx, ly, wall_h - 0.05, check=False)
        if night:
            dr.lights.append({'type': 'POINT', 'pos': [_r(lx), _r(ly), _r(wall_h - 1.1)], 'energy': 300,
                              'color': '#FFD9A6'})
    if night:
        for lx, ly, lz in ((cx + sofa_x - 1.4, y0 + 0.4, 1.6), (x0 + 0.4, cy if nwin == 2 else cy - H / 6, 1.05)):
            dr.lights.append({'type': 'POINT', 'pos': [_r(lx), _r(ly), _r(lz)], 'energy': 60, 'color': '#FFD9A0'})
    # Time-of-day light through the windows: a tinted point light just inside each one.
    col, energy = WINDOW_LIGHT[tod]
    for p in dr.pieces:
        if p['kind'] != 'window':
            continue
        yaw = math.radians(p['rot'])
        # The window's local -y points into the room; the light sits 0.6 m inside, at the pane's middle.
        ix = p['pos'][0] + math.sin(yaw) * 0.6
        iy = p['pos'][1] - math.cos(yaw) * 0.6
        dr.lights.append({'type': 'POINT', 'pos': [_r(ix), _r(iy), _r(p['pos'][2] + p['size'][2] / 2)],
                          'energy': energy, 'color': col})


# ---------------------------------------------------------------- park (outdoor hangouts)
PARK_FENCE = 30.0


def fountain_parts(night):
    stone, water = '#B9BCC4', '#7FC8F0'
    wmat = 'glow' if night else 'pane'
    return [P('cyl', 0, 0, 0.25, 4.4, 4.4, 0.5, stone, bevel=0.04),
            P('cyl', 0, 0, 0.47, 4.0, 4.0, 0.06, water, wmat),
            P('cyl', 0, 0, 1.1, 0.7, 0.7, 1.3, shade(stone, 0.9)),
            P('cyl', 0, 0, 1.85, 1.8, 1.8, 0.24, stone, bevel=0.03),
            P('cyl', 0, 0, 1.98, 1.6, 1.6, 0.04, water, wmat),
            P('cyl', 0, 0, 2.2, 0.16, 0.16, 0.4, shade(stone, 0.9)),
            P('ball', 0, 0, 2.5, 0.3, 0.3, 0.3, water, wmat)]


def playground_parts(rnd):
    cols = rnd.sample(OBBY, 4)
    parts = [P('box', -2.2, 0.6, 0.4, 1.2, 1.2, 0.8, cols[0], 'stud', 0.05),
             P('box', -2.2, 0.6, 1.2, 0.8, 0.8, 0.8, cols[1], 'stud', 0.05),
             P('box', -1.0, 0.6, 0.6, 1.2, 1.2, 1.2, cols[2], 'stud', 0.05),
             # slide from the tall block down to the right
             P('box', 0.6, 0.6, 0.72, 2.4, 0.7, 0.08, cols[3], 'gloss', 0.02, ry=-28),
             P('box', 0.6, 0.25, 0.9, 2.4, 0.06, 0.2, shade(cols[3], 0.85), ry=-28),
             P('box', 0.6, 0.95, 0.9, 2.4, 0.06, 0.2, shade(cols[3], 0.85), ry=-28)]
    # Swing frame: two A-frames, a top bar, two seats.
    for sx in (-1.6, 1.6):
        for sy in (-1, 1):
            parts.append(P('box', sx, -1.6 + sy * 0.55, 1.15, 0.1, 0.1, 2.4, '#3B3F47', rx=sy * 26))
    parts.append(P('box', 0, -1.6, 2.25, 3.5, 0.1, 0.1, '#3B3F47'))
    for sx in (-0.6, 0.6):
        parts += [P('box', sx - 0.2, -1.6, 1.4, 0.02, 0.02, 1.6, '#8B8F98'), P('box', sx + 0.2, -1.6, 1.4, 0.02, 0.02, 1.6, '#8B8F98'),
                  P('box', sx, -1.6, 0.6, 0.5, 0.2, 0.05, rnd.choice(OBBY), bevel=0.01)]
    return parts


def flowerbed_parts(rnd):
    parts = [P('cyl', 0, 0, 0.03, 2.4, 2.4, 0.06, '#5A3A22'),
             P('cyl', 0, 0, 0.08, 2.6, 2.6, 0.02, '#A9ADB6')]
    for i in range(9):
        a, d = i * 40 + rnd.uniform(-10, 10), rnd.uniform(0.3, 1.0)
        x, y = d * math.cos(math.radians(a)), d * math.sin(math.radians(a))
        parts += [P('box', x, y, 0.18, 0.04, 0.04, 0.24, '#4C9A2A'),
                  P('ball', x, y, 0.32, 0.18, 0.18, 0.14, rnd.choice(['#E84A5F', '#FFCE54', '#FC6E51', '#AC92EC', '#F6F7FB']))]
    return parts


def park(dr, night):
    """A paved plaza under the action in a city park: gravel paths out in four directions, a fountain, benches,
    planters and lamp posts along the paths, flower beds, a pond, trees all around, a playground corner, then
    a low fence with houses beyond it, hills, a hazy skyline and clouds."""
    rnd = dr.rnd
    c = dr.area['clear']
    cx, cy = dr.cx, dr.cy
    dr.put('ground', dr.tpl('ground', [P('box', 0, 0, -0.1, 360, 360, 0.2, GROUND_HEX['park'])], decal=True,
                            shadow=False), cx, cy, SLAB_TOP - 0.2, check=False)
    # Plaza paving over the clearance box plus a border (border top 2 mm below the paving, seams 2 mm proud).
    pw, pd = c[2] - c[0] + 3.0, c[3] - c[1] + 3.0
    tiles = [P('box', 0, 0, 0.004, pw, pd, 0.008, '#A9A092')]
    nx, ny = max(2, int((pw - 1.0) / 1.5)), max(2, int((pd - 1.0) / 1.5))
    tw, td = (pw - 1.0) / nx, (pd - 1.0) / ny
    for i in range(nx):
        for j in range(ny):
            tiles.append(P('box', -(pw - 1.0) / 2 + tw * (i + 0.5), -(pd - 1.0) / 2 + td * (j + 0.5), 0.006, tw - 0.04,
                           td - 0.04, 0.008, '#C7BDAC' if (i + j) % 2 else '#B8AE9D'))
    dr.put('plaza', dr.tpl('plaza', tiles, decal=True, shadow=False), cx, cy, -0.010, check=False)
    # Paths to the four sides of the park, with benches, planters and lamps along them.
    bench = dr.tpl('bench', [P('box', 0, 0, 0.45, 1.6, 0.5, 0.08, '#B07A45', bevel=0.02),
                             P('box', 0, 0.22, 0.76, 1.6, 0.06, 0.42, '#B07A45', bevel=0.02),
                             P('box', -0.7, 0, 0.21, 0.08, 0.45, 0.42, '#3B3F47'),
                             P('box', 0.7, 0, 0.21, 0.08, 0.45, 0.42, '#3B3F47')])
    planter = dr.tpl('planter', [P('box', 0, 0, 0.25, 1.4, 0.7, 0.5, '#B9A48A', bevel=0.04),
                                 P('box', -0.35, 0, 0.6, 0.5, 0.5, 0.3, '#E84A5F', bevel=0.1),
                                 P('box', 0.3, 0.05, 0.62, 0.55, 0.5, 0.32, '#FFCE54', bevel=0.1)])
    lamp_key = dr.tpl('lamp', lamp_parts(night))
    bin_key = dr.tpl('bin', [P('cyl', 0, 0, 0.45, 0.5, 0.5, 0.9, '#3E6E4A', bevel=0.02),
                             P('cyl', 0, 0, 0.93, 0.56, 0.56, 0.06, '#2E5238')])
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        half = pw / 2 if dx else pd / 2
        L = PARK_FENCE - half
        along = dx != 0
        path = dr.tpl('path', [P('box', 0, 0, 0.004, L if along else 2.6, 2.6 if along else L, 0.008, '#B9A98A')],
                      decal=True, shadow=False)
        dr.put('path', path, cx + dx * (half + L / 2), cy + dy * (half + L / 2), -0.008, check=False)
        facing = math.degrees(math.atan2(dy, dx))     # path direction
        for k, d in enumerate((3.0, 9.0, 15.0, 21.0)):
            for side in (-1, 1):
                px = cx + dx * (half + d) + (-dy) * side * 1.9
                py = cy + dy * (half + d) + dx * side * 1.9
                # Benches face the path; lamps and planters alternate between them.
                if k % 2 == 0:
                    dr.put('bench', bench, px, py, 0.0, facing + 90 * side, gap=0.1)
                else:
                    dr.put('planter' if side > 0 else 'bin', planter if side > 0 else bin_key, px, py, 0.0, facing)
            lx = cx + dx * (half + d + 3.0) + (-dy) * 1.9
            ly = cy + dy * (half + d + 3.0) + dx * 1.9
            dr.put('lamp', lamp_key, lx, ly, 0.0, facing + 90)
    for qx, qy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = cx + qx * (pw / 2 + 0.5), cy + qy * (pd / 2 + 0.5)
        if dr.put('lamp', lamp_key, x, y, 0.0, rnd.uniform(0, 360)) and night:
            dr.lights.append({'type': 'POINT', 'pos': [_r(x), _r(y), 3.4], 'energy': 120, 'color': '#FFE2B0'})
        dr.put('planter', planter, cx + qx * (pw / 2 - 1.2), cy + qy * (pd / 2 - 0.1), 0.0, 0.0)
    fountain = dr.tpl('fountain', fountain_parts(night))
    # The fountain takes one of the plaza's free diagonals (the paths and their furniture hold the axes).
    diagonals = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    rnd.shuffle(diagonals)
    for qx, qy in diagonals:
        if dr.put('fountain', fountain, cx + qx * (pw / 2 + 3.8), cy + qy * (pd / 2 + 3.8), 0.0, 0.0, gap=0.5):
            break
    dr.scatter('playground', dr.tpl('playground', playground_parts(rnd)), 1, 3.0, 9.0, gap=1.0, rot=None)
    sand = dr.tpl('sandbox', [P('box', 0, 0, 0.001, 3.0, 3.0, 0.002, '#E8D7A8'),
                              P('box', 0, 1.45, 0.1, 3.0, 0.1, 0.2, '#A0703F'), P('box', 0, -1.45, 0.1, 3.0, 0.1, 0.2, '#A0703F'),
                              P('box', 1.45, 0, 0.1, 0.1, 3.0, 0.2, '#A0703F'), P('box', -1.45, 0, 0.1, 0.1, 3.0, 0.2, '#A0703F')])
    dr.scatter('sandbox', sand, 1, 3.0, 9.0, gap=0.6, rot=0.0)
    pond = dr.tpl('pond', [P('cyl', 0, 0, 0.001, 9.0, 7.0, 0.002, '#6FB7E0', 'gloss'),
                           P('cyl', 0, 0, 0.0025, 9.6, 7.6, 0.001, '#C9B99A')], decal=True, shadow=False)
    dr.scatter('pond', pond, 1, 8.0, 14.0, gap=0.0, rot=None)
    beds = [dr.tpl('flowerbed', flowerbed_parts(rnd)) for _ in range(2)]
    dr.scatter('flowerbed', beds, 6, 1.0, 10.0, gap=0.4)
    tree_keys = [dr.tpl('tree', tree_parts(s, rnd, rnd.choice(LEAVES))) for s in ('round', 'round', 'poplar', 'pine')]
    bush_keys = [dr.tpl('bush', tree_parts('bush', rnd, rnd.choice(LEAVES))) for _ in range(2)]
    dr.scatter('tree', tree_keys, 16, 2.5, 9.0, scale=(0.8, 1.15), sectors=16, gap=0.5)
    dr.scatter('tree', tree_keys, 28, 9.0, 26.0, scale=(0.9, 1.3), sectors=20, gap=0.6)
    dr.scatter('bush', bush_keys, 18, 0.5, 12.0, scale=(0.8, 1.2), gap=0.3)
    # Low fence around the park with gaps for the paths, houses on the streets beyond, then the far ring.
    F = PARK_FENCE
    seg = dr.tpl('fence', fence_parts(6.0, rnd.choice([WHITE, '#F3EBDD'])))
    for i in range(10):
        a = -F + 3.0 + i * 6.0
        if abs(a) > 4.5:
            for (x, y, rot) in ((cx + a, cy - F, 0.0), (cx + a, cy + F, 0.0), (cx - F, cy + a, 90.0), (cx + F, cy + a, 90.0)):
                dr.put('fence', seg, x, y, 0.0, rot, check=False)
    def house():
        return house_parts(rnd, night)
    front = F + 5.0
    _row(dr, 'house', house, cx - F - 6, cx + F + 6, cy + front, 'x', 0.0, rnd)
    _row(dr, 'house', house, cx - F - 6, cx + F + 6, cy - front, 'x', 180.0, rnd)
    _row(dr, 'house', house, cy - F, cy + F, cx - front, 'y', 90.0, rnd)
    _row(dr, 'house', house, cy - F, cy + F, cx + front, 'y', -90.0, rnd)
    _far_ring(dr, 'town', night)


PRESETS = {'town_street': town_street, 'night_forest': night_forest, 'classroom': classroom, 'bedroom': bedroom,
           'studio': studio, 'home': home, 'park': park, 'sky_obby': lambda dr, night: obby(dr, night, False),
           'lava_obby': lambda dr, night: obby(dr, night, True)}


# ---------------------------------------------------------------- public API
def layout(m, scales=None, tops=None):
    """Deterministic set layout for a compiled manifest (JSON-serialisable).

    scales / tops: cast id -> scale / visual top (what the camera frames; solver.solve passes its own)."""
    st = m.get('setting') or {}
    preset = st.get('preset') if st.get('preset') in PRESETS else 'sky_obby'
    tod = st.get('time_of_day', 'noon')
    seed = _seed(m)
    area = clearance(m, scales, tops)
    dr = Dresser(m, area, random.Random(seed))
    dr.lights = []
    night = tod == 'night'
    PRESETS[preset](dr, night)
    sky, lights = sky_and_lights(preset, tod, st.get('lighting', 'natural'))
    used = {p['template'] for p in dr.pieces}
    return {'version': VERSION, 'preset': preset, 'time_of_day': tod, 'seed': seed, 'area': area,
            'sky': sky, 'lights': lights + dr.lights,
            'templates': {k: v for k, v in dr.templates.items() if k in used}, 'pieces': dr.pieces}


def piece_boxes(lay, everything=False):
    """Oriented boxes of the set: [{'id', 'kind', 'c': [x, y, z], 'h': [hx, hy, hz], 'yaw': deg}]."""
    out = []
    for p in (lay or {}).get('pieces', []):
        if not (p['occluder'] or (everything and p['size'][2] > DECAL_TOP and
                                  not lay['templates'][p['template']]['decal'])):
            continue
        sx, sy, sz = p['size']
        out.append({'id': 'set:' + p['id'], 'kind': p['kind'], 'c': [p['pos'][0], p['pos'][1], p['pos'][2] + sz / 2],
                    'h': [sx / 2, sy / 2, sz / 2], 'yaw': p['rot']})
    return out


def prop_boxes(m):
    """Boxes of the big story props that never move (houses, trees, beds...), from the manifest."""
    out = []
    for p in (m.get('setting') or {}).get('props', []):
        t = p.get('type')
        if t in S.HOLDABLE:
            continue
        sc = float(p.get('scale', 1) or 1)
        yaw = float(p.get('rotation', 0) or 0)
        x, y, z = p['position']
        if t == 'platform':
            sx, sy = (p.get('size') or [2.0, 2.0])[:2]
            boxes = [(0, 0, -0.3, sx, sy, 0.6)]
        else:
            boxes = PROP_BOXES.get(t, [])
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        for i, (bx, by, bz, sx, sy, sz) in enumerate(boxes):
            out.append({'id': f'prop:{p["id"]}' + (f':{i}' if i else ''), 'kind': t,
                        'c': [x + sc * (c * bx - s * by), y + sc * (s * bx + c * by), z + sc * bz],
                        'h': [sc * sx / 2, sc * sy / 2, sc * sz / 2], 'yaw': yaw})
    return out


def box_arrays(boxes):
    import numpy as np
    if not boxes:
        return None
    return (np.array([b['c'] for b in boxes], dtype=float), np.array([b['h'] for b in boxes], dtype=float),
            np.radians(np.array([b['yaw'] for b in boxes], dtype=float)))


def _local(P, C, YAW):
    import numpy as np
    d = P[:, None, :] - C[None, :, :]
    c, s = np.cos(YAW)[None, :], np.sin(YAW)[None, :]
    return np.stack([d[..., 0] * c + d[..., 1] * s, -d[..., 0] * s + d[..., 1] * c, d[..., 2]], axis=-1)


def inside(arrs, P, pad=0.0):
    """(F, N) bool: point P[f] lies inside box n (grown by pad)."""
    import numpy as np
    if arrs is None:
        return np.zeros((len(P), 0), dtype=bool)
    C, H, YAW = arrs
    L = _local(np.asarray(P, dtype=float), C, YAW)
    return np.all(np.abs(L) <= H[None, :, :] + pad, axis=-1)


def segment_hits(arrs, P0, P1, pad=0.0):
    """(F, N) bool: the segment P0[f] -> P1[f] passes through box n (slab test in each box's frame)."""
    import numpy as np
    P0, P1 = np.asarray(P0, dtype=float), np.asarray(P1, dtype=float)
    if arrs is None:
        return np.zeros((len(P0), 0), dtype=bool)
    C, H, YAW = arrs
    A, B = _local(P0, C, YAW), _local(P1, C, YAW)
    Dv = B - A
    Hh = H[None, :, :] + pad
    with np.errstate(divide='ignore', invalid='ignore'):
        inv = np.where(np.abs(Dv) > 1e-12, 1.0 / Dv, np.inf)
        t1 = (-Hh - A) * inv
        t2 = (Hh - A) * inv
    lo = np.where(np.abs(Dv) > 1e-12, np.minimum(t1, t2), np.where(np.abs(A) <= Hh, -np.inf, np.inf))
    hi = np.where(np.abs(Dv) > 1e-12, np.maximum(t1, t2), np.where(np.abs(A) <= Hh, np.inf, -np.inf))
    tmin = np.maximum(0.0, lo.max(axis=-1))
    tmax = np.minimum(1.0, hi.min(axis=-1))
    return tmax >= tmin


def ray_distance(arrs, origin, direction, max_dist):
    """Distance along a ray to the first box it enters (inf when none within max_dist)."""
    import numpy as np
    if arrs is None:
        return float('inf')
    o = np.asarray(origin, dtype=float)
    d = np.asarray(direction, dtype=float)
    d = d / np.linalg.norm(d)
    C, H, YAW = arrs
    A = _local(o[None, :], C, YAW)[0]
    Dv = _local((o + d)[None, :], C, YAW)[0] - A
    with np.errstate(divide='ignore', invalid='ignore'):
        inv = np.where(np.abs(Dv) > 1e-12, 1.0 / Dv, np.inf)
        t1, t2 = (-H - A) * inv, (H - A) * inv
    lo = np.where(np.abs(Dv) > 1e-12, np.minimum(t1, t2), np.where(np.abs(A) <= H, -np.inf, np.inf))
    hi = np.where(np.abs(Dv) > 1e-12, np.maximum(t1, t2), np.where(np.abs(A) <= H, np.inf, -np.inf))
    tmin, tmax = np.maximum(0.0, lo.max(axis=-1)), hi.min(axis=-1)
    ok = (tmax >= tmin) & (tmin <= max_dist)
    return float(tmin[ok].min()) if ok.any() else float('inf')
