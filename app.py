import os, time, secrets, hmac, json, shutil, urllib.parse
from pathlib import Path
from flask import Flask, request, session, jsonify, render_template, redirect, send_from_directory
from werkzeug.exceptions import HTTPException
from core import *
from providers import google_token, check
import requests
from PIL import Image
from render import duration

app=Flask(__name__)
key=os.getenv('SESSION_SECRET','')
if len(key)<32 or key.startswith('replace-'):
    # Persistent random development key; public hosting still requires a real admin password.
    key=setting('session_key') or secrets.token_hex(32); set_setting('session_key',key)
app.secret_key=key
app.config.update(MAX_CONTENT_LENGTH=250*1024*1024,SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE')=='1')
attempts={}

@app.before_request
def protect():
    if request.path.startswith('/static/') or request.path=='/login': return
    if not session.get('owner'):
        return (jsonify(error='Sign in first'),401) if request.path.startswith('/api/') else redirect('/login')
    if request.method not in ['GET','HEAD','OPTIONS']:
        if not hmac.compare_digest(request.headers.get('X-CSRF',''),session.get('csrf','!')):
            return jsonify(error='Refresh the page and try again'),403

@app.after_request
def headers(r):
    r.headers['X-Content-Type-Options']='nosniff'; r.headers['X-Frame-Options']='DENY'
    r.headers['Referrer-Policy']='same-origin'
    r.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"
    r.headers['Cache-Control']='no-store'; return r

@app.errorhandler(Exception)
def error(e):
    if isinstance(e,HTTPException): return jsonify(error=e.description),e.code
    if isinstance(e,(ValueError,KeyError,TypeError)): return jsonify(error=str(e)),400
    app.logger.exception('Request failed')
    return jsonify(error='Request failed. Check server logs; no credentials are shown here.'),500

@app.route('/login',methods=['GET','POST'])
def login():
    message=''; configured=os.getenv('ADMIN_PASSWORD','')
    if not configured or configured.startswith('replace-'):
        return render_template('login.html',error='Set ADMIN_PASSWORD in .env, then restart the app.'),503
    if request.method=='POST':
        # Origin + one-use session nonce protect login against cross-site forms.
        if not hmac.compare_digest(request.form.get('nonce',''),session.pop('login_nonce','!')): message='Refresh and try again.'
        else:
            ip=request.remote_addr; recent=[t for t in attempts.get(ip,[]) if t>time.time()-60]
            attempts[ip]=recent+[time.time()]
            if len(recent)>=8: message='Too many attempts. Wait one minute.'
            elif hmac.compare_digest(request.form.get('password',''),configured):
                session.clear(); session['owner']=True; session['csrf']=secrets.token_hex(24); return redirect('/')
            else: message='Incorrect password.'
    session['login_nonce']=secrets.token_hex(24)
    return render_template('login.html',error=message)

@app.get('/')
def home(): return render_template('index.html',csrf=session['csrf'])
@app.post('/api/logout')
def logout(): session.clear(); return jsonify(ok=True)
@app.get('/api/state')
def state():
    with db() as c:
        projects=[json.loads(r[0]) for r in c.execute('SELECT body FROM projects ORDER BY updated DESC')]
        jobs=[]
        for r in c.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 80'):
            j=dict(r); st=json.loads(j.pop('state')); j['phase']=st.get('phase','Queued'); jobs.append(j)
        assets=[dict(r) for r in c.execute('SELECT * FROM assets')]
    return jsonify(projects=projects,jobs=jobs,assets=assets,settings=settings(),
       connections={'openai':bool(secret('OPENAI_API_KEY')),'runway':bool(secret('RUNWAYML_API_SECRET')),
       'google':bool(secret('GOOGLE_CLIENT_ID') and secret('GOOGLE_CLIENT_SECRET')),
       'youtube':bool(secret('YOUTUBE_REFRESH_TOKEN')),'channel':setting('channel',{}),
       'ffmpeg':bool(shutil.which('ffmpeg')),'worker':time.time()-setting('worker_heartbeat',0)<40},
       redirect_uri=os.getenv('PUBLIC_URL','http://localhost:8000').rstrip('/')+'/oauth/callback')

@app.post('/api/projects')
def create_project(): return jsonify(new_project())
@app.put('/api/projects/<pid>')
def edit_project(pid):
    old=project(pid)
    if busy(pid): raise ValueError('Wait for the active job, or cancel it before editing')
    incoming=request.get_json()
    editable=['title','topic','character','format','reference','music','music_volume','voice_volume','narration','captions','description','scenes']
    p=old|{k:incoming[k] for k in editable if k in incoming}; validate(p)
    # Any edit invalidates the previous render and requires explicit re-render before publishing.
    if any(p.get(k)!=old.get(k) for k in editable): p['output']=''
    save_project(p); return jsonify(p)
@app.post('/api/projects/<pid>/clone')
def clone(pid):
    p=project(pid); p.update(id=secrets.token_hex(12),output='',youtube_id='',title=p['title']+' copy')
    save_project(p); return jsonify(p)
@app.post('/api/projects/<pid>/queue')
def queue(pid):
    p=validate(project(pid)); kind=request.json.get('kind','produce')
    if kind not in ['plan','produce','upload']: raise ValueError('Invalid job kind')
    if kind=='plan' and not secret('OPENAI_API_KEY'): raise ValueError('Connect OpenAI first')
    if kind=='produce':
        if not shutil.which('ffmpeg'): raise ValueError('Install FFmpeg')
        if (not p['scenes'] or p['narration']) and not secret('OPENAI_API_KEY'): raise ValueError('Connect OpenAI for scripts and narration')
        if (not p['scenes'] or any(not s.get('clip') for s in p['scenes'])) and not secret('RUNWAYML_API_SECRET'): raise ValueError('Connect Runway or attach clips to every scene')
    if kind=='upload':
        if not p.get('output'): raise ValueError('Render and preview the video first')
        if p.get('youtube_id'): raise ValueError('Already uploaded. Clone for a separate video.')
        if not secret('YOUTUBE_REFRESH_TOKEN'): raise ValueError('Connect YouTube first')
        if type(settings()['audience']) is not bool or type(settings()['synthetic']) is not bool: raise ValueError('Choose audience and disclosure in Autopilot')
        with db() as c:
            if c.execute("SELECT 1 FROM jobs WHERE project=? AND state LIKE '%upload_session%'",(pid,)).fetchone():
                raise ValueError('An upload session already exists. Resume its job; do not create another.')
    s={'snapshot':p,'publish':{k:settings()[k] for k in ['privacy','audience','synthetic']}}
    if kind=='upload': s['output']=p['output']
    jid=enqueue(pid,kind,state=s)
    return jsonify(id=jid)

@app.post('/api/jobs/<jid>/<action>')
def job_action(jid,action):
    j=job(jid)
    if action=='cancel': update_job(jid,status='cancelled')
    elif action=='retry':
        if j['status']!='blocked': raise ValueError('Only blocked jobs can be retried')
        if busy(j['project']): raise ValueError('Another job is active for this project')
        update_job(jid,status='waiting',due=time.time(),attempts=0,error='')
    elif action=='approve':
        if j['status']!='review': raise ValueError('Job is not awaiting review')
        if not secret('YOUTUBE_REFRESH_TOKEN'): raise ValueError('Connect YouTube first')
        cfg=settings()
        if type(cfg['audience']) is not bool or type(cfg['synthetic']) is not bool: raise ValueError('Choose audience and disclosure')
        st=j['state']; st['automatic']=False; st['publish']={k:cfg[k] for k in ['privacy','audience','synthetic']}
        with db() as c: c.execute("UPDATE jobs SET kind='upload',status='waiting',state=?,due=? WHERE id=?",(json.dumps(st),time.time(),jid))
    else: raise ValueError('Unknown action')
    return jsonify(ok=True)

@app.post('/api/settings')
def save_settings():
    v=request.json; cfg=settings()
    if 'enabled' in v and type(v['enabled']) is not bool: raise ValueError('Enabled must be true or false')
    for k in DEFAULTS:
        if k in v and k!='next_run': cfg[k]=v[k]
    cfg['interval_hours']=finite(cfg['interval_hours'],1,168)
    cfg['daily_video_cap']=int(finite(cfg['daily_video_cap'],1,20))
    cfg['daily_estimate_cap']=finite(cfg['daily_estimate_cap'],0.01,10000)
    cfg['estimate_per_video']=finite(cfg['estimate_per_video'],0.01,1000)
    ZoneInfo(cfg['timezone'])
    if cfg['mode'] not in ['review','autopilot'] or cfg['privacy'] not in ['private','unlisted','public']: raise ValueError('Invalid publishing choice')
    if cfg['audience'] not in [True,False,None] or cfg['synthetic'] not in [True,False,None]: raise ValueError('Invalid audience/disclosure')
    if cfg['voice'] not in ['alloy','echo','fable','onyx','nova','shimmer','coral','sage']: raise ValueError('Invalid voice')
    if cfg['video_model']!='gen4.5': raise ValueError('This adapter supports gen4.5')
    if cfg['enabled']:
        missing=ready(upload=cfg['mode']=='autopilot')
        # Use new selections for readiness instead of old stored settings.
        missing=[x for x in missing if x not in ['audience choice','synthetic-content disclosure choice']]
        if cfg['mode']=='autopilot' and (type(cfg['audience']) is not bool or type(cfg['synthetic']) is not bool): missing.append('audience and disclosure choices')
        if missing: raise ValueError('Setup required: '+', '.join(missing))
        validate(project(cfg['template_id']))
        if not settings()['enabled']: cfg['next_run']=time.time()+60
    set_setting('preferences',cfg); return jsonify(ok=True)

@app.post('/api/secrets')
def secrets_api():
    for name in ['OPENAI_API_KEY','RUNWAYML_API_SECRET','GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET']:
        if request.json.get(name): save_secret(name,request.json[name].strip())
    return jsonify(ok=True)
@app.post('/api/assets')
def upload_asset():
    if request.form.get('rights')!='yes': raise ValueError('Confirm you can use this asset')
    f=request.files['file']; ext=Path(f.filename).suffix.lower()
    kinds={'.png':'image','.jpg':'image','.jpeg':'image','.webp':'image','.mp4':'video','.mov':'video','.webm':'video','.mp3':'audio','.wav':'audio','.m4a':'audio'}
    kind=kinds.get(ext)
    if not kind: raise ValueError('Unsupported file type')
    aid=secrets.token_hex(16)+('.png' if kind=='image' else ext); path=FILES/aid
    f.save(path)
    try:
        if kind=='image':
            with Image.open(path) as im:
                im.thumbnail((1280,1280)); im.convert('RGB').save(path,'PNG')
            if path.stat().st_size>5*1024*1024: raise ValueError('Image too large')
        else:
            if duration(path)>3600: raise ValueError('Use media shorter than one hour')
    except Exception:
        path.unlink(missing_ok=True); raise ValueError('Media could not be validated')
    with db() as c: c.execute('INSERT INTO assets VALUES (?,?,?)',(aid,f.filename[:200],kind))
    return jsonify(id=aid,kind=kind)
@app.get('/media/<name>')
def media(name):
    # Expose only registered assets or completed project output, never DB/vault/work files.
    with db() as c:
        allowed=c.execute('SELECT 1 FROM assets WHERE id=?',(name,)).fetchone()
        if not allowed: allowed=any(json.loads(r[0]).get('output')==name for r in c.execute('SELECT body FROM projects'))
    if not allowed: return jsonify(error='Not found'),404
    return send_from_directory(FILES,name,conditional=True)

@app.get('/oauth/start')
def oauth_start():
    if not secret('GOOGLE_CLIENT_ID') or not secret('GOOGLE_CLIENT_SECRET'): raise ValueError('Add Google OAuth credentials first')
    session['oauth_state']=secrets.token_urlsafe(32); session['oauth_time']=time.time()
    params={'client_id':secret('GOOGLE_CLIENT_ID'),'redirect_uri':os.getenv('PUBLIC_URL','http://localhost:8000').rstrip('/')+'/oauth/callback',
      'response_type':'code','scope':'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly',
      'access_type':'offline','prompt':'consent','state':session['oauth_state']}
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?'+urllib.parse.urlencode(params))
@app.get('/oauth/callback')
def oauth_callback():
    expected=session.pop('oauth_state','!'); started=session.pop('oauth_time',0)
    if not hmac.compare_digest(request.args.get('state',''),expected) or time.time()-started>600: raise ValueError('Expired or invalid OAuth state')
    if request.args.get('error'): return redirect('/?connection=cancelled')
    token=google_token(request.args['code'])
    r=check(requests.get('https://www.googleapis.com/youtube/v3/channels',params={'part':'snippet','mine':'true'},headers={'Authorization':'Bearer '+token},timeout=30)).json()
    if not r.get('items'): raise ValueError('No YouTube channel found for this account')
    ch=r['items'][0]; set_setting('channel',{'id':ch['id'],'title':ch['snippet']['title']})
    return redirect('/?connection=success')
@app.post('/api/youtube/disconnect')
def disconnect():
    token=secret('YOUTUBE_REFRESH_TOKEN')
    if token:
        check(requests.post('https://oauth2.googleapis.com/revoke',data={'token':token},timeout=30))
    save_secret('YOUTUBE_REFRESH_TOKEN',''); set_setting('channel',{})
    cfg=settings(); cfg['enabled']=False; set_setting('preferences',cfg)
    return jsonify(ok=True)

if __name__=='__main__': app.run(host='127.0.0.1',port=8000,debug=False)
