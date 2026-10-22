"""Speechify provider (mocked only): default off, never called without the key, ledger + budget + billing
limits like OpenAI TTS, word speech marks -> measured caption timing."""
import base64
import copy
import io
import wave

import numpy as np
import pytest

from blox import budget, paid, prefs, vault
from blox.util import Blocked
from blox.voice import speechify, tts

URL = speechify.BASE + '/v1/audio/speech'


def wav_b64(seconds=1.0, sr=24000):
    t = np.arange(int(seconds * sr)) / sr
    x = (0.3 * np.sin(2 * np.pi * 170 * t) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())
    return base64.b64encode(buf.getvalue()).decode()


def marks(words, start_ms=0):
    chunks, t = [], start_ms
    for w in words:
        chunks.append({'type': 'word', 'value': w, 'start_time': t, 'end_time': t + 200, 'start': 0, 'end': len(w)})
        t += 250
    return {'type': 'sentence', 'value': ' '.join(words), 'start_time': start_ms, 'end_time': t, 'chunks': chunks}


LINE = {'id': 'l1', 'text': 'Jump now! The lava does not wait.', 'emotion': 'excited', 'pace': 'fast', 'volume': 'shout',
        'speech_rate': 1.3}
CHAR = {'id': 'ch_bloxy', 'name': 'Bloxy', 'voice': {'engine': 'speechify', 'speechify_voice_id': 'voice-abc_1'}}


def test_default_off_and_registered():
    p = prefs.defaults() if hasattr(prefs, 'defaults') else copy.deepcopy(prefs.DEFAULTS)
    assert p['production']['tts_provider'] == 'piper'
    assert 'speechify' in prefs.TTS_PROVIDERS and 'kokoro' in prefs.TTS_PROVIDERS
    assert 'SPEECHIFY_API_KEY' in vault.KNOWN
    assert p['budget']['prices']['speechify_per_kchar'] > 0
    assert tts.estimate_cost('x' * 2000, 'speechify', p['budget']['prices']) == pytest.approx(2 * p['budget']['prices']['speechify_per_kchar'])


def test_ssml_carries_emotion_volume_and_rate():
    s = speechify.ssml('Fish & chips <now>', LINE)
    assert s.startswith('<speak>') and s.endswith('</speak>')
    assert 'emotion="energetic"' in s and 'volume="x-loud"' in s and 'rate="+30%"' in s
    assert 'Fish &amp; chips &lt;now&gt;' in s, 'text is escaped, never injected as markup'
    plain = speechify.ssml('Hi.', {'emotion': 'neutral', 'pace': 'normal', 'volume': 'normal'})
    assert plain == '<speak>Hi.</speak>'


def test_words_from_marks_flattens_and_converts_ms():
    ws = speechify.words_from_marks(marks(['Jump', 'now!', 'The'], start_ms=120))
    assert [w['word'] for w in ws] == ['Jump', 'now', 'The']
    assert ws[0] == {'word': 'Jump', 'start': 0.12, 'end': 0.32}
    assert speechify.words_from_marks({}) == []


def test_never_called_without_key(db, tmp_path):
    p = copy.deepcopy(prefs.get(db))
    with pytest.raises(Blocked) as e:
        tts.synthesize_line(LINE, CHAR, p, str(tmp_path / 'v'), 'vid1')
    assert e.value.state == 'needs_credentials'
    assert db.query('SELECT * FROM paid_calls') == [], 'nothing recorded in the ledger'
    assert db.query('SELECT * FROM budget_ledger') == [], 'nothing reserved'
    with pytest.raises(Blocked):
        speechify.list_voices()


