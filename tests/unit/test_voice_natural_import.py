"""voice.json import (owner action): every value validated as untrusted input, no secrets, consent for a cloned
voice, written into a character's voice with its consent record, behind login and CSRF, audit-logged."""
import copy
import io
import json

import pytest

from blox import repo, store
from blox.voice import voicecfg

GOOD = {
    'engine': 'elevenlabs', 'voice_name': 'Deadpan narrator', 'voice_id': 'Ab12Cd34Ef56Gh78', 'route': 'library',
    'model': 'eleven_multilingual_v2',
    'settings': {'stability': 0.45, 'similarity_boost': 0.8, 'style': 0.15, 'use_speaker_boost': True, 'speed': 1.02},
    'section_speed': {'hook': 1.06, 'body': 1.0, 'end': 0.93},
    'pace': {'wps': {'min': 4.0, 'target': 4.19, 'max': 4.45}, 'first_word_s': 0.0, 'wps_sections': {'hook': [4.35, 4.75]}},
    'consent': {'route': 'library', 'who': 'Dhruv', 'statement': 'Library voice used under my plan.', 'date': '2026-10-19'},
    'approved_by_dhruv': '2026-10-19', 'applies_to': ['narrator'], 'calibrated_on': 'mac',
}


def parse(**over):
    doc = copy.deepcopy(GOOD)
    doc.update(over)
    return voicecfg.parse_voice_json(doc)


def test_a_good_file_maps_onto_the_character_voice():
    out = parse()
    assert out['voice'] == {'engine': 'elevenlabs', 'elevenlabs_voice_id': 'Ab12Cd34Ef56Gh78',
                            'elevenlabs_model': 'eleven_multilingual_v2', 'elevenlabs_settings': GOOD['settings'],
                            'elevenlabs_section_speed': GOOD['section_speed']}
    rec = out['record']
    assert rec['consent'] == GOOD['consent'] and rec['approved_by_dhruv'] == '2026-10-19'
    assert rec['route'] == 'library' and rec['voice_name'] == 'Deadpan narrator' and rec['pace']['wps']['target'] == 4.19
    assert out['ignored'] == ['calibrated_on'], 'unknown keys are ignored, not stored'
    assert 'calibrated_on' not in json.dumps(rec)
    # A free engine takes the voice id only; ElevenLabs-only values are checked but not used.
    piper = voicecfg.parse_voice_json({'engine': 'piper', 'voice_id': '408', 'settings': {'speed': 1.0}})
    assert piper['voice'] == {'engine': 'piper', 'piper_speaker': 408} and piper['notes']
    kokoro = voicecfg.parse_voice_json({'engine': 'kokoro', 'voice_id': 'af_heart'})
    assert kokoro['voice'] == {'engine': 'kokoro', 'kokoro_voice': 'af_heart'} and kokoro['record']['route'] == 'library'


def test_a_cloned_voice_needs_consent_who_and_date():
    with pytest.raises(ValueError, match='consent'):
        parse(route='clone', consent=None)
    with pytest.raises(ValueError, match='consent'):
        parse(route='clone', consent={'route': 'clone', 'who': 'Dhruv'})
    with pytest.raises(ValueError, match='consent'):
        parse(route='clone', consent={'route': 'clone', 'date': '2026-10-19'})
    with pytest.raises(ValueError, match='does not match'):
        parse(route='clone', consent={'route': 'library', 'who': 'Dhruv', 'date': '2026-10-19'})
    ok = parse(route='clone', consent={'route': 'clone', 'who': 'Dhruv (my own voice)', 'date': '2026-10-19',
                                       'statement': 'My own voice, recorded by me.'})
    assert ok['record']['route'] == 'clone' and ok['record']['consent']['who'] == 'Dhruv (my own voice)'
    with pytest.raises(ValueError, match='Free engines'):
        voicecfg.parse_voice_json({'engine': 'piper', 'voice_id': 1, 'route': 'clone',
                                   'consent': {'who': 'x', 'date': '2026-10-19'}})


@pytest.mark.parametrize('doc', [
    dict(GOOD, api_key='sk_live_123'),
    dict(GOOD, settings=dict(GOOD['settings'], xi_api_key='abc')),
    dict(GOOD, consent=dict(GOOD['consent'], token='abc')),
    dict(GOOD, ElevenLabsSecret='abc'),
    dict(GOOD, extras=[{'auth_token': 'x'}]),
])
def test_any_key_token_or_secret_field_is_refused(doc):
    with pytest.raises(ValueError) as e:
        voicecfg.parse_voice_json(doc)
    assert str(e.value) == voicecfg.SECRET_REFUSED and 'sk_live' not in str(e.value)


