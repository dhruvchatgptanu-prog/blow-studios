"""Story formats, and the calibration of the first-person story format.

A plan may name its format (plan['format']): 'skit' (on-screen dialogue), 'storytime' (narrated) or
'first_person_story': an ordinary "I" does one outrageous thing and it snowballs in about 25-45 s. A plan
without a format keeps the behaviour from before formats existed.

The first-person format is checked against a calibration: target lengths, narration pace (overall and by
section: a quicker hook, a slower ending), the longest shot, title length and where the mid-video gag goes.
The defaults below come from the owner's own measured scripts. The owner can import new numbers measured on
his own computer (calibration.json and pace_profile.json); every imported value is untrusted, checked for
type and range, and anything unknown or out of range is dropped (and reported back) instead of stored.
"""
import copy
import math
import re
import time

from .. import untrusted

FORMATS = ('skit', 'storytime', 'first_person_story')
FIRST_PERSON = 'first_person_story'
MODES = ('world_scale', 'grounded')
LOOP_TYPES = ('callback', 'sequel_tease')
MECHANISMS = ('impossible_opponent', 'audience_control', 'constraint_curse', 'everyday_accident', 'rescue_mystery',
              'identity_lie', 'world_status', 'crime_reversal', 'taboo_vs_wholesome', 'self_involvement',
              'absurd_belief', 'words_become_real', 'mild_mischief', 'supernatural')
TWIST_TYPES = ('lie_becomes_true', 'backfire', 'role_flip', 'scale_flip', 'hidden_reason', 'narrator_exposed',
               'karma_loop', 'unexpected_rescuer', 'authority_from_home')

# Short descriptions for the story writer (plain English, no examples copied from anyone's videos).
MODE_NOTES = {
    'world_scale': 'a superlative feat or object (the strongest, the biggest, every single one)',
    'grounded': 'an everyday situation with a social or moral sting; the reversal can sit in the title',
}
MECHANISM_NOTES = {
    'impossible_opponent': 'the narrator takes on someone or something they cannot possibly beat',
    'audience_control': 'the viewers decide or change what happens to the narrator',
    'constraint_curse': 'a rule, limit or curse the narrator has to live with',
    'everyday_accident': 'a small mistake or broken rule that snowballs',
    'rescue_mystery': 'someone is rescued, or rescues the narrator, and the reason is the mystery',
    'identity_lie': 'the narrator pretends to be someone they are not',
    'world_status': 'the narrator suddenly owns, rules or is ranked first in something huge',
    'crime_reversal': 'a crime, debt or role swap that turns around on the narrator',
    'taboo_vs_wholesome': 'something that sounds bad turns out wholesome (or the other way round)',
    'self_involvement': 'the narrator meets or deals with another version of themself',
    'absurd_belief': 'the narrator believes, or refuses to believe, something absurd',
    'words_become_real': 'what the narrator says comes true, literally',
    'mild_mischief': 'a harmless prank or bit of mischief that gets out of hand',
    'supernatural': 'a fantasy or supernatural premise played completely straight',
}
TWIST_NOTES = {
    'lie_becomes_true': 'the lie the narrator told turns out to be true',
    'backfire': 'the plan works, and that is the problem',
    'role_flip': 'the narrator and the other side swap places',
    'scale_flip': 'the huge thing turns tiny, or the tiny thing turns huge',
    'hidden_reason': 'the strange behaviour had a reason all along',
    'narrator_exposed': 'the narrator was the cause (or the culprit) the whole time',
    'karma_loop': 'what the narrator did comes straight back to them',
    'unexpected_rescuer': 'help arrives from the least likely person',
    'authority_from_home': 'the real authority turns out to be someone from home (a parent, a sibling)',
}

# The owner's playbook measured on his own scripts. Times are finished-video seconds.
DEFAULTS = {
    'duration_s': {'min': 25.0, 'target': 34.0, 'max': 45.0},
    'wps': {'min': 4.0, 'target': 4.19, 'max': 4.45},
    'wps_sections': {'hook': [4.35, 4.75], 'body': [4.0, 4.4], 'end': [3.7, 4.05]},
    'beat_wps_max': 5.1,
    'hook_max_s': 2.4,
    'max_shot_s': 3.5,
    'title_words': {'min': 3, 'max': 9},
    'mid_gag_window': [0.35, 0.70],
    'mid_gag_max_s': 2.5,
    'first_word_s': 0.0,
    'winning_mechanisms': [],
    'source': 'defaults',
    'imported_at': None,
}
FIRST_WORD_TOLERANCE_S = 0.3
TWIST_DROPOUT_S = 0.5           # finished seconds of music silence just before the twist
# Optional story labels a plan carries in plan['story'] (compile copies them into m['story']).
STORY_KEYS = ('mechanism', 'mode', 'twist_type', 'loop_type')
SECTION_HALF_WIDTH = 0.2        # a measured section pace v becomes the band [v - 0.2, v + 0.2]
STORE_KEY = 'format_calibration'
MAX_CHANNELS = 20
MAX_MECHANISM_ITEMS = 50

