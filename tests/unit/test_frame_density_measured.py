"""Measured dialogue density: per-character measured speaking rates drive the line estimate, the validator
accepts the real density of the free voices, and lines can be voiced at validation time for exact timing."""
import copy
import json
import os
from pathlib import Path

import pytest

from blox import demo, migrations, prefs, production, repo
from blox.manifest import compile as C, schema as S
from blox.manifest.validate import validate
from blox.voice import piper

ROOT = Path(__file__).resolve().parents[2]
PLANS = json.loads((ROOT / 'stories' / 'batch-2026-10-11-mainstream.json').read_text())
ROWS = {c['id']: {'name': c['name'], 'bible': c['bible'], 'voice': c['voice']} for c in demo.CHARACTERS}
RATE = prefs.DEFAULTS['production']['speech_rate']


def line(text, speaker='bloxy', **over):
    out = {'id': 'l1', 'speaker': speaker, 'kind': 'dialogue', 'text': text, 'emotion': 'neutral', 'pace': 'normal',
           'volume': 'normal', 'speech_rate': RATE}
    out.update(over)
    return out


def build(plan, p=None, **kw):
    p = p or prefs.DEFAULTS
    pr = p['production']
    return C.compile_plan(copy.deepcopy(plan), fps=pr['fps'], width=pr['width'], height=pr['height'],
                          **C.pace_kwargs(p), **kw)


# ----------------------------------------------------------------------------- the measured voice config
def test_every_built_in_voice_carries_a_measurement_at_the_production_rate():
    for c in demo.CHARACTERS:
        mv = C.measured_voice(c['voice'])
        assert mv is not None, c['id']
        wps, overhead, rate = mv
        assert 4.0 <= wps <= 6.0 and 0.0 < overhead <= 0.3 and rate == RATE, c['id']
        # Far faster than the planning default (2.7 words/s) the engine was assumed to speak at.
        assert wps / rate > S.PACE_WPS['normal'] * 1.2


@pytest.mark.parametrize('voice', [None, {}, {'measured_wps': 5.0}, {'measured_wps': 'x', 'line_overhead_s': 0.1, 'measured_rate': 1.3},
                                   {'measured_wps': 30, 'line_overhead_s': 0.1, 'measured_rate': 1.3},
                                   {'measured_wps': 5, 'line_overhead_s': -1, 'measured_rate': 1.3},
                                   {'measured_wps': 5, 'line_overhead_s': 0.1, 'measured_rate': 9}, 'nope'])
def test_incomplete_or_absurd_measurements_fall_back_to_the_default(voice):
    assert C.measured_voice(voice) is None
    assert C.estimate_line_frames(line('Hello there my friend.'), 30, voice=voice) == \
        C.estimate_line_frames(line('Hello there my friend.'), 30)


