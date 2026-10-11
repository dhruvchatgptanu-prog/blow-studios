"""Channel kit: a ready-to-use branding pack the owner uses to create the YouTube channel by hand.

Generated from the cast and the owner's preferences, with no network and no paid calls:

* channel name and handle suggestions (original, brand-free; never 'roblox' in a name);
* profile picture: Bloxy's face close-up rendered by the real Blender renderer on a plain studio set with a
  transparent background, composited on a bold flat colour (800x800, face inside the circular crop);
* banner: the cast line-up rendered at preview quality plus the channel name in the caption font (2560x1440,
  everything that matters inside the 1546x423 safe area);
* channel description (original characters, hand-made animation, AI-assist disclosure, upload cadence);
* default upload settings summary and a short step list for creating and connecting the channel.

Files are written under data/channel_kit/ and served through the media route (see ``registered_files``).
Fonts: only the DejaVu Sans Bold the captions use. No logos, trademarks or brand marks anywhere in the pack.
"""
import json
import os
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import captions_layout as CL, config, prefs as prefsmod, repo, store
from .animation import blender as BL
from .util import now

REL_DIR = 'channel_kit'
PROFILE_SIZE = 800
BANNER_SIZE = (2560, 1440)
SAFE_AREA = (1546, 423)
FILES = {'profile': 'profile.png', 'profile_round_preview': 'profile_round_preview.png', 'banner': 'banner.png',
         'banner_mobile_preview': 'banner_mobile_preview.png', 'lineup': 'lineup.png', 'kit_json': 'kit.json',
         'kit_md': 'kit.md'}
# Bold flat backgrounds: a deep blue that contrasts with every cast palette (warm skins, teal, gold, pink).
PROFILE_BG = '#2F55E8'
BANNER_BG = '#2A4BD8'
BANNER_BG_DARK = '#213DB4'
BANNER_BG_LIGHT = '#3B63F2'
FORBIDDEN_NAME_WORDS = ('roblox', 'youtube', 'lego', 'minecraft', 'official')
CATEGORY_NAMES = {'1': 'Film & Animation', '10': 'Music', '15': 'Pets & Animals', '17': 'Sports', '20': 'Gaming',
                  '22': 'People & Blogs', '23': 'Comedy', '24': 'Entertainment', '27': 'Education',
                  '28': 'Science & Technology'}
LANGUAGES = {'en': 'English', 'en-US': 'English (United States)', 'en-GB': 'English (United Kingdom)',
             'en-AU': 'English (Australia)'}
HANDLE_RX = re.compile(r'^[A-Za-z0-9._-]{3,30}$')


def kit_dir():
    return config.DATA_DIR / REL_DIR


def registered_files():
    """Relative paths under the data directory that the media route may serve for the kit."""
    return {f'{REL_DIR}/{name}' for name in FILES.values()}


# ---------------------------------------------------------------- text pack
def _cast(chars):
    """[(cast_id, character_id, name, bible)] for active characters in creation order."""
    out = []
    for cid, c in chars.items():
        slug = re.sub(r'[^a-z0-9]', '', c['name'].lower()) or 'char'
        out.append((slug, cid, c['name'], c.get('bible') or {}))
    return out


def _forbidden(name, p, chars):
    low = name.lower()
    if any(w in low for w in FORBIDDEN_NAME_WORDS):
        return 'contains a brand or reserved word'
    for g in p['channel'].get('game_names') or []:
        if g and g.lower() in low:
            return 'names another game'
    if low in {c['name'].lower() for c in chars.values()}:
        return 'is just a character name'
    if not 3 <= len(name) <= 40:
        return 'length'
    return None


def handle_for(name):
    base = re.sub(r'[^a-z0-9]', '', name.lower().replace('&', ' and '))
    return base[:30] if len(base) >= 3 else ''


def name_suggestions(p, chars):
    cast = _cast(chars)
    hero = cast[0][2] if cast else 'Bloxy'
    world = (p['channel']['identity'].get('recurring_world') or '').split(':')[0].strip()
    raw = [(f'{hero} & Friends', 'Hero-led: the recurring cast is the brand'),
           (f"{hero}'s Obby Tales", 'Hero plus the genre word'),
           (f'{world} Stories' if world else 'Skyline Stories', 'Named after the recurring world'),
           ('Block Tales', 'Short, generic, easy to say'),
           (f'The {hero} Show', 'Series feel; works for skits and storytime'),
           ('Studded Sky Stories', 'Describes the look without naming any game'),
           ('Obby Odd Squad', 'Ensemble name for the five-character cast')]
    out, seen = [], set()
    for name, why in raw:
        if name in seen or _forbidden(name, p, chars):
            continue
        seen.add(name)
        handle = handle_for(name)
        out.append({'name': name, 'handle': handle, 'why': why,
                    'handle_alternatives': [h for h in (handle[:30], re.sub(r'[^a-z0-9_]', '', name.lower().replace(
                        '&', 'and').replace(' ', '_'))[:30], (handle + 'shorts')[:30]) if HANDLE_RX.match(h)
                        and h != handle][:2]})
    return out


