"""ElevenLabs from the owner's voice.json (mocked only): imported settings and speed by story section replace the
emotion defaults, neighbouring lines go along as previous_text / next_text, all of it is in the cache key, and a
credit check stops for the owner before a video would use over a quarter of the characters left."""
import base64
import copy
import io
import wave

import numpy as np
import pytest

from blox import paid, prefs, production, vault
from blox.util import Blocked, Retry
from blox.voice import sections, tts

from ..conftest import have

TTS = 'https://api.elevenlabs.io/v1/text-to-speech/'
SUB = tts.ELEVENLABS_SUBSCRIPTION_URL
VOICE_ID = 'Ab12Cd34Ef56Gh78'
IMPORTED = {'engine': 'elevenlabs', 'elevenlabs_voice_id': VOICE_ID, 'elevenlabs_model': 'eleven_turbo_v2_5',
            'elevenlabs_settings': {'stability': 0.42, 'similarity_boost': 0.8, 'style': 0.2,
                                    'use_speaker_boost': False, 'speed': 1.05},
            'elevenlabs_section_speed': {'hook': 1.06, 'body': 1.0, 'end': 0.93}}
ME = {'id': 'ch_me', 'name': 'Me', 'voice': IMPORTED}
FRIEND = {'id': 'ch_pal', 'name': 'Pal', 'voice': {'engine': 'piper'}}


def line(i, speaker, start, text):
    return {'id': f'l{i}', 'speaker': speaker, 'start_frame': start, 'text': text, 'emotion': 'neutral',
            'pace': 'normal', 'volume': 'normal', 'speech_rate': 1.3, 'kind': 'narration'}


def manifest():
    return {'fps': 30, 'beats': [{'start_frame': 0, 'end_frame': 66, 'purpose': 'hook'},
                                 {'start_frame': 66, 'end_frame': 300, 'purpose': 'escalation'},
                                 {'start_frame': 300, 'end_frame': 400, 'purpose': 'loop'}],
            'lines': [line(1, 'me', 0, 'I sued the moon.'), line(2, 'me', 70, 'It sued me back.'),
                      line(3, 'pal', 100, 'Bro, why?'), line(4, 'me', 150, 'The judge was a tide.'),
                      line(5, 'me', 320, 'I lost the ocean.')]}


CAST = {'me': ME, 'pal': FRIEND}


