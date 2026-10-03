"""Real FFmpeg work (no network): assembly, ducking, captions, loudness and technical QA on the output.

Shots here are FFmpeg test patterns standing in for rendered shots; the Blender renderer itself is
exercised in tests/integration (slow) and by ``python -m blox.cli demo``.
"""
import os
import shutil

import numpy as np
import pytest

from blox import config, demo, media, prefs
from blox.assembly import assemble, duck_envelope
from blox.manifest import compile as C
from blox.qa.report import Checks
from blox.qa import technical
from blox.voice import audio as A

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg not installed')
W, H = 270, 480


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    work = tmp_path_factory.mktemp('assembly')
    m = C.compile_plan(demo.plan(), fps=30, width=W, height=H)
    p = json_copy(prefs.DEFAULTS)
    p['production'].update(width=W, height=H)
    shot_files = {}
    for s in m['shots']:
        n = s['end_frame'] - s['start_frame']
        out = str(work / f'{s["id"]}.mp4')
        media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=size={W}x{H}:rate=30',
                   '-frames:v', str(n), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', out])
        shot_files[s['id']] = out
    line_audio = {}
    for ln in m['lines']:
        dur = (ln['est_end_frame'] - ln['start_frame']) / 30 * 0.9
        t = np.arange(int(dur * A.SR)) / A.SR
        syll = 0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 4 * t))  # 4 Hz syllable-like gating
        wav = 0.3 * np.sin(2 * np.pi * 180 * t) * syll
        path = str(work / f'{ln["id"]}.wav')
        A.write_wav(path, wav)
        line_audio[ln['id']] = {'file': path, 'duration_s': dur}
    rep = assemble(m, shot_files, line_audio, p, str(work / 'out'), m['title'])
    return m, p, rep, line_audio


def json_copy(x):
    import json
    return json.loads(json.dumps(x))


def test_final_file_properties(built):
    m, p, rep, _ = built
    info = media.probe(rep['final'])
    vs, au = media.video_stream(info), media.audio_stream(info)
    assert (int(vs['width']), int(vs['height'])) == (W, H)
    assert media.fraction(vs['avg_frame_rate']) == pytest.approx(30, abs=0.01)
    assert abs(float(info['format']['duration']) - 30.0) < 0.1
    assert au['codec_name'] == 'aac' and int(au['sample_rate']) == 48000 and int(au['channels']) == 2


def test_loudness_normalised_without_clipping(built):
    m, p, rep, _ = built
    lufs = A.loudness(rep['mix']['paths']['master'])
    assert abs(lufs['integrated_lufs'] - p['qa']['loudness_target_lufs']) < 1.5
    assert lufs['true_peak_dbtp'] <= p['qa']['true_peak_max_dbtp'] + 0.2


def test_music_is_ducked_under_dialogue(built):
    m, p, rep, _ = built
    music = A.decode(rep['mix']['paths']['music'])
    dialog = A.decode(rep['mix']['paths']['dialog'])
    env = A.rms_envelope(dialog)
    speech = env > 0.02
    menv = A.rms_envelope(music)
    n = min(len(menv), len(speech))
    assert np.mean(menv[:n][speech[:n]]) < 0.6 * np.mean(menv[:n][~speech[:n]])


def test_duck_envelope_shape():
    sig = np.concatenate([np.zeros(A.SR), 0.3 * np.ones(A.SR), np.zeros(A.SR)])
    gains, _ = duck_envelope(sig, 10)
    assert gains[int(0.5 * A.SR)] == pytest.approx(1.0, abs=0.02)
    assert gains[int(1.5 * A.SR)] == pytest.approx(10 ** (-10 / 20), abs=0.03)


def test_captions_written_inside_safe_area(built):
    m, p, rep, _ = built
    assert rep['captions'], 'caption layout expected'
    sa = p['production']['safe_area']
    for c in rep['captions']:
        x0, y0, x1, y1 = c['box']
        # Safe area edges are fractions of the frame: clear of the Shorts UI on the right and bottom.
        assert x0 >= sa['left'] * W - 2 and x1 <= sa['right'] * W + 2, c
        assert y0 >= sa['top'] * H - 2 and y1 <= sa['bottom'] * H + 2, c
    text = open(rep['captions_file'], encoding='utf-8').read()
    assert 'Dialogue:' in text


def test_cover_and_thumbnail_files(built):
    m, p, rep, _ = built
    for k in ('cover', 'thumbnail'):
        assert os.path.getsize(rep[k]) > 1000


def test_technical_qa_passes_on_good_file(built):
    m, p, rep, _ = built
    ck = Checks(30)
    technical.run(ck, m, rep['final'], p, rep, rep['mix']['paths']['dialog'])
    by = {c['id']: c for c in ck.items}
    for cid in ('decode', 'dimensions', 'fps', 'duration'):
        assert by[cid]['status'] == 'pass', (cid, by[cid]['evidence'])


def test_technical_qa_catches_wrong_dimensions_and_missing_audio(built, tmp_path):
    m, p, rep, _ = built
    bad = str(tmp_path / 'bad.mp4')
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=25',
               '-t', '30', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', bad])
    ck = Checks(30)
    technical.run(ck, m, bad, p, {}, None)
    by = {c['id']: c for c in ck.items}
    assert by['dimensions']['status'] == 'fail' and by['fps']['status'] == 'fail'
    assert any(c['status'] == 'fail' and 'audio' in c['id'] for c in ck.items)


def test_corrupt_file_fails_decode(tmp_path, built):
    m, p, rep, _ = built
    bad = tmp_path / 'corrupt.mp4'
    data = open(rep['final'], 'rb').read()
    bad.write_bytes(data[: len(data) // 3])
    ck = Checks(30)
    technical.run(ck, m, str(bad), p, {}, None)
    assert next(c for c in ck.items if c['id'] == 'decode')['status'] == 'fail'


def test_media_commands_never_use_a_shell():
    import inspect
    src = inspect.getsource(media)
    assert 'shell=True' not in src


def test_upscaled_shots_are_disclosed(built):
    m, p, rep, _ = built
    ck = Checks(30)
    rep2 = dict(rep, shot_native_sizes=[{'shot': 's1', 'renderer': 'runway', 'native': [720, 1280], 'upscaled': True},
                                        {'shot': 's2', 'renderer': 'blender', 'native': [W, H], 'upscaled': False}])
    technical.run(ck, m, rep['final'], p, rep2, rep['mix']['paths']['dialog'])
    c = next(x for x in ck.items if x['id'] == 'native_resolution')
    assert [s['shot'] for s in c['evidence']['upscaled_shots']] == ['s1'] and 'upscaled' in c['evidence']['note']
