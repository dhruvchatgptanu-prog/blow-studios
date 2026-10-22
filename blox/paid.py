"""Exactly-once guard for paid provider calls.

``run(key, ...)`` executes ``fn`` at most once per idempotency key unless the
request provably never reached the provider. The sequence is:

1. Return the stored response if this key already completed (crash after the
   call but before the caller persisted its output).
2. If a previous attempt is ``intent``/``ambiguous`` (we sent it but do not know
   the outcome), try ``recover()``; otherwise hold for review. We never pay
   twice automatically unless the owner set ``ambiguous_retry_max_usd`` above the
   call's estimate.
3. Check the provider circuit breaker and reserve budget atomically.
4. Record intent, call the provider, then record the outcome.
"""
import json

from . import breaker, budget, db as dbmod, prefs as prefsmod, store
from .http import ProviderError
from .util import Blocked, Retry, new_id, now

BILLING_CODES = ('insufficient_quota', 'billing_hard_limit_reached', 'insufficient_credits', 'quota_exceeded')


def get(key, d=None):
    d = d or dbmod.get()
    return d.one('SELECT * FROM paid_calls WHERE idempotency_key=?', (key,))


def _set(d, key, **fields):
    fields['updated_at'] = now()
    cols = ', '.join(f'{k}=?' for k in fields)
    d.execute(f'UPDATE paid_calls SET {cols} WHERE idempotency_key=?', (*fields.values(), key))


def _store_response(resp):
    try:
        s = json.dumps(resp, default=str)
    except (TypeError, ValueError):
        return None
    return s if len(s) < 2_000_000 else None


