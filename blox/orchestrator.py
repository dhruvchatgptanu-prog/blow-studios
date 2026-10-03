"""Autopilot orchestration: slots, buffer, assignment, skips, periodic research.

Production and publication are separate:
* production runs ahead and keeps ``buffer_target`` QA-approved videos ready;
* each approved video is assigned to exactly one upcoming slot, uploaded
  ``upload_lead_minutes`` before it, and scheduled with publishAt;
* a slot that cannot be filled safely is skipped with a recorded reason;
* slots in the past are never filled (no catch-up bursts after downtime).

The tick runs under a database lock so only one orchestrator acts at a time.
"""
import json

from . import budget, db as dbmod, jobs, prefs as prefsmod, store, vault, videos
from .research import youtube_api as Y
from .timeutil import fmt, slot_times
from .util import new_id, now

LOCK = 'orchestrator'


def ensure_slots(d, p, t):
    s = p['schedule']
    horizon = t + s['horizon_hours'] * 3600
    for item in slot_times(t - 3600, horizon, s):
        if item['status'] == 'dst_gap':
            key = 'dst_gap:' + item['local']
            if not store.get(key, None, d):
                store.put(key, {'local': item['local'], 'near': item['near']}, d)
            continue
        d.execute('INSERT INTO slots(id, slot_at, status, detail, created_at, updated_at) VALUES (?,?,?,?,?,?) '
                  'ON CONFLICT(slot_at) DO NOTHING',
                  (new_id('sl_'), item['at'], 'open', json.dumps({'dst': item['status']}), t, t))
    # Remove future open slots that no longer match the schedule (cadence edited).
    valid = {round(x['at'], 3) for x in slot_times(t, horizon, s) if x['at']}
    for row in d.query("SELECT id, slot_at FROM slots WHERE status='open' AND slot_at>?", (t,)):
        if round(row['slot_at'], 3) not in valid:
            d.execute('DELETE FROM slots WHERE id=?', (row['id'],))


def publish_blockers(d, p):
    reasons = []
    a = p['autopilot']
    if a['emergency_stop']:
        reasons.append('Emergency stop is active')
    if not a['enabled']:
        reasons.append('Autopilot is off')
    elif a['paused']:
        reasons.append('Autopilot is paused' + (f': {a["pause_reason"]}' if a.get('pause_reason') else ''))
    if not vault.configured('YOUTUBE_REFRESH_TOKEN'):
        reasons.append('YouTube is not connected')
    elif not (store.get('channel', {}, d) or {}).get('confirmed'):
        reasons.append('The connected channel has not been confirmed')
    if store.get('youtube_auth_error', '', d):
        reasons.append(store.get('youtube_auth_error', '', d))
    if p['publishing']['made_for_kids'] is None or p['publishing']['synthetic_disclosure'] is None:
        reasons.append('Audience or synthetic-content disclosure not chosen')
    restriction = store.get('publishing_restriction', None, d)
    if restriction and now() - restriction.get('at', 0) < 86400:
        reasons.append('YouTube recently refused scheduled publishing: ' + restriction['reason'][:160])
    try:
        if Y.remaining('insert', 'publishing', p, d) < 1:
            reasons.append('Daily YouTube upload quota bucket is used up')
    except Exception:
        pass
    return reasons


def eligible_videos(d, p):
    rows = d.query("SELECT * FROM videos WHERE status='approved' AND slot_id IS NULL ORDER BY approved_at")
    out = []
    for r in rows:
        meta = json.loads(r['metadata'] or '{}')
        # Review mode needs approval for everything; demo content always needs it.
        if (p['autopilot']['mode'] == 'review' or r['origin'] == 'demo') and not meta.get('owner_approved'):
            continue
        out.append(r)
    return out


def window_count(d, at):
    """Largest number of publications in any 24 h window that contains ``at``, counting ``at`` itself."""
    rows = [r['slot_at'] for r in d.query("SELECT slot_at FROM slots WHERE status IN "
                                          "('assigned','uploaded','scheduled','published') AND slot_at>? AND slot_at<?",
                                          (at - 86400, at + 86400))]
    pts = sorted(rows + [at])
    best = 0
    for start in pts:
        if at - 86400 < start <= at:
            best = max(best, sum(1 for x in pts if start <= x < start + 86400))
    return best


def describe_buffer(d):
    rows = d.query("SELECT status, COUNT(*) AS n FROM videos WHERE status IN ('discovered','researched','concept_selected',"
                   "'scripted','storyboarded','generating','rendering','checking','repairing','approved','needs_review',"
                   "'blocked') GROUP BY status")
    return {r['status']: r['n'] for r in rows}


