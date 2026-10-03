"""UTC storage, local display and publication-slot generation.

All instants are stored as UTC epoch seconds. Local time (default
Australia/Adelaide) is used only for display, for the daily/monthly budget
periods and, under the ``local_wall_clock`` policy, to place slots.

Daylight-saving rules for ``local_wall_clock`` (the default):

* Spring forward (Adelaide: first Sunday of October, 02:00 ACST -> 03:00 ACDT):
  a local slot time that does not exist that night (e.g. 02:00) is not created.
  It is reported as ``dst_gap`` in the calendar, not as a skipped publication.
* Fall back (first Sunday of April, 03:00 ACDT -> 02:00 ACST): a local time
  that occurs twice (e.g. 02:00) produces one slot, at its first occurrence.
* So a spring-forward day has one fewer slot and the real gap around the
  change is longer; the rolling-24h cap is still enforced on real time.

``fixed_interval_utc`` places slots every N minutes of real time; their local
labels shift by an hour after a DST change.
"""
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def local(ts, tz):
    return datetime.fromtimestamp(ts, ZoneInfo(tz))


def fmt(ts, tz, with_date=True):
    if ts is None:
        return ''
    dt = local(ts, tz)
    s = dt.strftime('%a %d %b %Y, %H:%M ' if with_date else '%H:%M ') + (dt.tzname() or '')
    return s


def iso_utc(ts):
    return datetime.fromtimestamp(ts, UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(s):
    if not s:
        return None
    s = s.replace('Z', '+00:00')
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def resolve_local(naive, tz):
    """Return (utc_ts, status) for a naive local datetime.

    status is 'ok', 'dst_gap' (time does not exist) or 'dst_fold' (time occurs
    twice; the first occurrence is returned).
    """
    z = ZoneInfo(tz)
    first = naive.replace(tzinfo=z, fold=0)
    second = naive.replace(tzinfo=z, fold=1)
    roundtrip = first.astimezone(UTC).astimezone(z).replace(tzinfo=None)
    if roundtrip != naive:
        return None, 'dst_gap'
    if first.utcoffset() != second.utcoffset():
        return first.astimezone(UTC).timestamp(), 'dst_fold'
    return first.astimezone(UTC).timestamp(), 'ok'


def slot_times(start, end, sched):
    """All slot instants in [start, end) plus DST notes, sorted by time."""
    tz = sched['timezone']
    interval = int(sched['interval_minutes'])
    hh, mm = (int(x) for x in sched['anchor_local'].split(':'))
    out = []
    if sched['policy'] == 'fixed_interval_utc':
        ref, _ = resolve_local(datetime(2026, 1, 1, hh, mm), tz)
        step = interval * 60
        k = int((start - ref) // step)
        t = ref + k * step
        while t < end:
            if t >= start:
                out.append({'at': t, 'status': 'ok'})
            t += step
        return out
    z = ZoneInfo(tz)
    d0 = datetime.fromtimestamp(start, z).date() - timedelta(days=1)
    d1 = datetime.fromtimestamp(end, z).date() + timedelta(days=1)
    seen = set()
    d = d0
    while d <= d1:
        minute = hh * 60 + mm
        # Wall-clock slots repeat from the anchor every local day.
        first = minute % interval
        m = first
        while m < 24 * 60:
            naive = datetime.combine(d, dtime(m // 60, m % 60))
            ts, status = resolve_local(naive, tz)
            if status == 'dst_gap':
                # Report where the missing slot would have been.
                approx, _ = resolve_local(naive + timedelta(hours=1), tz)
                if approx is not None and start <= approx < end:
                    out.append({'at': None, 'status': 'dst_gap', 'local': naive.strftime('%Y-%m-%d %H:%M'),
                                'near': approx})
            elif start <= ts < end and ts not in seen:
                seen.add(ts)
                out.append({'at': ts, 'status': status})
            m += interval
        d += timedelta(days=1)
    out.sort(key=lambda s: s['at'] if s['at'] is not None else s['near'])
    return out


def pacific_day(ts=None):
    """YouTube Data API quota resets at midnight Pacific Time."""
    import time as _t
    return datetime.fromtimestamp(ts if ts is not None else _t.time(), ZoneInfo('America/Los_Angeles')).date().isoformat()


def next_pacific_midnight(ts):
    z = ZoneInfo('America/Los_Angeles')
    dt = datetime.fromtimestamp(ts, z)
    nxt = datetime.combine(dt.date() + timedelta(days=1), dtime(0, 0), tzinfo=z)
    return nxt.timestamp()


def days_ago_date(days, tz='UTC'):
    return (datetime.now(ZoneInfo(tz)).date() - timedelta(days=days)).isoformat()



