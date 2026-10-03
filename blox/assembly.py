"""Final assembly: shots + dialogue + SFX + music + captions -> MP4, cover, thumbnail.

All intermediates (stems, caption file, joined picture) are kept next to the
final file so a repair can redo only the affected stage.
"""
import json
import os
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import captions_layout as CL, config, media
from .voice import audio as A, synth

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00141414,&H64000000,-1,0,0,0,100,100,0,0,1,{outline},0,2,{ml},{mr},{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(frame, fps):
    t = frame / float(fps)
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    mnt, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f'{h}:{mnt:02d}:{s:02d}.{c:02d}'


def _ass_text(s):
    return s.replace('\\', '').replace('{', '').replace('}', '').replace('\n', ' ')


def write_captions(m, prefs, path, width, height, positions=None):
    pr = prefs['production']
    mt = CL.metrics(width, height, pr)
    out = [ASS_HEADER.format(w=width, h=height, font=pr['caption_font'], size=mt['font_size'], outline=mt['outline'],
                             ml=mt['left'], mr=width - mt['right'], mv=height - mt['bottom'])]
    layout = []
    top_y = CL.top_anchor(height, pr)
    for c in m['captions']:
        pos = (positions or {}).get(c['id'], 'bottom')
        lines, _ = CL.wrap(c['text'], width, height, pr)
        tag = r'{\fad(40,40)}' if pos == 'bottom' else r'{\an8\fad(40,40)}'
        text = tag + r'\N'.join(_ass_text(l) for l in lines)
        mv = 0 if pos == 'bottom' else top_y
        out.append(f'Dialogue: 0,{_ass_time(c["start_frame"], m["fps"])},{_ass_time(c["end_frame"], m["fps"])},Cap,,0,0,'
                   f'{mv},,{text}\n')
        bx, _ = CL.box(c['text'], width, height, pr, pos)
        layout.append({'id': c['id'], 'text': c['text'], 'lines': lines, 'box': bx, 'start_frame': c['start_frame'],
                       'end_frame': c['end_frame'], 'timing': c['timing'], 'position': pos})
    with open(path, 'w', encoding='utf-8') as f:
        f.write(''.join(out))
    return layout


def _place(track, samples, start_s, gain=1.0):
    s = int(round(start_s * A.SR))
    if s >= len(track):
        return
    e = min(len(track), s + len(samples))
    track[s:e] += samples[:e - s] * gain


def duck_envelope(dialog, duck_db, threshold_db=-42.0, attack_s=0.06, release_s=0.35):
    block = int(A.SR * 0.01)
    env = A.db(A.rms_envelope(dialog, A.SR, 0.01))
    target = np.where(env > threshold_db, -duck_db, 0.0)
    out = np.zeros_like(target)
    g = 0.0
    a = 1 - np.exp(-0.01 / attack_s)
    r = 1 - np.exp(-0.01 / release_s)
    # Look ahead slightly so the duck starts before the first syllable.
    look = 3
    target = np.concatenate([target[look:], np.zeros(look)])
    for i, tg in enumerate(target):
        k = a if tg < g else r
        g += (tg - g) * k
        out[i] = g
    gains_db = np.repeat(out, block)
    if len(gains_db) < len(dialog):
        gains_db = np.concatenate([gains_db, np.zeros(len(dialog) - len(gains_db))])
    return 10 ** (gains_db[:len(dialog)] / 20), out


def mix(m, line_audio, prefs, work_dir, music_asset=None):
    pr = prefs['production']
    fps = m['fps']
    total = m['duration_frames'] / fps
    n = int(round(total * A.SR))
    dialog = np.zeros(n)
    placements = []
    for ln in m['lines']:
        la = line_audio.get(ln['id'])
        if not la:
            continue
        s = A.decode(la['file'])
        start = ln['start_frame'] / fps
        _place(dialog, s, start)
        placements.append({'line': ln['id'], 'start_s': round(start, 3), 'end_s': round(start + len(s) / A.SR, 3)})
    sfx = np.zeros(n)
    for x in m['sfx']:
        clip = synth.sfx(x['cue'], seed=7)
        _place(sfx, clip / (np.max(np.abs(clip)) or 1.0) * 0.6, x['frame'] / fps, 10 ** (x['gain_db'] / 20))
    if pr['music'] == 'none':
        music = np.zeros(n)
        music_source = 'none'
    elif pr['music'] == 'asset' and music_asset:
        raw = A.decode(music_asset)
        reps = int(np.ceil(n / max(1, len(raw))))
        music = np.tile(raw, reps)[:n]
        music_source = 'owner_asset'
    else:
        music = synth.music_track([(mu['frame'] / fps, mu['cue']) for mu in m['music']], total)[:n]
        if len(music) < n:
            music = np.concatenate([music, np.zeros(n - len(music))])
        music_source = 'generated'
    music = music * 10 ** (pr['music_gain_db'] / 20)
    gains, env_db = duck_envelope(dialog, pr['duck_db'])
    music_ducked = music * gains
    master = dialog + sfx * 0.7 + music_ducked
    os.makedirs(work_dir, exist_ok=True)
    paths = {k: os.path.join(work_dir, f'{k}.wav') for k in ('dialog', 'sfx', 'music', 'master_pre', 'master')}
    A.write_wav(paths['dialog'], dialog)
    A.write_wav(paths['sfx'], sfx)
    A.write_wav(paths['music'], music_ducked)
    peak = np.max(np.abs(master)) or 1.0
    if peak > 0.98:
        master = master / peak * 0.98
    stereo = np.stack([master, master], axis=1)
    A.write_wav(paths['master_pre'], stereo)
    qa = prefs['qa']
    A.loudnorm(paths['master_pre'], paths['master'], target=qa['loudness_target_lufs'],
               tp=min(-1.5, qa['true_peak_max_dbtp'] - 0.5), channels=2)
    speech_frac = float(np.mean(env_db < -1)) if len(env_db) else 0.0
    return {'paths': paths, 'placements': placements, 'music_source': music_source,
            'ducking': {'duck_db': pr['duck_db'], 'fraction_ducked': round(speech_frac, 3)}}


def concat_shots(m, shot_files, work_dir):
    listing = os.path.join(work_dir, 'concat.txt')
    parts = []
    for s in m['shots']:
        src = shot_files[s['id']]
        if s.get('transition_in') == 'fade_in':
            faded = os.path.join(work_dir, f'fade_{s["id"]}.mp4')
            media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-i', src, '-vf', 'fade=t=in:st=0:d=0.3', '-c:v',
                       'libx264', '-crf', '16', '-pix_fmt', 'yuv420p', '-an', faded])
            src = faded
        dst = os.path.join(work_dir, f'part_{s["id"]}.mp4')
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy(src, dst)
        parts.append(os.path.basename(dst))
    with open(listing, 'w') as f:
        f.write(''.join(f"file '{p}'\n" for p in parts))
    joined = os.path.join(work_dir, 'joined.mp4')
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', 'concat.txt', '-an',
               '-c', 'copy', 'joined.mp4'], cwd=work_dir)
    return joined