@pytest.mark.parametrize('over,needle', [
    ({'settings': dict(GOOD['settings'], stability=1.5)}, 'stability'),
    ({'settings': dict(GOOD['settings'], speed=1.3)}, 'speed'),
    ({'settings': dict(GOOD['settings'], speed=0.5)}, 'speed'),
    ({'settings': dict(GOOD['settings'], style='0.2')}, 'style'),
    ({'settings': dict(GOOD['settings'], similarity_boost=True)}, 'similarity_boost'),
    ({'settings': dict(GOOD['settings'], use_speaker_boost='yes')}, 'use_speaker_boost'),
    ({'section_speed': {'hook': 1.5}}, 'hook'),
    ({'section_speed': {'end': -1}}, 'end'),
    ({'section_speed': [1.0]}, 'section_speed'),
    ({'consent': dict(GOOD['consent'], statement='x' * 301)}, 'statement'),
    ({'consent': dict(GOOD['consent'], date='2026-02-30')}, 'date'),
    ({'consent': dict(GOOD['consent'], date='19/10/2026')}, 'date'),
    ({'approved_by_dhruv': 'yesterday'}, 'approved_by_dhruv'),
    ({'engine': 'openai'}, 'engine'),
    ({'engine': 'voice-clone-pro'}, 'engine'),
    ({'engine': None}, 'engine'),
    ({'route': 'borrowed'}, 'route'),
    ({'voice_id': 'short'}, 'voice_id'),
    ({'voice_id': '../../etc'}, 'voice_id'),
    ({'model': 'Eleven Turbo!'}, 'model'),
    ({'voice_name': 'n' * 81}, 'voice_name'),
    ({'pace': {'wps': 500}}, 'pace'),
    ({'pace': {'Bad Name': 1}}, 'pace'),
    ({'applies_to': ['a'] * 11}, 'applies_to'),
])
def test_out_of_range_or_malformed_values_are_refused(over, needle):
    with pytest.raises(ValueError) as e:
        parse(**over)
    assert needle in str(e.value)


def test_file_level_limits():
    assert voicecfg.load_voice_json(json.dumps(GOOD).encode()) == GOOD
    assert voicecfg.load_voice_json(b'\xef\xbb\xbf' + json.dumps(GOOD).encode()) == GOOD, 'a BOM is fine'
    for raw in (b'{"engine": NaN}', b'{"settings": {"speed": Infinity}}', b'not json', b'\xff\xfe',
                b' ' * (voicecfg.VOICE_JSON_MAX_BYTES + 1), b'[' * 5000 + b']' * 5000):
        with pytest.raises(ValueError):
            doc = voicecfg.load_voice_json(raw)
            voicecfg.parse_voice_json(doc)
    with pytest.raises(ValueError):
        voicecfg.parse_voice_json([GOOD])
    deep = {'engine': 'elevenlabs'}
    node = deep
    for _ in range(10):
        node['n'] = {}
        node = node['n']
    with pytest.raises(ValueError):
        voicecfg.parse_voice_json(deep)


def test_character_voice_config_validates_the_elevenlabs_keys():
    ok = voicecfg.clean({'elevenlabs_model': 'eleven_turbo_v2_5', 'elevenlabs_settings': {'stability': 0.3, 'junk': 1},
                         'elevenlabs_section_speed': {'hook': 1.1, 'other': 3}})
    assert ok == {'elevenlabs_model': 'eleven_turbo_v2_5', 'elevenlabs_settings': {'stability': 0.3},
                  'elevenlabs_section_speed': {'hook': 1.1}}
    for bad in ({'elevenlabs_model': 'x'}, {'elevenlabs_model': 'UPPER_case'}, {'elevenlabs_settings': {'speed': 2}},
                {'elevenlabs_settings': 'fast'}, {'elevenlabs_section_speed': {'body': 0.1}}):
        with pytest.raises(ValueError):
            voicecfg.clean(bad)
    assert voicecfg.clean({'elevenlabs_settings': {}, 'elevenlabs_section_speed': None}) == {}


# ----------------------------------------------------------------------------- the endpoint
def upload(client, path, doc, character='ch_bloxy', raw=None):
    data = {'character': character, 'file': (io.BytesIO(raw if raw is not None else json.dumps(doc).encode()),
                                             'voice.json')}
    return client.post(path, data=data, content_type='multipart/form-data')


def voice(db, cid='ch_bloxy'):
    return repo.characters(active_only=False, d=db)[cid]['voice']


