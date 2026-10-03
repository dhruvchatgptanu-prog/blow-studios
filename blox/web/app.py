"""Owner studio: HTML shell + JSON API.

Single-owner by design: one password (ADMIN_PASSWORD or ADMIN_PASSWORD_HASH).
Run behind a TLS reverse proxy for remote access (see docs/DEPLOYMENT.md).
"""
import json
import os
import secrets
import shutil
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .. import (budget, breaker, config, db as dbmod, jobs, logs, migrations, orchestrator, paid,
                prefs as prefsmod, repo, runtime, store, vault, videos)
from ..animation import blender as BL
from ..manifest import compile as C, director, schema as S, validate as V
from ..research import service as research, transcripts, youtube_api as Y
from ..timeutil import fmt, slot_times
from ..util import Blocked, now
from ..youtube import oauth
from . import security
from .assets import register as register_assets

log = logs.get('blox.web')


def create_app():
    runtime.init()
    root = config.APP_ROOT
    app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
    key = os.environ.get('SESSION_SECRET', '')
    if len(key) < 32 or key.startswith('replace-'):
        key = store.get('session_key') or secrets.token_hex(32)
        store.put('session_key', key)
    app.secret_key = key
    app.config.update(MAX_CONTENT_LENGTH=260 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=config.env_flag('COOKIE_SECURE'),
                      SESSION_COOKIE_NAME='blox_session', PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    proxies = int(os.environ.get('TRUSTED_PROXIES', '0') or 0)
    if proxies:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)
    app.before_request(security.protect)
    app.after_request(security.headers)

    @app.errorhandler(Exception)
    def error(e):
        if isinstance(e, HTTPException):
            return jsonify(error=e.description), e.code
        if isinstance(e, (ValueError, videos.InvalidTransition)):
            return jsonify(error=str(e)[:500]), 400
        if isinstance(e, Blocked):
            return jsonify(error=str(e)[:500], state=e.state), 409
        log.exception('request failed', extra={'path': request.path})
        return jsonify(error='Request failed. Check the server logs; no credentials are shown here.'), 500

    # ------------------------------------------------------------ auth
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if not security.password_configured():
            return render_template('login.html', error='Set ADMIN_PASSWORD (or ADMIN_PASSWORD_HASH) in .env, then restart.'), 503
        message = ''
        if request.method == 'POST':
            nonce = session.pop('login_nonce', '!')
            if not secrets.compare_digest(request.form.get('nonce', ''), nonce):
                message = 'Refresh and try again.'
            elif security.rate_limited(request.remote_addr or '?'):
                message = 'Too many attempts. Wait a few minutes.'
            elif security.check_password(request.form.get('password', '')):
                security.reset_attempts(request.remote_addr or '?')
                security.start_session()
                store.audit('login', {'ip': request.remote_addr}, actor='owner')
                return redirect('/')
            else:
                message = 'Incorrect password.'
        session['login_nonce'] = secrets.token_hex(24)
        return render_template('login.html', error=message, nonce=session['login_nonce'])

    @app.post('/api/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get('/')
    def home():
        return render_template('index.html', csrf=session['csrf'])

    @app.get('/healthz')
    def healthz():
        ok = True
        detail = {}
        try:
            dbmod.get().scalar('SELECT 1 AS x')
            detail['database'] = 'ok'
            detail['migrations_pending'] = len(migrations.pending())
        except Exception:
            ok = False
            detail['database'] = 'error'
        if ok:
            lw = jobs.live_workers(120)
            detail['worker_seen_seconds_ago'] = int(now() - lw['last_seen']) if lw['last_seen'] else None
            detail['worker_roles_missing'] = lw['missing']
        return jsonify(ok=ok, **detail), (200 if ok else 503)

    # ------------------------------------------------------------ dashboard
    def readiness():
        d = dbmod.get()
        st = vault.status()
        yt = oauth.status()
        p = prefsmod.get()
        items = {
            'openai': {'ok': st['OPENAI_API_KEY'], 'label': 'OpenAI (scripts, voices, QA review)'},
            'youtube_data': {'ok': st['YOUTUBE_API_KEY'], 'label': 'YouTube Data API key (research)'},
            'google_oauth': {'ok': yt['oauth_client'], 'label': 'Google OAuth client'},
            'youtube_channel': {'ok': yt['connected'] and bool(yt['channel'].get('confirmed')),
                                'label': 'YouTube channel connected and confirmed', 'detail': yt['channel'].get('title')},
            'blender': {'ok': BL.available(), 'label': 'Blender renderer'},
            'ffmpeg': {'ok': bool(shutil.which(config.FFMPEG_BIN)), 'label': 'FFmpeg'},
            'runway': {'ok': st['RUNWAYML_API_SECRET'], 'label': 'Runway (optional generative shots)',
                       'optional': True},
            'elevenlabs': {'ok': st['ELEVENLABS_API_KEY'], 'label': 'ElevenLabs (optional voices)', 'optional': True},
            'audience': {'ok': p['publishing']['made_for_kids'] is not None and
                         p['publishing']['synthetic_disclosure'] is not None,
                         'label': 'Audience and synthetic-content disclosure chosen'},
        }
        lw = jobs.live_workers(60)
        if not lw['workers']:
            hb = d.scalar('SELECT MAX(heartbeat_at) AS t FROM workers')
            wdetail = f'last seen {int(now() - hb)}s ago' if hb else 'never seen'
        else:
            wdetail = 'no live worker for: ' + ', '.join(lw['missing']) if lw['missing'] else 'all roles covered'
        items['worker'] = {'ok': bool(lw['workers']) and not lw['missing'], 'label': 'Background workers running',
                           'detail': wdetail}
        return items

    @app.get('/api/state')
    def state():
        d = dbmod.get()
        p = prefsmod.get(d)
        tz = p['schedule']['timezone']
        nxt = d.one("SELECT * FROM slots WHERE slot_at>? AND status NOT IN ('skipped') ORDER BY slot_at LIMIT 1", (now(),))
        if not nxt:
            items = [x for x in slot_times(now(), now() + 2 * 86400, p['schedule']) if x['at']]
            nxt = {'slot_at': items[0]['at'], 'status': 'not_created'} if items else None
        if nxt:
            nxt['local'] = fmt(nxt['slot_at'], tz)
        running = d.query("SELECT id, kind, video_id, status, attempts, last_error, updated_at FROM tasks WHERE status IN "
                          "('running','queued') ORDER BY updated_at DESC LIMIT 25")
        return jsonify(
            autopilot=p['autopilot'], mode=p['autopilot']['mode'], timezone=tz, next_slot=nxt,
            buffer={'approved_unscheduled': int(d.scalar("SELECT COUNT(*) AS n FROM videos WHERE status='approved' AND "
                                                         'slot_id IS NULL') or 0),
                    'target': p['schedule']['buffer_target'], 'by_status': orchestrator.describe_buffer(d)},
            jobs=running, readiness=readiness(), budget=budget.summary(p, d), blockers=orchestrator.publish_blockers(d, p),
            breakers=breaker.all_states(d), alerts=_alerts(d, p),
            quota={b: {'used_today': Y.used(b, d), 'limit': Y.limits(p)[b]} for b in ('shared', 'search', 'insert')},
            workers=d.query('SELECT id, roles, heartbeat_at, status, current_task FROM workers WHERE heartbeat_at>? '
                            'ORDER BY heartbeat_at DESC', (now() - 300,)),
            server_time=now())

    def _alerts(d, p):
        out = []
        for v in d.query("SELECT id, title, status, status_reason FROM videos WHERE status IN "
                         "('needs_review','needs_credentials','blocked') ORDER BY updated_at DESC LIMIT 20"):
            out.append({'kind': v['status'], 'video': v['id'], 'title': v['title'], 'message': v['status_reason']})
        if p['autopilot']['pause_reason'] and p['autopilot']['paused']:
            out.append({'kind': 'paused', 'message': p['autopilot']['pause_reason']})
        return out

    # ------------------------------------------------------------ videos
    @app.get('/api/videos')
    def list_videos():
        status = request.args.get('status')
        rows = videos.listing(status)
        for r in rows:
            r['metadata'] = {k: r['metadata'].get(k) for k in ('cost_estimate', 'publish_metadata', 'owner_approved',
                                                                'repair_rounds')}
        return jsonify(videos=rows)

    @app.get('/api/videos/<vid>')
    def video_detail(vid):
        d = dbmod.get()
        v = videos.get(vid, d)
        mf = repo.manifest(v['manifest_id'], d) if v['manifest_id'] else None
        out = {'video': v, 'events': videos.events(vid, d), 'manifests': repo.manifests(vid, d),
               'manifest': mf, 'shots': repo.shots(v['manifest_id'], d) if mf else [],
               'lines': repo.lines(v['manifest_id'], d) if mf else [],
               'render': repo.render(v['render_id'], d) if v['render_id'] else None,
               'qa': repo.qa(v['qa_report_id'], d) if v['qa_report_id'] else None,
               'qa_history': repo.qa_history(vid, d), 'repairs': repo.repairs(vid, d),
               'spend': budget.video_spend(vid, d), 'pending_paid_calls': paid.pending_for_video(vid, d),
               'tasks': d.query('SELECT id, kind, status, attempts, last_error, updated_at FROM tasks WHERE video_id=? '
                                'ORDER BY created_at DESC LIMIT 40', (vid,)),
               'upload': d.one('SELECT id, status, youtube_video_id, bytes_total, bytes_confirmed, requested, observed, '
                               'error, updated_at FROM uploads WHERE video_id=?', (vid,)),
               'slot': d.one('SELECT * FROM slots WHERE id=?', (v['slot_id'],)) if v['slot_id'] else None,
               'concept': d.one('SELECT * FROM concepts WHERE id=?', (v['concept_id'],)) if v['concept_id'] else None}
        return jsonify(out)

    @app.post('/api/videos')
    def create_video():
        body = request.get_json(force=True) or {}
        kind = body.get('kind', 'demo')
        d = dbmod.get()
        p = prefsmod.get(d)
        if kind == 'autopilot_style':
            vid = videos.create('Untitled (researching)', 'manual', d=d)
            jobs.enqueue('video.develop', {}, video_id=vid, idempotency_key=f'develop:{vid}', d=d)
            return jsonify(id=vid)
        if kind == 'demo':
            from ..demo import plan
            plan_body = plan()
        elif kind == 'plan':
            plan_body = body.get('plan')
        else:
            raise ValueError('Unknown kind')
        return jsonify(id=_create_from_plan(plan_body, p, d, source='demo' if kind == 'demo' else 'manual'))

    def _create_from_plan(plan_body, p, d, source):
        if not isinstance(plan_body, dict) or len(json.dumps(plan_body)) > C.MAX_PLAN_BYTES:
            raise ValueError('Plan must be a JSON object under 400 KB')
        pr = p['production']
        m = C.compile_plan(plan_body, fps=pr['fps'], width=pr['width'], height=pr['height'])
        rep = V.validate(m, p)
        vid = videos.create(m['title'] or 'Untitled', 'manual', status='concept_selected', d=d,
                            metadata={'publish_metadata': m.get('metadata') or {'title': m['title']}}, actor='owner')
        chars = repo.characters(active_only=False, d=d)
        names = {c['id']: chars.get(c['character_id'], {}).get('name', c['id']) for c in m['cast']}
        mid = repo.save_manifest(vid, m, source, director.script(m, names), rep, d)
        videos.merge_metadata(vid, {'plan': plan_body}, d)
        if rep['ok']:
            videos.transition(vid, 'scripted', 'Manifest validated', actor='owner', manifest_id=mid, d=d)
        else:
            videos.hold(vid, 'needs_review', 'Manifest has validation errors; fix them in the editor', 'owner', d=d)
        return vid

    @app.put('/api/videos/<vid>/plan')
    def save_plan(vid):
        d = dbmod.get()
        p = prefsmod.get(d)
        v = videos.get(vid, d)
        if v['status'] not in videos.EDITABLE:
            raise ValueError('This video is already uploaded; duplicate it to make changes')
        if jobs.active_for_video(vid, d):
            raise ValueError('Wait for running work to finish or cancel it before editing')
        plan_body = (request.get_json(force=True) or {}).get('plan')
        if not isinstance(plan_body, dict) or len(json.dumps(plan_body)) > C.MAX_PLAN_BYTES:
            raise ValueError('Plan must be a JSON object under 400 KB')
        pr = p['production']
        m = C.compile_plan(plan_body, fps=pr['fps'], width=pr['width'], height=pr['height'])
        rep = V.validate(m, p)
        chars = repo.characters(active_only=False, d=d)
        names = {c['id']: chars.get(c['character_id'], {}).get('name', c['id']) for c in m['cast']}
        mid = repo.save_manifest(vid, m, 'owner_edit', director.script(m, names), rep, d)
        videos.merge_metadata(vid, {'plan': plan_body}, d)
        target = 'scripted' if rep['ok'] else 'needs_review'
        cur = videos.get(vid, d)['status']
        if target == 'scripted':
            if cur in videos.HOLDS:
                videos.merge_metadata(vid, {'resume_status': 'scripted'}, d)
                videos.resume(vid, d=d)
            elif cur != 'scripted':
                videos.transition(vid, 'scripted', 'Owner edited the plan', actor='owner', d=d)
        else:
            videos.hold(vid, 'needs_review', 'Edited plan has validation errors', 'owner', d=d)
        return jsonify(manifest_id=mid, validation=rep)

    @app.post('/api/validate-plan')
    def validate_plan():
        p = prefsmod.get()
        plan_body = (request.get_json(force=True) or {}).get('plan')
        pr = p['production']
        m = C.compile_plan(plan_body, fps=pr['fps'], width=pr['width'], height=pr['height'])
        rep = V.validate(m, p)
        return jsonify(validation=rep, script=director.script(m), beats=len(m['beats']))

    @app.post('/api/videos/<vid>/<action>')
    def video_action(vid, action):
        d = dbmod.get()
        body = request.get_json(silent=True) or {}
        v = videos.get(vid, d)
        p = prefsmod.get(d)
        if action == 'produce':
            if v['status'] not in ('scripted', 'concept_selected', 'discovered', 'researched'):
                raise ValueError(f'Video is {v["status"]}; produce starts from a scripted manifest')
            jobs.enqueue('video.develop', {}, video_id=vid, idempotency_key=f'develop:{vid}:{int(now())}', d=d)
        elif action == 'approve':
            if v['status'] != 'approved':
                raise ValueError('Only QA-approved videos can be approved for publishing')
            if p['publishing']['made_for_kids'] is None or p['publishing']['synthetic_disclosure'] is None:
                raise ValueError('Choose the audience and disclosure settings first')
            videos.merge_metadata(vid, {'owner_approved': True, 'owner_approved_at': now()}, d)
        elif action == 'unapprove':
            videos.merge_metadata(vid, {'owner_approved': False}, d)
        elif action == 'resume':
            videos.resume(vid, d=d)
            st = videos.get(vid, d)['status']
            if st in ('discovered', 'researched', 'concept_selected', 'scripted'):
                jobs.enqueue('video.develop', {}, video_id=vid, idempotency_key=f'develop:{vid}:{int(now())}', d=d)
            elif st == 'storyboarded':
                jobs.enqueue('video.voice', {'manifest_id': v['manifest_id']}, video_id=vid,
                             idempotency_key=f'voice:{v["manifest_id"]}:resume{int(now())}', d=d)
            elif st in ('generating', 'rendering', 'repairing'):
                jobs.enqueue('video.voice', {'manifest_id': v['manifest_id']}, video_id=vid,
                             idempotency_key=f'voice:{v["manifest_id"]}:resume{int(now())}', d=d)
            elif st == 'checking' and v['render_id']:
                jobs.enqueue('video.qa', {'manifest_id': v['manifest_id'], 'render_id': v['render_id']}, video_id=vid,
                             idempotency_key=f'qa:{v["render_id"]}:resume{int(now())}', d=d)
            elif st in ('uploaded_private', 'processing_verified', 'scheduled'):
                up = d.one('SELECT id FROM uploads WHERE video_id=?', (vid,))
                if up:
                    jobs.enqueue('video.verify', {'upload_id': up['id']}, video_id=vid,
                                 idempotency_key=f'verify:{up["id"]}:resume{int(now())}', d=d)
        elif action == 'cancel':
            jobs.cancel_for_video(vid, d)
            if v['slot_id']:
                d.execute("UPDATE slots SET status='skipped', video_id=NULL, reason='Owner cancelled the video', "
                          'updated_at=? WHERE id=? AND status=\'assigned\'', (now(), v['slot_id']))
            if v['status'] not in videos.TERMINAL:
                videos.transition(vid, 'cancelled', 'Cancelled by owner (provider work already submitted may still '
                                                    'finish and be charged)', actor='owner', d=d)
        elif action == 'regenerate_shot':
            key = str(body.get('shot', ''))
            sh = next((x for x in repo.shots(v['manifest_id'], d) if x['shot_key'] == key), None)
            if not sh:
                raise ValueError('Unknown shot')
            if v['status'] in videos.UPLOADED:
                raise ValueError('Already uploaded')
            repo.upsert_shot(vid, v['manifest_id'], key, sh['renderer'], d, status='pending',
                             repair_attempts=sh['repair_attempts'] + 1,
                             detail=dict(sh['detail'] or {}, runway=None, owner_request=True))
            jobs.enqueue('shot.render', {'manifest_id': v['manifest_id'], 'shot': key}, video_id=vid,
                         idempotency_key=f'shot:{v["manifest_id"]}:{key}:owner{int(now())}', d=d)
            if v['status'] in ('approved', 'checking', 'blocked', 'needs_review'):
                try:
                    videos.transition(vid, 'generating', f'Owner regenerating shot {key}', actor='owner', d=d)
                except videos.InvalidTransition:
                    pass
        elif action == 'revoice_line':
            key = str(body.get('line', ''))
            row = next((x for x in repo.lines(v['manifest_id'], d) if x['line_key'] == key), None)
            if not row:
                raise ValueError('Unknown line')
            ln = next(x for x in repo.manifest(v['manifest_id'], d)['body']['lines'] if x['id'] == key)
            repo.upsert_line(vid, v['manifest_id'], ln, d, status='pending', repair_attempts=row['repair_attempts'] + 1)
            jobs.enqueue('video.voice', {'manifest_id': v['manifest_id'], 'rerender': True}, video_id=vid,
                         idempotency_key=f'voice:{v["manifest_id"]}:owner{int(now())}', d=d)
        elif action == 'resolve_paid':
            paid.resolve(str(body.get('key', '')), str(body.get('outcome', '')), d)
        elif action == 'set_cover':
            m = repo.manifest(v['manifest_id'], d)['body']
            f = int(body.get('frame', 0))
            if not 0 <= f < m['duration_frames']:
                raise ValueError('Frame outside the video')
            m['cover_frame'] = f
            d.execute('UPDATE manifests SET body=? WHERE id=?', (json.dumps(m), v['manifest_id']))
            if v['render_id']:
                jobs.enqueue('video.assemble', {'manifest_id': v['manifest_id']}, video_id=vid,
                             idempotency_key=f'assemble:{v["manifest_id"]}:cover{f}:{int(now())}', d=d)
        elif action == 'duplicate':
            plan_body = v['metadata'].get('plan')
            if not plan_body:
                raise ValueError('Only videos with an editable plan can be duplicated')
            new = _create_from_plan(plan_body, p, d, 'duplicate')
            store.audit('video_duplicate', {'video': vid, 'new': new}, actor='owner', d=d)
            return jsonify(id=new)
        else:
            raise ValueError('Unknown action')
        store.audit('video_' + action, {'video': vid}, actor='owner', d=d)
        return jsonify(ok=True)

    # ------------------------------------------------------------ research
    @app.get('/api/research')
    def research_view():
        d = dbmod.get()
        return jsonify(candidates=research.top(60, d), coverage=research.coverage_summary(d),
                       watchlist=d.query('SELECT channel_id, title, subscriber_count, baseline, note FROM ref_channels '
                                         'WHERE watch=1'),
                       references=d.query('SELECT video_id, title, transcript_status, media_status FROM ref_videos '
                                          'WHERE user_reference=1'),
                       cost=research.plan_cost(prefsmod.get(d)),
                       disclaimer='Top candidates in the monitored sample, not the most popular videos across YouTube.')

    @app.post('/api/research/<action>')
    def research_action(action):
        d = dbmod.get()
        body = request.get_json(silent=True) or {}
        if action == 'run':
            if not vault.configured('YOUTUBE_API_KEY'):
                raise ValueError('Add a YouTube Data API key in Connections first')
            tid = jobs.enqueue('research.discover', {}, idempotency_key=f'discover:manual:{int(now())}', d=d)
            return jsonify(task=tid)
        if action == 'snapshot':
            tid = jobs.enqueue('research.snapshot', {}, idempotency_key=f'snapshot:manual:{int(now())}', d=d)
            return jsonify(task=tid)
        if action == 'reference':
            return jsonify(research.add_reference(str(body.get('url', ''))[:300], d))
        if action == 'unwatch':
            d.execute('UPDATE ref_channels SET watch=0 WHERE channel_id=?', (str(body.get('channel_id', '')),))
            return jsonify(ok=True)
        if action == 'transcript':
            vid = str(body.get('video_id', ''))
            if not Y.VIDEO_ID.match(vid):
                raise ValueError('Invalid video id')
            tid = transcripts.from_upload('yt:' + vid, str(body.get('text', ''))[:200000], body.get('rights') is True)
            return jsonify(transcript=tid)
        if action == 'analyze':
            payload = {}
            if body.get('asset'):
                a = d.one('SELECT id, kind FROM assets WHERE id=?', (str(body['asset']),))
                if not a or a['kind'] != 'video':
                    raise ValueError('Choose an uploaded video asset')
                payload['asset'] = a['id']
            else:
                vid = str(body.get('video_id', ''))
                if not Y.VIDEO_ID.match(vid):
                    raise ValueError('Invalid video id')
                payload['video_id'] = vid
            tid = jobs.enqueue('research.reference', payload, idempotency_key=f'ref:{json.dumps(payload)}:{int(now())}', d=d)
            return jsonify(task=tid)
        raise ValueError('Unknown action')

    @app.get('/api/research/video/<vid>')
    def research_video(vid):
        d = dbmod.get()
        v = d.one('SELECT * FROM ref_videos WHERE video_id=?', (vid,))
        if not v:
            abort(404)
        for k in ('shorts', 'rank', 'tags', 'sources', 'topics'):
            v[k] = json.loads(v[k] or 'null')
        v['snapshots'] = d.query('SELECT retrieved_at, views, likes, comments FROM ref_snapshots WHERE video_id=? '
                                 'ORDER BY retrieved_at', (vid,))
        v['transcript'] = transcripts.latest('yt:' + vid, d)
        an = d.one('SELECT * FROM ref_analyses WHERE subject=? ORDER BY created_at DESC', ('yt:' + vid,))
        if an:
            an['findings'] = json.loads(an['findings'])
        v['analysis'] = an
        return jsonify(v)

    # ------------------------------------------------------------ calendar
    @app.get('/api/calendar')
    def calendar():
        d = dbmod.get()
        p = prefsmod.get(d)
        tz = p['schedule']['timezone']
        start = now() - float(request.args.get('past_hours', 48)) * 3600
        end = now() + float(request.args.get('future_hours', 48)) * 3600
        rows = d.query('SELECT s.*, v.title, v.status AS video_status, v.youtube_video_id FROM slots s LEFT JOIN videos v '
                       'ON v.id=s.video_id WHERE slot_at>=? AND slot_at<=? ORDER BY slot_at', (start, end))
        for r in rows:
            r['local'] = fmt(r['slot_at'], tz)
            r['detail'] = json.loads(r['detail'] or '{}')
        gaps = [x for x in slot_times(start, end, p['schedule']) if x['status'] == 'dst_gap']
        return jsonify(slots=rows, dst_gaps=gaps, timezone=tz, policy=p['schedule']['policy'],
                       dst_rules=('Slots use local wall-clock times. On the spring-forward night a local time that does '
                                  'not exist is not scheduled; on the fall-back night a repeated time is used once '
                                  '(first occurrence). Times are stored in UTC.'))

    # ------------------------------------------------------------ settings & autopilot
    @app.get('/api/settings')
    def get_settings():
        return jsonify(prefs=prefsmod.get(), defaults=prefsmod.DEFAULTS,
                       vocab={'renderers': sorted(prefsmod.RENDERERS), 'tts': sorted(prefsmod.TTS_PROVIDERS)})

    @app.post('/api/settings')
    def save_settings():
        patch = request.get_json(force=True) or {}
        patch.pop('autopilot', None)  # autopilot state changes go through /api/autopilot
        p = prefsmod.update(patch)
        store.audit('settings_changed', {'sections': sorted(patch)}, actor='owner')
        return jsonify(prefs=p)

    @app.post('/api/autopilot/<action>')
    def autopilot(action):
        d = dbmod.get()
        p = prefsmod.get(d)
        body = request.get_json(silent=True) or {}
        a = p['autopilot']
        if action == 'enable':
            problems = [k for k, x in readiness().items() if not x['ok'] and not x.get('optional')
                        and k not in ('runway', 'elevenlabs')]
            if body.get('confirm') != 'ENABLE':
                raise ValueError('Type ENABLE to confirm automatic production and publishing')
            if problems:
                raise ValueError('Finish setup first: ' + ', '.join(readiness()[k]['label'] for k in problems))
            mode = body.get('mode', a['mode'])
            if mode not in ('review', 'autopilot'):
                raise ValueError('Invalid mode')
            a.update(enabled=True, paused=False, mode=mode, activated_at=now(), pause_reason='')
        elif action == 'disable':
            a.update(enabled=False)
        elif action == 'pause':
            a.update(paused=True, pause_reason=str(body.get('reason', 'Paused by owner'))[:200])
        elif action == 'resume':
            if a['emergency_stop']:
                raise ValueError('Clear the emergency stop first')
            a.update(paused=False, pause_reason='')
        elif action == 'estop':
            orchestrator.emergency_stop(d=d)
            return jsonify(ok=True)
        elif action == 'clear_estop':
            orchestrator.clear_emergency_stop(d=d)
            return jsonify(ok=True)
        elif action == 'mode':
            if body.get('mode') not in ('review', 'autopilot'):
                raise ValueError('Invalid mode')
            a['mode'] = body['mode']
        else:
            raise ValueError('Unknown action')
        prefsmod.put(p, d)
        store.audit('autopilot_' + action, {'mode': a['mode']}, actor='owner', d=d)
        return jsonify(autopilot=a)

    # ------------------------------------------------------------ connections
    @app.get('/api/connections')
    def connections():
        return jsonify(secrets=vault.status(), labels=vault.KNOWN, youtube=oauth.status(), readiness=readiness(),
                       breakers=breaker.all_states(), redirect_uri=oauth.redirect_uri(),
                       blender=shutil.which(config.BLENDER_BIN), ffmpeg=shutil.which(config.FFMPEG_BIN))

    @app.post('/api/secrets')
    def save_secrets():
        body = request.get_json(force=True) or {}
        saved = []
        for name in vault.KNOWN:
            val = body.get(name)
            if isinstance(val, str) and val.strip():
                if len(val) > 4000:
                    raise ValueError(f'{name} is too long')
                if name == 'TRANSCRIPT_PROVIDER_URL':
                    from ..netsafe import UnsafeURL, check_url
                    try:
                        check_url(val.strip())
                    except UnsafeURL as e:
                        raise ValueError(f'Transcript provider URL rejected: {e}')
                vault.put(name, val.strip())
                saved.append(name)
            elif body.get('clear_' + name) is True:
                vault.put(name, '')
                saved.append(name)
        store.audit('secrets_updated', {'names': saved}, actor='owner')
        return jsonify(saved=saved)

    @app.get('/oauth/start')
    def oauth_start():
        return redirect(oauth.start(session, analytics=request.args.get('analytics', '1') == '1'))

    @app.get('/oauth/callback')
    def oauth_callback():
        try:
            oauth.finish(session, request.args)
        except (ValueError, Blocked) as e:
            return redirect('/?connection_error=' + __import__('urllib.parse').parse.quote(str(e)[:200]) + '#connections')
        return redirect('/?connection=success#connections')

    @app.post('/api/youtube/<action>')
    def youtube_action(action):
        body = request.get_json(silent=True) or {}
        if action == 'confirm':
            return jsonify(channel=oauth.confirm_channel(str(body.get('channel_id', ''))))
        if action == 'disconnect':
            oauth.disconnect()
            p = prefsmod.get()
            p['autopilot']['paused'] = True
            p['autopilot']['pause_reason'] = 'YouTube disconnected'
            prefsmod.put(p)
            return jsonify(ok=True)
        if action == 'reset_restriction':
            store.put('publishing_restriction', None)
            return jsonify(ok=True)
        raise ValueError('Unknown action')

    # ------------------------------------------------------------ characters
    @app.get('/api/characters')
    def get_characters():
        return jsonify(characters=repo.characters(active_only=False),
                       vocab={'palette_slots': ['skin', 'top', 'top_trim', 'pants', 'shoes', 'hair', 'eyes', 'brows',
                                                'mouth', 'badge', 'hat'],
                              'tops': ['hoodie', 'tee'], 'hair': ['messy_block', 'short_block', None],
                              'hats': ['cap', None], 'badges': ['star', None]})

    @app.put('/api/characters/<cid>')
    def put_character(cid):
        import re as _re
        if not _re.fullmatch(r'ch_[a-z0-9_]{2,30}', cid):
            raise ValueError('Character ids look like ch_name')
        body = request.get_json(force=True) or {}
        name = str(body.get('name', ''))[:40].strip()
        if not name:
            raise ValueError('Name is required')
        bible = body.get('bible') or {}
        voice = body.get('voice') or {}
        pal = bible.get('palette') or {}
        for k, val in pal.items():
            if not _re.fullmatch(r'#[0-9A-Fa-f]{6}', str(val)):
                raise ValueError(f'Colour {k} must be #RRGGBB')
        scale = float(bible.get('scale', 1.0))
        if not 0.6 <= scale <= 1.2:
            raise ValueError('Scale must be between 0.6 and 1.2')
        clean_bible = {'summary': str(bible.get('summary', ''))[:400], 'personality': str(bible.get('personality', ''))[:400],
                       'scale': scale, 'palette': pal,
                       'costume': {k: (bible.get('costume') or {}).get(k) for k in ('top', 'hair', 'hat', 'badge')},
                       'visual_rules': [str(x)[:160] for x in (bible.get('visual_rules') or [])][:12]}
        clean_voice = {'openai_voice': str(voice.get('openai_voice', 'alloy'))[:20],
                       'openai_instructions': str(voice.get('openai_instructions', ''))[:600],
                       'elevenlabs_voice_id': str(voice.get('elevenlabs_voice_id', ''))[:64],
                       'local_test_voice': str(voice.get('local_test_voice', 'kal16'))[:10],
                       'rights_note': str(voice.get('rights_note', ''))[:300]}
        repo.save_character(cid, name, clean_bible, clean_voice, bool(body.get('active', True)))
        store.audit('character_saved', {'id': cid}, actor='owner')
        return jsonify(ok=True)

    @app.post('/api/characters/<cid>/preview')
    def character_preview(cid):
        """Render a still of the character with the real renderer (no paid calls)."""
        chars = repo.characters(active_only=False)
        if cid not in chars:
            abort(404)
        from ..demo import plan as demo_plan
        pl = demo_plan()
        pl['cast'] = [{'id': 'hero', 'character_id': cid}]
        pl['performance'] = {'hero': [{'t': 0, 'position': [0.0, 0.0], 'facing': 0, 'expression': 'happy',
                                       'eye_target': {'kind': 'camera'}}]}
        pl['actions'], pl['lines'] = [], []
        pl['setting']['props'] = [{'id': 'p', 'type': 'platform', 'position': [0, 0, 0], 'size': [3, 3], 'color': '#4CAF50'}]
        pl['shots'] = [{'id': 's1', 'start_s': 0, 'end_s': 30, 'camera': {'subject': 'hero', 'framing_start': 'medium_wide',
                                                                         'framing_end': 'medium_wide', 'move': 'static'}}]
        m = C.compile_plan(pl, fps=30, width=540, height=960)
        from ..animation import solver as SV
        bibles = {'hero': chars[cid]['bible']}
        solved = SV.solve(m, bibles)
        out = config.WORK_DIR / 'character_previews' / cid
        p = prefsmod.get()
        res = BL.render_range(m, bibles, solved, 10, 11, str(out), dict(p, production=dict(p['production'], width=540,
                                                                                           height=960)), quality='final')
        rel = repo.rel(res['preview'])
        store.put('character_preview:' + cid, rel)
        return jsonify(preview='/media/' + rel)

    # ------------------------------------------------------------ queue, budget, analytics, audit
    @app.get('/api/tasks')
    def get_tasks():
        return jsonify(tasks=jobs.recent(150))

    @app.post('/api/tasks/<tid>/<action>')
    def task_action(tid, action):
        if action == 'cancel':
            jobs.cancel(tid)
        elif action == 'requeue':
            if not jobs.requeue(tid):
                raise ValueError('Only failed, dead or cancelled tasks can be requeued')
        else:
            raise ValueError('Unknown action')
        store.audit('task_' + action, {'task': tid}, actor='owner')
        return jsonify(ok=True)

    @app.get('/api/budget')
    def get_budget():
        d = dbmod.get()
        p = prefsmod.get(d)
        return jsonify(summary=budget.summary(p, d), prices=p['budget']['prices'],
                       ledger=d.query('SELECT category, provider, status, estimate, actual, actual_kind, video_id, note, '
                                      'created_at FROM budget_ledger ORDER BY created_at DESC LIMIT 200'),
                       paid_calls=d.query('SELECT provider, operation, status, estimate, actual, measured, video_id, error, '
                                          'created_at FROM paid_calls ORDER BY created_at DESC LIMIT 100'),
                       estimate_method=COST_METHOD)

    @app.get('/api/analytics')
    def get_analytics():
        from .. import learning
        d = dbmod.get()
        return jsonify(status=store.get('analytics_status', {}, d), findings=learning.latest(d),
                       snapshots=d.query('SELECT youtube_video_id, fetched_at, start_date, end_date, metrics, missing '
                                         'FROM analytics_snapshots ORDER BY fetched_at DESC LIMIT 100'))

    @app.get('/api/audit')
    def get_audit():
        return jsonify(entries=store.recent_audit(200))

    @app.get('/api/vocabulary')
    def vocabulary():
        return jsonify(schema={k: getattr(S, k) for k in (
            'PURPOSES', 'SETTING_PRESETS', 'TIME_OF_DAY', 'LIGHTING', 'PROP_TYPES', 'MOUTH_SHAPES', 'ARM_POSES',
            'POSTURES', 'STANCES', 'WEIGHTS', 'ACTIONS', 'FRAMINGS', 'CAMERA_ANGLES', 'CAMERA_SIDES', 'CAMERA_MOVES',
            'EASES', 'TRANSITIONS', 'EMOTIONS', 'PACES', 'VOLUMES', 'SFX_CUES', 'MUSIC_CUES')} |
            {'EXPRESSIONS': list(S.EXPRESSIONS), 'COORDINATE_SYSTEM': S.COORDINATE_SYSTEM})

    register_assets(app)

    # ------------------------------------------------------------ media
    @app.get('/media/<path:name>')
    def media_file(name):
        d = dbmod.get()
        name = name.replace('\\', '/')
        if '..' in name.split('/') or name.startswith('/'):
            abort(404)
        allowed = False
        if '/' not in name:
            allowed = bool(d.one('SELECT 1 AS x FROM assets WHERE id=?', (name,)))
            path = config.MEDIA_DIR / name
        else:
            path = config.DATA_DIR / name
            allowed = bool(
                d.one('SELECT 1 AS x FROM renders WHERE file=? OR cover_file=? OR thumbnail_file=?', (name, name, name))
                or d.one('SELECT 1 AS x FROM shots WHERE file=? OR preview_file=?', (name, name))
                or d.one('SELECT 1 AS x FROM audio_lines WHERE file=?', (name,))
                or name.startswith('work/character_previews/'))
        try:
            real = Path(os.path.realpath(path))
            base = Path(os.path.realpath(config.DATA_DIR))
            if base not in real.parents:
                allowed = False
        except OSError:
            allowed = False
        if not allowed or not path.exists():
            abort(404)
        return send_file(path, conditional=True)

    return app


COST_METHOD = (
    'Per video = script LLM calls (tokens × price) + voices (spoken minutes × TTS price, or characters × ElevenLabs '
    'price) + alignment ASR + QA (ASR of the final mix + vision images) + generative seconds × Runway price (0 for '
    'Blender shots) + a repair allowance. Multiply by videos per day (12 at a two-hour cadence) for the daily figure. '
    'All prices are editable in Settings → Budget and should be checked against each provider\'s current pricing.')
