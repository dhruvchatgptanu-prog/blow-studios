"""Camera and facing geometry shared by the validator and the renderer plan.

Angles follow the manifest convention: yaw in degrees about +z, 0 = facing the
establishing camera (-y), positive = turning toward the character's left (+x
when facing the camera). A direction with yaw a is (sin a, -cos a).

Camera *side* is relative to the subject's facing at the start of the shot:
'three_quarter_left' places the camera 35 degrees toward the subject's left.
"""
import math

from . import schema as S
from .compile import pose_at

CAMERA_SIDE_DEG = {'front': 0, 'three_quarter_left': 35, 'three_quarter_right': -35, 'profile_left': 90,
                   'profile_right': -90, 'over_shoulder_left': 155, 'over_shoulder_right': -155, 'back': 180}
ANGLE_PITCH = {'eye': 0.0, 'low': -14.0, 'high': 18.0, 'overhead': 62.0}


def wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def direction(yaw_deg):
    a = math.radians(yaw_deg)
    return (math.sin(a), -math.cos(a))


def subject_info(m, subject, frame):
    """(target point [x, y, z], height, facing) for a camera subject at frame."""
    chars = m['tracks']['characters']
    height = 2.0
    if subject in chars:
        p = pose_at(chars[subject]['keys'], frame)
        return [p['position'][0], p['position'][1], 0.0], height, p['facing']
    if subject == 'two_shot':
        pts = [pose_at(t['keys'], frame) for t in chars.values()]
        if not pts:
            return [0.0, 0.0, 0.0], height, 0.0
        cx = sum(p['position'][0] for p in pts) / len(pts)
        cy = sum(p['position'][1] for p in pts) / len(pts)
        spread = max(math.dist(a['position'], b['position']) for a in pts for b in pts) if len(pts) > 1 else 0
        return [cx, cy, 0.0], height + spread * 0.9, 0.0
    if isinstance(subject, str) and subject.startswith('prop:'):
        pid = subject[5:]
        for p in m['setting'].get('props', []):
            if p['id'] == pid:
                return list(p['position']), 0.8 * p.get('scale', 1), 0.0
    return [0.0, 0.0, 0.0], height, 0.0


def camera_azimuth(m, shot):
    _, _, facing = subject_info(m, shot['camera']['subject'], shot['start_frame'])
    return wrap(facing + CAMERA_SIDE_DEG.get(shot['camera']['side'], 0))


def face_visible(pose, cam_azimuth, limit=75.0):
    return abs(wrap(pose['facing'] + pose['head']['yaw'] - cam_azimuth)) <= limit


def camera_pose(m, shot, frame, sensor_mm=36.0):
    """Camera location/rotation for a frame of a shot.

    The subject's vertical extent visible in frame is FRAMING_HEIGHT * height;
    distance follows from the lens and the vertical sensor size of a 9:16
    frame. Returns dict(location, look_at, lens_mm, framing_height).
    """
    from .compile import camera_state
    cam = shot['camera']
    st = camera_state(shot, frame)
    target, height, _ = subject_info(m, cam['subject'], shot['start_frame'] if cam['move'] != 'follow' else frame)
    visible = st['visible_height_ratio'] * height
    lens = cam.get('lens_mm', 35) or 35
    aspect = m['height'] / m['width']
    sensor_v = sensor_mm * (aspect if aspect > 1 else 1)
    vfov = 2 * math.atan(sensor_v / (2 * lens))
    dist = (visible / 2) / math.tan(vfov / 2)
    # Aim point: the upper part of the subject for close framings (the face).
    ratio = st['visible_height_ratio']
    aim_z = height * (0.86 if ratio <= 0.5 else 0.72 if ratio <= 1.0 else 0.5)
    if cam['subject'].startswith('prop:'):
        aim_z = target[2] + 0.3
    az = camera_azimuth(m, shot)
    span = max(1, shot['end_frame'] - shot['start_frame'])
    u = st['progress']
    move = cam['move']
    if move in ('orbit_left', 'orbit_right'):
        az += (25.0 if move == 'orbit_left' else -25.0) * u
    pitch = ANGLE_PITCH.get(cam['angle'], 0.0)
    if move == 'crane_up':
        pitch += 14.0 * u
    elif move == 'crane_down':
        pitch -= 14.0 * u
    dx, dy = direction(az)
    horiz = dist * math.cos(math.radians(pitch))
    loc = [target[0] + dx * horiz, target[1] + dy * horiz, aim_z + dist * math.sin(math.radians(pitch))]
    look = [target[0], target[1], aim_z]
    if move in ('truck_left', 'truck_right', 'pan_left', 'pan_right'):
        # Lateral axis of the camera (screen-right as seen through the lens).
        rx, ry = -dy, dx
        sgn = -1.0 if move.endswith('left') else 1.0
        amount = 0.9 * u * sgn
        if move.startswith('truck'):
            loc[0] += rx * amount
            loc[1] += ry * amount
        look[0] += rx * amount
        look[1] += ry * amount
    _ = span
    return {'location': [round(v, 4) for v in loc], 'look_at': [round(v, 4) for v in look], 'lens_mm': lens,
            'framing': st['framing'], 'azimuth': round(az, 2), 'pitch': pitch}


def framing_face_ok(framing):
    return framing in S.FACE_READABLE
