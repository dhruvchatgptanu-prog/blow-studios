"""Story backlog: autopilot production without an LLM API, plus the own-history originality fix."""
import copy

import pytest

from blox import demo, orchestrator, pipeline, prefs, repo, videos
from blox.story import backlog
from blox.util import Blocked

from .helpers import ready_to_publish


def story(n=0, **over):
    p = demo.plan()
    p['title'] = f'Test story {n}'
    for i, ln in enumerate(p['lines']):
        ln['text'] = ln['text'].rstrip('.!?') + f' {"take " * (n % 3)}number {n} line {i}.'.replace('  ', ' ')
    p.update(over)
    return p


def short_lines_story(n):
    p = demo.plan()
    p['title'] = f'Variant {n}'
    p['metadata'] = dict(p.get('metadata') or {}, title=f'Variant {n}')
    p['lines'] = [dict(ln, text=['Whoa.', 'Hey!', 'Hmm.', 'Okay.', 'Look!', 'Oh no.', 'Ha!'][i] + f' {n}')
                  for i, ln in enumerate(p['lines'])]
    return p


def test_import_validates_and_dedupes(db):
    p = prefs.get(db)
    bad = demo.plan()
    bad['title'] = 'Broken'
    bad['actions'].append({'character': 'hero', 'type': 'backflip', 't': 3, 'main_frames': 10})
    res = backlog.add([short_lines_story(1), short_lines_story(1), bad, 'nope'], 'claude', p, db)
    assert [r['status'] for r in res] == ['ready', 'duplicate', 'rejected', 'rejected']
    assert 'backflip' in res[2]['reason']
    s = backlog.summary(p, db)
    assert s['ready'] == 1 and s['per_day'] == 12 and s['days_left'] == pytest.approx(0.1)


def test_take_is_atomic_and_fifo(db, clock):
    p = prefs.get(db)
    backlog.add([short_lines_story(1)], 'claude', p, db)
    clock.advance(1)
    backlog.add([short_lines_story(2)], 'claude', p, db)
    a = backlog.take('v1', db)
    b = backlog.take('v2', db)
    assert a['title'] == 'Variant 1' and b['title'] == 'Variant 2'
    assert backlog.take('v3', db) is None
    assert backlog.release_for_video('v1', db) == 1 and backlog.take('v4', db)['title'] == 'Variant 1'


def _develop(db, vid, steps=4):
    from blox.tasks import ensure_loaded
    ensure_loaded()
    for _ in range(steps):
        v = videos.get(vid, db)
        if v['status'] in ('scripted', 'storyboarded') or v['status'] in videos.HOLDS:
            break
        pipeline.develop_step(v, prefs.get(db), db, repo.characters(d=db))
    return videos.get(vid, db)


def test_autopilot_uses_backlog_without_llm(db):
    p = prefs.get(db)
    assert pipeline.story_source(p) == 'backlog'  # no OpenAI key connected
    backlog.add([short_lines_story(7)], 'claude', p, db)
    vid = videos.create('Untitled (researching)', 'autopilot', d=db)
    v = _develop(db, vid)
    assert v['status'] == 'scripted' and v['title'] == 'Variant 7'
    assert repo.manifest(v['manifest_id'], db)['source'] == 'backlog'
    assert v['metadata']['originality_script']['decision'] == 'pass'
    assert backlog.summary(p, db)['used'] == 1


def test_empty_backlog_blocks_with_clear_reason(db):
    vid = videos.create('Untitled (researching)', 'autopilot', d=db)
    with pytest.raises(Blocked, match='backlog is empty'):
        _develop(db, vid)


def test_second_copy_of_a_story_is_held_as_too_similar(db):
    p = prefs.get(db)
    first = short_lines_story(3)
    backlog.add([first], 'claude', p, db)
    v1 = videos.create('a', 'autopilot', d=db)
    assert _develop(db, v1)['status'] == 'scripted'
    copy_ = copy.deepcopy(first)
    copy_['title'] = 'Same story, new title'
    backlog.add([copy_], 'claude', p, db)
    v2 = videos.create('b', 'autopilot', d=db)
    with pytest.raises(Blocked, match='too close'):
        _develop(db, v2)


def test_buffer_stops_when_backlog_runs_out(db, clock):
    ready_to_publish(db)
    p = prefs.get(db)
    p['schedule'].update(buffer_target=3, max_in_production=3)
    prefs.put(p, db)
    backlog.add([short_lines_story(1)], 'claude', p, db)
    assert orchestrator.maintain_buffer(db, prefs.get(db), clock()) == 1
    assert orchestrator.maintain_buffer(db, prefs.get(db), clock()) == 0


def test_cancel_returns_story_to_backlog(client, db):
    backlog.add([short_lines_story(5)], 'claude', prefs.get(db), db)
    vid = videos.create('x', 'autopilot', d=db)
    _develop(db, vid)
    assert backlog.summary(prefs.get(db), db)['ready'] == 0
    assert client.post(f'/api/videos/{vid}/cancel', json={}).status_code == 200
    assert backlog.summary(prefs.get(db), db)['ready'] == 1


def test_backlog_api_and_readiness(client, db):
    r = client.post('/api/backlog', json={'plans': [short_lines_story(9)], 'source': 'owner'}).get_json()
    assert r['results'][0]['status'] == 'ready' and r['summary']['ready'] == 1
    sid = r['results'][0]['id']
    st = client.get('/api/state').get_json()['readiness']
    assert st['stories']['ok'] and st['openai']['optional']
    assert client.post(f'/api/backlog/{sid}/reject', json={'note': 'not funny'}).status_code == 200
    assert client.get('/api/backlog').get_json()['summary']['ready'] == 0
    assert client.get('/api/state').get_json()['readiness']['stories']['ok'] is False


def test_llm_script_is_not_compared_with_its_own_concept(db, monkeypatch):
    """Regression: own-history used to include the current video, so every LLM script failed originality."""
    from blox import llm
    from blox.story import generate as G
    monkeypatch.setattr(llm, 'available', lambda: True)
    plan = short_lines_story(11)
    concept = {'title': 'Concept 11', 'premise': plan['story']['premise'], 'hook': plan['hook']['text'],
               'ending': plan['story']['ending'], 'reveal': 'door', 'hook_type': 'danger', 'ending_type': 'twist',
               'setting_preset': 'sky_obby', 'cast': ['ch_bloxy', 'ch_pip'], 'pattern_names_used': [],
               'inspiration': []}
    monkeypatch.setattr(G, 'extract_patterns', lambda *a, **k: {'patterns': [], 'caveats': []})
    monkeypatch.setattr(G, 'generate_concepts', lambda *a, **k: [concept])

    def plan_with_validation(key, c, p, chars, cast_map, vid):
        from blox.manifest import compile as C
        from blox.manifest.validate import validate
        m = C.compile_plan(plan, fps=30, width=1080, height=1920)
        return plan, m, validate(m, p)
    monkeypatch.setattr(G, 'plan_with_validation', plan_with_validation)
    monkeypatch.setattr(pipeline, '_embedder', lambda vid: None)
    vid = videos.create('Untitled', 'autopilot', d=db)
    v = _develop(db, vid)
    assert v['status'] == 'scripted', v['status_reason']
    checks = {c['check']: c['status'] for c in v['metadata']['originality_script']['checks']}
    assert checks.get('repeated_premise', 'pass') == 'pass'
