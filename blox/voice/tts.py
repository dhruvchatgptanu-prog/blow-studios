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
* piper      - free offline voices (blox.voice.piper); timing ``estimated``.
* kokoro     - free offline voices (blox.voice.kokoro); the package returns no
               timestamps, so timing is ``estimated``.
* speechify  - paid, the owner's key; word speech marks -> ``measured_provider``.

A character may override the studio's provider with ``voice.engine``.

Voices are synthetic voices from the provider's catalogue or voices the owner
has rights to; this module never clones a person's voice and never calls a
voice-cloning or voice-creation endpoint: an ElevenLabs voice is only ever the
voice id the owner supplied (typed on Characters or imported from voice.json).

Natural voices (owner's rule, 2026-10-19; pref ``time_stretch_speech``, off by
default): speech is never time-stretched. A line slightly too long for its slot
is voiced again at the engine's own faster speed (``fit_speed`` on the line,
folded into the engine's native control: Piper length scale, Kokoro speed,
Speechify prosody rate, OpenAI ``speed`` on models that take it, ElevenLabs
``speed``) instead of FFmpeg ``atempo``, and a pitch shift only uses the
rubberband filter (pitch only) or is skipped with the reason on the result.
"""
import base64
import json
import math
import os
import re
import tempfile

from .. import config, db as dbmod, media, paid, vault
from ..http import ProviderError, request
from ..manifest import schema as S
from ..util import Blocked, Retry, stable_hash
from . import audio as A, voicecfg
from .sections import line_section

TTS_INSTRUCTION_LIMIT = 1200
OPENAI_VOICES = ['alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'onyx', 'nova', 'sage', 'shimmer', 'verse',
                 'marin', 'cedar']
FLITE_VOICES = ['kal', 'kal16', 'awb', 'rms', 'slt']
LOUDNESS_VOLUME = {'whisper': -6.0, 'soft': -3.0, 'normal': 0.0, 'loud': 1.5, 'shout': 3.0}
# OpenAI models that take the native ``speed`` parameter (0.25-4.0). gpt-4o-mini-tts does not support it; for that
# model the speech rate is asked for in the instructions instead.
OPENAI_SPEED_MODELS = {'tts-1', 'tts-1-hd'}
# ElevenLabs voice_settings.speed accepts 0.7-1.2; faster requests are clamped to 1.2.
ELEVENLABS_SPEED = (0.7, 1.2)
# Piper reads every line at the same calm level, so on top of the loudness lift above, loud and shouted lines rise
# a little in pitch and gain presence (a high shelf from 2.5 kHz), the way a raised voice does:
# volume -> (extra semitones, presence dB).
PIPER_VOLUME_FX = {'loud': (0.5, 2.0), 'shout': (1.0, 4.0)}
# Longest silence kept inside a Piper line at speech rate 1 (it shrinks with the rate). Piper leaves 0.2-0.5 s
# between sentences; storytime delivery runs them together. Deliberate pauses belong in pause_after_ms.
PIPER_MAX_GAP_S = 0.16
# ElevenLabs: neighbouring lines sent as previous_text / next_text (continuity of delivery), capped in length.
ELEVENLABS_CONTEXT_CHARS = 600
ELEVENLABS_SUBSCRIPTION_URL = 'https://api.elevenlabs.io/v1/user/subscription'
# Stop for the owner before voicing a video that would use more than this share of the ElevenLabs characters left.
ELEVENLABS_CREDIT_SHARE_MAX = 0.25
# The owner's button on a video stopped by the credit check (static/app.js, action approve_credits).
CREDIT_APPROVE_BUTTON = 'Use the credits anyway'


def line_rate(line):
    """Native speaking speed the line was compiled for (1.0 = normal)."""
    from .piper import speech_rate
    return speech_rate(line)


def fit_speed(line):
    """How much faster than its compiled speech rate a re-voiced line is spoken (``fit_speed``, set by line
    fitting; 1.0 = not re-voiced). Never above the 1.5x line speed-up limit."""
    try:
        x = float(line.get('fit_speed', 1.0))
    except (TypeError, ValueError):
        return 1.0
    return x if math.isfinite(x) and 1.0 <= x <= S.MAX_LINE_SPEEDUP else 1.0


def engine_line(line):
    """The line as a speech-rate-driven engine sees it: a fitting speed-up is folded into its native speech rate
    (Piper length scale, Kokoro speed, Speechify rate, OpenAI speed), so it is synthesised faster, not stretched."""
    f = fit_speed(line)
    if f == 1.0:
        return line
    return dict(line, speech_rate=round(min(2.0, line_rate(line) * f), 4))


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
             (f'Speak about {line_rate(line):g} times faster than a normal conversational pace, crisp and clear.'
              if line_rate(line) != 1.0 else ''),
             'Speak only the given words. Natural breaths and pauses. Do not add sounds or words.']
    return ' '.join(p for p in parts if p)[:TTS_INSTRUCTION_LIMIT]


def estimate_cost(text, provider, prices, pace='normal'):
    words = max(1, len(re.findall(r"[A-Za-z0-9']+", text)))
    minutes = words / 150.0 * (1.2 if pace == 'slow' else 1.0)
    if provider == 'openai':
        return max(0.0005, minutes * prices['openai_tts_per_min'])
    if provider == 'elevenlabs':
        return max(0.0005, len(text) / 1000.0 * prices['elevenlabs_per_kchar'])
    if provider == 'speechify':
        from .speechify import estimate_cost as speechify_cost
        return speechify_cost(text, prices)
    return 0.0


# ------------------------------------------------------------------ providers
def _openai_tts(text, voice_name, instr, model, out_path, speed=1.0):
    key = vault.get('OPENAI_API_KEY')
    if not key:
        raise Blocked('Connect OpenAI to generate natural voices', state='needs_credentials')
    body = {'model': model, 'voice': voice_name, 'input': text, 'instructions': instr, 'response_format': 'wav'}
    if speed != 1.0 and model in OPENAI_SPEED_MODELS:
        body['speed'] = round(max(0.25, min(4.0, speed)), 3)
    r = request('openai', 'POST', 'https://api.openai.com/v1/audio/speech',
                headers={'Authorization': 'Bearer ' + key}, json=body, timeout=(10, 120))
    if not r.content or len(r.content) < 1000:
        raise Blocked('OpenAI returned empty audio', state='failed')
    with open(out_path, 'wb') as f:
        f.write(r.content)
    return {'path': out_path}


def elevenlabs_speed(line, voice, section='body'):
    """ElevenLabs ``voice_settings.speed`` for a line, or None when the request leaves it out.

    A voice with imported settings (voice.json) speaks at settings.speed x the section's speed (hook, body or end)
    x any line-fitting speed-up; the studio speech rate does not apply to it (the owner's own speed setting
    replaces it). Other voices speak at the line's speech rate. Both are clamped to ElevenLabs' 0.7-1.2."""
    settings, sections = voicecfg.imported_elevenlabs(voice)
    if settings is not None:
        speed = settings.get('speed', 1.0) * sections.get(section, 1.0) * fit_speed(line)
    else:
        speed = line_rate(engine_line(line))
        if speed == 1.0:
            return None
    return round(max(ELEVENLABS_SPEED[0], min(ELEVENLABS_SPEED[1], speed)), 3)


def elevenlabs_voice_settings(line, voice=None, section='body'):
    """The voice_settings object of an ElevenLabs request: the character's imported settings when it has them,
    else defaults by emotion (more expressive for lively lines), plus the speed (elevenlabs_speed)."""
    expressive = line['emotion'] not in ('neutral', 'bored')
    out = {'stability': 0.35 if expressive else 0.55, 'similarity_boost': 0.75,
           'style': 0.45 if expressive else 0.15, 'use_speaker_boost': True}
    settings, _ = voicecfg.imported_elevenlabs(voice)
    if settings is not None:
        out.update({k: v for k, v in settings.items() if k != 'speed'})
    speed = elevenlabs_speed(line, voice, section)
    if speed is not None:
        out['speed'] = speed
    return out


def elevenlabs_context(m, line, pronunciations=None):
    """(previous_text, next_text) for an ElevenLabs request: the same speaker's neighbouring lines in the manifest,
    up to two before and one after (as spoken), so delivery carries on naturally from line to line."""
    if not m or not line.get('id'):
        return '', ''
    same = sorted((ln for ln in m.get('lines') or () if ln.get('speaker') == line.get('speaker')),
                  key=lambda ln: ln.get('start_frame', 0))
    ids = [ln.get('id') for ln in same]
    if line['id'] not in ids:
        return '', ''
    i = ids.index(line['id'])
    prev = ' '.join(spoken_text(str(ln.get('text', '')), pronunciations) for ln in same[max(0, i - 2):i])
    nxt = spoken_text(str(same[i + 1].get('text', '')), pronunciations) if i + 1 < len(same) else ''
    return prev.strip()[-ELEVENLABS_CONTEXT_CHARS:], nxt.strip()[:ELEVENLABS_CONTEXT_CHARS]


def elevenlabs_request(line, voice, pr, manifest=None):
    """Model, voice settings and continuity text of a line's ElevenLabs request (manifest: for the section and
    the neighbouring lines; without it the line counts as 'body' and has no neighbours). Only a voice described by
    a voice.json gets the neighbouring lines (voicecfg.sends_context); other voices keep their old request."""
    section = line_section(manifest, line) if manifest and 'start_frame' in line else 'body'
    prev, nxt = '', ''
    if voicecfg.sends_context(voice):
        prev, nxt = elevenlabs_context(manifest, line, pr.get('pronunciations'))
    return {'model': voicecfg.elevenlabs_model(voice, pr['elevenlabs_model']), 'section': section,
            'settings': elevenlabs_voice_settings(line, voice, section), 'previous_text': prev, 'next_text': nxt}


def _elevenlabs_tts(text, voice_id, line, model, out_path, settings=None, previous_text='', next_text=''):
    key = vault.get('ELEVENLABS_API_KEY')
    if not key:
        raise Blocked('Connect ElevenLabs or choose another voice provider', state='needs_credentials')
    if not re.fullmatch(voicecfg.ELEVENLABS_VOICE_RE, voice_id or ''):
        raise Blocked('This character has no valid ElevenLabs voice ID', state='needs_review')
    if settings is None:
        settings = elevenlabs_voice_settings(line)
    body = {'text': text, 'model_id': model, 'voice_settings': settings}
    if previous_text:
        body['previous_text'] = previous_text
    if next_text:
        body['next_text'] = next_text
    r = request('elevenlabs', 'POST', f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps',
                headers={'xi-api-key': key}, params={'output_format': 'mp3_44100_128'},
                json=body, timeout=(10, 120))
    j = r.json()
    with open(out_path, 'wb') as f:
        f.write(base64.b64decode(j['audio_base64']))
    al = j.get('alignment') or j.get('normalized_alignment')
    return {'path': out_path, 'char_alignment': al}


def _credit_count(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= 1e12:
        raise ValueError('bad count')
    return int(x)


def elevenlabs_credits():
    """Characters left on the owner's ElevenLabs plan, from GET /v1/user/subscription.

    This reads the account (like listing voices): it is not billed, so it does not go through the paid-call
    ledger. The response is untrusted: only character_limit and character_count are read, as whole numbers."""
    key = vault.get('ELEVENLABS_API_KEY')
    if not key:
        raise Blocked('Connect ElevenLabs or choose another voice provider', state='needs_credentials')
    unreadable = 'ElevenLabs sent a credit report Blox could not read, so nothing was voiced. Try again later.'
    try:
        r = request('elevenlabs', 'GET', ELEVENLABS_SUBSCRIPTION_URL, headers={'xi-api-key': key}, timeout=(10, 30))
        j = r.json()
        limit, used = _credit_count(j['character_limit']), _credit_count(j['character_count'])
    except ProviderError as e:
        if e.auth and 'permission' in str(e.code or '').lower():
            # A scoped key: it may make speech but may not read the account, so the credits cannot be checked.
            raise Blocked('Your ElevenLabs API key works, but it is not allowed to read your account, so Blox '
                          'could not check the credits left and voiced nothing. In ElevenLabs, give the key read '
                          'access to "User" (or make a key that has it), save it on Connections, then retry.',
                          state='needs_credentials')
        if e.auth:
            raise Blocked('ElevenLabs did not accept the API key when Blox checked the credits left. Check the key '
                          'on Connections.', state='needs_credentials')
        if e.transient:
            raise Retry('Could not reach ElevenLabs to check the credits left; trying again later.',
                        delay=e.retry_after)
        raise Blocked('Could not check the ElevenLabs credits left, so nothing was voiced. Try again later.',
                      state='needs_review')
    except (ValueError, TypeError, KeyError):
        raise Blocked(unreadable, state='needs_review')
    return {'character_limit': limit, 'character_count': used, 'remaining': max(0, limit - used)}


class CreditStop(Blocked):
    """The ElevenLabs credit check stopped a video for the owner: it would use more than a quarter of the characters
    that were left on the plan. ``needed`` is what it still needs, ``already`` what it has used, ``left`` what the
    plan has left now; the owner may approve going on once (CREDIT_APPROVE_BUTTON)."""

    def __init__(self, message, needed, already, left):
        super().__init__(message, state='needs_review')
        self.needed, self.already, self.left = needed, already, left


def credit_allowance(left, already=0, approved=False):
    """ElevenLabs characters one video may use in all: a quarter of what was left on the plan when it started (what
    is left now plus what the video has already used), or everything left once the owner approved going on."""
    base = max(0, left) + max(0, already)
    return base if approved else ELEVENLABS_CREDIT_SHARE_MAX * base


def check_elevenlabs_credits(needed, already=0, approved=False):
    """Stop for the owner when a video's ElevenLabs lines (``needed`` characters, on top of the ``already`` it has
    used) would take it past a quarter of the characters left on the plan (CreditStop). With the owner's approval
    only a plan that has too few characters left stops it. Returns the credit report, with ``allowed`` (what the
    video may use in all), when it is fine to go on."""
    credits = elevenlabs_credits()
    left = credits['remaining']
    allowed = credit_allowance(left, already, approved)
    if already + needed <= allowed:
        return dict(credits, allowed=allowed)
    if approved:
        raise Blocked(f'This video needs about {needed:,} more ElevenLabs characters and your plan has only '
                      f'{left:,} left. Add credits or shorten the script, then retry.', state='needs_review')
    used = (f'This video has used about {already:,} ElevenLabs characters and needs about {needed:,} more; '
            if already else f'This video needs about {needed:,} ElevenLabs characters and ')
    raise CreditStop(f'{used}your plan has {left:,} left. That is more than a quarter of what '
                     f'{"was left when the video started" if already else "is left"}, so Blox stopped before voicing '
                     f'{"more of it" if already else "it"}. Add credits or shorten the script, then retry. Or press '
                     f'"{CREDIT_APPROVE_BUTTON}" on the video to go on this once.', needed, already, left)


def paid_key(video_id, line, spec_hash):
    """The paid-call ledger key of one take of a line (one take is billed once, see blox.paid)."""
    return f'tts:{video_id}:{line["id"]}:{spec_hash}'


def billed_characters(line, character, pr, video_id=None, attempt=0, manifest=None):
    """ElevenLabs characters voicing this take of the line will bill: its spoken text, or 0 when the line uses
    another engine or this exact take was already paid for (its ledger entry is complete, so it is reused)."""
    if voicecfg.engine_for((character or {}).get('voice'), pr['tts_provider']) != 'elevenlabs':
        return 0
    ls = line_spec(line, character, pr, attempt, manifest)
    if video_id is not None:
        row = paid.get(paid_key(video_id, line, ls['hash']))
        if row and row['status'] == 'completed':
            return 0
    return len(ls['say'])


def elevenlabs_sent(video_id, d=None):
    """ElevenLabs characters already sent for a video, from the paid-call ledger: completed requests and requests
    whose outcome is unknown (they may have been charged). Untrusted-safe: odd entries count as 0."""
    d = d or dbmod.get()
    total = 0
    for r in d.query("SELECT request_summary FROM paid_calls WHERE provider='elevenlabs' AND operation='tts' AND "
                     "video_id=? AND status IN ('completed', 'intent', 'ambiguous')", (video_id,)):
        try:
            n = json.loads(r['request_summary'] or '{}').get('chars', 0)
        except (ValueError, TypeError, AttributeError):
            n = 0
        if isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 1_000_000:
            total += n
    return total


def _local_tts(text, voice, out_path):
    # FFmpeg's flite source has no speaking-rate option, so the test voice always speaks at its own rate (it is
    # not time-stretched to match speech_rate; line fitting handles it like any long line).
    voice = voice if voice in FLITE_VOICES else 'kal16'
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
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


def _post_shift(provider, voice, line):
    """(semitones, formants, presence dB) a voiced line is coloured with after synthesis."""
    from .piper import voice_settings
    vs = voice_settings(voice)
    semitones, presence = vs.get('pitch_semitones', 0.0), 0.0
    if provider in ('piper', 'kokoro'):
        extra, presence = PIPER_VOLUME_FX.get(line.get('volume'), (0.0, 0.0))
        semitones += extra
    return semitones, vs['pitch_formants'], presence


def post_filter(provider, voice, line, resample_fallback=True):
    """FFmpeg filter chain applied to a voiced line before cleanup: the character's pitch shift
    (``voice.pitch_semitones``, any provider) plus, for Piper, the raised-voice colour of loud lines.
    Returns '' when nothing applies. With ``resample_fallback=False`` (time stretching off) the pitch shift is
    left out when the rubberband filter is missing (see pitch_skipped)."""
    semitones, formants, presence = _post_shift(provider, voice, line)
    parts = []
    if abs(semitones) >= 0.05:
        parts.append(A.pitch_filter(semitones, formants, resample_fallback=resample_fallback))
    if presence:
        parts.append(f'highshelf=f=2500:g={presence:g}')
    return ','.join(p for p in parts if p)


def pitch_skipped(provider, voice, line, resample_fallback=True):
    """Why the line's pitch shift was left out (A.PITCH_SKIPPED), or None when it was applied or not wanted."""
    if resample_fallback or abs(_post_shift(provider, voice, line)[0]) < 0.05 or A.has_filter('rubberband'):
        return None
    return A.PITCH_SKIPPED


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
def line_spec(line, character, pr, attempt=0, manifest=None):
    """Everything that determines a line's raw audio: provider (the character's engine override or the studio's),
    spoken text, voice settings and direction. Returns the spec, its hash and the raw file name.

    A re-voiced line's ``fit_speed`` and an ElevenLabs line's request (model, settings with the effective speed,
    previous/next text) are part of the spec, so their audio is cached and billed apart from the original."""
    voice = (character or {}).get('voice') or {}
    provider = voicecfg.engine_for(voice, pr['tts_provider'])
    say = spoken_text(line['text'], pr['pronunciations'])
    # The voice.json consent record (with its import time) does not change the audio, so it is not in the key.
    sound = {k: v for k, v in voice.items() if k != 'voice_import'}
    spec = {'p': provider, 'text': say, 'voice': sound, 'emotion': line['emotion'], 'pace': line['pace'],
            'volume': line['volume'], 'delivery': line.get('delivery', ''), 'model': pr['tts_model'],
            'attempt': attempt}
    if line_rate(line) != 1.0:
        spec['rate'] = line_rate(line)
    if fit_speed(line) != 1.0:
        spec['fit_speed'] = fit_speed(line)
    if line.get('kind') == 'narration':
        spec['kind'] = 'narration'
    eleven = None
    if provider == 'piper':
        from . import piper
        spec['piper'] = [pr.get('piper_model', piper.DEFAULT_MODEL),
                         piper.speaker_for((character or {}).get('id'), voice)]
    elif provider == 'kokoro':
        from . import kokoro
        spec['kokoro'] = [pr.get('kokoro_model', kokoro.DEFAULT_MODEL),
                          kokoro.voice_for((character or {}).get('id'), voice),
                          kokoro.speed_for(engine_line(line), voice)]
    elif provider == 'speechify':
        from . import speechify
        spec['speechify'] = [pr.get('speechify_model', speechify.DEFAULT_MODEL), voice.get('speechify_voice_id', '')]
    elif provider == 'elevenlabs':
        eleven = elevenlabs_request(line, voice, pr, manifest)
        # Lines voiced before imported settings and continuity existed keep their cache key.
        if (voicecfg.imported_elevenlabs(voice)[0] is not None or eleven['previous_text'] or eleven['next_text']
                or eleven['model'] != pr['elevenlabs_model']):
            spec['elevenlabs'] = {k: eleven[k] for k in ('model', 'settings', 'previous_text', 'next_text')}
    h = stable_hash(spec)
    return {'provider': provider, 'say': say, 'voice': voice, 'spec': spec, 'hash': h, 'elevenlabs': eleven,
            'raw': f'{line["id"]}_{h}_raw' + ('.mp3' if provider == 'elevenlabs' else '.wav')}


