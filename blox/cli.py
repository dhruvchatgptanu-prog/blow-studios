"""Command line: migrations, demo render, data migration, backups, password hashing.

    python -m blox.cli migrate
    python -m blox.cli demo --quality preview --voice local_test --out data/demo
    python -m blox.cli qa-demo --dir data/demo
    python -m blox.cli copy-to-postgres --to postgresql://user:pass@host/db
    python -m blox.cli backup [--dest data/backups]
    python -m blox.cli restore <backup.tar.gz>
    python -m blox.cli hash-password
    python -m blox.cli healthcheck [--worker]   (container health checks)
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
    """Copy every table from a SQLite database into an empty PostgreSQL database."""
    src_path = a.source or str(config.DATA_DIR / 'studio.db')
    if not os.path.exists(src_path):
        sys.exit('No SQLite database at ' + src_path)
    if not a.to.startswith('postgresql://'):
        sys.exit('--to must be a postgresql:// URL')
    # Bring the SQLite source up to the current schema first (whatever DATABASE_URL says).
    os.environ['DATABASE_URL'] = 'sqlite:///' + src_path
    runtime.reset_for_tests()
    runtime.init()
    os.environ['DATABASE_URL'] = a.to
    runtime.reset_for_tests()
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


def _skip_scratch(info):
    """Leave out rendered frame folders and partial downloads; keep shots, voices and finals."""
    parts = info.name.split('/')
    if 'frames' in parts or info.name.endswith('.part'):
        return None
    return info


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
        src = sqlite3.connect(d.path)  # the database actually in use (DATABASE_URL or data/studio.db)
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
        if not a.no_work and config.WORK_DIR.exists():
            tar.add(str(config.WORK_DIR), arcname='work', filter=_skip_scratch)
    shutil.rmtree(tmp)
    os.chmod(out, 0o600)
    print(json.dumps({'backup': out, 'database': d.dialect, 'includes_vault_key': bool(a.include_key),
                      'includes_work': not a.no_work,
                      'note': 'Store the vault key separately unless --include-key was used.'}))


def _merge_tree(src, dst):
    """Move files from src into dst, keeping any file that already exists in dst."""
    n = 0
    for root, _dirs, files in os.walk(src):
        target = dst / os.path.relpath(root, src)
        target.mkdir(parents=True, exist_ok=True)
        for f in files:
            if not (target / f).exists():
                shutil.move(os.path.join(root, f), str(target / f))
                n += 1
    return n


def cmd_restore(a):
    """Restore files, work and (for SQLite) the database from a backup archive.

    The current SQLite database is moved aside, never deleted. For PostgreSQL restore the
    database with pg_restore (docs/OPERATIONS.md); files and work are still restored here."""
    if not os.path.exists(a.archive):
        sys.exit('Archive not found')
    url = config.database_url()
    db_path = url[len('sqlite:///'):] if url.startswith('sqlite:///') else None
    if db_path and os.path.exists(db_path) and not a.force:
        sys.exit('A database already exists. Use --force to replace it (it is moved aside, not deleted).')
    config.ensure_dirs()
    r = config.DATA_DIR / '.restore'
    shutil.rmtree(r, ignore_errors=True)
    with tarfile.open(a.archive) as tar:
        for m in tar.getmembers():
            if m.name.startswith('/') or '..' in m.name.split('/') or m.issym() or m.islnk() or m.isdev():
                sys.exit('Unsafe path in archive: ' + m.name)
        try:
            tar.extractall(r, filter='data')
        except TypeError:  # Python without extraction filters
            tar.extractall(r)
    restored = []
    if (r / 'db' / 'studio.db').exists():
        if db_path:
            if os.path.exists(db_path):
                os.replace(db_path, f'{db_path}.before-restore-{int(time.time())}')
            shutil.move(str(r / 'db' / 'studio.db'), db_path)
            restored.append('database')
        else:
            print('Archive holds a SQLite database but DATABASE_URL is PostgreSQL; use copy-to-postgres after '
                  'restoring it into data/studio.db.', file=sys.stderr)
    if (r / 'vault.key').exists() and not (config.DATA_DIR / 'vault.key').exists():
        shutil.move(str(r / 'vault.key'), str(config.DATA_DIR / 'vault.key'))
        restored.append('vault.key')
    for sub, target in (('files', config.MEDIA_DIR), ('work', config.WORK_DIR)):
        if (r / sub).exists():
            n = _merge_tree(r / sub, target)
            restored.append(f'{sub} ({n} files)')
    shutil.rmtree(r, ignore_errors=True)
    print(json.dumps({'restored': a.archive, 'parts': restored}))


def cmd_hash_password(a):
    from werkzeug.security import generate_password_hash
    pw = getpass.getpass('New studio password (12+ characters): ')
    if len(pw) < 12:
        sys.exit('Use at least 12 characters')
    print(generate_password_hash(pw, method='scrypt'))


def cmd_healthcheck(a):
    """Exit 0 when the database answers (and, with --worker, this host's worker heartbeat is fresh)."""
    import socket
    try:
        d = dbmod.get()
        d.scalar('SELECT 1 AS x')
    except Exception as e:
        sys.exit(f'database unreachable: {type(e).__name__}')
    if a.worker:
        row = d.one("SELECT MAX(heartbeat_at) AS t FROM workers WHERE host=? AND status='running'",
                    (socket.gethostname(),))
        if not row or not row['t'] or time.time() - row['t'] > a.max_age:
            sys.exit('no fresh worker heartbeat on this host')
    print('ok')


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
    s.add_argument('--from', dest='source', help='SQLite file (default data/studio.db)')
    s.set_defaults(fn=cmd_copy_to_postgres)
    s = sub.add_parser('backup')
    s.add_argument('--dest')
    s.add_argument('--include-key', action='store_true')
    s.add_argument('--no-work', action='store_true', help='skip data/work (renders, shots, voices)')
    s.set_defaults(fn=cmd_backup)
    s = sub.add_parser('restore')
    s.add_argument('archive')
    s.add_argument('--force', action='store_true')
    s.set_defaults(fn=cmd_restore)
    sub.add_parser('hash-password').set_defaults(fn=cmd_hash_password)
    s = sub.add_parser('healthcheck')
    s.add_argument('--worker', action='store_true')
    s.add_argument('--max-age', type=float, default=90)
    s.set_defaults(fn=cmd_healthcheck)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == '__main__':
    main()
