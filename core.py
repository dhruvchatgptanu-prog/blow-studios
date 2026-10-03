"""Persistent single-owner studio. Run one worker against a local SQLite volume."""
import os, json, sqlite3, secrets, time, math
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet

ROOT = Path(os.environ.get('BLOX_DATA', Path(__file__).parent / 'data')).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
os.chmod(ROOT, 0o700)
FILES = ROOT / 'files'; FILES.mkdir(exist_ok=True)
def db():
    c = sqlite3.connect(ROOT / 'studio.db', timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c

def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, body TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,
          status TEXT NOT NULL, state TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
          attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL, created REAL NOT NULL, slot TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reservations (job TEXT PRIMARY KEY, day TEXT NOT NULL, estimate REAL NOT NULL);
        ''')
    key = ROOT / 'vault.key'
    try:
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f: f.write(Fernet.generate_key())
    except FileExistsError: pass

init()
DEFAULTS = {'text_model':'gpt-4.1-mini','video_model':'gen4.5','voice':'alloy',
    'timezone':'Australia/Adelaide','interval_hours':24,'daily_video_cap':2,
    'daily_estimate_cap':15,'estimate_per_video':5,'enabled':False,'mode':'review',
    'template_id':'','next_run':0,'privacy':'private','audience':None,'synthetic':None}

def settings():
    with db() as c: row=c.execute("SELECT value FROM settings WHERE key='preferences'").fetchone()
    return DEFAULTS | (json.loads(row[0]) if row else {})
def set_setting(key, value):
    with db() as c: c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value)))
def setting(key, default=None):
    with db() as c: row=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default
def secret(name):
    val=setting('secret:'+name)
    return Fernet((ROOT/'vault.key').read_bytes()).decrypt(val.encode()).decode() if val else os.getenv(name,'')
def save_secret(name,value):
    set_setting('secret:'+name,Fernet((ROOT/'vault.key').read_bytes()).encrypt(value.encode()).decode())
def project(pid):
    with db() as c: r=c.execute('SELECT body FROM projects WHERE id=?',(pid,)).fetchone()
    if not r: raise ValueError('Project not found')
    return json.loads(r[0])
def save_project(p):
    with db() as c: c.execute('INSERT OR REPLACE INTO projects VALUES (?,?,?)',(p['id'],json.dumps(p),time.time()))
def new_project(title='Untitled story'):
    p={'id':secrets.token_hex(12),'title':title,'topic':'An original Roblox-style obstacle-course adventure',
       'character':'An original block-shaped hero in a blue hoodie, expressive eyebrows and round eyes.',
       'format':'shorts','reference':'','music':'','music_volume':0.15,'voice_volume':1,
       'narration':True,'captions':True,'description':'','scenes':[], 'output':'','youtube_id':''}
    save_project(p); return p
def asset(aid, kind=None):
    with db() as c: r=c.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
    if not r or (kind and r['kind']!=kind): raise ValueError('Asset not found or wrong type')
    return FILES / aid
def finite(x, lo, hi):
    x=float(x)
    if not math.isfinite(x) or not lo<=x<=hi: raise ValueError(f'Value must be between {lo} and {hi}')
    return x
def validate(p):
    for k,limit in [('title',100),('topic',2000),('character',1800),('description',4500)]:
        if not isinstance(p.get(k),str) or len(p[k])>limit: raise ValueError(f'Invalid {k}')
    if not p['title'].strip(): raise ValueError('Give the video a title')
    if p.get('format') not in ['shorts','landscape']: raise ValueError('Invalid video format')
    if p.get('reference'): asset(p['reference'],'image')
    if p.get('music'): asset(p['music'],'audio')
    for k in ['music_volume','voice_volume']: p[k]=finite(p.get(k,1),0,1)
    if not isinstance(p.get('scenes'),list) or len(p['scenes'])>24: raise ValueError('Use up to 24 scenes')
    for s in p['scenes']:
        for k in ['setting','expression','action','camera','narration']:
            if not isinstance(s.get(k),str) or len(s[k])>1000: raise ValueError(f'Invalid scene {k}')
        s['duration']=int(finite(s.get('duration',5),5,10))
        if s['duration'] not in [5,10]: raise ValueError('Scene duration must be 5 or 10 seconds')
        s['trim']=finite(s.get('trim',0),0,3600)
        if s.get('clip'): asset(s['clip'],'video')
    return p
def job(jid):
    with db() as c: r=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
    if not r: raise ValueError('Job not found')
    d=dict(r); d['state']=json.loads(d['state']); return d
def update_job(jid,**fields):
    if 'state' in fields: fields['state']=json.dumps(fields['state'])
    assert set(fields)<=set(['state','status','error','attempts','due'])
    with db() as c: c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),jid))
def enqueue(pid,kind='produce',slot=None,c=None,state=None):
    own=c is None; c=c or db()
    try:
        if own: c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM jobs WHERE project=? AND status IN ('queued','running','waiting','review')",(pid,)).fetchone():
            raise ValueError('This project already has an active job')
        jid=secrets.token_hex(12); t=time.time()
        c.execute('INSERT INTO jobs(id,project,kind,status,due,created,slot,state) VALUES (?,?,?,?,?,?,?,?)',(jid,pid,kind,'queued',t,t,slot,json.dumps(state or {})))
        if own: c.commit()
        return jid
    finally:
        if own: c.close()
def busy(pid):
    with db() as c: return bool(c.execute("SELECT 1 FROM jobs WHERE project=? AND status IN ('queued','running','waiting','review')",(pid,)).fetchone())
def ready(upload=False):
    missing=[]
    if not secret('OPENAI_API_KEY'): missing.append('OpenAI API key')
    if not secret('RUNWAYML_API_SECRET'): missing.append('Runway API key')
    import shutil
    if not shutil.which('ffmpeg'): missing.append('FFmpeg')
    if upload:
        if not secret('YOUTUBE_REFRESH_TOKEN'): missing.append('YouTube connection')
        s=settings()
        if type(s['audience']) is not bool: missing.append('audience choice')
        if type(s['synthetic']) is not bool: missing.append('synthetic-content disclosure choice')
    return missing
