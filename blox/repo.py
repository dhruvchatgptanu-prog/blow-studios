"""Data access helpers for production records."""
import json

from . import config, db as dbmod
from .util import new_id, now


def _j(row, *keys):
    if row:
        for k in keys:
            if k in row and isinstance(row[k], str):
                row[k] = json.loads(row[k] or 'null')
    return row


# ---------------------------------------------------------------- characters
def characters(active_only=True, d=None):
    d = d or dbmod.get()
    rows = d.query('SELECT * FROM characters' + (' WHERE active=1' if active_only else '') + ' ORDER BY created_at')
    return {r['id']: _j(r, 'bible', 'voice') for r in rows}


def save_character(cid, name, bible, voice, active=True, d=None):
    d = d or dbmod.get()
    t = now()
    d.execute('''INSERT INTO characters(id, name, bible, voice, active, created_at, updated_at) VALUES (?,?,?,?,?,?,?)
                 ON CONFLICT(id) DO UPDATE SET name=excluded.name, bible=excluded.bible, voice=excluded.voice,
                 active=excluded.active, updated_at=excluded.updated_at''',
              (cid, name, json.dumps(bible), json.dumps(voice), int(active), t, t))


# ---------------------------------------------------------------- manifests
def save_manifest(video_id, manifest, source, director_script='', validation=None, d=None):
    d = d or dbmod.get()
    with d.tx():
        ver = int(d.scalar('SELECT COALESCE(MAX(version),0) AS v FROM manifests WHERE video_id=?', (video_id,)) or 0) + 1
        mid = new_id('mf_')
        d.execute('INSERT INTO manifests(id, video_id, version, source, body, director_script, validation, created_at) '
                  'VALUES (?,?,?,?,?,?,?,?)', (mid, video_id, ver, source, json.dumps(manifest), director_script,
                                              json.dumps(validation or {}), now()))
        d.execute('UPDATE videos SET manifest_id=?, updated_at=? WHERE id=?', (mid, now(), video_id))
    return mid


def manifest(mid, d=None):
    d = d or dbmod.get()
    return _j(d.one('SELECT * FROM manifests WHERE id=?', (mid,)), 'body', 'validation')


def manifests(video_id, d=None):
    d = d or dbmod.get()
    return [_j(r, 'validation') for r in d.query(
        'SELECT id, version, source, validation, created_at FROM manifests WHERE video_id=? ORDER BY version', (video_id,))]


# ---------------------------------------------------------------- shots & lines
def upsert_shot(video_id, manifest_id, shot_key, renderer, d=None, **fields):
    d = d or dbmod.get()
    row = d.one('SELECT * FROM shots WHERE manifest_id=? AND shot_key=?', (manifest_id, shot_key))
    t = now()
    if not row:
        sid = new_id('sh_')
        d.execute('INSERT INTO shots(id, video_id, manifest_id, shot_key, renderer, status, updated_at) '
                  'VALUES (?,?,?,?,?,?,?)', (sid, video_id, manifest_id, shot_key, renderer, 'pending', t))
        row = {'id': sid}
    if fields:
        sets = {k: (json.dumps(v) if k == 'detail' else v) for k, v in fields.items()}
        sets['updated_at'] = t
        d.execute('UPDATE shots SET ' + ', '.join(f'{k}=?' for k in sets) + ' WHERE id=?', (*sets.values(), row['id']))
    return shot(row['id'], d)


def shot(sid, d=None):
    d = d or dbmod.get()
    return _j(d.one('SELECT * FROM shots WHERE id=?', (sid,)), 'detail')


def shots(manifest_id, d=None):
    d = d or dbmod.get()
    return [_j(r, 'detail') for r in d.query('SELECT * FROM shots WHERE manifest_id=? ORDER BY shot_key', (manifest_id,))]


