"""Paid calls: exactly-once submission, ambiguous outcomes, budgets and circuit breakers."""
import pytest

from blox import breaker, budget, paid, prefs
from blox.http import ProviderError
from blox.util import Blocked, Retry


def call(d, fn, key='k1', estimate=0.10, video_id='v1', category='animation', provider='runway'):
    return paid.run(key, provider=provider, operation='op', category=category, estimate=estimate, fn=fn,
                    video_id=video_id, d=d)


def test_completed_call_is_never_repeated(anydb):
    n = []
    fn = lambda: n.append(1) or {'id': 'task-1'}  # noqa: E731
    assert call(anydb, fn) == {'id': 'task-1'}
    assert call(anydb, fn) == {'id': 'task-1'}
    assert len(n) == 1
    assert budget.spent(anydb, video_id='v1') == pytest.approx(0.10)


def test_ambiguous_submission_blocks_and_is_not_retried(anydb):
    n = []

    def timeout():
        n.append(1)
        raise ProviderError('runway', 'read timeout', ambiguous=True)
    with pytest.raises(Blocked) as e:
        call(anydb, timeout)
    assert e.value.state == 'needs_review'
    with pytest.raises(Blocked):
        call(anydb, timeout)  # second attempt refuses before calling the provider
    assert len(n) == 1
    # The reservation stays counted: it may have been charged.
    assert budget.spent(anydb, video_id='v1') == pytest.approx(0.10)
    assert [r['status'] for r in paid.pending_for_video('v1', anydb)] == ['ambiguous']


def test_owner_resolves_ambiguous_as_not_submitted_then_call_proceeds(anydb):
    def timeout():
        raise ProviderError('runway', 'read timeout', ambiguous=True)
    with pytest.raises(Blocked):
        call(anydb, timeout)
    paid.resolve('k1', 'not_submitted', anydb)
    assert budget.spent(anydb, video_id='v1') == 0
    assert call(anydb, lambda: {'id': 'ok'}) == {'id': 'ok'}


def test_crash_between_intent_and_response_is_treated_as_ambiguous(anydb):
    class Crash(Exception):
        pass

    def crash():
        raise Crash('process died mid-call')
    with pytest.raises(Crash):
        call(anydb, crash)
    n = []
    with pytest.raises(Blocked):
        call(anydb, lambda: n.append(1))
    assert not n


def test_recover_hook_avoids_resubmission(anydb):
    def timeout():
        raise ProviderError('openai', 'read timeout', ambiguous=True)
    with pytest.raises(Blocked):
        call(anydb, timeout, provider='openai', category='tts')
    out = paid.run('k1', provider='openai', operation='op', category='tts', estimate=0.1, video_id='v1',
                   fn=lambda: pytest.fail('must not resubmit'), recover=lambda: {'file': 'saved.wav'}, d=anydb)
    assert out == {'file': 'saved.wav'}


def test_not_sent_releases_reservation_and_retries(anydb):
    def refused():
        raise ProviderError('runway', 'could not connect', sent=False)
    with pytest.raises(Retry):
        call(anydb, refused)
    assert budget.spent(anydb, video_id='v1') == 0
    assert call(anydb, lambda: {'id': 'x'}) == {'id': 'x'}


@pytest.mark.parametrize('status,code,state', [(401, None, 'needs_credentials'), (402, None, 'blocked'),
                                               (429, 'insufficient_quota', 'blocked'), (400, None, 'failed')])
def test_definitive_errors(anydb, status, code, state):
    def rejected():
        raise ProviderError('openai', 'rejected', status=status, code=code)
    with pytest.raises(Blocked) as e:
        call(anydb, rejected, provider='openai')
    assert e.value.state == state
    assert budget.spent(anydb, video_id='v1') == 0


def test_rate_limit_is_retried_not_blocked(anydb):
    def limited():
        raise ProviderError('openai', 'slow down', status=429, code='rate_limit_exceeded', retry_after=7)
    with pytest.raises(Retry) as e:
        call(anydb, limited, provider='openai')
    assert e.value.delay == 7