# Allowed ranges of every calibration value (anything outside is dropped).
RANGES = {
    'duration_s': (10.0, 180.0), 'wps': (2.0, 6.0), 'wps_sections': (2.0, 6.0), 'beat_wps_max': (2.0, 8.0),
    'hook_max_s': (0.5, 6.0), 'max_shot_s': (1.0, 10.0), 'title_words': (1, 20), 'mid_gag_window': (0.05, 0.95),
    'mid_gag_max_s': (0.5, 8.0), 'first_word_s': (0.0, 3.0),
}
SECTIONS = ('hook', 'body', 'end')
# pace_profile.json section names -> line sections (blox.voice.sections).
PACE_PROFILE_SECTIONS = {'hook_0_8s': 'hook', 'body': 'body', 'last_15s': 'end'}
# Mechanism names used by the owner's capture script -> this format's mechanisms.
MECHANISM_ALIASES = {
    'crime_then_reversal': 'crime_reversal',
    'role_swap_or_debt': 'crime_reversal',
    'crime_or_rulebreak': 'everyday_accident',
    'rescue_or_hero': 'rescue_mystery',
    'absurd_belief_or_refusal': 'absurd_belief',
    'constraint_or_curse': 'constraint_curse',
    'supernatural_fantasy': 'supernatural',
}

# Title grammar: "I [past-tense verb] [one extreme object] 😳".
TITLE_EMOJI = '\U0001F633'
_EMOJI = re.compile('[\U0001F000-\U0001FAFF☀-➿⬀-⯿]')
_WORD = re.compile(r"[A-Za-z0-9']+")
NARRATOR_WORDS = {'i', 'me', 'my', 'mine', 'myself', "i'm", "i've", "i'd", "i'll"}
LIKE_SUB = re.compile(r'\b(like|likes|subscribe|subscribed|subscribing|sub|subs)\b', re.I)
STOPWORDS = {
    'a', 'an', 'the', 'and', 'or', 'but', 'so', 'to', 'of', 'in', 'on', 'at', 'for', 'with', 'from', 'by', 'it',
    'is', 'was', 'were', 'be', 'been', 'am', 'are', 'that', 'this', 'then', 'than', 'just', 'i', 'me', 'my',
    'you', 'your', 'he', 'she', 'they', 'we', 'him', 'her', 'his', 'their', 'our', 'its', "it's", 'not', 'no',
    'yes', 'did', 'do', 'does', 'had', 'have', 'has', 'what', 'who', 'all', 'up', 'out', 'now', 'again', 'there',
    'here', 'one', "i'm", "i've", "i'd", "i'll", 'very', 'too', 'oh', 'ok', 'okay',
}


def words(text):
    return _WORD.findall(str(text or '').replace('’', "'"))


def title_report(title):
    """Title grammar facts: word count, emoji, the 😳 at the end, the narrator, hashtags."""
    t = str(title or '')
    ws = [w.lower() for w in words(t)]
    stripped = t.rstrip()
    return {'words': len(ws), 'has_emoji': bool(_EMOJI.search(t)), 'has_flushed': TITLE_EMOJI in t,
            'flushed_last': stripped.endswith(TITLE_EMOJI), 'narrator': any(w in NARRATOR_WORDS for w in ws),
            'hashtags': bool(re.search(r'#\w', t)), 'title_words': ws}


def content_words(text):
    """Words that carry meaning (stopwords dropped, a plural 's' folded) for the loop-callback comparison."""
    out = set()
    for w in words(text):
        w = w.lower()
        if w in STOPWORDS or len(w) < 2:
            continue
        if w.endswith("'s"):
            w = w[:-2]
        elif len(w) > 3 and w.endswith('s') and not w.endswith('ss'):
            w = w[:-1]
        out.add(w)
    return out


