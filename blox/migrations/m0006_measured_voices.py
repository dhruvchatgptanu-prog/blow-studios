"""Measured speaking rates for the built-in cast voices.

Fills ``measured_wps``, ``line_overhead_s`` and ``measured_rate`` (see demo.CHARACTERS) into the voice
config of existing rows of the built-in characters that have no measurement yet and still use the Piper
speaker and length scale the values were measured with. An edited voice, or a character the owner made,
is left alone: its lines keep the conservative default estimate until it is measured.
"""
import json
import time


def up(d):
    from ..demo import CHARACTERS
    from ..manifest import schema as S
    from ..voice.piper import speaker_for
    t = time.time()
    for c in CHARACTERS:
        row = d.one('SELECT voice FROM characters WHERE id=?', (c['id'],))
        if not row:
            continue
        voice = json.loads(row['voice'] or '{}') if isinstance(row['voice'], str) else dict(row['voice'] or {})
        ref = c['voice']
        if any(voice.get(k) not in (None, '') for k in S.MEASURED_VOICE_KEYS):
            continue
        if speaker_for(c['id'], voice) != speaker_for(c['id'], ref):
            continue
        if abs(float(voice.get('piper_length_scale') or 1.0) - float(ref.get('piper_length_scale') or 1.0)) > 1e-6:
            continue
        voice.update({k: ref[k] for k in S.MEASURED_VOICE_KEYS})
        d.execute('UPDATE characters SET voice=?, updated_at=? WHERE id=?', (json.dumps(voice), t, c['id']))
