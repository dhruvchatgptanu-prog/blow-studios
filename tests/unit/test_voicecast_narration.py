"""Storytime narration lines: compiled, validated, voiced by the narrator, never lip-synced, still captioned and
checked against speech recognition."""
import copy
import wave

import numpy as np
import pytest

from blox import demo, prefs, production
from blox.animation import solver as SV
from blox.manifest import compile as C, director, schema as S, validate as V
from blox.qa import motion, story
from blox.qa.report import Checks
from blox.story import generate as G

NARRATION = {'id': 'n1', 'kind': 'narration', 'text': 'I froze.', 't': 6.3, 'emotion': 'worried'}


def storytime(**line):
    p = demo.plan()
    p['narrator'] = 'hero'
    p['lines'].append(dict(NARRATION, **line))
    return p


def compiled(plan, **kw):
    return C.compile_plan(plan, fps=30, width=540, height=960, **kw)


def codes(rep, key='errors'):
    return {e['code'] for e in rep[key]}


def test_narration_compiles_with_the_narrators_voice_and_captions():
    m = compiled(storytime())
    n1 = next(ln for ln in m['lines'] if ln['id'] == 'n1')
    assert m['narrator'] == 'hero' and n1['kind'] == 'narration' and n1['speaker'] == 'hero'
    assert all(ln['kind'] == 'dialogue' for ln in m['lines'] if ln['id'] != 'n1'), 'dialogue is the default'
    assert [c['text'] for c in m['captions'] if c['line_id'] == 'n1'], 'narration is captioned'
    vocal = [v for b in m['beats'] for v in b['vocal'] if v['line'] == 'n1']
    assert vocal and vocal[0]['kind'] == 'narration'
    # Without a plan narrator a speaker-less narration line falls back to the off-screen narrator voice.
    p = storytime()
    del p['narrator']
    assert next(ln for ln in compiled(p)['lines'] if ln['id'] == 'n1')['speaker'] == 'narrator'
    # Old plans are unchanged: no narrator key, every line dialogue.
    old = compiled(demo.plan())
    assert 'narrator' not in old and {ln['kind'] for ln in old['lines']} == {'dialogue'}


def test_narration_validates_and_may_run_over_any_shot():
    m = compiled(storytime())
    rep = V.validate(m, prefs.DEFAULTS)
    assert rep['ok'], rep['errors']
    # n1 plays while the camera frames Pip: fine for voice-over, a warning for on-screen dialogue.
    assert not any('n1' in w['message'] for w in rep['warnings'] if w['code'] == 'offscreen_speech')
    rep_d = V.validate(compiled(storytime(kind='dialogue', speaker='hero')), prefs.DEFAULTS)
    assert any('n1' in w['message'] for w in rep_d['warnings'] if w['code'] == 'offscreen_speech')


@pytest.mark.parametrize('change, code', [
    ({'kind': 'monologue'}, 'enum'),
    ({'speaker': 'pip'}, 'narration_speaker'),
])
def test_bad_narration_lines_are_rejected(change, code):
    rep = V.validate(compiled(storytime(**change)), prefs.DEFAULTS)
    assert code in codes(rep)


def test_narrator_must_be_in_the_cast():
    p = storytime()
    p['narrator'] = 'rook'
    assert 'narrator' in codes(V.validate(compiled(p), prefs.DEFAULTS))


def test_validate_flags_unknown_characters_when_given_the_roster():
    m = compiled(demo.plan())
    roster = {c['id']: c for c in demo.CHARACTERS}
    assert V.validate(m, prefs.DEFAULTS, characters=roster)['ok']
    m['cast'][1]['character_id'] = 'ch_nobody'
    assert 'cast_character' in codes(V.validate(m, prefs.DEFAULTS, characters=roster))


def test_director_script_marks_voice_over():
    m = compiled(storytime())
    text = director.script(m, {'hero': 'Bloxy', 'pip': 'Pip'})
    assert 'Narrator: Bloxy' in text and 'BLOXY (V.O. narration, mouth closed)' in text and 'I froze.' in text


def test_narration_is_not_lip_synced():
    m = compiled(storytime())
    n1 = next(ln for ln in m['lines'] if ln['id'] == 'n1')
    l3 = next(ln for ln in m['lines'] if ln['id'] == 'l3')
    assert S.lip_synced(l3, 'hero') and not S.lip_synced(n1) and not S.lip_synced(n1, 'hero')
    vis, _ = SV.build_visemes(m, 'hero', m['duration_frames'], {})
    assert not any(vis[f] for f in range(n1['start_frame'], n1['est_end_frame'])), 'no visemes for voice-over'
    assert any(vis[f] for f in range(l3['start_frame'], l3['est_end_frame']))


def _wav(path, seconds=1.5, sr=48000):
    t = np.arange(int(seconds * sr)) / sr
    x = (0.4 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)) * 32767).astype(np.int16)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())
    return str(path)