# ------------------------------------------------------------------ validation of calibration values
def _num(v, lo, hi):
    """A finite number inside [lo, hi] (booleans and strings are refused), else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    x = float(v)
    if not math.isfinite(x) or not lo <= x <= hi:
        return None
    return round(x, 3)


def _note(dropped, msg):
    if dropped is not None and len(dropped) < 60:
        dropped.append(msg[:200])


def _r(v):
    """A short, safe rendering of an imported value for a message."""
    if isinstance(v, dict):
        return 'an object'
    if isinstance(v, (list, tuple)) and len(v) > 4:
        return 'a list'
    return _safe(repr(list(v) if isinstance(v, tuple) else v), 40)


def _bounded_dict(name, raw, keys, dropped, integer=False):
    """{min[, target], max} inside RANGES[name], ordered, missing parts filled from the defaults."""
    lo, hi = RANGES[name]
    base = DEFAULTS[name]
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and 'target' in keys:
        v = _num(raw, lo, hi)
        if v is None:
            _note(dropped, f'{name} {_r(raw)} is outside {lo:g}-{hi:g}')
            return None
        # A single number is the target. A length keeps the default range around it; a pace keeps the
        # default spread (as a fraction of the target).
        if name == 'duration_s':
            return {'min': min(base['min'], v), 'target': v, 'max': max(base['max'], v)}
        return {'min': max(lo, round(v * base['min'] / base['target'], 3)), 'target': v,
                'max': min(hi, round(v * base['max'] / base['target'], 3))}
    if not isinstance(raw, dict):
        _note(dropped, f'{name} is not a set of numbers')
        return None
    out = {}
    for k in keys:
        if k not in raw:
            out[k] = base[k]
            continue
        v = _num(raw[k], lo, hi)
        if v is None or (integer and v != int(v)):
            _note(dropped, f'{name}.{k} {_r(raw[k])} is outside {lo:g}-{hi:g}' if v is None else
                  f'{name}.{k} must be a whole number')
            return None
        out[k] = int(v) if integer else v
    vals = [out[k] for k in keys]
    if vals != sorted(vals):
        _note(dropped, f'{name} values are not in order ({", ".join(keys)})')
        return None
    return out


def _band(name, raw, dropped):
    lo, hi = RANGES[name]
    if not (isinstance(raw, (list, tuple)) and len(raw) == 2):
        _note(dropped, f'{name} must be a pair [low, high]')
        return None
    a, b = _num(raw[0], lo, hi), _num(raw[1], lo, hi)
    if a is None or b is None or a > b:
        _note(dropped, f'{name} {_r(list(raw))} is not an ordered pair inside {lo:g}-{hi:g}')
        return None
    return [a, b]


def _mechanisms(raw, dropped):
    if not isinstance(raw, (list, tuple)):
        _note(dropped, 'winning_mechanisms is not a list')
        return None
    out = []
    for item in list(raw)[:MAX_MECHANISM_ITEMS]:
        if isinstance(item, dict):
            item = item.get('mechanism', item.get('name'))
        if not isinstance(item, str):
            continue
        key = item.strip().lower()[:60]
        key = MECHANISM_ALIASES.get(key, key)
        if key not in MECHANISMS:
            _note(dropped, f'mechanism "{_safe(item, 40)}" is not one this format knows')
            continue
        if key not in out:
            out.append(key)
    return out


def _safe(text, limit):
    return untrusted.clean(text, limit)


def _scalar(name, raw, dropped):
    lo, hi = RANGES[name]
    v = _num(raw, lo, hi)
    if v is None:
        _note(dropped, f'{name} {_r(raw)} is outside {lo:g}-{hi:g}')
    return v


def canonical(values, dropped=None):
    """Keep only valid calibration keys of the stored shape (DEFAULTS keys), checked again."""
    if not isinstance(values, dict):
        return {}
    out = {}
    for name in ('duration_s', 'wps'):
        if name in values:
            v = _bounded_dict(name, values[name], ('min', 'target', 'max'), dropped)
            if v is not None:
                out[name] = v
    if 'title_words' in values:
        v = _bounded_dict('title_words', values['title_words'], ('min', 'max'), dropped, integer=True)
        if v is not None:
            out['title_words'] = v
    if isinstance(values.get('wps_sections'), dict):
        secs = {}
        for s in SECTIONS:
            if s in values['wps_sections']:
                b = _band('wps_sections', values['wps_sections'][s], dropped)
                if b is not None:
                    secs[s] = b
        if secs:
            out['wps_sections'] = secs
    if 'mid_gag_window' in values:
        b = _band('mid_gag_window', values['mid_gag_window'], dropped)
        if b is not None and b[0] < b[1]:
            out['mid_gag_window'] = b
    for name in ('beat_wps_max', 'hook_max_s', 'max_shot_s', 'mid_gag_max_s', 'first_word_s'):
        if name in values:
            v = _scalar(name, values[name], dropped)
            if v is not None:
                out[name] = v
    if 'winning_mechanisms' in values:
        v = _mechanisms(values['winning_mechanisms'], dropped)
        if v is not None:
            out['winning_mechanisms'] = v
    if isinstance(values.get('source'), str):
        out['source'] = _safe(values['source'], 120)
    ts = values.get('imported_at')
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and math.isfinite(ts) and ts > 0:
        out['imported_at'] = float(ts)
    return out


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k == 'wps_sections' and isinstance(v, dict):
            out[k] = dict(out.get(k) or {}, **copy.deepcopy(v))
        else:
            out[k] = copy.deepcopy(v)
    return out


def merged(values, base=None):
    """``values`` (already cleaned) laid over ``base`` (default: the defaults)."""
    return _merge(base if base is not None else DEFAULTS, canonical(values))


# ------------------------------------------------------------------ import from the owner's files
def calibration_channels(raw):
    """Channel names of a per-channel calibration.json (capture shape), else []."""
    if not isinstance(raw, dict) or not isinstance(raw.get('per_channel'), dict):
        return []
    return [k for k in raw['per_channel'] if isinstance(k, str) and 0 < len(k) <= 100][:MAX_CHANNELS]


def clean_calibration(raw, channel=None, dropped=None):
    """Validated calibration values from an imported calibration.json.

    Two shapes are accepted: ``{"per_channel": {"<channel>": {...}}}`` (the capture script; ``channel`` picks
    one, and may be left out when there is only one) and the flat shape of the pace script. Mapped keys:
    duration_s, wps, first_word_s, max_shot_s, title_words, winning_mechanisms, beat_wps_max. Everything
    else (free-text notes, other creators' data) is ignored; out-of-range values and unknown mechanisms are
    dropped and described in ``dropped``."""
    if not isinstance(raw, dict):
        raise ValueError('calibration.json must hold a JSON object')
    body = raw
    if 'per_channel' in raw:
        names = calibration_channels(raw)
        if not names:
            raise ValueError('calibration.json has no usable channels')
        if channel is None and len(names) == 1:
            channel = names[0]
        if not isinstance(channel, str) or channel not in names:
            raise ValueError('Choose which channel of calibration.json to use')
        body = raw['per_channel'][channel]
        if not isinstance(body, dict):
            raise ValueError('That channel has no calibration values')
    picked = {k: body[k] for k in ('duration_s', 'wps', 'first_word_s', 'max_shot_s', 'title_words',
                                   'winning_mechanisms', 'beat_wps_max') if k in body}
    return canonical(picked, dropped)


def clean_pace_profile(raw, dropped=None):
    """Section pace bands from an imported pace_profile.json: wps_by_section {hook_0_8s, body, last_15s} each
    become [v - 0.2, v + 0.2] for the hook, body and end sections. Other keys are ignored."""
    if not isinstance(raw, dict):
        raise ValueError('pace_profile.json must hold a JSON object')
    by = raw.get('wps_by_section')
    if not isinstance(by, dict):
        _note(dropped, 'pace_profile.json has no wps_by_section')
        return {}
    lo, hi = RANGES['wps_sections']
    secs = {}
    for key, sec in PACE_PROFILE_SECTIONS.items():
        if key not in by:
            continue
        v = by[key]
        if isinstance(v, dict):
            v = v.get('median', v.get('wps'))
        x = _num(v, lo, hi)
        if x is None:
            _note(dropped, f'wps_by_section.{key} {_r(v)} is outside {lo:g}-{hi:g}')
            continue
        secs[sec] = [round(max(lo, x - SECTION_HALF_WIDTH), 2), round(min(hi, x + SECTION_HALF_WIDTH), 2)]
    return {'wps_sections': secs} if secs else {}


# ------------------------------------------------------------------ stored calibration
def _stored(d=None):
    from .. import store
    raw = store.get(STORE_KEY, {}, d)
    return raw if isinstance(raw, dict) else {}


def calibration(d=None):
    """The first_person_story calibration: the owner's imported values laid over DEFAULTS. Falls back to the
    defaults when nothing was imported or the store cannot be read."""
    try:
        own = _stored(d).get(FIRST_PERSON)
    except Exception:  # no database (tools, render hosts): the defaults apply
        own = None
    return _merge(DEFAULTS, canonical(own)) if isinstance(own, dict) else copy.deepcopy(DEFAULTS)


def save_calibration(cal, source, d=None):
    """Store cleaned calibration values (over any earlier import) with where they came from and when."""
    clean = canonical(cal)
    for k in ('source', 'imported_at'):
        clean.pop(k, None)
    if not clean:
        raise ValueError('Nothing to save: the files had no usable values')
    from .. import store
    stored = _stored(d)
    prev = stored.get(FIRST_PERSON) if isinstance(stored.get(FIRST_PERSON), dict) else {}
    new = _merge(canonical(prev), clean)
    new['source'] = _safe(source, 120) or 'owner import'
    new['imported_at'] = time.time()
    stored = dict(stored)
    stored[FIRST_PERSON] = new
    store.put(STORE_KEY, stored, d)
    return calibration(d)


def reset_calibration(d=None):
    from .. import store
    stored = dict(_stored(d))
    stored.pop(FIRST_PERSON, None)
    store.put(STORE_KEY, stored, d)
    return calibration(d)
