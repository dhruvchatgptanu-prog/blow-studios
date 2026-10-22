"""Core production, research, queue, budget and publishing schema."""


def up(d):
    d.ddl('''
    CREATE TABLE IF NOT EXISTS audit_log (id TEXT PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL,
      action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX IF NOT EXISTS audit_log_at ON audit_log(at);

    CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, kind TEXT NOT NULL, role TEXT NOT NULL, video_id TEXT,
      payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 100,
      due_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
      lease_owner TEXT, lease_expires_at REAL, heartbeat_at REAL, idempotency_key TEXT UNIQUE, ckey TEXT,
      last_error TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
      updated_at REAL NOT NULL, finished_at REAL);
    CREATE INDEX IF NOT EXISTS tasks_claim ON tasks(status, role, due_at);
    CREATE INDEX IF NOT EXISTS tasks_video ON tasks(video_id);

    CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, roles TEXT NOT NULL, host TEXT NOT NULL,
      pid INTEGER NOT NULL, started_at REAL NOT NULL, heartbeat_at REAL NOT NULL, status TEXT NOT NULL,
      current_task TEXT);
    CREATE TABLE IF NOT EXISTS locks (name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL);

    CREATE TABLE IF NOT EXISTS paid_calls (id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
      provider TEXT NOT NULL, operation TEXT NOT NULL, video_id TEXT, status TEXT NOT NULL, provider_ref TEXT,
      request_summary TEXT NOT NULL DEFAULT '{}', estimate REAL NOT NULL DEFAULT 0, actual REAL,
      measured INTEGER NOT NULL DEFAULT 0, usage TEXT NOT NULL DEFAULT '{}', response TEXT,
      error TEXT NOT NULL DEFAULT '',
      created_at REAL NOT NULL, updated_at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS paid_calls_video ON paid_calls(video_id);

    CREATE TABLE IF NOT EXISTS budget_ledger (id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, video_id TEXT,
      category TEXT NOT NULL, provider TEXT NOT NULL, status TEXT NOT NULL, estimate REAL NOT NULL, actual REAL,
      actual_kind TEXT NOT NULL DEFAULT 'estimated', local_day TEXT NOT NULL, local_month TEXT NOT NULL,
      created_at REAL NOT NULL, updated_at REAL NOT NULL, note TEXT NOT NULL DEFAULT '');
    CREATE INDEX IF NOT EXISTS budget_day ON budget_ledger(local_day);
    CREATE INDEX IF NOT EXISTS budget_month ON budget_ledger(local_month);
    CREATE INDEX IF NOT EXISTS budget_video ON budget_ledger(video_id);

    CREATE TABLE IF NOT EXISTS breakers (provider TEXT PRIMARY KEY, state TEXT NOT NULL,
      failures INTEGER NOT NULL DEFAULT 0, opened_at REAL, retry_at REAL, last_error TEXT NOT NULL DEFAULT '',
      updated_at REAL NOT NULL);

    CREATE TABLE IF NOT EXISTS quota_ledger (id TEXT PRIMARY KEY, bucket TEXT NOT NULL, pt_day TEXT NOT NULL,
      units INTEGER NOT NULL, method TEXT NOT NULL, purpose TEXT NOT NULL DEFAULT '', at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS quota_day ON quota_ledger(pt_day, bucket);

    CREATE TABLE IF NOT EXISTS api_cache (key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, expires_at REAL NOT NULL,
      body TEXT NOT NULL);

    CREATE TABLE IF NOT EXISTS research_runs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, params TEXT NOT NULL,
      status TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL, quota_used INTEGER NOT NULL DEFAULT 0,
      coverage TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS ref_channels (channel_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
      uploads_playlist TEXT, subscriber_count INTEGER, subscriber_hidden INTEGER NOT NULL DEFAULT 0,
      video_count INTEGER, watch INTEGER NOT NULL DEFAULT 0, baseline TEXT NOT NULL DEFAULT '{}', fetched_at REAL,
      added_at REAL NOT NULL, note TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS ref_videos (video_id TEXT PRIMARY KEY, url TEXT NOT NULL, channel_id TEXT,
      channel_title TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
      tags TEXT NOT NULL DEFAULT '[]', category_id TEXT, published_at REAL, first_seen_at REAL NOT NULL,
      last_seen_at REAL NOT NULL, duration_s REAL, live TEXT, default_language TEXT, embed_w INTEGER,
      embed_h INTEGER, shorts TEXT NOT NULL DEFAULT '{}', shorts_confidence REAL NOT NULL DEFAULT 0,
      sources TEXT NOT NULL DEFAULT '[]', user_reference INTEGER NOT NULL DEFAULT 0, owned INTEGER NOT NULL DEFAULT 0,
      transcript_status TEXT NOT NULL DEFAULT 'not_requested', media_status TEXT NOT NULL DEFAULT 'unavailable',
      rank TEXT NOT NULL DEFAULT '{}', score REAL, topics TEXT NOT NULL DEFAULT '[]');
    CREATE INDEX IF NOT EXISTS ref_videos_pub ON ref_videos(published_at);
    CREATE INDEX IF NOT EXISTS ref_videos_score ON ref_videos(score);
    CREATE TABLE IF NOT EXISTS ref_snapshots (id TEXT PRIMARY KEY, video_id TEXT NOT NULL, retrieved_at REAL NOT NULL,
      views INTEGER, likes INTEGER, comments INTEGER, run_id TEXT);
    CREATE INDEX IF NOT EXISTS ref_snapshots_v ON ref_snapshots(video_id, retrieved_at);
    CREATE TABLE IF NOT EXISTS transcripts (id TEXT PRIMARY KEY, subject TEXT NOT NULL, provider TEXT NOT NULL,
      provenance TEXT NOT NULL, rights_basis TEXT NOT NULL, language TEXT, timing TEXT NOT NULL,
      status TEXT NOT NULL, segments TEXT NOT NULL DEFAULT '[]', text TEXT NOT NULL DEFAULT '',
      created_at REAL NOT NULL, note TEXT NOT NULL DEFAULT '');
    CREATE INDEX IF NOT EXISTS transcripts_subject ON transcripts(subject);
    CREATE TABLE IF NOT EXISTS ref_analyses (id TEXT PRIMARY KEY, subject TEXT NOT NULL, method TEXT NOT NULL,
      status TEXT NOT NULL, findings TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
      note TEXT NOT NULL DEFAULT '');
    CREATE INDEX IF NOT EXISTS ref_analyses_subject ON ref_analyses(subject);
    CREATE TABLE IF NOT EXISTS patterns (id TEXT PRIMARY KEY, created_at REAL NOT NULL, sources TEXT NOT NULL,
      method TEXT NOT NULL, body TEXT NOT NULL);

    CREATE TABLE IF NOT EXISTS characters (id TEXT PRIMARY KEY, name TEXT NOT NULL, bible TEXT NOT NULL,
      voice TEXT NOT NULL DEFAULT '{}', active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
      updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS concepts (id TEXT PRIMARY KEY, created_at REAL NOT NULL, status TEXT NOT NULL,
      premise TEXT NOT NULL, hook TEXT NOT NULL, ending TEXT NOT NULL, features TEXT NOT NULL DEFAULT '{}',
      inspiration TEXT NOT NULL DEFAULT '{}', originality TEXT NOT NULL DEFAULT '{}', method TEXT NOT NULL);

    CREATE TABLE IF NOT EXISTS videos (id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
      status_reason TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL, concept_id TEXT, manifest_id TEXT,
      render_id TEXT, qa_report_id TEXT, slot_id TEXT, youtube_video_id TEXT, youtube_url TEXT,
      metadata TEXT NOT NULL DEFAULT '{}', features TEXT NOT NULL DEFAULT '{}', settings TEXT NOT NULL DEFAULT '{}',
      legacy_project_id TEXT UNIQUE, approved_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS videos_status ON videos(status);
    CREATE TABLE IF NOT EXISTS video_events (id TEXT PRIMARY KEY, video_id TEXT NOT NULL, at REAL NOT NULL,
      from_status TEXT, to_status TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');
    CREATE INDEX IF NOT EXISTS video_events_v ON video_events(video_id, at);
    CREATE TABLE IF NOT EXISTS manifests (id TEXT PRIMARY KEY, video_id TEXT NOT NULL, version INTEGER NOT NULL,
      source TEXT NOT NULL, body TEXT NOT NULL, director_script TEXT NOT NULL DEFAULT '',
      validation TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS manifests_v ON manifests(video_id, version);
    CREATE TABLE IF NOT EXISTS shots (id TEXT PRIMARY KEY, video_id TEXT NOT NULL, manifest_id TEXT NOT NULL,
      shot_key TEXT NOT NULL, renderer TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
      repair_attempts INTEGER NOT NULL DEFAULT 0, spec_hash TEXT NOT NULL DEFAULT '', file TEXT,
      telemetry_file TEXT, preview_file TEXT, native_w INTEGER, native_h INTEGER, provider_ref TEXT,
      detail TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS shots_key ON shots(manifest_id, shot_key);
    CREATE TABLE IF NOT EXISTS audio_lines (id TEXT PRIMARY KEY, video_id TEXT NOT NULL, manifest_id TEXT NOT NULL,
      line_key TEXT NOT NULL, speaker TEXT NOT NULL, text TEXT NOT NULL, spoken_text TEXT NOT NULL,
      voice TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
      repair_attempts INTEGER NOT NULL DEFAULT 0, file TEXT, duration_s REAL, alignment TEXT NOT NULL DEFAULT '{}',
      alignment_kind TEXT NOT NULL DEFAULT 'none', spec_hash TEXT NOT NULL DEFAULT '',
      error TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS audio_lines_key ON audio_lines(manifest_id, line_key);
    CREATE TABLE IF NOT EXISTS renders (id TEXT PRIMARY KEY, video_id TEXT NOT NULL, manifest_id TEXT NOT NULL,
      status TEXT NOT NULL, file TEXT, cover_file TEXT, thumbnail_file TEXT, detail TEXT NOT NULL DEFAULT '{}',
      created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS qa_reports (id TEXT PRIMARY KEY, video_id TEXT NOT NULL, render_id TEXT NOT NULL,
      verdict TEXT NOT NULL, checks TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS repairs (id TEXT PRIMARY KEY, video_id TEXT NOT NULL, qa_report_id TEXT NOT NULL,
      target TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL,
      detail TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL);

    CREATE TABLE IF NOT EXISTS slots (id TEXT PRIMARY KEY, slot_at REAL NOT NULL UNIQUE, status TEXT NOT NULL,
      video_id TEXT UNIQUE, reason TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}',
      created_at REAL NOT NULL, updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY, video_id TEXT NOT NULL UNIQUE, slot_id TEXT,
      status TEXT NOT NULL, marker TEXT NOT NULL UNIQUE, session_enc TEXT, bytes_total INTEGER,
      bytes_confirmed INTEGER NOT NULL DEFAULT 0, youtube_video_id TEXT UNIQUE, requested TEXT NOT NULL DEFAULT '{}',
      observed TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
      updated_at REAL NOT NULL);

    CREATE TABLE IF NOT EXISTS analytics_snapshots (id TEXT PRIMARY KEY, youtube_video_id TEXT NOT NULL,
      video_id TEXT, fetched_at REAL NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
      metrics TEXT NOT NULL DEFAULT '{}', retention TEXT NOT NULL DEFAULT '[]', missing TEXT NOT NULL DEFAULT '[]');
    CREATE INDEX IF NOT EXISTS analytics_v ON analytics_snapshots(youtube_video_id, fetched_at);
    CREATE TABLE IF NOT EXISTS learning_findings (id TEXT PRIMARY KEY, created_at REAL NOT NULL,
      feature TEXT NOT NULL, metric TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL)
    ''')
    cols = d.columns('assets')
    for name, decl in [('rights', "TEXT NOT NULL DEFAULT ''"), ('created_at', 'REAL'), ('bytes', 'INTEGER'),
                       ('sha256', 'TEXT'), ('meta', "TEXT NOT NULL DEFAULT '{}'")]:
        if name not in cols:
            d.ddl(f'ALTER TABLE assets ADD COLUMN {name} {decl}')
