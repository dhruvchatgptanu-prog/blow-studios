import hashlib
import json
import math
import secrets
import time


def new_id(prefix=''):
    return prefix + secrets.token_hex(10)


def now():
    return time.time()


def sha256_file(path, limit=None):
    h = hashlib.sha256()
    n = 0
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
            n += len(chunk)
            if limit and n > limit:
                raise ValueError('File exceeds size limit')
    return h.hexdigest()


def stable_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:24]


def finite(x, lo, hi, name='value'):
    try:
        x = float(x)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be a number')
    if not math.isfinite(x) or not lo <= x <= hi:
        raise ValueError(f'{name} must be between {lo} and {hi}')
    return x


def integer(x, lo, hi, name='value'):
    v = finite(x, lo, hi, name)
    if int(v) != v:
        raise ValueError(f'{name} must be a whole number')
    return int(v)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def text(x, limit, name='text', allow_empty=True):
    if not isinstance(x, str):
        raise ValueError(f'{name} must be text')
    if len(x) > limit:
        raise ValueError(f'{name} is longer than {limit} characters')
    if not allow_empty and not x.strip():
        raise ValueError(f'{name} is required')
    return x


class Blocked(Exception):
    """A step cannot proceed without a person or a configuration change."""

    def __init__(self, message, state='blocked', retry_after=None):
        super().__init__(message)
        self.state = state  # blocked | needs_credentials | needs_review | failed
        self.retry_after = retry_after


class Retry(Exception):
    """A safe, transient failure. The task is retried with backoff."""

    def __init__(self, message, delay=None):
        super().__init__(message)
        self.delay = delay


class Waiting(Exception):
    """The step is waiting for an external process (provider job, processing)."""

    def __init__(self, message, delay=15):
        super().__init__(message)
        self.delay = delay
