"""Small dialect-portable database layer (SQLite or PostgreSQL).

Why not an ORM: the schema is modest, and the queue needs dialect-specific
locking (``BEGIN IMMEDIATE`` on SQLite, ``FOR UPDATE SKIP LOCKED`` on
PostgreSQL) that is clearer as explicit SQL.

Conventions used by every module:
* SQL is written with ``?`` placeholders; they are translated for psycopg.
* Ids are TEXT; times are UTC epoch seconds stored as REAL (DOUBLE PRECISION
  on PostgreSQL); JSON documents are TEXT.
* Booleans are stored as INTEGER 0/1 on both engines.
* Connections run in autocommit mode; ``tx()`` opens an explicit transaction.
"""
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager

from . import config


def _dict_factory(cursor, row):
    return {d[0]: row[i] for i, d in enumerate(cursor.description)}


class Database:
    def __init__(self, url):
        self.url = url
        if url.startswith(('postgres://', 'postgresql://')):
            self.dialect = 'postgres'
        elif url.startswith('sqlite:///'):
            self.dialect = 'sqlite'
            self.path = url[len('sqlite:///'):]
        else:
            raise ValueError('DATABASE_URL must start with sqlite:/// or postgresql://')
        self._local = threading.local()

    # -- connections -------------------------------------------------
    def _connect(self):
        if self.dialect == 'sqlite':
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            c.row_factory = _dict_factory
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('PRAGMA busy_timeout=30000')
            c.execute('PRAGMA synchronous=NORMAL')
            return c
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.url, autocommit=True, row_factory=dict_row)

    def conn(self):
        loc = self._local
        if getattr(loc, 'pid', None) != os.getpid() or getattr(loc, 'c', None) is None:
            loc.c = self._connect()
            loc.pid = os.getpid()
            loc.depth = 0
        return loc.c

    def close(self):
        c = getattr(self._local, 'c', None)
        if c is not None:
            try:
                c.close()
            finally:
                self._local.c = None

    # -- statements --------------------------------------------------
    def _sql(self, sql):
        if self.dialect == 'postgres':
            return sql.replace('%', '%%').replace('?', '%s')
        return sql

    @staticmethod
    def _params(params):
        out = []
        for p in params:
            if isinstance(p, bool):
                out.append(int(p))
            elif isinstance(p, (dict, list)):
                out.append(json.dumps(p, sort_keys=True))
            else:
                out.append(p)
        return tuple(out)

    def execute(self, sql, params=()):
        c = self.conn()
        try:
            return c.execute(self._sql(sql), self._params(params))
        except Exception:
            # A broken PostgreSQL connection must not poison the thread forever.
            if self.dialect == 'postgres' and getattr(c, 'closed', False):
                self._local.c = None
            raise

    def query(self, sql, params=()):
        return self.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        return self.execute(sql, params).fetchone()

    def scalar(self, sql, params=()):
        row = self.one(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    def rowcount(self, sql, params=()):
        return self.execute(sql, params).rowcount

    @contextmanager
    def tx(self):
        """Exclusive-write transaction. Nested calls join the outer one."""
        c = self.conn()
        loc = self._local
        if loc.depth:
            loc.depth += 1
            try:
                yield self
            finally:
                loc.depth -= 1
            return
        c.execute('BEGIN IMMEDIATE' if self.dialect == 'sqlite' else 'BEGIN')
        loc.depth = 1
        try:
            yield self
        except BaseException:
            loc.depth = 0
            try:
                c.execute('ROLLBACK')
            except Exception:
                pass
            raise
        else:
            loc.depth = 0
            c.execute('COMMIT')

    def in_tx(self):
        return bool(getattr(self._local, 'depth', 0))

    # -- helpers -----------------------------------------------------
    def ddl(self, sql):
        """Run DDL written for SQLite, adapting types for PostgreSQL."""
        if self.dialect == 'postgres':
            sql = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', sql)
            sql = re.sub(r'\bBLOB\b', 'BYTEA', sql)
        for stmt in [s.strip() for s in sql.split(';')]:
            if stmt:
                self.execute(stmt)

    def table_exists(self, name):
        if self.dialect == 'sqlite':
            return bool(self.one("SELECT 1 AS x FROM sqlite_master WHERE type='table' AND name=?", (name,)))
        return bool(self.one("SELECT 1 AS x FROM information_schema.tables WHERE table_name=?", (name,)))

    def columns(self, table):
        if self.dialect == 'sqlite':
            return [r['name'] for r in self.query(f'PRAGMA table_info({table})')]
        return [r['column_name'] for r in self.query(
            'SELECT column_name FROM information_schema.columns WHERE table_name=? ORDER BY ordinal_position', (table,))]


_db = None
_lock = threading.Lock()


def get():
    global _db
    with _lock:
        if _db is None or _db.url != config.database_url():
            _db = Database(config.database_url())
        return _db


def reset():
    """Forget the cached handle (tests switch DATABASE_URL)."""
    global _db
    with _lock:
        if _db is not None:
            _db.close()
        _db = None


def loads(v, default=None):
    if v is None or v == '':
        return default
    if isinstance(v, (dict, list)):
        return v
    return json.loads(v)


def dumps(v):
    return json.dumps(v, sort_keys=True, default=str)
