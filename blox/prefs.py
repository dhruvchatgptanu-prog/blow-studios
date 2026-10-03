"""Studio preferences: defaults, validation and storage.

Everything the owner can change from the UI lives here. Values are validated
on every write; workers re-read them on each step so a pause or emergency stop
takes effect at the next checkpoint.
"""
import copy
import json
import re
from zoneinfo import ZoneInfo

from . import db as dbmod
from .util import finite, integer, text

KEY = 'studio_prefs'

DEFAULT_PRONUNCIATIONS = {
    'Roblox': 'Roh-blocks',
    'obby': 'obbee',
    'Obby': 'Obbee',
    'Robux': 'Roh-bucks',
    'noob': 'noob',
}

DEFAULTS = {
    'autopilot': {
        'enabled': False,
        # review: produce + QA, then wait for owner approval to schedule.
        # autopilot: QA-approved videos are scheduled without per-video approval.
        'mode': 'review',
        'paused': False,
        'emergency_stop': False,
        'activated_at': None,
        'pause_reason': '',
    },
    'schedule': {
        'timezone': 'Australia/Adelaide',
        'interval_minutes': 120,
        'anchor_local': '00:00',
        # local_wall_clock: slots at fixed local times (00:00, 02:00, ...).
        #   DST gap -> that local time does not exist and no slot is created.
        #   DST overlap -> only the first occurrence is used.
        # fixed_interval_utc: every N minutes from the anchor in UTC; local
        #   labels shift by an hour across DST changes.
        'policy': 'local_wall_clock',
        'max_per_rolling_24h': 12,
        'upload_lead_minutes': 180,
        'min_lead_minutes': 45,
        'horizon_hours': 36,
        'buffer_target': 3,
        'max_in_production': 3,
    },
    'publishing': {
        'made_for_kids': None,
        'synthetic_disclosure': None,
        'category_id': '20',
        'default_language': 'en',
        'tags': ['roblox', 'animation', 'shorts', 'obby', 'story'],
        'description_footer': ('Original animated story with Roblox-inspired block characters. '
                               'Created with computer animation and AI-assisted writing and voices. '
                               'Not captured gameplay. Not affiliated with or endorsed by Roblox Corporation.'),
        'upload_thumbnail': False,
        'notify_subscribers': True,
        'publish_verify_grace_minutes': 45,
    },
    'channel': {
        'niche': 'Funny, emotional Roblox-style obby adventure stories with recurring characters',
        'audience_note': 'General audience, ages 13+',
        'reference_urls': [],
        'game_names': ['Brookhaven', 'Adopt Me', 'Blox Fruits', 'Tower of Hell', 'Doors', 'Grow a Garden',
                       'Natural Disaster Survival', 'Murder Mystery 2', 'Pet Simulator', 'Dress to Impress',
                       'Steal a Brainrot', '99 Nights in the Forest', 'Arsenal', 'Bee Swarm Simulator'],
        'negative_keywords': ['free robux', 'hack', 'exploit', 'scam', 'giveaway'],
        'identity': {
            'name': 'Blox Studio',
            'tone': 'Warm, funny, kind-hearted underdog stories with a twist ending',
            'visual_style': 'Bright saturated block world, chunky studded platforms, soft daylight, clean silhouettes',
            'recurring_world': 'Skyline Obby: floating studded platforms above a calm sea of clouds',
        },
    },
    'research': {
        'enabled': True,
        'interval_minutes': 360,
        'snapshot_interval_minutes': 120,
        'snapshot_top_n': 60,
        'windows_hours': [24, 72, 168],
        'queries': ['roblox story', 'roblox animation', 'roblox obby', 'roblox funny moments'],
        'max_search_calls_per_run': 6,
        'max_pages_per_query': 2,
        'region_code': 'US',
        'relevance_language': 'en',
        'cache_minutes': 60,
        'weights': {
            'velocity': 0.30, 'age_adjusted': 0.15, 'engagement': 0.15,
            'channel_relative': 0.15, 'relevance': 0.15, 'freshness': 0.10,
        },
        'diversity_lambda': 0.25,
        'quota': {
            # Reported defaults as of 2026; verify in Google Cloud Console.
            'shared_units_per_day': 10000,
            'search_calls_per_day': 100,
            'insert_calls_per_day': 100,
            # Shared-bucket units kept back for publishing/verification.
            'reserve_shared_units': 1500,
            'reserve_search_calls': 0,
        },
    },
    'production': {
        'renderer': 'blender',
        'min_seconds': 30,
        'max_seconds': 60,
        'target_seconds': 42,
        'fps': 30,
        'width': 1080,
        'height': 1920,
        'blender_engine': 'BLENDER_EEVEE',
        'blender_samples': 16,
        'text_model': 'gpt-5-mini',
        'vision_model': 'gpt-5-mini',
        'asr_model': 'whisper-1',
        'embedding_model': 'text-embedding-3-small',
        'tts_provider': 'openai',
        'tts_model': 'gpt-4o-mini-tts',
        'elevenlabs_model': 'eleven_multilingual_v2',
        'runway_model': 'gen4.5',
        'music': 'generated',
        'music_asset': '',
        'music_gain_db': -20,
        'duck_db': 10,
        'captions': True,
        'caption_font': 'DejaVu Sans',
        'caption_font_size_ratio': 0.042,
        'safe_area': {'left': 0.07, 'right': 0.80, 'top': 0.12, 'bottom': 0.74},
        'pronunciations': DEFAULT_PRONUNCIATIONS,
        'max_tempo': 1.06,
        'min_expression_frames': 8,
        'allow_template_stories': False,
    },
    'qa': {
        'max_repairs_per_target': 2,
        'max_repair_rounds': 3,
        'uncertain_policy': 'hold',
        'require_asr': False,
        'loudness_target_lufs': -14.0,
        'loudness_tolerance': 2.5,
        'true_peak_max_dbtp': -1.0,
        'max_silence_s': 1.6,
        'max_freeze_s': 1.5,
        'max_av_drift_ms': 60,
        'foot_slide_mm_per_frame': 6.0,
        'vision_review': 'when_available',
    },
    'budget': {
        'currency': 'USD',
        'per_video_usd': 1.00,
        'daily_usd': 15.00,
        'monthly_usd': 300.00,
        'repair_share': 0.5,
        # Ambiguous paid requests (sent, but outcome unknown) are never
        # retried automatically unless the call's estimate is below this.
        'ambiguous_retry_max_usd': 0.0,
        # Editable price table. These are estimates for planning only; the
        # provider's invoice is the source of truth.
        'prices': {
            'openai_text_in_per_mtok': 0.25,
            'openai_text_out_per_mtok': 2.00,
            'openai_embed_per_mtok': 0.02,
            'openai_tts_per_min': 0.015,
            'openai_asr_per_min': 0.006,
            'openai_vision_per_image': 0.002,
            'elevenlabs_per_kchar': 0.20,
            'runway_per_second': 0.12,
            'local_render_per_min': 0.0,
        },
    },
    'analytics': {
        'enabled': True,
        'interval_hours': 24,
        'min_age_days': 3,
        'observation_days': 7,
        'min_sample': 8,
    },
}

