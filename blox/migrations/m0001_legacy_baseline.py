"""Schema of the original Blox Studio release (kept byte-compatible)."""


def up(d):
    d.ddl('''
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, body TEXT NOT NULL, updated REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,
      status TEXT NOT NULL, state TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
      attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL, created REAL NOT NULL, slot TEXT UNIQUE);
    CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS reservations (job TEXT PRIMARY KEY, day TEXT NOT NULL, estimate REAL NOT NULL)
    ''')
