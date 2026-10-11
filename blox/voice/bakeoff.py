"""Voice bake-off: the same 12-line test script read by every candidate voice, measured the same way.

Candidates are our own engines' catalogue voices (Piper LibriTTS speakers, Kokoro presets, and, when the owner
has saved a key and clicks Run, the paid providers' catalogue voices). Each candidate's lines go through the
production voice chain (``tts.synthesize_line``), are measured into a profile (``profiles``) and scored against
the character's target profile when one is set. Only free engines run automatically.
"""
import copy
import json
import re

from .. import config, db as dbmod, prefs as prefsmod, repo, store
from ..util import Blocked, now
from . import kokoro, piper, profiles, speechify, tts, voicecfg

# Twelve fixed lines, five roles. Original lines in the Blox obby world; no brands, no other games.
TEST_SCRIPT = [
    {'id': 'b01', 'role': 'narration', 'kind': 'narration', 'emotion': 'neutral', 'pace': 'normal', 'volume': 'normal',
     'text': 'Day one of the tower run. Three hundred floors, one rope, and a sandwich with a plan.'},
    {'id': 'b02', 'role': 'narration', 'kind': 'narration', 'emotion': 'happy', 'pace': 'normal', 'volume': 'normal',
     'text': 'By floor ninety the rope was gone, the sandwich was gone, and the plan had a new name: panic.'},
    {'id': 'b03', 'role': 'narration', 'kind': 'narration', 'emotion': 'worried', 'pace': 'normal', 'volume': 'normal',
     'text': 'Nobody told them the last platform was painted on.'},
    {'id': 'b04', 'role': 'shout', 'kind': 'dialogue', 'emotion': 'excited', 'pace': 'fast', 'volume': 'shout',
     'text': 'Jump! Jump now! The lava does not wait for anybody!'},
    {'id': 'b05', 'role': 'shout', 'kind': 'dialogue', 'emotion': 'angry', 'pace': 'fast', 'volume': 'loud',
     'text': 'That was my checkpoint! Who moves a checkpoint?!'},
    {'id': 'b06', 'role': 'deadpan_verdict', 'kind': 'dialogue', 'emotion': 'bored', 'pace': 'slow',
     'volume': 'normal', 'text': 'Verdict. The floor is lava. The floor was always lava. Next.'},
    {'id': 'b07', 'role': 'deadpan_verdict', 'kind': 'dialogue', 'emotion': 'neutral', 'pace': 'normal',
     'volume': 'normal', 'text': 'Nine out of ten. The one point is for landing on your face.'},
    {'id': 'b08', 'role': 'kid_line', 'kind': 'dialogue', 'emotion': 'proud', 'pace': 'normal', 'volume': 'normal',
     'text': 'Fixed it. Took four blocks and a snack.'},
    {'id': 'b09', 'role': 'kid_line', 'kind': 'dialogue', 'emotion': 'startled', 'pace': 'fast', 'volume': 'loud',
     'text': 'Wait. Was that door always there?'},
    {'id': 'b10', 'role': 'pause_punchline', 'kind': 'dialogue', 'emotion': 'happy', 'pace': 'normal',
     'volume': 'normal', 'delivery': 'a beat of silence before the last word',
     'text': 'I have climbed a lot of towers in my time. And this one... has stairs.'},
    {'id': 'b11', 'role': 'pause_punchline', 'kind': 'dialogue', 'emotion': 'neutral', 'pace': 'normal',
     'volume': 'normal', 'delivery': 'pause before the punchline',
     'text': 'The shortcut was real. It was just... a shortcut to the start.'},
    {'id': 'b12', 'role': 'narration', 'kind': 'narration', 'emotion': 'happy', 'pace': 'normal', 'volume': 'soft',
     'text': 'They never found the sandwich. The sandwich found them.'},
]
ROLES = ('narration', 'shout', 'deadpan_verdict', 'kid_line', 'pause_punchline')
PAID = {'speechify': speechify.KEY, 'openai': 'OPENAI_API_KEY', 'elevenlabs': 'ELEVENLABS_API_KEY'}


