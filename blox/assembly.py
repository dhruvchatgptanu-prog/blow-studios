"""Final assembly: shots + dialogue + SFX + music + captions -> MP4, cover, thumbnail.

All intermediates (stems, caption file, joined picture) are kept next to the
final file so a repair can redo only the affected stage.

Picture layers: the shots are joined as rendered (``joined_cut.mp4``, the
clean picture when anything is drawn on it), planned freeze frames and the
on-screen hook text go into ``joined.mp4`` (the captionless reference QA
measures captions against), and the captions are burned into ``final.mp4``.
"""
import json
import os
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import captions_layout as CL, config, media
from .manifest import schema as S
from .voice import audio as A, synth

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},{text},{text},{edge},&H70000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{ml},{mr},{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
# The hook band: the caption style, larger, anchored top-centre (alignment 8) at the band's MarginV.
HOOK_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{font},{size},{text},{text},{edge},&H70000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},8,{ml},{mr},{mv},1

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


def _caption_text(lines, active):
    """ASS text of a wrapped caption with token ``active`` (index over all tokens) in the highlight colour."""
    out, k = [], 0
    hi, lo = CL.ass_colour(CL.ACTIVE_COLOUR), CL.ass_colour(CL.TEXT_COLOUR)
    for line in lines:
        toks = []
        for tok in line.split():
            t = _ass_text(tok)
            toks.append('{\\1c' + hi + '&}' + t + '{\\1c' + lo + '&}' if k == active else t)
            k += 1
        out.append(' '.join(toks))
    return r'\N'.join(out)


def caption_segments(c):
    """(start_frame, end_frame, active word index) runs covering the caption's display time."""
    words = c.get('words') or [{'start_frame': c['start_frame'], 'end_frame': c['end_frame']}]
    segs = [(max(c['start_frame'], w['start_frame']), min(c['end_frame'], w['end_frame']), i) for i, w in enumerate(words)]
    segs = [s for s in segs if s[1] > s[0]]
    return segs or [(c['start_frame'], c['end_frame'], 0)]


def write_captions(m, prefs, path, width, height, positions=None):
    """One subtitle event per active word, so the spoken word turns yellow (estimated timing when the line
    was not aligned); the caption pops in on its first event and fades out on its last."""
    pr = prefs['production']
    mt = CL.metrics(width, height, pr)
    out = [ASS_HEADER.format(w=width, h=height, font=pr['caption_font'], size=mt['font_size'], outline=mt['outline'],
                             shadow=mt['shadow'], text=CL.ass_colour(CL.TEXT_COLOUR), edge=CL.ass_colour(CL.OUTLINE_COLOUR),
                             ml=mt['left'], mr=width - mt['right'], mv=height - mt['bottom'])]
    layout = []
    top_y = CL.top_anchor(height, pr)
    fps = m['fps']
    for c in m['captions']:
        pos = (positions or {}).get(c['id'], 'bottom')
        lines, _ = CL.wrap(c['text'], width, height, pr)
        mv = 0 if pos == 'bottom' else top_y
        segs = caption_segments(c)
        for j, (a, b, active) in enumerate(segs):
            tags = '' if pos == 'bottom' else r'\an8'
            if j == 0:
                tags += r'\fscx86\fscy86\t(0,90,\fscx100\fscy100)'
            fade_in, fade_out = (40 if j == 0 else 0), (40 if j == len(segs) - 1 else 0)
            if fade_in or fade_out:
                tags += f'\\fad({fade_in},{fade_out})'
            text = ('{' + tags + '}' if tags else '') + _caption_text(lines, active if c.get('words') else -1)
            out.append(f'Dialogue: 0,{_ass_time(a, fps)},{_ass_time(b, fps)},Cap,,0,0,{mv},,{text}\n')
        bx, _ = CL.box(c['text'], width, height, pr, pos)
        layout.append({'id': c['id'], 'text': c['text'], 'lines': lines, 'box': bx, 'start_frame': c['start_frame'],
                       'end_frame': c['end_frame'], 'timing': c['timing'], 'position': pos,
                       'words': c.get('words') or []})
    with open(path, 'w', encoding='utf-8') as f:
        f.write(''.join(out))
    return layout


