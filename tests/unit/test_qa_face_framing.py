"""A shot whose subject shows the camera a cheek (face turned away) or no face at all fails face framing."""
from blox.qa import motion
from blox.qa.report import Checks


def _tele(face_dot, fx=0.5):
    return {f: {'camera': {'location': [1, 0, 1.7]},
                'characters': {'hero': {'face2d': [fx, 0.4, 1.0], 'face_dot': face_dot, 'in_front_of_camera': True,
                                        'root': [0, 0, 0], 'face': [0, 0, 1.7]}}} for f in range(30)}


M = {'shots': [{'id': 's1', 'start_frame': 0, 'end_frame': 30, 'camera': {'subject': 'hero'}}]}


def test_face_toward_camera_passes():
    ck = Checks(30)
    motion._face_framing(ck, M, _tele(0.8))
    assert ck.items[0]['status'] == 'pass'


def test_cheek_shot_fails_with_a_face_camera_repair():
    ck = Checks(30)
    motion._face_framing(ck, M, _tele(0.1))
    c = ck.items[0]
    assert c['status'] == 'fail' and c['evidence']['face_turned_away'] == 30
    assert c['repair']['params'] == {'face_camera': 'hero', 'camera_wider': True}


def test_face_out_of_frame_fails():
    ck = Checks(30)
    motion._face_framing(ck, M, _tele(0.9, fx=1.3))
    assert ck.items[0]['status'] == 'fail' and ck.items[0]['evidence']['face_off_screen'] == 30
