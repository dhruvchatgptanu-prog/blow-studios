"""Voice base endpoints: engines status, voice profiles (measure reference clips), the bake-off, and the import
of the owner's voice.json into a character's voice."""
import re

from flask import Blueprint, abort, jsonify, request

from .. import db as dbmod, prefs as prefsmod, repo, store, vault
from ..timeutil import iso_utc
from ..util import now
from ..voice import bakeoff, kokoro, piper, profiles, speechify, voicecfg

bp = Blueprint('voicebase', __name__)


@bp.get('/api/voice-engines')
def voice_engines():
    p = prefsmod.get()
    return jsonify(engines={
        'piper': {'free': True, 'installed': piper.installed(p['production'].get('piper_model', piper.DEFAULT_MODEL)),
                  'license': piper.VOICES[piper.DEFAULT_MODEL]['license']},
        'kokoro': {'free': True, 'installed': kokoro.installed(), 'package': kokoro.engine_available(),
                   'license': kokoro.MODELS[kokoro.DEFAULT_MODEL]['license'], 'presets': list(kokoro.PRESETS),
                   'speed_range': list(kokoro.SPEED_RANGE)},
        'speechify': {'free': False, 'configured': vault.configured(speechify.KEY), 'models': list(speechify.MODELS)},
        'openai': {'free': False, 'configured': vault.configured('OPENAI_API_KEY')},
        'elevenlabs': {'free': False, 'configured': vault.configured('ELEVENLABS_API_KEY')},
    }, default=p['production']['tts_provider'], choices=list(voicecfg.ENGINES),
        profiles=[{'id': r['id'], 'name': r['name'], 'source': r['source'], 'measured': r['measured'],
                   'created_at': r['created_at']} for r in profiles.list_profiles()],
        script=[{'id': ln['id'], 'role': ln['role'], 'text': ln['text']} for ln in bakeoff.TEST_SCRIPT])


@bp.post('/api/voice-engines/kokoro/install')
def kokoro_install():
    """Download the pinned Kokoro model files (GitHub release only, checksum verified)."""
    path = kokoro.install()
    store.audit('kokoro_installed', {'dir': str(path)}, actor='owner')
    return jsonify(ok=True, installed=kokoro.installed(), package=kokoro.engine_available())


@bp.get('/api/voice-profiles')
def list_voice_profiles():
    return jsonify(profiles=profiles.list_profiles())


@bp.post('/api/voice-profiles/measure')
def measure_voice_profiles():
    """Measure uploaded reference clips (files or a zip, multipart) into a profile; the audio is deleted."""
    uploads = [(f.filename or 'clip', f.read()) for f in request.files.getlist('files') if f and f.filename]
    if not uploads:
        raise ValueError('Choose audio files or a zip of clips')
    name = request.form.get('name', '')
    source = str(request.form.get('source', ''))[:300] or 'owner upload'
    created = profiles.import_upload(uploads, name, source)
    store.audit('voice_profiles_measured', {'ids': [c['id'] for c in created], 'clips': sum(c['measured'].get('clips', 0)
                                                                                           for c in created)},
                actor='owner')
    return jsonify(profiles=created)


@bp.delete('/api/voice-profiles/<pid>')
def delete_voice_profile(pid):
    if not profiles.get(pid):
        abort(404)
    profiles.delete(pid)
    store.audit('voice_profile_deleted', {'id': pid}, actor='owner')
    return jsonify(ok=True)


@bp.post('/api/voice-profiles/score')
def score_profiles():
    """Match score between two stored profiles (candidate vs target)."""
    body = request.get_json(force=True) or {}
    a, b = profiles.get(str(body.get('candidate', ''))), profiles.get(str(body.get('target', '')))
    if not a or not b:
        abort(404)
    score, comps = profiles.match_score(a['measured'], b['measured'])
    return jsonify(score=score, components=comps)


def _character(cid):
    chars = repo.characters(active_only=False)
    if cid not in chars:
        abort(404)
    return chars[cid]


@bp.get('/api/voice-bakeoff/<cid>')
def bakeoff_view(cid):
    char = _character(cid)
    d = dbmod.get()
    return jsonify(character={'id': cid, 'name': char['name'], 'voice': char['voice']},
                   candidates=bakeoff.candidates(cid, char, d=d), results=bakeoff.rescore(cid, char, d),
                   script=[{'id': ln['id'], 'role': ln['role'], 'text': ln['text']} for ln in bakeoff.TEST_SCRIPT])