def hook_overlay(m, prefs, telemetry, width, height, work_dir=None):
    """The on-screen hook band of a manifest: where it goes (captions_layout.hook_band, off the faces the
    telemetry reports during the band), when, and the ASS file that draws it when ``work_dir`` is given.
    None when the plan keeps the hook text off screen or has none."""
    hk = m.get('hook') or {}
    text = ' '.join(str(hk.get('text', '')).split())
    if not hk.get('on_screen') or not text:
        return None
    win = hk.get('overlay') or {'start_frame': 0, 'end_frame': min(m['duration_frames'], hk['end_frame'])}
    frames = range(win['start_frame'], win['end_frame'])
    band = CL.hook_band(text, width, height, prefs['production'], telemetry or {}, frames)
    ov = {'text': text, 'start_frame': win['start_frame'], 'end_frame': win['end_frame'],
          'pop_frames': S.HOOK_POP_FRAMES, 'fade_frames': S.HOOK_FADE_FRAMES, **band}
    if work_dir:
        ov['file'] = write_hook(m, prefs, os.path.join(work_dir, 'hook.ass'), width, height, ov)
    return ov


def write_hook(m, prefs, path, width, height, ov):
    """One ASS event for the hook band: a 4-frame pop-in (scale) and a quick fade-out."""
    pr = prefs['production']
    fps = m['fps']
    pop_ms = int(round(ov['pop_frames'] / fps * 1000))
    fade_ms = int(round(ov['fade_frames'] / fps * 1000))
    head = HOOK_ASS_HEADER.format(w=width, h=height, font=pr['caption_font'], size=ov['font_size'],
                                  outline=ov['outline'], shadow=ov['shadow'], text=CL.ass_colour(CL.TEXT_COLOUR),
                                  edge=CL.ass_colour(CL.OUTLINE_COLOUR), ml=ov['left'], mr=width - ov['right'],
                                  mv=ov['y'])
    tags = (r'\an8' + rf'\fscx55\fscy55\t(0,{pop_ms},\fscx100\fscy100)' + rf'\fad({max(1, pop_ms // 2)},{fade_ms})')
    text = '{' + tags + '}' + r'\N'.join(_ass_text(l) for l in ov['lines'])
    ev = f'Dialogue: 0,{_ass_time(ov["start_frame"], fps)},{_ass_time(ov["end_frame"], fps)},Hook,,0,0,{ov["y"]},,{text}\n'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(head + ev)
    return path


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
        start = x['frame'] / fps
        if x.get('align') == 'peak':
            # Automatic cut whooshes peak on the cut frame instead of starting there.
            start = max(0.0, start - int(np.argmax(np.abs(clip))) / A.SR)
        _place(sfx, clip / (np.max(np.abs(clip)) or 1.0) * 0.6, start, 10 ** (x['gain_db'] / 20))
    if pr['music'] == 'none':
        music = np.zeros(n)
        music_source = 'none'
    elif pr['music'] == 'asset' and music_asset:
        raw = A.decode(music_asset)
        reps = int(np.ceil(n / max(1, len(raw))))
        music = np.tile(raw, reps)[:n]
        music_source = 'owner_asset'
    else:
        music = synth.music_track([(mu['frame'] / fps, mu['cue'], mu.get('section', 0)) for mu in m['music']],
                                  total)[:n]
        if len(music) < n:
            music = np.concatenate([music, np.zeros(n - len(music))])
        music_source = 'generated'
    music = music * 10 ** (pr['music_gain_db'] / 20)
    drops = [(d['start_frame'] / fps, d['end_frame'] / fps) for d in m.get('music_dropouts', [])]
    music = synth.apply_dropouts(music, drops)
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
            'ducking': {'duck_db': pr['duck_db'], 'fraction_ducked': round(speech_frac, 3)},
            'music_dropouts_s': [[round(a, 3), round(b, 3)] for a, b in drops],
            'auto_sfx': sum(1 for x in m['sfx'] if x.get('auto'))}


