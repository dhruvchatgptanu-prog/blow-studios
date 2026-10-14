"""Production pace: settings, and every production compile uses the owner's pace and speech rate."""
import copy
from pathlib import Path

import pytest

from blox import demo, pipeline, prefs, production, repo, videos
from blox.manifest import compile as C
from blox.story import backlog, generate as G

ROOT = Path(__file__).resolve().parents[2]


def test_defaults_and_validation():
    pr = prefs.DEFAULTS['production']
    assert pr['pace'] == 1.5 and pr['speech_rate'] == 1.3
    # Default lengths are finished-video seconds at the default pace (30 s of story -> 20 s).
    assert pr['min_seconds'] * pr['pace'] <= 30 <= pr['max_seconds'] * pr['pace']
    for key, bad in (('pace', 0.99), ('pace', 2.01), ('pace', 'x'), ('speech_rate', 0.79), ('speech_rate', 1.61)):
        p = copy.deepcopy(prefs.DEFAULTS)
        p['production'][key] = bad
        with pytest.raises(ValueError):
            prefs.validate(p)
    p = copy.deepcopy(prefs.DEFAULTS)
    p['production'].update(pace=2, speech_rate=0.8)
    assert prefs.validate(p)['production']['pace'] == 2.0


def _store_pre_pace(db, **lengths):
    import json
    old = copy.deepcopy(prefs.DEFAULTS)
    for k in ('pace', 'speech_rate'):
        old['production'].pop(k)
    old['production'].update(lengths)
    db.execute('INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (prefs.KEY, json.dumps(old)))


def test_settings_saved_before_pace_follow_the_new_length_defaults(db):
    _store_pre_pace(db, min_seconds=30, target_seconds=42)
    pr = prefs.get(db)['production']
    assert (pr['pace'], pr['min_seconds'], pr['target_seconds']) == (1.5, 20, 28)
    prefs.put(prefs.get(db), db)
    # Lengths the owner chose are kept (and the validator explains the pace if a story no longer fits).
    _store_pre_pace(db, min_seconds=25, target_seconds=42)
    pr = prefs.get(db)['production']
    assert (pr['min_seconds'], pr['target_seconds']) == (25, 42)
    # Once pace is stored, lengths are never touched again.
    p = prefs.get(db)
    p['production'].update(min_seconds=30, target_seconds=42)
    prefs.put(p, db)
    assert prefs.get(db)['production']['min_seconds'] == 30


def test_settings_page_offers_pace_and_explains_it():
    js = (ROOT / 'static' / 'app.js').read_text()
    assert "inp('production', 'pace'" in js and "inp('production', 'speech_rate'" in js
    assert 'never above 1.5' in js and 'story time' in js


def test_owner_pace_reaches_backlog_import_and_production(db):
    from blox.tasks import ensure_loaded
    ensure_loaded()
    p = prefs.get(db)
    plan = demo.plan()
    res = backlog.add([plan], 'owner', p, db)
    assert res[0]['status'] == 'ready'
    assert backlog.get(res[0]['id'], db)['validation']['duration_s'] == 20.0
    vid = videos.create('Untitled', 'autopilot', d=db)
    for _ in range(4):
        v = videos.get(vid, db)
        if v['status'] == 'scripted' or v['status'] in videos.HOLDS:
            break
        pipeline.develop_step(v, prefs.get(db), db, repo.characters(d=db))
    v = videos.get(vid, db)
    assert v['status'] == 'scripted', v['status_reason']
    m = repo.manifest(v['manifest_id'], db)['body']
    assert m['pace'] == {'timeline': 1.5, 'speech_rate': 1.3} and m['duration_frames'] == 20 * m['fps']
    assert 'Pace: the story plays 1.5x faster' in repo.manifest(v['manifest_id'], db)['director_script']


def test_backlog_import_validates_at_the_configured_pace(db):
    p = prefs.get(db)
    p['production'].update(pace=2.0, speech_rate=1.6)
    prefs.put(p, db)
    res = backlog.add([demo.plan()], 'owner', prefs.get(db), db)
    assert res[0]['status'] == 'rejected' and 'pace 2' in res[0]['reason']


def test_editor_endpoints_and_demo_use_the_owner_pace(client, db):
    plan = demo.plan()
    r = client.post('/api/validate-plan', json={'plan': plan}).get_json()
    assert r['validation']['ok'] and r['validation']['stats']['duration_s'] == 20.0
    vid = client.post('/api/videos', json={'kind': 'demo'}).get_json()['id']
    v = videos.get(vid, db)
    assert repo.manifest(v['manifest_id'], db)['body']['pace']['timeline'] == 1.5
    r = client.put(f'/api/videos/{vid}/plan', json={'plan': plan}).get_json()
    assert repo.manifest(r['manifest_id'], db)['body']['pace'] == {'timeline': 1.5, 'speech_rate': 1.3}
    # The Settings page saves through /api/settings; out-of-range values are refused.
    assert client.post('/api/settings', json={'production': {'pace': 2.5}}).status_code == 400
    r = client.post('/api/settings', json={'production': {'pace': 1.0, 'speech_rate': 1.0, 'min_seconds': 30,
                                                          'target_seconds': 42}})
    assert r.status_code == 200 and r.get_json()['prefs']['production']['pace'] == 1.0
    r = client.post('/api/validate-plan', json={'plan': plan}).get_json()
    assert r['validation']['stats']['duration_s'] == 30.0


def test_local_production_and_llm_plans_compile_at_the_pace(monkeypatch):
    seen = []

    def spy(plan, **kw):
        seen.append(kw)
        raise ValueError('stop here')
    monkeypatch.setattr(C, 'compile_plan', spy)
    p = copy.deepcopy(prefs.DEFAULTS)
    with pytest.raises(ValueError, match='stop here'):
        production.produce_local(demo.plan(), {}, p, '/nonexistent')
    monkeypatch.setattr(G, 'compile_plan', spy)
    monkeypatch.setattr(G, 'generate_plan', lambda *a, **k: {'performance': [], 'actions': []})
    with pytest.raises(Exception):
        G.plan_with_validation('k', {}, p, {}, {}, max_fixes=0)
    assert [(k['pace'], k['speech_rate']) for k in seen] == [(1.5, 1.3), (1.5, 1.3)]


def test_llm_is_asked_for_story_time():
    rules = ' '.join(G.plan_rules(copy.deepcopy(prefs.DEFAULTS)))
    assert 'Duration between 30 and 90 seconds (target 42)' in rules
    assert 'plays the plan 1.5x faster' in rules
    p = copy.deepcopy(prefs.DEFAULTS)
    p['production'].update(pace=1.0)
    assert 'Duration between 20 and 60 seconds (target 28)' in ' '.join(G.plan_rules(p))