def wav_b64(seconds=0.8, sr=24000):
    t = np.arange(int(seconds * sr)) / sr
    x = (0.3 * np.sin(2 * np.pi * 170 * t) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def eleven(db, fake_http):
    """ElevenLabs key saved; TTS answers with audio and character timings; the subscription has credits."""
    vault.put('ELEVENLABS_API_KEY', 'xi-test', db)
    sent = []

    def speak(url, **kw):
        sent.append({'url': url, 'json': kw.get('json'), 'headers': kw.get('headers')})
        text = kw['json']['text']
        n = len(text)
        return 200, {'audio_base64': wav_b64(), 'alignment': {
            'characters': list(text), 'character_start_times_seconds': [0.6 * i / n for i in range(n)],
            'character_end_times_seconds': [0.6 * (i + 1) / n for i in range(n)]}}, {}
    fake_http.on('POST', TTS, speak)
    fake_http.on('GET', SUB, json_body={'tier': 'creator', 'character_count': 1000, 'character_limit': 100000})
    fake_http.sent = sent
    return fake_http


def test_section_of_a_line_follows_its_beat():
    m = manifest()
    assert [sections.line_section(m, ln) for ln in m['lines']] == ['hook', 'body', 'body', 'body', 'end']
    assert sections.line_section({'beats': []}, m['lines'][0]) == 'body'


def test_imported_settings_and_section_speed_replace_the_emotion_defaults():
    v = IMPORTED
    lively = dict(line(1, 'me', 0, 'x'), emotion='excited')
    s = tts.elevenlabs_voice_settings(lively, v, 'hook')
    assert s == {'stability': 0.42, 'similarity_boost': 0.8, 'style': 0.2, 'use_speaker_boost': False,
                 'speed': round(1.05 * 1.06, 3)}
    assert tts.elevenlabs_voice_settings(lively, v, 'end')['speed'] == round(1.05 * 0.93, 3)
    assert tts.elevenlabs_voice_settings(dict(lively, fit_speed=1.1), v, 'hook')['speed'] == 1.2, 'clamped'
    low = dict(v, elevenlabs_settings={'speed': 0.7}, elevenlabs_section_speed={'end': 0.9})
    assert tts.elevenlabs_speed(lively, low, 'end') == 0.7, 'clamped at the bottom too'
    # Characters without imported settings keep the emotion defaults and the speech rate.
    legacy = tts.elevenlabs_voice_settings(lively, {'elevenlabs_voice_id': VOICE_ID}, 'hook')
    assert legacy == {'stability': 0.35, 'similarity_boost': 0.75, 'style': 0.45, 'use_speaker_boost': True,
                      'speed': 1.2}
    # Stored values that are out of range count as absent.
    assert tts.elevenlabs_voice_settings(lively, dict(v, elevenlabs_settings={'stability': 7}), 'hook') == legacy


def test_request_carries_settings_section_speed_and_neighbouring_lines(eleven, db, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    m = manifest()
    by_id = {ln['id']: ln for ln in m['lines']}
    res = tts.synthesize_line(by_id['l4'], ME, p, str(tmp_path / 'v'), 'vid1', manifest=m)
    body = eleven.sent[-1]['json']
    assert eleven.sent[-1]['url'].startswith(TTS + VOICE_ID + '/with-timestamps')
    assert body['model_id'] == 'eleven_turbo_v2_5'
    assert body['voice_settings'] == {'stability': 0.42, 'similarity_boost': 0.8, 'style': 0.2,
                                      'use_speaker_boost': False, 'speed': 1.05}
    assert body['previous_text'] == 'I sued the moon. It sued me back.', 'the same speaker, two lines before'
    assert body['next_text'] == 'I lost the ocean.'
    assert res['alignment_kind'] == 'measured_provider' and res['provider'] == 'elevenlabs'
    tts.synthesize_line(by_id['l1'], ME, p, str(tmp_path / 'v'), 'vid1', manifest=m)
    hook = eleven.sent[-1]['json']
    assert hook['voice_settings']['speed'] == round(1.05 * 1.06, 3) and 'previous_text' not in hook
    assert hook['next_text'] == 'It sued me back.'
    tts.synthesize_line(by_id['l5'], ME, p, str(tmp_path / 'v'), 'vid1', manifest=m)
    end = eleven.sent[-1]['json']
    assert end['voice_settings']['speed'] == round(1.05 * 0.93, 3) and 'next_text' not in end
    assert end['previous_text'] == 'It sued me back. The judge was a tide.'
    rows = db.query('SELECT * FROM paid_calls')
    assert len(rows) == 3 and all(r['status'] == 'completed' for r in rows), 'every request through the ledger'


def test_spec_hash_changes_with_settings_section_speed_and_context():
    pr = copy.deepcopy(prefs.DEFAULTS['production'])
    m = manifest()
    l4 = m['lines'][3]

    def h(voice, mm=m, ln=l4):
        return tts.line_spec(ln, dict(ME, voice=voice), pr, manifest=mm)['hash']
    base = h(IMPORTED)
    assert h(dict(IMPORTED, elevenlabs_settings=dict(IMPORTED['elevenlabs_settings'], stability=0.5))) != base
    assert h(dict(IMPORTED, elevenlabs_settings=dict(IMPORTED['elevenlabs_settings'], speed=1.1))) != base
    assert h(dict(IMPORTED, elevenlabs_model='eleven_multilingual_v2')) != base
    hook = m['lines'][0]
    assert h(dict(IMPORTED, elevenlabs_section_speed={'hook': 1.1}), ln=hook) != h(IMPORTED, ln=hook)
    other = manifest()
    other['lines'][1]['text'] = 'It countersued.'
    assert h(IMPORTED, mm=other) != base, 'the previous_text is part of the key'
    assert tts.line_spec(dict(l4, fit_speed=1.1), ME, pr, manifest=m)['hash'] != base
    assert h(dict(IMPORTED, voice_import={'imported_at': '2026-10-22T00:00:00Z'})) == base, \
        'importing the same voice again does not re-voice (and re-bill) its lines'
    # A plain ElevenLabs voice keeps the cache key (and request) it always had, with or without a manifest: only a
    # voice from voice.json gets its neighbouring lines, so older projects are not voiced and billed again.
    plain = {'id': 'ch_x', 'voice': {'engine': 'elevenlabs', 'elevenlabs_voice_id': VOICE_ID}}
    legacy = tts.line_spec(l4, plain, pr)
    assert 'elevenlabs' not in legacy['spec']
    with_m = tts.line_spec(l4, plain, pr, manifest=m)
    assert with_m['hash'] == legacy['hash'] and with_m['elevenlabs']['previous_text'] == ''
    # A lean voice.json import (no settings) still counts as the owner's voice: it gets the context.
    lean = dict(plain['voice'], voice_import={'engine': 'elevenlabs', 'voice_id': VOICE_ID})
    assert tts.line_spec(l4, dict(plain, voice=lean), pr, manifest=m)['elevenlabs']['previous_text']


def test_a_plain_elevenlabs_voice_sends_no_neighbouring_lines(eleven, db, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    m = manifest()
    plain = {'id': 'ch_x', 'name': 'X', 'voice': {'engine': 'elevenlabs', 'elevenlabs_voice_id': VOICE_ID}}
    tts.synthesize_line(m['lines'][3], plain, p, str(tmp_path / 'v'), 'vid1', manifest=m)
    body = eleven.sent[-1]['json']
    assert 'previous_text' not in body and 'next_text' not in body


def test_voice_lines_passes_the_manifest_and_other_engines_ignore_it(eleven, db, tmp_path, monkeypatch):
    seen = []
    real = tts.synthesize_line

    def spy(ln, who, p, wd, vid, attempt=0, use_asr=None, manifest=None):
        seen.append((ln['id'], manifest is not None))
        if who['voice'].get('engine') == 'piper':
            return {'file': '', 'duration_s': 1.0, 'words': []}
        return real(ln, who, p, wd, vid, attempt=attempt, use_asr=use_asr, manifest=manifest)
    monkeypatch.setattr(production.tts, 'synthesize_line', spy)
    p = copy.deepcopy(prefs.get(db))
    out = production.voice_lines(manifest(), CAST, p, str(tmp_path / 'v'), 'vid1')
    assert sorted(out) == ['l1', 'l2', 'l3', 'l4', 'l5'] and all(has for _, has in seen)
    assert eleven.count('GET', SUB) == 1, 'one credit check before the first paid line'
    assert eleven.count('POST', TTS) == 4, 'the four ElevenLabs lines'


# ----------------------------------------------------------------------------- credit guard
def test_credit_guard_blocks_above_a_quarter_of_what_is_left(eleven, db, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    m = manifest()
    need = production.elevenlabs_characters(m, CAST, p)
    assert need == sum(len(ln['text']) for ln in m['lines'] if ln['speaker'] == 'me')
    left = need * 4 - 1
    eleven.on('GET', SUB, json_body={'character_count': 50000 - left, 'character_limit': 50000})
    with pytest.raises(Blocked) as e:
        production.voice_lines(m, CAST, p, str(tmp_path / 'v'), 'vid1')
    assert e.value.state == 'needs_review'
    msg = str(e.value)
    assert f'{need:,}' in msg and f'{left:,}' in msg and 'quarter' in msg
    assert eleven.count('POST', TTS) == 0, 'stopped before any paid line'
    assert db.query('SELECT * FROM paid_calls') == [] and db.query('SELECT * FROM budget_ledger') == []
    # Exactly a quarter is fine.
    eleven.on('GET', SUB, json_body={'character_count': 50000 - need * 4, 'character_limit': 50000})
    assert production.credit_guard(m, CAST, p) == need
    assert tts.check_elevenlabs_credits(need)['remaining'] == need * 4


def test_credit_guard_passes_below_and_is_skipped_for_free_voices(eleven, db, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    assert production.credit_guard(manifest(), CAST, p) > 0 and eleven.count('GET', SUB) == 1
    free = {'me': FRIEND, 'pal': FRIEND}
    assert production.credit_guard(manifest(), free, p) == 0
    assert production.credit_guard(manifest(), CAST, p, only=['l3']) == 0, 'only lines still to voice count'
    assert eleven.count('GET', SUB) == 1, 'no check when no ElevenLabs line is voiced'
    calls = [c['url'] for c in eleven.calls]
    assert all(u.startswith(SUB) or u.startswith(TTS) for u in calls)
    assert not any(w in u for u in calls for w in ('/voices/add', 'voice-generation', '/ivc', '/pvc', 'clone'))


def test_credit_report_is_untrusted_and_failures_never_voice(db, fake_http):
    vault.put('ELEVENLABS_API_KEY', 'xi-test', db)
    for bad in ({'character_count': 'lots', 'character_limit': 10}, {'character_limit': 10}, [1, 2],
                {'character_count': True, 'character_limit': 10}, {'character_count': -5, 'character_limit': 10}):
        fake_http.on('GET', SUB, json_body=bad)
        with pytest.raises(Blocked) as e:
            tts.check_elevenlabs_credits(10)
        assert e.value.state == 'needs_review' and 'could not read' in str(e.value)
    fake_http.on('GET', SUB, status=401, json_body={'detail': {'status': 'invalid_api_key', 'message': 'no'}})
    with pytest.raises(Blocked) as e:
        tts.check_elevenlabs_credits(10)
    assert e.value.state == 'needs_credentials' and 'xi-test' not in str(e.value)
    fake_http.on('GET', SUB, status=503, json_body={})
    with pytest.raises(Retry):
        tts.check_elevenlabs_credits(10)
    assert fake_http.count('POST', TTS) == 0 and paid.BILLING_CODES


def test_no_key_means_no_request(db, fake_http):
    with pytest.raises(Blocked) as e:
        production.credit_guard(manifest(), CAST, copy.deepcopy(prefs.get(db)))
    assert e.value.state == 'needs_credentials' and fake_http.calls == []


def test_a_key_without_account_read_access_is_explained(db, fake_http):
    vault.put('ELEVENLABS_API_KEY', 'xi-test', db)
    fake_http.on('GET', SUB, status=401, json_body={'detail': {
        'status': 'missing_permissions',
        'message': 'The API key you used is missing the permission user_read to execute this operation.'}})
    with pytest.raises(Blocked) as e:
        tts.check_elevenlabs_credits(10)
    msg = str(e.value)
    assert e.value.state == 'needs_credentials' and 'not allowed to read your account' in msg and '"User"' in msg
    assert 'did not accept' not in msg and 'xi-test' not in msg
    assert fake_http.count('POST', TTS) == 0


# ----------------------------------------------------------------------------- re-voices inside the quarter
def long_lines():
    """Four lines of the imported voice, each slightly longer than its slot (the fake take lasts 0.8 s, a slot
    0.733 s), at production pace 1.5 so line fitting may speed them up natively."""
    texts = ['I sued the moon.', 'It sued me back.', 'The judge was a tide.', 'I lost the ocean.']
    lines = [dict(line(i + 1, 'me', 22 * i, t), pause_after_ms=0) for i, t in enumerate(texts)]
    return {'fps': 30, 'duration_frames': 88, 'pace': {'timeline': 1.5, 'speech_rate': 1.3},
            'beats': [{'start_frame': 0, 'end_frame': 88, 'purpose': 'escalation'}], 'lines': lines}


def sent_chars(eleven):
    return sum(len(c['json']['text']) for c in eleven.sent)


@pytest.mark.skipif(not have('ffmpeg'), reason='needs FFmpeg')
@pytest.mark.parametrize('quarters', [4, 6, 40])
def test_re_voices_never_take_a_video_past_a_quarter_of_what_was_left(eleven, db, tmp_path, quarters):
    """The reviewer's case: first takes at exactly 25% of what is left, then lines slightly over their slot. The
    re-voices to fit count against the same quarter: nothing goes over it, whatever the headroom."""
    p = copy.deepcopy(prefs.get(db))
    m = long_lines()
    cast = {'me': ME}
    need = production.elevenlabs_characters(m, cast, p)
    left = need * quarters
    eleven.on('GET', SUB, json_body={'character_count': 100000 - left, 'character_limit': 100000})
    budget = production.CreditBudget()
    wd = str(tmp_path / 'v')
    results = production.voice_lines(m, cast, p, wd, 'vid1', budget=budget)
    _, _, report = production.retime(m, results, p, max_shift_s=0.0,
                                     revoice=production.revoicer(m, cast, p, wd, 'vid1', budget=budget))
    assert sent_chars(eleven) <= 0.25 * left, (sent_chars(eleven), left, report)
    assert tts.elevenlabs_sent('vid1', db) == sent_chars(eleven), 'the ledger counts the same characters'
    tried = [r for r in report if r['action'] in ('native_speed', 'native_speed_skipped')]
    assert len(tried) == 4, report
    skipped = [r for r in report if r['action'] == 'native_speed_skipped']
    if quarters == 4:
        assert eleven.count('POST', TTS) == 4 and len(skipped) == 4, 'no room left: no re-voice is sent'
        assert all(r['reason'] == 'credits' for r in skipped)
    elif quarters == 6:
        assert 0 < len(skipped) < 4, 'some re-voices fit in the quarter, the rest are skipped'
    else:
        assert not skipped and eleven.count('POST', TTS) == 8, 'plenty left: every line is re-voiced'
    assert eleven.count('GET', SUB) == 1, 'one credit check covers the first takes and the re-voices'


@pytest.mark.skipif(not have('ffmpeg'), reason='needs FFmpeg')
def test_a_re_voice_checks_the_credits_when_the_run_had_none_to_check(eleven, db, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    m = long_lines()
    eleven.on('GET', SUB, json_body={'character_count': 99990, 'character_limit': 100000})  # 10 left
    rv = production.revoicer(m, {'me': ME}, p, str(tmp_path / 'v'), 'vid1')
    assert rv(m['lines'][0], 1.08) == {'skipped': 'credits'}
    assert eleven.count('GET', SUB) == 1 and eleven.count('POST', TTS) == 0


@pytest.mark.skipif(not have('ffmpeg'), reason='needs FFmpeg')
def test_takes_already_paid_for_are_not_counted_again(eleven, db, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    m = manifest()
    production.voice_lines(m, CAST, p, str(tmp_path / 'v'), 'vid1')
    assert production.elevenlabs_characters(m, CAST, p, video_id='vid1') == 0, 'reused from the ledger, not billed'
    assert production.elevenlabs_characters(m, CAST, p, video_id='vid2') > 0
    assert production.elevenlabs_characters(m, CAST, p, video_id='vid1', attempts={'l1': 1}) == \
        len(m['lines'][0]['text']), 'a repair take is a new request'
    n = eleven.count('GET', SUB)
    production.voice_lines(m, CAST, p, str(tmp_path / 'v'), 'vid1')
    assert eleven.count('GET', SUB) == n, 'nothing to bill, so nothing to check'


# ----------------------------------------------------------------------------- the pipeline, and the owner's OK
@pytest.mark.skipif(not have('ffmpeg'), reason='needs FFmpeg')
def test_the_voice_task_stops_then_goes_on_once_with_the_owners_ok(eleven, db, client, tmp_path):
    import json

    from blox import pipeline, repo, store, videos
    from blox.demo import plan
    from blox.manifest import compile as C
    m = C.compile_plan(plan(), fps=30, width=540, height=960, pace=1.5, speech_rate=1.3)
    m['lines'] = m['lines'][:3]
    for cid in ('ch_bloxy', 'ch_pip'):
        ch = repo.characters(active_only=False, d=db)[cid]
        repo.save_character(cid, ch['name'], ch['bible'], dict(ch['voice'], **IMPORTED), True, db)
    vid = videos.create('Credit stop', 'autopilot', status='storyboarded', d=db)
    mid = repo.save_manifest(vid, m, 'test', '', {}, db)
    db.execute('UPDATE videos SET manifest_id=? WHERE id=?', (mid, vid))
    p = prefs.get(db)
    need = production.elevenlabs_characters(m, pipeline._cast(m, db), p)
    left = need * 3  # the video would use a third of what is left
    eleven.on('GET', SUB, json_body={'character_count': 100000 - left, 'character_limit': 100000})

    class Ctx:
        payload = {'manifest_id': mid}
        video_id = vid

        def beat(self, force=False):
            pass
    with pytest.raises(tts.CreditStop) as e:
        pipeline.voice(Ctx())
    assert e.value.state == 'needs_review' and tts.CREDIT_APPROVE_BUTTON in str(e.value)
    assert eleven.count('POST', TTS) == 0
    stop = videos.get(vid, db)['metadata']['credit_stop']
    assert stop['chars'] == need and stop['left'] == left
    videos.hold(vid, 'needs_review', str(e.value))  # what the worker does with a stop
    # The owner's button: only for a video the credit check stopped.
    other = videos.create('No stop', 'autopilot', status='needs_review', d=db)
    assert client.post(f'/api/videos/{other}/approve_credits', json={}).status_code == 400
    r = client.post(f'/api/videos/{vid}/approve_credits', json={})
    assert r.status_code == 200, r.get_json()
    v = videos.get(vid, db)
    assert v['status'] == 'storyboarded' and v['metadata']['credit_approval']['chars'] == need
    assert db.query("SELECT kind FROM tasks WHERE video_id=? AND kind='video.voice'", (vid,))
    audit = [a for a in store.recent_audit(20, db) if a['action'] == 'elevenlabs_credits_approved']
    assert audit and audit[0]['detail'] == {'video': vid, 'chars': need, 'left': left}
    out = pipeline.voice(Ctx())
    assert eleven.count('POST', TTS) >= 3 and out
    meta = videos.get(vid, db)['metadata']
    assert meta.get('credit_approval') is None and meta.get('credit_stop') is None, 'the OK is used up'
    # Once only: voicing a line again later (the owner's "re-voice line") is checked against what the video has
    # already used, so the video as a whole stays inside the quarter.
    assert client.post(f'/api/videos/{vid}/revoice_line', json={'line': m['lines'][0]['id']}).status_code == 200
    before = eleven.count('POST', TTS)
    with pytest.raises(tts.CreditStop) as e:
        pipeline.voice(Ctx())
    assert e.value.already == sent_chars(eleven) and 'has used about' in str(e.value)
    assert eleven.count('POST', TTS) == before
    assert json.loads(json.dumps(videos.get(vid, db)['metadata']['credit_stop']))['already'] == e.value.already


@pytest.mark.skipif(not have('ffmpeg'), reason='needs FFmpeg')
def test_native_re_voice_raises_the_elevenlabs_speed(eleven, db, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    m = manifest()
    l4 = m['lines'][3]
    rv = production.revoicer(m, CAST, p, str(tmp_path / 'v'), 'vid1')
    res = rv(l4, 1.1)
    assert res['native_speed'] == 1.1
    assert eleven.sent[-1]['json']['voice_settings']['speed'] == round(1.05 * 1.1, 3)
    # Already at ElevenLabs' fastest: no paid request is made for nothing.
    fast = {'me': dict(ME, voice=dict(IMPORTED, elevenlabs_settings={'speed': 1.2})), 'pal': FRIEND}
    n = eleven.count('POST', TTS)
    assert production.revoicer(m, fast, p, str(tmp_path / 'v'), 'vid1')(l4, 1.1) is None
    assert eleven.count('POST', TTS) == n


def test_validation_time_measurement_never_voices_a_paid_character(monkeypatch, tmp_path):
    from blox import demo
    from blox.manifest import compile as C
    seen = []

    def synth(ln, who, p, wd, vid, attempt=0, use_asr=None, manifest=None):
        seen.append(ln['speaker'])
        return {'file': str(tmp_path / 'x.wav'), 'raw': str(tmp_path / 'x.raw'), 'duration_s': 1.0}
    monkeypatch.setattr(production.tts, 'synthesize_line', synth)
    m = C.compile_plan(demo.plan(), fps=30, width=540, height=960)
    rows = {'ch_bloxy': {'name': 'Bloxy', 'voice': IMPORTED}, 'ch_pip': {'name': 'Pip', 'voice': {}}}
    ids = production.measure_lines(m, rows, copy.deepcopy(prefs.DEFAULTS), work_dir=str(tmp_path / 'measure'))
    assert seen and set(seen) == {'pip'}, 'only the free voice was measured'
    assert sorted(ids) == sorted(ln['id'] for ln in m['lines'] if ln['speaker'] == 'pip')
