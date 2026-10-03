"""Text-to-speech per line, with pronunciation overrides, cleanup and alignment.

Providers:
* openai     - gpt-4o-mini-tts (or configured model), per-line ``instructions``
               carry emotion, pace and delivery. No timestamps are returned;
               word timing comes from ASR alignment (whisper-1, paid, labelled
               ``measured_asr``) or an energy-based estimate (``estimated``).
* elevenlabs - /v1/text-to-speech/{voice}/with-timestamps returns character
               timings, converted to word timings (``measured_provider``).
* local_test - FFmpeg's built-in flite voices. Robotic. For pipeline tests
               only; QA blocks publishing a video that uses it.

Voices are synthetic voices from the provider's catalogue or voices the owner
has rights to; this module never clones a person's voice.
"""
import base64
import os
import re
import tempfile

from .. import config, media, paid, vault
from ..http import request
from ..util import Blocked, stable_hash
from . import audio as A

TTS_INSTRUCTION_LIMIT = 1200
OPENAI_VOICES = ['alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'onyx', 'nova', 'sage', 'shimmer', 'verse',
                 'marin', 'cedar']
FLITE_VOICES = ['kal', 'kal16', 'awb', 'rms', 'slt']
LOUDNESS_VOLUME = {'whisper': -6.0, 'soft': -3.0, 'normal': 0.0, 'loud': 1.5, 'shout': 3.0}


def spoken_text(text, pronunciations):
    """Apply whole-word pronunciation overrides for the TTS input only."""
    out = text
    for word, say in sorted((pronunciations or {}).items(), key=lambda kv: -len(kv[0])):
        out = re.sub(r'(?<![A-Za-z])' + re.escape(word) + r'(?![A-Za-z])', say, out)
    return out


def instructions(line, character_name, voice):
    parts = [voice.get('openai_instructions') or 'Natural, expressive delivery.',
             f'You are voicing {character_name}.' if character_name else '',
             f'Emotion: {line["emotion"]}.', f'Pace: {line["pace"]}.', f'Volume: {line["volume"]}.',
             (f'Direction: {line["delivery"]}.' if line.get('delivery') else ''),
             'Speak only the given words. Natural breaths and pauses. Do not add sounds or words.']
    return ' '.join(p for p in parts if p)[:TTS_INSTRUCTION_LIMIT]


def estimate_cost(text, provider, prices, pace='normal'):
    words = max(1, len(re.findall(r"[A-Za-z0-9']+", text)))
    minutes = words / 150.0 * (1.2 if pace == 'slow' else 1.0)
    if provider == 'openai':
        return max(0.0005, minutes * prices['openai_tts_per_min'])
    if provider == 'elevenlabs':
        return max(0.0005, len(text) / 1000.0 * prices['elevenlabs_per_kchar'])
    return 0.0


# ------------------------------------------------------------------ providers
def _openai_tts(text, voice_name, instr, model, out_path):
    key = vault.get('OPENAI_API_KEY')
    if not key:
        raise Blocked('Connect OpenAI to generate natural voices', state='needs_credentials')
    r = request('openai', 'POST', 'https://api.openai.com/v1/audio/speech',
                headers={'Authorization': 'Bearer ' + key},
                json={'model': model, 'voice': voice_name, 'input': text, 'instructions': instr,
                      'response_format': 'wav'}, timeout=(10, 120))
    if not r.content or len(r.content) < 1000:
        raise Blocked('OpenAI returned empty audio', state='failed')
    with open(out_path, 'wb') as f:
        f.write(r.content)
    return {'path': out_path}


def _elevenlabs_tts(text, voice_id, line, model, out_path):
    key = vault.get('ELEVENLABS_API_KEY')
    if not key:
        raise Blocked('Connect ElevenLabs or choose another voice provider', state='needs_credentials')
    if not re.fullmatch(r'[A-Za-z0-9]{8,64}', voice_id or ''):
        raise Blocked('This character has no valid ElevenLabs voice ID', state='needs_review')
    expressive = line['emotion'] not in ('neutral', 'bored')
    settings = {'stability': 0.35 if expressive else 0.55, 'similarity_boost': 0.75,
                'style': 0.45 if expressive else 0.15, 'use_speaker_boost': True}
    r = request('elevenlabs', 'POST', f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps',
                headers={'xi-api-key': key}, params={'output_format': 'mp3_44100_128'},
                json={'text': text, 'model_id': model, 'voice_settings': settings}, timeout=(10, 120))
    j = r.json()
    with open(out_path, 'wb') as f:
        f.write(base64.b64decode(j['audio_base64']))
    al = j.get('alignment') or j.get('normalized_alignment')
    return {'path': out_path, 'char_alignment': al}


