"""Durable queue: idempotency, atomic claiming, leases, crash recovery, dead letters."""
import threading

import pytest

from blox import jobs


def test_enqueue_is_idempotent(anydb):
    a = jobs.enqueue('video.voice', {'x': 1}, video_id='v1', idempotency_key='voice:m1:0', d=anydb)
    b = jobs.enqueue('video.voice', {'x': 2}, video_id='v1', idempotency_key='voice:m1:0', d=anydb)
    assert a == b
    assert anydb.scalar('SELECT COUNT(*) AS n FROM tasks') == 1


def test_unknown_kind_rejected(anydb):
    with pytest.raises(ValueError):
        jobs.enqueue('rm -rf', {}, d=anydb)


def test_two_workers_never_claim_the_same_task(anydb):
    for i in range(20):
        jobs.enqueue('research.snapshot', {'i': i}, idempotency_key=f'k{i}', d=anydb)
    claimed, errors = [], []

    def worker(name):
        try:
            while True:
                t = jobs.claim(name, ['research'])
                if not t:
                    return
                claimed.append(t['id'])
        except Exception as e:  # pragma: no cover - surfaced below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(f'w{i}',)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert len(claimed) == 20
    assert len(set(claimed)) == 20


def test_roles_and_due_time_respected(anydb, clock):
    jobs.enqueue('video.upload', {}, idempotency_key='u', d=anydb)
    jobs.enqueue('research.snapshot', {}, idempotency_key='later', due_at=clock() + 600, d=anydb)
    assert jobs.claim('w', ['research'], d=anydb) is None  # not due yet; upload is another role
    clock.advance(601)
    assert jobs.claim('w', ['research'], d=anydb)['kind'] == 'research.snapshot'


def test_crashed_worker_lease_expires_and_task_is_resumed(anydb, clock):
    tid = jobs.enqueue('shot.render', {'shot': 's1'}, idempotency_key='s1', d=anydb)
    t1 = jobs.claim('crashed', ['render'], lease_s=60, d=anydb)
    assert t1['id'] == tid
    assert jobs.claim('other', ['render'], d=anydb) is None  # lease still valid
    clock.advance(61)
    assert jobs.claim('other', ['render'], d=anydb) is None  # expired lease is re-queued with backoff
    assert anydb.one('SELECT status FROM tasks WHERE id=?', (tid,))['status'] == 'queued'
    clock.advance(3600)
    t2 = jobs.claim('other', ['render'], d=anydb)
    assert t2['id'] == tid and t2['lease_owner'] == 'other' and t2['attempts'] == 1
    assert 'lease expired' in t2['last_error']
    # The crashed worker comes back: its writes are rejected because it lost the lease.
    assert jobs.complete(tid, 'crashed', {'bad': True}, d=anydb) is False
    assert jobs.heartbeat(tid, 'crashed', d=anydb) is False
    assert jobs.complete(tid, 'other', {'ok': True}, d=anydb) is True
    assert anydb.one('SELECT status FROM tasks WHERE id=?', (tid,))['status'] == 'succeeded'


def test_retry_backoff_then_dead_letter(anydb, clock):
    tid = jobs.enqueue('video.qa', {}, idempotency_key='qa', max_attempts=3, d=anydb)
    for expected in ('queued', 'queued', 'dead'):
        t = None
        for _ in range(5):
            t = jobs.claim('w', ['qa'], d=anydb)
            if t:
                break
            clock.advance(4000)
        assert t and t['id'] == tid
        assert jobs.retry(tid, 'w', 'boom', d=anydb) == expected
    row = anydb.one('SELECT status, attempts, last_error FROM tasks WHERE id=?', (tid,))
    assert row['status'] == 'dead' and row['attempts'] == 3 and row['last_error'] == 'boom'
    assert jobs.requeue(tid, d=anydb) == 1


def test_wait_does_not_consume_attempts(anydb, clock):
    tid = jobs.enqueue('video.verify', {}, idempotency_key='v', d=anydb)
    for _ in range(10):
        jobs.claim('w', ['publish'], d=anydb)
        jobs.wait(tid, 'w', 30, 'processing', d=anydb)
        clock.advance(31)
    assert anydb.one('SELECT attempts FROM tasks WHERE id=?', (tid,))['attempts'] == 0


def test_concurrency_key_limits(anydb):
    for i in range(3):
        jobs.enqueue('shot.render', {}, idempotency_key=f's{i}', ckey='blender', d=anydb)
    assert jobs.claim('a', ['render'], d=anydb)
    assert jobs.claim('b', ['render'], d=anydb) is None  # blender limit is 1
    assert jobs.claim('b', ['render'], limits={'blender': 2}, d=anydb)


def test_cancelled_task_cannot_be_completed(anydb):
    tid = jobs.enqueue('video.assemble', {}, idempotency_key='a', d=anydb)
    jobs.claim('w', ['render'], d=anydb)
    jobs.cancel(tid, d=anydb)
    assert jobs.complete(tid, 'w', d=anydb) is False
    assert jobs.heartbeat(tid, 'w', d=anydb) is False


def test_leader_lock(anydb, clock):
    assert jobs.acquire_lock('orchestrator', 'a', 60, d=anydb)
    assert not jobs.acquire_lock('orchestrator', 'b', 60, d=anydb)
    assert jobs.acquire_lock('orchestrator', 'a', 60, d=anydb)  # renew
    clock.advance(61)
    assert jobs.acquire_lock('orchestrator', 'b', 60, d=anydb)


def test_blocked_kinds_on_pause_and_estop(anydb):
    from blox import prefs, worker
    p = prefs.get(anydb)
    assert worker.blocked_kinds(p) == []
    p['autopilot']['paused'] = True
    assert set(worker.blocked_kinds(p)) == jobs.PAID_OR_PUBLISHING
    p['autopilot']['emergency_stop'] = True
    blocked = set(worker.blocked_kinds(p))
    assert 'video.upload' in blocked and 'shot.render' in blocked
    assert not blocked & jobs.READ_ONLY
