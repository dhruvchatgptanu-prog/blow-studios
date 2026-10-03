"""Slots, DST rules, rolling caps, skips, buffer, pause and emergency stop."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from blox import jobs, orchestrator, prefs, store, videos
from blox.timeutil import slot_times

from .helpers import approved_video, ready_to_publish

ADL = ZoneInfo('Australia/Adelaide')
SCHED = {'timezone': 'Australia/Adelaide', 'interval_minutes': 120, 'anchor_local': '00:00',
         'policy': 'local_wall_clock'}


def ts(y, m, d, h=0, mi=0, tz=ADL):
    return datetime(y, m, d, h, mi, tzinfo=tz).timestamp()


def local_hours(items):
    return [datetime.fromtimestamp(x['at'], ADL).strftime('%H:%M') for x in items if x['at']]


def test_regular_day_has_twelve_wall_clock_slots():
    items = slot_times(ts(2026, 9, 21), ts(2026, 9, 22), SCHED)
    assert local_hours(items) == [f'{h:02d}:00' for h in range(0, 24, 2)]


def test_spring_forward_gap_slot_is_reported_not_created():
    # Adelaide moves from 02:00 ACST to 03:00 ACDT on 2026-10-04.
    start = datetime(2026, 10, 4, 0, 0, tzinfo=ADL).timestamp()
    end = datetime(2026, 10, 5, 0, 0, tzinfo=ADL).timestamp()
    items = slot_times(start, end, SCHED)
    gaps = [x for x in items if x['status'] == 'dst_gap']
    assert [g['local'] for g in gaps] == ['2026-10-04 02:00']
    assert '02:00' not in local_hours(items) and len(local_hours(items)) == 11


def test_fall_back_repeated_time_used_once():
    # 2027-04-04 03:00 ACDT -> 02:00 ACST: 02:00 occurs twice; only the first is used.
    start = ts(2027, 4, 4, 0)
    end = ts(2027, 4, 5, 0)
    items = slot_times(start, end, SCHED)
    hours = local_hours(items)
    assert hours.count('02:00') == 1 and len(hours) == 12
    fold = next(x for x in items if x['status'] == 'dst_fold')
    assert datetime.fromtimestamp(fold['at'], ADL).utcoffset().total_seconds() == 10.5 * 3600  # first (ACDT)


def test_fixed_interval_policy_keeps_real_time_spacing_across_dst():
    s = dict(SCHED, policy='fixed_interval_utc')
    items = slot_times(ts(2026, 10, 3, 12), ts(2026, 10, 4, 12), s)
    gaps = {b['at'] - a['at'] for a, b in zip(items, items[1:])}
    assert gaps == {7200}


def test_custom_interval_and_anchor():
    s = dict(SCHED, interval_minutes=90, anchor_local='00:30')
    hours = local_hours(slot_times(ts(2026, 9, 21), ts(2026, 9, 21, 6), s))
    assert hours == ['00:30', '02:00', '03:30', '05:00']


def test_past_slots_are_skipped_never_backfilled(db, clock):
    clock.t = ts(2026, 9, 21, 8)
    ready_to_publish(db)
    orchestrator.ensure_slots(db, prefs.get(db), clock())
    for _ in range(4):
        approved_video(db)
    # The scheduler was down for ten hours.
    clock.t = ts(2026, 9, 21, 18, 5)
    orchestrator.tick('o1', d=db)
    skipped = db.query("SELECT slot_at, reason FROM slots WHERE status='skipped' ORDER BY slot_at")
    assert len(skipped) >= 5
    assert all('not filled to avoid catch-up' in r['reason'] for r in skipped if r['slot_at'] < clock() - 60)
    assigned = db.query("SELECT slot_at FROM slots WHERE status='assigned'")
    # Only slots inside the upload window (min lead 45 min .. lead 3 h) are assigned: 20:00 (and none earlier).
    assert all(r['slot_at'] > clock() + 45 * 60 for r in assigned)
    assert len(assigned) <= 2
    assert jobs.recent(50, db)[0]['kind'] in ('video.upload', 'research.discover', 'video.develop')


def test_unfillable_slot_is_skipped_with_reason(db, clock):
    clock.t = ts(2026, 9, 21, 9, 30)
    orchestrator.ensure_slots(db, prefs.get(db), clock())
    clock.t = ts(2026, 9, 21, 10, 20)  # the 10:00 slot is now past; autopilot is off
    orchestrator.assign_and_skip(db, prefs.get(db), clock())
    row = db.one('SELECT status, reason FROM slots WHERE slot_at=?', (ts(2026, 9, 21, 10),))
    assert row['status'] == 'skipped'
    assert 'Autopilot is off' in row['reason'] and 'YouTube is not connected' in row['reason']


def test_review_mode_requires_owner_approval(db, clock):
    clock.t = ts(2026, 9, 21, 9, 0)
    ready_to_publish(db, mode='review')
    v = approved_video(db)
    orchestrator.tick('o1', d=db)
    assert videos.get(v, db)['slot_id'] is None
    videos.merge_metadata(v, {'owner_approved': True}, db)
    orchestrator.tick('o1', d=db)
    assert videos.get(v, db)['slot_id'] is not None


def test_each_video_gets_one_slot_and_one_upload_task(db, clock):
    clock.t = ts(2026, 9, 21, 9, 0)
    ready_to_publish(db)
    v = approved_video(db)
    for _ in range(3):
        orchestrator.tick('o1', d=db)
        clock.advance(20)
    assert db.scalar("SELECT COUNT(*) AS n FROM slots WHERE video_id=?", (v,)) == 1
    assert db.scalar("SELECT COUNT(*) AS n FROM tasks WHERE kind='video.upload' AND video_id=?", (v,)) == 1


def test_rolling_24h_cap(db, clock):
    clock.t = ts(2026, 9, 21, 9, 0)
    p = ready_to_publish(db)
    p['schedule']['max_per_rolling_24h'] = 3
    prefs.put(p, db)
    # Three publications already in the previous 24 hours.
    for i, h in enumerate((0, 2, 4)):
        db.execute("INSERT INTO slots(id, slot_at, status, created_at, updated_at) VALUES (?,?, 'published', 0, 0)",
                   (f'old{i}', ts(2026, 9, 21, h)))
    approved_video(db)
    orchestrator.tick('o1', d=db)
    capped = db.query("SELECT reason FROM slots WHERE status='skipped' AND reason LIKE 'Rolling%'")
    assert capped, 'next slot must be skipped because of the rolling cap'
    assert db.scalar("SELECT COUNT(*) AS n FROM slots WHERE status='assigned'") == 0


def test_twelve_per_day_does_not_exceed_cap(db):
    for i in range(12):
        db.execute("INSERT INTO slots(id, slot_at, status, created_at, updated_at) VALUES (?,?, 'scheduled', 0, 0)",
                   (f's{i}', ts(2026, 9, 21, 2 * i)))
    assert orchestrator.window_count(db, ts(2026, 9, 22, 0)) == 12  # 02:00..22:00 plus the new 00:00
    assert orchestrator.window_count(db, ts(2026, 9, 21, 23)) == 13  # an off-grid extra would exceed 12


def test_buffer_kept_ahead_and_respects_limits(db, clock):
    clock.t = ts(2026, 9, 21, 9, 0)
    p = ready_to_publish(db)
    p['schedule'].update(buffer_target=3, max_in_production=2)
    prefs.put(p, db)
    created = orchestrator.maintain_buffer(db, prefs.get(db), clock())
    assert created == 2
    assert orchestrator.maintain_buffer(db, prefs.get(db), clock()) == 0
    assert db.scalar("SELECT COUNT(*) AS n FROM tasks WHERE kind='video.develop'") == 2


def test_paused_autopilot_starts_no_new_videos(db, clock):
    clock.t = ts(2026, 9, 21, 9, 0)
    p = ready_to_publish(db)
    p['autopilot']['paused'] = True
    prefs.put(p, db)
    assert orchestrator.maintain_buffer(db, prefs.get(db), clock()) == 0


def test_emergency_stop_cancels_queued_paid_and_publishing_work(db, clock):
    clock.t = ts(2026, 9, 21, 9, 0)
    ready_to_publish(db)
    jobs.enqueue('video.upload', {}, video_id='v', idempotency_key='u', d=db)
    jobs.enqueue('shot.render', {}, video_id='v', idempotency_key='s', d=db)
    keep = jobs.enqueue('research.snapshot', {}, idempotency_key='r', d=db)
    n = orchestrator.emergency_stop(d=db)
    assert n == 2
    assert db.one('SELECT status FROM tasks WHERE id=?', (keep,))['status'] == 'queued'
    assert 'Emergency stop is active' in orchestrator.publish_blockers(db, prefs.get(db))
    out = orchestrator.tick('o1', d=db)
    assert out['emergency_stop'] and db.scalar("SELECT COUNT(*) AS n FROM videos") == 0
    orchestrator.clear_emergency_stop(d=db)
    a = prefs.get(db)['autopilot']
    assert not a['emergency_stop'] and a['paused'], 'clearing the stop does not silently resume'


def test_running_task_sees_stop_reason(db):
    from blox.tasks import Ctx
    tid = jobs.enqueue('shot.render', {}, idempotency_key='s', d=db)
    t = jobs.claim('w', ['render'], d=db)
    ctx = Ctx(t, 'w')
    assert ctx.stop_reason() is None
    orchestrator.emergency_stop(d=db)
    assert ctx.stop_reason() == 'emergency stop'
    assert tid


def test_dst_gap_is_recorded_for_the_calendar(db, clock):
    clock.t = ts(2026, 10, 3, 20, 0)
    orchestrator.ensure_slots(db, prefs.get(db), clock())
    assert store.get('dst_gap:2026-10-04 02:00', None, db)
    hours = [datetime.fromtimestamp(r['slot_at'], ADL).strftime('%d %H:%M')
             for r in db.query('SELECT slot_at FROM slots ORDER BY slot_at')]
    assert '04 02:00' not in hours and '04 04:00' in hours


def test_editing_cadence_removes_stale_open_slots(db, clock):
    clock.t = ts(2026, 9, 21, 9, 0)
    orchestrator.ensure_slots(db, prefs.get(db), clock())
    p = prefs.get(db)
    p['schedule']['interval_minutes'] = 240
    prefs.put(p, db)
    orchestrator.ensure_slots(db, prefs.get(db), clock())
    future = [datetime.fromtimestamp(r['slot_at'], ADL).hour
              for r in db.query("SELECT slot_at FROM slots WHERE slot_at>?", (clock(),))]
    assert future and all(h % 4 == 0 for h in future)


@pytest.mark.parametrize('bad', [{'interval_minutes': 5}, {'timezone': 'Mars/Olympus'}, {'anchor_local': '25:00'}])
def test_schedule_validation(db, bad):
    p = prefs.get(db)
    p['schedule'].update(bad)
    with pytest.raises(ValueError):
        prefs.put(p, db)


def test_demo_videos_never_publish_without_explicit_approval(db, clock):
    clock.t = ts(2026, 9, 21, 9, 0)
    ready_to_publish(db, mode='autopilot')
    v = approved_video(db)
    db.execute("UPDATE videos SET origin='demo' WHERE id=?", (v,))
    orchestrator.tick('o1', d=db)
    assert videos.get(v, db)['slot_id'] is None
    videos.merge_metadata(v, {'owner_approved': True}, db)
    orchestrator.tick('o1', d=db)
    assert videos.get(v, db)['slot_id'] is not None
