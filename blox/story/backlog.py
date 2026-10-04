"""Story backlog: original story plans written ahead of time (by the owner, or by Claude in a chat
session) that autopilot can produce without calling an LLM API.

Every plan is compiled and validated on import; invalid or duplicate plans are rejected with
reasons. Stories are claimed atomically, one per video, oldest first. At production time the plan is
validated again and screened for originality against research references and recent own videos.
"""
import json

from .. import db as dbmod, store
from ..manifest import compile as C
from ..manifest.validate import validate
from ..util import new_id, now, stable_hash

MAX_BATCH = 200


def fingerprint(plan):
    lines = ' '.join(str(ln.get('text', '')).lower() for ln in plan.get('lines') or [])
    return stable_hash([str(plan.get('title', '')).strip().lower(), lines])


def add(plans, source, p, d=None):
    """Validate and store plans. Returns one result per plan: {'title', 'status', 'id'|'reason'}."""
    d = d or dbmod.get()
    if not isinstance(plans, list) or not plans:
        raise ValueError('Provide a non-empty list of story plans')
    if len(plans) > MAX_BATCH:
        raise ValueError(f'At most {MAX_BATCH} stories per import')
    pr = p['production']
    from .. import repo
    chars = repo.characters(active_only=False, d=d)
    out = []
    for plan in plans:
        title = str((plan or {}).get('title', 'Untitled'))[:100] if isinstance(plan, dict) else 'Untitled'
        if not isinstance(plan, dict) or len(json.dumps(plan)) > C.MAX_PLAN_BYTES:
            out.append({'title': title, 'status': 'rejected', 'reason': 'Not a JSON object under 400 KB'})
            continue
        try:
            m = C.compile_plan(plan, fps=pr['fps'], width=pr['width'], height=pr['height'], **C.pace_kwargs(p))
            rep = validate(m, p, characters=chars)
        except (ValueError, KeyError, TypeError) as e:
            out.append({'title': title, 'status': 'rejected', 'reason': f'Could not compile: {e}'[:300]})
            continue
        if not rep['ok']:
            out.append({'title': title, 'status': 'rejected',
                        'reason': '; '.join(e['message'] for e in rep['errors'][:4])})
            continue
        fp = fingerprint(plan)
        if d.one('SELECT 1 AS x FROM story_backlog WHERE fingerprint=?', (fp,)):
            out.append({'title': title, 'status': 'duplicate', 'reason': 'Already in the backlog'})
            continue
        sid = new_id('sb_')
        t = now()
        d.execute('''INSERT INTO story_backlog(id, title, logline, plan, fingerprint, source, status, validation,
                     created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                  (sid, title, str(plan.get('logline', ''))[:300], json.dumps(plan), fp, source[:40], 'ready',
                   json.dumps({'warnings': [w['message'] for w in rep['warnings']][:20],
                               'duration_s': rep['stats'].get('duration_s')}), t, t))
        out.append({'title': title, 'status': 'ready', 'id': sid})
    store.audit('stories_imported', {'source': source, 'ready': sum(1 for r in out if r['status'] == 'ready'),
                                     'rejected': sum(1 for r in out if r['status'] == 'rejected')}, d=d)
    return out


def take(video_id, d=None):
    """Claim the oldest ready story for a video (atomic). Returns the row or None when empty."""
    d = d or dbmod.get()
    for _ in range(5):
        row = d.one("SELECT id FROM story_backlog WHERE status='ready' ORDER BY created_at, id LIMIT 1")
        if not row:
            return None
        n = d.rowcount("UPDATE story_backlog SET status='used', video_id=?, updated_at=? WHERE id=? AND status='ready'",
                       (video_id, now(), row['id']))
        if n == 1:
            return get(row['id'], d)
    return None


def get(sid, d=None):
    d = d or dbmod.get()
    row = d.one('SELECT * FROM story_backlog WHERE id=?', (sid,))
    if row:
        row['plan'] = json.loads(row['plan'])
        row['validation'] = json.loads(row['validation'] or '{}')
    return row


def set_status(sid, status, note='', d=None):
    if status not in ('ready', 'rejected'):
        raise ValueError('Status must be ready or rejected')
    d = d or dbmod.get()
    n = d.rowcount("UPDATE story_backlog SET status=?, note=?, video_id=NULL, updated_at=? WHERE id=? AND status<>'used'",
                   (status, note[:300], now(), sid))
    if n != 1:
        raise ValueError('Unknown story, or it was already used by a video')


def release_for_video(video_id, d=None):
    """Return a story to the backlog when its video is cancelled before anything was published."""
    d = d or dbmod.get()
    return d.rowcount("UPDATE story_backlog SET status='ready', video_id=NULL, updated_at=? WHERE video_id=? "
                      "AND status='used'", (now(), video_id))


def slots_per_day(p):
    s = p['schedule']
    return max(1, min(s['max_per_rolling_24h'], int(round(24 * 60 / s['interval_minutes']))))


def summary(p, d=None):
    d = d or dbmod.get()
    counts = {r['status']: r['n'] for r in d.query('SELECT status, COUNT(*) AS n FROM story_backlog GROUP BY status')}
    ready = counts.get('ready', 0)
    per_day = slots_per_day(p)
    return {'ready': ready, 'used': counts.get('used', 0), 'rejected': counts.get('rejected', 0),
            'per_day': per_day, 'days_left': round(ready / per_day, 1)}


def listing(limit=300, d=None):
    d = d or dbmod.get()
    rows = d.query('SELECT id, title, logline, source, status, validation, note, video_id, created_at FROM story_backlog '
                   "ORDER BY CASE status WHEN 'ready' THEN 0 WHEN 'used' THEN 1 ELSE 2 END, created_at LIMIT ?", (limit,))
    for r in rows:
        r['validation'] = json.loads(r['validation'] or '{}')
    return rows