# ----------------------------------------------------------------------------- the estimate
def test_measured_estimate_is_the_fit_plus_a_small_margin():
    voice = {'measured_wps': 5.0, 'line_overhead_s': 0.1, 'measured_rate': 1.3}
    text = 'one two three four five six seven eight nine ten'
    fit = 0.1 + 10 / 5.0
    want = fit * (1 + S.MEASURED_MARGIN) + S.MEASURED_MARGIN_S
    assert C.estimate_line_frames(line(text), 30, voice=voice) == -(-want * 30 // 1)
    assert 0 < S.MEASURED_MARGIN <= 0.1 and 0 < S.MEASURED_MARGIN_S <= 0.15, 'small but never zero'
    # Well under the conservative default, and never so short that the fit itself would not fit.
    assert C.estimate_line_frames(line(text), 30, voice=voice) < 0.85 * C.estimate_line_frames(line(text), 30)
    assert C.estimate_line_frames(line(text), 30, voice=voice) > fit * 30


def test_measured_estimate_scales_with_speech_rate_pace_emotion_and_narration():
    voice = {'measured_wps': 5.0, 'line_overhead_s': 0.1, 'measured_rate': 1.3, 'narration_length_scale': 1.1}
    text = ' '.join(['word'] * 20)
    base = C.estimate_line_frames(line(text), 300, voice=voice)
    slower = C.estimate_line_frames(line(text, speech_rate=1.0), 300, voice=voice)
    assert slower == pytest.approx(((base - S.MEASURED_MARGIN_S * 300) * 1.3 + S.MEASURED_MARGIN_S * 300), abs=2)
    assert C.estimate_line_frames(line(text, pace='slow'), 300, voice=voice) > base > \
        C.estimate_line_frames(line(text, pace='fast'), 300, voice=voice)
    assert C.estimate_line_frames(line(text, emotion='sad'), 300, voice=voice) > base
    assert C.estimate_line_frames(line(text, emotion='excited'), 300, voice=voice) == base
    assert C.estimate_line_frames(line(text, kind='narration'), 300, voice=voice) > base
    # The multipliers are the engine's own.
    assert piper.PACE is S.DELIVERY_PACE_SCALE and piper.SUBDUED is S.SUBDUED_EMOTIONS


def test_compile_uses_the_speakers_measured_voice_and_keeps_the_default_for_strangers():
    plan = copy.deepcopy(PLANS[0])
    m_default = build(plan)
    m_rows = build(plan, characters=ROWS)
    voices = C.cast_voices(plan, ROWS)
    m_voices = build(plan, voices=voices)
    assert all(ln['estimate'] == 'default' for ln in m_default['lines'])
    assert all(ln['estimate'] == 'measured_voice' for ln in m_rows['lines'])
    assert [ln['est_frames'] for ln in m_rows['lines']] == [ln['est_frames'] for ln in m_voices['lines']]
    assert sum(ln['est_frames'] for ln in m_rows['lines']) < 0.8 * sum(ln['est_frames'] for ln in m_default['lines'])
    # A cast member whose character has no measurement keeps the conservative estimate.
    strangers = {cid: dict(r, voice={k: v for k, v in r['voice'].items() if k not in S.MEASURED_VOICE_KEYS})
                 for cid, r in ROWS.items()}
    m_strange = build(plan, characters=strangers)
    assert [ln['est_frames'] for ln in m_strange['lines']] == [ln['est_frames'] for ln in m_default['lines']]
    assert all(ln['estimate'] == 'default' for ln in m_strange['lines'])
    # Explicit voices win over the rows.
    m_mixed = build(plan, characters=ROWS, voices={m_rows['lines'][0]['speaker']: {}})
    assert m_mixed['lines'][0]['estimate'] == 'default'


@pytest.mark.parametrize('title', [p['title'] for p in PLANS])
def test_all_batch_plans_validate_with_the_measured_voices(title):
    plan = next(p for p in PLANS if p['title'] == title)
    m = build(plan, characters=ROWS)
    rep = validate(m, prefs.DEFAULTS, characters=ROWS)
    assert not rep['errors'], rep['errors'][:3]
    assert all(ln['estimate'] == 'measured_voice' for ln in m['lines'])
    # The density the validator now accepts for this cast (words that fit every line window, per finished
    # second): 4.3+ words/s, the register of the references, against about 4 with the default estimate.
    allow = C.line_fit_allowance(m)
    assert allow <= S.MAX_LINE_SPEEDUP / RATE + 1e-9
    voices = C.cast_voices(plan, ROWS)

    def capacity(voice_of):
        total = 0
        for ln in m['lines']:
            n = 0
            while C.estimate_line_frames(dict(ln, text=' '.join(['word'] * (n + 1))), m['fps'],
                                         voice=voice_of(ln)) <= (ln['window_end_frame'] - ln['start_frame']) * allow:
                n += 1
            total += n
        return total / (m['duration_frames'] / m['fps'])
    measured, default = capacity(lambda ln: voices[ln['speaker']]), capacity(lambda ln: None)
    assert measured >= 4.3 and measured >= 1.08 * default, (measured, default)


def test_measured_estimates_stay_inside_the_total_speedup_cap():
    """A line the validator passes without a tempo warning never needs more than the 1.5x total to be voiced."""
    for plan in PLANS[:3]:
        m = build(plan, characters=ROWS)
        rep = validate(m, prefs.DEFAULTS, characters=ROWS)
        for ln in m['lines']:
            avail = ln['window_end_frame'] - ln['start_frame']
            if ln['est_frames'] <= avail:
                continue
            assert ln['est_frames'] <= avail * C.line_fit_allowance(m)
            assert any(w['code'] == 'dialogue_tempo' for w in rep['warnings'])


# ----------------------------------------------------------------------------- validation-time measurement
def _fake_synth(monkeypatch, tmp_path, seconds_per_word=0.2, calls=None):
    calls = calls if calls is not None else []

    def synthesize_line(ln, who, p, work_dir, video_id, attempt=0, use_asr=None):
        calls.append(ln['id'])
        os.makedirs(work_dir, exist_ok=True)
        f = os.path.join(work_dir, f'{ln["id"]}.wav')
        Path(f).write_bytes(b'RIFF')
        raw = f + '.raw'
        Path(raw).write_bytes(b'RIFF')
        return {'file': f, 'raw': raw, 'duration_s': 0.1 + seconds_per_word * len(C.words(ln['text']))}
    monkeypatch.setattr(production.tts, 'synthesize_line', synthesize_line)
    return calls


def test_measure_lines_sets_exact_lengths_and_caches_them(monkeypatch, tmp_path):
    calls = _fake_synth(monkeypatch, tmp_path)
    m = build(PLANS[0], characters=ROWS)
    before = [ln['est_frames'] for ln in m['lines']]
    ids = production.measure_lines(m, ROWS, prefs.DEFAULTS, work_dir=str(tmp_path / 'measure'))
    assert ids == sorted(ln['id'] for ln in m['lines']) and len(calls) == len(m['lines'])
    for ln, est in zip(m['lines'], before):
        assert ln['measured_frames'] == -(-(0.1 + 0.2 * len(C.words(ln['text']))) * m['fps'] // 1)
        assert ln['est_end_frame'] == ln['start_frame'] + ln['measured_frames'] and ln['measured_preflight'] is True
        assert ln['est_frames'] == est, 'the estimate stays for reference'
    assert not list((tmp_path / 'measure').glob('*.wav')), 'only the durations are kept'
    m2 = build(PLANS[0], characters=ROWS)
    production.measure_lines(m2, ROWS, prefs.DEFAULTS, work_dir=str(tmp_path / 'measure'))
    assert len(calls) == len(m['lines']), 'the second validation of the same story synthesises nothing'
    assert [ln['measured_frames'] for ln in m2['lines']] == [ln['measured_frames'] for ln in m['lines']]
    # The voiced line later replaces the preflight measurement.
    C.update_line_timing(m2, {m2['lines'][0]['id']: {'duration_s': 9.0, 'words': None}})
    assert 'measured_preflight' not in m2['lines'][0] and m2['lines'][0]['measured_frames'] == 9 * m2['fps']


def test_preflight_is_off_by_default_and_never_uses_a_paid_voice(monkeypatch, tmp_path):
    calls = _fake_synth(monkeypatch, tmp_path)
    assert prefs.DEFAULTS['production']['measure_lines_on_validate'] is False
    p = prefs.validate(copy.deepcopy(prefs.DEFAULTS))
    assert p['production']['measure_lines_on_validate'] is False
    m = build(PLANS[1], characters=ROWS)
    production.preflight(m, ROWS, p)
    assert not calls and not any('measured_frames' in ln for ln in m['lines'])
    paid = copy.deepcopy(p)
    paid['production'].update(measure_lines_on_validate=True, tts_provider='openai')
    production.preflight(m, ROWS, paid)
    assert not calls and not any('measured_frames' in ln for ln in m['lines'])
    bad = copy.deepcopy(p)
    bad['production']['measure_lines_on_validate'] = 'yes'
    with pytest.raises(ValueError):
        prefs.validate(bad)


def test_preflight_measures_with_the_free_voice_and_survives_a_missing_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(production.config, 'DATA_DIR', tmp_path)
    p = copy.deepcopy(prefs.DEFAULTS)
    p['production']['measure_lines_on_validate'] = True
    m = build(PLANS[2], characters=ROWS)

    def broken(*a, **k):
        raise production.Blocked('Piper is not installed', state='needs_review')
    monkeypatch.setattr(production.tts, 'synthesize_line', broken)
    production.preflight(m, ROWS, p)
    assert not any('measured_frames' in ln for ln in m['lines'])
    assert any('not measured' in n for n in m['notes'])
    calls = _fake_synth(monkeypatch, tmp_path, seconds_per_word=0.05)
    production.preflight(m, ROWS, p)
    assert len(calls) == len(m['lines']) and all(ln['measured_preflight'] for ln in m['lines'])
    assert any('lines measured with the piper voices' in n for n in m['notes'])
    assert (tmp_path / 'work' / 'measure' / 'durations.json').exists()
    rep = validate(m, p, characters=ROWS)
    assert not rep['errors']


def test_validator_allows_the_fitting_tempo_on_a_preflight_measurement_but_not_on_a_voiced_line():
    m = build(PLANS[0], characters=ROWS)
    ln = m['lines'][0]
    avail = ln['window_end_frame'] - ln['start_frame']
    over = int(avail * 1.08)
    assert over <= avail * C.line_fit_allowance(m)
    ln['measured_frames'] = over
    ln['est_end_frame'] = ln['start_frame'] + over
    ln['measured_preflight'] = True
    rep = validate(m, prefs.DEFAULTS, characters=ROWS)
    assert not any(e['code'] == 'dialogue_fit' for e in rep['errors'])
    assert any(w['code'] == 'dialogue_tempo' and ln['id'] in w['message'] for w in rep['warnings'])
    del ln['measured_preflight']
    rep = validate(m, prefs.DEFAULTS, characters=ROWS)
    assert any(e['code'] == 'dialogue_fit' and ln['id'] in e['message'] for e in rep['errors'])


# ----------------------------------------------------------------------------- storage: migration and the editor
def _voice(db, cid):
    return json.loads(db.one('SELECT voice FROM characters WHERE id=?', (cid,))['voice'])


def test_migration_fills_measurements_only_where_missing_and_still_valid(db):
    assert '0006_measured_voices' in migrations.VERSIONS and migrations.pending(db) == []
    for c in demo.CHARACTERS:
        assert C.measured_voice(_voice(db, c['id'])) == C.measured_voice(c['voice'])
    # An install from before this release: Bloxy edited but on the measured speaker (filled), Pip moved to another
    # speaker (left alone), Tally slowed down (left alone), Dot already measured by hand (kept).
    strip = {c['id']: {k: v for k, v in c['voice'].items() if k not in S.MEASURED_VOICE_KEYS} for c in demo.CHARACTERS}
    db.execute('UPDATE characters SET voice=? WHERE id=?', (json.dumps(dict(strip['ch_bloxy'], pitch_semitones=1)), 'ch_bloxy'))
    db.execute('UPDATE characters SET voice=? WHERE id=?', (json.dumps(dict(strip['ch_pip'], piper_speaker=12)), 'ch_pip'))
    db.execute('UPDATE characters SET voice=? WHERE id=?', (json.dumps(dict(strip['ch_tally'], piper_length_scale=1.2)), 'ch_tally'))
    db.execute('UPDATE characters SET voice=? WHERE id=?', (json.dumps(dict(strip['ch_dot'], measured_wps=4.0, line_overhead_s=0.3,
                                                                           measured_rate=1.3)), 'ch_dot'))
    db.execute('DELETE FROM characters WHERE id=?', ('ch_rook',))
    db.execute('DELETE FROM schema_migrations WHERE version=?', ('0006_measured_voices',))
    assert migrations.migrate(db) == ['0006_measured_voices']
    assert C.measured_voice(_voice(db, 'ch_bloxy')) == C.measured_voice(ROWS['ch_bloxy']['voice'])
    assert _voice(db, 'ch_bloxy')['pitch_semitones'] == 1
    assert C.measured_voice(_voice(db, 'ch_pip')) is None and _voice(db, 'ch_pip')['piper_speaker'] == 12
    assert C.measured_voice(_voice(db, 'ch_tally')) is None
    assert C.measured_voice(_voice(db, 'ch_dot')) == (4.0, 0.3, 1.3)
    assert db.one('SELECT 1 AS x FROM characters WHERE id=?', ('ch_rook',)) is None, 'm0006 adds nobody'
    assert migrations.migrate(db) == []


def test_characters_page_keeps_a_measurement_unless_the_speaker_or_speed_changes(client, db):
    j = client.get('/api/characters').get_json()
    tally = j['characters']['ch_tally']
    assert C.measured_voice(tally['voice']) == C.measured_voice(ROWS['ch_tally']['voice'])
    body = {'name': tally['name'], 'active': True, 'bible': tally['bible'], 'voice': dict(tally['voice'], pitch_semitones=-1)}
    assert client.put('/api/characters/ch_tally', json=body).status_code == 200
    saved = repo.characters(active_only=False, d=db)['ch_tally']['voice']
    assert saved['pitch_semitones'] == -1 and C.measured_voice(saved) == C.measured_voice(tally['voice'])
    body['voice']['piper_speaker'] = 123
    assert client.put('/api/characters/ch_tally', json=body).status_code == 200
    saved = repo.characters(active_only=False, d=db)['ch_tally']['voice']
    assert saved['piper_speaker'] == 123 and C.measured_voice(saved) is None
    # A measurement cannot be typed in by hand through the editor: it comes from measuring.
    body['voice'].update(piper_speaker=tally['voice']['piper_speaker'], measured_wps=9.9)
    assert client.put('/api/characters/ch_tally', json=body).status_code == 200
    assert C.measured_voice(repo.characters(active_only=False, d=db)['ch_tally']['voice']) is None
