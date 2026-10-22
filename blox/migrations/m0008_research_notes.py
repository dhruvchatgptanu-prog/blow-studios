"""Owner-supplied research notes (pasted text or imported files), stored as untrusted data with provenance."""


def up(d):
    d.ddl('''
    CREATE TABLE IF NOT EXISTS research_notes (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
      source TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL, text TEXT NOT NULL,
      extracted TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS research_notes_created ON research_notes(created_at)
    ''')