def concat_shots(m, shot_files, work_dir, hook_ass=None):
    """Join the shots in order (stream copy), then draw the planned freeze frames and the hook band
    (``hook_ass``, from write_hook) into joined.mp4 in one encode when there are any."""
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
    freezes = [e for e in m.get('effects', []) if e.get('type') == 'freeze']
    first = 'joined_cut.mp4' if freezes or hook_ass else 'joined.mp4'
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', 'concat.txt', '-an',
               '-c', 'copy', first], cwd=work_dir)
    if freezes or hook_ass:
        apply_freezes(freezes, work_dir, first, 'joined.mp4', hook_ass=hook_ass)
    return joined


def clean_picture(work_dir, joined):
    """The joined picture before anything was drawn on it (the cover is taken from here)."""
    cut = os.path.join(work_dir, 'joined_cut.mp4')
    return cut if os.path.exists(cut) else joined


def freeze_graph(freezes, src='0:v', ref='1:v'):
    """FFmpeg filter graph that holds frame f for n frames (f+1..f+n-1 repeat f) for every freeze.
    The frame count, and so the sync with the sound, is unchanged. Returns (graph, output label)."""
    k = len(freezes)
    parts = [f'[{ref}]split={k}' + ''.join(f'[r{i}]' for i in range(k))] if k > 1 else []
    cur = f'[{src}]'
    for i, e in enumerate(sorted(freezes, key=lambda e: e['frame'])):
        r = f'[r{i}]' if k > 1 else f'[{ref}]'
        parts.append(f'{cur}{r}freezeframes=first={e["frame"] + 1}:last={e["frame"] + e["frames"] - 1}:'
                     f'replace={e["frame"]}[v{i}]')
        cur = f'[v{i}]'
    return ';'.join(parts), cur


def apply_freezes(freezes, work_dir, src, dst, hook_ass=None):
    """Freeze frames (and the hook band, ``hook_ass``) go into the joined picture, before captions: the
    captionless reference QA compares the final against holds the same frames and the same band, and the
    captions keep running over the held picture. The frame count is unchanged."""
    args = [config.FFMPEG_BIN, '-y', '-v', 'error', '-i', src]
    hook_vf = f'ass={os.path.basename(hook_ass)}:fontsdir={os.path.dirname(CL.font_path())}' if hook_ass else None
    if freezes:
        graph, out = freeze_graph(freezes)
        if hook_vf:
            graph += f';{out}{hook_vf}[vh]'
            out = '[vh]'
        args += ['-i', src, '-filter_complex', graph, '-map', out]
    else:
        args += ['-vf', hook_vf]
    media.run(args + ['-c:v', 'libx264', '-crf', '16', '-pix_fmt', 'yuv420p', '-an', dst], cwd=work_dir)


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


def _expression_at(m, cid, f):
    """Expression the character's track holds at frame f (the last key at or before it)."""
    expr = 'neutral'
    for k in (m.get('tracks', {}).get('characters', {}).get(cid) or {}).get('keys', []):
        if k['frame'] > f:
            break
        expr = (k.get('pose') or {}).get('expression') or expr
    return expr


def cover_windows(m):
    """Frame ranges a cover may come from: the hook and the payoff reaction."""
    D = m['duration_frames']
    return [(0, min(D, m['hook']['end_frame'])), (max(0, m['payoff']['start_frame']), D)]


