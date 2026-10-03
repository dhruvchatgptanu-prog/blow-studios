"""YouTube Data API v3 client for research, with a quota ledger and caching.

Quota model (configurable in Settings, because Google changes it): as of
2026, ``search.list`` and ``videos.insert`` are reported to have their own
per-call buckets (default 100 calls/day each) and every other method spends
units from the shared bucket (default 10,000/day, list calls cost 1 unit).
Quota resets at midnight Pacific Time. The API's own ``quotaExceeded`` error is
authoritative: when it appears, the bucket is marked exhausted for the day.
"""
import hashlib
import json
import re
import time

from .. import db as dbmod, prefs as prefsmod, vault
from ..http import ProviderError, request
from ..timeutil import next_pacific_midnight, pacific_day
from ..util import Blocked, new_id, now

BASE = 'https://www.googleapis.com/youtube/v3/'
COST = {'search': ('search', 1), 'videos': ('shared', 1), 'channels': ('shared', 1), 'playlistItems': ('shared', 1),
        'captions': ('shared', 50)}


class QuotaExhausted(Blocked):
    def __init__(self, bucket, message):
        super().__init__(message, state='blocked')
        self.bucket = bucket


def used(bucket, d=None, day=None):
    d = d or dbmod.get()
    return int(d.scalar('SELECT COALESCE(SUM(units),0) AS u FROM quota_ledger WHERE pt_day=? AND bucket=?',
                        (day or pacific_day(), bucket)) or 0)


def limits(p=None):
    q = (p or prefsmod.get())['research']['quota']
    return {'shared': q['shared_units_per_day'], 'search': q['search_calls_per_day'], 'insert': q['insert_calls_per_day']}


def remaining(bucket, purpose='research', p=None, d=None):
    p = p or prefsmod.get(d)
    q = p['research']['quota']
    lim = limits(p)[bucket]
    reserve = 0
    if purpose == 'research':
        reserve = q['reserve_shared_units'] if bucket == 'shared' else (q['reserve_search_calls'] if bucket == 'search' else 0)
    return lim - reserve - used(bucket, d)


def spend(bucket, units, method, purpose='research', p=None, d=None):
    """Atomically record quota use or refuse when it would exceed the budget."""
    d = d or dbmod.get()
    with d.tx():
        if remaining(bucket, purpose, p, d) < units:
            reset = next_pacific_midnight(now())
            raise QuotaExhausted(bucket, f'YouTube {bucket} quota for {purpose} is used up for today; it resets at '
                                         f'midnight Pacific Time ({time.strftime("%H:%M UTC", time.gmtime(reset))}).')
        d.execute('INSERT INTO quota_ledger(id, bucket, pt_day, units, method, purpose, at) VALUES (?,?,?,?,?,?,?)',
                  (new_id('q_'), bucket, pacific_day(), units, method, purpose, now()))


def mark_exhausted(bucket, method, d=None):
    d = d or dbmod.get()
    rest = max(0, limits()[bucket] - used(bucket, d))
    if rest:
        d.execute('INSERT INTO quota_ledger(id, bucket, pt_day, units, method, purpose, at) VALUES (?,?,?,?,?,?,?)',
                  (new_id('q_'), bucket, pacific_day(), rest, method, 'api_reported_exhausted', now()))


def _cache_key(method, params):
    return method + ':' + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:32]


def call(method, params, cache_minutes=0, purpose='research', d=None, access_token=None):
    d = d or dbmod.get()
    params = dict(params)
    key = _cache_key(method, params)
    if cache_minutes:
        row = d.one('SELECT body, fetched_at FROM api_cache WHERE key=? AND expires_at>?', (key, now()))
        if row:
            body = json.loads(row['body'])
            body['_cached_at'] = row['fetched_at']
            return body
    headers = {}
    if access_token:
        headers['Authorization'] = 'Bearer ' + access_token
    else:
        api_key = vault.get('YOUTUBE_API_KEY')
        if not api_key:
            raise Blocked('Add a YouTube Data API key in Connections to run research', state='needs_credentials')
        params['key'] = api_key
    bucket, units = COST[method]
    spend(bucket, units, method, purpose, d=d)
    try:
        r = request('youtube_data', 'GET', BASE + method, params=params, timeout=(10, 40))
    except ProviderError as e:
        if e.code in ('quotaExceeded', 'dailyLimitExceeded'):
            mark_exhausted(bucket, method, d)
            raise QuotaExhausted(bucket, 'YouTube reports the daily quota is exhausted for this API project.')
        if e.code in ('keyInvalid', 'forbidden', 'accessNotConfigured') or e.status in (400,) and e.code == 'keyInvalid':
            raise Blocked('YouTube rejected the API key or the YouTube Data API is not enabled for the project: '
                          + str(e), state='needs_credentials')
        raise
    body = r.json()
    if cache_minutes:
        d.execute('INSERT INTO api_cache(key, fetched_at, expires_at, body) VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE '
                  'SET fetched_at=excluded.fetched_at, expires_at=excluded.expires_at, body=excluded.body',
                  (key, now(), now() + cache_minutes * 60, json.dumps(body)))
    return body


