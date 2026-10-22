"""Voice bake-off: fixed script, candidates, runs (free engines only unless explicitly paid), scoring against a
target profile, "Use this voice", and the per-character voice config validation."""
import re
import wave

import numpy as np
import pytest

from blox import repo, vault
from blox.util import Blocked
from blox.voice import bakeoff, piper, profiles, voicecfg


def fake_wav(path, seconds=1.2, sr=22050, f0=160):
    t = np.arange(int(seconds * sr)) / sr
    x = (0.3 * np.sin(2 * np.pi * f0 * t) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 4 * t))) * 32767).astype(np.int16)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


@pytest.fixture
def piper_mock(tmp_path, monkeypatch):
    d = tmp_path / 'voices'
    monkeypatch.setenv('BLOX_VOICES_DIR', str(d))
    m = d / piper.DEFAULT_MODEL
    m.mkdir(parents=True)
    (m / f'{piper.DEFAULT_MODEL}.onnx').write_bytes(b'onnx')
    (m / f'{piper.DEFAULT_MODEL}.onnx.json').write_text('{}')
    calls = []
    real_run = piper.media.run

    def run(args, timeout=None, **kw):
        if args[1:3] == ['-m', 'piper']:
            calls.append(args)
            fake_wav(args[args.index('-f') + 1], f0=100 + 2 * int(args[args.index('-s') + 1]) % 200)
            return b'', b''
        return real_run(args, timeout=timeout, **kw)
    monkeypatch.setattr(piper.media, 'run', run)
    return calls


def test_fixed_script_covers_the_roles_and_is_original():
    assert len(bakeoff.TEST_SCRIPT) == 12
    roles = {ln['role'] for ln in bakeoff.TEST_SCRIPT}
    assert roles == set(bakeoff.ROLES)
    assert all(re.fullmatch(r'b\d\d', ln['id']) for ln in bakeoff.TEST_SCRIPT)
    text = ' '.join(ln['text'] for ln in bakeoff.TEST_SCRIPT).lower()
    for word in ('roblox', 'minecraft', 'fortnite', 'youtube', 'tiktok'):
        assert word not in text
    assert any(ln['volume'] == 'shout' for ln in bakeoff.TEST_SCRIPT)
    assert any('...' in ln['text'] for ln in bakeoff.TEST_SCRIPT if ln['role'] == 'pause_punchline')


def test_candidates_free_first_and_paid_only_when_configured(db):
    chars = repo.characters(active_only=False, d=db)
    c = bakeoff.candidates('ch_pip', chars['ch_pip'], d=db)
    assert c[0]['engine'] == 'piper' and c[0]['voice'] == '288' and c[0]['free']
    kok = [x for x in c if x['engine'] == 'kokoro']
    assert [x['voice'] for x in kok] == list(bakeoff.kokoro.PRESETS) and all(x['free'] for x in kok)
    assert not any(x['engine'] == 'speechify' for x in c), 'no Speechify voice id on the character'
    openai = [x for x in c if x['engine'] == 'openai']
    assert openai and openai[0]['free'] is False and openai[0]['available'] is False
    char2 = dict(chars['ch_pip'], voice=dict(chars['ch_pip']['voice'], speechify_voice_id='abc'))
    sp = [x for x in bakeoff.candidates('ch_pip', char2, d=db) if x['engine'] == 'speechify']
    assert sp and sp[0]['available'] is False and sp[0]['free'] is False
    vault.put('SPEECHIFY_API_KEY', 'k', db)
    sp = [x for x in bakeoff.candidates('ch_pip', char2, d=db) if x['engine'] == 'speechify']
    assert sp[0]['available'] is True


def test_run_candidate_measures_scores_and_stores(db, piper_mock):
    chars = repo.characters(active_only=False, d=db)
    char = chars['ch_tally']
    res = bakeoff.run_candidate('ch_tally', char, 'piper', '504', d=db)
    assert len(res['lines']) == 12 and len(piper_mock) == 12
    assert {ln['role'] for ln in res['lines']} == set(bakeoff.ROLES)
    assert all(ln['file'].startswith('work/character_previews/bakeoff/ch_tally/piper_504/') for ln in res['lines'])
    assert res['measured']['clips'] == 12 and res['measured']['words_per_s'] > 0
    assert res['score'] is None and res['free'] is True and res['license'] == 'CC BY 4.0'
    assert bakeoff.results('ch_tally', db)[0]['voice'] == '504'
    # A target profile gives a 0-100 score; rescoring without synthesis picks up a new target.
    pid = profiles.save('ref', 'test', dict(res['measured']), db)
    char['voice']['target_profile'] = pid
    repo.save_character('ch_tally', char['name'], char['bible'], char['voice'], True, db)
    out = bakeoff.rescore('ch_tally', char, db)
    assert out[0]['score'] == 100 and out[0]['target_profile'] == pid
    n = len(piper_mock)
    res2 = bakeoff.run_candidate('ch_tally', char, 'piper', '12', d=db)
    assert len(piper_mock) == n + 12 and isinstance(res2['score'], int) and 0 <= res2['score'] <= 100
    assert [r['voice'] for r in bakeoff.results('ch_tally', db)][0] == '504', 'best score first'


