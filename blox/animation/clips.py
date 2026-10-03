"""Shots made from the owner's uploaded footage (kept from the original release)."""
import os

from .. import config, media


def render_shot(m, s, out_dir):
    clip = s.get('clip') or {}
    asset = clip.get('asset')
    if not asset or '/' in asset or asset.startswith('.'):
        raise ValueError('Invalid clip asset')
    src = config.MEDIA_DIR / asset
    if not src.exists():
        raise ValueError('Clip asset not found')
    trim = float(clip.get('trim_s', 0) or 0)
    frames = s['end_frame'] - s['start_frame']
    need = frames / m['fps']
    have = media.duration(src)
    if have + 0.05 < trim + need:
        raise ValueError(f'Shot {s["id"]}: the clip is {have:.2f}s but needs {trim + need:.2f}s (trim + duration)')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, 'shot.mp4')
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-ss', f'{trim:.3f}', '-i', str(src), '-vf',
               f'scale={m["width"]}:{m["height"]}:force_original_aspect_ratio=increase,crop={m["width"]}:{m["height"]},'
               f'setsar=1,fps={m["fps"]}', '-frames:v', str(frames), '-an', '-c:v', 'libx264', '-crf', '16',
               '-pix_fmt', 'yuv420p', out])
    info = media.video_stream(media.probe(src))
    return {'video': out, 'width': int(info['width']), 'height': int(info['height'])}
