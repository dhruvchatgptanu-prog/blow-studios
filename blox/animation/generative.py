"""Generative shots through Runway's documented task API (optional renderer).

Continuity: the first frame of the shot is rendered deterministically in
Blender (same characters, set, framing) and sent as the reference image, with
a prompt built from the manifest beats. A generative model does not follow
second-by-second directions exactly; QA reviews these shots with the vision
model and marks them uncertain when it cannot.

The provider task id is stored before polling; an ambiguous submission is
never repeated automatically (see ``paid.run``).
"""
import base64
import io
import os

from PIL import Image

from .. import config, db as dbmod, media, netsafe, paid, repo, vault
from ..http import request
from ..util import Blocked, Waiting
from . import blender as BL

API = 'https://api.dev.runwayml.com/v1/'
VERSION = '2024-11-06'
DURATIONS = range(2, 11)


def headers():
    key = vault.get('RUNWAYML_API_SECRET')
    if not key:
        raise Blocked('Connect a Runway API key to render generative shots', state='needs_credentials')
    return {'Authorization': 'Bearer ' + key, 'X-Runway-Version': VERSION}


def prompt_for(m, s, characters):
    beats = [b for b in m['beats'] if b['shot'] == s['id']]
    parts = ['Original 3D block-character animation, Roblox-inspired style, not captured gameplay.',
             f'Setting: {m["setting"].get("description") or m["setting"]["preset"]}, {m["setting"]["time_of_day"]}.']
    for c in m['cast']:
        ch = characters.get(c['character_id']) or {}
        rules = '; '.join((ch.get('bible') or {}).get('visual_rules', []))
        parts.append(f'{ch.get("name", c["id"])}: {rules}.')
    for b in beats:
        if b['description']:
            parts.append(b['description'])
        for ch in b['characters']:
            for a in ch['actions']:
                if a['starts_here']:
                    parts.append(f'{ch["id"]} {a["type"].replace("_", " ")}.')
    cam = s['camera']
    parts.append(f'Camera: {cam["framing_start"].replace("_", " ")} to {cam["framing_end"].replace("_", " ")}, '
                 f'{cam["move"].replace("_", " ")}. Smooth motion, stable faces and costumes, grounded feet, '
                 'no text, no logos, no extra characters.')
    return ' '.join(parts)[:950]


def _reference_image(m, bibles, solved, s, out_dir, prefs):
    res = BL.render_range(m, bibles, solved, s['start_frame'], s['start_frame'] + 1, os.path.join(out_dir, 'ref'),
                          prefs, quality='preview')
    still = res['preview']
    im = Image.open(still).convert('RGB').resize((720, 1280))
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=90)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


def render_shot(ctx, vid, mid, m, s, sh, out_dir, prefs):
    d = dbmod.get()
    detail = dict(sh['detail'] or {})
    rw = detail.get('runway') or {}
    attempt = sh['repair_attempts']
    if not rw.get('task_id'):
        from ..pipeline import _cast, solved_for
        cast = _cast(m, d)
        solved, _ = solved_for(vid, mid, m, d)
        secs = (s['end_frame'] - s['start_frame']) / m['fps']
        dur = max(2, min(10, int(-(-secs // 1))))
        image = _reference_image(m, {cid: c['bible'] for cid, c in cast.items()}, solved, s, out_dir, prefs)
        body = {'model': prefs['production']['runway_model'], 'promptText': prompt_for(m, s, repo.characters(d=d)),
                'promptImage': image, 'ratio': '720:1280', 'duration': dur}
        est = dur * prefs['budget']['prices']['runway_per_second']

        def submit():
            r = request('runway', 'POST', API + 'image_to_video', headers=headers(), json=body, timeout=(10, 90))
            return {'id': r.json()['id']}
        res = paid.run(f'runway:{vid}:{mid}:{s["id"]}:{attempt}', provider='runway', operation='image_to_video',
                       category='animation' if attempt == 0 else 'repair', estimate=est, fn=submit, video_id=vid,
                       summary={'shot': s['id'], 'duration': dur}, provider_ref=lambda r: r['id'])
        rw = {'task_id': res['id'] if 'id' in res else res.get('provider_ref'), 'duration': dur}
        detail['runway'] = rw
        repo.upsert_shot(vid, mid, s['id'], 'runway', d, provider_ref=rw['task_id'], detail=detail, status='generating')
        raise Waiting('Runway task submitted', delay=20)
    r = request('runway', 'GET', API + 'tasks/' + rw['task_id'], headers=headers(), timeout=(10, 40))
    j = r.json()
    status = j.get('status')
    if status in ('PENDING', 'THROTTLED', 'RUNNING'):
        raise Waiting(f'Runway task {status.lower()}', delay=15)
    if status != 'SUCCEEDED':
        repo.upsert_shot(vid, mid, s['id'], 'runway', d, status='failed', error=f'Runway task {status}')
        raise Blocked(f'Runway generation for shot {s["id"]} ended with {status}. Regenerate the shot or switch it '
                      'to the Blender renderer.', state='needs_review')
    url = (j.get('output') or [None])[0]
    if not url:
        raise Blocked('Runway reported success without an output file', state='needs_review')
    raw = os.path.join(out_dir, 'runway_raw.mp4')
    os.makedirs(out_dir, exist_ok=True)
    netsafe.fetch(url, 'runway', dest=raw)
    info = media.video_stream(media.probe(raw))
    nw, nh = int(info['width']), int(info['height'])
    frames = s['end_frame'] - s['start_frame']
    out = os.path.join(out_dir, 'shot.mp4')
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-i', raw, '-vf',
               f'scale={m["width"]}:{m["height"]}:flags=lanczos:force_original_aspect_ratio=increase,'
               f'crop={m["width"]}:{m["height"]},fps={m["fps"]},tpad=stop_mode=clone:stop_duration=2',
               '-frames:v', str(frames), '-an', '-c:v', 'libx264', '-crf', '16', '-pix_fmt', 'yuv420p', out])
    detail['upscaled'] = (nw, nh) != (m['width'], m['height'])
    detail['native'] = [nw, nh]
    repo.upsert_shot(vid, mid, s['id'], 'runway', d, status='done', file=repo.rel(out), native_w=nw, native_h=nh,
                     attempts=sh['attempts'] + 1, detail=detail, error='')
    return {'video': out}

