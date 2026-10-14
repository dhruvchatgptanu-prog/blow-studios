"""Optional multimodal review of short motion windows (not single stills).

For every cut and every action, a strip of frames spanning the motion is
composited and sent to a vision-capable model with a strict JSON schema. Its
findings are probabilistic: high-confidence problems fail, the rest are marked
uncertain. When no model is configured the review is reported as not run.
"""
import os

from PIL import Image, ImageDraw

from .. import llm
from ..manifest.compile import tc
from . import motion as MO

ISSUE_TYPES = ['deformation', 'extra_limb', 'missing_limb', 'duplication', 'morphing', 'floating', 'sliding',
               'prop_glitch', 'camera_jump', 'unreadable_expression', 'lighting_flicker', 'text_artifact', 'other']

SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['strips'],
    'properties': {'strips': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['strip_id', 'observed_motion', 'issues'],
        'properties': {
            'strip_id': {'type': 'string'},
            'observed_motion': {'type': 'string'},
            'issues': {'type': 'array', 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['type', 'frame_label', 'observation', 'confidence', 'severity'],
                'properties': {
                    'type': {'type': 'string', 'enum': ISSUE_TYPES},
                    'frame_label': {'type': 'string'},
                    'observation': {'type': 'string'},
                    'confidence': {'type': 'number'},
                    'severity': {'type': 'string', 'enum': ['critical', 'major', 'minor']},
                }}},
        }}}},
}

SYSTEM = ('You are a strict animation QA reviewer. Each image is a left-to-right strip of consecutive sampled '
          'frames from a vertical block-character animation, labelled with timestamps. Compare frames within a '
          'strip to judge motion. Report only problems you can actually see: deformed or broken body parts, extra '
          'or missing limbs, duplicated characters, identity/costume changes, floating or sliding feet, props that '
          'pop or vanish, camera jumps inside a shot, unreadable expressions, lighting flicker, stray text. '
          'Describe observed_motion factually. Do not speculate; if unsure, use low confidence.')


def strips(m, video, work_dir, max_strips=10):
    fps = m['fps']
    wins = []
    for s in m['shots'][1:]:
        c = s['start_frame']
        wins.append(('cut_' + s['id'], [c - 4, c - 1, c, c + 1, c + 4]))
    for a in m['tracks']['actions']:
        a0 = a['start_frame']
        a1 = a0 + a['anticipation_frames']
        a2 = a1 + a['main_frames']
        wins.append((f'action_{a["id"]}_{a["type"]}', [a0, a1, (a1 + a2) // 2, a2, min(a['end_frame'] - 1, a2 + 4)]))
    wins = wins[:max_strips]
    out = []
    os.makedirs(work_dir, exist_ok=True)
    for sid, frames in wins:
        frames = [max(0, min(m['duration_frames'] - 1, f)) for f in frames]
        tiles = []
        for f in frames:
            img = Image.fromarray(MO.decode_rgb_frame(video, f, fps)).resize((216, 384))
            d = ImageDraw.Draw(img)
            d.rectangle((0, 0, 216, 20), fill=(0, 0, 0))
            d.text((4, 4), f'{tc(f, fps)} f{f}', fill=(255, 255, 255))
            tiles.append(img)
        strip = Image.new('RGB', (216 * len(tiles), 384))
        for i, t in enumerate(tiles):
            strip.paste(t, (216 * i, 0))
        path = os.path.join(work_dir, f'{sid}.jpg')
        strip.save(path, quality=85)
        out.append({'id': sid, 'frames': frames, 'path': path})
    return out


def run(ck, m, video, work_dir, prefs, video_id, render_id, has_generative):
    mode = prefs['qa']['vision_review']
    if mode == 'off' or not llm.available():
        reason = 'disabled in settings' if mode == 'off' else 'no vision-capable model connected'
        sev = 'major' if (has_generative or mode == 'required') else 'info'
        status = 'uncertain' if sev == 'major' else 'skipped'
        ck.add('vision_review', 'Multimodal review of motion windows', 'visual', status, sev,
               {'reason': reason, 'note': 'Deterministic telemetry and pixel checks still ran. Perceptual defects '
                'such as subtle deformation are only covered by this review.'}, 0.5, 'vision_model')
        return
    ss = strips(m, video, work_dir)
    content = [{'type': 'text', 'text': 'Strips: ' + ', '.join(f'{s["id"]} (frames {s["frames"]})' for s in ss)}]
    for s in ss:
        content.append({'type': 'text', 'text': 'strip_id: ' + s['id']})
        content.append(llm.image_part(s['path'], 'high'))
    data, _ = llm.chat_json(f'qa_vision:{video_id}:{render_id}', system=SYSTEM, content=content,
                            schema_name='motion_review', schema=SCHEMA, model=prefs['production']['vision_model'],
                            video_id=video_id, operation='qa_vision', category='vision', est_in=1500, est_out=1500,
                            images=len(ss), p=prefs, max_output_tokens=4000)
    by_id = {s['id']: s for s in ss}
    n_issues = 0
    for st in data['strips']:
        s = by_id.get(st['strip_id'])
        if not s:
            continue
        for iss in st['issues']:
            n_issues += 1
            conf = max(0.0, min(1.0, float(iss['confidence'])))
            status = 'fail' if conf >= 0.7 else 'uncertain'
            shot = MO.shot_at(m, s['frames'][len(s['frames']) // 2])
            ck.add(f'vision:{st["strip_id"]}:{n_issues}', f'Vision review: {iss["type"].replace("_", " ")}', 'visual',
                   status, iss['severity'] if status == 'fail' else ('major' if iss['severity'] != 'minor' else 'minor'),
                   {'observation': iss['observation'], 'frame_label': iss['frame_label'],
                    'observed_motion': st['observed_motion'], 'strip_frames': s['frames']},
                   conf, 'vision_model', frames=(s['frames'][0], s['frames'][-1]),
                   target={'kind': 'shot', 'id': shot['id'] if shot else None},
                   repair={'action': 're_render_shot', 'shot': shot['id']} if shot and status == 'fail' else None)
    ck.add('vision_review', 'Multimodal review of motion windows', 'visual', 'pass', 'info',
           {'strips_reviewed': len(ss), 'issues_reported': n_issues}, 0.6, 'vision_model')
