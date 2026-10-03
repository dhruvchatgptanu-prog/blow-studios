"""Durable task queue stored in the main database.

* Atomic claiming: ``BEGIN IMMEDIATE`` (SQLite) or ``FOR UPDATE SKIP LOCKED``
  (PostgreSQL), so two workers can never run the same task.
* Leases + heartbeats: a running task belongs to one worker until its lease
  expires. Every state write checks the lease, so a worker that lost its lease
  (crash, pause, network partition) cannot overwrite the new owner's work.
* Idempotency keys: enqueueing the same logical step twice returns the
  existing task instead of creating a duplicate.
* Retry with exponential backoff; tasks that exhaust ``max_attempts`` go to
  ``dead`` (the dead-letter state) and their video is held for review.
* Concurrency keys cap how many tasks of one kind (for example Runway
  generations) may run at once.
"""
import json
import random

from . import db as dbmod
from .util import new_id, now

KIND_ROLE = {
    'research.discover': 'research',
    'research.snapshot': 'research',
    'research.reference': 'research',
    'research.analyze': 'research',
    'video.develop': 'orchestrate',
    'video.voice': 'render',
    'shot.render': 'render',
    'video.assemble': 'render',
    'video.qa': 'qa',
    'video.repair': 'orchestrate',
    'video.upload': 'publish',
    'video.verify': 'publish',
    'analytics.collect': 'analytics',
}
ROLES = sorted(set(KIND_ROLE.values()))


def live_workers(window_s=60, d=None):
    """Workers whose heartbeat thread reported within ``window_s`` and the roles they cover."""
    d = d or dbmod.get()
    rows = d.query("SELECT id, roles, heartbeat_at, current_task FROM workers WHERE status='running' AND heartbeat_at>?",
                   (now() - window_s,))
    covered = sorted({r for w in rows for r in w['roles'].split(',') if r})
    return {'workers': rows, 'covered': covered, 'missing': [r for r in ROLES if r not in covered],
            'last_seen': max((w['heartbeat_at'] for w in rows), default=None)}
# Tasks that spend money or publish. Pause and emergency stop gate these.
PAID_OR_PUBLISHING = {'video.develop', 'video.voice', 'shot.render', 'video.qa', 'video.repair', 'video.upload'}
READ_ONLY = {'video.verify', 'research.snapshot', 'analytics.collect'}

DEFAULT_LIMITS = {'runway': 2, 'blender': 1, 'openai': 4, 'elevenlabs': 2, 'youtube_upload': 1}

ACTIVE = ('queued', 'running')


def enqueue(kind, payload=None, video_id=None, due_at=None, priority=100, idempotency_key=None,
            max_attempts=5, ckey=None, d=None):
    if kind not in KIND_ROLE:
        raise ValueError('Unknown task kind ' + kind)
    d = d or dbmod.get()
    t = now()
    with d.tx():
        if idempotency_key:
            row = d.one('SELECT id, status FROM tasks WHERE idempotency_key=?', (idempotency_key,))
            if row:
                return row['id']
        tid = new_id('tk_')
        d.execute('''INSERT INTO tasks(id, kind, role, video_id, payload, status, priority, due_at, attempts,
                     max_attempts, idempotency_key, ckey, created_at, updated_at)
                     VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?,?)''',
                  (tid, kind, KIND_ROLE[kind], video_id, json.dumps(payload or {}, default=str), 'queued', priority,
                   due_at if due_at is not None else t, max_attempts, idempotency_key, ckey, t, t))
        return tid


def reenqueue(idempotency_key, delay=0, d=None):
    """Re-arm a finished task that shares an idempotency key (used for repairs)."""
    d = d or dbmod.get()
    t = now()
    return d.rowcount('''UPDATE tasks SET status='queued', due_at=?, attempts=0, lease_owner=NULL,
                         lease_expires_at=NULL, last_error='', updated_at=?, finished_at=NULL
                         WHERE idempotency_key=? AND status NOT IN ('queued','running')''',
                      (t + delay, t, idempotency_key))


def _reclaim_expired(d, t):
    rows = d.query("SELECT id, attempts, max_attempts FROM tasks WHERE status='running' AND lease_expires_at < ?", (t,))
    for r in rows:
        n = r['attempts'] + 1
        status = 'dead' if n >= r['max_attempts'] else 'queued'
        d.execute('''UPDATE tasks SET status=?, attempts=?, lease_owner=NULL, lease_expires_at=NULL,
                     last_error=?, due_at=?, updated_at=? WHERE id=? AND status='running' ''',
                  (status, n, 'Worker lease expired (worker crashed or restarted). Resuming from saved state.',
                   t + backoff(n), t, r['id']))
    return len(rows)


def backoff(attempt, base=15, cap=1800):
    return min(cap, base * (2 ** max(0, attempt - 1))) * (0.85 + 0.3 * random.random())


