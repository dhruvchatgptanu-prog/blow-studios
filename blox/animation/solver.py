"""Host-side motion solver: compiled manifest -> per-frame rig state.

Everything that decides *how* a character moves happens here, in plain Python,
so it is deterministic and unit-testable without Blender:

* base poses interpolated from the manifest's pose keys (with easing);
* actions layered on top with anticipation / main / follow-through / hold
  phases and blend envelopes;
* locomotion and turns planned as footsteps; planted feet stay fixed in world
  space and legs are solved with analytic two-bone IK;
* hand contact poses (chest, mouth, forehead, props) solved with IK;
* overlapping action and follow-through from a damped spring on body angles;
* eye-lines from the actual head transform toward the target;
* blinks, brows, lids and mouth shapes (expression + visemes);
* the camera for every frame, using the solved root trajectories.

The Blender script only applies these transforms and renders, then reports
what the evaluated scene actually did (telemetry) so QA can compare.
"""
import hashlib
import math
import os
import zlib

import numpy as np

from . import rig as R
from . import visemes as VIS
from ..manifest import schema as S
from ..manifest.compile import _ease, pose_at
from ..manifest.geometry import ANGLE_PITCH, CAMERA_SIDE_DEG, direction, wrap

D2R = math.pi / 180.0
R2D = 180.0 / math.pi
LENS_BY_FRAMING = {'extreme_wide': 24, 'wide': 28, 'full': 35, 'medium_wide': 40, 'medium': 45,
                   'medium_close': 55, 'close_up': 70, 'extreme_close_up': 85}
SENSOR_W = 36.0


# ---------------------------------------------------------------- math
def rx(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)


def ry(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=float)


def rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)


def euler_m(deg):
    x, y, z = (v * D2R for v in deg)
    return rz(z) @ ry(y) @ rx(x)


def m_to_euler(M):
    sy = -M[2, 0]
    sy = max(-1.0, min(1.0, sy))
    y = math.asin(sy)
    if abs(math.cos(y)) > 1e-6:
        x = math.atan2(M[2, 1], M[2, 2])
        z = math.atan2(M[1, 0], M[0, 0])
    else:
        x = math.atan2(-M[1, 2], M[1, 1])
        z = 0.0
    return [x * R2D, y * R2D, z * R2D]


