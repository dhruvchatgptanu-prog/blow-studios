"""Loudness normalisation of lines too short for an integrated-loudness measurement ("Heh.")."""
import numpy as np

from blox.voice import audio as A


def _tone(path, seconds, amp=0.05):
    t = np.arange(int(A.SR * seconds)) / A.SR
    A.write_wav(path, (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32))


def test_a_quarter_second_line_is_normalised_not_crashed(tmp_path):
    src, dst = tmp_path / 'heh.wav', tmp_path / 'heh_norm.wav'
    _tone(str(src), 0.24)
    info = A.loudnorm(str(src), str(dst), target=-18.0, tp=-2.0)
    assert info['short_clip'] and dst.exists()
    out = A.decode(str(dst))
    assert abs(len(out) - len(A.decode(str(src)))) < A.SR * 0.05
    rms_db = float(A.db(np.sqrt(np.mean(out ** 2))))
    assert -24.0 < rms_db < -12.0 and A.peak_dbfs(out) <= -1.9


def test_a_normal_line_still_uses_the_two_pass_measurement(tmp_path):
    src, dst = tmp_path / 'line.wav', tmp_path / 'line_norm.wav'
    _tone(str(src), 2.0)
    info = A.loudnorm(str(src), str(dst), target=-18.0, tp=-2.0)
    assert 'short_clip' not in info and np.isfinite(info['input_i'])
