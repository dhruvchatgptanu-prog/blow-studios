"""Google OAuth 2.0 for one YouTube channel (web server flow + PKCE).

Scopes (least privilege):
* youtube.upload    - videos.insert (with status.publishAt) and thumbnails.set
* youtube.readonly  - identify the channel, verify processing/scheduling/publication,
                      reconcile interrupted uploads via the uploads playlist
* yt-analytics.readonly (optional) - performance learning
Not requested: youtube / youtube.force-ssl (edit or delete videos).
"""
import base64
import hashlib
import json
import secrets
import time
import urllib.parse

from .. import config, store, vault
from ..http import ProviderError, request
from ..util import Blocked

AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
REVOKE = 'https://oauth2.googleapis.com/revoke'
SCOPE_UPLOAD = 'https://www.googleapis.com/auth/youtube.upload'
SCOPE_READ = 'https://www.googleapis.com/auth/youtube.readonly'
SCOPE_ANALYTICS = 'https://www.googleapis.com/auth/yt-analytics.readonly'
STATE_TTL = 600


def redirect_uri():
    return config.public_url() + '/oauth/callback'


def configured():
    return vault.configured('GOOGLE_CLIENT_ID') and vault.configured('GOOGLE_CLIENT_SECRET')


def start(session, analytics=True):
    if not configured():
        raise ValueError('Add the Google OAuth client ID and secret first')
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    state = secrets.token_urlsafe(32)
    session['oauth'] = {'state': state, 'verifier': verifier, 'at': time.time(), 'analytics': bool(analytics)}
    scopes = [SCOPE_UPLOAD, SCOPE_READ] + ([SCOPE_ANALYTICS] if analytics else [])
    params = {'client_id': vault.get('GOOGLE_CLIENT_ID'), 'redirect_uri': redirect_uri(), 'response_type': 'code',
              'scope': ' '.join(scopes), 'access_type': 'offline', 'prompt': 'consent select_account',
              'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'}
    return AUTH + '?' + urllib.parse.urlencode(params)


def finish(session, args):
    import hmac
    pending = session.pop('oauth', None) or {}
    if not pending or not hmac.compare_digest(str(args.get('state', '')), pending.get('state', '!')):
        raise ValueError('OAuth state did not match this browser session. Start the connection again.')
    if time.time() - pending.get('at', 0) > STATE_TTL:
        raise ValueError('The connection request expired. Start again.')
    if args.get('error'):
        raise ValueError('Google returned: ' + str(args.get('error'))[:80])
    code = args.get('code')
    if not code:
        raise ValueError('No authorization code returned')
    r = request('google_oauth', 'POST', TOKEN, data={
        'client_id': vault.get('GOOGLE_CLIENT_ID'), 'client_secret': vault.get('GOOGLE_CLIENT_SECRET'), 'code': code,
        'redirect_uri': redirect_uri(), 'grant_type': 'authorization_code', 'code_verifier': pending['verifier']},
        timeout=(10, 40))
    tok = r.json()
    if not tok.get('refresh_token'):
        raise ValueError('Google did not return a refresh token. Remove Blox Studio from your Google account '
                         'permissions and connect again.')
    granted = set((tok.get('scope') or '').split())
    if SCOPE_UPLOAD not in granted or SCOPE_READ not in granted:
        raise ValueError('Upload and read-only YouTube permissions are both required; tick both boxes on the consent screen.')
    vault.put('YOUTUBE_REFRESH_TOKEN', tok['refresh_token'])
    _save_access(tok)
    store.put('youtube_scopes', sorted(granted))
    store.put('youtube_auth_error', '')
    ch = identify_channel()
    store.audit('youtube_connected', {'channel': ch.get('id'), 'scopes': sorted(granted)}, actor='owner')
    return ch


def _save_access(tok):
    vault.put('YOUTUBE_ACCESS_TOKEN', json.dumps({'token': tok['access_token'],
                                                  'expires_at': time.time() + int(tok.get('expires_in', 3600)) - 90}))


