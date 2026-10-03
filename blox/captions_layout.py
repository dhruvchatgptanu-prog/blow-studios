"""Caption layout: one place that decides font, size, wrapping and position.

The validator uses ``fits`` before rendering; the assembler writes the same
layout into an ASS subtitle file (pixel coordinates, PlayRes = frame size);
QA then measures where caption pixels actually landed in the rendered video.
"""
import functools
import os

from PIL import ImageFont

FONT_CANDIDATES = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
]
MAX_LINES = 2


def font_path():
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise RuntimeError('Caption font not found. Install fonts-dejavu-core.')


@functools.lru_cache(maxsize=16)
def _font(size):
    return ImageFont.truetype(font_path(), size)


def metrics(width, height, pr):
    sa = pr['safe_area']
    size = max(12, int(round(pr['caption_font_size_ratio'] * height)))
    outline = max(2, size // 9)
    left, right = int(sa['left'] * width), int(sa['right'] * width)
    bottom = int((sa['bottom'] - 0.015) * height)
    return {'font_size': size, 'outline': outline, 'left': left, 'right': right, 'bottom': bottom,
            'max_width': right - left - 2 * outline, 'line_height': int(size * 1.18),
            'top_limit': int(sa['top'] * height)}


def text_width(text, size):
    # ASS font size is close to the font's line height; measuring with the
    # same pixel size in PIL is slightly conservative (wider), which is safe.
    return _font(size).getlength(text)


def wrap(text, width, height, pr):
    mt = metrics(width, height, pr)
    words = text.split()
    lines, cur = [], ''
    for w in words:
        trial = (cur + ' ' + w).strip()
        if cur and text_width(trial, mt['font_size']) > mt['max_width']:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines, mt


def fits(text, width, height, pr):
    lines, mt = wrap(text, width, height, pr)
    widest = max((text_width(l, mt['font_size']) for l in lines), default=0)
    block_top = mt['bottom'] - len(lines) * mt['line_height']
    if len(lines) > MAX_LINES:
        return False, f'{len(lines)} lines at {mt["font_size"]}px'
    if widest > mt['max_width']:
        return False, f'word wider than safe area ({int(widest)}px > {mt["max_width"]}px)'
    if block_top < mt['top_limit']:
        return False, 'caption block extends above the safe area'
    return True, f'{len(lines)} line(s), widest {int(widest)}px of {mt["max_width"]}px'


def top_anchor(height, pr):
    return int((pr['safe_area']['top'] + 0.015) * height)


def box(text, width, height, pr, position='bottom'):
    """Expected caption bounding box in pixels (x0, y0, x1, y1)."""
    lines, mt = wrap(text, width, height, pr)
    widest = max((text_width(l, mt['font_size']) for l in lines), default=0)
    cx = (mt['left'] + mt['right']) / 2
    x0 = cx - widest / 2 - mt['outline']
    x1 = cx + widest / 2 + mt['outline']
    if position == 'top':
        y0 = top_anchor(height, pr) - mt['outline']
        y1 = top_anchor(height, pr) + len(lines) * mt['line_height'] + mt['outline']
    else:
        y1 = mt['bottom'] + mt['outline']
        y0 = mt['bottom'] - len(lines) * mt['line_height'] - mt['outline']
    return [int(x0), int(y0), int(x1), int(y1)], lines


def face_boxes(rec_chars, width, height):
    """Normalised boxes around on-screen faces from render telemetry."""
    out = []
    for r in rec_chars.values():
        if not r or r.get('face_dot', 0) < 0.1:
            continue
        fx, fy = r['face2d'][0], r['face2d'][1]
        hh = r['face_height_frac'] * 0.6
        hw = hh * height / width
        if -0.2 < fx < 1.2 and -0.2 < fy < 1.2:
            out.append([fx - hw, fy - hh, fx + hw, fy + hh])
    return out


def _overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def choose_positions(captions, telemetry, width, height, pr):
    """Pick bottom (default) or top for each caption so it does not cover a face."""
    out = {}
    for c in captions:
        cand = {}
        for pos in ('bottom', 'top'):
            bx, _ = box(c['text'], width, height, pr, pos)
            nb = [bx[0] / width, bx[1] / height, bx[2] / width, bx[3] / height]
            cover = 0.0
            for f in range(c['start_frame'], c['end_frame']):
                rec = telemetry.get(f)
                if not rec:
                    continue
                for fb in face_boxes(rec['characters'], width, height):
                    cover = max(cover, _overlap(nb, fb))
            cand[pos] = cover
        out[c['id']] = 'bottom' if cand['bottom'] <= 1e-4 or cand['bottom'] <= cand['top'] else 'top'
    return out