def native_speed(line, character, pr, manifest=None):
    """The engine's own speed control for this line, as its request or command carries it, or None when the
    engine has none (the FFmpeg test voice; OpenAI models without a ``speed`` parameter)."""
    voice = (character or {}).get('voice') or {}
    provider = voicecfg.engine_for(voice, pr['tts_provider'])
    el = engine_line(line)
    if provider == 'piper':
        from . import piper
        return piper.delivery(el, voice)[0]
    if provider == 'kokoro':
        from . import kokoro
        return kokoro.speed_for(el, voice)
    if provider == 'speechify':
        from . import speechify
        return round(min(speechify.RATE_RANGE[1], max(speechify.RATE_RANGE[0], line_rate(el))), 2)
    if provider == 'openai':
        return round(max(0.25, min(4.0, line_rate(el))), 3) if pr['tts_model'] in OPENAI_SPEED_MODELS else None
    if provider == 'elevenlabs':
        section = line_section(manifest, line) if manifest and 'start_frame' in line else 'body'
        return elevenlabs_speed(line, voice, section) or 1.0
    return None


def can_speed_up(line, character, pr, factor, manifest=None):
    """True when the line's engine can natively speak ``factor`` times faster than it does now (it has a speed
    control and is not already at the end of its range)."""
    if factor <= 1.0:
        return False
    now = native_speed(line, character, pr, manifest)
    if now is None:
        return False
    return native_speed(dict(line, fit_speed=factor), character, pr, manifest) != now