def claim(worker_id, roles, lease_s=300, kinds_blocked=(), limits=None, d=None):
    d = d or dbmod.get()
    limits = DEFAULT_LIMITS if limits is None else limits
    roles = [r for r in roles if r in ROLES]
    if not roles:
        return None
    t = now()
    with d.tx():
        _reclaim_expired(d, t)
        running = {r['ckey']: r['n'] for r in d.query(
            "SELECT ckey, COUNT(*) AS n FROM tasks WHERE status='running' AND ckey IS NOT NULL GROUP BY ckey")}
        full = [k for k, lim in limits.items() if running.get(k, 0) >= lim]
        where = ["status='queued'", 'due_at<=?', 'role IN (%s)' % ','.join('?' * len(roles))]
        params = [t, *roles]
        if full:
            where.append('(ckey IS NULL OR ckey NOT IN (%s))' % ','.join('?' * len(full)))
            params += full
        if kinds_blocked:
            where.append('kind NOT IN (%s)' % ','.join('?' * len(kinds_blocked)))
            params += list(kinds_blocked)
        sel = 'SELECT id FROM tasks WHERE ' + ' AND '.join(where) + ' ORDER BY priority, due_at, created_at LIMIT 1'
        if d.dialect == 'postgres':
            sel += ' FOR UPDATE SKIP LOCKED'
        row = d.one(sel, params)
        if not row:
            return None
        n = d.rowcount('''UPDATE tasks SET status='running', lease_owner=?, lease_expires_at=?, heartbeat_at=?,
                          updated_at=? WHERE id=? AND status='queued' ''',
                       (worker_id, t + lease_s, t, t, row['id']))
        if n != 1:
            return None
        task = d.one('SELECT * FROM tasks WHERE id=?', (row['id'],))
    task['payload'] = json.loads(task['payload'])
    return task


def heartbeat(task_id, worker_id, lease_s=300, d=None):
    """Extend the lease. False means the lease was lost or the task cancelled."""
    d = d or dbmod.get()
    t = now()
    return d.rowcount('''UPDATE tasks SET lease_expires_at=?, heartbeat_at=?, updated_at=?
                         WHERE id=? AND lease_owner=? AND status='running' ''',
                      (t + lease_s, t, t, task_id, worker_id)) == 1


def owns(task_id, worker_id, d=None):
    d = d or dbmod.get()
    return bool(d.one("SELECT 1 AS x FROM tasks WHERE id=? AND lease_owner=? AND status='running'", (task_id, worker_id)))


def _finish(d, task_id, worker_id, **fields):
    fields['updated_at'] = now()
    cols = ', '.join(f'{k}=?' for k in fields)
    return d.rowcount(f"UPDATE tasks SET {cols} WHERE id=? AND lease_owner=? AND status='running'",
                      (*fields.values(), task_id, worker_id)) == 1


def complete(task_id, worker_id, result=None, d=None):
    d = d or dbmod.get()
    return _finish(d, task_id, worker_id, status='succeeded', result=json.dumps(result or {}, default=str),
                   lease_owner=None, lease_expires_at=None, finished_at=now(), last_error='')


def wait(task_id, worker_id, delay, note='', d=None):
    """Polling an external process. Does not count as a failed attempt."""
    d = d or dbmod.get()
    return _finish(d, task_id, worker_id, status='queued', due_at=now() + max(1, delay), lease_owner=None,
                   lease_expires_at=None, last_error=note[:500])


def retry(task_id, worker_id, error, delay=None, d=None):
    d = d or dbmod.get()
    row = d.one('SELECT attempts, max_attempts FROM tasks WHERE id=?', (task_id,))
    if not row:
        return False
    n = row['attempts'] + 1
    if n >= row['max_attempts']:
        _finish(d, task_id, worker_id, status='dead', attempts=n, last_error=str(error)[:1000],
                lease_owner=None, lease_expires_at=None, finished_at=now())
        return 'dead'
    _finish(d, task_id, worker_id, status='queued', attempts=n, due_at=now() + (delay if delay else backoff(n)),
            last_error=str(error)[:1000], lease_owner=None, lease_expires_at=None)
    return 'queued'


def fail(task_id, worker_id, error, d=None):
    d = d or dbmod.get()
    return _finish(d, task_id, worker_id, status='failed', last_error=str(error)[:1000], lease_owner=None,
                   lease_expires_at=None, finished_at=now())


def cancel(task_id, d=None):
    d = d or dbmod.get()
    return d.rowcount("UPDATE tasks SET status='cancelled', lease_owner=NULL, updated_at=?, finished_at=? "
                      "WHERE id=? AND status IN ('queued','running')", (now(), now(), task_id))


def cancel_for_video(video_id, d=None):
    d = d or dbmod.get()
    return d.rowcount("UPDATE tasks SET status='cancelled', lease_owner=NULL, updated_at=?, finished_at=? "
                      "WHERE video_id=? AND status IN ('queued','running')", (now(), now(), video_id))


def requeue(task_id, d=None):
    """Owner action: put a failed/dead task back in the queue."""
    d = d or dbmod.get()
    return d.rowcount("UPDATE tasks SET status='queued', attempts=0, due_at=?, last_error='', updated_at=?, "
                      "finished_at=NULL WHERE id=? AND status IN ('failed','dead','cancelled')",
                      (now(), now(), task_id))


def active_for_video(video_id, d=None):
    d = d or dbmod.get()
    return d.query("SELECT id, kind, status FROM tasks WHERE video_id=? AND status IN ('queued','running')",
                   (video_id,))


def recent(limit=100, d=None):
    d = d or dbmod.get()
    rows = d.query('SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?', (limit,))
    for r in rows:
        r['payload'] = json.loads(r['payload'])
        r['result'] = json.loads(r['result'])
    return rows


def acquire_lock(name, owner, ttl, d=None):
    """Leader election for singleton loops (scheduler, orchestrator tick)."""
    d = d or dbmod.get()
    t = now()
    with d.tx():
        row = d.one('SELECT owner, expires_at FROM locks WHERE name=?', (name,))
        if row and row['owner'] != owner and row['expires_at'] > t:
            return False
        d.execute('INSERT INTO locks(name, owner, expires_at) VALUES (?,?,?) '
                  'ON CONFLICT(name) DO UPDATE SET owner=excluded.owner, expires_at=excluded.expires_at',
                  (name, owner, t + ttl))
        return True


def release_lock(name, owner, d=None):
    d = d or dbmod.get()
    d.execute('DELETE FROM locks WHERE name=? AND owner=?', (name, owner))
