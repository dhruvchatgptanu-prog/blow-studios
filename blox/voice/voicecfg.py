"""Validation of the per-character voice settings added with the voice base.

engine            optional per-character engine override ('' = the studio's tts_provider).
kokoro_voice      Kokoro preset name (see blox.voice.kokoro.PRESETS); kokoro_speed 0.5-2.0.
speechify_voice_id  a voice id from the owner's Speechify account (paid; needs the key on Connections).
target_profile    id of a voice profile (voice_profiles table) the bake-off scores candidates against.
elevenlabs_model  ElevenLabs model id for this character (else the studio's elevenlabs_model).
elevenlabs_settings  {stability, similarity_boost, style: 0-1; use_speaker_boost: true/false; speed: 0.7-1.2}.
                  When set (or elevenlabs_section_speed is) they replace the emotion defaults for this character's
                  ElevenLabs lines, and the speed below replaces the studio speech rate.
elevenlabs_section_speed  {hook, body, end: 0.7-1.2}: speed multiplier by story section (blox.voice.sections).
                  The ElevenLabs speed is settings.speed x the section's speed (x a line-fitting speed-up),
                  clamped to 0.7-1.2.
voice_import      the consent and approval record of an imported voice.json. Only the import writes it; a save
                  on the Characters page keeps it while the voice it describes is still the character's.

voice.json is the owner's description of a voice (made on his Mac): engine, voice_name, voice_id, route
('library' or 'clone'), model, settings, section_speed, pace, consent {route, who, statement, date},
approved_by_dhruv and applies_to. ``parse_voice_json`` validates one as untrusted input. It never carries
secrets (a field named like a key, token or secret is refused) and Blox never clones a voice: it only uses the
voice id the owner supplies.
"""
import datetime
import json
import math
import re

from .kokoro import EXCLUDED as KOKORO_EXCLUDED, SPEED_RANGE, VOICE_RE as KOKORO_VOICE_RE

ENGINES = ('piper', 'kokoro', 'speechify', 'openai', 'elevenlabs')
FREE_ENGINES = ('piper', 'kokoro')
PROFILE_RE = r'vp_[A-Za-z0-9_-]{4,40}'
SPEECHIFY_VOICE_RE = r'[A-Za-z0-9_\-]{1,64}'

ELEVENLABS_MODEL_RE = r'[a-z0-9_]{3,64}'
ELEVENLABS_VOICE_RE = r'[A-Za-z0-9]{8,64}'
ELEVENLABS_SPEED = (0.7, 1.2)
ELEVENLABS_UNIT_KEYS = ('stability', 'similarity_boost', 'style')
SECTIONS = ('hook', 'body', 'end')
ELEVENLABS_KEYS = ('elevenlabs_model', 'elevenlabs_settings', 'elevenlabs_section_speed')

# voice.json import
IMPORT_ENGINES = ('elevenlabs',) + FREE_ENGINES
ROUTES = ('library', 'clone')
SECRET_WORDS = ('key', 'token', 'secret')
VOICE_JSON_FIELDS = ('engine', 'voice_name', 'voice_id', 'route', 'model', 'settings', 'section_speed', 'pace',
                     'consent', 'approved_by_dhruv', 'applies_to')
VOICE_JSON_MAX_BYTES = 64 * 1024
PIPER_SPEAKERS = 904
SECRET_REFUSED = ('This voice.json has a field named like a key, token or secret. Remove it and import again: '
                  'Blox never takes secrets from a voice file (save API keys on Connections).')