def synthesize_line(line, character, prefs, work_dir, video_id, attempt=0, use_asr=None, manifest=None):
    """Generate, clean and align one line. Returns a result dict.

    character: {'name', 'voice': {...}} or None for the narrator. manifest: the line's manifest, when known (the
    ElevenLabs request uses its story sections and neighbouring lines; other engines ignore it).
    """
    pr = prefs['production']
    ls = line_spec(line, character, pr, attempt, manifest)
    provider, say, voice, spec, h = ls['provider'], ls['say'], ls['voice'], ls['spec'], ls['hash']
    name = (character or {}).get('name', 'Narrator')
    voice_meta = None
    os.makedirs(work_dir, exist_ok=True)
    raw = os.path.join(work_dir, ls['raw'])
    char_al, prov_words = None, None
    el = engine_line(line)  # the speech rate the engine is asked for (a fitting speed-up included)
    if provider == 'local_test':
        _local_tts(say, voice.get('local_test_voice', 'kal16'), raw)
    elif provider == 'piper':
        from . import piper
        voice_meta = piper.synthesize(say, raw, spec['piper'][1], el, model=spec['piper'][0], voice=voice)
    elif provider == 'kokoro':
        from . import kokoro
        if os.path.exists(raw) and os.path.getsize(raw) > 2000:
            # Written atomically by the runner for this exact spec (bake-off batches render ahead).
            voice_meta = kokoro.plan(say, raw, spec['kokoro'][1], el, model=spec['kokoro'][0], voice=voice)[1]
        else:
            voice_meta = kokoro.synthesize(say, raw, spec['kokoro'][1], el, model=spec['kokoro'][0], voice=voice)
        prov_words = voice_meta.get('words')
    else:
        if provider == 'speechify' and not vault.configured('SPEECHIFY_API_KEY'):
            # Checked before the ledger: without the owner's key nothing is reserved, recorded or sent.
            raise Blocked('Connect Speechify (API key on Connections) or choose another voice engine',
                          state='needs_credentials')
        est = estimate_cost(say, provider, prefs['budget']['prices'], line['pace'])
        key = paid_key(video_id, line, h)

        def call():
            if provider == 'openai':
                v = voice.get('openai_voice') or 'alloy'
                if v not in OPENAI_VOICES:
                    raise Blocked(f'Unknown OpenAI voice {v!r} for {name}', state='needs_review')
                return _openai_tts(say, v, instructions(el, name, voice), pr['tts_model'], raw,
                                   speed=line_rate(el))
            if provider == 'speechify':
                from . import speechify
                return speechify.synthesize(say, voice.get('speechify_voice_id'), el, raw, spec['speechify'][0])
            eleven = ls['elevenlabs']
            return _elevenlabs_tts(say, voice.get('elevenlabs_voice_id'), line, eleven['model'], raw,
                                   settings=eleven['settings'], previous_text=eleven['previous_text'],
                                   next_text=eleven['next_text'])

        res = paid.run(key, provider=provider, operation='tts', category='tts', estimate=est, fn=call,
                       video_id=video_id, summary={'line': line['id'], 'chars': len(say)},
                       recover=lambda: ({'path': raw} if os.path.exists(raw) and os.path.getsize(raw) > 1000 else None))
        char_al = (res or {}).get('char_alignment')
        prov_words = (res or {}).get('words')
    stretch_ok = bool(pr.get('time_stretch_speech', False))
    fx = post_filter(provider, voice, line, resample_fallback=stretch_ok)
    skipped = pitch_skipped(provider, voice, line, resample_fallback=stretch_ok)
    src = raw
    if fx:
        src = os.path.join(work_dir, f'{line["id"]}_{h}_fx.wav')
        A.apply_filter(raw, src, fx)
    samples = A.decode(src)
    if len(samples) < A.SR * 0.15:
        raise Blocked(f'Line {line["id"]}: the voice provider returned silence', state='failed')
    trimmed, lead = A.trim_silence(samples)
    squeezed = 0.0
    if provider == 'piper':
        trimmed, squeezed = A.squeeze_gaps(trimmed, max(0.08, PIPER_MAX_GAP_S / line_rate(el)))
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
    elif prov_words:
        # Provider word timestamps (Speechify speech marks), shifted by the trimmed lead-in and kept within the
        # cleaned line, so the active caption word follows the real timing.
        words = [{'word': w['word'], 'start': round(min(duration, max(0.0, float(w['start']) - lead)), 3),
                  'end': round(min(duration, max(0.0, float(w['end']) - lead)), 3)} for w in prov_words]
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
        'voice_meta': voice_meta,
        'post': {'filter': fx, 'gaps_removed_s': squeezed, 'pitch_skipped': skipped},
        'fit_speed': fit_speed(line),
    }