def _local_tts(text, voice, out_path):
    voice = voice if voice in FLITE_VOICES else 'kal16'
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, dir=config.WORK_DIR) as tf:
        # textfile= avoids putting user text inside the filter graph string.
        tf.write(re.sub(r'[^\w\s.,!?\'-]', ' ', text))
        tpath = tf.name
    try:
        media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-f', 'lavfi', '-i', f'flite=textfile={tpath}:voice={voice}',
                   '-ar', str(A.SR), '-ac', '1', out_path], timeout=120)
    finally:
        os.unlink(tpath)
    return {'path': out_path}


# ------------------------------------------------------------------ alignment
def words_from_chars(text, al):
    chars = al.get('characters') or []
    st = al.get('character_start_times_seconds') or []
    en = al.get('character_end_times_seconds') or []
    words, cur, s, e = [], '', None, None
    for c, a, b in zip(chars, st, en):
        if re.match(r"[A-Za-z0-9']", c):
            if not cur:
                s = a
            cur += c
            e = b
        elif cur:
            words.append({'word': cur, 'start': float(s), 'end': float(e)})
            cur = ''
    if cur:
        words.append({'word': cur, 'start': float(s), 'end': float(e)})
    return words


def asr_words(path, model):
    key = vault.get('OPENAI_API_KEY')
    if not key:
        return None
    with open(path, 'rb') as f:
        r = request('openai', 'POST', 'https://api.openai.com/v1/audio/transcriptions',
                    headers={'Authorization': 'Bearer ' + key},
                    files={'file': (os.path.basename(path), f, 'audio/wav')},
                    data={'model': model, 'response_format': 'verbose_json', 'timestamp_granularities[]': 'word'},
                    timeout=(10, 120))
    j = r.json()
    return [{'word': w['word'], 'start': float(w['start']), 'end': float(w['end'])} for w in j.get('words', [])], \
        j.get('text', '')


def estimated_words(text, samples):
    """Distribute words over voiced regions by syllable weight (estimate)."""
    ws = re.findall(r"[A-Za-z0-9']+", text)
    regions = A.speech_regions(samples) or [(0.0, len(samples) / A.SR)]
    total_voiced = sum(b - a for a, b in regions)
    weights = [max(1, len(re.findall(r'[aeiouy]+', w.lower()))) + 0.25 for w in ws]
    wsum = sum(weights) or 1.0
    out = []
    ri, r_used = 0, 0.0
    for w, wt in zip(ws, weights):
        need = total_voiced * wt / wsum
        start = regions[ri][0] + r_used
        remaining = need
        end = start
        while remaining > 1e-6 and ri < len(regions):
            avail = regions[ri][1] - (regions[ri][0] + r_used)
            take = min(avail, remaining)
            end = regions[ri][0] + r_used + take
            remaining -= take
            r_used += take
            if r_used >= regions[ri][1] - regions[ri][0] - 1e-6 and remaining > 1e-6:
                ri += 1
                r_used = 0.0
                if ri < len(regions):
                    end = regions[ri][0]
        out.append({'word': w, 'start': round(start, 3), 'end': round(end, 3)})
        if ri < len(regions) and r_used >= regions[ri][1] - regions[ri][0] - 1e-6:
            ri += 1
            r_used = 0.0
    return out