def test_budget_limits(anydb):
    p = prefs.get(anydb)
    p['budget'].update(per_video_usd=0.50, daily_usd=1.00, monthly_usd=100, repair_share=0.5)
    prefs.put(p, anydb)
    budget.reserve('a', 0.40, 'animation', 'runway', video_id='v1', d=anydb)
    with pytest.raises(budget.BudgetExceeded, match='Per-video'):
        budget.reserve('b', 0.20, 'animation', 'runway', video_id='v1', d=anydb)
    budget.reserve('c', 0.45, 'animation', 'runway', video_id='v2', d=anydb)
    with pytest.raises(budget.BudgetExceeded, match='Daily'):
        budget.reserve('d', 0.20, 'animation', 'runway', video_id='v3', d=anydb)
    # Idempotent: the same key does not double-count.
    budget.reserve('a', 0.40, 'animation', 'runway', video_id='v1', d=anydb)
    assert budget.spent(anydb) == pytest.approx(0.85)


def test_repair_share_cap(anydb):
    p = prefs.get(anydb)
    p['budget'].update(per_video_usd=1.0, repair_share=0.25)
    prefs.put(p, anydb)
    budget.reserve('r1', 0.20, 'repair', 'runway', video_id='v1', d=anydb)
    with pytest.raises(budget.BudgetExceeded, match='Repair allowance'):
        budget.reserve('r2', 0.10, 'repair', 'runway', video_id='v1', d=anydb)


def test_budget_exhaustion_blocks_paid_call_before_sending(anydb):
    p = prefs.get(anydb)
    p['budget']['daily_usd'] = 0.05
    prefs.put(p, anydb)
    with pytest.raises(budget.BudgetExceeded):
        call(anydb, lambda: pytest.fail('provider must not be called'))


def test_released_key_is_rechecked_against_limits(anydb):
    p = prefs.get(anydb)
    p['budget'].update(per_video_usd=0.30)
    prefs.put(p, anydb)
    budget.reserve('a', 0.20, 'animation', 'runway', video_id='v1', d=anydb)
    budget.release('a', anydb)
    budget.reserve('b', 0.20, 'animation', 'runway', video_id='v1', d=anydb)
    with pytest.raises(budget.BudgetExceeded):
        budget.reserve('a', 0.20, 'animation', 'runway', video_id='v1', d=anydb)


def test_summary_separates_estimated_and_measured(anydb):
    budget.reserve('a', 0.10, 'script', 'openai', d=anydb)
    budget.commit('a', 0.03, True, anydb)
    budget.reserve('b', 0.20, 'tts', 'openai', d=anydb)
    budget.commit('b', None, False, anydb)
    budget.reserve('c', 0.05, 'tts', 'openai', d=anydb)
    s = budget.summary(d=anydb)['today']
    assert s['committed_measured'] == pytest.approx(0.03)
    assert s['committed_estimated'] == pytest.approx(0.20)
    assert s['reserved'] == pytest.approx(0.05)


def test_breaker_opens_then_half_opens(anydb, clock):
    for _ in range(breaker.THRESHOLD):
        breaker.failure('runway', 'HTTP 503', anydb)
    with pytest.raises(breaker.Open):
        breaker.check('runway', anydb)
    clock.advance(breaker.DELAYS[0] + 1)
    breaker.check('runway', anydb)  # one trial call allowed
    with pytest.raises(breaker.Open):
        breaker.check('runway', anydb)  # concurrent callers still blocked
    breaker.success('runway', anydb)
    breaker.check('runway', anydb)


def test_open_breaker_stops_paid_call_without_reserving(anydb):
    for _ in range(breaker.THRESHOLD):
        breaker.failure('runway', 'HTTP 503', anydb)
    with pytest.raises(breaker.Open):
        call(anydb, lambda: pytest.fail('must not call'))
    assert budget.spent(anydb) == 0


def test_global_breaker_pauses_autopilot(anydb):
    p = prefs.get(anydb)
    p['autopilot'].update(enabled=True, paused=False)
    prefs.put(p, anydb)

    def flaky():
        raise ProviderError('openai', 'HTTP 503', status=503)
    for i in range(breaker.GLOBAL_THRESHOLD):
        breaker.reset('openai', anydb)  # keep the provider breaker closed; only the global count matters here
        with pytest.raises(Retry):
            call(anydb, flaky, key=f'g{i}', provider='openai', category='script', video_id=None)
    a = prefs.get(anydb)['autopilot']
    assert a['paused'] and 'global circuit breaker' in a['pause_reason']
