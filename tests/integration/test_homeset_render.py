"""Slow, local integration test: the 'home' set (and the 'park') builds in Blender and renders one still of the
family skit.

One frame of the fixture story's two-shot per preset at preview quality (270x480). Checks the Blender log (pieces built
as linked duplicates of their templates), the evaluated scene (no set piece between the camera and any face,
by Blender's own ray test) and the pixels (a busy, warm interior behind the characters, not a bare wall).
Skipped automatically when Blender is not installed.
"""
import os
import re
import shutil

import numpy as np
import pytest

from blox import config, demo, prefs
from blox.animation import blender as BL, solver as SV
from blox.manifest import compile as C

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not shutil.which(config.BLENDER_BIN), reason='Blender not installed')]


def background_detail(img, boxes):
    """Mean luminance gradient (0-255 scale) over the top 70% of the frame, outside the characters."""
    g = img[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    h, w = g.shape
    grad = np.zeros_like(g)
    grad[:, 1:] += np.abs(np.diff(g, axis=1))
    grad[1:, :] += np.abs(np.diff(g, axis=0))
    mask = np.zeros_like(g, dtype=bool)
    mask[:int(h * 0.7), :] = True
    for x0, y0, x1, y1 in boxes:
        mask[max(0, int(y0 * h) - 4):int(y1 * h) + 4, max(0, int(x0 * w) - 4):int(x1 * w) + 4] = False
    return float(grad[mask].mean()) if mask.any() else 0.0


@pytest.mark.parametrize('preset', ['home', 'park'])
def test_home_set_builds_and_renders_the_family_skit(tmp_path, preset):
    from PIL import Image
    from blox.qa.motion import load_telemetry
    plan = demo.home_plan() if preset == 'home' else demo.park_plan()
    # A wide framing of the two-shot leaves room around the characters to measure the background.
    cam = next(x for x in plan['shots'] if x['id'] == 's1')['camera']
    cam.update(framing_start='wide', framing_end='wide', move='static')
    m = C.compile_plan(plan, fps=30, width=270, height=480)
    assert m['setting']['preset'] == preset
    bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id'])
              for c in m['cast']}
    solved = SV.solve(m, bibles)
    s = next(x for x in m['shots'] if x['id'] == 's1')
    f0 = (s['start_frame'] + s['end_frame']) // 2
    lines = []
    p = prefs.DEFAULTS
    res = BL.render_range(m, bibles, solved, f0, f0 + 1, str(tmp_path), dict(p, production=dict(
        p['production'], width=270, height=480)), quality='preview', on_progress=lines.append)
    built = next(t for t in lines if t.startswith('built scene'))
    pieces = int(re.search(r'set_pieces=(\d+)', built).group(1))
    meshes = int(re.search(r'set_meshes=(\d+)', built).group(1))
    assert pieces == len(solved['set']['pieces']) and pieces >= 50
    assert meshes <= len(solved['set']['templates']) and meshes < pieces  # repeated pieces share a mesh
    rec = load_telemetry([res['telemetry']])[f0]
    assert set(rec['characters']) == {'bloxy', 'pip', 'dot'}
    for cid, c in rec['characters'].items():
        assert c.get('face_blocker') in (None, ''), (cid, c['face_blocker'])
    img = np.asarray(Image.open(res['preview']).convert('RGB'))
    detail = background_detail(img, [c['bbox2d'] for c in rec['characters'].values()])
    print(f'{preset}: pieces={pieces} meshes={meshes} background_detail={detail:.2f}')
    assert detail >= BUSY_MIN[preset], f'{preset} background looks empty ({detail:.2f})'
    assert os.path.exists(res['video'])


# Measured on these stills: the furnished home gives 5.8 (beams, shelves, windows across the top of the frame),
# the park 5.6 (trees, houses and skyline); the bare-walled first cut of the home measured 2.8. The bars sit between,
# level with the town street's.
BUSY_MIN = {'home': 4.0, 'park': 4.0}