def choose_cover_frame(m, telemetry):
    """The strongest reaction frame: the largest readable face on screen in the hook or the payoff
    (telemetry face_height_frac), weighted by how big a reaction its expression key is
    (schema.COVER_EXPRESSION_WEIGHT). Frames on a cut are skipped. Earliest wins a tie.
    Returns (frame, evidence); the manifest's own frame when the plan or the owner chose one."""
    src = m.get('cover_source', 'plan')
    if src != 'auto' or not telemetry:
        return m['cover_frame'], {'source': src, 'note': 'cover frame set by the ' + ('plan' if src == 'plan' else src)}
    edges = set()
    for s in m['shots']:
        edges.update(range(s['start_frame'], s['start_frame'] + 2))
        edges.update(range(s['end_frame'] - 2, s['end_frame']))
    best = None
    for f in sorted(telemetry):
        if f in edges or not any(a <= f < b for a, b in cover_windows(m)):
            continue
        for cid, r in telemetry[f]['characters'].items():
            if not r or r.get('face_dot', 0) < 0.35 or r.get('face_height_frac', 0) <= 0:
                continue
            fx, fy = r['face2d'][0], r['face2d'][1]
            if not (0.1 <= fx <= 0.9 and 0.08 <= fy <= 0.8):
                continue
            expr = _expression_at(m, cid, f)
            score = r['face_height_frac'] * (1.0 + S.COVER_EXPRESSION_WEIGHT.get(expr, 0.3))
            if best is None or score > best['score'] + 1e-9:
                best = {'score': round(score, 4), 'frame': f, 'character': cid, 'expression': expr,
                        'face_height_frac': round(r['face_height_frac'], 4)}
    if not best:
        return m['cover_frame'], {'source': 'auto', 'note': 'no readable face in the hook or the payoff; the default frame'}
    return best['frame'], {'source': 'auto', **best}


def thumbnails(m, joined, work_dir, title, prefs=None, telemetry=None):
    """Cover (the chosen frame of the clean picture with the hook text on it) and the thumbnails. Without an
    on-screen hook the thumbnail carries the title instead, as before."""
    fps = m['fps']
    cover = os.path.join(work_dir, 'cover.png')
    frame, evidence = choose_cover_frame(m, telemetry or {})
    media.extract_frame(joined, frame, fps, cover)
    im = Image.open(cover).convert('RGB')
    w, h = im.size
    band = None
    if prefs:
        band = hook_overlay(m, prefs, telemetry or {}, w, h)
        if band:
            band = CL.hook_band(band['text'], w, h, prefs['production'], telemetry or {}, [frame])
            im = CL.draw_hook_text(im, band)
            im.save(cover)
    th = im.copy()
    if not band:
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
    return {'cover': cover, 'thumbnail': vert, 'thumbnail_16x9': landp, 'cover_frame': frame,
            'cover_choice': evidence, 'cover_hook_band': band and {k: band[k] for k in ('lines', 'y', 'box', 'font_size')}}


def assemble(m, shot_files, line_audio, prefs, work_dir, title, music_asset=None, telemetry_paths=None):
    os.makedirs(work_dir, exist_ok=True)
    vs = media.video_stream(media.probe(shot_files[m['shots'][0]['id']]))
    width, height = int(vs['width']), int(vs['height'])
    tele = {}
    if telemetry_paths:
        from .qa.motion import load_telemetry
        tele = load_telemetry([p for p in telemetry_paths if p and os.path.exists(p)])
    hook = hook_overlay(m, prefs, tele, width, height, work_dir)
    joined = concat_shots(m, shot_files, work_dir, hook_ass=hook['file'] if hook else None)
    clean = clean_picture(work_dir, joined)
    mixed = mix(m, line_audio, prefs, work_dir, music_asset)
    cap_path = os.path.join(work_dir, 'captions.ass')
    positions = None
    avoid = [{'start_frame': hook['start_frame'], 'end_frame': hook['end_frame'], 'box': hook['box_norm']}] if hook else []
    if tele or avoid:
        positions = CL.choose_positions(m['captions'], tele, width, height, prefs['production'], avoid=avoid)
    layout = write_captions(m, prefs, cap_path, width, height, positions) if prefs['production']['captions'] else []
    final = final_encode(m, joined, mixed['paths']['master'], cap_path if layout else None, work_dir)
    thumbs = thumbnails(m, clean, work_dir, title, prefs, tele)
    report = {'final': final, 'joined': joined, 'joined_clean': clean, 'width': width, 'height': height, 'mix': mixed,
              'captions': layout, 'captions_file': cap_path if layout else None, **thumbs,
              'hook_overlay': hook, 'native_render_size': [width, height]}
    with open(os.path.join(work_dir, 'assembly.json'), 'w') as f:
        json.dump(report, f, default=str, indent=1)
    return report
