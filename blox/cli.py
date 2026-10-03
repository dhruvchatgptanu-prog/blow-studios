"""Command line: migrations, demo render, data migration, backups, password hashing.

    python -m blox.cli migrate
    python -m blox.cli demo --quality preview --voice local_test --out data/demo
    python -m blox.cli qa-demo --dir data/demo
    python -m blox.cli copy-to-postgres --to postgresql://user:pass@host/db
    python -m blox.cli backup [--dest data/backups]
    python -m blox.cli restore <backup.tar.gz>
    python -m blox.cli hash-password
"""
import argparse
import copy
import getpass
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import time

from . import config, db as dbmod, migrations, prefs as prefsmod, runtime

TABLES_ORDER = ['settings', 'projects', 'jobs', 'assets', 'reservations', 'schema_migrations', 'audit_log', 'tasks',
                'workers', 'locks', 'paid_calls', 'budget_ledger', 'breakers', 'quota_ledger', 'api_cache',
                'research_runs', 'ref_channels', 'ref_videos', 'ref_snapshots', 'transcripts', 'ref_analyses', 'patterns',
                'characters', 'concepts', 'videos', 'video_events', 'manifests', 'shots', 'audio_lines', 'renders',
                'qa_reports', 'repairs', 'slots', 'uploads', 'analytics_snapshots', 'learning_findings']


def cmd_migrate(a):
    d = runtime.init()
    print(json.dumps({'database': d.dialect, 'pending': migrations.pending(d)}))


def cmd_demo(a):
    runtime.init()
    from . import demo, production
    p = copy.deepcopy(prefsmod.get())
    p['production']['tts_provider'] = a.voice
    if a.width:
        p['production']['width'], p['production']['height'] = a.width, a.height
    chars = {c['id']: c for c in demo.CHARACTERS}
    t = time.time()
    res = production.produce_local(demo.plan(), chars, p, a.out, quality=a.quality,
                                   log=lambda s: print(f'[{time.time() - t:7.1f}s] {s}', flush=True))
    print(json.dumps({'final': res['assembly']['final'], 'cover': res['assembly']['cover'],
                      'retime': res['retime'], 'seconds': round(time.time() - t, 1)}, indent=1))


def cmd_qa_demo(a):
    """Run QA on a directory produced by ``demo`` (no paid calls)."""
    runtime.init()
    from .qa.run import run_all
    m = json.load(open(os.path.join(a.dir, 'manifest.json')))
    solved = json.load(open(os.path.join(a.dir, 'solved.json')))
    asm = json.load(open(os.path.join(a.dir, 'render', 'assembly.json')))
    tele = [os.path.join(a.dir, 'shots', s['id'], 'telemetry.jsonl') for s in m['shots']]
    lines = {}
    vdir = os.path.join(a.dir, 'voice')
    for ln in m['lines']:
        cands = sorted(f for f in os.listdir(vdir) if f.startswith(ln['id'] + '_') and f.endswith('.wav') and '_raw' not in f)
        if cands:
            from .voice import audio as A
            path = os.path.join(vdir, cands[-1])
            from .voice.tts import estimated_words
            s = A.decode(path)
            lines[ln['id']] = {'file': path, 'duration_s': len(s) / A.SR, 'words': estimated_words(ln['text'], s),
                               'alignment_kind': 'estimated', 'provider': 'local_test', 'test_voice': True}
    p = prefsmod.get()
    rep = run_all(m, solved, tele, lines, asm, p, 'demo', 'demo', allow_paid=False)
    print(json.dumps({'verdict': rep['verdict'], 'summary': rep['summary'], 'reasons': rep['reasons']}, indent=1))
    for c in rep['checks']:
        if c['status'] != 'pass':
            print(f'- [{c["status"]}/{c["severity"]}] {c["name"]} {c["time"] or ""}: {json.dumps(c["evidence"])[:300]}')


def cmd_copy_to_postgres(a):
    """Copy every table from the SQLite database into an empty PostgreSQL database."""
    src_path = str(config.DATA_DIR / 'studio.db')
    if not os.path.exists(src_path):
        sys.exit('No SQLite database at ' + src_path)
    runtime.init()  # migrates the SQLite source first
    os.environ['DATABASE_URL'] = a.to
    dbmod.reset()
    dst = dbmod.get()
    migrations.migrate(dst)
    src = sqlite3.connect(src_path)
    src.row_factory = sqlite3.Row
    copied = {}
    with dst.tx():
        for t in TABLES_ORDER:
            rows = src.execute(f'SELECT * FROM {t}').fetchall()
            if not rows:
                continue
            if dst.scalar(f'SELECT COUNT(*) AS n FROM {t}') and t not in ('schema_migrations', 'characters', 'settings'):
                sys.exit(f'Destination table {t} is not empty; use an empty database')
            cols = rows[0].keys()
            for r in rows:
                dst.execute(f'INSERT INTO {t} ({",".join(cols)}) VALUES ({",".join("?" * len(cols))}) ON CONFLICT DO NOTHING',
                            tuple(r[c] for c in cols))
            copied[t] = len(rows)
    print(json.dumps({'copied': copied, 'note': 'Set DATABASE_URL to the PostgreSQL URL and restart all services.'}))


