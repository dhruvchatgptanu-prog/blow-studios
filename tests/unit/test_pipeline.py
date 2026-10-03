"""QA verdicts, the bounded repair loop, failed renders and the non-destructive legacy upgrade."""
import json
import sqlite3

import pytest

from blox import config, jobs, prefs, repo, videos
from blox.qa.report import Checks, verdict

from .helpers import run_one


def P(**qa):
    p = json.loads(json.dumps(prefs.DEFAULTS))
    p['qa'].update(qa)
    return p


def item(status, severity, repair=None, cid='x'):
    c = Checks(30)
    return c.add(cid, cid, 'motion', status, severity, {}, 0.9, 'test', frames=(0, 10), repair=repair)


def test_verdicts():
    assert verdict([item('pass', 'critical')], P())[0] == 'approved'
    assert verdict([item('fail', 'critical')], P())[0] == 'blocked'
    assert verdict([item('fail', 'major', {'action': 're_render_shot', 'shot': 's1'})], P())[0] == 'repair'
    assert verdict([item('fail', 'major', {'action': 're_render_shot'}), item('fail', 'major', cid='y')], P())[0] == 'blocked'
    assert verdict([item('uncertain', 'major')], P())[0] == 'hold'
    assert verdict([item('uncertain', 'major')], P(uncertain_policy='allow_minor'))[0] == 'approved'
    assert verdict([item('uncertain', 'critical')], P(uncertain_policy='allow_minor'))[0] == 'hold'
    assert verdict([item('fail', 'minor')], P())[0] == 'approved'


def _video_with_shots(db, status='repairing'):
    from blox import demo
    from blox.manifest import compile as C
    m = C.compile_plan(demo.plan(), fps=30, width=540, height=960)
    vid = videos.create('Repair test', 'manual', status=status, d=db)
    mid = repo.save_manifest(vid, m, 'test', '', {'ok': True}, db)
    db.execute('UPDATE videos SET manifest_id=? WHERE id=?', (mid, vid))
    for s in m['shots']:
        repo.upsert_shot(vid, mid, s['id'], 'blender', db, status='done')
    for ln in m['lines']:
        repo.upsert_line(vid, mid, ln, db, status='done')
    return vid, mid


def _qa(db, vid, checks):
    rid = repo.save_render(vid, 'mf', 'done', file='x.mp4', d=db)
    report = {'verdict': 'repair', 'summary': {}, 'reasons': [c['name'] for c in checks], 'checks': checks}
    return repo.save_qa(vid, rid, report, db)


def _repair(db, vid, mid, qid):
    jobs.enqueue('video.repair', {'qa_report_id': qid, 'manifest_id': mid}, video_id=vid,
                 idempotency_key=f'repair:{qid}', d=db)
    return run_one('orchestrate')


def test_repair_rerenders_only_the_failing_shot(db):
    vid, mid = _video_with_shots(db)
    qid = _qa(db, vid, [item('fail', 'major', {'action': 're_render_shot', 'shot': 's2',
                                               'params': {'camera_tighter': True}})])
    t, out = _repair(db, vid, mid, qid)
    assert out == 'succeeded'
    queued = db.query("SELECT payload FROM tasks WHERE kind='shot.render' AND status='queued'")
    assert [json.loads(q['payload'])['shot'] for q in queued] == ['s2']
    sh = next(s for s in repo.shots(mid, db) if s['shot_key'] == 's2')
    assert sh['repair_attempts'] == 1 and sh['detail']['repair_params'] == {'camera_tighter': True}
    assert videos.get(vid, db)['status'] == 'generating'
    assert repo.repairs(vid, db)[0]['target'] == 'shot:s2'


def test_repair_limit_per_shot_blocks_instead_of_publishing(db):
    vid, mid = _video_with_shots(db)
    repo.upsert_shot(vid, mid, 's2', 'blender', db, repair_attempts=2)
    qid = _qa(db, vid, [item('fail', 'major', {'action': 're_render_shot', 'shot': 's2'})])
    _repair(db, vid, mid, qid)
    v = videos.get(vid, db)
    assert v['status'] == 'blocked' and 'after 2 repairs' in v['status_reason']
    assert not db.query("SELECT id FROM tasks WHERE kind='shot.render' AND status='queued'")


