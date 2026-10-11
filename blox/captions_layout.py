"""Caption layout: one place that decides font, size, wrapping and position.

The validator uses ``fits`` before rendering; the assembler writes the same
layout into an ASS subtitle file (pixel coordinates, PlayRes = frame size);
QA then measures where caption pixels actually landed in the rendered video.

Style: bold white sans in 1-3 word chunks with a heavy black outline and a
soft drop shadow, low in the frame above the Shorts UI; the word being spoken
turns yellow. The outline and the shadow are part of every box and fit test.

The on-screen hook text uses the same style, larger, in a band just under the
top of the safe area; ``hook_band`` keeps it off the faces the render telemetry
reports, and ``choose_positions`` keeps the captions out of the band.
"""
import functools
import os

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
]
MAX_LINES = 2
HOOK_SIZE_RATIO = 1.4       # largest hook font size / caption font size
HOOK_MIN_SIZE_RATIO = 1.0   # smallest hook size (the caption size) before the text is reported as not fitting
HOOK_MAX_LINES = 3
HOOK_TWO_LINE_RATIO = 1.2   # two lines win over three when they are still this much larger than the captions
HOOK_TEXT_WORDS_LIMIT = 12  # longer texts are not laid out (the validator already rejects more than six words)
HOOK_BAND_DROPS = (0.0, 0.06, 0.12, 0.18)  # candidate band tops below the safe-area top (fraction of height)
HOOK_SHADOW_ALPHA = 0x90    # the cover's drop shadow (the ASS BackColour alpha is similar)
OUTLINE_RATIO = 0.17   # outline thickness / font size: a heavy, sticker-like black edge
SHADOW_RATIO = 0.06    # drop shadow offset / font size
TEXT_COLOUR = '#FFFFFF'
ACTIVE_COLOUR = '#FFD60A'  # the word being spoken
OUTLINE_COLOUR = '#000000'


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
    outline = max(3, int(round(size * OUTLINE_RATIO)))
    shadow = max(1, int(round(size * SHADOW_RATIO)))
    left, right = int(sa['left'] * width), int(sa['right'] * width)
    bottom = int((sa['bottom'] - 0.015) * height) - shadow
    return {'font_size': size, 'outline': outline, 'shadow': shadow, 'left': left, 'right': right, 'bottom': bottom,
            'max_width': right - left - 2 * outline - shadow, 'line_height': int(size * 1.18),
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
    if len(lines) == 2:
        # Balance a two-line caption (no single word stranded on the second line) when both halves fit.
        best = None
        for k in range(1, len(words)):
            a, b = ' '.join(words[:k]), ' '.join(words[k:])
            wa, wb = text_width(a, mt['font_size']), text_width(b, mt['font_size'])
            if max(wa, wb) <= mt['max_width'] and (best is None or max(wa, wb) < best[0]):
                best = (max(wa, wb), [a, b])
        if best:
            lines = best[1]
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
    sh = mt.get('shadow', 0)
    x0 = cx - widest / 2 - mt['outline']
    x1 = cx + widest / 2 + mt['outline'] + sh
    if position == 'top':
        y0 = top_anchor(height, pr) - mt['outline']
        y1 = top_anchor(height, pr) + len(lines) * mt['line_height'] + mt['outline'] + sh
    else:
        y1 = mt['bottom'] + mt['outline'] + sh
        y0 = mt['bottom'] - len(lines) * mt['line_height'] - mt['outline']
    return [int(x0), int(y0), int(x1), int(y1)], lines


def ass_colour(hex_rgb, alpha=0):
    """'#RRGGBB' -> ASS '&HAABBGGRR' (alpha 0 = opaque)."""
    h = hex_rgb.lstrip('#')
    return f'&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}'.upper()


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


def _blocked(nb, c, avoid):
    """True when the normalised box ``nb`` of caption ``c`` meets a planned overlay (the hook band) that is on
    screen at any time the caption is."""
    for a in avoid or []:
        if a['start_frame'] < c['end_frame'] and a['end_frame'] > c['start_frame'] and _overlap(nb, a['box']) > 1e-6:
            return True
    return False


def choose_positions(captions, telemetry, width, height, pr, avoid=None):
    """Pick bottom (default) or top for each caption so it does not cover a face. ``avoid`` lists planned
    overlays ({start_frame, end_frame, box} in normalised coordinates, e.g. the hook band): a caption never
    takes a position that meets one while it is on screen, so the bottom wins during the hook band."""
    out = {}
    for c in captions:
        cand = {}
        for pos in ('bottom', 'top'):
            bx, _ = box(c['text'], width, height, pr, pos)
            nb = [bx[0] / width, bx[1] / height, bx[2] / width, bx[3] / height]
            if _blocked(nb, c, avoid):
                continue
            cover = 0.0
            for f in range(c['start_frame'], c['end_frame']):
                rec = telemetry.get(f)
                if not rec:
                    continue
                for fb in face_boxes(rec['characters'], width, height):
                    cover = max(cover, _overlap(nb, fb))
            cand[pos] = cover
        if 'top' not in cand or 'bottom' not in cand:
            out[c['id']] = 'top' if 'top' in cand and 'bottom' not in cand else 'bottom'
            continue
        out[c['id']] = 'bottom' if cand['bottom'] <= 1e-4 or cand['bottom'] <= cand['top'] else 'top'
    return out


# ---------------------------------------------------------------- on-screen hook text

def hook_metrics(width, height, pr, size):
    """Font metrics and the band's horizontal room. The top of the frame has no Shorts side column (that sits
    lower, beside the captions), so the band is centred between the safe-area left margin and its mirror."""
    sa = pr['safe_area']
    outline = max(3, int(round(size * OUTLINE_RATIO)))
    shadow = max(1, int(round(size * SHADOW_RATIO)))
    left = int(sa['left'] * width)
    right = width - left
    return {'font_size': size, 'outline': outline, 'shadow': shadow, 'left': left, 'right': right,
            'max_width': right - left - 2 * outline - shadow, 'line_height': int(size * 1.12)}


def _splits(words, k):
    """Every way to break ``words`` into k non-empty lines, in order."""
    if k == 1:
        yield [' '.join(words)]
        return
    for i in range(1, len(words) - k + 2):
        for rest in _splits(words[i:], k - 1):
            yield [' '.join(words[:i])] + rest


def _balanced_lines(words, size, max_width, k):
    """The k-line break of ``words`` with the narrowest widest line, or None when no break fits."""
    best = None
    for lines in _splits(words, k):
        widest = max(text_width(l, size) for l in lines)
        if widest <= max_width and (best is None or widest < best[0]):
            best = (widest, lines)
    return best[1] if best else None


def hook_wrap(text, width, height, pr):
    """How the hook text wraps. Sizes run from HOOK_SIZE_RATIO of the caption size down to HOOK_MIN_SIZE_RATIO:
    the largest at which the text fits the band's width in one or two balanced lines wins when it is still
    clearly larger than the captions (HOOK_TWO_LINE_RATIO), else the largest at which it fits in
    HOOK_MAX_LINES lines. Returns (lines, metrics, fits)."""
    base = metrics(width, height, pr)['font_size']
    hi, lo = int(round(base * HOOK_SIZE_RATIO)), max(12, int(round(base * HOOK_MIN_SIZE_RATIO)))
    words = text.split()
    sizes = list(range(hi, lo, -2)) + [lo]
    if not words or len(words) > HOOK_TEXT_WORDS_LIMIT:
        return [' '.join(words)], hook_metrics(width, height, pr, lo), False
    found = {}
    for max_lines in (2, HOOK_MAX_LINES):
        for size in sizes:
            mt = hook_metrics(width, height, pr, size)
            for k in range(1, min(max_lines, len(words)) + 1):
                lines = _balanced_lines(words, size, mt['max_width'], k)
                if lines:
                    found[max_lines] = (lines, mt)
                    break
            if max_lines in found:
                break
    two, three = found.get(2), found.get(HOOK_MAX_LINES)
    if two and (two[1]['font_size'] >= base * HOOK_TWO_LINE_RATIO or not three
                or three[1]['font_size'] <= two[1]['font_size']):
        return two[0], two[1], True
    if three:
        return three[0], three[1], True
    mt = hook_metrics(width, height, pr, lo)
    lines = (_balanced_lines(words, lo, mt['max_width'] * 4, min(HOOK_MAX_LINES, len(words)))
             or [' '.join(words)])
    return lines, mt, False


def hook_fits(text, width, height, pr):
    lines, mt, ok = hook_wrap(text, width, height, pr)
    widest = max((text_width(l, mt['font_size']) for l in lines), default=0)
    if not ok:
        return False, (f'{len(lines)} lines at the smallest size {mt["font_size"]}px' if len(lines) > HOOK_MAX_LINES
                       else f'word wider than the safe area ({int(widest)}px > {mt["max_width"]}px)')
    return True, f'{len(lines)} line(s) at {mt["font_size"]}px, widest {int(widest)}px of {mt["max_width"]}px'


def hook_box(lines, mt, width, y_top):
    """Pixel box (x0, y0, x1, y1) of the hook band whose first line starts at ``y_top``."""
    widest = max((text_width(l, mt['font_size']) for l in lines), default=0)
    cx = (mt['left'] + mt['right']) / 2
    x0 = cx - widest / 2 - mt['outline']
    x1 = cx + widest / 2 + mt['outline'] + mt['shadow']
    y0 = y_top - mt['outline']
    y1 = y_top + len(lines) * mt['line_height'] + mt['outline'] + mt['shadow']
    return [int(x0), int(y0), int(x1), int(y1)]


def hook_band(text, width, height, pr, telemetry=None, frames=()):
    """Where the on-screen hook text goes: the highest candidate band under the top of the safe area whose box
    clears every face the telemetry reports during ``frames``, else the candidate covering the least face.
    The result carries the wrapped lines, the font metrics, the pixel box and the normalised box."""
    lines, mt, ok = hook_wrap(text, width, height, pr)
    base_y = top_anchor(height, pr)
    best = None
    for drop in HOOK_BAND_DROPS:
        y = base_y + int(drop * height)
        bx = hook_box(lines, mt, width, y)
        nb = [bx[0] / width, bx[1] / height, bx[2] / width, bx[3] / height]
        cover = 0.0
        for f in frames:
            rec = (telemetry or {}).get(f)
            if not rec:
                continue
            for fb in face_boxes(rec['characters'], width, height):
                cover = max(cover, _overlap(nb, fb))
        if best is None or cover < best['face_cover'] - 1e-6:
            best = {'lines': lines, 'y': y, 'drop': drop, 'box': bx, 'box_norm': [round(v, 4) for v in nb],
                    'face_cover': round(cover, 5), 'fits': ok, **mt}
        if cover <= 1e-4:
            break
    return best


def draw_hook_text(im, band):
    """Composite the hook band on a PIL image in the caption style: soft drop shadow, heavy black outline,
    white bold text, centred in the safe width with the first line at band['y']."""
    f = _font(band['font_size'])
    out = im.convert('RGBA')
    layer = Image.new('RGBA', out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx = (band['left'] + band['right']) / 2
    sh, ol = band['shadow'], band['outline']
    for i, line in enumerate(band['lines']):
        y = band['y'] + i * band['line_height']
        d.text((cx + sh, y + sh), line, font=f, fill=(0, 0, 0, HOOK_SHADOW_ALPHA), anchor='ma', stroke_width=ol,
               stroke_fill=(0, 0, 0, HOOK_SHADOW_ALPHA))
    out.alpha_composite(layer)
    d = ImageDraw.Draw(out)
    for i, line in enumerate(band['lines']):
        y = band['y'] + i * band['line_height']
        d.text((cx, y), line, font=f, fill=(255, 255, 255, 255), anchor='ma', stroke_width=ol,
               stroke_fill=(0, 0, 0, 255))
    return out.convert('RGB')
