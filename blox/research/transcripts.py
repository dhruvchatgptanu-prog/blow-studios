"""Transcripts with provenance.

Sources, in order of preference:
* ``production_script`` - our own Blox videos: the approved script itself.
* ``upload``            - a transcript file the owner uploads (.srt/.vtt/.txt).
* ``asr``               - speech recognition of media the owner uploaded with a
                          rights confirmation (OpenAI whisper-1, word timings).
* ``provider``          - an authorized third-party transcript service the owner
                          configures (URL + key); contract documented below.
* ``owner_captions``    - captions of videos on the connected channel. This needs
                          the youtube.force-ssl scope, which Blox does not request
                          by default, so it is reported as unavailable.
The YouTube Data API cannot download captions of other creators' videos, and
Blox does not scrape them. If nothing is available the status is
``unavailable`` and analysis continues on metadata only.

Provider contract: GET {TRANSCRIPT_PROVIDER_URL}?video_id=<id> with
``Authorization: Bearer <key>`` returning
``{"language": "en", "segments": [{"start": 0.0, "end": 1.2, "text": "..."}]}``.
"""
import json
import re

from .. import db as dbmod, netsafe, paid, prefs as prefsmod, untrusted, vault
from ..util import new_id, now

TIME = re.compile(r'(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})|(\d{1,2}):(\d{2})[.,](\d{1,3})')


def _ts(s):
    m = TIME.search(s)
    if not m:
        return None
    if m.group(1):
        h, mi, se, ms = (int(x) for x in m.group(1, 2, 3, 4))
    else:
        h, (mi, se, ms) = 0, (int(x) for x in m.group(5, 6, 7))
    return h * 3600 + mi * 60 + se + int(str(ms).ljust(3, '0')[:3]) / 1000


def parse_captions(text):
    """SRT/VTT -> segments with supplied timings; plain text -> one untimed segment."""
    segs = []
    blocks = re.split(r'\n\s*\n', text.replace('\r', ''))
    for b in blocks:
        lines = [l for l in b.split('\n') if l.strip()]
        tline = next((l for l in lines if '-->' in l), None)
        if not tline:
            continue
        a, _, c = tline.partition('-->')
        start, end = _ts(a), _ts(c)
        body = ' '.join(l for l in lines[lines.index(tline) + 1:])
        body = re.sub(r'<[^>]+>', '', body)
        if start is not None and end is not None and body.strip():
            segs.append({'start': round(start, 3), 'end': round(end, 3), 'text': untrusted.clean(body, 500)})
    if segs:
        return segs, 'supplied'
    plain = untrusted.clean(re.sub(r'^WEBVTT.*$', '', text, flags=re.M), 20000)
    return ([{'start': None, 'end': None, 'text': plain}] if plain else []), 'none'


def save(subject, provider, provenance, rights, segments, timing, language=None, status='available', note='', d=None):
    d = d or dbmod.get()
    text = ' '.join(s['text'] for s in segments)
    tid = new_id('tr_')
    d.execute('''INSERT INTO transcripts(id, subject, provider, provenance, rights_basis, language, timing, status,
                 segments, text, created_at, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
              (tid, subject, provider, provenance, rights, language, timing, status, json.dumps(segments), text[:50000],
               now(), note[:500]))
    if subject.startswith('yt:'):
        d.execute('UPDATE ref_videos SET transcript_status=? WHERE video_id=?', (status, subject[3:]))
    return tid


def from_upload(subject, text, rights_confirmed):
    if not rights_confirmed:
        raise ValueError('Confirm that you may use this transcript')
    segs, timing = parse_captions(text)
    if not segs:
        raise ValueError('No transcript text found')
    flags = untrusted.injection_signals(' '.join(s['text'] for s in segs))
    return save(subject, 'upload', 'Owner-uploaded transcript file', 'owner_confirmed', segs, timing,
                note=('Possible instruction-like text: ' + '; '.join(flags)) if flags else '')


def from_provider(video_id, d=None):
    url = vault.get('TRANSCRIPT_PROVIDER_URL')
    key = vault.get('TRANSCRIPT_PROVIDER_KEY')
    if not url:
        return save('yt:' + video_id, 'none', 'No authorized transcript provider configured', 'none', [], 'none',
                    status='unavailable', note='Configure an authorized provider or upload a transcript.', d=d)
    body = netsafe.fetch(url, 'transcript_provider', params={'video_id': video_id},
                         headers={'Authorization': 'Bearer ' + key} if key else {}, max_bytes=5 * 1024 * 1024)
    j = json.loads(body)
    segs = [{'start': s.get('start'), 'end': s.get('end'), 'text': untrusted.clean(s.get('text', ''), 500)}
            for s in j.get('segments', [])[:5000] if s.get('text')]
    if not segs:
        return save('yt:' + video_id, 'provider', 'Authorized provider returned no transcript', 'provider_license', [],
                    'none', status='unavailable', d=d)
    timing = 'supplied' if all(isinstance(s['start'], (int, float)) for s in segs) else 'none'
    return save('yt:' + video_id, 'provider', 'Authorized transcript provider', 'provider_license', segs, timing,
                language=str(j.get('language', ''))[:10], d=d)


def from_asr(subject, media_path, video_id=None):
    """Speech recognition on owner-uploaded media (rights confirmed at upload)."""
    from ..voice.tts import asr_words
    from .. import media
    p = prefsmod.get()
    dur = media.duration(media_path)
    est = max(0.001, dur / 60 * p['budget']['prices']['openai_asr_per_min'])
    wav = str(media_path) + '.asr.wav'
    from .. import config
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-i', str(media_path), '-ac', '1', '-ar', '16000', '-t', '600', wav])
    words, text = paid.run(f'ref_asr:{subject}', provider='openai', operation='reference_asr', category='research',
                           estimate=est, fn=lambda: asr_words(wav, p['production']['asr_model']), video_id=video_id)
    segs = [{'start': w['start'], 'end': w['end'], 'text': untrusted.clean(w['word'], 60)} for w in words]
    return save(subject, 'asr', 'Speech recognition (whisper-1) of owner-uploaded media', 'owner_confirmed_upload',
                segs, 'supplied' if segs else 'none', note='Timings are ASR word timestamps (measured, may contain errors).')


def latest(subject, d=None):
    d = d or dbmod.get()
    r = d.one('SELECT * FROM transcripts WHERE subject=? ORDER BY created_at DESC', (subject,))
    if r:
        r['segments'] = json.loads(r['segments'])
    return r