def assign_and_skip(d, p, t):
    s = p['schedule']
    tz = s['timezone']
    lead = s['upload_lead_minutes'] * 60
    min_lead = s['min_lead_minutes'] * 60
    blockers = publish_blockers(d, p)
    # Past or too-late open slots are skipped, never back-filled.
    for row in d.query("SELECT * FROM slots WHERE status='open' AND slot_at<=?", (t + min_lead,)):
        if blockers:
            reason = 'Not published: ' + '; '.join(blockers)
        else:
            buf = describe_buffer(d)
            reason = ('No QA-approved video was ready in time (in production: ' +
                      (', '.join(f'{k} {v}' for k, v in buf.items()) or 'none') + ')')
        if row['slot_at'] < t - 60:
            reason = 'Slot passed while the scheduler was not running; not filled to avoid catch-up bursts. ' + reason
        d.execute("UPDATE slots SET status='skipped', reason=?, updated_at=? WHERE id=? AND status='open'",
                  (reason[:600], t, row['id']))
        store.audit('slot_skipped', {'slot': fmt(row['slot_at'], tz), 'reason': reason[:300]}, d=d)
    if blockers:
        return
    upcoming = d.query("SELECT * FROM slots WHERE status='open' AND slot_at>? AND slot_at<=? ORDER BY slot_at",
                       (t + min_lead, t + lead + 600))
    pool = eligible_videos(d, p)
    for slot in upcoming:
        if not pool:
            break
        if window_count(d, slot['slot_at']) > s['max_per_rolling_24h']:
            d.execute("UPDATE slots SET status='skipped', reason=?, updated_at=? WHERE id=?",
                      (f'Rolling 24-hour cap of {s["max_per_rolling_24h"]} publications reached', t, slot['id']))
            continue
        v = pool.pop(0)
        with d.tx():
            n = d.rowcount("UPDATE slots SET status='assigned', video_id=?, updated_at=? WHERE id=? AND status='open'",
                           (v['id'], t, slot['id']))
            if n != 1:
                continue
            d.execute('UPDATE videos SET slot_id=?, updated_at=? WHERE id=? AND slot_id IS NULL', (slot['id'], t, v['id']))
        jobs.enqueue('video.upload', {'slot_id': slot['id']}, video_id=v['id'], idempotency_key=f'upload:{v["id"]}',
                     max_attempts=10, ckey='youtube_upload', d=d)
        store.audit('slot_assigned', {'slot': fmt(slot['slot_at'], tz), 'video': v['id']}, d=d)


def maintain_buffer(d, p, t):
    a = p['autopilot']
    if not a['enabled'] or a['paused'] or a['emergency_stop']:
        return 0
    s = p['schedule']
    # Every QA-approved, unscheduled video counts toward the buffer (in review
    # mode it may still be waiting for the owner).
    approved = int(d.scalar("SELECT COUNT(*) AS n FROM videos WHERE status='approved' AND slot_id IS NULL") or 0)
    in_prod = videos.count(videos.IN_PRODUCTION, d)
    created = 0
    summary = budget.summary(p, d)
    per_video = p['budget']['per_video_usd']
    while approved + in_prod < s['buffer_target'] and in_prod < s['max_in_production']:
        if summary['daily_remaining'] < min(per_video, 0.05) or summary['monthly_remaining'] < min(per_video, 0.05):
            store.put('buffer_note', {'at': t, 'note': 'Budget remaining is too low to start another video today.'}, d)
            break
        vid = videos.create('Untitled (researching)', 'autopilot', d=d)
        jobs.enqueue('video.develop', {}, video_id=vid, idempotency_key=f'develop:{vid}', ckey='openai', d=d)
        in_prod += 1
        created += 1
    return created


def periodic(d, p, t):
    r = p['research']
    if r['enabled'] and vault.configured('YOUTUBE_API_KEY'):
        last = store.get('last_discover', 0, d)
        if t - last >= r['interval_minutes'] * 60:
            store.put('last_discover', t, d)
            jobs.enqueue('research.discover', {}, idempotency_key=f'discover:{int(t // 60)}', max_attempts=2, d=d)
        last = store.get('last_snapshot', 0, d)
        if t - last >= r['snapshot_interval_minutes'] * 60:
            store.put('last_snapshot', t, d)
            jobs.enqueue('research.snapshot', {}, idempotency_key=f'snapshot:{int(t // 60)}', max_attempts=2, d=d)
    an = p['analytics']
    if an['enabled'] and vault.configured('YOUTUBE_REFRESH_TOKEN'):
        last = store.get('last_analytics', 0, d)
        if t - last >= an['interval_hours'] * 3600:
            store.put('last_analytics', t, d)
            jobs.enqueue('analytics.collect', {}, idempotency_key=f'analytics:{int(t // 3600)}', max_attempts=2, d=d)


def tick(owner, t=None, d=None):
    d = d or dbmod.get()
    t = t or now()
    if not jobs.acquire_lock(LOCK, owner, 120, d):
        return {'leader': False}
    p = prefsmod.get(d)
    store.put('orchestrator_heartbeat', t, d)
    if p['autopilot']['emergency_stop']:
        ensure_slots(d, p, t)
        assign_and_skip(d, p, t)
        return {'leader': True, 'emergency_stop': True}
    ensure_slots(d, p, t)
    created = maintain_buffer(d, p, t)
    assign_and_skip(d, p, t)
    periodic(d, p, t)
    return {'leader': True, 'created': created}


def emergency_stop(actor='owner', d=None):
    d = d or dbmod.get()
    p = prefsmod.get(d)
    p['autopilot']['emergency_stop'] = True
    p['autopilot']['paused'] = True
    p['autopilot']['pause_reason'] = 'Emergency stop'
    prefsmod.put(p, d)
    n = d.rowcount("UPDATE tasks SET status='cancelled', updated_at=? WHERE status='queued' AND kind IN (%s)" %
                   ','.join('?' * len(jobs.PAID_OR_PUBLISHING)), (now(), *sorted(jobs.PAID_OR_PUBLISHING)))
    store.audit('emergency_stop', {'cancelled_queued_tasks': n}, actor=actor, d=d)
    return n


def clear_emergency_stop(actor='owner', d=None):
    d = d or dbmod.get()
    p = prefsmod.get(d)
    p['autopilot']['emergency_stop'] = False
    p['autopilot']['pause_reason'] = 'Cleared emergency stop; resume when ready'
    prefsmod.put(p, d)
    store.audit('emergency_stop_cleared', {}, actor=actor, d=d)
