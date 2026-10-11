"""Voice profiles: YIN pitch, rate, pauses, dynamics, brightness on synthetic signals; storage; match score;
the measure-clips upload (audio deleted afterwards); migration m0007 idempotent."""
import io
import os
import zipfile

import numpy as np
import pytest

from blox import config, migrations
from blox.voice import audio as A, profiles as P

SR = P.SR


def tone(f0, seconds=1.5, vibrato=0.0, sr=SR, syllables=0.0, gaps=()):
    """Harmonic tone (speech-like spectrum) at f0 Hz, optional vibrato, syllable-rate amplitude modulation and
    silent gaps [(start_s, end_s)]."""
    t = np.arange(int(seconds * sr)) / sr
    f = f0 * (1 + vibrato * np.sin(2 * np.pi * 5 * t))
    ph = 2 * np.pi * np.cumsum(f) / sr
    x = sum(np.sin(k * ph) / k for k in range(1, 8))
    if syllables:
        x = x * (0.55 + 0.45 * np.sign(np.sin(2 * np.pi * syllables * t)))
    x = 0.3 * x / np.max(np.abs(x))
    for a, b in gaps:
        x[int(a * sr):int(b * sr)] = 0.0
    return x.astype(np.float32)


def test_yin_tracks_pitch_and_spread():
    _, f0 = P.yin_f0(tone(140.0, vibrato=0.03))
    voiced = f0[np.isfinite(f0)]
    assert len(voiced) > 100
    assert abs(np.median(voiced) - 140.0) < 3.0, 'median within 2 percent'
    p10, p90 = np.percentile(voiced, [10, 90])
    assert 0.3 < 12 * np.log2(p90 / p10) < 1.5, 'the 3 percent vibrato spans about one semitone'
    _, hi = P.yin_f0(tone(320.0))
    assert abs(np.nanmedian(hi) - 320.0) < 5.0, 'no octave error on a higher voice'
    _, lo = P.yin_f0(tone(85.0))
    assert abs(np.nanmedian(lo) - 85.0) < 3.0, 'no octave error on a low voice'
    _, silent = P.yin_f0(np.zeros(SR, dtype=np.float32))
    assert not np.isfinite(silent).any()


def test_clip_measurements_on_speech_like_signal(tmp_path):
    x = tone(180.0, seconds=3.0, vibrato=0.02, syllables=4.0, gaps=[(1.0, 1.4), (2.2, 2.5)])
    path = tmp_path / 'clip.wav'
    A.write_wav(path, x, sr=SR)
    m = P.measure_clip(str(path), 'one two three four five six seven. eight nine ten!')
    assert m['regions'] == 3 and len(m['pauses']) == 2
    assert 0.25 < m['pauses'][0] < 0.45 and 0.2 < m['pauses'][1] < 0.4
    assert abs(m['speech_s'] - 2.3) < 0.2
    assert m['words'] == 10 and m['sentences'] == 2
    assert 6 <= m['syllables'] <= 12, 'four syllable peaks per second of speech'
    assert 150 < m['centroid_hz'] < 1500
    assert len(m['loudness_m']) > 10, 'FFmpeg momentary loudness values'
    prof = P.summarise([m])
    assert abs(prof['f0_median_hz'] - 180.0) < 4.0
    assert prof['words_per_s'] == round(10 / m['speech_s'], 2)
    assert prof['pauses_per_sentence'] == 1.0 and prof['pause_count'] == 2
    assert prof['clips'] == 1 and prof['version'] == P.VERSION
    assert 'dynamics_lu' in prof and prof['loud_lufs'] >= prof['quiet_lufs']


def test_match_score_is_deterministic_and_monotonic():
    target = {'f0_median_hz': 120.0, 'f0_range_st': 4.0, 'words_per_s': 3.5, 'pauses_per_sentence': 1.0,
              'dynamics_lu': 6.0, 'centroid_hz': 1800.0}
    same, comps = P.match_score(dict(target), target)
    assert same == 100 and set(comps) == set(P.SCORE)
    octave_up = dict(target, f0_median_hz=240.0)
    s1, c1 = P.match_score(octave_up, target)
    assert s1 < 100 and c1['f0_median_st']['distance'] == 12.0 and c1['f0_median_st']['score'] == 0.0
    nearly = dict(target, f0_median_hz=127.0)
    s2, _ = P.match_score(nearly, target)
    assert s1 < s2 < 100
    assert P.match_score(octave_up, target)[0] == s1, 'same inputs, same score'
    # Missing measurements are left out of the weighted mean rather than counted as a mismatch.
    partial, c3 = P.match_score({'f0_median_hz': 120.0, 'syllables_per_s': 4.0}, dict(target, syllables_per_s=4.0))
    assert partial == 100 and set(c3) == {'f0_median_st', 'rate'}
    assert P.match_score({}, target) == (None, {})


