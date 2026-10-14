"""Real FFmpeg assembly with the hook band and the reaction-frame cover (shots are test patterns, the faces
come from hand-written telemetry): the band is drawn into the captionless picture, the captions keep clear
of it, QA sees it as planned, and the cover is the biggest reaction face with the hook text on it."""
import copy
import json
import os
import shutil

import numpy as np
import pytest
from PIL import Image

from blox import config, media, prefs
from blox.assembly import assemble, choose_cover_frame
from blox.manifest import compile as C
from blox.qa import motion as MO, story, technical
from blox.qa.report import Checks
from blox.voice import audio as A

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg not installed')
W, H = 270, 480


def plan(**hook):
    return {
        'title': 'Hook cover test', 'duration_s': 8.0,
        'setting': {'preset': 'studio', 'props': []},
        'cast': [{'id': 'hero', 'character_id': 'ch_bloxy'}],
        'shots': [{'id': 's1', 'start_s': 0, 'end_s': 4.0, 'camera': {'subject': 'hero', 'framing_start': 'medium'}},
                  {'id': 's2', 'start_s': 4.0, 'end_s': 8.0, 'camera': {'subject': 'hero', 'framing_start': 'close_up'}}],
        'beats': [{'start_s': 0, 'purpose': 'hook'}, {'start_s': 6, 'purpose': 'payoff'}],
        'performance': {'hero': [{'t': 0, 'position': [0, 0], 'facing': 0, 'expression': 'neutral'},
                                 {'t': 1.0, 'expression': 'startled'}, {'t': 5.0, 'expression': 'screaming'}]},
        'actions': [],
        'lines': [{'id': 'l1', 'speaker': 'hero', 'text': 'Wait, is that the exit?', 't': 0.4, 'emotion': 'startled'},
                  {'id': 'l2', 'speaker': 'hero', 'text': 'No way.', 't': 5.5, 'emotion': 'scared'}],
        'hook': {'text': 'Two doors. One is lava.', 'question': 'Which door?', 't_end': 3.0, **hook},
        'payoff': {'text': 'It was the exit.', 't_start': 6.0},
    }


PLANTED = {20: 0.2, 100: 0.5, 200: 0.22}   # frame -> face height: hook (plain face), outside both windows, payoff scream


def write_telemetry(path, frames):
    with open(path, 'w') as f:
        for fr in range(frames):
            fh = PLANTED.get(fr, 0.12)
            rec = {'frame': fr, 'characters': {'hero': {'face2d': [0.5, 0.45], 'face_height_frac': fh, 'face_dot': 0.9,
                                                        'bbox2d': [0.3, 0.3, 0.7, 1.0], 'parts_visible': []}}}
            f.write(json.dumps(rec) + '\n')


def build(work, p, overlay=True):
    m = C.compile_plan(plan(), fps=30, width=W, height=H, hook_overlay=overlay)
    shot_files = {}
    for s in m['shots']:
        out = str(work / f'{s["id"]}.mp4')
        media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=size={W}x{H}:rate=30',
                   '-frames:v', str(s['end_frame'] - s['start_frame']), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                   '-an', out])
        shot_files[s['id']] = out
    line_audio = {}
    for ln in m['lines']:
        dur = (ln['est_end_frame'] - ln['start_frame']) / 30 * 0.9
        t = np.arange(int(dur * A.SR)) / A.SR
        wav = 0.3 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 4 * t)))
        path = str(work / f'{ln["id"]}.wav')
        A.write_wav(path, wav)
        line_audio[ln['id']] = {'file': path, 'duration_s': dur}
    tele = str(work / 'telemetry.jsonl')
    write_telemetry(tele, m['duration_frames'])
    rep = assemble(m, shot_files, line_audio, p, str(work / 'out'), m['title'], telemetry_paths=[tele])
    return m, rep, line_audio


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    p = copy.deepcopy(prefs.DEFAULTS)
    p['production'].update(width=W, height=H)
    m, rep, line_audio = build(tmp_path_factory.mktemp('hook'), p)
    return m, p, rep, line_audio