def norm(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def lerp(a, b, u):
    return a + (b - a) * u


def smooth(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def envelope(f, a0, a1, a3, a4):
    """0 before a0, ramps to 1 by a1, holds, ramps down from a3 to 0 at a4."""
    if f < a0 or f >= a4:
        return 0.0
    if f < a1:
        return smooth((f - a0) / max(1, a1 - a0))
    if f < a3:
        return 1.0
    return 1.0 - smooth((f - a3) / max(1, a4 - a3))


def two_bone(root, target, l1, l2, pole, sign):
    """Analytic two-bone IK in the parent's local space.

    Returns (upper_local_euler_deg, lower_angle_deg, reached_distance_error).
    sign=+1 for legs (knee bends with +x), -1 for arms (elbow bends with -x).
    """
    d = target - root
    dist = float(np.linalg.norm(d))
    lo, hi = abs(l1 - l2) + 1e-4, l1 + l2 - 1e-4
    err = max(0.0, dist - hi)
    dist_c = max(lo, min(hi, dist))
    n = norm(d) if dist > 1e-9 else np.array([0.0, 0.0, -1.0])
    cos_a = (l1 * l1 + dist_c * dist_c - l2 * l2) / (2 * l1 * dist_c)
    a = math.acos(max(-1.0, min(1.0, cos_a)))
    pp = pole - np.dot(pole, n) * n
    if np.linalg.norm(pp) < 1e-6:
        pp = np.array([0.0, -1.0, 0.0]) - np.dot(np.array([0.0, -1.0, 0.0]), n) * n
    pp = norm(pp)
    upper = n * math.cos(a) + pp * math.sin(a)
    mid = root + upper * l1
    tgt = root + n * dist_c
    lower = norm(tgt - mid)
    cr = np.cross(upper, lower)
    if np.linalg.norm(cr) < 1e-6:
        x_axis = norm(-sign * np.cross(upper, pp)) if np.linalg.norm(np.cross(upper, pp)) > 1e-6 else np.array([1.0, 0, 0])
        angle = 0.0
    else:
        x_axis = norm(sign * cr)
        angle = sign * math.acos(max(-1.0, min(1.0, float(np.dot(upper, lower))))) * R2D
    z_axis = -upper
    y_axis = np.cross(z_axis, x_axis)
    M = np.column_stack([x_axis, y_axis, z_axis])
    return m_to_euler(M), angle, err, lower


class Spring:
    """Damped spring per channel: overlapping action and follow-through."""

    def __init__(self, k, c):
        self.k, self.c = k, c
        self.x = None
        self.v = None

    def step(self, target):
        target = np.asarray(target, dtype=float)
        if self.x is None:
            self.x = target.copy()
            self.v = np.zeros_like(target)
            return self.x.copy()
        self.v = self.v * (1 - self.c) + self.k * (target - self.x)
        self.x = self.x + self.v
        return self.x.copy()

    def reset(self, value):
        self.x = np.asarray(value, dtype=float).copy()
        self.v = np.zeros_like(self.x)


# ---------------------------------------------------------------- FK
def fk(state, scale):
    """World transforms for every joint: name -> (R3, p3)."""
    out = {}
    for name, parent, off in R.JOINTS:
        rot = euler_m(state['rot'].get(name, (0, 0, 0)))
        loc = np.array(off, dtype=float) + np.array(state['loc'].get(name, (0, 0, 0)), dtype=float)
        if parent is None:
            Rw = rz(state['yaw'] * D2R) @ rot
            p = np.array(state['root'], dtype=float)
        else:
            Rp, pp = out[parent]
            Rw = Rp @ rot
            p = pp + Rp @ (loc * scale)
        out[name] = (Rw, p)
    return out


def local_point(fkres, joint, pt, scale):
    Rw, p = fkres[joint]
    return p + Rw @ (np.array(pt, dtype=float) * scale)


def to_local(fkres, joint, world, scale):
    Rw, p = fkres[joint]
    return Rw.T @ (np.asarray(world, dtype=float) - p) / scale


# ---------------------------------------------------------------- feet
class Foot:
    def __init__(self, pos, yaw):
        self.plants = [(-10 ** 9, np.array(pos, dtype=float), yaw)]
        self.swings = []   # (f0, f1, from_pos, to_pos, from_yaw, to_yaw, height)
        self.free = []     # (f0, f1) feet driven by the body (air, falls)

    def step(self, f0, f1, to_pos, to_yaw, height=0.1):
        frm_pos, frm_yaw = self.at_plant(f0)
        self.swings.append((f0, f1, frm_pos, np.array(to_pos, dtype=float), frm_yaw, to_yaw, height))
        self.plants.append((f1, np.array(to_pos, dtype=float), to_yaw))
        self.plants.sort(key=lambda p: p[0])

    def at_plant(self, f):
        cur = self.plants[0]
        for p in self.plants:
            if p[0] <= f:
                cur = p
        return cur[1].copy(), cur[2]

    def sample(self, f):
        """(world position of the sole centre, yaw, contact_expected)."""
        for a, b in self.free:
            if a <= f < b:
                return None, None, False
        for (f0, f1, p0, p1, y0, y1, h) in self.swings:
            if f0 <= f < f1:
                u = (f - f0) / max(1, f1 - f0)
                e = smooth(u)
                pos = p0 + (p1 - p0) * e
                pos = pos.copy()
                pos[2] += h * math.sin(math.pi * u)
                return pos, y0 + wrap(y1 - y0) * e, False
        pos, yaw = self.at_plant(f)
        return pos, yaw, True


def stance_offsets(stance, scale):
    w = {'neutral': 0.18, 'wide': 0.30, 'narrow': 0.10, 'step_forward': 0.18, 'step_back': 0.18}.get(stance, 0.18)
    fwd = {'step_forward': (-0.16, 0.08), 'step_back': (0.08, -0.12)}.get(stance, (0.0, 0.0))
    return (np.array([w, fwd[0], 0.0]) * scale, np.array([-w, fwd[1], 0.0]) * scale)


def world_feet(root_xy, yaw, stance, scale):
    left, right = stance_offsets(stance, scale)
    Rz = rz(yaw * D2R)
    base = np.array([root_xy[0], root_xy[1], 0.0])
    return base + Rz @ left, base + Rz @ right


# ---------------------------------------------------------------- solver
class CharacterSolver:
    def __init__(self, m, cid, bible, n_frames, repair=None):
        self.repair = repair or {}
        self.env = None
        self.visemes = None
        self.m = m
        self.cid = cid
        self.fps = m['fps']
        self.n = n_frames
        self.scale = float((bible or {}).get('scale', 1.0) or 1.0)
        self.keys = m['tracks']['characters'][cid]['keys']
        self.actions = [a for a in m['tracks']['actions'] if a['character'] == cid]
        self.blinks = set(m['tracks']['blinks'].get(cid, []))
        self.props = {p['id']: p for p in m['setting'].get('props', [])}
        self.root_xy = np.zeros((n_frames, 2))
        self.root_z = np.zeros(n_frames)
        self.yaw = np.zeros(n_frames)
        self._plan_root()
        self._plan_feet()

    # -- helpers
    def act_at(self, f, types=None):
        out = []
        for a in self.actions:
            if a['start_frame'] <= f < a['end_frame'] and (types is None or a['type'] in types):
                out.append(a)
        return out

    @staticmethod
    def phases(a):
        a0 = a['start_frame']
        a1 = a0 + a['anticipation_frames']
        a2 = a1 + a['main_frames']
        a3 = a2 + a['follow_through_frames']
        a4 = a3 + a['hold_frames']
        return a0, a1, a2, a3, a4

    # -- root trajectory
    def _plan_root(self):
        for f in range(self.n):
            p = pose_at(self.keys, f)
            self.root_xy[f] = p['position']
            self.yaw[f] = p['facing']
        sc = self.scale
        for a in self.actions:
            a0, a1, a2, a3, a4 = self.phases(a)
            t = a['type']
            if t in ('walk', 'run', 'jump'):
                p0 = np.array(a['params'].get('from') or pose_at(self.keys, a0)['position'], dtype=float)
                p1 = np.array(a['params']['to'], dtype=float)
                for f in range(a0, min(self.n, a4)):
                    if f < a1:
                        self.root_xy[f] = p0
                    elif f < a2:
                        u = (f - a1) / max(1, a2 - a1)
                        e = u if t == 'jump' else _ease(u, 'in_out')
                        self.root_xy[f] = p0 + (p1 - p0) * e
                    else:
                        self.root_xy[f] = p1
                if t == 'jump':
                    dist = float(np.linalg.norm(p1 - p0))
                    h = (0.45 + 0.12 * dist) * sc
                    for f in range(a1, min(self.n, a2)):
                        u = (f - a1) / max(1, a2 - a1)
                        self.root_z[f] = 4 * h * u * (1 - u)
                # Face the direction of travel while moving, then settle back.
                dv = p1 - p0
                if np.linalg.norm(dv) > 0.05:
                    move_yaw = math.degrees(math.atan2(dv[0], -dv[1]))
                    for f in range(a0, min(self.n, a4)):
                        base = self.yaw[f]
                        w = envelope(f, a0, a1, a2, max(a3, a2 + 1))
                        self.yaw[f] = base + wrap(move_yaw - base) * w
            elif t == 'turn':
                y0 = float(a['params'].get('from_facing', self.yaw[max(0, a0 - 1)]))
                y1 = float(a['params']['to_facing'])
                for f in range(a0, min(self.n, a4)):
                    if f < a1:
                        # Anticipation: a small counter-turn.
                        self.yaw[f] = y0 - 6 * math.copysign(1, wrap(y1 - y0)) * smooth((f - a0) / max(1, a1 - a0))
                    elif f < a2:
                        u = (f - a1) / max(1, a2 - a1)
                        self.yaw[f] = y0 + wrap(y1 - y0) * _ease(u, 'in_out')
                    else:
                        over = 4 * math.copysign(1, wrap(y1 - y0)) * (1 - smooth((f - a2) / max(1, a3 - a2 + 1)))
                        self.yaw[f] = y1 + (over if f < a3 else 0)
            elif t in ('hop', 'celebrate'):
                h = (0.28 if t == 'hop' else 0.42) * sc
                for f in range(a1, min(self.n, a2)):
                    u = (f - a1) / max(1, a2 - a1)
                    self.root_z[f] = 4 * h * u * (1 - u)
            elif t == 'stumble':
                fwd = direction(self.yaw[a0])
                for f in range(a0, min(self.n, a4)):
                    if f < a2:
                        u = (f - a0) / max(1, a2 - a0)
                        k = 0.22 * math.sin(math.pi * u) * sc
                    else:
                        k = 0.0
                    self.root_xy[f] = self.root_xy[f] + np.array(fwd) * k * 0.7

    # -- footsteps
    def _plan_feet(self):
        sc = self.scale
        p0 = pose_at(self.keys, 0)
        l, r = world_feet(self.root_xy[0], self.yaw[0], p0['feet']['stance'], sc)
        self.feet = {'l': Foot(l, self.yaw[0]), 'r': Foot(r, self.yaw[0])}
        # Stance changes and actions are planned in strict time order so every
        # step starts from the foot's real planted position at that moment.
        events = []
        prev = p0['feet']['stance']
        for k in self.keys[1:]:
            st = k['pose']['feet']['stance']
            if st != prev:
                events.append((k['frame'] - 8, 0, 'stance', k))
            prev = st
        for a in self.actions:
            events.append((a['start_frame'], 1, 'action', a))
        events.sort(key=lambda e: (e[0], e[1]))
        for _, _, kind, obj in events:
            if kind == 'stance':
                f = obj['frame']
                st = obj['pose']['feet']['stance']
                if self.act_at(f, S.LOCOMOTION | {'turn', 'fall_down', 'get_up', 'jump', 'hop', 'celebrate'}):
                    continue
                fc = min(self.n - 1, f)
                l, r = world_feet(self.root_xy[fc], self.yaw[fc], st, sc)
                self.feet['l'].step(f - 8, f, l, self.yaw[fc], 0.05 * sc)
                self.feet['r'].step(f - 2, f + 6, r, self.yaw[fc], 0.05 * sc)
                continue
            self._plan_action_feet(obj)
        for foot in self.feet.values():
            foot.swings.sort(key=lambda s: s[0])

    def _plan_action_feet(self, a):
        sc = self.scale
        if a is not None:
            a0, a1, a2, a3, a4 = self.phases(a)
            t = a['type']
            stance = pose_at(self.keys, min(self.n - 1, a4))['feet']['stance']
            if t in ('walk', 'run'):
                p0 = self.root_xy[a1]
                p1 = self.root_xy[min(self.n - 1, a2)]
                dist = float(np.linalg.norm(p1 - p0))
                stride = (0.5 if t == 'walk' else 0.95) * sc
                nsteps = max(2, int(math.ceil(dist / stride)) + 1)
                span = max(1, a2 - a1)
                h = (0.11 if t == 'walk' else 0.24) * sc
                order = ['l', 'r'] if (len(self.feet['l'].swings) % 2 == 0) else ['r', 'l']
                for i in range(nsteps):
                    fs = a1 + int(span * i / nsteps)
                    fe = a1 + int(span * (i + 1) / nsteps) + (2 if t == 'walk' else 0)
                    side = order[i % 2]
                    u = min(1.0, (i + 1) / nsteps)
                    target_f = min(self.n - 1, a1 + int(span * u))
                    yaw = self.yaw[target_f]
                    lpos, rpos = world_feet(self.root_xy[target_f], yaw, 'neutral', sc)
                    if i < nsteps - 1:
                        # Lead foot lands ahead of the hips.
                        fwd = np.array(list(direction(yaw)) + [0.0]) * stride * 0.25
                        lpos, rpos = lpos + fwd, rpos + fwd
                    self.feet[side].step(fs, min(fe, a2 + 6), lpos if side == 'l' else rpos, yaw, h)
                l, r = world_feet(self.root_xy[min(self.n - 1, a3)], self.yaw[min(self.n - 1, a3)], stance, sc)
                last = order[(nsteps) % 2]
                self.feet[last].step(a2, min(a3 + 2, a2 + 10), l if last == 'l' else r, self.yaw[min(self.n - 1, a3)], h * 0.5)
            elif t == 'turn':
                third = max(1, (a2 - a1) // 3)
                y1 = self.yaw[min(self.n - 1, a2)]
                l, r = world_feet(self.root_xy[a2 if a2 < self.n else self.n - 1], y1, stance, sc)
                first = 'l' if wrap(y1 - self.yaw[a0]) > 0 else 'r'
                second = 'r' if first == 'l' else 'l'
                self.feet[first].step(a1, a1 + 2 * third, l if first == 'l' else r, y1, 0.06 * sc)
                self.feet[second].step(a1 + third, a2 + 2, l if second == 'l' else r, y1, 0.06 * sc)
            elif t in ('jump', 'hop', 'celebrate'):
                land = min(self.n - 1, a2)
                for side in ('l', 'r'):
                    self.feet[side].free.append((a1, a2))
                l, r = world_feet(self.root_xy[land], self.yaw[land], stance, sc)
                self.feet['l'].plants.append((a2, l, self.yaw[land]))
                self.feet['r'].plants.append((a2, r, self.yaw[land]))
                for side in ('l', 'r'):
                    self.feet[side].plants.sort(key=lambda p: p[0])
            elif t == 'stumble':
                fwd = np.array(list(direction(self.yaw[a0])) + [0.0])
                l, r = world_feet(self.root_xy[a0], self.yaw[a0], 'neutral', sc)
                mid = a0 + (a2 - a0) // 2
                self.feet['r'].step(a0 + 1, mid, r + fwd * 0.26 * sc, self.yaw[a0], 0.08 * sc)
                self.feet['l'].step(a0 + 3, a2 - 2, l + fwd * 0.16 * sc, self.yaw[a0], 0.06 * sc)
                l2, r2 = world_feet(self.root_xy[min(self.n - 1, a3)], self.yaw[a0], stance, sc)
                self.feet['r'].step(a2, a2 + 8, r2, self.yaw[a0], 0.05 * sc)
                self.feet['l'].step(a2 + 4, a2 + 12, l2, self.yaw[a0], 0.05 * sc)
            elif t in ('fall_down',):
                end = self._end_of_state(a, 'get_up')
                for side in ('l', 'r'):
                    self.feet[side].free.append((a1, end))
            elif t == 'dance':
                for i, f in enumerate(range(a1, a2, max(6, self.fps // 3))):
                    side = 'l' if i % 2 == 0 else 'r'
                    l, r = world_feet(self.root_xy[f], self.yaw[f], 'neutral', sc)
                    self.feet[side].step(f, f + max(5, self.fps // 4), l if side == 'l' else r, self.yaw[f], 0.09 * sc)

    def _end_of_state(self, a, ender):
        for b in self.actions:
            if b['type'] == ender and b['start_frame'] >= a['start_frame']:
                return self.phases(b)[2]
        return self.n

    # -- per-frame targets
    def body_targets(self, f, pose):
        """Spine/neck/head angles and pelvis offset (before springs)."""
        lean = pose['torso']['lean_forward']
        side = pose['torso']['lean_side']
        twist = pose['torso']['twist']
        posture = pose['posture']
        # A slight baseline knee bend keeps the legs inside IK reach when the
        # weight shifts, so planted feet never lift off the ground.
        pelvis_drop = 0.03 + sum(float(r.get('pelvis_drop_extra', 0.0)) for r in self.repair.get('ranges', [])
                                 if r['start'] <= f < r['end'])
        head = dict(pose['head'])
        post = {'slumped': (12, -10, 0.02), 'crouch': (16, 6, 0.30), 'ready': (7, 2, 0.08),
                'proud': (-6, 6, 0.0)}.get(posture, (0, 0, 0))
        lean += post[0]
        head_pitch = head['pitch'] + post[1]
        pelvis_drop += post[2]
        weight = pose['feet']['weight']
        shift = {'left': 0.05, 'right': -0.05}.get(weight, 0.0)
        hip_roll = {'left': -3.0, 'right': 3.0}.get(weight, 0.0)
        breathe = math.sin(2 * math.pi * f / (self.fps * 3.6) + zlib.crc32(self.cid.encode()) % 7)
        tgt = {
            'pelvis': [0.0, hip_roll, 0.0],
            'spine': [-lean * 0.55 + 0.6 * breathe, side * 0.6, twist * 0.4],
            'chest': [-lean * 0.45, side * 0.4, twist * 0.6],
            'neck': [head_pitch * -0.4, 0.0, head['yaw'] * 0.3],
            'head': [head_pitch * -0.6, head['roll'], head['yaw'] * 0.7],
        }
        pelvis_loc = [shift + 0.008 * math.sin(2 * math.pi * f / (self.fps * 4.1)), 0.0, -pelvis_drop]
        shoulders = pose['shoulders']['raise'] * 0.06
        return tgt, pelvis_loc, shoulders

    def action_layers(self, f, tgt, pelvis_loc, arm_override, face_over):
        """Apply active actions to body targets. Returns (pelvis_z_extra, root_z_extra)."""
        sc = self.scale
        for a in self.actions:
            a0, a1, a2, a3, a4 = self.phases(a)
            end = a4 if a['type'] not in ('crouch', 'cower', 'fall_down') else self._end_of_state(
                a, 'stand_up' if a['type'] != 'fall_down' else 'get_up')
            if not (a0 <= f < max(end, a4)):
                continue
            t = a['type']
            w = envelope(f, a0, a1 if a1 > a0 else a0 + 1, a3, max(a4, a3 + 1))
            if t in ('jump', 'hop', 'celebrate'):
                crouch = 0.0
                if a0 <= f < a1:
                    crouch = (0.22 if t == 'jump' else 0.12) * smooth((f - a0) / max(1, a1 - a0))
                    tgt['spine'][0] -= 18 * smooth((f - a0) / max(1, a1 - a0))
                    arm_override['both_swing'] = 35 * smooth((f - a0) / max(1, a1 - a0))
                elif a1 <= f < a2:
                    u = (f - a1) / max(1, a2 - a1)
                    crouch = (0.22 if t == 'jump' else 0.12) * (1 - smooth(u * 3))
                    arm_override['both_swing'] = -70 * math.sin(math.pi * min(1.0, u * 1.4))
                    if t == 'celebrate':
                        arm_override['both_pose'] = 'raise'
                        arm_override['weight'] = 1.0
                elif a2 <= f < a3:
                    u = (f - a2) / max(1, a3 - a2)
                    crouch = 0.24 * math.sin(math.pi * min(1.0, u * 1.2)) * (1.0 if t == 'jump' else 0.6)
                    tgt['spine'][0] -= 10 * math.sin(math.pi * u)
                    if t == 'celebrate':
                        arm_override['right_pose'] = 'fist_pump'
                        arm_override['weight'] = 1 - smooth(u)
                pelvis_loc[2] -= crouch
            elif t in ('walk', 'run'):
                if a1 <= f < a3:
                    u = (f - a1) / max(1, a2 - a1)
                    freq = (2.0 if t == 'walk' else 3.0)
                    ph = 2 * math.pi * u * max(1, (a2 - a1) / self.fps) * freq / 2
                    amp = 22 if t == 'walk' else 40
                    arm_override['swing'] = amp * math.sin(ph) * w
                    pelvis_loc[2] -= (0.03 if t == 'walk' else 0.06) * abs(math.sin(ph)) * w
                    tgt['spine'][0] -= (4 if t == 'walk' else 14) * w
                    if t == 'run':
                        arm_override['elbow'] = -80 * w
            elif t == 'stumble':
                if a0 <= f < a3:
                    u = (f - a0) / max(1, a3 - a0)
                    tgt['spine'][0] -= 22 * math.sin(math.pi * u)
                    arm_override['windmill'] = (u, w)
                    face_over['eyes_open'] = max(face_over.get('eyes_open', 0), 1.25 * w)
            elif t in ('crouch', 'cower'):
                stand = self._end_of_state(a, 'stand_up')
                if f < stand:
                    u = smooth((f - a0) / max(1, a2 - a0))
                    pelvis_loc[2] -= 0.34 * u
                    tgt['spine'][0] -= 14 * u
                    if t == 'cower':
                        arm_override['both_pose'] = 'cower'
                        arm_override['weight'] = u
                        tgt['head'][0] += 14 * u
            elif t == 'stand_up':
                u = smooth((f - a0) / max(1, a2 - a0))
                pelvis_loc[2] -= 0.34 * (1 - u)
                tgt['spine'][0] -= 14 * (1 - u)
            elif t == 'fall_down':
                get_up = self._end_of_state(a, 'get_up')
                if f < get_up:
                    u = smooth((f - a1) / max(1, a2 - a1)) if f >= a1 else 0.0
                    pelvis_loc[2] -= 0.62 * u
                    pelvis_loc[1] += 0.0
                    tgt['spine'][0] += 18 * u
                    arm_override['both_pose'] = 'arms_out'
                    arm_override['weight'] = u * (1.0 if f < a3 else 0.4)
                    face_over['eyes_open'] = max(face_over.get('eyes_open', 0), 1.2 * w)
            elif t == 'get_up':
                u = smooth((f - a0) / max(1, a2 - a0))
                pelvis_loc[2] -= 0.62 * (1 - u)
                tgt['spine'][0] += 18 * (1 - u) - 20 * math.sin(math.pi * u)
            elif t == 'flinch':
                u = (f - a0) / max(1, a2 - a0)
                k = math.sin(math.pi * min(1.0, u)) if f < a2 else (1 - smooth((f - a2) / max(1, a4 - a2)))
                tgt['spine'][0] += 14 * k
                tgt['head'][0] += 10 * k
                pelvis_loc[2] -= 0.05 * k
                arm_override['both_pose'] = 'chest'
                arm_override['weight'] = k
                face_over['eyes_open'] = max(face_over.get('eyes_open', 0), 1.3 * k)
            elif t == 'look_around':
                u = (f - a1) / max(1, a2 - a1)
                if a1 <= f < a2:
                    tgt['head'][2] += 40 * math.sin(2 * math.pi * u) * w
                    face_over['look_yaw'] = 40 * math.sin(2 * math.pi * min(1, u + 0.06))
            elif t == 'nod':
                u = (f - a1) / max(1, a2 - a1)
                if a1 <= f < a2:
                    tgt['head'][0] += 12 * abs(math.sin(2 * math.pi * u)) * w
            elif t == 'head_shake':
                u = (f - a1) / max(1, a2 - a1)
                if a1 <= f < a2:
                    tgt['head'][2] += 18 * math.sin(3 * 2 * math.pi * u) * (1 - u * 0.5)
            elif t == 'talk':
                if a1 <= f < a2:
                    tgt['head'][0] += 3.0 * math.sin(2 * math.pi * (f - a1) / (self.fps * 0.55))
                    tgt['head'][2] += 4.0 * math.sin(2 * math.pi * (f - a1) / (self.fps * 1.7))
                    arm_override['talk_' + a['params'].get('hand', 'right')] = w
            elif t == 'dance':
                if a1 <= f < a2:
                    ph = 2 * math.pi * (f - a1) / (self.fps * 0.5)
                    pelvis_loc[2] -= 0.05 * abs(math.sin(ph))
                    tgt['spine'][1] += 6 * math.sin(ph / 2)
                    arm_override['both_pose'] = 'fist_pump'
                    arm_override['weight'] = 0.7 * w
                    arm_override['swing'] = 25 * math.sin(ph)
            elif t == 'shrug':
                k = w
                tgt['head'][1] += 8 * k
                arm_override['both_pose'] = 'shrug'
                arm_override['weight'] = k
                arm_override['shoulder_raise'] = 0.06 * k
            elif t == 'facepalm':
                tgt['head'][0] += 16 * w
                tgt['spine'][0] -= 6 * w
            elif t == 'think':
                tgt['head'][1] += 10 * w
                face_over['look_pitch'] = 18 * w
        _ = sc
        return pelvis_loc

    # -- arms
    def arm_angles(self, side, label, fkres, sc):
        """Angles (shoulder xyz, elbow x) for a static pose or an IK contact pose."""
        if label in R.IK_TARGETS:
            target = self.ik_world_target(side, label, fkres, sc)
            return self.solve_arm(side, target, fkres, sc)[:2]
        sh, el = R.ARM_POSES.get(label, R.ARM_POSES['rest'])
        if side == 'r':
            sh = (sh[0], -sh[1], -sh[2])
        return list(sh), el

    def ik_world_target(self, side, label, fkres, sc):
        mirror = -1.0 if side == 'r' else 1.0
        if label == 'chest':
            return local_point(fkres, 'chest', (0.08 * mirror, -0.30, 0.08), sc)
        if label == 'cross':
            return local_point(fkres, 'chest', (-0.18 * mirror, -0.30, 0.02), sc)
        if label == 'cover_mouth':
            return local_point(fkres, 'head', (0.0, -0.40, 0.17), sc)
        if label == 'chin':
            return local_point(fkres, 'head', R.CHIN_POINT, sc)
        if label == 'facepalm':
            return local_point(fkres, 'head', (0.05 * mirror, -0.36, 0.42), sc)
        if label == 'head_scratch':
            return local_point(fkres, 'head', (0.34 * mirror, 0.05, 0.50), sc)
        return local_point(fkres, 'chest', (0.3 * mirror, -0.3, 0.0), sc)

    def solve_arm(self, side, target_world, fkres, sc, palm=True):
        sh = 'shoulder_' + side
        root = np.array(R.OFFSET[sh], dtype=float) + np.array(self._loc.get(sh, (0, 0, 0)))
        tgt = to_local(fkres, 'chest', target_world, sc)
        mirror = -1.0 if side == 'r' else 1.0
        pole = norm(np.array([0.35 * mirror, 0.8, -0.45]))
        if not palm:
            upper, elbow, err, _ = two_bone(root, tgt, R.UPPER_ARM, R.FOREARM, pole, -1)
            return upper, elbow, err
        # The palm centre sits HAND_REACH beyond the wrist along the forearm.
        # Iterate: solve for a wrist target, read back the forearm direction.
        fdir = norm(tgt - root)
        for _ in range(4):
            upper, elbow, err, lower = two_bone(root, tgt - fdir * R.HAND_REACH, R.UPPER_ARM, R.FOREARM, pole, -1)
            fdir = lower
        return upper, elbow, err

    # -- legs
    def solve_leg(self, side, ankle_world, foot_yaw, fkres, sc):
        hip = 'hip_' + side
        root = np.array(R.OFFSET[hip], dtype=float)
        tgt = to_local(fkres, 'pelvis', ankle_world, sc)
        upper, knee, err, _ = two_bone(root, tgt, R.THIGH, R.SHIN, np.array([0.0, -1.0, 0.0]), +1)
        return upper, knee, err

    # -- main loop
    def solve(self, camera_at):
        fps, sc = self.fps, self.scale
        out = []
        springs = {k: Spring(0.25, 0.55) for k in ('spine', 'chest', 'neck', 'head', 'pelvis')}
        look_spring = Spring(0.2, 0.5)
        arm_springs = {'l': Spring(0.3, 0.6), 'r': Spring(0.3, 0.6)}
        held = {}
        prop_events = [e for e in self.m['tracks']['props'] if e['character'] == self.cid]
        for f in range(self.n):
            pose = pose_at(self.keys, f)
            tgt, pelvis_loc, shoulder_raise = self.body_targets(f, pose)
            arm_override = {}
            face_over = {}
            pelvis_loc = self.action_layers(f, tgt, pelvis_loc, arm_override, face_over)
            shoulder_raise += arm_override.get('shoulder_raise', 0.0)
            rot = {}
            for k in ('pelvis', 'spine', 'chest', 'neck', 'head'):
                rot[k] = list(springs[k].step(tgt[k]))
            # Eye-line: turn the head part of the way toward the target.
            self._loc = {'pelvis': pelvis_loc, 'shoulder_l': [0, 0, shoulder_raise], 'shoulder_r': [0, 0, shoulder_raise]}
            state = {'root': [self.root_xy[f][0], self.root_xy[f][1], self.root_z[f]], 'yaw': self.yaw[f],
                     'rot': rot, 'loc': self._loc}
            fkres = fk(state, sc)
            eye_target = self.eye_target_world(pose['eye_target'], f, fkres, camera_at)
            extra = np.zeros(2)
            if eye_target is not None and pose['eye_target'].get('kind') != 'forward':
                local = to_local(fkres, 'neck', eye_target, sc) - np.array(R.OFFSET['head'])
                want_yaw = math.degrees(math.atan2(local[0], -local[1]))
                want_pitch = math.degrees(math.atan2(local[2] - 0.34, math.hypot(local[0], local[1])))
                follow = 0.45
                extra = np.array([max(-20, min(20, want_pitch * follow * 0.6)),
                                  max(-35, min(35, want_yaw * follow))])
            extra = look_spring.step(extra)
            rot['head'][0] -= float(extra[0])
            rot['head'][2] += float(extra[1])
            state['rot'] = rot
            fkres = fk(state, sc)
            # Arms
            arms = {}
            for side, name in (('l', 'left'), ('r', 'right')):
                label = pose['arms'][name]
                sh_ang, el_ang = self.arm_angles(side, label, fkres, sc)
                ang = np.array(list(sh_ang) + [el_ang], dtype=float)
                ov_pose = arm_override.get(name + '_pose') or arm_override.get('both_pose')
                if ov_pose:
                    if ov_pose == 'cower':
                        tw = local_point(fkres, 'head', (0.12 * (1 if side == 'l' else -1), -0.32, 0.62), sc)
                        u_, e_, _ = self.solve_arm(side, tw, fkres, sc)
                        oa = np.array(list(u_) + [e_])
                    else:
                        s2, e2 = self.arm_angles(side, ov_pose, fkres, sc)
                        oa = np.array(list(s2) + [e2])
                    ang = ang + (oa - ang) * arm_override.get('weight', 1.0)
                if 'both_swing' in arm_override:
                    ang[0] += arm_override['both_swing']
                if 'swing' in arm_override:
                    ang[0] += arm_override['swing'] * (1 if side == 'l' else -1)
                    ang[3] += arm_override.get('elbow', -18) * 0.6 if 'elbow' in arm_override else -14
                if 'windmill' in arm_override:
                    u, ww = arm_override['windmill']
                    ph = 2 * math.pi * u * 2 + (0 if side == 'l' else math.pi)
                    ang[0] += -70 * math.sin(ph) * ww
                    ang[1] += (-50 if side == 'l' else 50) * ww
                if arm_override.get('talk_' + name):
                    tw_ = arm_override['talk_' + name]
                    g = math.sin(2 * math.pi * f / (fps * 1.3))
                    ang[0] += (-22 + 10 * g) * tw_
                    ang[3] += (-45 + 15 * g) * tw_
                arms[side] = ang
            # Hand actions (IK, exact contact)
            contact = {}
            for a in self.actions:
                a0, a1, a2, a3, a4 = self.phases(a)
                if not (a0 <= f < a4) or a['type'] not in ('wave', 'point', 'reach', 'grab', 'push', 'drop',
                                                         'facepalm', 'think'):
                    continue
                side = 'l' if a['params'].get('hand', 'right') == 'left' else 'r'
                w = envelope(f, a0, a1 if a1 > a0 else a0 + 1, a3 if a['type'] != 'facepalm' else a4 - 4,
                             max(a4, a3 + 1))
                t = a['type']
                if t == 'wave':
                    s2, e2 = self.arm_angles(side, 'wave', fkres, sc)
                    wav = np.array(list(s2) + [e2], dtype=float)
                    if a1 <= f < a2:
                        ph = 2 * math.pi * (f - a1) / (fps * 0.36)
                        wav[2] += (22 if side == 'l' else -22) * math.sin(ph)
                        wav[3] += 14 * math.sin(ph + 0.6)
                    arms[side] = arms[side] + (wav - arms[side]) * w
                elif t in ('point',):
                    tgt_w = self.eye_target_world(pose['eye_target'], f, fkres, camera_at)
                    if tgt_w is None:
                        tgt_w = local_point(fkres, 'chest', (0, -2.0, 0.2), sc)
                    shp = local_point(fkres, 'shoulder_' + side, (0, 0, 0), sc)
                    dvec = norm(np.asarray(tgt_w) - shp)
                    reach_pt = shp + dvec * (R.UPPER_ARM + R.FOREARM + R.HAND_REACH) * sc * 0.97
                    u_, e_, _ = self.solve_arm(side, reach_pt, fkres, sc)
                    arms[side] = arms[side] + (np.array(list(u_) + [e_]) - arms[side]) * w
                elif t in ('reach', 'grab', 'push'):
                    prop = self.props.get(a['params'].get('prop'))
                    if prop:
                        pt = np.array(prop['position'], dtype=float)
                        pt[2] += 0.15 * prop.get('scale', 1)
                        if t == 'push' and a2 <= f < a3:
                            pt = pt + np.array(list(direction(self.yaw[f])) + [0]) * 0.04
                        u_, e_, err = self.solve_arm(side, pt, fkres, sc)
                        arms[side] = arms[side] + (np.array(list(u_) + [e_]) - arms[side]) * w
                        if w > 0.99:
                            contact[side] = {'prop': prop['id'], 'error_m': round(err * sc, 4)}
                elif t in ('facepalm', 'think'):
                    label = 'facepalm' if t == 'facepalm' else 'chin'
                    tw = self.ik_world_target(side, label, fkres, sc)
                    u_, e_, _ = self.solve_arm(side, tw, fkres, sc)
                    arms[side] = arms[side] + (np.array(list(u_) + [e_]) - arms[side]) * w
                    if w > 0.99:
                        contact[side] = {'target': label}
            for side in ('l', 'r'):
                if side in contact:
                    arm_springs[side].reset(arms[side])
                    final = arms[side]
                else:
                    final = arm_springs[side].step(arms[side])
                rot['shoulder_' + side] = [float(final[0]), float(final[1]), float(final[2])]
                rot['elbow_' + side] = [float(final[3]), 0.0, 0.0]
                rot['wrist_' + side] = [0.0, 0.0, 0.0]
            state['rot'] = rot
            fkres = fk(state, sc)
            # Legs
            feet_info = {}
            for side in ('l', 'r'):
                pos, fyaw, planted = self.feet[side].sample(f)
                hip_w = fkres['hip_' + side][1]
                if pos is None:
                    # Airborne or falling: tuck relative to the hips.
                    Rp = fkres['pelvis'][0]
                    tuck = 0.18 if self.root_z[f] > 0.05 else 0.05
                    pos = hip_w + Rp @ (np.array([0.0, -0.06, -(R.THIGH + R.SHIN) + tuck]) * sc)
                    sitting = any(a['type'] == 'fall_down' and self.phases(a)[1] <= f for a in self.actions)
                    if sitting and self.root_z[f] < 0.01:
                        fwd = np.array(list(direction(self.yaw[f])) + [0.0])
                        pos = hip_w + fwd * 0.55 * sc
                        pos[2] = R.ANKLE_HEIGHT * sc
                    fyaw = self.yaw[f]
                    ankle = pos
                else:
                    ankle = pos + np.array([0, 0, R.ANKLE_HEIGHT * sc])
                upper, knee, err = self.solve_leg(side, ankle, fyaw, fkres, sc)
                rot['hip_' + side] = upper
                rot['knee_' + side] = [knee, 0.0, 0.0]
                feet_info[side] = {'planned': [round(float(v), 4) for v in ankle], 'contact_expected': bool(planted),
                                   'ik_error_m': round(err * sc, 4)}
            state['rot'] = rot
            fkres = fk(state, sc)
            for side in ('l', 'r'):
                # Keep the sole flat: ankle cancels the accumulated leg rotation.
                Rk = fkres['knee_' + side][0]
                _, fyaw, _ = self.feet[side].sample(f)
                desired = rz((fyaw if fyaw is not None else self.yaw[f]) * D2R)
                rot['ankle_' + side] = m_to_euler(Rk.T @ desired)
            state['rot'] = rot
            fkres = fk(state, sc)
            face = self.face(f, pose, fkres, eye_target, face_over)
            # Props held by this character
            for e in prop_events:
                if e['frame'] == f:
                    if e['event'] == 'attach':
                        held[e['prop']] = 'l' if e.get('hand') == 'left' else 'r'
                    elif e['event'] == 'detach':
                        held.pop(e['prop'], None)
            hands = {}
            for side in ('l', 'r'):
                hands[side] = [round(float(v), 4) for v in local_point(fkres, 'wrist_' + side, (0, 0, -0.10), sc)]
            props_out = {}
            for pid, side in held.items():
                Rw, _ = fkres['wrist_' + side]
                props_out[pid] = {'location': hands[side], 'rotation': m_to_euler(Rw)}
            out.append({
                'root': [round(float(v), 5) for v in state['root']], 'yaw': round(float(self.yaw[f]), 4),
                'rot': {k: [round(float(x), 3) for x in v] for k, v in rot.items()},
                'loc': {k: [round(float(x), 4) for x in v] for k, v in self._loc.items()},
                'face': face, 'feet': feet_info, 'hands': hands, 'props': props_out, 'contact': contact,
                'expression': pose.get('expression'),
                'actions': [a['type'] for a in self.act_at(f)],
                'airborne': bool(self.root_z[f] > 0.02),
            })
        return out

    def eye_target_world(self, et, f, fkres, camera_at):
        kind = (et or {}).get('kind')
        if kind == 'camera':
            return np.array(camera_at(f), dtype=float) if camera_at else None
        if kind == 'point':
            return np.array(et.get('point', [0, -3, 1.6]), dtype=float)
        if kind == 'prop':
            p = self.props.get(et.get('id'))
            if p:
                v = np.array(p['position'], dtype=float)
                v[2] += 0.5 * p.get('scale', 1)
                return v
        if kind == 'character':
            other = self.others.get(et.get('id')) if hasattr(self, 'others') else None
            if other is not None:
                xy = other.root_xy[f]
                return np.array([xy[0], xy[1], 1.65 * other.scale + other.root_z[f]])
        return None

    def face(self, f, pose, fkres, eye_target, over):
        F = R.FACE
        brows = pose['brows']
        eyes_open = max(pose['eyes']['open'], over.get('eyes_open', 0))
        closure = 0.0
        for b in self.blinks:
            d = f - b
            if -1 <= d <= 4:
                closure = max(closure, [0.55, 1.0, 1.0, 0.7, 0.35, 0.1][d + 1])
        # Pupils from the real head transform toward the target.
        px = pz = 0.0
        if eye_target is not None:
            loc = to_local(fkres, 'head', eye_target, self.scale)
            eye_c = np.array([0.0, R.FACE_FRONT_Y, F['eye_z']])
            d = loc - eye_c
            yaw = math.atan2(d[0], -d[1]) if abs(d[1]) > 1e-6 else 0.0
            pitch = math.atan2(d[2], math.hypot(d[0], d[1]))
            px = max(-1.0, min(1.0, yaw / (35 * D2R)))
            pz = max(-1.0, min(1.0, pitch / (30 * D2R)))
        if 'look_yaw' in over:
            px = max(-1.0, min(1.0, over['look_yaw'] / 35))
        if 'look_pitch' in over:
            pz = max(-1.0, min(1.0, over['look_pitch'] / 30))
        mouth_shape_w = self.mouth_weights(f)
        return {
            'brow_l': [round(brows['inner'], 3), round(brows['outer'], 3), round(brows.get('asym', 0), 3)],
            'brow_r': [round(brows['inner'], 3), round(brows['outer'], 3), round(-brows.get('asym', 0), 3)],
            'eye_open': round(float(eyes_open), 3),
            'blink': round(closure, 3),
            'squint': round(pose['eyes']['squint'], 3),
            'pupil': [round(px, 3), round(pz, 3)],
            'mouth': mouth_shape_w,
            'speaking': bool(self.visemes and self.visemes[f]),
        }

    def mouth_weights(self, f):
        """Expression shape weights blended across key transitions + visemes."""
        keys = self.keys
        if f <= keys[0]['frame'] or len(keys) == 1:
            a = b = keys[0]['pose']['mouth']
            u = 0.0
        else:
            a = b = keys[-1]['pose']['mouth']
            u = 0.0
            for k0, k1 in zip(keys, keys[1:]):
                if k0['frame'] <= f < k1['frame']:
                    blend = k1.get('blend_frames') or min(10, k1['frame'] - k0['frame'])
                    start = k1['frame'] - blend
                    a, b = k0['pose']['mouth'], k1['pose']['mouth']
                    u = 0.0 if f < start else smooth((f - start) / max(1, blend))
                    break
        w = {}
        for shape, amt in ((a['shape'], 1 - u), (b['shape'], u)):
            if shape != 'neutral' and amt > 0:
                w['shape:' + shape] = round(w.get('shape:' + shape, 0) + amt, 3)
        opening = lerp(a['open'], b['open'], u)
        # QA lip-sync repairs: sample the mouth timing earlier/later within a line and open it wider.
        shift, boost = (getattr(self, 'mouth_repair', None) or {}).get(f, (0, 1.0))
        fv = min(max(f - shift, 0), self.n - 1)
        vis = self.visemes[fv] if self.visemes else {}
        env = getattr(self, 'env', None)
        e = float(env[fv]) if env is not None and fv < len(env) else None
        in_line = bool(getattr(self, 'line_frames', None)) and f in self.line_frames
        if vis or in_line or (e is not None and e > 0.12):
            # While speaking, the jaw follows the real audio amplitude; text
            # visemes choose the mouth shape; expressions keep only a hint of the
            # corners (a strong smile/frown shape would hold the mouth closed).
            for k in list(w):
                w[k] = round(w[k] * 0.3 / boost, 3)
            gain = 1.0 if e is None else (0.08 + 0.92 * e)
            for v, amt in vis.items():
                if v != 'rest':
                    w['vis:' + v] = round(min(1.0, amt * gain * boost), 3)
            if e is not None and e > 0.12 and not any(k.startswith('vis:') and v > 0.2 for k, v in w.items()):
                w['vis:AI'] = round(min(1.0, 0.7 * e * boost), 3)
        elif opening > 0.05:
            w['vis:AI'] = round(min(1.0, opening * 0.6), 3)
        return w


def build_visemes(m, cid, n, alignments):
    """Per-frame viseme weights for a character from its lines.

    alignments: line_id -> {'words': [...] relative to audio start, 'kind': 'measured'|'estimated'}
    """
    fps = m['fps']
    keys = []
    kinds = set()
    for ln in m['lines']:
        if ln['speaker'] != cid:
            continue
        al = (alignments or {}).get(ln['id'])
        start_s = ln['start_frame'] / fps
        if al and al.get('words'):
            words = al['words']
            kinds.add(al.get('kind', 'measured'))
        else:
            dur = (ln.get('measured_frames') or ln['est_frames']) / fps
            words = VIS.estimate_words(ln['text'], 0.0, dur)
            kinds.add('estimated_from_text')
        keys += VIS.viseme_keys(words, start_s)
    return VIS.frame_weights(keys, fps, n), sorted(kinds)


def shot_lens(shot):
    cam = shot['camera']
    if cam.get('lens_mm'):
        return float(cam['lens_mm'])
    order = S.FRAMINGS
    tight = max(order.index(cam['framing_start']), order.index(cam['framing_end']))
    return float(LENS_BY_FRAMING[order[tight]])


def apply_shot_repairs(shot, params):
    """Return a copy of a shot with QA repair adjustments applied to its camera."""
    if not params:
        return shot
    import copy as _copy
    s = _copy.deepcopy(shot)
    cam = s['camera']
    order = S.FRAMINGS
    step = (1 if params.get('camera_tighter') else 0) - (1 if params.get('camera_wider') else 0)
    if step:
        for k in ('framing_start', 'framing_end'):
            i = order.index(cam[k]) + step
            cam[k] = order[max(0, min(len(order) - 1, i))]
        cam['lens_mm'] = None
    if params.get('face_camera'):
        cam['side'] = 'front'
        cam['subject'] = params['face_camera']
    if params.get('camera_smooth'):
        cam['shake'] = 0.0
        cam['ease'] = 'in_out'
    return s


_CODE_VERSION = None


def code_version():
    """Fingerprint of the motion code; part of the solved-motion cache key, so an upgrade re-solves."""
    global _CODE_VERSION
    if _CODE_VERSION is None:
        h = hashlib.sha256()
        here = os.path.dirname(os.path.abspath(__file__))
        for name in (os.path.join(here, 'solver.py'), os.path.join(here, 'rig.py'), os.path.join(here, 'visemes.py'),
                     os.path.join(here, 'sets.py'),
                     os.path.join(here, '..', 'manifest', 'compile.py'), os.path.join(here, '..', 'manifest', 'geometry.py')):
            with open(name, 'rb') as f:
                h.update(f.read())
        _CODE_VERSION = h.hexdigest()[:16]
    return _CODE_VERSION


def _facing_camera_score(frames, chars, subj, f0):
    """Mean cosine (ground plane) between where the subject looks and the direction to the camera."""
    c = chars[subj]
    total = 0.0
    for i, fr in enumerate(frames):
        f = f0 + i
        root = np.array(c.root_xy[f], dtype=float)
        to_cam = np.array(fr['location'][:2], dtype=float) - root
        if np.linalg.norm(to_cam) < 1e-6:
            continue
        et = pose_at(c.keys, f).get('eye_target') or {}
        if et.get('kind') == 'camera':
            total += 1.0
            continue
        tgt = c.eye_target_world(et, f, None, None)
        look = (tgt[:2] - root) if tgt is not None else None
        if look is None or np.linalg.norm(look) < 0.05:
            look = np.array(direction(c.yaw[f]), dtype=float)
        total += float(np.dot(look / np.linalg.norm(look), to_cam / np.linalg.norm(to_cam)))
    return total / max(1, len(frames))


def _blocked_frames(frames, chars, subj, f0):
    """Frames in which another character's body is between the camera and the subject's face."""
    c = chars[subj]
    bad = []
    for i, fr in enumerate(frames):
        f = f0 + i
        cam = np.array(fr['location'], dtype=float)
        face = np.array([c.root_xy[f][0], c.root_xy[f][1], R.FACE_CENTER_Z * c.scale + c.root_z[f]])
        if line_hits_others(cam, face, chars, subj, f):
            bad.append(f)
    return bad


def view_targets(m, chars, subj, f):
    """Points the camera of a shot must see at frame f: the subject's face (every face in a two-shot),
    or a prop's rest position. Faces use the same body-axis point as _blocked_frames."""
    def face(c):
        return np.array([c.root_xy[f][0], c.root_xy[f][1], R.FACE_CENTER_Z * c.scale + c.root_z[f]])
    if subj in chars:
        return [face(chars[subj])]
    if subj == 'two_shot':
        return [face(c) for c in chars.values()]
    if isinstance(subj, str) and subj.startswith('prop:'):
        for p in m['setting'].get('props', []):
            if p['id'] == subj[5:]:
                return [np.array(p['position'], dtype=float) + np.array([0.0, 0.0, 0.15])]
    return []


def _scenery_blocked_frames(m, frames, chars, subj, f0, boxes):
    """Frames whose camera is inside a scenery box, or whose view of the subject passes through one.

    boxes: sets.box_arrays of set pieces (and, for faces, the big static story props).
    """
    from . import sets as SETS
    if boxes is None or not frames:
        return []
    cams = np.array([fr['location'] for fr in frames], dtype=float)
    bad = SETS.inside(boxes, cams, pad=0.08).any(axis=1)
    p0, p1, owner = [], [], []
    for i in range(len(frames)):
        for t in view_targets(m, chars, subj, f0 + i):
            d = t - cams[i]
            n = float(np.linalg.norm(d))
            if n > 0.1:
                p0.append(cams[i])
                p1.append(t - d / n * 0.05)
                owner.append(i)
    if p0:
        hits = SETS.segment_hits(boxes, np.array(p0), np.array(p1)).any(axis=1)
        for i, h in zip(owner, hits):
            bad[i] |= bool(h)
    return [f0 + i for i in np.nonzero(bad)[0].tolist()]


def line_hits_others(cam, face, chars, subj, f, near_subject=0.35):
    """True when the sight line camera -> face passes through another character's body cylinder."""
    seg = face - cam
    length = float(np.linalg.norm(seg))
    if length < 1e-6:
        return False
    stop = max(0.0, 1.0 - near_subject / length)
    for oid, o in chars.items():
        if oid == subj:
            continue
        ctr = np.array([o.root_xy[f][0], o.root_xy[f][1]])
        rad = 0.42 * o.scale
        top = (R.HEIGHT + 0.12) * o.scale + o.root_z[f]
        for t in np.linspace(0.0, stop, 48):
            p = cam + seg * t
            if o.root_z[f] - 0.05 <= p[2] <= top and np.hypot(p[0] - ctr[0], p[1] - ctr[1]) < rad:
                return True
    return False


def solve_camera(m, chars, n, repair=None, set_layout=None):
    """Per-frame camera {location, look_at, lens} for the whole timeline.

    set_layout: the sets.layout of this manifest (computed here when not given). Its pieces, plus the big
    static story props, are scenery the camera must not sit in or look through.
    """
    from ..manifest.compile import camera_state
    from . import sets as SETS
    if set_layout is None:
        set_layout = SETS.layout(m, {cid: c.scale for cid, c in chars.items()})
    set_boxes = SETS.piece_boxes(set_layout)
    # Faces: set pieces and big story props. Props as subjects: set pieces only (a key may sit in a chest).
    scenery_faces = SETS.box_arrays(set_boxes + SETS.prop_boxes(m))
    scenery_props = SETS.box_arrays(set_boxes)
    out = [None] * n
    aspect = m['height'] / m['width']
    for shot in m['shots']:
        shot = apply_shot_repairs(shot, ((repair or {}).get('shots') or {}).get(shot['id']))
        cam = shot['camera']
        lens = shot_lens(shot)
        sensor_v = SENSOR_W * aspect if aspect > 1 else SENSOR_W
        vfov = 2 * math.atan(sensor_v / (2 * lens))
        subj = cam['subject']
        f0 = shot['start_frame']

        def subject_xyz(f):
            if subj in chars:
                c = chars[subj]
                return np.array([c.root_xy[f][0], c.root_xy[f][1], 0.0]), (R.HEIGHT + 0.12) * c.scale, c.yaw[f0]
            if subj == 'two_shot':
                pts = [np.array([c.root_xy[f][0], c.root_xy[f][1], 0.0]) for c in chars.values()]
                cen = sum(pts) / len(pts)
                spread = max(np.linalg.norm(a - b) for a in pts for b in pts) if len(pts) > 1 else 0.0
                return cen, R.HEIGHT + 0.12 + spread * 1.1, 0.0
            if isinstance(subj, str) and subj.startswith('prop:'):
                for p in m['setting'].get('props', []):
                    if p['id'] == subj[5:]:
                        return np.array(p['position'], dtype=float), 0.9 * p.get('scale', 1), 0.0
            return np.zeros(3), R.HEIGHT, 0.0

        base_target, height, facing0 = subject_xyz(f0)
        az0 = wrap(facing0 + CAMERA_SIDE_DEG.get(cam['side'], 0))
        follow = Spring(0.15 if (((repair or {}).get('shots') or {}).get(shot['id']) or {}).get('camera_smooth') else 0.3, 0.6)
        follow.reset(base_target)
        follow_z = Spring(0.3, 0.6)
        follow_z.reset([0.0])
        face_z = R.FACE_CENTER_Z * (chars[subj].scale if subj in chars else 1.0)
        rnd = (zlib.crc32(shot['id'].encode()) % 1000) / 1000.0
        shot_rp = (((repair or {}).get('shots') or {}).get(shot['id'])) or {}

        def frames_at(az_offset):
            follow.reset(base_target)
            follow_z.reset([0.0])
            return [frame_at(f, az_offset) for f in range(f0, min(n, shot['end_frame']))]

        def frame_at(f, az_offset):
            st = camera_state(shot, f)
            visible = st['visible_height_ratio'] * height
            dist = (visible / 2) / math.tan(vfov / 2)
            if subj in chars and visible < height:
                # Measure tight framings to the face plane, not the body axis.
                dist += R.HEAD_HALF_DEPTH * chars[subj].scale
            # Composition: keep 12% headroom above the head; very tight shots
            # centre on the face; wide shots keep the feet in frame.
            aim_z = height - 0.38 * visible
            if visible < 0.8 * height and subj in chars:
                aim_z = min(aim_z, face_z + 0.06)
            aim_z = max(aim_z, min(height * 0.5, visible * 0.32))
            if cam['move'] == 'follow':
                cur, _, _ = subject_xyz(f)
                target = follow.step(cur)
                if subj in chars:
                    aim_z += float(follow_z.step([chars[subj].root_z[f] * 0.95])[0])
            else:
                target = base_target
            u = st['progress']
            az = az0 + az_offset
            if cam['move'] in ('orbit_left', 'orbit_right'):
                az += (25.0 if cam['move'] == 'orbit_left' else -25.0) * u
            pitch = ANGLE_PITCH.get(cam['angle'], 0.0)
            if cam['move'] == 'crane_up':
                pitch += 14 * u
            elif cam['move'] == 'crane_down':
                pitch -= 14 * u
            dx, dy = direction(az)
            horiz = dist * math.cos(pitch * D2R)
            loc = np.array([target[0] + dx * horiz, target[1] + dy * horiz, aim_z + dist * math.sin(pitch * D2R)])
            look = np.array([target[0], target[1], aim_z])
            if cam['move'] in ('truck_left', 'truck_right', 'pan_left', 'pan_right'):
                rxv, ryv = -dy, dx
                sgn = -1.0 if cam['move'].endswith('left') else 1.0
                off = np.array([rxv, ryv, 0.0]) * 0.9 * u * sgn
                if cam['move'].startswith('truck'):
                    loc = loc + off
                look = look + off
            if cam['shake'] > 0:
                k = cam['shake'] * 0.015 * dist
                look = look + np.array([k * math.sin(f * 0.9 + rnd * 7), 0.0, k * math.sin(f * 1.3 + rnd * 3)])
            return {'shot': shot['id'], 'location': [round(float(v), 4) for v in loc],
                    'look_at': [round(float(v), 4) for v in look], 'lens': lens,
                    'sensor_width': SENSOR_W, 'framing': st['framing']}

        # Another character or a piece of scenery between the camera and the subject would hide the
        # subject (or put the camera inside it). Swing the camera around the subject, the same for the
        # whole shot so it does not jump: fewest blocked frames first, then the side the subject looks
        # toward (where their eye targets are), then the smallest swing. The unswung camera stays a
        # candidate (ranked last on ties) for when every swing is worse.
        scenery = scenery_faces if (subj in chars or subj == 'two_shot') else scenery_props

        def blocked(trial):
            bad = set(_blocked_frames(trial, chars, subj, f0)) if subj in chars else set()
            return bad | set(_scenery_blocked_frames(m, trial, chars, subj, f0, scenery))

        frames = frames_at(0.0)
        base_blocked = blocked(frames)
        if base_blocked:
            offsets = [35, -35, 55, -55, 80, -80] + ([110, -110, 140, -140] if shot_rp.get('camera_clear') else [])
            ranked = []
            for i, off in enumerate(offsets):
                trial = frames_at(float(off))
                score = _facing_camera_score(trial, chars, subj, f0) if subj in chars else 0.0
                ranked.append((len(blocked(trial)), -round(score, 2), i, off, trial))
            score0 = _facing_camera_score(frames, chars, subj, f0) if subj in chars else 0.0
            ranked.append((len(base_blocked), -round(score0, 2), len(offsets), 0, frames))
            _, _, _, off, frames = min(ranked, key=lambda r: r[:3])
            if off:
                for fr in frames:
                    fr['occlusion_avoided_deg'] = off
        for i, fr in enumerate(frames):
            out[f0 + i] = fr
    for f in range(n):
        if out[f] is None:
            out[f] = out[f - 1] if f else {'shot': None, 'location': [0, -6, 1.5], 'look_at': [0, 0, 1],
                                           'lens': 35.0, 'sensor_width': SENSOR_W, 'framing': 'wide'}
    return out


def solve(m, bibles, alignments=None, repair=None, envelopes=None):
    """Solve the whole timeline. bibles: cast id -> character bible dict.

    repair: {'shots': {shot_id: {...camera knobs}},
             'characters': {cast_id: {'ranges': [{'start': f0, 'end': f1, 'pelvis_drop_extra': m}]}},
             'lines': {line_id: {'mouth_shift_frames': k, 'mouth_gain': g}}}
    Character repairs are limited to the repaired shot's frames so other,
    already-rendered shots stay consistent.
    """
    n = m['duration_frames']
    chars = {}
    for c in m['cast']:
        cs = CharacterSolver(m, c['id'], bibles.get(c['id']), n,
                             ((repair or {}).get('characters') or {}).get(c['id']))
        chars[c['id']] = cs
    for cs in chars.values():
        cs.others = chars
    from . import sets as SETS
    set_layout = SETS.layout(m, {cid: cs.scale for cid, cs in chars.items()})
    camera = solve_camera(m, chars, n, repair, set_layout)
    viseme_kinds = {}
    for cid, cs in chars.items():
        cs.visemes, viseme_kinds[cid] = build_visemes(m, cid, n, alignments)
        cs.env = (envelopes or {}).get(cid)
        cs.line_frames = set()
        cs.mouth_repair = {}
        for ln in m['lines']:
            if ln['speaker'] == cid:
                cs.line_frames.update(range(ln['start_frame'], ln['est_end_frame']))
                lr = ((repair or {}).get('lines') or {}).get(ln['id'])
                if lr:
                    shift = max(-6, min(6, int(lr.get('mouth_shift_frames', 0))))
                    boost = max(1.0, min(2.0, float(lr.get('mouth_gain', 1.0))))
                    for f in range(ln['start_frame'], min(n, ln['est_end_frame'] + 3)):
                        cs.mouth_repair[f] = (shift, boost)
        if cs.env is not None:
            viseme_kinds[cid] = viseme_kinds[cid] + ['amplitude_from_audio']
    frames = {cid: cs.solve(lambda f: camera[f]['location']) for cid, cs in chars.items()}
    return {'n': n, 'fps': m['fps'], 'camera': camera, 'characters': frames, 'viseme_timing': viseme_kinds,
            'scales': {cid: cs.scale for cid, cs in chars.items()}, 'props': solve_props(m, frames, n),
            'set': set_layout}


def solve_props(m, frames, n):
    """World position of every prop per frame: at rest, held, or falling after a drop."""
    fps = m['fps']
    out = {}
    for p in m['setting'].get('props', []):
        pid = p['id']
        rest = [float(v) for v in p['position']]
        rot = [0.0, 0.0, float(p.get('rotation', 0))]
        cur = list(rest)
        vz = 0.0
        falling = False
        seq = []
        for f in range(n):
            held = None
            for cid, fr in frames.items():
                if pid in fr[f].get('props', {}):
                    held = fr[f]['props'][pid]
            if held:
                cur = list(held['location'])
                rot = list(held['rotation'])
                falling, vz = True, 0.0
            elif falling:
                vz -= 9.81 / fps
                cur[2] = max(rest[2], cur[2] + vz / fps)
                if cur[2] <= rest[2]:
                    falling = False
            seq.append({'location': [round(v, 4) for v in cur], 'rotation': [round(v, 3) for v in rot]})
        out[pid] = seq
    return out
