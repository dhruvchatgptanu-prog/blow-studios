"""Story backlog: validated, original story plans that autopilot can produce without an LLM API."""


def up(d):
    d.ddl('''
    CREATE TABLE IF NOT EXISTS story_backlog (id TEXT PRIMARY KEY, title TEXT NOT NULL, logline TEXT NOT NULL DEFAULT '',
      plan TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE, source TEXT NOT NULL, status TEXT NOT NULL,
      validation TEXT NOT NULL DEFAULT '{}', note TEXT NOT NULL DEFAULT '', video_id TEXT,
      created_at REAL NOT NULL, updated_at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS story_backlog_status ON story_backlog(status, created_at)
    ''')
