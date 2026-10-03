"""YouTube upload, scheduling and publication verification.

States on the video:
  approved -> upload_started -> uploaded_private (YouTube accepted the file and
  returned an id) -> processing_verified (processing succeeded) -> scheduled
  (status.publishAt confirmed on the video) -> published (privacyStatus is
  observed as public after the slot time).

Duplicate-upload protection:
* one ``uploads`` row per video (unique), created before any request;
* the resumable session URL is stored encrypted before bytes are sent; after
  a restart the confirmed byte offset is queried and the upload resumes;
* every upload carries a hidden marker tag; if a session is lost after bytes
  were sent, the channel's uploads playlist is searched for the marker before
  anything new is uploaded;
* once all bytes were acknowledged, a new upload is never started
  automatically.
"""
import json
import os
import re
import urllib.parse

from .. import config, db as dbmod, jobs, prefs as prefsmod, store, vault, videos
from ..http import ProviderError
from ..research import youtube_api as Y
from ..tasks import handler
from ..timeutil import iso_utc, parse_iso
from ..util import Blocked, Waiting, new_id, now
from . import oauth

UPLOAD_URL = 'https://www.googleapis.com/upload/youtube/v3/videos'
API = 'https://www.googleapis.com/youtube/v3/'
CHUNK = 8 * 1024 * 1024


def get_upload(video_id, d=None):
    d = d or dbmod.get()
    r = d.one('SELECT * FROM uploads WHERE video_id=?', (video_id,))
    if r:
        r['requested'] = json.loads(r['requested'])
        r['observed'] = json.loads(r['observed'])
    return r


def _set(d, uid, **f):
    f['updated_at'] = now()
    for k in ('requested', 'observed'):
        if k in f:
            f[k] = json.dumps(f[k])
    d.execute('UPDATE uploads SET ' + ', '.join(f'{k}=?' for k in f) + ' WHERE id=?', (*f.values(), uid))


def build_metadata(v, slot_at, p):
    pub = p['publishing']
    md = v['metadata'].get('publish_metadata') or {}
    title = (md.get('title') or v['title'])[:100]
    desc = (md.get('description') or '').strip()
    footer = pub['description_footer'].strip()
    description = (desc + '\n\n' + footer + '\n\n#Shorts').strip()[:4900]
    tags = list(dict.fromkeys([t for t in (md.get('tags') or []) + pub['tags'] if t]))
    while sum(len(t) + 2 for t in tags) > 450:
        tags.pop()
    status = {'privacyStatus': 'private', 'publishAt': iso_utc(slot_at),
              'selfDeclaredMadeForKids': pub['made_for_kids'], 'containsSyntheticMedia': pub['synthetic_disclosure']}
    return {'snippet': {'title': title, 'description': description, 'tags': tags, 'categoryId': pub['category_id'],
                        'defaultLanguage': pub['default_language'], 'defaultAudioLanguage': pub['default_language']},
            'status': status}


def preflight(v, slot, p):
    ch = store.get('channel', {}) or {}
    problems = []
    if not vault.configured('YOUTUBE_REFRESH_TOKEN'):
        problems.append('YouTube is not connected')
    elif not ch.get('confirmed'):
        problems.append('Confirm the connected channel in Connections')
    if p['publishing']['made_for_kids'] is None:
        problems.append('Choose the made-for-kids setting')
    if p['publishing']['synthetic_disclosure'] is None:
        problems.append('Choose the altered/synthetic content disclosure')
    if p['autopilot']['emergency_stop']:
        problems.append('Emergency stop is active')
    if slot and slot['slot_at'] - now() < p['schedule']['min_lead_minutes'] * 60:
        problems.append('Too close to the slot time to upload and verify processing safely')
    return problems


def _marker():
    return 'bxs' + new_id()[:10]