def fit_line(line_result, window_s, max_tempo, work_dir, stretch=True):
    """If a line is slightly long, apply a gentle tempo change (<= max_tempo).

    Callers pass ``manifest.compile.tempo_cap`` so that native speech rate times
    tempo never exceeds 1.5x.

    Returns (result, action) where action is 'fits', 'tempo' or 'too_long'.
    Aggressive stretching is never applied: callers must rewrite or retime.
    With ``stretch=False`` (the owner's no-time-stretch rule) the audio is never
    changed: a slightly long line returns 'speed_up' instead of 'tempo', and the
    caller re-voices it at a natively faster engine speed (production.retime).
    """
    d = line_result['duration_s']
    if d <= window_s:
        return line_result, 'fits'
    ratio = d / max(0.05, window_s)
    if ratio > max_tempo:
        return line_result, 'too_long'
    if not stretch:
        return line_result, 'speed_up'
    out = line_result['file'].replace('.wav', f'_t{int(ratio * 1000)}.wav')
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-i', line_result['file'], '-af', f'atempo={ratio:.4f}',
               '-ar', str(A.SR), '-ac', '1', out])
    r = dict(line_result)
    r['file'] = out
    r['duration_s'] = round(len(A.decode(out)) / A.SR, 3)
    r['words'] = [dict(w, start=round(w['start'] / ratio, 3), end=round(w['end'] / ratio, 3)) for w in r['words']]
    r['tempo'] = round(ratio, 4)
    return r, 'tempo'