def _diff_box(a_path, b_path, frame, fps=30):
    a = MO.decode_rgb_frame(a_path, frame, fps).astype(np.int16)
    b = MO.decode_rgb_frame(b_path, frame, fps).astype(np.int16)
    ys, xs = np.where(np.abs(a - b).max(axis=2) > 60)
    if len(xs) < 20:
        return None
    return [xs.min() / W, ys.min() / H, xs.max() / W, ys.max() / H]


def test_band_is_drawn_into_the_captionless_picture_only_while_the_hook_plays(built):
    m, p, rep, _ = built
    ov = rep['hook_overlay']
    assert ov and ov['end_frame'] == m['hook']['overlay']['end_frame'] == 90 and os.path.exists(ov['file'])
    assert rep['joined_clean'] != rep['joined'] and os.path.exists(rep['joined_clean'])
    pb = ov['box_norm']
    for f in (10, 45, 80):
        box = _diff_box(rep['joined'], rep['joined_clean'], f)
        assert box, f'no hook text at frame {f}'
        assert box[0] >= pb[0] - 0.02 and box[1] >= pb[1] - 0.02 and box[2] <= pb[2] + 0.02 and box[3] <= pb[3] + 0.02
        assert box[1] >= p['production']['safe_area']['top'] - 0.015
    assert _diff_box(rep['joined'], rep['joined_clean'], 95) is None          # gone after the window
    assert _diff_box(rep['joined'], rep['joined_clean'], 200) is None
    # The frame count and the clean picture are untouched.
    assert media.probe(rep['joined_clean'])['format'] and rep['joined_clean'].endswith('joined_cut.mp4')


def test_captions_during_the_band_sit_at_the_bottom_and_the_final_only_adds_captions(built):
    m, p, rep, _ = built
    during = [c for c in rep['captions'] if c['start_frame'] < 90]
    assert during and all(c['position'] == 'bottom' for c in during)
    c = during[0]
    f = (c['start_frame'] + c['end_frame']) // 2
    box = _diff_box(rep['final'], rep['joined'], f)
    assert box and box[1] > 0.5, 'the final adds only the caption at the bottom; the band is already in the picture'
    ov = rep['hook_overlay']['box_norm']
    for c in during:
        cb = [c['box'][0] / W, c['box'][1] / H, c['box'][2] / W, c['box'][3] / H]
        assert cb[1] >= ov[3] or cb[3] <= ov[1], (c['id'], cb, ov)


def test_qa_treats_the_band_as_planned(built):
    m, p, rep, line_audio = built
    ck = Checks(30)
    technical.run(ck, m, rep['final'], p, rep, dialog_wav=rep['mix']['paths']['dialog'])
    story._caption_pixels(ck, m, rep, p)
    story._hook_pixels(ck, m, rep, p)
    by = {c['id']: c for c in ck.items}
    for cid in ('black_frames', 'freeze', 'completeness', 'captions_safe_area', 'hook_text_on_screen'):
        assert by[cid]['status'] == 'pass', (cid, by[cid]['evidence'])
    ev = by['hook_text_on_screen']['evidence']
    assert ev['text'] == 'Two doors. One is lava.' and ev['problems'] == [] and ev['measured_box'][1] >= 0.12 - 0.015
    # A band that was not drawn, or one meeting a caption, fails the check.
    ck = Checks(30)
    story._hook_pixels(ck, m, dict(rep, joined_clean=rep['joined']), p)
    assert ck.items[-1]['status'] == 'fail'
    ck = Checks(30)
    clash = dict(rep, captions=[dict(rep['captions'][0], box=[0, 0, W, H], start_frame=0, end_frame=90)])
    story._hook_pixels(ck, m, clash, p)
    assert ck.items[-1]['status'] == 'fail' and 'meets caption' in ck.items[-1]['evidence']['problems'][0]