@handler('video.upload')
def upload(ctx):
    d = dbmod.get()
    p = prefsmod.get(d)
    vid = ctx.video_id
    v = videos.get(vid, d)
    slot = d.one('SELECT * FROM slots WHERE id=?', (ctx.payload['slot_id'],))
    up = get_upload(vid, d)
    if not up or up['status'] in ('new', 'initiating'):
        problems = preflight(v, slot, p)
        if problems:
            release_slot(d, slot, vid, 'Upload preflight failed: ' + '; '.join(problems))
            if any('connected' in x or 'Confirm' in x for x in problems):
                raise Blocked('; '.join(problems), state='needs_credentials')
            return {'skipped': problems}
    path = os.path.join(config.DATA_DIR, d.one('SELECT file FROM renders WHERE id=?', (v['render_id'],))['file'])
    total = os.path.getsize(path)
    if not up:
        md = build_metadata(v, slot['slot_at'], p)
        marker = _marker()
        md['snippet']['tags'] = md['snippet']['tags'] + [marker]
        d.execute('''INSERT INTO uploads(id, video_id, slot_id, status, marker, bytes_total, requested, created_at,
                     updated_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                  (new_id('up_'), vid, slot['id'], 'new', marker, total, json.dumps(md), now(), now()))
        up = get_upload(vid, d)
    if v['status'] == 'approved':
        videos.transition(vid, 'upload_started', f'Uploading for slot {iso_utc(slot["slot_at"])}', d=d)
    if up['youtube_video_id']:
        return _uploaded(d, up, vid)
    if up['status'] in ('new', 'initiating') or not up['session_enc']:
        _set(d, up['id'], status='initiating')
        Y.spend('insert', 1, 'videos.insert', purpose='publishing')
        r = oauth.authed('POST', UPLOAD_URL, params={'uploadType': 'resumable', 'part': 'snippet,status',
                                                     'notifySubscribers': str(p['publishing']['notify_subscribers']).lower()},
                         headers={'X-Upload-Content-Type': 'video/mp4', 'X-Upload-Content-Length': str(total),
                                  'Content-Type': 'application/json; charset=UTF-8'},
                         json=up['requested'], timeout=(10, 60))
        loc = r.headers.get('Location', '')
        u = urllib.parse.urlparse(loc)
        if u.scheme != 'https' or u.hostname not in ('www.googleapis.com', 'youtube.googleapis.com'):
            raise Blocked('Unexpected upload session host returned by YouTube', state='needs_review')
        # Initiating a session creates no video, so re-initiating after an error is safe.
        _set(d, up['id'], status='uploading', session_enc=vault.encrypt(loc), bytes_confirmed=0)
        up = get_upload(vid, d)
    session = vault.decrypt(up['session_enc'])
    while True:
        ctx.beat()
        if ctx.stop_reason() == 'emergency stop':
            raise Waiting('Emergency stop: upload paused at a safe point', delay=300)
        try:
            r = oauth.authed('PUT', session, headers={'Content-Length': '0', 'Content-Range': f'bytes */{total}'},
                             ok=(200, 201, 308), timeout=(10, 60))
        except ProviderError as e:
            if e.status in (404, 410):
                return _lost_session(ctx, d, up, vid, total)
            raise
        if r.status_code in (200, 201):
            return _complete(d, up, vid, r.json())
        offset = confirmed_offset(r.headers.get('Range'))
        _set(d, up['id'], bytes_confirmed=offset)
        if offset >= total:
            raise Waiting('All bytes acknowledged; waiting for YouTube to finish the upload response', delay=30)
        with open(path, 'rb') as f:
            f.seek(offset)
            chunk = f.read(CHUNK)
        end = offset + len(chunk) - 1
        try:
            r = oauth.authed('PUT', session, data=chunk, headers={'Content-Type': 'video/mp4',
                                                                  'Content-Range': f'bytes {offset}-{end}/{total}'},
                             ok=(200, 201, 308), timeout=(10, 300))
        except ProviderError as e:
            if e.status in (404, 410):
                return _lost_session(ctx, d, up, vid, total)
            if e.ambiguous or not e.sent or e.status in (500, 502, 503, 504):
                raise Waiting('Upload interrupted; resuming from the confirmed offset', delay=20)
            raise
        if r.status_code in (200, 201):
            return _complete(d, up, vid, r.json())


def confirmed_offset(rng):
    """Next byte to send from a resumable-upload Range header ("bytes=0-N"); 0 when absent or malformed."""
    m = re.fullmatch(r'\s*bytes=0-(\d+)\s*', rng or '')
    return int(m.group(1)) + 1 if m else 0


def _complete(d, up, vid, body):
    yid = body.get('id')
    if not yid:
        raise Blocked('YouTube finished the upload without returning a video id', state='needs_review')
    _set(d, up['id'], status='uploaded', youtube_video_id=yid, bytes_confirmed=up['bytes_total'], observed={'insert': {
        'status': body.get('status', {}), 'at': now()}})
    return _uploaded(d, get_upload(vid, d), vid)


def _uploaded(d, up, vid):
    v = videos.get(vid, d)
    yid = up['youtube_video_id']
    if v['status'] == 'upload_started':
        videos.transition(vid, 'uploaded_private', f'YouTube accepted the upload ({yid}); not public yet', d=d,
                          youtube_video_id=yid, youtube_url=f'https://www.youtube.com/shorts/{yid}')
    if up['slot_id']:
        d.execute("UPDATE slots SET status='uploaded', updated_at=? WHERE id=?", (now(), up['slot_id']))
    jobs.enqueue('video.verify', {'upload_id': up['id']}, video_id=vid, idempotency_key=f'verify:{up["id"]}',
                 due_at=now() + 60, max_attempts=12, d=d)
    store.audit('youtube_uploaded', {'video': vid, 'youtube_id': yid})
    return {'youtube_id': yid}


def _lost_session(ctx, d, up, vid, total):
    """Session expired. Reconcile via the uploads playlist before any new upload."""
    found = reconcile(up)
    if found:
        _set(d, up['id'], status='uploaded', youtube_video_id=found, observed={'reconciled': True})
        return _uploaded(d, get_upload(vid, d), vid)
    sent_all = (up['bytes_confirmed'] or 0) >= total
    tries = int(up['observed'].get('reconcile_tries', 0)) + 1
    obs = dict(up['observed'], reconcile_tries=tries)
    _set(d, up['id'], observed=obs)
    if sent_all or tries < 4:
        if tries >= 6:
            raise Blocked('The upload session was lost after all bytes were sent and the video is not visible in the '
                          'uploads list. Check YouTube Studio; Blox will not upload a duplicate.', state='needs_review')
        raise Waiting('Upload session expired; checking the uploads list before deciding', delay=180)
    # Never finished and not found after repeated checks: safe to start a new session.
    _set(d, up['id'], status='new', session_enc=None, bytes_confirmed=0)
    raise Waiting('Previous session expired before completion; starting a new upload session', delay=5)


def reconcile(up):
    ch = store.get('channel', {}) or {}
    pl = ch.get('uploads_playlist')
    if not pl:
        return None
    Y.spend('shared', 1, 'playlistItems.list(reconcile)', purpose='publishing')
    r = oauth.authed('GET', API + 'playlistItems', params={'part': 'contentDetails', 'playlistId': pl, 'maxResults': 25},
                     timeout=(10, 30))
    ids = [it['contentDetails']['videoId'] for it in r.json().get('items', [])]
    if not ids:
        return None
    Y.spend('shared', 1, 'videos.list(reconcile)', purpose='publishing')
    r = oauth.authed('GET', API + 'videos', params={'part': 'snippet', 'id': ','.join(ids)}, timeout=(10, 30))
    for it in r.json().get('items', []):
        if up['marker'] in (it.get('snippet', {}).get('tags') or []):
            return it['id']
    return None


def release_slot(d, slot, vid, reason):
    if slot:
        d.execute("UPDATE slots SET status='skipped', video_id=NULL, reason=?, updated_at=? WHERE id=? AND status IN "
                  "('assigned','open')", (reason[:500], now(), slot['id']))
    d.execute('UPDATE videos SET slot_id=NULL WHERE id=?', (vid,))
    store.audit('slot_released', {'video': vid, 'reason': reason})


@handler('video.verify')
def verify(ctx):
    d = dbmod.get()
    p = prefsmod.get(d)
    up = d.one('SELECT * FROM uploads WHERE id=?', (ctx.payload['upload_id'],))
    up['requested'] = json.loads(up['requested'])
    up['observed'] = json.loads(up['observed'])
    vid = up['video_id']
    yid = up['youtube_video_id']
    slot = d.one('SELECT * FROM slots WHERE id=?', (up['slot_id'],)) if up['slot_id'] else None
    Y.spend('shared', 1, 'videos.list(verify)', purpose='publishing')
    r = oauth.authed('GET', API + 'videos', params={'part': 'status,processingDetails', 'id': yid}, timeout=(10, 30))
    items = r.json().get('items') or []
    if not items:
        raise Waiting('Video not yet visible through the API', delay=120)
    it = items[0]
    st = it.get('status', {})
    proc = it.get('processingDetails', {})
    obs = dict(up['observed'], last={'status': st, 'processing': proc, 'at': now()})
    d.execute('UPDATE uploads SET observed=?, updated_at=? WHERE id=?', (json.dumps(obs), now(), up['id']))
    v = videos.get(vid, d)
    if st.get('uploadStatus') in ('failed', 'rejected', 'deleted') or proc.get('processingStatus') in ('failed', 'terminated'):
        reason = st.get('failureReason') or st.get('rejectionReason') or proc.get('processingFailureReason') or st.get('uploadStatus')
        videos.hold(vid, 'blocked', f'YouTube processing failed: {reason}', d=d)
        if slot:
            d.execute("UPDATE slots SET status='failed', reason=?, updated_at=? WHERE id=?",
                      (f'YouTube processing failed: {reason}', now(), slot['id']))
        return {'failed': reason}
    processed = st.get('uploadStatus') == 'processed' or proc.get('processingStatus') == 'succeeded'
    if not processed:
        raise Waiting('YouTube is still processing the upload', delay=90)
    if v['status'] == 'uploaded_private':
        videos.transition(vid, 'processing_verified', 'YouTube finished processing', d=d)
        v = videos.get(vid, d)
    if not (up['requested'].get('status') or {}).get('publishAt'):
        # Uploaded without a schedule (for example by the previous version).
        if st.get('privacyStatus') == 'public':
            if v['status'] != 'published':
                videos.transition(vid, 'published', 'Observed public on YouTube', d=d)
            return {'published': yid}
        videos.hold(vid, 'needs_review', f'On YouTube as {st.get("privacyStatus")}; it was uploaded without a '
                                         'scheduled time. Publish it in YouTube Studio if intended.', d=d)
        return {'status': st.get('privacyStatus')}
    requested = parse_iso(up['requested']['status']['publishAt'])
    observed_at = parse_iso(st.get('publishAt')) if st.get('publishAt') else None
    if v['status'] == 'processing_verified':
        if st.get('privacyStatus') == 'public':
            videos.transition(vid, 'published', 'Observed public on YouTube', d=d)
            _slot_published(d, slot)
            return {'published': yid}
        if observed_at is None or abs(observed_at - requested) > 120:
            msg = ('YouTube did not keep the scheduled publication time (publishAt missing or changed). API projects '
                   'that have not passed YouTube\'s audit can only upload private videos. The video stays private.')
            videos.hold(vid, 'needs_review', msg, d=d)
            if slot:
                d.execute("UPDATE slots SET status='failed', reason=?, updated_at=? WHERE id=?", (msg, now(), slot['id']))
            store.put('publishing_restriction', {'reason': msg, 'at': now(), 'video': vid})
            return {'restricted': True}
        videos.transition(vid, 'scheduled', f'Scheduled for {iso_utc(requested)}', d=d)
        if slot:
            d.execute("UPDATE slots SET status='scheduled', updated_at=? WHERE id=?", (now(), slot['id']))
        raise Waiting('Scheduled; verifying publication after the slot time', delay=max(60, requested - now() + 90))
    if v['status'] == 'scheduled':
        if st.get('privacyStatus') == 'public':
            videos.transition(vid, 'published', 'Observed public on YouTube after the scheduled time', d=d)
            _slot_published(d, slot)
            return {'published': yid}
        grace = p['publishing']['publish_verify_grace_minutes'] * 60
        if now() < requested + grace:
            raise Waiting('Waiting for YouTube to publish at the scheduled time', delay=max(60, min(300, requested + grace - now())))
        msg = f'Still {st.get("privacyStatus")} {int((now() - requested) / 60)} minutes after the scheduled time.'
        videos.hold(vid, 'needs_review', msg, d=d)
        if slot:
            d.execute("UPDATE slots SET status='failed', reason=?, updated_at=? WHERE id=?", (msg, now(), slot['id']))
        return {'not_public': True}
    return {'status': v['status']}


def _slot_published(d, slot):
    if slot:
        d.execute("UPDATE slots SET status='published', updated_at=? WHERE id=?", (now(), slot['id']))