def test_repair_rounds_limit(db):
    vid, mid = _video_with_shots(db)
    videos.merge_metadata(vid, {'repair_rounds': 3}, db)
    qid = _qa(db, vid, [item('fail', 'major', {'action': 're_render_shot', 'shot': 's1'})])
    _repair(db, vid, mid, qid)
    v = videos.get(vid, db)
    assert v['status'] == 'blocked' and 'Repair limit reached' in v['status_reason']


def test_repair_that_needs_a_human_is_held(db):
    vid, mid = _video_with_shots(db)
    qid = _qa(db, vid, [item('fail', 'major', {'action': 'rewrite_script'})])
    _repair(db, vid, mid, qid)
    assert videos.get(vid, db)['status'] == 'needs_review'


def test_repair_with_unknown_action_does_not_stall(db):
    vid, mid = _video_with_shots(db)
    qid = _qa(db, vid, [item('fail', 'major', {'action': 'teleport_fix'})])
    _repair(db, vid, mid, qid)
    v = videos.get(vid, db)
    assert v['status'] == 'needs_review' and 'cannot perform automatically' in v['status_reason']


def test_failed_render_retries_then_holds_video(db, monkeypatch, clock, tmp_path):
    vid, mid = _video_with_shots(db, status='generating')
    repo.upsert_shot(vid, mid, 's1', 'blender', db, status='pending')
    broken = tmp_path / 'blender'
    broken.write_text('#!/bin/sh\necho "Error: GPU exploded" >&2\nexit 3\n')
    broken.chmod(0o755)
    monkeypatch.setattr(config, 'BLENDER_BIN', str(broken))
    jobs.enqueue('shot.render', {'manifest_id': mid, 'shot': 's1'}, video_id=vid, idempotency_key='r1',
                 max_attempts=2, d=db)
    outs = []
    for _ in range(3):
        t, out = run_one('render')
        if t:
            outs.append(out)
        clock.advance(4000)
    assert outs == ['queued', 'dead']
    v = videos.get(vid, db)
    assert v['status'] == 'blocked' and 'failed repeatedly' in v['status_reason']
    task = db.one("SELECT last_error FROM tasks WHERE idempotency_key='r1'")
    assert 'GPU exploded' in task['last_error'] or 'exit' in task['last_error'].lower()


def test_missing_blender_is_reported(db, monkeypatch):
    from blox.animation import blender as BL
    monkeypatch.setattr(config, 'BLENDER_BIN', '/nonexistent/blender')
    assert BL.available() is False


# ------------------------------------------------------------------ legacy upgrade
LEGACY_SCHEMA = '''
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE projects (id TEXT PRIMARY KEY, body TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE jobs (id TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
  due REAL NOT NULL, created REAL NOT NULL, slot TEXT UNIQUE);
CREATE TABLE assets (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL);
CREATE TABLE reservations (job TEXT PRIMARY KEY, day TEXT NOT NULL, estimate REAL NOT NULL);
'''