# ------------------------------------------------------------------ main entry
def synthesize_line(line, character, prefs, work_dir, video_id, attempt=0, use_asr=None):
    """Generate, clean and align one line. Returns a result dict.

    character: {'name', 'voice': {...}} or None for the narrator.
    """
    pr = prefs['production']
    provider = pr['tts_provider']
    voice = (character or {}).get('voice') or {}
    name = (character or {}).get('name', 'Narrator')
    say = spoken_text(line['text'], pr['pronunciations'])
    spec = {'p': provider, 'text': say, 'voice': voice, 'emotion': line['emotion'], 'pace': line['pace'],
            'volume': line['volume'], 'delivery': line.get('delivery', ''), 'model': pr['tts_model'],
            'attempt': attempt}
    h = stable_hash(spec)
    os.makedirs(work_dir, exist_ok=True)
    raw = os.path.join(work_dir, f'{line["id"]}_{h}_raw' + ('.mp3' if provider == 'elevenlabs' else '.wav'))
    char_al = None
    if provider == 'local_test':
        _local_tts(say, voice.get('local_test_voice', 'kal16'), raw)
    else:
        est = estimate_cost(say, provider, prefs['budget']['prices'], line['pace'])
        key = f'tts:{video_id}:{line["id"]}:{h}'

        def call():
            if provider == 'openai':
                v = voice.get('openai_voice') or 'alloy'
                if v not in OPENAI_VOICES:
                    raise Blocked(f'Unknown OpenAI voice {v!r} for {name}', state='needs_review')
                return _openai_tts(say, v, instructions(line, name, voice), pr['tts_model'], raw)
            return _elevenlabs_tts(say, voice.get('elevenlabs_voice_id'), line, pr['elevenlabs_model'], raw)

        res = paid.run(key, provider=provider, operation='tts', category='tts', estimate=est, fn=call,
                       video_id=video_id, summary={'line': line['id'], 'chars': len(say)},
                       recover=lambda: ({'path': raw} if os.path.exists(raw) and os.path.getsize(raw) > 1000 else None))
        char_al = (res or {}).get('char_alignment')
    samples = A.decode(raw)
    if len(samples) < A.SR * 0.15:
        raise Blocked(f'Line {line["id"]}: the voice provider returned silence', state='failed')
    trimmed, lead = A.trim_silence(samples)
    gain = 10 ** (LOUDNESS_VOLUME.get(line['volume'], 0.0) / 20)
    clean = os.path.join(work_dir, f'{line["id"]}_{h}.wav')
    tmp = clean + '.tmp.wav'
    A.write_wav(tmp, trimmed * gain)
    A.loudnorm(tmp, clean, target=-18.0 + LOUDNESS_VOLUME.get(line['volume'], 0.0), tp=-2.0)
    os.unlink(tmp)
    final = A.decode(clean)
    duration = len(final) / A.SR
    words, kind, asr_text = None, 'estimated', None
    if char_al:
        words = [dict(w, start=max(0.0, w['start'] - lead), end=max(0.0, w['end'] - lead))
                 for w in words_from_chars(say, char_al)]
        kind = 'measured_provider'
    want_asr = use_asr if use_asr is not None else (provider == 'openai')
    if words is None and want_asr and vault.configured('OPENAI_API_KEY') and provider != 'local_test':
        est = max(0.0005, duration / 60 * prefs['budget']['prices']['openai_asr_per_min'])
        try:
            res = paid.run(f'asr_align:{video_id}:{line["id"]}:{h}', provider='openai', operation='asr_alignment',
                           category='asr', estimate=est, video_id=video_id, fn=lambda: asr_words(clean, pr['asr_model']))
            if res:
                words, asr_text = res[0], res[1]
                kind = 'measured_asr'
        except Blocked:
            words = None
    if not words:
        words = estimated_words(say, final)
        kind = 'estimated'
    # Map spoken words back onto caption words when overrides changed the text.
    cap_words = re.findall(r"[A-Za-z0-9']+", line['text'])
    if len(cap_words) == len(words):
        for w, cw in zip(words, cap_words):
            w['caption_word'] = cw
    regions = A.speech_regions(final)
    return {
        'file': clean, 'raw': raw, 'duration_s': round(duration, 3), 'words': words, 'alignment_kind': kind,
        'provider': provider, 'voice': voice, 'spoken_text': say, 'asr_text': asr_text, 'spec_hash': h,
        'peak_dbfs': round(A.peak_dbfs(final), 2), 'clipped_fraction': A.clipped_fraction(final),
        'voiced_regions': regions,
        'internal_silence_s': round(max([b[0] - a[1] for a, b in zip(regions, regions[1:])] or [0.0]), 3),
        'test_voice': provider == 'local_test',
    }


def fit_line(line_result, window_s, max_tempo, work_dir):
    """If a line is slightly long, apply a gentle tempo change (<= max_tempo).

    Returns (result, action) where action is 'fits', 'tempo' or 'too_long'.
    Aggressive stretching is never applied: callers must rewrite or retime.
    """
    d = line_result['duration_s']
    if d <= window_s:
        return line_result, 'fits'
    ratio = d / max(0.05, window_s)
    if ratio > max_tempo:
        return line_result, 'too_long'
    out = line_result['file'].replace('.wav', f'_t{int(ratio * 1000)}.wav')
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-i', line_result['file'], '-af', f'atempo={ratio:.4f}',
               '-ar', str(A.SR), '-ac', '1', out])
    r = dict(line_result)
    r['file'] = out
    r['duration_s'] = round(len(A.decode(out)) / A.SR, 3)
    r['words'] = [dict(w, start=round(w['start'] / ratio, 3), end=round(w['end'] / ratio, 3)) for w in r['words']]
    r['tempo'] = round(ratio, 4)
    return r, 'tempo'

