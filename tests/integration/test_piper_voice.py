"""Slow, local: the real Piper engine with the installed model (skipped when it is not installed)."""
import os

import pytest

from blox.voice import audio as A, piper

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not piper.installed(), reason='Piper voice model not installed '
                                 '(python -m blox.cli install-voice, or set BLOX_VOICES_DIR)')]


def test_real_piper_speech(tmp_path):
    out = str(tmp_path / 'line.wav')
    meta = piper.synthesize("Wait... where's the next platform?!", out, 60,
                            {'pace': 'fast', 'emotion': 'startled'})
    x = A.decode(out)
    assert meta['license'] == 'CC BY 4.0'
    assert 1.0 < len(x) / A.SR < 6.0
    regions = A.speech_regions(x)
    assert len(regions) >= 1 and A.peak_dbfs(x) > -20
    assert os.path.getsize(out) > 20000
