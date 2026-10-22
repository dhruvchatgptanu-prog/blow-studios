"""Live: one OpenAI TTS line, its transcription, and a schema-constrained JSON chat."""
import copy

from blox import demo, prefs

from .conftest import need


def test_tts_and_asr_roundtrip(db, tmp_path):
    need('OPENAI_API_KEY')
    from blox.voice import tts
    p = copy.deepcopy(prefs.get(db))
    p['production']['tts_provider'] = 'openai'
    line = {'id': 'l1', 'speaker': 'hero', 'text': 'Wait, where is the next platform?', 'emotion': 'startled',
            'pace': 'fast', 'volume': 'loud'}
    ch = next(c for c in demo.CHARACTERS if c['id'] == 'ch_bloxy')
    res = tts.synthesize_line(line, ch, p, str(tmp_path), 'live_test', use_asr=True)
    assert res['duration_s'] > 0.8 and not res.get('test_voice')
    assert res['alignment_kind'] in ('measured_asr', 'measured_provider')
    heard = ' '.join(w['word'].lower() for w in res['words'])
    assert 'platform' in heard


def test_json_chat(db):
    need('OPENAI_API_KEY')
    from blox import llm
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['answer'],
              'properties': {'answer': {'type': 'string'}}}
    data, _ = llm.chat_json('live:chat', system='Reply with one word.', content='Say "ok".', schema_name='ok',
                            schema=schema, operation='live_test', category='other', est_in=50, est_out=20)
    assert isinstance(data['answer'], str)
