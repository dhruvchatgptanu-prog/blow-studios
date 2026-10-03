"""Backups include the active database, uploads and renders, and restore without deleting anything."""
import json
import os
import tarfile

from blox import cli, config, videos


def test_backup_and_restore_round_trip(db, tmp_path, capsys):
    vid = videos.create('Backed up', 'manual', d=db)
    shot = config.WORK_DIR / vid / 'shots' / 's1'
    (shot / 'frames').mkdir(parents=True, exist_ok=True)
    (shot / 'shot.mp4').write_bytes(b'mp4')
    (shot / 'frames' / 'frame_00001.png').write_bytes(b'png')
    cli.main(['backup', '--dest', str(tmp_path / 'bk')])
    out = json.loads(capsys.readouterr().out)
    with tarfile.open(out['backup']) as tar:
        names = tar.getnames()
    assert 'db/studio.db' in names
    assert f'work/{vid}/shots/s1/shot.mp4' in names
    assert not any('/frames' in n for n in names), 'rendered frame folders are scratch'
    assert 'vault.key' not in names
    assert oct(os.stat(out['backup']).st_mode)[-3:] == '600'

    db.execute('DELETE FROM videos')
    os.remove(shot / 'shot.mp4')
    from blox import db as dbmod
    dbmod.reset()
    cli.main(['restore', out['backup'], '--force'])
    res = json.loads(capsys.readouterr().out)
    assert res['parts'][0] == 'database'
    assert (shot / 'shot.mp4').read_bytes() == b'mp4', 'missing render restored'
    d = dbmod.get()
    assert d.one('SELECT title FROM videos WHERE id=?', (vid,))['title'] == 'Backed up'
    path = config.database_url()[len('sqlite:///'):]
    assert any(f.startswith(os.path.basename(path) + '.before-restore-') for f in os.listdir(os.path.dirname(path)))


def test_restore_rejects_unsafe_archives(db, tmp_path):
    import io
    import pytest
    bad = tmp_path / 'bad.tar.gz'
    with tarfile.open(bad, 'w:gz') as tar:
        info = tarfile.TarInfo('../escape.txt')
        info.size = 1
        tar.addfile(info, io.BytesIO(b'x'))
    with pytest.raises(SystemExit):
        cli.main(['restore', str(bad), '--force'])
    assert not (config.DATA_DIR.parent / 'escape.txt').exists()


def test_copy_sqlite_to_postgres(db, tmp_path, capsys):
    import pytest
    from tests.conftest import PG_URL, _pg_schema_url
    if not PG_URL:
        pytest.skip('Set BLOX_TEST_DATABASE_URL to test copying into PostgreSQL')
    vid = videos.create('Copied', 'manual', d=db, metadata={'k': 'v'})
    src = config.database_url()[len('sqlite:///'):]
    url, schema = _pg_schema_url(PG_URL)
    try:
        cli.main(['copy-to-postgres', '--from', src, '--to', url])
        out = json.loads(capsys.readouterr().out)
        assert out['copied']['videos'] == 1
        from blox import db as dbmod
        d = dbmod.get()
        assert d.dialect == 'postgres'
        row = d.one('SELECT title, metadata FROM videos WHERE id=?', (vid,))
        assert row['title'] == 'Copied' and json.loads(row['metadata']) == {'k': 'v'}
        with pytest.raises(SystemExit):
            cli.main(['copy-to-postgres', '--from', src, '--to', url])  # refuses a non-empty target
    finally:
        from blox import db as dbmod
        dbmod.reset()
        import psycopg
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(f'DROP SCHEMA {schema} CASCADE')
