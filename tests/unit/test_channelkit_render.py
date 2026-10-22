"""Integration: the real profile picture through Blender (skipped when Blender is not installed)."""
import pytest
from PIL import Image

from blox import channel_kit as CK, config, prefs as prefsmod, repo
from blox.animation import blender as BL

pytestmark = pytest.mark.skipif(not BL.available(), reason='Blender not installed')


def test_real_profile_picture_renders_face_inside_the_circle(db):
    p = prefsmod.get(db)
    chars = repo.characters(d=db)
    cast = CK._cast(chars)
    hero = cast[0]
    assert hero[2] == 'Bloxy'
    work = config.WORK_DIR / 'channel_kit_test' / 'profile'
    still = CK.render_still([hero], hero[0], 'medium_close', CK.PROFILE_SIZE, CK.PROFILE_SIZE,
                            {hero[0]: {'position': [0.0, 0.0], 'expression': 'happy'}}, work, p, 'final')
    with Image.open(still) as im:
        assert im.size == (800, 800) and im.mode == 'RGBA'
        alpha = im.split()[-1]
        # Transparent background in the corners, opaque subject in the middle.
        assert all(alpha.getpixel(xy) == 0 for xy in ((2, 2), (797, 2), (2, 200), (797, 200)))
        assert alpha.getpixel((400, 400)) == 255
        bbox = alpha.point(lambda v: 255 if v > 24 else 0).getbbox()
    # The head must sit inside the inscribed circle of the square (YouTube shows a round avatar): the
    # face is centred, and the opaque area must not reach the corners.
    out = config.DATA_DIR / 'channel_kit_test'
    out.mkdir(parents=True, exist_ok=True)
    rep = CK.compose_profile(still, out / 'profile.png', round_preview=out / 'round.png')
    assert rep['size'] == [800, 800]
    with Image.open(out / 'profile.png') as im:
        bg = CK._rgb(CK.PROFILE_BG)
        assert im.getpixel((3, 3)) == bg and im.getpixel((796, 3)) == bg
        centre = im.getpixel((400, 380))
        assert centre != bg
        # Bloxy's face: warm yellow skin at the centre of the frame.
        assert centre[0] > 180 and centre[1] > 140 and centre[2] < 140
    x0, y0, x1, y1 = bbox
    assert x0 > 0 and y0 > 60 and x1 < 800, bbox  # not touching the left/right/top edges
