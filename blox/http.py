"""Outbound HTTP with classified failures.

The key distinction for paid APIs is whether a failed request may already have
been accepted by the provider:

* ``sent=False``  - connection never established (DNS, refused, connect
  timeout, TLS handshake). Safe to retry; nothing was charged.
* ``ambiguous=True`` - the request may have reached the provider (read
  timeout, connection dropped mid-response, gateway timeout). Paid
  submissions in this state are never retried automatically.
"""
import requests

from .logs import scrub

DEFAULT_TIMEOUT = (10, 90)


class ProviderError(Exception):
    def __init__(self, provider, message, status=None, sent=True, ambiguous=False, retry_after=None,
                 code=None, body=None):
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.sent = sent
        self.ambiguous = ambiguous
        self.retry_after = retry_after
        self.code = code
        self.body = body

    @property
    def auth(self):
        return self.status in (401, 403) and self.code not in ('quotaExceeded', 'rateLimitExceeded',
                                                               'dailyLimitExceeded', 'uploadLimitExceeded')

    @property
    def transient(self):
        return (not self.sent) or self.status in (429, 500, 502, 503) or self.code in ('rateLimitExceeded', 'backendError')


def _retry_after(r):
    v = r.headers.get('Retry-After')
    if not v:
        return None
    try:
        return max(1.0, min(3600.0, float(v)))
    except ValueError:
        return None


def _error_code(r):
    try:
        j = r.json()
    except ValueError:
        return None, None
    err = j.get('error') if isinstance(j, dict) else None
    if isinstance(err, dict):
        errs = err.get('errors') or []
        reason = errs[0].get('reason') if errs and isinstance(errs[0], dict) else None
        return reason or err.get('status') or err.get('code'), scrub(str(err.get('message', '')))[:300]
    if isinstance(err, str):
        return err, scrub(str(j.get('error_description', '')))[:300]
    detail = j.get('detail') if isinstance(j, dict) else None
    if isinstance(detail, dict):
        return detail.get('status'), scrub(str(detail.get('message', '')))[:300]
    return None, None


def request(provider, method, url, *, timeout=DEFAULT_TIMEOUT, ok=(200, 201, 202, 204), session=None, **kw):
    kw.setdefault('allow_redirects', False)
    s = session or requests
    try:
        r = s.request(method, url, timeout=timeout, **kw)
    except requests.exceptions.ConnectTimeout:
        raise ProviderError(provider, f'{provider}: connection timed out before the request was sent', sent=False)
    except requests.exceptions.SSLError:
        raise ProviderError(provider, f'{provider}: TLS handshake failed', sent=False)
    except requests.exceptions.ReadTimeout:
        raise ProviderError(provider, f'{provider}: no response before timeout; the request may have been processed',
                            ambiguous=True)
    except requests.exceptions.ConnectionError as e:
        text = str(e)
        not_sent = any(s in text for s in ('NewConnectionError', 'NameResolutionError', 'Failed to establish',
                                            'Connection refused', 'Max retries exceeded with url'))
        # "Max retries" wraps connect-phase failures in urllib3 when no data was sent.
        if not_sent and 'RemoteDisconnected' not in text and 'ProtocolError' not in text:
            raise ProviderError(provider, f'{provider}: could not connect', sent=False)
        raise ProviderError(provider, f'{provider}: connection dropped; the request may have been processed',
                            ambiguous=True)
    if r.status_code in ok:
        return r
    code, msg = _error_code(r)
    ambiguous = r.status_code in (504,)
    detail = f' ({code})' if code else ''
    raise ProviderError(provider, f'{provider} returned HTTP {r.status_code}{detail}. {msg or ""}'.strip(),
                        status=r.status_code, ambiguous=ambiguous, retry_after=_retry_after(r), code=code)
