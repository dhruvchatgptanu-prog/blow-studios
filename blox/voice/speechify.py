"""Speechify: an optional paid voice provider using the owner's own API key.

Default OFF. Nothing here runs unless a character (or the studio) is set to the ``speechify`` engine and
``SPEECHIFY_API_KEY`` is saved on Connections; every request goes through the paid-call ledger
(``paid.run``: idempotency key, budget reservation, circuit breaker, billing-limit detection) exactly like OpenAI
TTS. The voices are catalogue voices from the owner's Speechify account; this module never clones anyone.

Request shape, from Speechify's public API docs as known when this was written (assumptions, not verified
against a live account; adjust ``BASE``/``DEFAULT_MODEL`` on Settings if the API changed):
* POST {BASE}/v1/audio/speech, ``Authorization: Bearer <key>``, JSON
  {"input": text or SSML, "voice_id": ..., "audio_format": "wav", "model": "simba-english",
   "language": "en-US"}; the response is JSON {"audio_data": base64, "audio_format": ..,
   "billable_characters_count": n, "speech_marks": {"type": "sentence", "start_time", "end_time", "chunks":
   [{"type": "word", "value", "start", "end", "start_time", "end_time"}]}} with times in milliseconds.
* SSML: <speak> with <speechify:style emotion="..."> and <prosody rate="..."> are accepted.
* GET {BASE}/v1/voices lists the account's voices: [{"id", "display_name", "gender", "locale", "models"}].
* Billing is per character; ``prices.speechify_per_kchar`` on Settings is the planning estimate.
"""
import base64
import html
import re

from .. import vault
from ..http import request
from ..util import Blocked
from .piper import speech_rate

BASE = 'https://api.sws.speechify.com'
DEFAULT_MODEL = 'simba-english'
MODELS = ('simba-english', 'simba-multilingual')
KEY = 'SPEECHIFY_API_KEY'
VOICE_RE = r'[A-Za-z0-9_\-]{1,64}'
# Our line emotions -> Speechify style emotions (documented set, as understood; unknown ones are left out).
EMOTIONS = {'happy': 'cheerful', 'excited': 'energetic', 'proud': 'bright', 'laughing': 'cheerful',
            'angry': 'angry', 'sad': 'sad', 'scared': 'terrified', 'worried': 'fearful', 'startled': 'surprised',
            'neutral': None, 'bored': 'relaxed', 'tired': 'relaxed', 'embarrassed': 'warm', 'disappointed': 'sad',
            'calm': 'calm'}
VOLUME = {'whisper': 'x-soft', 'soft': 'soft', 'normal': None, 'loud': 'loud', 'shout': 'x-loud'}
RATE_RANGE = (0.5, 2.0)


def configured():
    return vault.configured(KEY)


def estimate_cost(text, prices):
    return max(0.0005, len(text) / 1000.0 * float((prices or {}).get('speechify_per_kchar', 0.0)))


def ssml(text, line):
    """Wrap the line in SSML carrying its emotion, volume and native speech rate."""
    body = html.escape(text, quote=False)
    rate = speech_rate(line)
    attrs = []
    vol = VOLUME.get(line.get('volume'))
    if vol:
        attrs.append(f'volume="{vol}"')
    if rate != 1.0:
        rate = min(RATE_RANGE[1], max(RATE_RANGE[0], rate))
        attrs.append(f'rate="{(rate - 1.0) * 100:+.0f}%"')
    if attrs:
        body = f'<prosody {" ".join(attrs)}>{body}</prosody>'
    emo = EMOTIONS.get(line.get('emotion'))
    if emo:
        body = f'<speechify:style emotion="{emo}">{body}</speechify:style>'
    return f'<speak>{body}</speak>'


def words_from_marks(marks):
    """Word timings (seconds) from a speech_marks object; nested sentence chunks are flattened."""
    out = []

    def walk(node):
        if not isinstance(node, dict):
            return
        chunks = node.get('chunks') or []
        if node.get('type') == 'word' or (not chunks and node.get('value')):
            w = re.sub(r"[^A-Za-z0-9']+", '', str(node.get('value', '')))
            st, en = node.get('start_time'), node.get('end_time')
            if w and st is not None and en is not None:
                out.append({'word': w, 'start': round(float(st) / 1000.0, 3), 'end': round(float(en) / 1000.0, 3)})
            return
        for c in chunks:
            walk(c)
    walk(marks)
    return out


def synthesize(text, voice_id, line, out_path, model=DEFAULT_MODEL):
    """One paid request (callers wrap it in ``paid.run``). Returns {'path', 'words', 'billable_characters'}."""
    key = vault.get(KEY)
    if not key:
        raise Blocked('Connect Speechify (API key on Connections) or choose another voice engine',
                      state='needs_credentials')
    if not re.fullmatch(VOICE_RE, voice_id or ''):
        raise Blocked('This character has no valid Speechify voice id', state='needs_review')
    if model not in MODELS:
        raise Blocked(f'Unknown Speechify model {model!r}', state='needs_review')
    body = {'input': ssml(text, line), 'voice_id': voice_id, 'audio_format': 'wav', 'model': model,
            'language': 'en-US'}
    r = request('speechify', 'POST', BASE + '/v1/audio/speech', headers={'Authorization': 'Bearer ' + key},
                json=body, timeout=(10, 120))
    j = r.json()
    audio = base64.b64decode(j.get('audio_data') or '')
    if len(audio) < 1000:
        raise Blocked('Speechify returned empty audio', state='failed')
    with open(out_path, 'wb') as f:
        f.write(audio)
    words = words_from_marks(j.get('speech_marks') or {})
    return {'path': out_path, 'words': words or None, 'billable_characters': j.get('billable_characters_count'),
            'audio_format': j.get('audio_format', 'wav')}


def list_voices():
    """The account's voices (only on an explicit owner action; needs the key; not a billed call as documented)."""
    key = vault.get(KEY)
    if not key:
        raise Blocked('Connect Speechify (API key on Connections) first', state='needs_credentials')
    r = request('speechify', 'GET', BASE + '/v1/voices', headers={'Authorization': 'Bearer ' + key}, timeout=(10, 60))
    out = []
    for v in r.json() if isinstance(r.json(), list) else []:
        vid = str(v.get('id', ''))
        if re.fullmatch(VOICE_RE, vid):
            out.append({'id': vid, 'name': str(v.get('display_name', vid))[:60], 'gender': str(v.get('gender', ''))[:12],
                        'locale': str(v.get('locale', ''))[:12]})
    return out