RENDERERS = {'blender', 'runway', 'clips'}
TTS_PROVIDERS = {'openai', 'elevenlabs', 'local_test'}
ENGINES = {'BLENDER_EEVEE', 'CYCLES', 'BLENDER_WORKBENCH'}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ('pronunciations', 'prices', 'weights', 'safe_area'):
            out[k] = _merge(out[k], v)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = copy.deepcopy(v)
    return out


def get(d=None):
    d = d or dbmod.get()
    row = d.one('SELECT value FROM settings WHERE key=?', (KEY,))
    stored = json.loads(row['value']) if row else {}
    return _merge(DEFAULTS, stored)


def _bool_or_none(v, name):
    if v is not None and not isinstance(v, bool):
        raise ValueError(f'{name} must be true, false or unset')
    return v


def _bool(v, name):
    if not isinstance(v, bool):
        raise ValueError(f'{name} must be true or false')
    return v


def _strlist(v, name, max_items=50, max_len=120):
    if not isinstance(v, list) or len(v) > max_items:
        raise ValueError(f'{name} must be a list of up to {max_items} items')
    return [text(x, max_len, name).strip() for x in v if isinstance(x, str) and x.strip()]


def validate(p):
    a = p['autopilot']
    _bool(a['enabled'], 'autopilot.enabled')
    _bool(a['paused'], 'autopilot.paused')
    _bool(a['emergency_stop'], 'autopilot.emergency_stop')
    if a['mode'] not in ('review', 'autopilot'):
        raise ValueError('autopilot.mode must be review or autopilot')
    text(a.get('pause_reason', ''), 300, 'pause reason')

    s = p['schedule']
    try:
        ZoneInfo(str(s['timezone']))
    except (KeyError, ValueError, OSError):
        raise ValueError(f'Unknown timezone {str(s["timezone"])[:60]!r}; use an IANA name such as Australia/Adelaide')
    s['interval_minutes'] = integer(s['interval_minutes'], 30, 7 * 24 * 60, 'interval minutes')
    if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', str(s['anchor_local'])):
        raise ValueError('anchor time must be HH:MM')
    if s['policy'] not in ('local_wall_clock', 'fixed_interval_utc'):
        raise ValueError('Unknown schedule policy')
    s['max_per_rolling_24h'] = integer(s['max_per_rolling_24h'], 1, 48, 'rolling 24h cap')
    s['upload_lead_minutes'] = integer(s['upload_lead_minutes'], 30, 24 * 60, 'upload lead')
    s['min_lead_minutes'] = integer(s['min_lead_minutes'], 20, s['upload_lead_minutes'], 'minimum lead')
    s['horizon_hours'] = integer(s['horizon_hours'], 6, 168, 'horizon')
    s['buffer_target'] = integer(s['buffer_target'], 0, 24, 'buffer target')
    s['max_in_production'] = integer(s['max_in_production'], 1, 12, 'max in production')

    pub = p['publishing']
    _bool_or_none(pub['made_for_kids'], 'made for kids')
    _bool_or_none(pub['synthetic_disclosure'], 'synthetic disclosure')
    text(pub['category_id'], 4, 'category')
    if not re.fullmatch(r'\d{1,3}', pub['category_id']):
        raise ValueError('category id must be numeric')
    text(pub['default_language'], 10, 'language')
    pub['tags'] = _strlist(pub['tags'], 'tags', 30, 60)
    if sum(len(t) + 2 for t in pub['tags']) > 450:
        raise ValueError('tags are too long in total')
    text(pub['description_footer'], 1500, 'description footer')
    _bool(pub['upload_thumbnail'], 'upload thumbnail')
    _bool(pub['notify_subscribers'], 'notify subscribers')
    pub['publish_verify_grace_minutes'] = integer(pub['publish_verify_grace_minutes'], 5, 600, 'verify grace')

    c = p['channel']
    text(c['niche'], 500, 'niche', allow_empty=False)
    text(c['audience_note'], 200, 'audience note')
    c['reference_urls'] = _strlist(c['reference_urls'], 'reference URLs', 50, 300)
    c['game_names'] = _strlist(c['game_names'], 'game names', 100, 60)
    c['negative_keywords'] = _strlist(c['negative_keywords'], 'negative keywords', 100, 60)
    for k in ('name', 'tone', 'visual_style', 'recurring_world'):
        text(c['identity'].get(k, ''), 400, 'identity ' + k)

    r = p['research']
    _bool(r['enabled'], 'research enabled')
    r['interval_minutes'] = integer(r['interval_minutes'], 30, 7 * 24 * 60, 'research interval')
    r['snapshot_interval_minutes'] = integer(r['snapshot_interval_minutes'], 30, 24 * 60, 'snapshot interval')
    r['snapshot_top_n'] = integer(r['snapshot_top_n'], 0, 500, 'snapshot top N')
    if not isinstance(r['windows_hours'], list) or not 1 <= len(r['windows_hours']) <= 6:
        raise ValueError('Choose one to six research windows')
    r['windows_hours'] = sorted({integer(x, 1, 24 * 60, 'window hours') for x in r['windows_hours']})
    r['queries'] = _strlist(r['queries'], 'queries', 20, 100)
    r['max_search_calls_per_run'] = integer(r['max_search_calls_per_run'], 0, 50, 'search calls per run')
    r['max_pages_per_query'] = integer(r['max_pages_per_query'], 1, 10, 'pages per query')
    if not re.fullmatch(r'[A-Z]{2}', r['region_code']):
        raise ValueError('region code must be two capital letters')
    text(r['relevance_language'], 8, 'relevance language')
    r['cache_minutes'] = integer(r['cache_minutes'], 0, 24 * 60, 'cache minutes')
    for k in DEFAULTS['research']['weights']:
        r['weights'][k] = finite(r['weights'].get(k, 0), 0, 1, 'weight ' + k)
    r['weights'] = {k: r['weights'][k] for k in DEFAULTS['research']['weights']}
    if sum(r['weights'].values()) <= 0:
        raise ValueError('At least one ranking weight must be positive')
    r['diversity_lambda'] = finite(r['diversity_lambda'], 0, 1, 'diversity')
    q = r['quota']
    q['shared_units_per_day'] = integer(q['shared_units_per_day'], 0, 10_000_000, 'shared quota')
    q['search_calls_per_day'] = integer(q['search_calls_per_day'], 0, 1_000_000, 'search quota')
    q['insert_calls_per_day'] = integer(q['insert_calls_per_day'], 0, 1_000_000, 'upload quota')
    q['reserve_shared_units'] = integer(q['reserve_shared_units'], 0, q['shared_units_per_day'], 'reserved units')
    q['reserve_search_calls'] = integer(q['reserve_search_calls'], 0, q['search_calls_per_day'], 'reserved searches')

    pr = p['production']
    if pr['renderer'] not in RENDERERS:
        raise ValueError('Unknown renderer')
    pr['min_seconds'] = integer(pr['min_seconds'], 5, 180, 'minimum length')
    pr['max_seconds'] = integer(pr['max_seconds'], pr['min_seconds'], 180, 'maximum length')
    pr['target_seconds'] = integer(pr['target_seconds'], pr['min_seconds'], pr['max_seconds'], 'target length')
    if pr['fps'] not in (24, 25, 30, 60):
        raise ValueError('fps must be 24, 25, 30 or 60')
    pr['width'] = integer(pr['width'], 240, 2160, 'width')
    pr['height'] = integer(pr['height'], 240, 3840, 'height')
    if pr['width'] % 2 or pr['height'] % 2:
        raise ValueError('Resolution must use even numbers')
    if pr['blender_engine'] not in ENGINES:
        raise ValueError('Unknown Blender engine')
    pr['blender_samples'] = integer(pr['blender_samples'], 1, 1024, 'samples')
    for k in ('text_model', 'vision_model', 'asr_model', 'embedding_model', 'tts_model', 'elevenlabs_model', 'runway_model'):
        if not re.fullmatch(r'[A-Za-z0-9._:\-]{1,64}', str(pr[k])):
            raise ValueError(f'Invalid model name for {k}')
    if pr['tts_provider'] not in TTS_PROVIDERS:
        raise ValueError('Unknown TTS provider')
    if pr['music'] not in ('generated', 'asset', 'none'):
        raise ValueError('music must be generated, asset or none')
    text(pr['music_asset'], 80, 'music asset')
    pr['music_gain_db'] = finite(pr['music_gain_db'], -40, 0, 'music gain')
    pr['duck_db'] = finite(pr['duck_db'], 0, 30, 'ducking')
    _bool(pr['captions'], 'captions')
    text(pr['caption_font'], 60, 'caption font')
    pr['caption_font_size_ratio'] = finite(pr['caption_font_size_ratio'], 0.02, 0.08, 'caption size')
    sa = pr['safe_area']
    for k in ('left', 'right', 'top', 'bottom'):
        sa[k] = finite(sa[k], 0, 1, 'safe area ' + k)
    if not (sa['left'] < sa['right'] and sa['top'] < sa['bottom']):
        raise ValueError('Safe area edges are inverted')
    if not isinstance(pr['pronunciations'], dict) or len(pr['pronunciations']) > 300:
        raise ValueError('pronunciations must be a mapping of up to 300 entries')
    for k, v in pr['pronunciations'].items():
        text(k, 60, 'pronunciation word', allow_empty=False)
        text(v, 120, 'pronunciation', allow_empty=False)
    pr['max_tempo'] = finite(pr['max_tempo'], 1.0, 1.15, 'max tempo')
    pr['min_expression_frames'] = integer(pr['min_expression_frames'], 4, 60, 'min expression frames')
    _bool(pr['allow_template_stories'], 'allow template stories')

    qa = p['qa']
    qa['max_repairs_per_target'] = integer(qa['max_repairs_per_target'], 0, 5, 'repairs per target')
    qa['max_repair_rounds'] = integer(qa['max_repair_rounds'], 0, 6, 'repair rounds')
    if qa['uncertain_policy'] not in ('hold', 'allow_minor'):
        raise ValueError('uncertain policy must be hold or allow_minor')
    _bool(qa['require_asr'], 'require ASR')
    qa['loudness_target_lufs'] = finite(qa['loudness_target_lufs'], -30, -8, 'loudness target')
    qa['loudness_tolerance'] = finite(qa['loudness_tolerance'], 0.5, 6, 'loudness tolerance')
    qa['true_peak_max_dbtp'] = finite(qa['true_peak_max_dbtp'], -6, 0, 'true peak')
    qa['max_silence_s'] = finite(qa['max_silence_s'], 0.3, 10, 'max silence')
    qa['max_freeze_s'] = finite(qa['max_freeze_s'], 0.3, 10, 'max freeze')
    qa['max_av_drift_ms'] = finite(qa['max_av_drift_ms'], 10, 500, 'max drift')
    qa['foot_slide_mm_per_frame'] = finite(qa['foot_slide_mm_per_frame'], 0.5, 50, 'foot slide')
    if qa['vision_review'] not in ('off', 'when_available', 'required'):
        raise ValueError('vision review must be off, when_available or required')

    b = p['budget']
    b['per_video_usd'] = finite(b['per_video_usd'], 0, 1000, 'per-video budget')
    b['daily_usd'] = finite(b['daily_usd'], 0, 100000, 'daily budget')
    b['monthly_usd'] = finite(b['monthly_usd'], 0, 1000000, 'monthly budget')
    b['repair_share'] = finite(b['repair_share'], 0, 2, 'repair share')
    b['ambiguous_retry_max_usd'] = finite(b['ambiguous_retry_max_usd'], 0, 1, 'ambiguous retry ceiling')
    for k in DEFAULTS['budget']['prices']:
        b['prices'][k] = finite(b['prices'].get(k, DEFAULTS['budget']['prices'][k]), 0, 1000, 'price ' + k)

    an = p['analytics']
    _bool(an['enabled'], 'analytics enabled')
    an['interval_hours'] = integer(an['interval_hours'], 1, 168, 'analytics interval')
    an['min_age_days'] = integer(an['min_age_days'], 1, 60, 'min age')
    an['observation_days'] = integer(an['observation_days'], 1, 90, 'observation window')
    an['min_sample'] = integer(an['min_sample'], 3, 500, 'min sample')
    return p


def put(p, d=None):
    d = d or dbmod.get()
    validate(p)
    d.execute('INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (KEY, json.dumps(p, sort_keys=True)))
    return p


def update(patch, d=None):
    """Deep-merge a partial update, validate and store. Returns new prefs."""
    d = d or dbmod.get()
    with d.tx():
        p = _merge(get(d), patch)
        return put(p, d)
