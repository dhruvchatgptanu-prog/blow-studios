"""YouTube publishing against a fake YouTube (no network): resumable uploads, duplicate
prevention, expired authorization and the processed/scheduled/published distinction."""
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from blox import jobs, orchestrator, store, videos
from blox.youtube import publisher

from .helpers import approved_video, ready_to_publish, run_one

SESSION = 'https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=sess1'


class FakeYouTube:
    def __init__(self, http, total):
        self.http = http
        self.total = total
        self.received = b''
        self.inits = 0
        self.fail_next_chunk = None
        self.session_gone = False
        self.video = {'id': 'yt_abc123', 'status': {'uploadStatus': 'uploaded', 'privacyStatus': 'private'},
                      'processingDetails': {'processingStatus': 'processing'}, 'snippet': {'tags': []}}
        self.playlist = []
        self.token_error = None
        http.on('POST', 'https://oauth2.googleapis.com/token', self.token)
        http.on('POST', 'https://www.googleapis.com/upload/youtube/v3/videos', self.init)
        http.on('PUT', SESSION, self.put)
        http.on('GET', 'https://www.googleapis.com/youtube/v3/videos', self.videos)
        http.on('GET', 'https://www.googleapis.com/youtube/v3/playlistItems', self.playlist_items)

    def token(self, **kw):
        if self.token_error:
            return 400, json.dumps({'error': self.token_error, 'error_description': 'Token has been expired or revoked.'}).encode(), {}
        return 200, json.dumps({'access_token': 'ya29.test', 'expires_in': 3599}).encode(), {}

    def init(self, **kw):
        self.inits += 1
        self.metadata = kw['json']
        assert kw['headers']['Authorization'] == 'Bearer ya29.test'
        return 200, b'', {'Location': SESSION}

    def put(self, **kw):
        if self.session_gone:
            return 404, b'{}', {}
        cr = kw['headers']['Content-Range']
        if cr.startswith('bytes */'):
            return self._state()
        if self.fail_next_chunk:
            exc, self.fail_next_chunk = self.fail_next_chunk, None
            # The provider may or may not have stored part of the chunk before the drop.
            self.received += kw['data'][:len(kw['data']) // 2]
            raise exc
        start, end = map(int, re.match(r'bytes (\d+)-(\d+)/', cr).groups())
        assert start == len(self.received), 'client must resume exactly at the confirmed offset'
        self.received += kw['data']
        return self._state()

    def _state(self):
        if len(self.received) >= self.total:
            body = dict(self.video, snippet={'tags': self.metadata['snippet']['tags']})
            return 200, json.dumps(body).encode(), {}
        hdr = {'Range': f'bytes=0-{len(self.received) - 1}'} if self.received else {}
        return 308, b'', hdr

    def videos(self, url, **kw):
        ids = kw['params']['id'].split(',')
        items = []
        for i in ids:
            if i == self.video['id']:
                items.append(self.video)
            for p in self.playlist:
                if p['id'] == i:
                    items.append(p)
        return 200, json.dumps({'items': items}).encode(), {}

    def playlist_items(self, url, **kw):
        return 200, json.dumps({'items': [{'contentDetails': {'videoId': p['id']}} for p in self.playlist]}).encode(), {}


@pytest.fixture
def studio(db, clock, fake_http, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    clock.t = datetime(2026, 9, 21, 9, 0, tzinfo=ZoneInfo('Australia/Adelaide')).timestamp()
    ready_to_publish(db)
    monkeypatch.setattr(publisher, 'CHUNK', 300)
    vid = approved_video(db, size=1000)
    orchestrator.tick('o1', d=db)
    v = videos.get(vid, db)
    assert v['slot_id'], 'video should be assigned to the next slot'
    yt = FakeYouTube(fake_http, 1000)
    return {'db': db, 'vid': vid, 'yt': yt, 'clock': clock, 'http': fake_http}


def upload_until_done(s, max_runs=10):
    outcomes = []
    for _ in range(max_runs):
        t, out = run_one('publish')
        if t is None:
            s['clock'].advance(60)
            continue
        outcomes.append((t['kind'], out))
        if t['kind'] == 'video.upload' and out in ('succeeded', 'blocked', 'failed', 'dead'):
            break
    return outcomes


def test_resumable_upload_requests_private_scheduled_video_with_disclosures(studio):
    outcomes = upload_until_done(studio)
    assert outcomes[-1] == ('video.upload', 'succeeded')
    yt = studio['yt']
    md = yt.metadata
    assert md['status']['privacyStatus'] == 'private'
    assert md['status']['publishAt'].endswith('Z')
    assert md['status']['selfDeclaredMadeForKids'] is False
    assert md['status']['containsSyntheticMedia'] is True
    assert 'Not captured gameplay' in md['snippet']['description']
    up = publisher.get_upload(studio['vid'], studio['db'])
    assert up['marker'] in md['snippet']['tags']
    v = videos.get(studio['vid'], studio['db'])
    assert v['status'] == 'uploaded_private', 'an accepted upload is not "published"'
    assert v['youtube_video_id'] == 'yt_abc123'
    assert yt.received == open(publisher.os.path.join(publisher.config.DATA_DIR,
                                                     studio['db'].one('SELECT file FROM renders')['file']), 'rb').read()


def test_interrupted_chunk_resumes_from_confirmed_offset(studio):
    yt = studio['yt']
    yt.fail_next_chunk = requests.exceptions.ReadTimeout('read timed out')
    t, out = run_one('publish')
    assert (t['kind'], out) == ('video.upload', 'waiting')
    assert 'resuming' in studio['db'].one('SELECT last_error FROM tasks WHERE id=?', (t['id'],))['last_error']
    studio['clock'].advance(30)
    outcomes = upload_until_done(studio)
    assert outcomes[-1] == ('video.upload', 'succeeded')
    assert yt.inits == 1, 'resuming must not open a second upload'
    assert len(yt.received) == 1000


def test_worker_crash_mid_upload_resumes_without_duplicate(studio):
    yt = studio['yt']
    db = studio['db']
    t = jobs.claim('crashed-worker', ['publish'], lease_s=60, d=db)
    # Simulate: the crashed worker initiated the session and sent one chunk, then died.
    from blox.tasks import Ctx, ensure_loaded
    ensure_loaded()

    class Boom(Exception):
        pass
    calls = {'n': 0}
    real_put = yt.put

    def dying_put(**kw):
        r = real_put(**kw)
        if not kw['headers']['Content-Range'].startswith('bytes */'):
            calls['n'] += 1
            if calls['n'] == 1:
                raise Boom('worker process killed')
        return r
    yt.http.routes = [(m, p, dying_put if p == SESSION else f) for m, p, f in yt.http.routes]
    with pytest.raises(Exception):
        publisher.upload(Ctx(t, 'crashed-worker', 60))
    studio['clock'].advance(61 + 3600)
    outcomes = upload_until_done(studio)
    assert outcomes[-1] == ('video.upload', 'succeeded')
    assert yt.inits == 1 and len(yt.received) == 1000


def test_second_upload_task_never_uploads_again(studio):
    upload_until_done(studio)
    db = studio['db']
    jobs.enqueue('video.upload', {'slot_id': videos.get(studio['vid'], db)['slot_id']}, video_id=studio['vid'],
                 idempotency_key='upload:again', d=db)
    t, out = run_one('publish')
    while t and t['kind'] != 'video.upload':
        t, out = run_one('publish')
    assert out == 'succeeded'
    assert studio['yt'].inits == 1
    assert studio['http'].count('POST', 'https://www.googleapis.com/upload/youtube/v3/videos') == 1


def test_lost_session_after_all_bytes_is_reconciled_by_marker(studio):
    yt = studio['yt']
    db = studio['db']
    yt.fail_next_chunk = None
    # Send everything, but lose the final response, then lose the session.
    orig_state = yt._state

    def no_final(**kw):
        return 308, b'', ({'Range': f'bytes=0-{len(yt.received) - 1}'} if yt.received else {})
    yt._state = no_final
    t, out = run_one('publish')
    assert out == 'waiting'
    assert publisher.get_upload(studio['vid'], db)['bytes_confirmed'] == 1000
    yt._state = orig_state
    yt.session_gone = True
    up = publisher.get_upload(studio['vid'], db)
    yt.playlist = [{'id': 'yt_found1', 'snippet': {'tags': ['story', up['marker']]}}]
    studio['clock'].advance(60)
    t, out = run_one('publish')
    assert out == 'succeeded'
    assert videos.get(studio['vid'], db)['youtube_video_id'] == 'yt_found1'
    assert yt.inits == 1


def test_lost_session_after_all_bytes_never_reuploads(studio):
    yt = studio['yt']
    db = studio['db']
    yt._state = lambda **kw: (308, b'', {'Range': f'bytes=0-{len(yt.received) - 1}'} if yt.received else {})
    run_one('publish')
    yt.session_gone = True
    outs = []
    for _ in range(8):
        studio['clock'].advance(200)
        t, out = run_one('publish')
        if t:
            outs.append(out)
        if out == 'blocked':
            break
    assert outs[-1] == 'blocked'
    assert yt.inits == 1
    v = videos.get(studio['vid'], db)
    assert v['status'] == 'needs_review' and 'will not upload a duplicate' in v['status_reason']


def test_expired_authorization_holds_for_reconnect(studio):
    yt = studio['yt']
    yt.token_error = 'invalid_grant'
    t, out = run_one('publish')
    assert out == 'blocked'
    db = studio['db']
    v = videos.get(studio['vid'], db)
    assert v['status'] == 'needs_credentials'
    assert 'Reconnect' in store.get('youtube_auth_error', '', db)
    assert any('Reconnect' in b for b in orchestrator.publish_blockers(db, __import__('blox.prefs').prefs.get(db)))
    assert yt.inits == 0


def test_access_token_refreshed_once_on_401(studio):
    yt = studio['yt']
    seen = []
    real_init = yt.init

    def init(**kw):
        seen.append(1)
        if len(seen) == 1:
            return 401, json.dumps({'error': {'code': 401, 'message': 'Invalid Credentials'}}).encode(), {}
        return real_init(**kw)
    yt.http.on('POST', 'https://www.googleapis.com/upload/youtube/v3/videos', init)
    outcomes = upload_until_done(studio)
    assert outcomes[-1] == ('video.upload', 'succeeded')
    assert studio['http'].count('POST', 'https://oauth2.googleapis.com/token') == 2


def _verify(studio):
    for _ in range(6):
        t, out = run_one('publish')
        if t and t['kind'] == 'video.verify':
            return out
        studio['clock'].advance(120)
    raise AssertionError('no verify task ran')


def test_processing_then_scheduled_then_published_only_when_observed(studio):
    upload_until_done(studio)
    db, yt, vid = studio['db'], studio['yt'], studio['vid']
    assert _verify(studio) == 'waiting'
    assert videos.get(vid, db)['status'] == 'uploaded_private'
    yt.video['status']['uploadStatus'] = 'processed'
    yt.video['processingDetails']['processingStatus'] = 'succeeded'
    yt.video['status']['publishAt'] = yt.metadata['status']['publishAt']
    studio['clock'].advance(120)
    assert _verify(studio) == 'waiting'
    assert videos.get(vid, db)['status'] == 'scheduled'
    slot = db.one('SELECT * FROM slots WHERE video_id=?', (vid,))
    assert slot['status'] == 'scheduled'
    # At the slot time YouTube flips it public; only then is it "published".
    studio['clock'].t = slot['slot_at'] + 120
    yt.video['status']['privacyStatus'] = 'public'
    assert _verify(studio) == 'succeeded'
    assert videos.get(vid, db)['status'] == 'published'
    assert db.one('SELECT status FROM slots WHERE id=?', (slot['id'],))['status'] == 'published'


def test_unaudited_project_restriction_is_reported(studio):
    upload_until_done(studio)
    db, yt, vid = studio['db'], studio['yt'], studio['vid']
    yt.video['status'].update(uploadStatus='processed', privacyStatus='private')  # publishAt dropped
    yt.video['processingDetails']['processingStatus'] = 'succeeded'
    studio['clock'].advance(120)
    assert _verify(studio) == 'succeeded'
    v = videos.get(vid, db)
    assert v['status'] == 'needs_review' and 'audit' in v['status_reason']
    assert store.get('publishing_restriction', None, db)
    assert any('refused scheduled publishing' in b for b in orchestrator.publish_blockers(db, __import__('blox.prefs').prefs.get(db)))


def test_not_public_after_grace_is_flagged_not_marked_published(studio):
    upload_until_done(studio)
    db, yt, vid = studio['db'], studio['yt'], studio['vid']
    yt.video['status'].update(uploadStatus='processed', publishAt=yt.metadata['status']['publishAt'])
    yt.video['processingDetails']['processingStatus'] = 'succeeded'
    studio['clock'].advance(120)
    _verify(studio)
    slot = db.one('SELECT * FROM slots WHERE video_id=?', (vid,))
    studio['clock'].t = slot['slot_at'] + 46 * 60
    assert _verify(studio) == 'succeeded'
    v = videos.get(vid, db)
    assert v['status'] == 'needs_review' and 'after the scheduled time' in v['status_reason']


def test_processing_failure_blocks(studio):
    upload_until_done(studio)
    yt = studio['yt']
    yt.video['status'].update(uploadStatus='rejected', rejectionReason='duplicate')
    studio['clock'].advance(120)
    _verify(studio)
    v = videos.get(studio['vid'], studio['db'])
    assert v['status'] == 'blocked' and 'duplicate' in v['status_reason']


def test_preflight_releases_slot_when_audience_not_chosen(studio):
    db = studio['db']
    p = __import__('blox.prefs').prefs.get(db)
    p['publishing']['made_for_kids'] = None
    __import__('blox.prefs').prefs.put(p, db)
    slot_id = videos.get(studio['vid'], db)['slot_id']
    t, out = run_one('publish')
    assert out == 'succeeded'  # completed by skipping, nothing sent
    assert studio['yt'].inits == 0
    assert videos.get(studio['vid'], db)['slot_id'] is None
    row = db.one('SELECT status, reason FROM slots WHERE id=?', (slot_id,))
    assert row['status'] == 'skipped' and 'made-for-kids' in row['reason']


@pytest.mark.parametrize('hdr,offset', [(None, 0), ('bytes=0-299', 300), ('bytes=0--1', 0), ('bytes=5-9', 0),
                                        ('garbage', 0)])
def test_range_header_parsing(hdr, offset):
    assert publisher.confirmed_offset(hdr) == offset


def test_metadata_never_exceeds_limits(db):
    from blox import prefs
    vid = approved_video(db, title='x' * 300)
    v = videos.get(vid, db)
    v['metadata']['publish_metadata'] = {'title': 'T' * 300, 'description': 'd' * 9000, 'tags': ['tag%d' % i for i in range(300)]}
    md = publisher.build_metadata(v, 1_800_000_000, prefs.get(db))
    assert len(md['snippet']['title']) <= 100
    assert len(md['snippet']['description']) <= 5000
    assert sum(len(t) + 2 for t in md['snippet']['tags']) <= 450
    assert parse_qs(urlparse(SESSION).query)['upload_id'] == ['sess1']
