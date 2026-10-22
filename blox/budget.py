"""Atomic budget reservations and spend accounting.

Every paid step reserves its *estimated* cost before the provider is called.
The reservation is checked against the per-video, daily and monthly limits in
one transaction, so concurrent workers cannot overspend together.

After the call, the entry is either committed (with a measured cost when the
provider reports usage, otherwise the estimate) or released (the request was
provably never sent). Ambiguous requests stay reserved: we assume they were
charged.

Days and months follow the studio display timezone (default
Australia/Adelaide).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db as dbmod, prefs as prefsmod
from .util import Blocked, new_id, now

CATEGORIES = ('script', 'research', 'analysis', 'animation', 'tts', 'asr', 'vision', 'repair', 'embedding', 'other')


class BudgetExceeded(Blocked):
    def __init__(self, message):
        super().__init__(message, state='blocked')


def _periods(t, tz):
    dt = datetime.fromtimestamp(t, ZoneInfo(tz))
    return dt.date().isoformat(), dt.strftime('%Y-%m')


_SPEND = ("COALESCE(SUM(CASE WHEN status='released' THEN 0 WHEN status='committed' "
          "THEN COALESCE(actual, estimate) ELSE estimate END), 0)")


def spent(d=None, day=None, month=None, video_id=None, category=None):
    d = d or dbmod.get()
    where, params = [], []
    if day:
        where.append('local_day=?'); params.append(day)
    if month:
        where.append('local_month=?'); params.append(month)
    if video_id:
        where.append('video_id=?'); params.append(video_id)
    if category:
        where.append('category=?'); params.append(category)
    sql = f'SELECT {_SPEND} AS s FROM budget_ledger' + (' WHERE ' + ' AND '.join(where) if where else '')
    return float(d.scalar(sql, params) or 0)


def reserve(key, estimate, category, provider, video_id=None, note='', p=None, d=None):
    """Reserve ``estimate`` USD. Idempotent per ``key``. Raises BudgetExceeded."""
    if category not in CATEGORIES:
        raise ValueError('Unknown budget category')
    d = d or dbmod.get()
    p = p or prefsmod.get(d)
    b = p['budget']
    t = now()
    day, month = _periods(t, p['schedule']['timezone'])
    estimate = max(0.0, float(estimate))
    with d.tx():
        row = d.one('SELECT * FROM budget_ledger WHERE key=?', (key,))
        if row and row['status'] != 'released':
            return row
        if estimate > 0:
            if spent(d, day=day) + estimate > b['daily_usd'] + 1e-9:
                raise BudgetExceeded(f'Daily budget of ${b["daily_usd"]:.2f} would be exceeded. '
                                     'Production resumes tomorrow or when the limit is raised.')
            if spent(d, month=month) + estimate > b['monthly_usd'] + 1e-9:
                raise BudgetExceeded(f'Monthly budget of ${b["monthly_usd"]:.2f} would be exceeded.')
            if video_id:
                v_spent = spent(d, video_id=video_id)
                if v_spent + estimate > b['per_video_usd'] + 1e-9:
                    raise BudgetExceeded(f'Per-video budget of ${b["per_video_usd"]:.2f} would be exceeded '
                                         f'(${v_spent:.2f} already used by this video).')
                if category == 'repair':
                    r_spent = spent(d, video_id=video_id, category='repair')
                    cap = b['per_video_usd'] * b['repair_share']
                    if r_spent + estimate > cap + 1e-9:
                        raise BudgetExceeded(f'Repair allowance (${cap:.2f}) for this video is used up.')
        if row:
            # A released reservation (request never sent) is being retried.
            d.execute("UPDATE budget_ledger SET status='reserved', estimate=?, actual=NULL, local_day=?, "
                      "local_month=?, updated_at=? WHERE id=?", (estimate, day, month, t, row['id']))
            return d.one('SELECT * FROM budget_ledger WHERE id=?', (row['id'],))
        rid = new_id('bl_')
        d.execute('''INSERT INTO budget_ledger(id, key, video_id, category, provider, status, estimate,
                     local_day, local_month, created_at, updated_at, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (rid, key, video_id, category, provider, 'reserved', estimate, day, month, t, t, note[:300]))
        return d.one('SELECT * FROM budget_ledger WHERE id=?', (rid,))


def commit(key, actual=None, measured=False, d=None):
    d = d or dbmod.get()
    d.execute("UPDATE budget_ledger SET status='committed', actual=?, actual_kind=?, updated_at=? WHERE key=?",
              (actual, 'measured' if measured else 'estimated', now(), key))


def release(key, d=None):
    d = d or dbmod.get()
    d.execute("UPDATE budget_ledger SET status='released', updated_at=? WHERE key=? AND status='reserved'",
              (now(), key))


def summary(p=None, d=None):
    d = d or dbmod.get()
    p = p or prefsmod.get(d)
    day, month = _periods(now(), p['schedule']['timezone'])

    def split(where, params):
        rows = d.query(f'''SELECT status, actual_kind, COALESCE(SUM(estimate),0) AS est, COALESCE(SUM(actual),0) AS act
                           FROM budget_ledger WHERE {where} GROUP BY status, actual_kind''', params)
        out = {'reserved': 0.0, 'committed_measured': 0.0, 'committed_estimated': 0.0, 'released': 0.0}
        for r in rows:
            if r['status'] == 'reserved':
                out['reserved'] += r['est']
            elif r['status'] == 'released':
                out['released'] += r['est']
            elif r['actual_kind'] == 'measured':
                out['committed_measured'] += r['act']
            else:
                out['committed_estimated'] += r['act'] or r['est']
        out['total_counted'] = out['reserved'] + out['committed_measured'] + out['committed_estimated']
        return {k: round(v, 4) for k, v in out.items()}

    b = p['budget']
    today = split('local_day=?', (day,))
    mon = split('local_month=?', (month,))
    return {
        'currency': b['currency'], 'day': day, 'month': month,
        'today': today, 'month_totals': mon,
        'limits': {'per_video': b['per_video_usd'], 'daily': b['daily_usd'], 'monthly': b['monthly_usd']},
        'daily_remaining': round(b['daily_usd'] - today['total_counted'], 4),
        'monthly_remaining': round(b['monthly_usd'] - mon['total_counted'], 4),
        'by_category_today': {r['category']: round(r['s'], 4) for r in d.query(
            f'SELECT category, {_SPEND} AS s FROM budget_ledger WHERE local_day=? GROUP BY category', (day,))},
        'note': ('Estimates use the editable price table. "Measured" means the provider reported usage '
                 '(for example token counts) that was priced with that table. Your provider invoice is the '
                 'source of truth; set billing limits in each provider dashboard as a hard ceiling.'),
    }


def video_spend(video_id, d=None):
    d = d or dbmod.get()
    rows = d.query('SELECT category, provider, status, estimate, actual, actual_kind, note, created_at '
                   'FROM budget_ledger WHERE video_id=? ORDER BY created_at', (video_id,))
    return {'total': round(spent(d, video_id=video_id), 4), 'entries': rows}