def test_legacy_database_upgrade_is_non_destructive(tmp_path):
    from blox import db as dbmod, runtime, store
    path = tmp_path / 'legacy.db'
    c = sqlite3.connect(path)
    c.executescript(LEGACY_SCHEMA)
    footage = {'id': 'p1', 'title': 'Footage short', 'format': 'shorts', 'narration': True, 'output': 'p1.mp4',
               'scenes': [{'clip': 'a1', 'duration': 4, 'narration': 'One leap.', 'action': 'Jump'},
                          {'clip': 'a2', 'duration': 4, 'narration': 'Made it!', 'action': 'Land'}]}
    uploaded = {'id': 'p2', 'title': 'Old upload', 'format': 'shorts', 'youtube_id': 'oldYT123456',
                'scenes': [{'setting': 'sky', 'action': 'jump', 'duration': 5}]}
    c.execute('INSERT INTO projects VALUES (?,?,?)', ('p1', json.dumps(footage), 1.0))
    c.execute('INSERT INTO projects VALUES (?,?,?)', ('p2', json.dumps(uploaded), 2.0))
    c.execute("INSERT INTO jobs VALUES ('j1','p2','render','running','{\"runway_task\":\"rt-9\"}','',1,0,0,NULL)")
    c.execute("INSERT INTO reservations VALUES ('j1','2026-09-30',0.84)")
    c.execute("INSERT INTO assets VALUES ('a1','clip1.mp4','video')")
    c.execute("INSERT INTO settings VALUES ('preferences', ?)",
              (json.dumps({'autopilot': True, 'mode': 'autopilot', 'audience': False, 'timezone': 'Australia/Adelaide',
                           'daily_estimate_cap': 9.0}),))
    c.commit()
    c.close()
    import os
    os.environ['DATABASE_URL'] = 'sqlite:///' + str(path)
    runtime.reset_for_tests()
    d = runtime.init()
    try:
        assert d.scalar('SELECT COUNT(*) AS n FROM projects') == 2
        assert d.one("SELECT status FROM jobs WHERE id='j1'")['status'] == 'cancelled'
        v1 = d.one("SELECT * FROM videos WHERE legacy_project_id='p1'")
        v2 = d.one("SELECT * FROM videos WHERE legacy_project_id='p2'")
        assert v1['manifest_id'] and json.loads(v1['metadata'])['resume_status'] == 'scripted'
        assert v1['render_id']
        assert v2['status'] == 'uploaded_private' and v2['youtube_video_id'] == 'oldYT123456'
        assert json.loads(v2['metadata'])['legacy_job']['state']['runway_task'] == 'rt-9'
        assert d.one("SELECT status FROM tasks WHERE video_id=? AND kind='video.verify'", (v2['id'],))
        assert d.one("SELECT estimate FROM budget_ledger WHERE key='legacy:j1'")['estimate'] == pytest.approx(0.84)
        p = prefs.get(d)
        assert p['autopilot']['enabled'] is False and p['publishing']['made_for_kids'] is False
        assert p['budget']['daily_usd'] == 9.0
        assert store.get('legacy_prefs_imported', False, d)
        # Re-running start-up does not duplicate anything.
        runtime.reset_for_tests()
        d = runtime.init()
        assert d.scalar('SELECT COUNT(*) AS n FROM videos') == 2
    finally:
        dbmod.reset()


def test_repairs_on_one_shot_are_combined(db):
    vid, mid = _video_with_shots(db)
    qid = _qa(db, vid, [item('fail', 'major', {'action': 're_render_shot', 'shot': 's2',
                                               'params': {'camera_wider': True}}, cid='a'),
                        item('fail', 'major', {'action': 're_render_shot', 'shot': 's2',
                                               'params': {'lines': {'l2': {'mouth_shift_frames': -3}}}}, cid='b')])
    _repair(db, vid, mid, qid)
    sh = next(s for s in repo.shots(mid, db) if s['shot_key'] == 's2')
    assert sh['detail']['repair_params'] == {'camera_wider': True, 'lines': {'l2': {'mouth_shift_frames': -3}}}


def test_lipsync_repair_accumulates_the_measured_lag():
    from blox.pipeline import merge_repair_params
    p = merge_repair_params({}, {'lines': {'l1': {'mouth_shift_frames': -3, 'mouth_gain': 1.3}}})
    merge_repair_params(p, {'lines': {'l1': {'mouth_shift_frames': 1, 'mouth_gain': 1.3}}})
    assert p == {'lines': {'l1': {'mouth_shift_frames': -2, 'mouth_gain': 1.69}}}


def test_settings_repair_that_changes_nothing_is_held_not_rerendered(db):
    vid, mid = _video_with_shots(db)
    repo.upsert_shot(vid, mid, 's2', 'blender', db, repair_attempts=1, detail={'repair_params': {'camera_wider': True}})
    qid = _qa(db, vid, [item('fail', 'major', {'action': 're_render_shot', 'shot': 's2',
                                               'params': {'camera_wider': True}})])
    _repair(db, vid, mid, qid)
    v = videos.get(vid, db)
    assert v['status'] == 'needs_review' and 'cannot change' in v['status_reason']
    assert not db.query("SELECT id FROM tasks WHERE kind='shot.render' AND status='queued'")


def test_plain_retry_after_a_render_glitch_is_still_allowed(db):
    vid, mid = _video_with_shots(db)
    repo.upsert_shot(vid, mid, 's2', 'blender', db, repair_attempts=1)
    qid = _qa(db, vid, [item('fail', 'critical', {'action': 're_render_shot', 'shot': 's2'})])
    _repair(db, vid, mid, qid)
    assert videos.get(vid, db)['status'] == 'generating'
