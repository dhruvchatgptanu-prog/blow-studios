"""Hand-to-face action checks measure against the point the gesture is meant to touch."""
from blox.qa import motion
from blox.qa.report import Checks


def run(kind, palm, chin=True):
    recs = {}
    for f in range(40):
        recs[f] = {'face': [0.0, -0.29, 1.73], 'palm_r': list(palm), 'root': [0.0, 0.0, 0.0],
                   'face_dot': 1.0, 'face_height_frac': 0.3, 'bbox2d': [0.3, 0.2, 0.7, 0.8],
                   'in_front_of_camera': True}
        if chin:
            recs[f]['chin'] = [0.0, -0.38, 1.49]
    m = {'setting': {'props': []},
         'shots': [{'id': 's1', 'start_frame': 0, 'end_frame': 40, 'camera': {'subject': 'hero'}}],
         'tracks': {'actions': [{'id': 'a1', 'character': 'hero', 'type': kind, 'params': {}, 'start_frame': 0,
                                 'anticipation_frames': 5, 'main_frames': 20, 'end_frame': 35}]}}
    ck = Checks(30)
    motion._actions(ck, m, 'hero', recs, None, {}, 1.0)
    return ck.items[0]


def test_think_hand_at_the_chin_passes():
    c = run('think', (0.0, -0.39, 1.50))
    assert c['status'] == 'pass' and c['evidence']['min_palm_to_chin_m'] < 0.05


def test_think_hand_far_from_the_chin_fails():
    c = run('think', (0.3, -0.2, 1.1))
    assert c['status'] == 'fail' and c['repair']


def test_think_without_chin_telemetry_estimates_it_with_lower_confidence():
    c = run('think', (0.0, -0.36, 1.50), chin=False)
    assert c['status'] == 'pass' and c['confidence'] < 0.85 and 'estimated' in c['evidence']['chin']


def test_facepalm_still_measures_the_face():
    assert run('facepalm', (0.03, -0.38, 1.80))['status'] == 'pass'
    assert run('facepalm', (0.0, -0.39, 1.50))['status'] == 'fail'
