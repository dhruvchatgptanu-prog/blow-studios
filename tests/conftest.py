"""Shared fixtures.

Unit tests (tests/unit) never touch the network or paid services: outbound
HTTP is replaced by ``fake_http`` and any unexpected request fails the test.
Live tests (tests/live) are skipped unless LIVE_TESTS=1 and credentials are
present; see tests/live/README.md.

Database tests run on SQLite by default. Set BLOX_TEST_DATABASE_URL to a
PostgreSQL URL to run the ``anydb`` tests against PostgreSQL too (each test
gets a fresh schema).
"""
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['BLOX_DATA'] = tempfile.mkdtemp(prefix='blox-tests-')
os.environ['ADMIN_PASSWORD'] = 'test-only-pässword'
for _k in ('DATABASE_URL', 'OPENAI_API_KEY', 'RUNWAYML_API_SECRET', 'ELEVENLABS_API_KEY', 'YOUTUBE_API_KEY',
           'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'TRANSCRIPT_PROVIDER_URL', 'TRANSCRIPT_PROVIDER_KEY',
           'BLOX_VAULT_KEY', 'SESSION_SECRET', 'TRUSTED_PROXIES', 'COOKIE_SECURE'):
    os.environ.pop(_k, None)

import pytest  # noqa: E402
import requests  # noqa: E402
from requests.structures import CaseInsensitiveDict  # noqa: E402

PG_URL = os.environ.get('BLOX_TEST_DATABASE_URL', '')


def _fresh(url):
    from blox import runtime
    os.environ['DATABASE_URL'] = url
    runtime.reset_for_tests()
    d = runtime.init()
    return d


def _pg_schema_url(base):
    """Create an isolated schema and return a URL whose search_path points at it."""
    import psycopg
    schema = 't_' + uuid.uuid4().hex[:10]
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA {schema}')
    sep = '&' if '?' in base else '?'
    return base + f'{sep}options=-csearch_path%3D{schema}', schema


@pytest.fixture
def db(tmp_path):
    """Fresh SQLite database for one test."""
    d = _fresh('sqlite:///' + str(tmp_path / 'studio.db'))
    yield d
    from blox import db as dbmod
    dbmod.reset()


@pytest.fixture(params=['sqlite'] + (['postgresql'] if PG_URL else []))
def anydb(request, tmp_path):
    """Runs the test on SQLite and, when configured, on PostgreSQL."""
    from blox import db as dbmod
    if request.param == 'sqlite':
        d = _fresh('sqlite:///' + str(tmp_path / 'studio.db'))
        yield d
        dbmod.reset()
        return
    url, schema = _pg_schema_url(PG_URL)
    d = _fresh(url)
    yield d
    dbmod.reset()
    import psycopg
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'DROP SCHEMA {schema} CASCADE')


class FakeHTTP:
    """Routes ``requests.request`` to handlers; unexpected requests fail loudly."""

    def __init__(self):
        self.routes = []
        self.calls = []

    def on(self, method, prefix, fn=None, *, status=200, json_body=None, headers=None, raises=None, body=b''):
        def default(**_):
            if raises:
                raise raises
            return status, (json.dumps(json_body).encode() if json_body is not None else body), headers or {}
        self.routes.append((method.upper(), prefix, fn or default))
        return self

    def __call__(self, method, url, **kw):
        self.calls.append({'method': method.upper(), 'url': url, **kw})
        for m, prefix, fn in reversed(self.routes):
            if m == method.upper() and url.startswith(prefix):
                out = fn(url=url, **kw)
                if isinstance(out, requests.Response):
                    return out
                status, body, headers = out
                return response(status, body, headers, url)
        raise AssertionError(f'Unexpected outbound request in a unit test: {method} {url}')

    def count(self, method, prefix):
        return sum(1 for c in self.calls if c['method'] == method.upper() and c['url'].startswith(prefix))


def response(status, body=b'', headers=None, url=''):
    r = requests.Response()
    r.status_code = status
    r._content = body if isinstance(body, bytes) else json.dumps(body).encode()
    r.headers = CaseInsensitiveDict(headers or {})
    r.url = url
    return r


@pytest.fixture
def fake_http(monkeypatch):
    f = FakeHTTP()
    monkeypatch.setattr(requests, 'request', f)
    return f


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    """Unit tests must not reach the network even by accident."""
    if 'live' in request.keywords:
        return
    def refuse(*a, **k):
        raise AssertionError('Network access attempted in a unit test')
    monkeypatch.setattr(requests.Session, 'request', refuse)
    if not any(n in request.fixturenames for n in ('fake_http',)):
        monkeypatch.setattr(requests, 'request', refuse)


@pytest.fixture
def client(db):
    from blox.web import security
    from blox.web.app import create_app
    security._attempts.clear()
    app = create_app()
    app.config['TESTING'] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s['owner'] = True
        s['csrf'] = 'test-csrf'
        s['exp'] = 9e12
    c.environ_base['HTTP_X_CSRF'] = 'test-csrf'
    return c


def have(binary):
    import shutil
    return shutil.which(binary) is not None


class Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    """Controls time.time() (blox.util.now and everything else that reads the wall clock)."""
    import time as _time
    c = Clock(1_790_000_000.0)  # 2026-09-21 UTC
    monkeypatch.setattr(_time, 'time', c)
    return c