def cmd_backup(a):
    runtime.init()
    d = dbmod.get()
    dest = a.dest or str(config.BACKUP_DIR)
    os.makedirs(dest, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    out = os.path.join(dest, f'blox-backup-{stamp}.tar.gz')
    tmp = os.path.join(dest, f'.db-{stamp}')
    os.makedirs(tmp, exist_ok=True)
    if d.dialect == 'sqlite':
        src = sqlite3.connect(str(config.DATA_DIR / 'studio.db'))
        bk = sqlite3.connect(os.path.join(tmp, 'studio.db'))
        src.backup(bk)
        bk.close()
        src.close()
    else:
        with open(os.path.join(tmp, 'README.txt'), 'w') as f:
            f.write('PostgreSQL: back up the database with pg_dump (see docs/OPERATIONS.md).\n')
    with tarfile.open(out, 'w:gz') as tar:
        tar.add(tmp, arcname='db')
        if (config.DATA_DIR / 'vault.key').exists() and a.include_key:
            tar.add(str(config.DATA_DIR / 'vault.key'), arcname='vault.key')
        tar.add(str(config.MEDIA_DIR), arcname='files')
    shutil.rmtree(tmp)
    os.chmod(out, 0o600)
    print(json.dumps({'backup': out, 'includes_vault_key': bool(a.include_key),
                      'note': 'Store the vault key separately unless --include-key was used.'}))


def cmd_restore(a):
    if not os.path.exists(a.archive):
        sys.exit('Archive not found')
    if (config.DATA_DIR / 'studio.db').exists() and not a.force:
        sys.exit('A database already exists in BLOX_DATA. Use --force to replace it (it is moved aside, not deleted).')
    config.ensure_dirs()
    with tarfile.open(a.archive) as tar:
        for m in tar.getmembers():
            if m.name.startswith('/') or '..' in m.name.split('/') or m.issym() or m.islnk():
                sys.exit('Unsafe path in archive: ' + m.name)
        if (config.DATA_DIR / 'studio.db').exists():
            os.replace(config.DATA_DIR / 'studio.db', config.DATA_DIR / f'studio.db.before-restore-{int(time.time())}')
        tar.extractall(config.DATA_DIR / '.restore')
    r = config.DATA_DIR / '.restore'
    if (r / 'db' / 'studio.db').exists():
        shutil.move(str(r / 'db' / 'studio.db'), str(config.DATA_DIR / 'studio.db'))
    if (r / 'vault.key').exists():
        shutil.move(str(r / 'vault.key'), str(config.DATA_DIR / 'vault.key'))
    if (r / 'files').exists():
        for f in os.listdir(r / 'files'):
            if not (config.MEDIA_DIR / f).exists():
                shutil.move(str(r / 'files' / f), str(config.MEDIA_DIR / f))
    shutil.rmtree(r, ignore_errors=True)
    print(json.dumps({'restored': a.archive}))


def cmd_hash_password(a):
    from werkzeug.security import generate_password_hash
    pw = getpass.getpass('New studio password (12+ characters): ')
    if len(pw) < 12:
        sys.exit('Use at least 12 characters')
    print(generate_password_hash(pw, method='scrypt'))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='blox')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('migrate').set_defaults(fn=cmd_migrate)
    s = sub.add_parser('demo')
    s.add_argument('--quality', default='preview', choices=['preview', 'final'])
    s.add_argument('--voice', default='local_test', choices=['local_test', 'openai', 'elevenlabs'])
    s.add_argument('--out', default=str(config.DATA_DIR / 'demo'))
    s.add_argument('--width', type=int)
    s.add_argument('--height', type=int)
    s.set_defaults(fn=cmd_demo)
    s = sub.add_parser('qa-demo')
    s.add_argument('--dir', default=str(config.DATA_DIR / 'demo'))
    s.set_defaults(fn=cmd_qa_demo)
    s = sub.add_parser('copy-to-postgres')
    s.add_argument('--to', required=True)
    s.set_defaults(fn=cmd_copy_to_postgres)
    s = sub.add_parser('backup')
    s.add_argument('--dest')
    s.add_argument('--include-key', action='store_true')
    s.set_defaults(fn=cmd_backup)
    s = sub.add_parser('restore')
    s.add_argument('archive')
    s.add_argument('--force', action='store_true')
    s.set_defaults(fn=cmd_restore)
    sub.add_parser('hash-password').set_defaults(fn=cmd_hash_password)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == '__main__':
    main()