def script_lines(speech_rate):
    return [dict(ln, speech_rate=speech_rate) for ln in TEST_SCRIPT]


def _slug(s):
    return re.sub(r'[^A-Za-z0-9]+', '_', str(s))[:40]


def result_key(cid, engine, voice):
    return f'bakeoff:{cid}:{engine}:{_slug(voice)}'


def candidates(cid, char, p=None, d=None):
    """Candidate voices for a character, free engines first. ``available`` says whether a run can happen now."""
    from .. import vault
    p = p or prefsmod.get(d)
    voice = char.get('voice') or {}
    out = [{'engine': 'piper', 'voice': str(piper.speaker_for(cid, voice)), 'free': True,
            'label': f'Piper LibriTTS speaker {piper.speaker_for(cid, voice)} (current)',
            'available': piper.installed(p['production'].get('piper_model', piper.DEFAULT_MODEL)),
            'license': piper.VOICES[piper.DEFAULT_MODEL]['license']}]
    kinst = kokoro.installed(p['production'].get('kokoro_model', kokoro.DEFAULT_MODEL)) and kokoro.engine_available()
    names = list(kokoro.PRESETS)
    kv = voice.get('kokoro_voice')
    if kv and kv not in names:
        names.insert(0, kv)
    for name in names:
        out.append({'engine': 'kokoro', 'voice': name, 'free': True, 'available': kinst,
                    'label': f'Kokoro {name}' + (' (current)' if name == kokoro.voice_for(cid, voice) else ''),
                    'license': kokoro.MODELS[kokoro.DEFAULT_MODEL]['license']})
    if voice.get('speechify_voice_id'):
        out.append({'engine': 'speechify', 'voice': voice['speechify_voice_id'], 'free': False,
                    'available': vault.configured(speechify.KEY), 'label': f'Speechify {voice["speechify_voice_id"]}',
                    'license': 'Speechify account terms'})
    if voice.get('openai_voice'):
        out.append({'engine': 'openai', 'voice': voice['openai_voice'], 'free': False,
                    'available': vault.configured('OPENAI_API_KEY'), 'label': f'OpenAI {voice["openai_voice"]}',
                    'license': 'OpenAI terms'})
    if voice.get('elevenlabs_voice_id'):
        out.append({'engine': 'elevenlabs', 'voice': voice['elevenlabs_voice_id'], 'free': False,
                    'available': vault.configured('ELEVENLABS_API_KEY'),
                    'label': f'ElevenLabs {voice["elevenlabs_voice_id"]}', 'license': 'ElevenLabs terms'})
    return out


def voice_override(voice, engine, voice_name):
    """The character's voice config with the candidate voice set for ``engine``."""
    v = dict(voice or {})
    v['engine'] = engine
    if engine == 'piper':
        v['piper_speaker'] = int(voice_name)
    elif engine == 'kokoro':
        v['kokoro_voice'] = str(voice_name)
    elif engine == 'speechify':
        v['speechify_voice_id'] = str(voice_name)
    elif engine == 'openai':
        v['openai_voice'] = str(voice_name)
    elif engine == 'elevenlabs':
        v['elevenlabs_voice_id'] = str(voice_name)
    else:
        raise ValueError('Unknown engine')
    return v


