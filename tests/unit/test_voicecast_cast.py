"""The new recurring cast (Rook, Dot, Ms. Tally): forward migration, and resolution everywhere a cast is used."""
import copy
import json
import re
import wave

import numpy as np
import pytest

from blox import demo, migrations, pipeline, prefs, production, repo
from blox.animation import solver as SV
from blox.manifest import compile as C, schema as S, validate as V
from blox.migrations import m0005_new_cast
from blox.story import backlog, generate as G
from blox.voice import piper

NEW = ('ch_rook', 'ch_dot', 'ch_tally')


def test_bibles_use_the_shared_vocabulary():
    chars = {c['id']: c for c in demo.CHARACTERS}
    assert set(NEW) == set(demo.NEW_CAST_IDS) <= set(chars)
    want = {'ch_rook': ('Rook', 1.05), 'ch_dot': ('Dot', 0.72), 'ch_tally': ('Ms. Tally', 1.12)}
    for cid, (name, scale) in want.items():
        c = chars[cid]
        assert c['name'] == name and c['bible']['scale'] == scale
        assert c['bible']['summary'] and c['bible']['personality'] and c['bible']['visual_rules']
        for k, v in c['bible']['costume'].items():
            assert k in S.COSTUME and v in S.COSTUME[k], (cid, k, v)
        for v in c['bible'].get('costume_variants', {}).values():
            assert all(k in S.COSTUME and x in S.COSTUME[k] for k, x in v.items())
        assert all(re.fullmatch(r'#[0-9A-F]{6}', x) for x in c['bible']['palette'].values())
        assert c['voice']['piper_speaker'] == piper.DEFAULT_SPEAKERS[cid]
    assert chars['ch_rook']['bible']['palette']['top2'] == '#1B1B2F'
    assert chars['ch_tally']['bible']['palette']['hair'] == '#9AA0A6'
    # Distinct voices: five different speakers, pitch shifts as cast.
    assert len({c['voice']['piper_speaker'] for c in demo.CHARACTERS}) == len(demo.CHARACTERS)
    assert [chars[c]['voice']['pitch_semitones'] for c in NEW] == [2, 4, -2]


def _row(db, cid):
    r = db.one('SELECT * FROM characters WHERE id=?', (cid,))
    return r and dict(r, bible=json.loads(r['bible']), voice=json.loads(r['voice']))


def test_migration_adds_only_missing_characters_and_is_idempotent(db):
    assert {c for c in NEW if _row(db, c)} == set(NEW), 'a fresh install has the whole cast'
    # An install from before this release: no Rook or Dot; the owner already made their own "ch_tally";
    # Bloxy was edited on the Characters page.
    db.execute('DELETE FROM characters WHERE id IN (?, ?)', ('ch_rook', 'ch_dot'))
    db.execute('UPDATE characters SET name=?, voice=? WHERE id=?', ('Teach', json.dumps({'piper_speaker': 7}),
                                                                    'ch_tally'))
    db.execute('UPDATE characters SET voice=? WHERE id=?', (json.dumps({'piper_speaker': 60}), 'ch_bloxy'))
    db.execute('DELETE FROM schema_migrations WHERE version=?', ('0005_new_cast',))
    assert migrations.pending(db) == ['0005_new_cast']
    assert migrations.migrate(db) == ['0005_new_cast']
    rook = _row(db, 'ch_rook')
    assert rook['name'] == 'Rook' and rook['active'] == 1 and rook['voice']['pitch_semitones'] == 2
    assert _row(db, 'ch_dot')['bible']['costume']['hair'] == 'pigtails'
    assert _row(db, 'ch_tally')['name'] == 'Teach' and _row(db, 'ch_tally')['voice'] == {'piper_speaker': 7}
    assert _row(db, 'ch_bloxy')['voice'] == {'piper_speaker': 60}, 'existing characters are never overwritten'
    before = db.query('SELECT id, name, bible, voice, updated_at FROM characters ORDER BY id')
    m0005_new_cast.up(db)
    assert migrations.migrate(db) == []
    assert db.query('SELECT id, name, bible, voice, updated_at FROM characters ORDER BY id') == before


def storytime_cast():
    p = demo.plan()
    p['cast'] = [{'id': 'rook', 'character_id': 'ch_rook'}, {'id': 'dot', 'character_id': 'ch_dot'}]
    blob = json.dumps(p).replace('"hero"', '"rook"').replace('"pip"', '"dot"')
    p = json.loads(blob)
    p['narrator'] = 'rook'
    p['lines'].append({'id': 'n1', 'kind': 'narration', 'text': 'I froze.', 't': 6.3})
    return p


