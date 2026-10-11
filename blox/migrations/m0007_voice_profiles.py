"""Voice profiles: measurements of a set of voice clips (reference clips the owner uploaded, or one of our own
engines' voices), kept as numbers plus provenance. The audio itself is never stored."""


def up(d):
    d.ddl('''
    CREATE TABLE IF NOT EXISTS voice_profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL,
      measured TEXT NOT NULL, created_at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS voice_profiles_created ON voice_profiles(created_at)
    ''')