def test_preview_changes_nothing_then_apply_writes_voice_and_record(client, db):
    before = copy.deepcopy(voice(db))
    r = upload(client, '/api/voice-json/preview', GOOD)
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j['character']['id'] == 'ch_bloxy' and j['record']['consent']['who'] == 'Dhruv'
    fields = {c['field']: c for c in j['changes']}
    assert fields['engine']['to'] == 'elevenlabs' and fields['elevenlabs_voice_id']['to'] == 'Ab12Cd34Ef56Gh78'
    assert fields['elevenlabs_section_speed']['to'] == GOOD['section_speed']
    assert voice(db) == before, 'a preview saves nothing'
    r = upload(client, '/api/voice-json/apply', GOOD)
    assert r.status_code == 200, r.get_json()
    v = voice(db)
    assert v['engine'] == 'elevenlabs' and v['elevenlabs_voice_id'] == 'Ab12Cd34Ef56Gh78'
    assert v['elevenlabs_model'] == 'eleven_multilingual_v2' and v['elevenlabs_settings'] == GOOD['settings']
    assert v['elevenlabs_section_speed'] == GOOD['section_speed']
    assert v['piper_speaker'] == before['piper_speaker'], 'the rest of the voice is kept'
    rec = v['voice_import']
    assert rec['consent'] == GOOD['consent'] and rec['approved_by_dhruv'] == '2026-10-19' and rec['imported_at']
    audit = [a for a in store.recent_audit(20, db) if a['action'] == 'voice_json_imported']
    assert audit and audit[0]['detail']['voice_id'] == 'Ab12Cd34Ef56Gh78'
    assert 'statement' not in json.dumps(audit[0]['detail'])


def test_json_body_works_and_a_new_file_replaces_old_settings(client, db):
    assert upload(client, '/api/voice-json/apply', GOOD).status_code == 200
    lean = {'engine': 'elevenlabs', 'voice_id': 'Zz99Yy88Xx77', 'route': 'library'}
    r = client.post('/api/voice-json/apply', json={'character': 'ch_bloxy', 'voice_json': lean})
    assert r.status_code == 200, r.get_json()
    v = voice(db)
    assert v['elevenlabs_voice_id'] == 'Zz99Yy88Xx77' and v['voice_import']['voice_id'] == 'Zz99Yy88Xx77'
    assert not any(k in v for k in ('elevenlabs_model', 'elevenlabs_settings', 'elevenlabs_section_speed')), \
        'settings of the previous voice do not carry over to another voice'


def test_bad_files_are_refused_with_a_plain_reason_and_nothing_is_saved(client, db):
    before = copy.deepcopy(voice(db))
    cases = [(dict(GOOD, api_key='sk_live_123'), None, 'secret'),
             (dict(GOOD, route='clone', consent={}), None, 'consent'),
             (dict(GOOD, settings={'stability': 9}), None, 'stability'),
             (None, b'{"engine": NaN}', 'not valid JSON'),
             (None, b'x' * (voicecfg.VOICE_JSON_MAX_BYTES + 10), 'KB')]
    for doc, raw, needle in cases:
        r = upload(client, '/api/voice-json/apply', doc, raw=raw)
        assert r.status_code == 400 and needle in r.get_json()['error'], (needle, r.get_json())
        assert 'sk_live' not in r.get_json()['error'] and 'Traceback' not in r.get_json()['error']
    assert upload(client, '/api/voice-json/apply', GOOD, character='ch_nobody').status_code == 404
    assert upload(client, '/api/voice-json/apply', GOOD, character='../etc').status_code == 400
    assert client.post('/api/voice-json/apply', json=['x']).status_code == 400
    assert voice(db) == before


def test_the_endpoint_needs_login_and_csrf(client, db):
    from blox.web.app import create_app
    anon = create_app().test_client()
    for path in ('/api/voice-json/preview', '/api/voice-json/apply'):
        assert upload(anon, path, GOOD).status_code == 401
    no_csrf = create_app().test_client()
    with no_csrf.session_transaction() as s:
        s['owner'] = True
        s['csrf'] = 'right'
        s['exp'] = 9e12
    no_csrf.environ_base['HTTP_X_CSRF'] = 'wrong'
    assert upload(no_csrf, '/api/voice-json/apply', GOOD).status_code == 403
    assert 'voice_import' not in voice(db)


def test_a_characters_page_save_keeps_the_record_while_the_voice_is_the_same(client, db):
    assert upload(client, '/api/voice-json/apply', GOOD).status_code == 200
    ch = client.get('/api/characters').get_json()['characters']['ch_bloxy']
    v = ch['voice']
    # What the page sends back (VoiceBase.fields returns the imported ElevenLabs keys unchanged).
    sent = {k: v[k] for k in ('piper_speaker', 'openai_voice', 'elevenlabs_voice_id', 'engine', 'elevenlabs_model',
                              'elevenlabs_settings', 'elevenlabs_section_speed')}
    sent['voice_import'] = {'engine': 'elevenlabs', 'voice_id': 'Ab12Cd34Ef56Gh78', 'consent': {'who': 'forged'}}
    body = {'name': ch['name'], 'active': True, 'bible': ch['bible'], 'voice': sent}
    assert client.put('/api/characters/ch_bloxy', json=body).status_code == 200
    saved = voice(db)
    assert saved['voice_import'] == v['voice_import'], 'the record is import-only: a save cannot rewrite it'
    assert saved['elevenlabs_settings'] == GOOD['settings']
    body['voice']['elevenlabs_voice_id'] = 'Other12345678'
    assert client.put('/api/characters/ch_bloxy', json=body).status_code == 200
    assert 'voice_import' not in voice(db), 'a record for another voice is dropped'
