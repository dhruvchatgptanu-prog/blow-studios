"""Live (opt-in twice): upload one 3-second PRIVATE test video without scheduling, then verify processing."""
import json
import os
import time

import pytest

from blox import config, media, vault
from blox.youtube import oauth

from .conftest import need


def test_private_upload(db, tmp_path):
    if os.environ.get('LIVE_UPLOAD') != '1':
        pytest.skip('Set LIVE_UPLOAD=1 to upload a private test video to your channel')
    need('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'LIVE_YOUTUBE_REFRESH_TOKEN')
    vault.put('YOUTUBE_REFRESH_TOKEN', os.environ['LIVE_YOUTUBE_REFRESH_TOKEN'], db)
    path = str(tmp_path / 'test.mp4')
    media.run([config.FFMPEG_BIN, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=1080x1920:rate=30',
               '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
               '-c:a', 'aac', '-shortest', path])
    size = os.path.getsize(path)
    md = {'snippet': {'title': 'Blox Studio live test (private, safe to delete)', 'categoryId': '20'},
          'status': {'privacyStatus': 'private', 'selfDeclaredMadeForKids': False}}
    r = oauth.authed('POST', 'https://www.googleapis.com/upload/youtube/v3/videos',
                     params={'uploadType': 'resumable', 'part': 'snippet,status'},
                     headers={'X-Upload-Content-Type': 'video/mp4', 'X-Upload-Content-Length': str(size),
                              'Content-Type': 'application/json; charset=UTF-8'}, json=md, timeout=(10, 60))
    session = r.headers['Location']
    with open(path, 'rb') as f:
        r = oauth.authed('PUT', session, data=f.read(), headers={'Content-Type': 'video/mp4'}, timeout=(10, 300))
    yid = r.json()['id']
    for _ in range(20):
        r = oauth.authed('GET', 'https://www.googleapis.com/youtube/v3/videos',
                         params={'part': 'status,processingDetails', 'id': yid}, timeout=(10, 30))
        st = r.json()['items'][0]
        if st['status'].get('uploadStatus') == 'processed':
            break
        time.sleep(15)
    print(json.dumps({'youtube_id': yid, 'status': st['status']}))
    assert st['status']['privacyStatus'] == 'private'