def _number(x, lo, hi, name):
    """A real JSON number (not text, not true/false) inside [lo, hi]."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f'{name} must be a number')
    x = float(x)
    if not math.isfinite(x) or not lo <= x <= hi:
        raise ValueError(f'{name} must be between {lo:g} and {hi:g}')
    return round(x, 4)


def elevenlabs_settings(s, name='ElevenLabs settings'):
    """Validated ElevenLabs voice settings: known keys only (others are ignored), each optional."""
    if not isinstance(s, dict):
        raise ValueError(f'{name} must be an object')
    out = {}
    for k in ELEVENLABS_UNIT_KEYS:
        if s.get(k) is not None:
            out[k] = _number(s[k], 0.0, 1.0, f'{name}: {k}')
    if s.get('use_speaker_boost') is not None:
        if not isinstance(s['use_speaker_boost'], bool):
            raise ValueError(f'{name}: use_speaker_boost must be true or false')
        out['use_speaker_boost'] = s['use_speaker_boost']
    if s.get('speed') is not None:
        out['speed'] = _number(s['speed'], *ELEVENLABS_SPEED, f'{name}: speed')
    return out


def section_speed(s, name='Section speed'):
    """Validated speed multipliers by story section ({hook, body, end}, each 0.7-1.2 and optional)."""
    if not isinstance(s, dict):
        raise ValueError(f'{name} must be an object')
    return {k: _number(s[k], *ELEVENLABS_SPEED, f'{name}: {k}') for k in SECTIONS if s.get(k) is not None}


def clean(voice):
    """Validated subset of the new keys (missing or blank keys are dropped, so defaults apply)."""
    voice = voice or {}
    out = {}
    eng = voice.get('engine')
    if eng in ENGINES:
        out['engine'] = eng
    elif eng not in (None, ''):
        raise ValueError('Unknown voice engine')
    kv = voice.get('kokoro_voice')
    if kv not in (None, ''):
        kv = str(kv)
        if not re.fullmatch(KOKORO_VOICE_RE, kv) or kv in KOKORO_EXCLUDED:
            raise ValueError('Kokoro voice must be a preset name such as af_heart')
        out['kokoro_voice'] = kv
    ks = voice.get('kokoro_speed')
    if ks not in (None, ''):
        try:
            x = float(ks)
        except (TypeError, ValueError):
            raise ValueError('Kokoro speed must be a number')
        if not math.isfinite(x) or not (SPEED_RANGE[0] <= x <= SPEED_RANGE[1]):
            raise ValueError(f'Kokoro speed must be between {SPEED_RANGE[0]:g} and {SPEED_RANGE[1]:g}')
        out['kokoro_speed'] = round(x, 3)
    sv = voice.get('speechify_voice_id')
    if sv not in (None, ''):
        sv = str(sv)
        if not re.fullmatch(SPEECHIFY_VOICE_RE, sv):
            raise ValueError('Speechify voice id must be letters, digits, - or _')
        out['speechify_voice_id'] = sv
    tp = voice.get('target_profile')
    if tp not in (None, ''):
        tp = str(tp)
        if not re.fullmatch(PROFILE_RE, tp):
            raise ValueError('target_profile must be a voice profile id')
        out['target_profile'] = tp
    em = voice.get('elevenlabs_model')
    if em not in (None, ''):
        if not isinstance(em, str) or not re.fullmatch(ELEVENLABS_MODEL_RE, em):
            raise ValueError('ElevenLabs model must be 3-64 lower-case letters, digits or _')
        out['elevenlabs_model'] = em
    es = voice.get('elevenlabs_settings')
    if es is not None and es != {}:
        es = elevenlabs_settings(es)
        if es:
            out['elevenlabs_settings'] = es
    ss = voice.get('elevenlabs_section_speed')
    if ss is not None and ss != {}:
        ss = section_speed(ss, 'ElevenLabs section speed')
        if ss:
            out['elevenlabs_section_speed'] = ss
    return out


def engine_for(voice, default):
    """The engine a character's lines use: its override when set, else the studio default."""
    eng = (voice or {}).get('engine')
    return eng if eng in ENGINES else default


def imported_elevenlabs(voice):
    """(settings, section speeds) a character's ElevenLabs lines use instead of the emotion defaults, or
    (None, None). Stored values are checked again here; an invalid one counts as absent."""
    voice = voice or {}
    try:
        s = elevenlabs_settings(voice['elevenlabs_settings']) if isinstance(voice.get('elevenlabs_settings'), dict) \
            else {}
        ss = section_speed(voice['elevenlabs_section_speed']) \
            if isinstance(voice.get('elevenlabs_section_speed'), dict) else {}
    except ValueError:
        return None, None
    if not s and not ss:
        return None, None
    return s, ss


def elevenlabs_model(voice, default):
    m = (voice or {}).get('elevenlabs_model')
    return m if isinstance(m, str) and re.fullmatch(ELEVENLABS_MODEL_RE, m) else default


def kept_import(prev, new):
    """The voice.json record survives a save on the Characters page while the voice it describes is still the
    character's ({'voice_import': record}); it is dropped once that voice id is changed."""
    rec = (prev or {}).get('voice_import')
    if not isinstance(rec, dict):
        return {}
    field = {'elevenlabs': 'elevenlabs_voice_id', 'piper': 'piper_speaker', 'kokoro': 'kokoro_voice'}.get(rec.get('engine'))
    cur = (new or {}).get(field) if field else None
    return {'voice_import': rec} if cur not in (None, '') and str(cur) == str(rec.get('voice_id')) else {}