def _cadence(p):
    s = p['schedule']
    mins = int(s['interval_minutes'])
    cap = int(s['max_per_rolling_24h'])
    if mins >= 1440:
        return 'every day' if mins == 1440 else f'every {mins // 1440} days'
    per_day = min(cap, 1440 // max(1, mins))
    hours = mins / 60
    return f'every {hours:g} hour{"s" if hours != 1 else ""} (up to {per_day} a day)'


def description(p, chars, name=None):
    ident = p['channel']['identity']
    name = name or ident.get('name') or 'This channel'
    cast = _cast(chars)
    names = [c[2] for c in cast]
    who = (', '.join(names[:-1]) + ' and ' + names[-1]) if len(names) > 1 else (names[0] if names else 'our cast')
    tone = (ident.get('tone') or 'funny, kind-hearted stories with a twist').strip().rstrip('.')
    tone = tone[0].lower() + tone[1:] if tone else tone
    lines = [
        f'{name}: original animated stories about {who}. Expect {tone}.',
        'Every Short is our own 3D block-character animation, made with original characters, sets and rig '
        'built by hand. Nothing here is captured gameplay.',
        'AI assistance: scripts are drafted with AI help and the voices are synthetic (text to speech). A person '
        'checks every video before it goes up, and each upload is marked as altered or synthetic content.',
        f'New Short {_cadence(p)}.',
        'Not affiliated with or endorsed by any game company.',
    ]
    text = '\n\n'.join(lines)
    return text[:1000]


def upload_defaults(p):
    pub = p['publishing']
    mfk = pub.get('made_for_kids')
    syn = pub.get('synthetic_disclosure')
    cat = str(pub.get('category_id') or '')
    lang = str(pub.get('default_language') or 'en')
    return {
        'audience': ('Not made for kids' if mfk is False else 'Made for kids' if mfk is True else
                     'Undecided: choose in Settings → Publishing before enabling autopilot'),
        'altered_or_synthetic_content': ('Yes: disclose altered or synthetic content on every upload' if syn
                                         else 'No disclosure selected: review Settings → Publishing'),
        'category': CATEGORY_NAMES.get(cat, f'Category {cat}'), 'category_id': cat,
        'language': LANGUAGES.get(lang, lang), 'language_code': lang,
        'tags': list(pub.get('tags') or []),
        'visibility': 'Private on upload; Blox makes each video public at its scheduled slot',
        'comments': 'On (general audience, moderated by you)',
        'notify_subscribers': bool(pub.get('notify_subscribers')),
        'thumbnail': ('Blox uploads a custom thumbnail' if pub.get('upload_thumbnail') else
                      'YouTube auto-thumbnail (custom thumbnails need a verified channel)'),
        'format': 'Shorts: vertical 9:16, under 60 s',
    }


def steps(p, chars, name=None):
    from .youtube import oauth
    name = name or p['channel']['identity'].get('name') or 'the channel'
    return [
        f'Sign in to a Google account you own and create a new channel (a brand channel keeps it separate from your '
        f'personal name). Enter the channel name, for example "{name}", and claim a handle from the suggestions.',
        f'Customisation → Branding: upload profile.png ({PROFILE_SIZE}x{PROFILE_SIZE}) as the picture and banner.png '
        f'({BANNER_SIZE[0]}x{BANNER_SIZE[1]}) as the banner image. Check the banner preview on TV, desktop and '
        f'mobile: everything important sits in the {SAFE_AREA[0]}x{SAFE_AREA[1]} safe area.',
        'Customisation → Basic info: paste the description from this kit, add your contact email if you want one, '
        'and set the channel country.',
        'Settings → Channel → Advanced settings: audience "No, set this channel as not made for kids" (Blox sends '
        'the same flag per video). Settings → Upload defaults: category, language, tags and the altered or '
        'synthetic content disclosure as listed in this kit.',
        'Google Cloud: enable the YouTube Data API, create an OAuth web client and add this exact redirect URI: '
        + oauth.redirect_uri() + '. Save the client ID and secret on the Connections page.',
        'Connections → "Connect YouTube": approve the upload and read-only scopes, then confirm the channel that '
        'Blox shows so uploads can only ever go to it.',
        'Start in review mode: the first uploads stay private until you approve them in Quality control; switch to '
        'autopilot only after a few videos have gone out the way you want.',
    ]


def text_pack(p=None, chars=None, name=None):
    p = p or prefsmod.get()
    chars = chars if chars is not None else repo.characters()
    names = name_suggestions(p, chars)
    chosen = name or p['channel']['identity'].get('name') or (names[0]['name'] if names else 'Block Tales')
    if _forbidden(chosen, p, chars) and names:
        chosen = names[0]['name']
    return {'channel_name': chosen, 'names': names, 'handles': [n['handle'] for n in names if n['handle']],
            'description': description(p, chars, chosen), 'upload_defaults': upload_defaults(p),
            'steps': steps(p, chars, chosen),
            'brand_rules': ['No logos, trademarks or other channels\' artwork anywhere in the pack.',
                            'Names never contain another company\'s or game\'s name; check handle availability when '
                            'creating the channel.',
                            'Only the repo\'s DejaVu Sans Bold is used for text.',
                            'Images are rendered from our own rig and characters; nothing is downloaded.'],
            'fonts': 'DejaVu Sans Bold (fonts-dejavu-core)'}


# ---------------------------------------------------------------- Blender stills
def _still_plan(cast, subject, framing, width, height, poses):
    """An authoring plan for one held pose on a plain studio set (no props, no lines, no actions)."""
    from .demo import plan as demo_plan
    pl = demo_plan()
    pl['title'] = 'Channel kit still'
    pl['cast'] = [{'id': slug, 'character_id': cid} for slug, cid, _n, _b in cast]
    pl['performance'] = {slug: [dict({'t': 0, 'facing': 0, 'eye_target': {'kind': 'camera'}}, **poses[slug])]
                         for slug, _c, _n, _b in cast}
    pl['actions'], pl['lines'] = [], []
    pl['setting'] = {'preset': 'studio', 'time_of_day': 'noon', 'lighting': 'natural',
                     'description': 'Plain studio backdrop for the channel kit', 'props': []}
    pl['shots'] = [{'id': 's1', 'start_s': 0, 'end_s': 30,
                    'camera': {'subject': subject, 'framing_start': framing, 'framing_end': framing, 'move': 'static',
                               'side': 'front', 'angle': 'eye'}}]
    return pl


def render_still(cast, subject, framing, width, height, poses, work_dir, p, quality, aim_z=None):
    """Render one frame of a held pose with the real renderer; returns the RGBA still path.

    aim_z: level the camera at this height (metres). The solver frames portrait video; for a landscape still the
    framing's visible height becomes the visible width (the sensor fits horizontally), so the aim is set here.
    """
    from .animation import sets as SETS, solver as SV
    from .manifest import compile as C
    pl = _still_plan(cast, subject, framing, width, height, poses)
    m = C.compile_plan(pl, fps=30, width=width, height=height)
    bibles = {slug: b for slug, _c, _n, b in cast}
    solved = SV.solve(m, bibles)
    lay = SETS.layout(m, solved.get('scales'))
    # Keep the studio sky and lights, drop the floor and cyclorama, and ask for an empty background alpha.
    solved['set'] = dict(lay, pieces=[], templates={}, transparent=True)
    if aim_z is not None:
        for cam in solved['camera']:
            if cam:
                cam['location'][2] = aim_z
                cam['look_at'][2] = aim_z
    res = BL.render_range(m, bibles, solved, 10, 11, str(work_dir), p, quality=quality)
    return res['preview']


def _font(size):
    return ImageFont.truetype(CL.font_path(), size)


def _rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _alpha_bbox(im):
    a = im.split()[-1].point(lambda v: 255 if v > 24 else 0)
    return a.getbbox()


def compose_profile(render_png, out_png, bg=PROFILE_BG, round_preview=None):
    """800x800: the transparent face render over a flat colour; the face must sit inside the inscribed circle."""
    im = Image.open(render_png).convert('RGBA')
    size = PROFILE_SIZE
    if im.size != (size, size):
        im = im.resize((size, size), Image.LANCZOS)
    canvas = Image.new('RGBA', (size, size), _rgb(bg) + (255,))
    canvas.alpha_composite(im)
    canvas.convert('RGB').save(out_png, 'PNG', optimize=True)
    if round_preview:
        mask = Image.new('L', (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        prev = Image.new('RGB', (size, size), (245, 245, 247))
        prev.paste(canvas.convert('RGB'), (0, 0), mask)
        prev.save(round_preview, 'PNG', optimize=True)
    bbox = _alpha_bbox(im)
    return {'size': [size, size], 'subject_bbox': list(bbox) if bbox else None, 'background': bg}


def _fit_text(draw, text, max_w, max_h, start=132, min_size=48):
    """Largest DejaVu size at which the name fits in at most two lines within max_w x max_h."""
    size = start
    while size >= min_size:
        f = _font(size)
        words = text.split()
        lines, cur = [], ''
        for w in words:
            trial = (cur + ' ' + w).strip()
            if cur and f.getlength(trial) > max_w:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        if cur:
            lines.append(cur)
        line_h = int(size * 1.15)
        if len(lines) <= 2 and all(f.getlength(ln) <= max_w for ln in lines) and line_h * len(lines) <= max_h:
            return f, lines, line_h
        size -= 6
    f = _font(min_size)
    return f, [text[:24]], int(min_size * 1.15)


def _outlined(draw, xy, text, font, fill=(255, 255, 255), size=100):
    sw = max(3, size // 14)
    sh = max(2, size // 18)
    draw.text((xy[0] + sh, xy[1] + sh), text, font=font, fill=(10, 16, 48), stroke_width=sw, stroke_fill=(10, 16, 48))
    draw.text(xy, text, font=font, fill=fill, stroke_width=sw, stroke_fill=(16, 22, 60))


def compose_banner(lineup_png, name, tagline, out_png, mobile_preview=None):
    """2560x1440 banner: flat two-tone background, cast line-up and the name, all inside the 1546x423 safe area."""
    W, H = BANNER_SIZE
    sw, sh = SAFE_AREA
    sx, sy = (W - sw) // 2, (H - sh) // 2
    canvas = Image.new('RGB', (W, H), _rgb(BANNER_BG))
    d = ImageDraw.Draw(canvas)
    # Depth outside the safe area (seen on TV/desktop): a lighter sky band above, a darker ground band below,
    # and two big soft discs. Plain shapes only; no marks or logos.
    d.rectangle((0, 0, W, sy - 60), fill=_rgb(BANNER_BG_LIGHT))
    d.rectangle((0, sy + sh + 40, W, H), fill=_rgb(BANNER_BG_DARK))
    discs = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(discs)
    dd.ellipse((-260, sy - 420, 620, sy + 460), fill=_rgb(BANNER_BG_LIGHT) + (110,))
    dd.ellipse((W - 700, sy + sh - 520, W + 320, sy + sh + 500), fill=_rgb(BANNER_BG_DARK) + (120,))
    canvas = Image.alpha_composite(canvas.convert('RGBA'), discs)
    # Cast line-up: auto-crop the transparent render, fit it to the right part of the safe area.
    lu = Image.open(lineup_png).convert('RGBA')
    bbox = _alpha_bbox(lu)
    if bbox:
        lu = lu.crop(bbox)
    max_h = int(sh * 0.92)
    max_w = int(sw * 0.6)
    scale = min(max_h / lu.height, max_w / lu.width)
    lu = lu.resize((max(1, int(lu.width * scale)), max(1, int(lu.height * scale))), Image.LANCZOS)
    lx = sx + sw - lu.width - 16
    ly = sy + sh - lu.height - 14
    # A soft contact shadow under the line-up so the characters sit on the ground band.
    shadow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((lx + 10, ly + lu.height - 26, lx + lu.width - 10, ly + lu.height + 22),
                                   fill=(8, 14, 40, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    canvas = Image.alpha_composite(canvas, shadow)
    canvas.alpha_composite(lu, (lx, ly))
    # Name + tagline in the left part of the safe area, caption font with a dark edge.
    d = ImageDraw.Draw(canvas)
    text_w = lx - sx - 36
    f, lines, line_h = _fit_text(d, name, text_w, int(sh * 0.6))
    tag_f, tag_lines, tag_h = _fit_text(d, tagline, text_w, 96, start=38, min_size=26)
    block_h = line_h * len(lines) + 14 + tag_h * len(tag_lines)
    y = sy + (sh - block_h) // 2
    for ln in lines:
        _outlined(d, (sx + 6, y), ln, f, size=f.size)
        y += line_h
    y += 14
    for ln in tag_lines:
        _outlined(d, (sx + 10, y), ln, tag_f, fill=(255, 214, 10), size=tag_f.size)
        y += tag_h
    out = canvas.convert('RGB')
    out.save(out_png, 'PNG', optimize=True)
    if mobile_preview:
        out.crop((sx, sy, sx + sw, sy + sh)).save(mobile_preview, 'PNG', optimize=True)
    return {'size': [W, H], 'safe_area': [sw, sh], 'safe_origin': [sx, sy], 'lineup_box': [lx, ly, lu.width, lu.height],
            'name_lines': lines, 'name_font_px': f.size, 'tagline_lines': tag_lines}


LINEUP_POSES = [
    {'expression': 'happy', 'arms': {'left': 'rest', 'right': 'wave'}},
    {'expression': 'smug', 'arms': {'left': 'cross', 'right': 'cross'}},
    {'expression': 'proud', 'arms': {'left': 'hips', 'right': 'hips'}},
    {'expression': 'excited', 'arms': {'left': 'raise', 'right': 'raise'}},
    {'expression': 'curious', 'arms': {'left': 'rest', 'right': 'chin'}},
]


def render_images(p, chars, name, out_dir, work_root=None):
    """Both kit images with the real renderer. Returns the image report (paths relative to the data dir)."""
    cast = _cast(chars)
    if not cast:
        raise ValueError('No active characters; add the cast first')
    work_root = work_root or (config.WORK_DIR / REL_DIR)
    hero = cast[0]
    # Profile: the hero's face, straight to camera, happy.
    poses = {hero[0]: {'position': [0.0, 0.0], 'expression': 'happy'}}
    still = render_still([hero], hero[0], 'medium_close', PROFILE_SIZE, PROFILE_SIZE, poses, work_root / 'profile',
                         p, 'final')
    prof = compose_profile(still, out_dir / FILES['profile'], round_preview=out_dir / FILES['profile_round_preview'])
    # Banner line-up: up to five characters in a row, hero in the middle, the others spread around it in cast
    # order (second character to the hero's left, third to the right, and so on), each with its own pose.
    seq = cast[:5]
    spread = [seq[0]]
    left = True
    for c in seq[1:]:
        if left:
            spread.insert(0, c)
        else:
            spread.append(c)
        left = not left
    spacing = 1.25
    x0 = -spacing * (len(spread) - 1) / 2
    poses = {}
    for i, c in enumerate(spread):
        k = cast.index(c) % len(LINEUP_POSES)
        poses[c[0]] = dict(LINEUP_POSES[k], position=[round(x0 + i * spacing, 3), 0.0])
    # 'wide' puts the camera where 6.7 m fit across the frame (see render_still); 4400x2000 at preview quality
    # renders 2200x1000 pixels, downscaled into the banner for crisp edges.
    still = render_still(spread, hero[0], 'wide', 4400, 2000, poses, work_root / 'lineup', p, 'preview',
                         aim_z=1.15)
    lu = Image.open(still).convert('RGBA')
    bbox = _alpha_bbox(lu)
    (lu.crop(bbox) if bbox else lu).save(out_dir / FILES['lineup'], 'PNG', optimize=True)
    ban = compose_banner(still, name, 'Original animated stories. New Short ' + _cadence(p).split(' (')[0] + '.',
                         out_dir / FILES['banner'], mobile_preview=out_dir / FILES['banner_mobile_preview'])
    return {'renderer': 'blender', 'profile': prof, 'banner': ban,
            'files': {k: f'{REL_DIR}/{FILES[k]}' for k in ('profile', 'profile_round_preview', 'banner',
                                                             'banner_mobile_preview', 'lineup')}}


# ---------------------------------------------------------------- pack on disk
def _markdown(kit):
    ud = kit['upload_defaults']
    out = [f'# Channel kit: {kit["channel_name"]}', '',
           f'Generated {kit["generated_at_iso"]} by Blox Studio from the cast and your settings. Everything here is '
           'original; no logos, trademarks or other channels\' artwork.', '',
           '## Name and handle suggestions', '']
    for n in kit['names']:
        out.append(f'- **{n["name"]}** — @{n["handle"]}' + (f' (also @{", @".join(n["handle_alternatives"])})'
                                                           if n['handle_alternatives'] else '') + f' — {n["why"]}')
    out += ['', 'Handles are first come, first served: check availability when you create the channel.', '',
            '## Channel description', '', '```', kit['description'], '```', '',
            '## Default upload settings', '',
            f'- Audience: {ud["audience"]}', f'- Altered or synthetic content: {ud["altered_or_synthetic_content"]}',
            f'- Category: {ud["category"]} (id {ud["category_id"]})', f'- Language: {ud["language"]}',
            f'- Tags: {", ".join(ud["tags"]) or "(none)"}', f'- Visibility: {ud["visibility"]}',
            f'- Comments: {ud["comments"]}', f'- Notify subscribers: {"yes" if ud["notify_subscribers"] else "no"}',
            f'- Thumbnail: {ud["thumbnail"]}', f'- Format: {ud["format"]}', '',
            '## Images', '']
    files = kit.get('files') or {}
    if files.get('profile'):
        out += [f'- Profile picture: {FILES["profile"]} ({PROFILE_SIZE}x{PROFILE_SIZE}, face inside the round crop; '
                f'{FILES["profile_round_preview"]} shows the crop)',
                f'- Banner: {FILES["banner"]} ({BANNER_SIZE[0]}x{BANNER_SIZE[1]}; the {SAFE_AREA[0]}x{SAFE_AREA[1]} '
                f'safe area holds the cast and the name; {FILES["banner_mobile_preview"]} is that crop)']
    else:
        out.append('- Images not rendered yet' + (f': {kit["images"].get("error")}' if kit.get('images', {}).get('error')
                                                 else ' (generate with images on the Connections page).'))
    out += ['', '## Steps', '']
    out += [f'{i + 1}. {s}' for i, s in enumerate(kit['steps'])]
    out += ['', '## Rules this kit follows', ''] + [f'- {r}' for r in kit['brand_rules']] + ['']
    return '\n'.join(out)


def generate(images=True, name=None, p=None, chars=None, d=None):
    """Write the pack to data/channel_kit/ and return it. Images need Blender; the text pack never does."""
    p = p or prefsmod.get(d)
    chars = chars if chars is not None else repo.characters(d=d)
    out_dir = kit_dir()
    os.makedirs(out_dir, exist_ok=True)
    previous = load()
    # Regenerating without a name keeps the name the owner chose last time.
    kit = text_pack(p, chars, name or (previous or {}).get('channel_name'))
    kit.update(version=1, generated_at=now(), files={'kit_json': f'{REL_DIR}/{FILES["kit_json"]}',
                                                      'kit_md': f'{REL_DIR}/{FILES["kit_md"]}'})
    from datetime import datetime, timezone
    kit['generated_at_iso'] = datetime.fromtimestamp(kit['generated_at'], timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    kit['images'] = {'renderer': 'skipped', 'error': None}
    if images:
        if not BL.available():
            kit['images']['error'] = 'Blender is not installed or BLENDER_BIN is wrong'
        else:
            try:
                rep = render_images(p, chars, kit['channel_name'], out_dir)
                kit['images'] = {'renderer': rep['renderer'], 'error': None, 'profile': rep['profile'],
                                 'banner': rep['banner']}
                kit['files'].update(rep['files'])
            except Exception as e:  # the text pack is still useful without the images
                kit['images'] = {'renderer': 'failed', 'error': f'{type(e).__name__}: {str(e)[:300]}'}
    if not kit['files'].get('profile') and previous and (previous.get('files') or {}).get('profile'):
        # Keep earlier images when only the text was regenerated.
        for k in ('profile', 'profile_round_preview', 'banner', 'banner_mobile_preview', 'lineup'):
            if previous['files'].get(k) and (config.DATA_DIR / previous['files'][k]).exists():
                kit['files'][k] = previous['files'][k]
        kit['images'] = dict(previous.get('images') or {}, note='images from an earlier run')
    with open(out_dir / FILES['kit_json'], 'w') as f:
        json.dump(kit, f, indent=1, sort_keys=True)
    with open(out_dir / FILES['kit_md'], 'w') as f:
        f.write(_markdown(kit))
    store.audit('channel_kit_generated', {'images': kit['images'].get('renderer'), 'name': kit['channel_name']},
                actor='owner', d=d)
    return kit


def load():
    path = kit_dir() / FILES['kit_json']
    if not path.exists():
        return None
    try:
        with open(path) as f:
            kit = json.load(f)
    except (OSError, ValueError):
        return None
    files = dict(kit.get('files') or {})
    kit['files'] = {k: v for k, v in files.items() if (config.DATA_DIR / v).exists()}
    return kit