@bp.post('/api/voice-bakeoff/<cid>/run')
def bakeoff_run(cid):
    """Read the test script with one candidate. Free engines run on request; a paid engine runs only with
    ``paid: true`` in the body (the explicit click) and its key saved."""
    char = _character(cid)
    body = request.get_json(force=True) or {}
    engine = str(body.get('engine', ''))
    voice = str(body.get('voice', ''))
    if engine not in voicecfg.ENGINES or not voice:
        raise ValueError('engine and voice are required')
    if engine == 'piper':
        if not voice.isdigit() or not 0 <= int(voice) < piper.VOICES[piper.DEFAULT_MODEL]['speakers']:
            raise ValueError('Piper voice is a speaker number 0-903')
    else:
        voicecfg.clean({{'kokoro': 'kokoro_voice', 'speechify': 'speechify_voice_id'}.get(engine, 'x'): voice})
    res = bakeoff.run_candidate(cid, char, engine, voice, paid_ok=bool(body.get('paid') is True))
    store.audit('voice_bakeoff_run', {'id': cid, 'engine': engine, 'voice': voice}, actor='owner')
    return jsonify(result=res)


@bp.post('/api/voice-bakeoff/<cid>/use')
def bakeoff_use(cid):
    char = _character(cid)
    body = request.get_json(force=True) or {}
    engine, voice = str(body.get('engine', '')), str(body.get('voice', ''))
    if engine not in voicecfg.ENGINES or not voice:
        raise ValueError('engine and voice are required')
    if engine == 'piper' and not voice.isdigit():
        raise ValueError('Piper voice is a speaker number')
    v = bakeoff.use_voice(cid, char, engine, voice)
    return jsonify(ok=True, voice=v)


@bp.get('/api/voice-engines/speechify/voices')
def speechify_voices():
    """The owner's Speechify catalogue; only on an explicit click and only with the key saved."""
    if not vault.configured(speechify.KEY):
        return jsonify(error='Save the Speechify API key on Connections first'), 400
    return jsonify(voices=speechify.list_voices())


# ------------------------------------------------------------------ voice.json import (owner action)
CHARACTER_RE = r'ch_[a-z0-9_]{2,30}'


def _voice_json_input():
    """(character id, character row, parsed voice.json) from a multipart upload (``file`` + ``character``) or a
    JSON body {"character": "ch_...", "voice_json": {...}}. Everything in the file is untrusted: size-capped,
    parsed without NaN/Infinity and validated by voicecfg.parse_voice_json."""
    if request.files or (request.mimetype or '').startswith('multipart/'):
        f = request.files.get('file')
        if not f or not f.filename:
            raise ValueError('Choose a voice.json file')
        doc = voicecfg.load_voice_json(f.read(voicecfg.VOICE_JSON_MAX_BYTES + 1))
        cid = request.form.get('character', '')
    else:
        if (request.content_length or 0) > 2 * voicecfg.VOICE_JSON_MAX_BYTES:
            raise ValueError(f'voice.json must be at most {voicecfg.VOICE_JSON_MAX_BYTES // 1024} KB')
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError('Send a voice.json file, or a JSON body with character and voice_json')
        cid, doc = body.get('character', ''), body.get('voice_json')
    if not isinstance(cid, str) or not re.fullmatch(CHARACTER_RE, cid):
        raise ValueError('Choose the character this voice is for')
    char = _character(cid)
    return cid, char, voicecfg.parse_voice_json(doc)


def _import_view(cid, char, parsed, new_voice):
    return {'character': {'id': cid, 'name': char['name']},
            'changes': voicecfg.voice_changes(char.get('voice') or {}, new_voice),
            'record': parsed['record'], 'ignored': parsed['ignored'], 'notes': parsed['notes']}


@bp.post('/api/voice-json/preview')
def voice_json_preview():
    """What importing this voice.json would change on the character (nothing is saved)."""
    cid, char, parsed = _voice_json_input()
    new_voice = voicecfg.apply_voice_json(char.get('voice') or {}, parsed)
    return jsonify(**_import_view(cid, char, parsed, new_voice))


@bp.post('/api/voice-json/apply')
def voice_json_apply():
    """Write the voice.json into the character's voice (engine, voice id, ElevenLabs model, settings and speed
    by section) and keep its consent/approval record with it (read-only on Characters). Audit-logged."""
    cid, char, parsed = _voice_json_input()
    record = dict(parsed['record'], imported_at=iso_utc(now()))
    new_voice = voicecfg.apply_voice_json(char.get('voice') or {}, parsed, record)
    view = _import_view(cid, char, parsed, new_voice)
    repo.save_character(cid, char['name'], char.get('bible') or {}, new_voice, bool(char.get('active', 1)))
    rec = parsed['record']
    store.audit('voice_json_imported', {'id': cid, 'engine': rec['engine'], 'voice_id': rec['voice_id'],
                                        'route': rec['route'], 'consent_date': (rec.get('consent') or {}).get('date'),
                                        'approved_by_dhruv': rec.get('approved_by_dhruv'),
                                        'changed': [c['field'] for c in view['changes']]}, actor='owner')
    return jsonify(ok=True, voice=new_voice, **view)