def final_encode(m, joined, master_wav, captions_ass, work_dir, burn_captions=True):
    fps = m['fps']
    dur = m['duration_frames'] / fps
    filters = []
    for s in m['shots']:
        if s.get('transition_in') == 'whip' and s['start_frame'] > 0:
            a = (s['start_frame'] - 3) / fps
            b = (s['start_frame'] + 3) / fps
            filters.append(f"avgblur=sizeX=48:sizeY=1:enable='between(t,{a:.3f},{b:.3f})'")
    if burn_captions and captions_ass:
        fontsdir = os.path.dirname(CL.font_path())
        filters.append(f'ass={os.path.basename(captions_ass)}:fontsdir={fontsdir}')
    filters.append(f'fps={fps}')
    vf = ','.join(filters)
    out = os.path.join(work_dir, 'final.mp4')
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-i', os.path.basename(joined), '-i', os.path.basename(master_wav),
               '-map', '0:v:0', '-map', '1:a:0', '-vf', vf, '-af', f'apad,atrim=0:{dur:.4f}',
               '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', str(fps),
               '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
               '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-t', f'{dur:.4f}',
               '-movflags', '+faststart', 'final.mp4'], cwd=work_dir)
    return out


def _font(size):
    return ImageFont.truetype(CL.font_path(), size)


def thumbnails(m, joined, work_dir, title):
    fps = m['fps']
    cover = os.path.join(work_dir, 'cover.png')
    media.extract_frame(joined, m['cover_frame'], fps, cover)
    im = Image.open(cover).convert('RGB')
    w, h = im.size
    th = im.copy()
    d = ImageDraw.Draw(th)
    size = max(24, int(h * 0.05))
    f = _font(size)
    words = title.upper().split()
    lines, cur = [], ''
    for wd in words:
        trial = (cur + ' ' + wd).strip()
        if cur and f.getlength(trial) > w * 0.84:
            lines.append(cur)
            cur = wd
        else:
            cur = trial
    if cur:
        lines.append(cur)
    y = int(h * 0.13)
    for line in lines[:3]:
        tw = f.getlength(line)
        d.text(((w - tw) / 2, y), line, font=f, fill=(255, 255, 255), stroke_width=max(3, size // 9),
               stroke_fill=(20, 20, 20))
        y += int(size * 1.15)
    vert = os.path.join(work_dir, 'thumbnail.jpg')
    th.save(vert, quality=92)
    # 16:9 version: blurred fill behind the vertical frame.
    land = im.resize((1280, int(1280 * h / w))).crop((0, 0, 1280, 720)).filter(ImageFilter.GaussianBlur(18))
    fg = th.resize((int(720 * w / h), 720))
    land.paste(fg, ((1280 - fg.width) // 2, 0))
    landp = os.path.join(work_dir, 'thumbnail_16x9.jpg')
    land.save(landp, quality=92)
    return {'cover': cover, 'thumbnail': vert, 'thumbnail_16x9': landp}


def assemble(m, shot_files, line_audio, prefs, work_dir, title, music_asset=None, telemetry_paths=None):
    os.makedirs(work_dir, exist_ok=True)
    joined = concat_shots(m, shot_files, work_dir)
    jinfo = media.probe(joined)
    vs = media.video_stream(jinfo)
    width, height = int(vs['width']), int(vs['height'])
    mixed = mix(m, line_audio, prefs, work_dir, music_asset)
    cap_path = os.path.join(work_dir, 'captions.ass')
    positions = None
    if telemetry_paths:
        from .qa.motion import load_telemetry
        tele = load_telemetry([p for p in telemetry_paths if p and os.path.exists(p)])
        positions = CL.choose_positions(m['captions'], tele, width, height, prefs['production'])
    layout = write_captions(m, prefs, cap_path, width, height, positions) if prefs['production']['captions'] else []
    final = final_encode(m, joined, mixed['paths']['master'], cap_path if layout else None, work_dir)
    thumbs = thumbnails(m, joined, work_dir, title)
    report = {'final': final, 'joined': joined, 'width': width, 'height': height, 'mix': mixed,
              'captions': layout, 'captions_file': cap_path if layout else None, **thumbs,
              'native_render_size': [width, height]}
    with open(os.path.join(work_dir, 'assembly.json'), 'w') as f:
        json.dump(report, f, default=str, indent=1)
    return report
