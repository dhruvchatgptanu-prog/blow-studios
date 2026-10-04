"""Generic key/value settings and the audit log."""
import json

from . import db as dbmod
from .util import new_id, now


def get(key, default=None, d=None):
    d = d or dbmod.get()
    row = d.one('SELECT value FROM settings WHERE key=?', (key,))
    return json.loads(row['value']) if row else default


def put(key, value, d=None):
    d = d or dbmod.get()
    d.execute('INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (key, json.dumps(value, sort_keys=True, default=str)))


def audit(action, detail=None, actor='system', d=None):
    d = d or dbmod.get()
    d.execute('INSERT INTO audit_log(id, at, actor, action, detail) VALUES (?,?,?,?,?)',
              (new_id('au_'), now(), actor, action, json.dumps(detail or {}, default=str)[:20000]))


def recent_audit(limit=100, d=None):
    d = d or dbmod.get()
    rows = d.query('SELECT * FROM audit_log ORDER BY at DESC LIMIT ?', (limit,))
    for r in rows:
        r['detail'] = json.loads(r['detail'])
    return rows