def test_paid_call_goes_through_ledger_and_returns_measured_words(db, fake_http, tmp_path):
    vault.put('SPEECHIFY_API_KEY', 'sk-test', db)
    p = copy.deepcopy(prefs.get(db))
    seen = {}

    def handler(url, **kw):
        seen['headers'] = kw.get('headers')
        seen['json'] = kw.get('json')
        words = ['Jump', 'now', 'The', 'lava', 'does', 'not', 'wait']
        return 200, {'audio_data': wav_b64(), 'audio_format': 'wav', 'billable_characters_count': 33,
                     'speech_marks': marks(words)}, {}
    fake_http.on('POST', URL, handler)
    res = tts.synthesize_line(LINE, CHAR, p, str(tmp_path / 'v'), 'vid1')
    assert seen['headers']['Authorization'] == 'Bearer sk-test'
    assert seen['json']['voice_id'] == 'voice-abc_1' and seen['json']['audio_format'] == 'wav'
    assert seen['json']['model'] == 'simba-english' and seen['json']['input'].startswith('<speak>')
    assert res['provider'] == 'speechify' and res['alignment_kind'] == 'measured_provider'
    assert [w['word'] for w in res['words']] == ['Jump', 'now', 'The', 'lava', 'does', 'not', 'wait']
    assert all(0.0 <= w['start'] <= w['end'] <= res['duration_s'] for w in res['words'])
    assert all(w['caption_word'] for w in res['words']), 'mapped back onto the caption words'
    rows = db.query('SELECT * FROM paid_calls')
    assert len(rows) == 1 and rows[0]['provider'] == 'speechify' and rows[0]['status'] == 'completed'
    assert rows[0]['operation'] == 'tts'
    assert budget.spent(db, category='tts') > 0
    # Same line again: served from the ledger, no second request.
    tts.synthesize_line(LINE, CHAR, p, str(tmp_path / 'v'), 'vid1')
    assert fake_http.count('POST', URL) == 1


def test_billing_limit_and_auth_failures_block(db, fake_http, tmp_path):
    vault.put('SPEECHIFY_API_KEY', 'sk-test', db)
    p = copy.deepcopy(prefs.get(db))
    fake_http.on('POST', URL, status=402, json_body={'error': 'payment required'})
    with pytest.raises(Blocked) as e:
        tts.synthesize_line(LINE, CHAR, p, str(tmp_path / 'v'), 'vid2')
    assert 'insufficient credits' in str(e.value) and e.value.state == 'blocked'
    row = db.one('SELECT * FROM paid_calls')
    assert row['status'] == 'failed'
    fake_http.on('POST', URL, status=401, json_body={'error': 'bad key'})
    with pytest.raises(Blocked) as e:
        tts.synthesize_line(dict(LINE, id='l9'), CHAR, p, str(tmp_path / 'v'), 'vid2')
    assert e.value.state == 'needs_credentials'
    assert paid.BILLING_CODES


def test_invalid_voice_id_and_empty_audio(db, fake_http, tmp_path):
    vault.put('SPEECHIFY_API_KEY', 'sk-test', db)
    p = copy.deepcopy(prefs.get(db))
    bad = dict(CHAR, voice={'engine': 'speechify', 'speechify_voice_id': 'bad id!'})
    with pytest.raises(Blocked):
        tts.synthesize_line(LINE, bad, p, str(tmp_path / 'v'), 'vid3')
    assert fake_http.count('POST', URL) == 0
    fake_http.on('POST', URL, json_body={'audio_data': '', 'speech_marks': {}})
    with pytest.raises(Blocked):
        tts.synthesize_line(dict(LINE, id='l5'), CHAR, p, str(tmp_path / 'v'), 'vid3')


def test_list_voices_only_with_key(db, fake_http):
    vault.put('SPEECHIFY_API_KEY', 'sk-test', db)
    fake_http.on('GET', speechify.BASE + '/v1/voices', json_body=[{'id': 'v1', 'display_name': 'Narrator', 'gender': 'male',
                                                                   'locale': 'en-US'}, {'id': 'bad id', 'display_name': 'x'}])
    assert speechify.list_voices() == [{'id': 'v1', 'name': 'Narrator', 'gender': 'male', 'locale': 'en-US'}]