def search(q, published_after, published_before, page_token=None, order='viewCount', region=None, lang=None,
           cache_minutes=60):
    params = {'part': 'snippet', 'q': q, 'type': 'video', 'maxResults': 50, 'order': order,
              'publishedAfter': published_after, 'publishedBefore': published_before, 'videoDuration': 'short',
              'safeSearch': 'none'}
    if page_token:
        params['pageToken'] = page_token
    if region:
        params['regionCode'] = region
    if lang:
        params['relevanceLanguage'] = lang
    return call('search', params, cache_minutes)


def videos(ids, cache_minutes=0):
    out = []
    for i in range(0, len(ids), 50):
        body = call('videos', {'part': 'snippet,contentDetails,statistics,player,liveStreamingDetails,status',
                               'id': ','.join(ids[i:i + 50]), 'maxHeight': 1920, 'maxResults': 50}, cache_minutes)
        out += body.get('items', [])
    return out


def channels(ids=None, handle=None, cache_minutes=360):
    if handle:
        return call('channels', {'part': 'snippet,statistics,contentDetails', 'forHandle': handle}, cache_minutes).get('items', [])
    out = []
    for i in range(0, len(ids or []), 50):
        out += call('channels', {'part': 'snippet,statistics,contentDetails', 'id': ','.join(ids[i:i + 50]),
                                 'maxResults': 50}, cache_minutes).get('items', [])
    return out


def playlist_items(playlist_id, page_token=None, cache_minutes=30):
    params = {'part': 'contentDetails,snippet', 'playlistId': playlist_id, 'maxResults': 50}
    if page_token:
        params['pageToken'] = page_token
    return call('playlistItems', params, cache_minutes)


VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
CHANNEL_ID = re.compile(r'^UC[A-Za-z0-9_-]{22}$')


def parse_reference(url):
    """Classify a user-supplied YouTube URL without fetching it.

    Returns ('video', id) | ('channel', id) | ('handle', '@name') or raises ValueError.
    """
    from urllib.parse import parse_qs, urlparse
    s = url.strip()
    if VIDEO_ID.match(s):
        return 'video', s
    if CHANNEL_ID.match(s):
        return 'channel', s
    if re.fullmatch(r'@[A-Za-z0-9._-]{3,30}', s):
        return 'handle', s
    u = urlparse(s if '://' in s else 'https://' + s)
    host = (u.hostname or '').lower()
    if host not in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'www.youtu.be'):
        raise ValueError('Only youtube.com and youtu.be links are supported')
    parts = [p for p in u.path.split('/') if p]
    if host.endswith('youtu.be') and parts and VIDEO_ID.match(parts[0]):
        return 'video', parts[0]
    if parts[:1] == ['watch']:
        v = parse_qs(u.query).get('v', [''])[0]
        if VIDEO_ID.match(v):
            return 'video', v
    if parts and parts[0] in ('shorts', 'embed', 'live') and len(parts) > 1 and VIDEO_ID.match(parts[1]):
        return 'video', parts[1]
    if parts and parts[0] == 'channel' and len(parts) > 1 and CHANNEL_ID.match(parts[1]):
        return 'channel', parts[1]
    if parts and parts[0].startswith('@') and re.fullmatch(r'@[A-Za-z0-9._-]{3,30}', parts[0]):
        return 'handle', parts[0]
    raise ValueError('Could not recognise a video or channel in that link')
