"""Host side of the Blender renderer: plan a shot, run Blender, encode, collect telemetry.

What this renderer controls exactly (deterministic, frame-accurate):
  root position/facing, pelvis height and weight shift, spine/chest lean and
  twist, neck/head yaw-pitch-roll, both arms (FK presets or IK contact), both
  legs (IK to planted footsteps), brows (inner/outer/asymmetry), eyelids
  (openness, blinks, squint), pupils (eye-line), mouth (11 expression shapes +
  9 viseme groups), held props, and the camera (framing, lens, moves, shake).
What it does NOT do: fingers (hands are blocks), cloth/hair simulation,
  physically simulated contacts or crowds. Directions outside the vocabulary
  in ``manifest.schema`` are rejected by the validator, not approximated.
"""
import json
import os
import shutil

from .. import config, media
from . import rig as R
from . import sets as SETS

SCRIPT = os.path.join(os.path.dirname(__file__), 'blender_scene.py')
# Subtle camera depth of field (production preference 'depth_of_field'), focused on the subject's face
# every frame. This is the f-number at a 45 mm lens; the renderer scales it with the lens squared so the
# background blur stays about the same from medium to close shots. Eyes and mouth lie within a few cm of
# the focus plane and stay sharp (well under a pixel at 1080 px); scenery several metres behind softens
# by a few pixels. It costs about a quarter more CPU per frame in EEVEE, hence off by default.
DOF_FSTOP = 4.0
QUALITY = {
    'preview': {'scale': 0.5, 'samples': 4, 'engine': 'BLENDER_EEVEE'},
    'final': {'scale': 1.0, 'samples': None, 'engine': None},
}


def available():
    return shutil.which(config.BLENDER_BIN) is not None


def rig_spec():
    return {
        'joints': [[n, p, list(o)] for n, p, o in R.JOINTS],
        'parts': [[j, n, list(s), list(c), slot, b] for j, n, s, c, slot, b in R.PARTS],
        'face': R.FACE, 'face_front_y': R.FACE_FRONT_Y,
        'chin_point': list(R.CHIN_POINT), 'head_center': list(R.HEAD_CENTER),
        'mouth_shapes': {k: list(v) for k, v in R.MOUTH_SHAPES.items()},
        'visemes': {k: list(v) for k, v in R.VISEMES.items()},
        'palette_slots': R.PALETTE_SLOTS,
    }


def build_plan(m, cast_specs, solved, frame_start, frame_end, out_dir, telemetry_path, prefs, quality='final',
               telemetry_only=False):
    pr = prefs['production']
    q = QUALITY[quality]
    w = int(m['width'] * q['scale']) // 2 * 2
    h = int(m['height'] * q['scale']) // 2 * 2
    frames = {cid: solved['characters'][cid][frame_start:frame_end] for cid in solved['characters']}
    # The set layout the camera solver avoided; recomputed (deterministically) for older solved files.
    lay = solved.get('set') or SETS.layout(m, solved.get('scales'))
    return {
        'version': 1,
        'fps': m['fps'], 'width': w, 'height': h,
        'engine': q['engine'] or pr['blender_engine'],
        'samples': q['samples'] or pr['blender_samples'],
        'frame_start': frame_start, 'frame_end': frame_end,
        'out_dir': str(out_dir), 'telemetry_path': str(telemetry_path),
        'rig': rig_spec(),
        'setting': m['setting'],
        'set': lay,
        'shot_subjects': {s['id']: s['camera']['subject'] for s in m['shots']},
        'dof_fstop': DOF_FSTOP if pr.get('depth_of_field') else None,
        'cast': cast_specs,
        'frames': frames,
        'camera': solved['camera'][frame_start:frame_end],
        'props': {pid: seq[frame_start:frame_end] for pid, seq in solved.get('props', {}).items()},
        'telemetry_only': telemetry_only,
        'bloom': m['setting'].get('time_of_day') in ('sunset', 'night'),
    }


def cast_specs(m, bibles):
    out = []
    for c in m['cast']:
        b = bibles.get(c['id']) or {}
        out.append({'id': c['id'], 'scale': b.get('scale', 1.0), 'palette': b.get('palette', {}),
                    'costume': b.get('costume', {})})
    return out


def render_range(m, bibles, solved, frame_start, frame_end, work_dir, prefs, quality='final', on_progress=None,
                 should_stop=None, telemetry_only=False):
    """Render frames [frame_start, frame_end) to an MP4. Returns dict with paths."""
    if not available():
        raise media.MediaError('Blender is not installed or BLENDER_BIN is wrong')
    os.makedirs(work_dir, exist_ok=True)
    frames_dir = os.path.join(work_dir, 'frames')
    if os.path.isdir(frames_dir):
        shutil.rmtree(frames_dir)
    os.makedirs(frames_dir)
    tele = os.path.join(work_dir, 'telemetry.jsonl')
    plan = build_plan(m, cast_specs(m, bibles), solved, frame_start, frame_end, frames_dir, tele, prefs, quality,
                      telemetry_only)
    plan_path = os.path.join(work_dir, 'plan.json')
    with open(plan_path, 'w') as f:
        json.dump(plan, f)

    def line(text):
        if text.startswith('BLOX ') and on_progress:
            on_progress(text[5:])

    media.stream([config.BLENDER_BIN, '-b', '--factory-startup', '-noaudio', '--python-exit-code', '1',
                  '-P', SCRIPT, '--', plan_path], on_line=line, should_stop=should_stop)
    result = {'telemetry': tele, 'plan': plan_path, 'width': plan['width'], 'height': plan['height'],
              'engine': plan['engine'], 'frames': frame_end - frame_start}
    if telemetry_only:
        return result
    out = os.path.join(work_dir, 'shot.mp4')
    media.encode_frames(os.path.join(frames_dir, 'frame_%05d.png'), out, m['fps'], start_number=frame_start)
    # Keep one still for previews/QA evidence, then free the PNG sequence.
    mid = frame_start + (frame_end - frame_start) // 2
    still = os.path.join(frames_dir, f'frame_{mid:05d}.png')
    if os.path.exists(still):
        shutil.copy(still, os.path.join(work_dir, 'preview.png'))
    shutil.rmtree(frames_dir, ignore_errors=True)
    result['video'] = out
    result['preview'] = os.path.join(work_dir, 'preview.png')
    return result