def run_candidate(cid, char, engine, voice_name, paid_ok=False, p=None, d=None):
    """Read the test script with one candidate, measure it and store the result. Paid engines only run when the
    owner asked for this candidate explicitly (``paid_ok``) and the key is saved; nothing is sent otherwise."""
    from .. import vault
    d = d or dbmod.get()
    p = copy.deepcopy(p or prefsmod.get(d))
    if engine not in voicecfg.ENGINES:
        raise ValueError('Unknown engine')
    if engine in PAID:
        if not paid_ok:
            raise Blocked(f'{engine} is a paid provider: run it with the explicit paid option', state='needs_review')
        if not vault.configured(PAID[engine]):
            raise Blocked(f'{engine} needs its API key on Connections before it can run', state='needs_credentials')
    voice = voice_override(char.get('voice'), engine, voice_name)
    character = {'id': cid, 'name': char.get('name', cid), 'voice': voice}
    p['production']['tts_provider'] = engine
    from ..manifest import compile as C
    rate = float(C.pace_kwargs({'production': p['production']})['speech_rate'])
    # Under character_previews so the owner UI can play the lines through the existing /media/ rule.
    out_dir = config.WORK_DIR / 'character_previews' / 'bakeoff' / cid / f'{engine}_{_slug(voice_name)}'
    out_dir.mkdir(parents=True, exist_ok=True)
    lines, items = [], []
    if engine == 'kokoro':
        # One runner process for the whole script (the model loads once); synthesize_line then reuses the raw
        # files, which carry the same spec hash it would compute itself.
        reqs = []
        for ln in script_lines(rate):
            ls = tts.line_spec(ln, character, p['production'])
            reqs.append((ls['say'], str(out_dir / ls['raw']), ls['spec']['kokoro'][1], ln, voice))
        kokoro.synthesize_batch(reqs, p['production'].get('kokoro_model', kokoro.DEFAULT_MODEL))
    for ln in script_lines(rate):
        res = tts.synthesize_line(ln, character, p, str(out_dir), f'bakeoff:{cid}', use_asr=False)
        lines.append({'id': ln['id'], 'role': ln['role'], 'text': ln['text'], 'file': repo.rel(res['file']),
                      'duration_s': res['duration_s'], 'alignment_kind': res['alignment_kind'],
                      'words_per_s': round(len(profiles.WORD_RE.findall(ln['text'])) / max(0.05, res['duration_s']), 2)})
        items.append((res['file'], ln['text']))
    measured = profiles.measure_files(items)
    target = None
    tp = (char.get('voice') or {}).get('target_profile')
    if tp:
        target = profiles.get(tp, d)
    score, comps = (profiles.match_score(measured, target['measured']) if target else (None, {}))
    result = {'character': cid, 'engine': engine, 'voice': str(voice_name), 'free': engine not in PAID,
              'speech_rate': rate, 'lines': lines, 'measured': measured, 'score': score, 'score_components': comps,
              'target_profile': (target or {}).get('id'), 'at': now(),
              'license': (piper.VOICES[piper.DEFAULT_MODEL]['license'] if engine == 'piper'
                          else kokoro.MODELS[kokoro.DEFAULT_MODEL]['license'] if engine == 'kokoro' else f'{engine} terms')}
    store.put(result_key(cid, engine, voice_name), result, d)
    return result


def results(cid, d=None):
    d = d or dbmod.get()
    rows = d.query('SELECT key, value FROM settings WHERE key LIKE ?', (f'bakeoff:{cid}:%',))
    out = [json.loads(r['value']) for r in rows]
    return sorted(out, key=lambda r: (r.get('score') is None, -(r.get('score') or 0), r['engine'], r['voice']))


def rescore(cid, char, d=None):
    """Re-score stored results against the character's current target profile (no synthesis)."""
    d = d or dbmod.get()
    tp = (char.get('voice') or {}).get('target_profile')
    target = profiles.get(tp, d) if tp else None
    out = []
    for r in results(cid, d):
        score, comps = profiles.match_score(r['measured'], target['measured']) if target else (None, {})
        if score != r.get('score') or r.get('target_profile') != (target or {}).get('id'):
            r.update(score=score, score_components=comps, target_profile=(target or {}).get('id'))
            store.put(result_key(cid, r['engine'], r['voice']), r, d)
        out.append(r)
    return out


def use_voice(cid, char, engine, voice_name, d=None):
    """Write the candidate into the character's voice config (engine override plus the engine's voice key)."""
    d = d or dbmod.get()
    voice = voice_override(char.get('voice'), engine, voice_name)
    voice.update(voicecfg.clean(voice))
    repo.save_character(cid, char['name'], char['bible'], voice, bool(char.get('active', True)), d)
    store.audit('voice_chosen', {'id': cid, 'engine': engine, 'voice': str(voice_name)}, actor='owner', d=d)
    return voice