def test_cover_is_the_biggest_reaction_face_with_the_hook_text(built):
    m, p, rep, _ = built
    assert m['cover_source'] == 'auto'
    assert rep['cover_frame'] == 200 and rep['cover_choice']['expression'] == 'screaming'
    assert rep['cover_choice']['character'] == 'hero' and rep['cover_choice']['score'] > 0.2 * 1.5
    # The face outside the hook and payoff windows is ignored however big; the plain hook face scores lower.
    f, ev = choose_cover_frame(m, MO.load_telemetry([os.path.join(os.path.dirname(rep['joined']), '..', 'telemetry.jsonl')]))
    assert f == 200 and ev['face_height_frac'] == 0.22
    cover = np.asarray(Image.open(rep['cover']).convert('RGB')).astype(np.int16)
    tmp = rep['cover'] + '.clean.png'
    media.extract_frame(rep['joined_clean'], 200, 30, tmp)
    clean = np.asarray(Image.open(tmp).convert('RGB')).astype(np.int16)
    ys, xs = np.where(np.abs(cover - clean).max(axis=2) > 60)
    band = rep['cover_hook_band']
    assert len(xs) > 200 and band['lines'] == ['Two doors.', 'One is lava.']
    assert band['box'][1] - 2 <= ys.min() and ys.max() <= band['box'][3] + 2
    assert ys.min() / H >= p['production']['safe_area']['top'] - 0.015
    assert os.path.getsize(rep['thumbnail']) > 1000 and os.path.getsize(rep['thumbnail_16x9']) > 1000


def test_plan_and_owner_choices_beat_the_automatic_pick(built):
    m, p, rep, _ = built
    tele = MO.load_telemetry([os.path.join(os.path.dirname(rep['joined']), '..', 'telemetry.jsonl')])
    owner = dict(m, cover_frame=33, cover_source='owner')
    assert choose_cover_frame(owner, tele) == (33, {'source': 'owner', 'note': 'cover frame set by the owner'})
    planned = dict(m, cover_frame=44, cover_source='plan')
    assert choose_cover_frame(planned, tele)[0] == 44
    assert choose_cover_frame(m, {})[0] == m['cover_frame']
    # Same-size faces everywhere: the biggest reaction wins (the scream in the payoff), earliest frame of it.
    quiet = {f: r for f, r in tele.items() if f not in PLANTED}
    assert choose_cover_frame(m, quiet)[0] == 180 and choose_cover_frame(m, quiet)[1]['expression'] == 'screaming'


def test_band_and_a_planned_freeze_share_one_encode(tmp_path):
    p = copy.deepcopy(prefs.DEFAULTS)
    p['production'].update(width=W, height=H)
    pl = plan()
    pl['effects'] = [{'type': 'freeze', 't': 6.6, 'frames': 12}]
    m = C.compile_plan(pl, fps=30, width=W, height=H)
    shot_files = {}
    for s in m['shots']:
        out = str(tmp_path / f'{s["id"]}.mp4')
        media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=size={W}x{H}:rate=30',
                   '-frames:v', str(s['end_frame'] - s['start_frame']), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                   '-an', out])
        shot_files[s['id']] = out
    rep = assemble(m, shot_files, {}, p, str(tmp_path / 'out'), m['title'])
    assert rep['hook_overlay'] and rep['joined_clean'].endswith('joined_cut.mp4')
    out, _ = media.run([config.FFPROBE_BIN, '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries',
                        'stream=nb_read_frames', '-of', 'csv=p=0', rep['joined']])
    assert int(out.decode().strip().split(',')[0]) == m['duration_frames']
    assert _diff_box(rep['joined'], rep['joined_clean'], 45)          # the band
    fz = m['effects'][0]
    held = MO.decode_rgb_frame(rep['joined'], fz['frame'] + 3, 30)
    first = MO.decode_rgb_frame(rep['joined'], fz['frame'], 30)
    assert np.abs(held.astype(np.int16) - first.astype(np.int16)).mean() < 2   # the freeze holds its frame


def test_off_screen_hook_keeps_the_picture_clean(tmp_path):
    p = copy.deepcopy(prefs.DEFAULTS)
    p['production'].update(width=W, height=H)
    m, rep, _ = build(tmp_path, p, overlay=False)
    assert m['hook']['on_screen'] is False and rep['hook_overlay'] is None
    assert rep['joined_clean'] == rep['joined'] and not os.path.exists(os.path.join(os.path.dirname(rep['joined']), 'hook.ass'))
    assert rep['cover_hook_band'] is None and rep['cover_frame'] == 200
    ck = Checks(30)
    story._hook_pixels(ck, m, rep, p)
    assert ck.items[-1]['status'] == 'skipped'