def access_token(force=False):
    if not force:
        raw = vault.get('YOUTUBE_ACCESS_TOKEN')
        if raw:
            try:
                j = json.loads(raw)
                if j['expires_at'] > time.time():
                    return j['token']
            except (ValueError, KeyError):
                pass
    refresh = vault.get('YOUTUBE_REFRESH_TOKEN')
    if not refresh:
        raise Blocked('Connect your YouTube channel in Connections', state='needs_credentials')
    try:
        r = request('google_oauth', 'POST', TOKEN, data={
            'client_id': vault.get('GOOGLE_CLIENT_ID'), 'client_secret': vault.get('GOOGLE_CLIENT_SECRET'),
            'refresh_token': refresh, 'grant_type': 'refresh_token'}, timeout=(10, 40))
    except ProviderError as e:
        if e.status in (400, 401) and (e.code in ('invalid_grant', 'invalid_client', 'unauthorized_client') or e.status == 401):
            store.put('youtube_auth_error', f'Google refused the saved authorization ({e.code}). Reconnect YouTube.')
            raise Blocked('YouTube authorization expired or was revoked. Reconnect the channel in Connections.',
                          state='needs_credentials')
        raise
    tok = r.json()
    if tok.get('refresh_token'):
        vault.put('YOUTUBE_REFRESH_TOKEN', tok['refresh_token'])
    _save_access(tok)
    return tok['access_token']


def authed(method, url, **kw):
    """Authorized request; refreshes once on 401."""
    headers = kw.pop('headers', {})
    for attempt in (0, 1):
        token = access_token(force=attempt == 1)
        try:
            return request('youtube', method, url, headers=dict(headers, Authorization='Bearer ' + token), **kw)
        except ProviderError as e:
            if e.status == 401 and attempt == 0:
                continue
            raise


def identify_channel():
    from ..research import youtube_api as Y
    Y.spend('shared', 1, 'channels.list(mine)', purpose='publishing')
    r = authed('GET', 'https://www.googleapis.com/youtube/v3/channels',
               params={'part': 'snippet,contentDetails,status', 'mine': 'true'}, timeout=(10, 30))
    items = r.json().get('items') or []
    if not items:
        raise ValueError('This Google account has no YouTube channel. Create one, then connect again.')
    if len(items) > 1:
        store.put('youtube_channel_candidates', [{'id': i['id'], 'title': i['snippet']['title']} for i in items])
    ch = items[0]
    info = {'id': ch['id'], 'title': ch['snippet']['title'], 'custom_url': ch['snippet'].get('customUrl', ''),
            'uploads_playlist': ch.get('contentDetails', {}).get('relatedPlaylists', {}).get('uploads'),
            'made_for_kids_default': ch.get('status', {}).get('madeForKids'), 'connected_at': time.time(),
            'confirmed': False}
    store.put('channel', info)
    return info


def confirm_channel(channel_id):
    ch = store.get('channel', {})
    if not ch or ch.get('id') != channel_id:
        raise ValueError('That is not the connected channel')
    ch['confirmed'] = True
    ch['confirmed_at'] = time.time()
    store.put('channel', ch)
    store.audit('youtube_channel_confirmed', {'channel': channel_id}, actor='owner')
    return ch


def disconnect():
    token = vault.get('YOUTUBE_REFRESH_TOKEN')
    if token:
        try:
            request('google_oauth', 'POST', REVOKE, data={'token': token}, timeout=(10, 30))
        except ProviderError:
            pass
    vault.put('YOUTUBE_REFRESH_TOKEN', '')
    vault.put('YOUTUBE_ACCESS_TOKEN', '')
    store.put('channel', {})
    store.put('youtube_scopes', [])
    store.audit('youtube_disconnected', {}, actor='owner')


def status():
    ch = store.get('channel', {}) or {}
    scopes = store.get('youtube_scopes', []) or []
    return {'oauth_client': configured(), 'connected': vault.configured('YOUTUBE_REFRESH_TOKEN'), 'channel': ch,
            'scopes': scopes, 'analytics_scope': SCOPE_ANALYTICS in scopes,
            'error': store.get('youtube_auth_error', ''), 'redirect_uri': redirect_uri(),
            'channel_candidates': store.get('youtube_channel_candidates', [])}