def test_profile_storage_round_trip(db):
    pid = P.save('warm narrator', 'owner upload; 3 clips; audio deleted', {'f0_median_hz': 110.0, 'clips': 3}, db)
    assert pid.startswith('vp_')
    row = P.get(pid, db)
    assert row['name'] == 'warm narrator' and row['measured']['clips'] == 3
    assert [r['id'] for r in P.list_profiles(db)] == [pid]
    P.delete(pid, db)
    assert P.get(pid, db) is None and P.list_profiles(db) == []


def test_migration_0007_is_idempotent(db):
    assert migrations.migrate(db) == [], 'everything applied by the fixture'
    assert db.table_exists('voice_profiles')
    cols = set(db.columns('voice_profiles'))
    assert {'id', 'name', 'source', 'measured', 'created_at'} <= cols
    assert '0007_voice_profiles' in migrations.applied(db)
    # Running the migration module again must not fail or duplicate anything.
    from blox.migrations import m0007_voice_profiles
    m0007_voice_profiles.up(db)
    assert db.table_exists('voice_profiles') and migrations.pending(db) == []


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def test_import_upload_measures_groups_and_deletes_audio(db, tmp_path):
    def wav_bytes(f0):
        p = tmp_path / f'{f0}.wav'
        A.write_wav(p, tone(f0, seconds=1.2, syllables=4.0), sr=SR)
        return p.read_bytes()
    index = ('file,line,voice\n'
             'deep/a.wav,Hello there friend.,Low voice\n'
             'deep/b.wav,Another line here!,Low voice\n'
             'high/c.wav,Tiny line.,High voice\n')
    data = _zip([('deep/a.wav', wav_bytes(100)), ('deep/b.wav', wav_bytes(105)), ('high/c.wav', wav_bytes(300)),
                 ('index.csv', index), ('notes/readme.txt', 'ignored'), ('../evil.wav', b'RIFF')])
    created = P.import_upload([('clips.zip', data)], 'ignored when grouped', 'owner upload of licensed samples', db)
    by_name = {c['name']: c for c in created}
    assert set(by_name) == {'Low voice', 'High voice'}
    low, high = by_name['Low voice']['measured'], by_name['High voice']['measured']
    assert low['clips'] == 2 and high['clips'] == 1
    assert 95 < low['f0_median_hz'] < 110 and 290 < high['f0_median_hz'] < 310
    assert low['words'] == 6 and low['words_per_s'] > 0 and 'pauses_per_sentence' in low
    assert 'audio deleted' in by_name['Low voice']['source'] and 'owner upload of licensed samples' in by_name['Low voice']['source']
    assert not [p for p in os.listdir(config.WORK_DIR) if p.startswith('clips_')], 'temporary audio removed'
    assert len(P.list_profiles(db)) == 2


def test_import_upload_rejects_empty_and_bad_zip(db):
    with pytest.raises(ValueError):
        P.import_upload([('clips.zip', b'not a zip')], 'x', 'y', db)
    with pytest.raises(ValueError):
        P.import_upload([('notes.txt', b'hello')], 'x', 'y', db)
    assert P.list_profiles(db) == []


def test_measure_endpoint_and_profile_listing(client, db, tmp_path):
    p = tmp_path / 'ref.wav'
    A.write_wav(p, tone(150.0, seconds=1.2, syllables=4.0), sr=SR)
    r = client.post('/api/voice-profiles/measure', data={'name': 'Ref A', 'source': 'test clips',
                                                         'files': [(io.BytesIO(p.read_bytes()), 'ref.wav')]},
                    content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    prof = r.get_json()['profiles'][0]
    assert prof['name'] == 'Ref A' and 140 < prof['measured']['f0_median_hz'] < 160
    listed = client.get('/api/voice-profiles').get_json()['profiles']
    assert [x['id'] for x in listed] == [prof['id']]
    eng = client.get('/api/voice-engines').get_json()
    assert eng['profiles'][0]['id'] == prof['id'] and eng['default'] == 'piper'
    assert set(eng['engines']) == {'piper', 'kokoro', 'speechify', 'openai', 'elevenlabs'}
    assert eng['engines']['speechify']['configured'] is False and eng['engines']['speechify']['free'] is False
    s = client.post('/api/voice-profiles/score', json={'candidate': prof['id'], 'target': prof['id']}).get_json()
    assert s['score'] == 100
    assert client.delete('/api/voice-profiles/' + prof['id']).status_code == 200
    assert client.get('/api/voice-profiles').get_json()['profiles'] == []
    assert client.post('/api/voice-profiles/measure', data={'name': 'x'}, content_type='multipart/form-data').status_code == 400