def run(key, *, provider, operation, category, estimate, fn, video_id=None, summary=None, recover=None,
        cost=None, provider_ref=None, d=None, p=None):
    d = d or dbmod.get()
    p = p or prefsmod.get(d)
    row = get(key, d)
    attempt_key = key
    if row:
        if row['status'] == 'completed':
            return json.loads(row['response']) if row['response'] else {'provider_ref': row['provider_ref']}
        if row['status'] in ('intent', 'ambiguous'):
            if recover:
                r = recover()
                if r is not None:
                    _set(d, key, status='completed', response=_store_response(r), error='recovered from saved output')
                    budget.commit(key, None, False, d)
                    return r
            ceiling = p['budget']['ambiguous_retry_max_usd']
            if estimate > ceiling or ceiling <= 0:
                _set(d, key, status='ambiguous')
                raise Blocked(f'{provider} {operation}: a previous request was sent but its outcome is unknown. '
                              'It may have been charged. Check the provider dashboard, then use "Resolve" on this '
                              'video to mark it as not submitted or to cancel.', state='needs_review')
            # Owner allowed cheap ambiguous retries. The earlier reservation stays
            # counted as spent; the retry gets its own reservation.
            attempt_key = f'{key}#retry{int(now())}'
            store.audit('ambiguous_retry', {'key': key, 'provider': provider, 'estimate': estimate})
    breaker.check(provider, d)
    budget.reserve(attempt_key, estimate, category, provider, video_id=video_id, note=operation, p=p, d=d)
    t = now()
    if row:
        _set(d, key, status='intent', error='', estimate=estimate)
    else:
        d.execute('''INSERT INTO paid_calls(id, idempotency_key, provider, operation, video_id, status, request_summary,
                     estimate, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                  (new_id('pc_'), key, provider, operation, video_id, 'intent',
                   json.dumps(summary or {}, default=str)[:4000], estimate, t, t))
    try:
        resp = fn()
    except ProviderError as e:
        global_open = False
        if not e.sent:
            _set(d, key, status='failed_not_sent', error=str(e)[:500])
            budget.release(attempt_key, d)
            global_open = breaker.failure(provider, e, d)
            _maybe_global_pause(global_open, d)
            raise Retry(str(e), delay=e.retry_after)
        if e.ambiguous:
            _set(d, key, status='ambiguous', error=str(e)[:500])
            global_open = breaker.failure(provider, e, d)
            _maybe_global_pause(global_open, d)
            raise Blocked(f'{e} Not retried automatically to avoid paying twice. Check the provider dashboard, then '
                          'resolve this video.', state='needs_review')
        # A definitive HTTP response: the provider rejected the request.
        _set(d, key, status='failed', error=str(e)[:500])
        budget.release(attempt_key, d)
        if e.status == 402 or (e.code or '').lower() in BILLING_CODES:
            # Checked before 429: OpenAI reports exhausted credit as 429 insufficient_quota.
            raise Blocked(f'{provider} reports insufficient credits or a billing limit: {e}', state='blocked')
        if e.status == 429 or e.status in (500, 502, 503):
            global_open = breaker.failure(provider, e, d)
            _maybe_global_pause(global_open, d)
            raise Retry(str(e), delay=e.retry_after)
        if e.auth:
            raise Blocked(f'{provider} rejected the credentials or the account lacks access: {e}',
                          state='needs_credentials')
        raise Blocked(f'{provider} rejected the request: {e}', state='failed')
    except (Blocked, Retry):
        raise
    except Exception as e:
        # Our own code failed after the request may have completed.
        _set(d, key, status='ambiguous', error=('internal: ' + type(e).__name__ + ': ' + str(e))[:500])
        raise
    actual, measured = (None, False)
    if cost:
        try:
            actual, measured = cost(resp)
        except Exception:
            actual, measured = (None, False)
    ref = provider_ref(resp) if provider_ref else None
    _set(d, key, status='completed', response=_store_response(resp), provider_ref=ref,
         actual=actual, measured=bool(measured))
    budget.commit(attempt_key, actual if actual is not None else estimate, measured, d)
    breaker.success(provider, d)
    return resp


def _maybe_global_pause(global_open, d):
    if not global_open:
        return
    p = prefsmod.get(d)
    if not p['autopilot']['paused']:
        p['autopilot']['paused'] = True
        p['autopilot']['pause_reason'] = ('Automatic pause: many provider failures in the last hour '
                                          '(global circuit breaker). Check Connections, then resume.')
        prefsmod.put(p, d)
        store.audit('global_breaker_pause', {}, d=d)


def resolve(key, outcome, d=None):
    """Owner reconciliation of an ambiguous call.

    outcome: 'not_submitted' -> release the reservation and allow a new call.
             'submitted_ref:<id>' -> record the provider job id found in the dashboard.
             'charged_discard' -> keep the cost, mark failed (caller will regenerate under a new key).
    """
    d = d or dbmod.get()
    row = get(key, d)
    if not row or row['status'] not in ('intent', 'ambiguous'):
        raise ValueError('That paid request is not awaiting reconciliation')
    if outcome == 'not_submitted':
        _set(d, key, status='failed_not_sent', error='owner confirmed not submitted')
        budget.release(key, d)
    elif outcome.startswith('submitted_ref:'):
        ref = outcome.split(':', 1)[1].strip()
        if not ref or len(ref) > 200:
            raise ValueError('Provide the provider job id')
        _set(d, key, status='completed', provider_ref=ref, response=json.dumps({'provider_ref': ref}))
        budget.commit(key, None, False, d)
    elif outcome == 'charged_discard':
        _set(d, key, status='failed', error='owner marked charged; discarded')
        budget.commit(key, None, False, d)
    else:
        raise ValueError('Unknown outcome')
    store.audit('paid_call_resolved', {'key': key, 'outcome': outcome.split(':')[0]}, actor='owner', d=d)


def pending_for_video(video_id, d=None):
    d = d or dbmod.get()
    return d.query("SELECT idempotency_key, provider, operation, status, error, estimate, created_at FROM paid_calls "
                   "WHERE video_id=? AND status IN ('intent','ambiguous') ORDER BY created_at", (video_id,))
