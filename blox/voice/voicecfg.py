"""Validation of the per-character voice settings added with the voice base.

engine            optional per-character engine override ('' = the studio's tts_provider).
kokoro_voice      Kokoro preset name (see blox.voice.kokoro.PRESETS); kokoro_speed 0.5-2.0.
speechify_voice_id  a voice id from the owner's Speechify account (paid; needs the key on Connections).
target_profile    id of a voice profile (voice_profiles table) the bake-off scores candidates against.
"""
import math
import re

from .kokoro import EXCLUDED as KOKORO_EXCLUDED, SPEED_RANGE, VOICE_RE as KOKORO_VOICE_RE

ENGINES = ('piper', 'kokoro', 'speechify', 'openai', 'elevenlabs')
FREE_ENGINES = ('piper', 'kokoro')
PROFILE_RE = r'vp_[A-Za-z0-9_-]{4,40}'
SPEECHIFY_VOICE_RE = r'[A-Za-z0-9_\-]{1,64}'


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
    return out


def engine_for(voice, default):
    """The engine a character's lines use: its override when set, else the studio default."""
    eng = (voice or {}).get('engine')
    return eng if eng in ENGINES else default
