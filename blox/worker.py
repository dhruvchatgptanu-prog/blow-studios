"""Long-running worker: claims tasks for its roles and runs the orchestrator tick.

Run one process per role group (see compose.yaml) or a single process with
``--roles all``. Workers are safe to run concurrently: claiming is atomic and
every write is lease-checked.
"""
import argparse
import logging
import os
import signal
import socket
import threading
import time

from . import breaker, db as dbmod, jobs, orchestrator, prefs as prefsmod, runtime, videos
from .http import ProviderError
from .media import MediaError
from .tasks import HANDLERS, Ctx, LeaseLost, ensure_loaded
from .util import Blocked, Retry, Waiting, new_id, now

log = logging.getLogger('blox.worker')
_stop = threading.Event()


def _hold_video(task, state, message):
    vid = task.get('video_id')
    if not vid:
        return
    try:
        if state == 'failed':
            videos.transition(vid, 'failed', message[:900])
        else:
            videos.hold(vid, state if state in videos.HOLDS else 'blocked', message[:900])
    except (videos.InvalidTransition, ValueError):
        pass


def run_task(task, worker_id, lease_s=300):
    ctx = Ctx(task, worker_id, lease_s)
    fn = HANDLERS.get(task['kind'])
    tid = task['id']
    if not fn:
        jobs.fail(tid, worker_id, 'No handler for ' + task['kind'])
        return 'failed'
    t0 = time.time()
    try:
        result = fn(ctx)
        jobs.complete(tid, worker_id, result)
        log.info('task done', extra={'task': tid, 'kind': task['kind'], 'video': task.get('video_id'),
                                     'seconds': round(time.time() - t0, 1)})
        return 'succeeded'
    except Waiting as w:
        jobs.wait(tid, worker_id, w.delay or 15, str(w))
        if task.get('video_id'):
            try:
                videos.update(task['video_id'], status_reason=str(w)[:300])
            except ValueError:
                pass
        return 'waiting'
    except breaker.Open as o:
        jobs.wait(tid, worker_id, max(30, (o.retry_at or now() + 60) - now()), str(o))
        return 'waiting'
    except Retry as r:
        state = jobs.retry(tid, worker_id, str(r), r.delay)
        if state == 'dead':
            _hold_video(task, 'needs_review', f'{task["kind"]} kept failing: {r}')
        return state
    except Blocked as b:
        jobs.fail(tid, worker_id, str(b))
        _hold_video(task, b.state, str(b))
        log.warning('task blocked', extra={'task': tid, 'kind': task['kind'], 'reason': str(b)[:300]})
        return 'blocked'
    except LeaseLost:
        log.warning('lease lost', extra={'task': tid})
        return 'lease_lost'
    except ProviderError as e:
        if e.auth:
            jobs.fail(tid, worker_id, str(e))
            _hold_video(task, 'needs_credentials', str(e))
            return 'blocked'
        state = jobs.retry(tid, worker_id, str(e), e.retry_after)
        if state == 'dead':
            _hold_video(task, 'needs_review', f'{task["kind"]}: {e}')
        return state
    except (MediaError, OSError) as e:
        msg = str(e)
        if 'stopped: emergency stop' in msg or 'stopped: paused' in msg:
            jobs.wait(tid, worker_id, 120, msg)
            return 'waiting'
        state = jobs.retry(tid, worker_id, msg[-800:])
        if state == 'dead':
            _hold_video(task, 'blocked', f'{task["kind"]} failed repeatedly: {msg[-400:]}')
        return state
    except Exception as e:
        log.exception('task crashed', extra={'task': tid, 'kind': task['kind']})
        state = jobs.retry(tid, worker_id, f'{type(e).__name__}: {str(e)[:600]}')
        if state == 'dead':
            _hold_video(task, 'needs_review', f'Internal error in {task["kind"]}: {type(e).__name__}: {str(e)[:300]}')
        return state


def blocked_kinds(p):
    if p['autopilot']['emergency_stop']:
        return [k for k in jobs.KIND_ROLE if k not in jobs.READ_ONLY]
    if p['autopilot']['paused']:
        return sorted(jobs.PAID_OR_PUBLISHING)
    return []


def heartbeat_loop(worker_id, roles):
    while not _stop.is_set():
        try:
            d = dbmod.get()
            d.execute('UPDATE workers SET heartbeat_at=? WHERE id=?', (now(), worker_id))
        except Exception:
            log.exception('heartbeat failed')
        _stop.wait(15)


def orchestrator_loop(worker_id, every_s=20):
    while not _stop.is_set():
        try:
            orchestrator.tick(worker_id)
        except Exception:
            log.exception('orchestrator tick failed')
        _stop.wait(every_s)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Blox Studio worker')
    ap.add_argument('--roles', default='all', help='comma list of: ' + ','.join(jobs.ROLES) + ' or all')
    ap.add_argument('--once', action='store_true', help='process at most one task and exit')
    ap.add_argument('--idle-sleep', type=float, default=3.0)
    args = ap.parse_args(argv)
    runtime.init()
    ensure_loaded()
    roles = jobs.ROLES if args.roles == 'all' else [r.strip() for r in args.roles.split(',') if r.strip()]
    worker_id = f'{socket.gethostname()}:{os.getpid()}:{new_id()[:6]}'
    d = dbmod.get()
    d.execute('INSERT INTO workers(id, roles, host, pid, started_at, heartbeat_at, status) VALUES (?,?,?,?,?,?,?)',
              (worker_id, ','.join(roles), socket.gethostname(), os.getpid(), now(), now(), 'running'))
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    threading.Thread(target=heartbeat_loop, args=(worker_id, roles), daemon=True).start()
    if 'orchestrate' in roles and args.once:
        orchestrator.tick(worker_id)
    elif 'orchestrate' in roles:
        # Own thread so slot handling continues while this process runs a long render.
        threading.Thread(target=orchestrator_loop, args=(worker_id,), daemon=True).start()
    log.info('worker started', extra={'worker': worker_id, 'roles': roles})
    while not _stop.is_set():
        try:
            p = prefsmod.get(d)
            task = jobs.claim(worker_id, roles, kinds_blocked=blocked_kinds(p))
            if not task:
                if args.once:
                    break
                _stop.wait(args.idle_sleep)
                continue
            d.execute('UPDATE workers SET current_task=? WHERE id=?', (task['id'], worker_id))
            run_task(task, worker_id)
            d.execute('UPDATE workers SET current_task=NULL WHERE id=?', (worker_id,))
            if args.once:
                break
        except Exception:
            log.exception('worker loop error')
            _stop.wait(5)
    d.execute("UPDATE workers SET status='stopped', heartbeat_at=? WHERE id=?", (now(), worker_id))
    log.info('worker stopped', extra={'worker': worker_id})


if __name__ == '__main__':
    main()
