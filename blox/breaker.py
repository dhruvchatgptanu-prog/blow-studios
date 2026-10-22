"""Per-provider and global circuit breakers.

closed -> (N consecutive transient failures) -> open until retry_at
open -> (retry_at reached) -> half_open: one trial call allowed
half_open -> success -> closed; failure -> open with a longer delay

The global breaker counts transient failures across all providers in a rolling
hour. When it opens, autopilot production pauses automatically with a reason.
"""
import json

from . import db as dbmod
from .util import new_id, now

THRESHOLD = 4
DELAYS = [60, 300, 900, 3600]
GLOBAL_WINDOW_S = 3600
GLOBAL_THRESHOLD = 25


class Open(Exception):
    def __init__(self, provider, retry_at):
        super().__init__(f'{provider} circuit breaker is open after repeated failures; retrying automatically later')
        self.provider = provider
        self.retry_at = retry_at


def state(provider, d=None):
    d = d or dbmod.get()
    return d.one('SELECT * FROM breakers WHERE provider=?', (provider,))


def check(provider, d=None):
    d = d or dbmod.get()
    t = now()
    row = state(provider, d)
    if not row or row['state'] == 'closed':
        return
    if row['state'] == 'open' and (row['retry_at'] or 0) > t:
        raise Open(provider, row['retry_at'])
    if row['state'] == 'open':
        # Allow exactly one trial call: whoever flips to half_open wins.
        n = d.rowcount("UPDATE breakers SET state='half_open', updated_at=? WHERE provider=? AND state='open'",
                       (t, provider))
        if n == 1:
            return
    raise Open(provider, (row['retry_at'] or t) + 30)


def success(provider, d=None):
    d = d or dbmod.get()
    d.execute('INSERT INTO breakers(provider, state, failures, updated_at) VALUES (?,?,0,?) '
              "ON CONFLICT(provider) DO UPDATE SET state='closed', failures=0, retry_at=NULL, updated_at=excluded.updated_at",
              (provider, 'closed', now()))


def failure(provider, error, d=None):
    d = d or dbmod.get()
    t = now()
    with d.tx():
        row = state(provider, d)
        n = (row['failures'] if row else 0) + 1
        st = row['state'] if row else 'closed'
        if st == 'half_open' or n >= THRESHOLD:
            delay = DELAYS[min(len(DELAYS) - 1, max(0, n - THRESHOLD))]
            d.execute('INSERT INTO breakers(provider, state, failures, opened_at, retry_at, last_error, updated_at) '
                      'VALUES (?,?,?,?,?,?,?) ON CONFLICT(provider) DO UPDATE SET state=excluded.state, '
                      'failures=excluded.failures, opened_at=excluded.opened_at, retry_at=excluded.retry_at, '
                      'last_error=excluded.last_error, updated_at=excluded.updated_at',
                      (provider, 'open', n, t, t + delay, str(error)[:500], t))
        else:
            d.execute('INSERT INTO breakers(provider, state, failures, last_error, updated_at) VALUES (?,?,?,?,?) '
                      'ON CONFLICT(provider) DO UPDATE SET failures=excluded.failures, '
                      'last_error=excluded.last_error, updated_at=excluded.updated_at',
                      (provider, 'closed', n, str(error)[:500], t))
        d.execute('INSERT INTO audit_log(id, at, actor, action, detail) VALUES (?,?,?,?,?)',
                  (new_id('au_'), t, 'system', 'provider_failure', json.dumps({'provider': provider})))
        recent = d.scalar("SELECT COUNT(*) AS n FROM audit_log WHERE action='provider_failure' AND at>?",
                          (t - GLOBAL_WINDOW_S,))
        return recent >= GLOBAL_THRESHOLD


def all_states(d=None):
    d = d or dbmod.get()
    return {r['provider']: r for r in d.query('SELECT * FROM breakers')}


def reset(provider, d=None):
    d = d or dbmod.get()
    d.execute('DELETE FROM breakers WHERE provider=?', (provider,))