def test_narrators_jaw_ignores_the_narration_audio(tmp_path):
    m = compiled(storytime())
    results = {ln['id']: {'file': _wav(tmp_path / f'{ln["id"]}.wav')} for ln in m['lines']}
    env = production.envelopes(m, results)['hero']
    n1 = next(ln for ln in m['lines'] if ln['id'] == 'n1')
    l3 = next(ln for ln in m['lines'] if ln['id'] == 'l3')
    assert env[n1['start_frame']:n1['est_end_frame']].max() == 0.0
    assert env[l3['start_frame']:l3['start_frame'] + 40].max() > 0.3


def test_solved_narrator_does_not_mouth_the_voice_over(tmp_path):
    """The narration changes nothing on screen: the narrator's face (mouth, speaking flag) is exactly what it is
    without the line, i.e. closed or reacting by expression only, while dialogue still lip-syncs."""
    def solve(plan):
        m = compiled(plan)
        results = {ln['id']: {'file': _wav(tmp_path / f'{ln["id"]}.wav'), 'words': [],
                              'alignment_kind': 'estimated'} for ln in m['lines']}
        bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id'])
                  for c in m['cast']}
        return m, SV.solve(m, bibles, production.alignments(results), envelopes=production.envelopes(m, results))
    m, with_n1 = solve(storytime())
    _, without = solve(dict(storytime(), lines=demo.plan()['lines']))
    n1 = next(ln for ln in m['lines'] if ln['id'] == 'n1')
    l3 = next(ln for ln in m['lines'] if ln['id'] == 'l3')
    hero, ref = with_n1['characters']['hero'], without['characters']['hero']
    for f in range(n1['start_frame'], n1['est_end_frame']):
        assert hero[f]['face']['mouth'] == ref[f]['face']['mouth'] and not hero[f]['face']['speaking']
    assert sum(hero[f]['face']['speaking'] for f in range(l3['start_frame'], l3['est_end_frame'])) > 5


def _scene(mouth, n=60):
    env = np.clip(np.sin(np.linspace(0, 6 * np.pi, n)) ** 2, 0, 1)
    opening = np.concatenate([mouth(env), np.zeros(5)])
    recs = {f: {'mouth_open': float(opening[f]), 'face_dot': 1.0, 'face_height_frac': 0.3,
                'bbox2d': [0.2, 0.2, 0.8, 0.8], 'in_front_of_camera': True, 'mouth2d': [0.5, 0.5, 1.0],
                'mouth_width_px': 40.0, 'palm2d_r': [0.5, 0.95, 0.9]} for f in range(n + 5)}
    m = {'fps': 30, 'width': 108, 'height': 192, 'shots': [{'id': 's1', 'start_frame': 0, 'end_frame': n + 5}],
         'lines': [{'id': 'n1', 'speaker': 'hero', 'kind': 'narration', 'start_frame': 0, 'est_end_frame': n}]}
    ck = Checks(30)
    motion._lipsync(ck, m, 'hero', recs, None, {'n1': env})
    return {c['id']: c for c in ck.items}


def test_qa_skips_lip_sync_for_narration_and_checks_the_mouth_stays_out_of_it():
    closed = _scene(lambda env: np.zeros(len(env)))
    assert closed['lipsync:n1']['status'] == 'skipped' and 'voice-over' in closed['lipsync:n1']['evidence']['reason']
    assert closed['voiceover:n1']['status'] == 'pass'
    mouthing = _scene(lambda env: env * 0.8)
    assert mouthing['voiceover:n1']['status'] == 'fail', 'a narrator mouthing the voice-over is caught'


def test_asr_check_includes_narration(tmp_path):
    m = compiled(storytime())
    dialog = _wav(tmp_path / 'dialog.wav', seconds=m['duration_frames'] / 30)
    placements = [{'line': ln['id'], 'start_s': ln['start_frame'] / 30, 'end_s': ln['start_frame'] / 30 + 1.0}
                  for ln in m['lines']]
    line_audio = {ln['id']: {'provider': 'piper', 'words': [], 'alignment_kind': 'estimated',
                             'asr_text': ln['text'] if ln['id'] != 'n1' else 'something else entirely'}
                  for ln in m['lines']}
    asr_text = ' '.join(ln['text'] for ln in m['lines'])
    ck = Checks(30)
    story.run(ck, m, line_audio, {'mix': {'paths': {'dialog': dialog}, 'placements': placements}, 'captions': []},
              copy.deepcopy(prefs.DEFAULTS), asr=(asr_text, []))
    c = next(i for i in ck.items if i['id'] == 'dialogue_matches_script')
    assert c['status'] == 'fail' and [x['line'] for x in c['evidence']['per_line_mismatches']] == ['n1']


def test_llm_plan_schema_and_rules_know_narration():
    sch = G.plan_schema(['hero', 'pip'], ['ch_bloxy', 'ch_pip'])
    assert sch['properties']['narrator']['enum'] == ['hero', 'pip', None]
    assert sch['properties']['lines']['items']['properties']['kind']['enum'] == S.LINE_KINDS
    assert {ln['kind'] for ln in G.EXAMPLE['lines']} == set(S.LINE_KINDS)
    assert any('narration' in r and 'voice-over' in r for r in G.plan_rules(prefs.DEFAULTS))
    raw = dict(storytime(), narrator=None, performance=[], actions=[])
    assert 'narrator' not in G.to_plan(raw)
