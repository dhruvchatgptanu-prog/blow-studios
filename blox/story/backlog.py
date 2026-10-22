"""Story backlog: original story plans written ahead of time (by the owner, or by Claude in a chat
session) that autopilot can produce without calling an LLM API.

Every plan is compiled and validated on import; invalid or duplicate plans are rejected with
reasons, and so is a title too close to another creator's imported reference title. Stories are
claimed atomically, one per video: the oldest ready story whose twist type was not used by the last
five produced videos, else the oldest ready story. A batch that repeats a twist type or a mechanism
within five consecutive stories is accepted with a warning (mechanism mix). At production time the plan
is validated again and screened for originality against research references and recent own videos.
"""
import json

from .. import db as dbmod, store
from ..learning import TWIST_TYPES, enum_value, stored_story_fields, story_fields
from ..manifest import compile as C
from ..manifest.validate import validate
from ..util import new_id, now, stable_hash
from . import originality

MAX_BATCH = 200
TWIST_WINDOW = 5         # no twist type twice within this many consecutive stories / recent videos
MIX_WINDOW = 5           # a batch warns on a mechanism repeated within this many consecutive stories
TAKE_SCAN = 200          # ready stories considered by take()


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
    from .. import production, repo
    chars = repo.characters(active_only=False, d=d)
    refs = originality.prepare_reference_titles(_reference_titles(d))
    out, accepted = [], []
    for plan in plans:
        title = str((plan or {}).get('title', 'Untitled'))[:100] if isinstance(plan, dict) else 'Untitled'
        if not isinstance(plan, dict) or len(json.dumps(plan)) > C.MAX_PLAN_BYTES:
            out.append({'title': title, 'status': 'rejected', 'reason': 'Not a JSON object under 400 KB'})
            continue
        try:
            m = C.compile_plan(plan, fps=pr['fps'], width=pr['width'], height=pr['height'], characters=chars,
                               **C.pace_kwargs(p))
            production.preflight(m, chars, p)
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
        tc = originality.manifest_title_check(m, refs=refs)
        if tc['blocked']:
            out.append({'title': title, 'status': 'rejected', 'reason': tc['reason'], 'closest': tc['closest'],
                        'title_score': tc['score']})
            continue
        warnings = [w['message'] for w in rep['warnings']][:20]
        if tc['note']:
            warnings = [tc['note']] + warnings[:19]
        sid = new_id('sb_')
        t = now()
        fields = story_fields(m, plan)
        d.execute('''INSERT INTO story_backlog(id, title, logline, plan, fingerprint, source, status, validation,
                     created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                  (sid, title, str(plan.get('logline', ''))[:300], json.dumps(plan), fp, source[:40], 'ready',
                   json.dumps({'warnings': warnings, 'duration_s': rep['stats'].get('duration_s'),
                               'story': fields}), t, t))
        res = {'title': title, 'status': 'ready', 'id': sid}
        if tc['note']:
            res['note'] = tc['note']
        out.append(res)
        accepted.append((res, fields))
    _warn_repeats(accepted)
    store.audit('stories_imported', {'source': source, 'ready': sum(1 for r in out if r['status'] == 'ready'),
                                     'rejected': sum(1 for r in out if r['status'] == 'rejected')}, d=d)
    return out


def _reference_titles(d):
    from ..research import notes
    return notes.reference_titles(d)


REPEAT_RULES = (
    ('twist_type', 'Twist type', TWIST_WINDOW,
     'Production spaces repeated twists apart when it can; reorder the batch to avoid it.'),
    ('mechanism', 'Mechanism', MIX_WINDOW,
     'A mix of mechanisms keeps the channel varied; reorder the batch or change one story if this was not meant.'),
)


def _warn_repeats(ready):
    """Adds a warning to each accepted story whose twist type, or mechanism, already appears in the previous four
    stories of the batch. ``ready``: [(result, story fields)] in batch order."""
    for key, label, window, advice in REPEAT_RULES:
        for k, (res, fields) in enumerate(ready):
            value = fields.get(key)
            if not value:
                continue
            earlier = [r for r, f in ready[max(0, k - window + 1):k] if f.get(key) == value]
            if earlier:
                res.setdefault('warnings', []).append(
                    f'{label} "{value}" repeats within {window} stories in a row (also in "{earlier[-1]["title"]}"). '
                    + advice)


def plan_twist(plan):
    story = plan.get('story') if isinstance(plan, dict) and isinstance(plan.get('story'), dict) else {}
    return enum_value(story.get('twist_type'), TWIST_TYPES)


def recent_twist_types(d, exclude=None, n=TWIST_WINDOW):
    """Twist types of the last ``n`` produced videos (newest first; None where a video has none).

    A video counts as produced once it has a manifest or has taken a backlog story; cancelled videos do not count.
    The twist type comes from the manifest (body.story.twist_type), else the copy stored on the video at
    development, else the backlog story the video took."""
    rows = d.query('''SELECT v.id, v.manifest_id, v.metadata, m.body FROM videos v
                      LEFT JOIN manifests m ON m.id = v.manifest_id
                      WHERE v.id <> ? AND v.status <> 'cancelled'
                      ORDER BY v.created_at DESC, v.id DESC LIMIT ?''', (exclude or '', n * 10))
    out = []
    for r in rows:
        body = json.loads(r['body']) if r['body'] else {}
        try:
            meta = json.loads(r['metadata'] or '{}')
        except ValueError:
            meta = {}
        twist = story_fields(body).get('twist_type') or stored_story_fields(meta).get('twist_type')
        if not r['manifest_id']:
            sb = d.one("SELECT plan FROM story_backlog WHERE video_id=? AND status='used'", (r['id'],))
            if not sb:
                continue
            twist = twist or plan_twist(json.loads(sb['plan']))
        out.append(twist)
        if len(out) >= n:
            break
    return out


def take(video_id, d=None):
    """Claim a ready story for a video (atomic). Returns the row or None when empty.

    Twist rotation: the oldest ready story whose twist type was not used by the last five produced videos (a story
    without a twist type always qualifies), else the oldest ready story."""
    d = d or dbmod.get()
    for _ in range(5):
        rows = d.query("SELECT id, plan FROM story_backlog WHERE status='ready' ORDER BY created_at, id LIMIT ?",
                       (TAKE_SCAN,))
        if not rows:
            return None
        recent = {t for t in recent_twist_types(d, exclude=video_id) if t}
        fresh = [r for r in rows if plan_twist(_plan(r['plan'])) not in recent]
        pick = (fresh or rows)[0]
        n = d.rowcount("UPDATE story_backlog SET status='used', video_id=?, updated_at=? WHERE id=? AND status='ready'",
                       (video_id, now(), pick['id']))
        if n == 1:
            row = get(pick['id'], d)
            row['rotation'] = ('oldest ready story' if pick is rows[0] else
                               'oldest story with a twist type not used by the last five videos')
            return row
    return None


def _plan(raw):
    try:
        plan = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return plan if isinstance(plan, dict) else {}


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
