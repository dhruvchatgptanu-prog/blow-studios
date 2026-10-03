import json, time, subprocess
import pytest, requests
import core, worker, providers
from core import *
H={'X-CSRF':'test-csrf'}

def test_auth_and_csrf(client):
    assert client.get('/').status_code==200
    assert client.post('/api/projects',json={}).status_code==403
    assert client.post('/api/projects',json={},headers=H).status_code==200
    from app import app
    assert app.test_client().get('/api/state').status_code==401

def test_secret_redaction(client):
    r=client.post('/api/secrets',json={'OPENAI_API_KEY':'private-test-value'},headers=H)
    assert r.status_code==200
    assert secret('OPENAI_API_KEY')=='private-test-value'
    assert 'private-test-value' not in client.get('/api/state').get_data(as_text=True)
    with db() as c: encrypted=c.execute("SELECT value FROM settings WHERE key='secret:OPENAI_API_KEY'").fetchone()[0]
    assert 'private-test-value' not in encrypted

def test_edit_validate_and_lock(client,project):
    p=project; p['scenes'][0]['trim']=-1
    assert client.put('/api/projects/'+p['id'],json=p,headers=H).status_code==400
    p['scenes'][0]['trim']=0
    enqueue(p['id'])
    assert client.put('/api/projects/'+p['id'],json=p,headers=H).status_code==400

def test_missing_credentials_block_autopilot(client,project):
    r=client.post('/api/settings',json={'enabled':True,'mode':'autopilot','template_id':project['id'],'audience':False,'synthetic':True},headers=H)
    assert r.status_code==400 and settings()['enabled'] is False
    assert client.post('/api/projects/'+project['id']+'/queue',json={'kind':'produce'},headers=H).status_code==400

def test_queue_claim_once(project):
    jid=enqueue(project['id'])
    with pytest.raises(ValueError): enqueue(project['id'])
    assert worker.claim()==jid
    assert worker.claim() is None

def test_schedule_no_duplicate_or_catchup(project):
    cfg=settings()|{'enabled':True,'template_id':project['id'],'next_run':1,'interval_hours':24}
    set_setting('preferences',cfg)
    worker.schedule_tick(1000); worker.schedule_tick(1000)
    with db() as c: assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
    assert settings()['next_run']==1000+24*3600
    with db() as c: j=dict(c.execute('SELECT * FROM jobs').fetchone())
    assert json.loads(j['state'])['snapshot']['scenes']==[]

def test_budget_reservation_is_idempotent():
    cfg=settings()|{'daily_video_cap':1,'daily_estimate_cap':5,'estimate_per_video':5}
    worker.reserve('a',cfg); worker.reserve('a',cfg)
    with pytest.raises(providers.Blocked): worker.reserve('b',cfg)

def test_paid_submission_not_duplicated_after_timeout(project,monkeypatch):
    count=[]
    def fail(*args): count.append(1); raise requests.Timeout()
    monkeypatch.setattr(worker,'video_create',fail)
    jid=enqueue(project['id'],state={'snapshot':project})
    worker.tick()
    update_job(jid,due=0)
    worker.tick()
    assert len(count)==1 and job(jid)['status']=='blocked'

def test_pause_blocks_pending_automatic_upload(project,monkeypatch):
    st={'snapshot':project,'output':'done.mp4','automatic':True,'mode':'autopilot'}
    jid=enqueue(project['id'],'auto',state=st)
    monkeypatch.setattr(worker,'upload_step',lambda *a:pytest.fail('Upload should not run'))
    worker.step(jid)
    assert job(jid)['status']=='review'

def test_resumable_upload_recovers_completed_response(project,monkeypatch):
    f=FILES/'finished.mp4';f.write_bytes(b'example')
    st={'output':f.name,'upload_session':'https://www.googleapis.com/upload/example','publish':{'audience':False,'synthetic':True,'privacy':'private'}}
    class R:
        status_code=200;ok=True
        def json(self):return {'id':'youtube-test-id'}
    monkeypatch.setattr(providers,'google_token',lambda:'test')
    monkeypatch.setattr(providers.requests,'put',lambda *a,**kw:R())
    monkeypatch.setattr(providers.requests,'post',lambda *a,**kw:pytest.fail('Must not initiate a second upload'))
    assert providers.upload_step(project,st,lambda:None)=='youtube-test-id'
    assert providers.upload_step(project,st,lambda:None)=='youtube-test-id'

def test_oauth_state_rejected(client):
    assert client.get('/oauth/callback?state=bogus&code=nope').status_code==400

def test_private_file_boundary(client):
    assert client.get('/media/vault.key').status_code==404

def test_real_ffmpeg_render_with_captions_and_music(project):
    from render import render,duration,run
    p=project;p['narration']=False;p['format']='landscape'
    clip='test-source.mp4';music='test-music.wav'
    run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=navy:s=640x360:r=30','-t','5','-c:v','libx264','-pix_fmt','yuv420p',str(FILES/clip)])
    run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=220:duration=5',str(FILES/music)])
    with db() as c:
        c.execute('INSERT INTO assets VALUES (?,?,?)',(clip,'Test footage','video'))
        c.execute('INSERT INTO assets VALUES (?,?,?)',(music,'Test sound','audio'))
    p['scenes'][0]['clip']=clip;p['music']=music
    result=render(p,{},'render-test')
    assert 4.8<duration(FILES/result)<5.3
    probe=json.loads(run(['ffprobe','-v','error','-show_streams','-of','json',str(FILES/result)]))
    video=next(s for s in probe['streams'] if s['codec_type']=='video')
    assert (video['width'],video['height'])==(1280,720)
    assert any(s['codec_type']=='audio' for s in probe['streams'])
