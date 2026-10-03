"""Numbered, forward-only schema migrations.

Each migration is applied once and recorded in ``schema_migrations``. Legacy
tables from the original release are never dropped; migration 0003 converts
legacy projects into productions while leaving the original rows intact.
"""
import importlib
import time

from .. import db as dbmod

VERSIONS = [
    '0001_legacy_baseline',
    '0002_core_schema',
    '0003_seed_and_legacy',
    '0004_story_backlog',
    '0005_new_cast',
]


def applied(d):
    d.ddl('CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at REAL NOT NULL)')
    return {r['version'] for r in d.query('SELECT version FROM schema_migrations')}


def migrate(d=None):
    d = d or dbmod.get()
    done = applied(d)
    ran = []
    for v in VERSIONS:
        if v in done:
            continue
        mod = importlib.import_module(f'{__name__}.m{v}')
        with d.tx():
            # Re-check inside the lock so two processes starting together
            # cannot both apply the same migration.
            if d.one('SELECT 1 AS x FROM schema_migrations WHERE version=?', (v,)):
                continue
            mod.up(d)
            d.execute('INSERT INTO schema_migrations(version, applied_at) VALUES (?,?)', (v, time.time()))
        ran.append(v)
    return ran


def pending(d=None):
    d = d or dbmod.get()
    done = applied(d)
    return [v for v in VERSIONS if v not in done]