def test_paid_candidates_never_run_implicitly(db, piper_mock):
    chars = repo.characters(active_only=False, d=db)
    char = chars['ch_bloxy']
    with pytest.raises(Blocked) as e:
        bakeoff.run_candidate('ch_bloxy', char, 'openai', 'verse', d=db)
    assert e.value.state == 'needs_review'
    with pytest.raises(Blocked) as e:
        bakeoff.run_candidate('ch_bloxy', char, 'speechify', 'abc', paid_ok=True, d=db)
    assert e.value.state == 'needs_credentials'
    assert db.query('SELECT * FROM paid_calls') == [] and db.query('SELECT * FROM budget_ledger') == []
    assert piper_mock == []


def test_use_voice_writes_the_character_config(db):
    chars = repo.characters(active_only=False, d=db)
    v = bakeoff.use_voice('ch_dot', chars['ch_dot'], 'kokoro', 'af_heart', db)
    assert v['engine'] == 'kokoro' and v['kokoro_voice'] == 'af_heart'
    saved = repo.characters(active_only=False, d=db)['ch_dot']['voice']
    assert saved['engine'] == 'kokoro' and saved['kokoro_voice'] == 'af_heart' and saved['pitch_semitones'] == 4
    v = bakeoff.use_voice('ch_dot', repo.characters(active_only=False, d=db)['ch_dot'], 'piper', '77', db)
    assert v['engine'] == 'piper' and v['piper_speaker'] == 77 and v['kokoro_voice'] == 'af_heart'
    with pytest.raises(ValueError):
        bakeoff.voice_override({}, 'nope', 'x')


def test_voicecfg_validation():
    assert voicecfg.clean({}) == {}
    assert voicecfg.clean({'engine': '', 'kokoro_voice': '', 'kokoro_speed': '', 'target_profile': ''}) == {}
    out = voicecfg.clean({'engine': 'kokoro', 'kokoro_voice': 'bf_emma', 'kokoro_speed': '1.15',
                          'speechify_voice_id': 'v_1', 'target_profile': 'vp_abcd1234'})
    assert out == {'engine': 'kokoro', 'kokoro_voice': 'bf_emma', 'kokoro_speed': 1.15, 'speechify_voice_id': 'v_1',
                   'target_profile': 'vp_abcd1234'}
    for bad in ({'engine': 'robot'}, {'kokoro_voice': 'af_alloy'}, {'kokoro_voice': 'x'}, {'kokoro_speed': 3},
                {'kokoro_speed': 'fast'}, {'speechify_voice_id': 'a b'}, {'target_profile': 'nope'}):
        with pytest.raises(ValueError):
            voicecfg.clean(bad)
    assert voicecfg.engine_for({'engine': 'speechify'}, 'piper') == 'speechify'
    assert voicecfg.engine_for({}, 'piper') == 'piper'


def test_bakeoff_endpoints(client, db, piper_mock):
    b = client.get('/api/voice-bakeoff/ch_pip').get_json()
    assert b['character']['id'] == 'ch_pip' and len(b['script']) == 12 and b['results'] == []
    assert b['candidates'][0] == {**b['candidates'][0], 'engine': 'piper', 'voice': '288'}
    assert client.get('/api/voice-bakeoff/ch_nobody').status_code == 404
    assert client.post('/api/voice-bakeoff/ch_pip/run', json={'engine': 'piper', 'voice': '9000'}).status_code == 400
    assert client.post('/api/voice-bakeoff/ch_pip/run', json={'engine': 'openai', 'voice': 'sage'}).status_code != 200
    assert db.query('SELECT * FROM paid_calls') == []
    r = client.post('/api/voice-bakeoff/ch_pip/run', json={'engine': 'piper', 'voice': '288'})
    assert r.status_code == 200, r.get_json()
    res = r.get_json()['result']
    assert len(res['lines']) == 12 and client.get('/media/' + res['lines'][0]['file']).status_code == 200
    assert client.get('/api/voice-bakeoff/ch_pip').get_json()['results'][0]['voice'] == '288'
    assert client.post('/api/voice-bakeoff/ch_pip/use', json={'engine': 'piper', 'voice': '288'}).status_code == 200
    assert repo.characters(active_only=False, d=db)['ch_pip']['voice']['engine'] == 'piper'
    # The Characters form keeps the new keys through its own validation.
    body = {'name': 'Pip', 'active': True, 'bible': {'scale': 0.9, 'palette': {}, 'costume': {}},
            'voice': {'piper_speaker': 42, 'engine': 'kokoro', 'kokoro_voice': 'bm_lewis', 'kokoro_speed': 0.95,
                      'target_profile': ''}}
    assert client.put('/api/characters/ch_pip', json=body).status_code == 200
    v = repo.characters(active_only=False, d=db)['ch_pip']['voice']
    assert v['engine'] == 'kokoro' and v['kokoro_voice'] == 'bm_lewis' and v['kokoro_speed'] == 0.95
    assert 'target_profile' not in v
    body['voice']['kokoro_voice'] = 'af_alloy'
    assert client.put('/api/characters/ch_pip', json=body).status_code == 400
    assert client.get('/api/voice-engines/speechify/voices').status_code == 400, 'no key: nothing is called'
