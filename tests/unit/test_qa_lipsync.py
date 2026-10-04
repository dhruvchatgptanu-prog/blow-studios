"""Lip-sync QA on pixels: rendered mouth darkness must follow the voice; hand-covered frames are excluded."""
import numpy as np

from blox.qa import motion
from blox.qa.report import Checks

W, H, N = 108, 192, 60


def scene(open_series, hand_over=False):
    env = np.clip(np.sin(np.linspace(0, 6 * np.pi, N)) ** 2, 0, 1)
    gray = np.full((N + 5, H, W), 0.8, dtype=np.float32)
    recs = {}
    for f in range(N + 5):
        o = open_series[f] if f < N else 0.0
        h = int(2 + 14 * o)
        gray[f, 96 - h // 2:96 + h // 2 + 1, 44:64] = 0.15
        recs[f] = {'mouth_open': float(o), 'face_dot': 1.0, 'face_height_frac': 0.3, 'bbox2d': [0.2, 0.2, 0.8, 0.8],
                   'in_front_of_camera': True, 'mouth2d': [0.5, 0.5, 1.0], 'mouth_width_px': 40.0,
                   'palm2d_r': [0.52, 0.42, 0.9] if hand_over else [0.5, 0.95, 0.9]}
    m = {'fps': 30, 'width': W, 'height': H, 'shots': [{'id': 's1', 'start_frame': 0, 'end_frame': N + 5}],
         'lines': [{'id': 'l1', 'speaker': 'hero', 'start_frame': 0, 'est_end_frame': N}]}
    return m, recs, gray, env


def run(open_series, hand_over=False):
    m, recs, gray, env = scene(open_series, hand_over)
    ck = Checks(30)
    motion._lipsync(ck, m, 'hero', recs, gray, {'l1': env})
    return ck.items[0]


def test_mouth_that_follows_the_voice_passes():
    env = np.clip(np.sin(np.linspace(0, 6 * np.pi, N)) ** 2, 0, 1)
    c = run(env)
    assert c['status'] == 'pass' and c['evidence']['pixel_mouth_voice_correlation'] > 0.8


def test_frozen_rendered_mouth_fails_even_if_scene_data_moves():
    env = np.clip(np.sin(np.linspace(0, 6 * np.pi, N)) ** 2, 0, 1)
    m, recs, gray, _ = scene(env)
    gray[:, 80:112, 40:68] = gray[0, 80:112, 40:68]  # rendered mouth never changes
    ck = Checks(30)
    motion._lipsync(ck, m, 'hero', recs, gray, {'l1': env})
    assert ck.items[0]['status'] == 'fail'


def test_hand_over_face_frames_are_excluded_not_failed():
    env = np.clip(np.sin(np.linspace(0, 6 * np.pi, N)) ** 2, 0, 1)
    c = run(env, hand_over=True)
    assert c['evidence']['frames_mouth_covered_by_hand'] == N
    assert c['evidence']['pixel_mouth_voice_correlation'] is None and c['status'] == 'pass' and c['confidence'] < 0.8


def test_hand_on_chest_is_not_occlusion():
    rec = {'mouth2d': [0.5, 0.5, 1.0], 'face_height_frac': 0.3, 'palm2d_l': [0.5, 0.75, 0.9]}
    assert motion._hand_over_mouth(rec, 9 / 16) is False
    rec['palm2d_l'] = [0.5, 0.4, 0.9]
    assert motion._hand_over_mouth(rec, 9 / 16) is True
    rec['palm2d_l'] = [0.5, 0.4, 1.3]  # behind the head
    assert motion._hand_over_mouth(rec, 9 / 16) is False
