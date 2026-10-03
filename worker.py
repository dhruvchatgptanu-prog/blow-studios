"""Durable staged worker. No paid API call runs until the user explicitly queues/enables it."""
import json, time, secrets, copy, logging
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from core import *
from providers import Blocked, plan, video_create, video_status, download, speech, upload_step
from render import render

def reserve(jid,cfg):
    day=datetime.now(ZoneInfo(cfg['timezone'])).date().isoformat()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM reservations WHERE job=?',(jid,)).fetchone(): return
        count,total=c.execute('SELECT COUNT(*),COALESCE(SUM(estimate),0) FROM reservations WHERE day=?',(day,)).fetchone()
        if count>=cfg['daily_video_cap'] or total+cfg['estimate_per_video']>cfg['daily_estimate_cap']:
            raise Blocked('Daily generation limit reached. Increase limits or retry tomorrow.')
        c.execute('INSERT INTO reservations VALUES (?,?,?)',(jid,day,cfg['estimate_per_video']))

def schedule_tick(now=None):
    now=now or time.time()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT value FROM settings WHERE key='preferences'").fetchone()
        cfg=DEFAULTS | (json.loads(row[0]) if row else {})
        if not cfg['enabled'] or cfg['next_run']>now: return
        r=c.execute('SELECT body FROM projects WHERE id=?',(cfg['template_id'],)).fetchone()
        if not r:
            cfg['enabled']=False
            c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('preferences',json.dumps(cfg))); return
        # No catch-up burst after downtime. One new job then advance from now.
        p=json.loads(r[0]); p['id']=secrets.token_hex(12); p['scenes']=[]; p['output']=''; p['youtube_id']=''
        p['topic']+=' Create a fresh original story variation. Story seed '+secrets.token_hex(6)+'.'
        c.execute('INSERT INTO projects VALUES (?,?,?)',(p['id'],json.dumps(p),now))
        jid=enqueue(p['id'],'auto',slot='auto:'+str(cfg['next_run']),c=c)
        state={'snapshot':p,'publish':{k:cfg[k] for k in ['privacy','audience','synthetic']},'automatic':True,'mode':cfg['mode']}
        c.execute('UPDATE jobs SET state=? WHERE id=?',(json.dumps(state),jid))
        cfg['next_run']=now+cfg['interval_hours']*3600
        c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('preferences',json.dumps(cfg)))

def claim():
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        r=c.execute("SELECT id FROM jobs WHERE status IN ('queued','waiting') AND due<=? ORDER BY created LIMIT 1",(time.time(),)).fetchone()
        if not r: return None
        c.execute("UPDATE jobs SET status='running' WHERE id=?",(r[0],)); return r[0]

def step(jid):
    j=job(jid); state=j['state']; p=state.setdefault('snapshot',project(j['project']))
    def persist(): update_job(jid,state=state)
    def wait(seconds=5):
        if job(jid)['status']=='cancelled': return
        update_job(jid,state=state,status='waiting',due=time.time()+seconds,attempts=0)
    def active():
        if job(jid)['status']=='cancelled': raise Blocked('Cancelled')
    if j['kind'] in ['produce','auto']: reserve(jid,settings())
    if not p['scenes'] or j['kind']=='plan':
        if state.get('plan_starting'): raise Blocked('Script request was interrupted. Reconcile provider usage before a new job.')
        state['phase']='Writing story'; state['plan_starting']=True; persist()
        result=plan(p); p.update({k:result[k] for k in ['title','description','scenes']}); validate(p)
        active(); state['plan_starting']=False; save_project(p); persist()
        if j['kind']=='plan': update_job(jid,status='done'); return
        wait(); return
    if not state.get('output') and j['kind']!='upload':
        clips=state.setdefault('clips',{})
        for i,s in enumerate(p['scenes']):
            active(); k=str(i)
            if s.get('clip'): continue
            item=clips.setdefault(k,{})
            state['phase']=f'Animating scene {i+1} of {len(p["scenes"])}'
            if item.get('file'): continue
            if not item.get('task'):
                if item.get('starting'): raise Blocked(f'Scene {i+1} submission outcome is unknown. Check Runway before starting a new job.')
                item['starting']=True; persist()
                item['task']=video_create(p,s); item['starting']=False; persist(); wait(12); return
            result=video_status(item['task'])
            if result['status'] in ['FAILED','CANCELED','CANCELLED']: raise Blocked(f'Scene {i+1} generation failed. Revise the scene and start a new job.')
            if result['status']!='SUCCEEDED': wait(12); return
            name=f'{jid}_scene{i}.mp4'; download(result['output'][0],FILES/name)
            item['file']=name; persist(); wait(); return
        for i,s in enumerate(p['scenes']):
            if not p['narration'] or not s['narration'].strip(): continue
            path=FILES/f'{jid}_voice{i}.mp3'
            if path.exists(): continue
            if state.get('tts_starting')==i: raise Blocked('Narration request interrupted. Check provider usage before a new job.')
            state['phase']=f'Recording narration {i+1}'; state['tts_starting']=i; persist()
            speech(s['narration'],path); state['tts_starting']=None; persist(); wait(); return
        active(); state['phase']='Rendering MP4'; persist()
        state['output']=render(p,state,jid); active(); p['output']=state['output']; save_project(p); persist()
        if not state.get('automatic'):
            state['phase']='Ready to preview'; update_job(jid,state=state,status='done'); return
        wait(); return
    if j['kind']=='upload' or state.get('automatic'):
        if state.get('automatic') and (state.get('mode')!='autopilot' or not settings()['enabled']):
            state['phase']='Waiting for review'; update_job(jid,state=state,status='review'); return
        active(); state['phase']='Uploading to YouTube'; persist()
        vid=upload_step(p,state,persist)
        if not vid: wait(); return
        p['youtube_id']=vid; save_project(p); state['phase']='Uploaded'; update_job(jid,state=state,status='done')

def tick():
    set_setting('worker_heartbeat',time.time()); schedule_tick()
    jid=claim()
    if not jid: return False
    try: step(jid)
    except (requests.Timeout,requests.ConnectionError) as e:
        j=job(jid)
        if j['status']=='cancelled': return True
        n=j['attempts']+1
        update_job(jid,status='waiting' if n<4 else 'blocked',attempts=n,due=time.time()+min(300,15*2**n),
          error='Network interrupted. Safe steps retry; uncertain paid submissions stop for review.')
    except Exception as e:
        if job(jid)['status']!='cancelled': update_job(jid,status='blocked',error=str(e)[:500])
    return True

if __name__=='__main__':
    import fcntl
    lock=open(ROOT/'worker.lock','w')
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('Another worker already runs against this data directory.')
    with db() as c: c.execute("UPDATE jobs SET status='waiting' WHERE status='running'")
    while True:
        try: worked=tick()
        except Exception: logging.exception('Worker tick failed'); worked=False
        time.sleep(2 if worked else 10)