def test_new_cast_resolves_in_compile_validate_solve_and_pipeline(db):
    p = copy.deepcopy(prefs.get(db))
    m = C.compile_plan(storytime_cast(), fps=30, width=540, height=960)
    chars = repo.characters(active_only=False, d=db)
    rep = V.validate(m, p, characters=chars)
    assert rep['ok'], rep['errors']
    cast = pipeline._cast(m, db)
    assert cast['rook']['name'] == 'Rook' and cast['dot']['name'] == 'Dot'
    assert cast['dot']['voice']['pitch_semitones'] == 4 and cast['dot']['bible']['scale'] == 0.72
    assert production.cast_characters(m, chars) == cast
    solved = SV.solve(m, {cid: c['bible'] for cid, c in cast.items()})
    assert solved['scales'] == {'rook': 1.05, 'dot': 0.72}


def test_backlog_accepts_the_new_cast_and_rejects_unknown_characters(db):
    p = copy.deepcopy(prefs.get(db))
    ok = storytime_cast()
    ghost = storytime_cast()
    ghost['title'] = 'Ghost cast'
    ghost['cast'][1]['character_id'] = 'ch_ghost'
    res = backlog.add([ok, ghost], 'test', p, db)
    assert res[0]['status'] == 'ready', res[0]
    assert res[1]['status'] == 'rejected' and 'ch_ghost' in res[1]['reason']


def test_story_generation_offers_the_new_cast(db):
    chars = repo.characters(d=db)
    assert set(NEW) <= set(chars)
    sch = G.concept_schema(sorted(chars))
    assert set(NEW) <= set(sch['properties']['concepts']['items']['properties']['cast']['items']['enum'])
    brief = {b['character_id']: b for b in G.character_brief(chars)}
    assert brief['ch_tally']['name'] == 'Ms. Tally' and 'referee' in brief['ch_tally']['summary']


def _fake_piper(tmp_path, monkeypatch):
    d = tmp_path / 'voices'
    monkeypatch.setenv('BLOX_VOICES_DIR', str(d))
    mdir = d / piper.DEFAULT_MODEL
    mdir.mkdir(parents=True)
    (mdir / f'{piper.DEFAULT_MODEL}.onnx').write_bytes(b'onnx')
    (mdir / f'{piper.DEFAULT_MODEL}.onnx.json').write_text('{}')
    calls = []
    real_run = piper.media.run

    def run(args, timeout=None, **kw):
        if args[1:3] == ['-m', 'piper']:
            calls.append(args)
            t = np.arange(int(1.2 * 22050)) / 22050
            x = 0.3 * np.sin(2 * np.pi * 150 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
            with wave.open(args[args.index('-f') + 1], 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes((x * 32767).astype(np.int16).tobytes())
            return b'', b''
        return real_run(args, timeout=timeout, **kw)
    monkeypatch.setattr(piper.media, 'run', run)
    return calls


@pytest.mark.parametrize('cid', NEW)
def test_voice_preview_works_for_the_new_cast(client, tmp_path, monkeypatch, cid):
    calls = _fake_piper(tmp_path, monkeypatch)
    r = client.post(f'/api/characters/{cid}/voice-preview', json={})
    j = r.get_json()
    assert r.status_code == 200, j
    assert j['speaker'] == piper.DEFAULT_SPEAKERS[cid] and j['preview'].startswith('/media/work/character_previews/')
    assert j['pitch_semitones'] == {'ch_rook': 2, 'ch_dot': 4, 'ch_tally': -2}[cid]
    assert client.get(j['preview']).status_code == 200
    assert calls[-1][calls[-1].index('-s') + 1] == str(piper.DEFAULT_SPEAKERS[cid])


def test_characters_page_lists_and_saves_the_new_cast(client):
    j = client.get('/api/characters').get_json()
    assert set(NEW) <= set(j['characters'])
    assert 'sunglasses' in j['vocab']['eyewear'] and 'cardigan' in j['vocab']['tops'] and 'top2' in j['vocab']['palette_slots']
    rook = j['characters']['ch_rook']
    body = {'name': rook['name'], 'active': True, 'bible': rook['bible'], 'voice': dict(rook['voice'], pitch_semitones=1.5)}
    assert client.put('/api/characters/ch_rook', json=body).status_code == 200
    saved = client.get('/api/characters').get_json()['characters']['ch_rook']
    assert saved['bible']['costume'] == rook['bible']['costume'] and saved['bible']['costume_variants'] == {
        'brag': {'hat': 'crown'}}
    assert saved['voice']['pitch_semitones'] == 1.5 and saved['voice']['piper_noise_w'] == 0.92
    body['voice']['pitch_semitones'] = 9
    assert client.put('/api/characters/ch_rook', json=body).status_code == 400