# ------------------------------------------------------------------ voice.json
def load_voice_json(raw):
    """Parse the bytes of an uploaded voice.json (size-capped; NaN and Infinity are refused)."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > VOICE_JSON_MAX_BYTES:
        raise ValueError(f'voice.json must be a JSON file of at most {VOICE_JSON_MAX_BYTES // 1024} KB')

    def bad_constant(_):
        raise ValueError('not a number')
    try:
        return json.loads(bytes(raw).decode('utf-8-sig'), parse_constant=bad_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValueError('voice.json is not valid JSON')


def _no_secrets(node, depth=0, count=None):
    count = count if count is not None else [0]
    count[0] += 1
    if depth > 6 or count[0] > 400:
        raise ValueError('voice.json is too large or too deeply nested')
    if isinstance(node, dict):
        for k, v in node.items():
            if any(w in str(k).lower() for w in SECRET_WORDS):
                raise ValueError(SECRET_REFUSED)
            _no_secrets(v, depth + 1, count)
    elif isinstance(node, list):
        for v in node:
            _no_secrets(v, depth + 1, count)


def _plain(x, limit, name):
    """Short plain text: control characters become spaces; longer than ``limit`` is refused."""
    if not isinstance(x, str):
        raise ValueError(f'{name} must be text')
    x = re.sub(r'[\x00-\x1f\x7f]', ' ', x).strip()
    if len(x) > limit:
        raise ValueError(f'{name} must be at most {limit} characters')
    return x


def _date(x, name):
    if not isinstance(x, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', x):
        raise ValueError(f'{name} must be a date written YYYY-MM-DD')
    try:
        d = datetime.date.fromisoformat(x)
    except ValueError:
        raise ValueError(f'{name} must be a real date (YYYY-MM-DD)')
    if not 2000 <= d.year <= 2100:
        raise ValueError(f'{name} must be a date between 2000 and 2100')
    return x


def _pace(p):
    """Pace targets are kept with the record for reference: names of letters, digits and _, with numbers, short
    lists of numbers or one level of such objects as values."""
    if not isinstance(p, dict) or len(p) > 16:
        raise ValueError('pace must be an object with at most 16 entries')

    def value(v, name, nested=True):
        if isinstance(v, list):
            if len(v) > 4:
                raise ValueError(f'{name} may list at most 4 numbers')
            return [_number(x, 0, 100, name) for x in v]
        if isinstance(v, dict) and nested:
            if len(v) > 8:
                raise ValueError(f'{name} has too many entries')
            return {key(k, name): value(x, f'{name}.{k}', False) for k, x in v.items()}
        return _number(v, 0, 100, name)

    def key(k, where):
        if not re.fullmatch(r'[a-z0-9_]{1,40}', str(k)):
            raise ValueError(f'{where}: names must be lower-case letters, digits or _')
        return str(k)
    return {key(k, 'pace'): value(v, f'pace.{k}') for k, v in p.items()}


def _voice_id(engine, v):
    if engine == 'elevenlabs':
        if not isinstance(v, str) or not re.fullmatch(ELEVENLABS_VOICE_RE, v):
            raise ValueError('voice_id must be an ElevenLabs voice id (8-64 letters and digits)')
        return v
    if engine == 'piper':
        if isinstance(v, str) and v.isdigit() and len(v) <= 4:
            v = int(v)
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < PIPER_SPEAKERS:
            raise ValueError(f'voice_id for Piper must be a speaker number 0-{PIPER_SPEAKERS - 1}')
        return v
    if not isinstance(v, str) or not re.fullmatch(KOKORO_VOICE_RE, v) or v in KOKORO_EXCLUDED:
        raise ValueError('voice_id for Kokoro must be a preset name such as af_heart')
    return v


def parse_voice_json(doc):
    """Validate a voice.json (untrusted). Returns {'engine', 'voice': keys to write into the character's voice
    config, 'clears': keys to remove from it, 'record': the consent/approval record, 'ignored': unknown fields,
    'notes': plain remarks for the preview}. Raises ValueError with a plain reason."""
    if not isinstance(doc, dict):
        raise ValueError('voice.json must be a JSON object')
    _no_secrets(doc)
    engine = doc.get('engine')
    engine = engine.strip().lower() if isinstance(engine, str) else engine
    if engine not in IMPORT_ENGINES:
        raise ValueError('Unknown engine in voice.json: use elevenlabs, piper or kokoro')
    route = doc.get('route', 'library' if engine in FREE_ENGINES else None)
    if route not in ROUTES:
        raise ValueError("voice.json route must be 'library' or 'clone'")
    if route == 'clone' and engine != 'elevenlabs':
        raise ValueError("Free engines have no cloned voices: route must be 'library'")
    voice_id = _voice_id(engine, doc.get('voice_id'))
    record = {'source': 'voice.json', 'engine': engine, 'voice_id': str(voice_id), 'route': route}
    if doc.get('voice_name') is not None:
        record['voice_name'] = _plain(doc['voice_name'], 80, 'voice_name')
    model = doc.get('model')
    if model is not None:
        if not isinstance(model, str) or not re.fullmatch(ELEVENLABS_MODEL_RE, model):
            raise ValueError('model must be 3-64 lower-case letters, digits or _')
    settings = elevenlabs_settings(doc['settings'], 'settings') if doc.get('settings') is not None else {}
    sections = section_speed(doc['section_speed'], 'section_speed') if doc.get('section_speed') is not None else {}
    if doc.get('pace') is not None:
        record['pace'] = _pace(doc['pace'])
    consent = doc.get('consent')
    c = {}
    if consent is not None:
        if not isinstance(consent, dict):
            raise ValueError('consent must be an object')
        if consent.get('route') is not None:
            if consent['route'] not in ROUTES:
                raise ValueError("consent.route must be 'library' or 'clone'")
            if consent['route'] != route:
                raise ValueError('consent.route does not match route')
            c['route'] = consent['route']
        if consent.get('who') is not None:
            c['who'] = _plain(consent['who'], 80, 'consent.who')
        if consent.get('statement') is not None:
            c['statement'] = _plain(consent['statement'], 300, 'consent.statement')
        if consent.get('date') is not None:
            c['date'] = _date(consent['date'], 'consent.date')
    if route == 'clone' and not (c.get('who') and c.get('date')):
        raise ValueError('A cloned voice needs a consent record: consent.who (whose voice it is) and consent.date '
                         '(YYYY-MM-DD).')
    if c:
        record['consent'] = c
    if doc.get('approved_by_dhruv') is not None:
        record['approved_by_dhruv'] = _date(doc['approved_by_dhruv'], 'approved_by_dhruv')
    at = doc.get('applies_to')
    if at is not None:
        at = [at] if isinstance(at, str) else at
        if not isinstance(at, list) or len(at) > 10:
            raise ValueError('applies_to must be a name or a list of up to 10 names')
        record['applies_to'] = [_plain(x, 40, 'applies_to') for x in at]
    notes = []
    if engine == 'elevenlabs':
        voice = {'engine': 'elevenlabs', 'elevenlabs_voice_id': voice_id}
        for k, v in (('elevenlabs_model', model), ('elevenlabs_settings', settings),
                     ('elevenlabs_section_speed', sections)):
            if v:
                voice[k] = v
                record[k.replace('elevenlabs_', '')] = v
        clears = [k for k in ELEVENLABS_KEYS if k not in voice]
    else:
        voice = {'engine': engine, 'piper_speaker' if engine == 'piper' else 'kokoro_voice': voice_id}
        clears = []
        if model or settings or sections:
            notes.append('model, settings and section_speed apply to ElevenLabs voices only; they were checked '
                         'but are not used for a free engine.')
    ignored = [re.sub(r'[^A-Za-z0-9_.-]', '', str(k))[:40] for k in doc if k not in VOICE_JSON_FIELDS][:20]
    return {'engine': engine, 'voice': voice, 'clears': clears, 'record': record, 'ignored': ignored,
            'notes': notes}


def apply_voice_json(voice, parsed, record=None):
    """A character's voice config with an imported voice.json applied (validated again as a whole)."""
    out = {k: v for k, v in (voice or {}).items() if k not in parsed['clears']}
    out.update(parsed['voice'])
    if record is not None:
        out['voice_import'] = record
    clean(out)
    return out


FIELD_LABELS = {'engine': 'Voice engine', 'elevenlabs_voice_id': 'ElevenLabs voice id',
                'elevenlabs_model': 'ElevenLabs model', 'elevenlabs_settings': 'ElevenLabs settings',
                'elevenlabs_section_speed': 'Speed by section', 'piper_speaker': 'Piper speaker',
                'kokoro_voice': 'Kokoro voice'}


def voice_changes(old, new):
    """[{field, label, from, to}] for every voice setting the import changes (the record is shown apart)."""
    old, new = old or {}, new or {}
    keys = [k for k in FIELD_LABELS if k in old or k in new]
    return [{'field': k, 'label': FIELD_LABELS[k], 'from': old.get(k), 'to': new.get(k)}
            for k in keys if old.get(k) != new.get(k)]
