"""Slow, local integration test: every set preset builds in Blender and renders a still with a background.

One frame per preset from a real story (preview quality, 270x480). Checks the Blender log (pieces built
as linked duplicates), the evaluated scene (no set piece between the camera and the subject's face, by a
ray test against the rendered geometry) and the pixels (the background around the characters has
structure, not an empty sky and plain). Skipped automatically when Blender is not installed.
"""
import json
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

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BATCH = os.path.join(ROOT, 'stories', 'batch-2026-10-03-claude.json')
CASES = [('town_street', 'The Golden Coin Trade', 's2'), ('classroom', 'Homework Panic', 's5'),
         ('bedroom', 'The Last Slice', 's3'), ('night_forest', 'The Scariest Campfire Story', 's2'),
         ('sky_obby', 'The Spring Pad Shortcut', 's5'), ('lava_obby', 'The Perfect Speedrun', 's2'),
         ('studio', 'The Perfect Selfie', 's2')]


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


@pytest.mark.parametrize('preset,title,shot', CASES)
def test_set_preset_builds_and_renders_a_busy_background(tmp_path, preset, title, shot):
    from PIL import Image
    from blox.qa.motion import load_telemetry
    with open(BATCH) as f:
        plan = next(p for p in json.load(f) if p['title'] == title)
    # A wide framing of the shot leaves room around the characters to measure the background; the set
    # layout widens its clearance for it like for any wide shot.
    cam = next(x for x in plan['shots'] if x['id'] == shot)['camera']
    cam.update(framing_start='wide', framing_end='wide', move='static')
    m = C.compile_plan(plan, fps=30, width=270, height=480)
    assert m['setting']['preset'] == preset
    bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id'])
              for c in m['cast']}
    solved = SV.solve(m, bibles)
    s = next(x for x in m['shots'] if x['id'] == shot)
    f0 = (s['start_frame'] + s['end_frame']) // 2
    lines = []
    p = prefs.DEFAULTS
    res = BL.render_range(m, bibles, solved, f0, f0 + 1, str(tmp_path), dict(p, production=dict(
        p['production'], width=270, height=480)), quality='preview', on_progress=lines.append)
    built = next(t for t in lines if t.startswith('built scene'))
    pieces = int(re.search(r'set_pieces=(\d+)', built).group(1))
    meshes = int(re.search(r'set_meshes=(\d+)', built).group(1))
    assert pieces == len(solved['set']['pieces']) and pieces >= 25
    assert meshes <= len(solved['set']['templates']) and meshes < pieces  # repeated pieces share a mesh
    rec = load_telemetry([res['telemetry']])[f0]
    subj = s['camera']['subject']
    if subj in rec['characters']:
        assert rec['characters'][subj].get('face_blocker') in (None, ''), rec['characters'][subj]['face_blocker']
    img = np.asarray(Image.open(res['preview']).convert('RGB'))
    detail = background_detail(img, [c['bbox2d'] for c in rec['characters'].values()])
    print(f'{preset}: pieces={pieces} meshes={meshes} background_detail={detail:.2f}')
    if preset == 'studio':
        assert detail < 2.0, 'the studio backdrop stays plain'
    else:
        assert detail >= BUSY_MIN[preset], f'{preset} background looks empty ({detail:.2f})'


# Minimum background detail per preset for these shots. Measured on the same wide stills: the old sparse
# sets gave town 1.97, classroom 1.33, bedroom 1.09, night forest 1.03, sky obby 2.61, lava obby 2.06;
# the new sets 7.1, 3.6, 5.2, 1.8, 6.9, 6.5. Thresholds sit between the two (the night forest is dark,
# so its gradients are small in absolute terms).
BUSY_MIN = {'town_street': 4.0, 'classroom': 2.5, 'bedroom': 3.0, 'night_forest': 1.4, 'sky_obby': 4.0,
            'lava_obby': 4.0}