def upsert_line(video_id, manifest_id, line, d=None, **fields):
    d = d or dbmod.get()
    row = d.one('SELECT * FROM audio_lines WHERE manifest_id=? AND line_key=?', (manifest_id, line['id']))
    t = now()
    if not row:
        lid = new_id('al_')
        d.execute('''INSERT INTO audio_lines(id, video_id, manifest_id, line_key, speaker, text, spoken_text, status,
                     updated_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                  (lid, video_id, manifest_id, line['id'], line['speaker'], line['text'], line['text'], 'pending', t))
        row = {'id': lid}
    if fields:
        sets = {k: (json.dumps(v) if k in ('voice', 'alignment') else v) for k, v in fields.items()}
        sets['updated_at'] = t
        d.execute('UPDATE audio_lines SET ' + ', '.join(f'{k}=?' for k in sets) + ' WHERE id=?',
                  (*sets.values(), row['id']))
    return _j(d.one('SELECT * FROM audio_lines WHERE id=?', (row['id'],)), 'voice', 'alignment')


def lines(manifest_id, d=None):
    d = d or dbmod.get()
    return [_j(r, 'voice', 'alignment') for r in d.query(
        'SELECT * FROM audio_lines WHERE manifest_id=? ORDER BY line_key', (manifest_id,))]


def line_results(manifest_id, d=None):
    """audio_lines rows -> the dict shape production/assembly/QA use."""
    out = {}
    for r in lines(manifest_id, d):
        if r['status'] != 'done' or not r['file']:
            continue
        al = r['alignment'] or {}
        out[r['line_key']] = {'file': str(config.DATA_DIR / r['file']), 'duration_s': r['duration_s'],
                              'words': al.get('words', []), 'alignment_kind': r['alignment_kind'],
                              'provider': al.get('provider'), 'test_voice': al.get('test_voice', False),
                              'asr_text': al.get('asr_text'), 'spec_hash': r['spec_hash']}
    return out


# ---------------------------------------------------------------- renders & QA
def save_render(video_id, manifest_id, status, file=None, cover=None, thumb=None, detail=None, d=None):
    d = d or dbmod.get()
    rid = new_id('rd_')
    d.execute('INSERT INTO renders(id, video_id, manifest_id, status, file, cover_file, thumbnail_file, detail, created_at)'
              ' VALUES (?,?,?,?,?,?,?,?,?)', (rid, video_id, manifest_id, status, file, cover, thumb,
                                             json.dumps(detail or {}, default=str), now()))
    d.execute('UPDATE videos SET render_id=?, updated_at=? WHERE id=?', (rid, now(), video_id))
    return rid


def render(rid, d=None):
    d = d or dbmod.get()
    return _j(d.one('SELECT * FROM renders WHERE id=?', (rid,)), 'detail')


def save_qa(video_id, render_id, report, d=None):
    d = d or dbmod.get()
    qid = new_id('qa_')
    d.execute('INSERT INTO qa_reports(id, video_id, render_id, verdict, checks, summary, created_at) VALUES (?,?,?,?,?,?,?)',
              (qid, video_id, render_id, report['verdict'], json.dumps(report, default=str),
               json.dumps(report['summary']), now()))
    d.execute('UPDATE videos SET qa_report_id=?, updated_at=? WHERE id=?', (qid, now(), video_id))
    return qid


def qa(qid, d=None):
    d = d or dbmod.get()
    r = d.one('SELECT * FROM qa_reports WHERE id=?', (qid,))
    if r:
        r['checks'] = json.loads(r['checks'])
        r['summary'] = json.loads(r['summary'] or '{}')
    return r


def qa_history(video_id, d=None):
    d = d or dbmod.get()
    rows = d.query('SELECT id, render_id, verdict, summary, created_at FROM qa_reports WHERE video_id=? ORDER BY created_at',
                   (video_id,))
    for r in rows:
        r['summary'] = json.loads(r['summary'] or '{}')
    return rows


def repairs(video_id, d=None):
    d = d or dbmod.get()
    return [_j(r, 'detail') for r in d.query('SELECT * FROM repairs WHERE video_id=? ORDER BY created_at', (video_id,))]


def rel(path):
    """Store media paths relative to the data directory."""
    p = str(path)
    root = str(config.DATA_DIR) + '/'
    return p[len(root):] if p.startswith(root) else p


def absp(relpath):
    return str(config.DATA_DIR / relpath) if relpath else None
