"""Production pace: what compression does to plausibility, dialogue fit and perceptual minimums."""
import copy
import json
from pathlib import Path

import pytest

from blox import demo, prefs
from blox.manifest import compile as C
from blox.manifest.validate import validate

ROOT = Path(__file__).resolve().parents[2]
STORIES = json.loads((ROOT / 'stories' / 'batch-2026-10-03-claude.json').read_text())


def default_prefs(**production):
    p = copy.deepcopy(prefs.DEFAULTS)
    p['production'].update(production)
    return prefs.validate(p)


def build(plan, p):
    pr = p['production']
    return C.compile_plan(copy.deepcopy(plan), fps=pr['fps'], width=pr['width'], height=pr['height'],
                          **C.pace_kwargs(p))


def story(title):
    return copy.deepcopy(next(s for s in STORIES if s['title'] == title))


def codes(rep, kind='errors'):
    return [e['code'] for e in rep[kind]]


@pytest.mark.parametrize('plan', STORIES + [demo.plan()], ids=[s['title'] for s in STORIES] + ['demo'])
def test_backlog_stories_and_demo_validate_at_the_default_pace(plan):
    p = default_prefs()
    assert (p['production']['pace'], p['production']['speech_rate']) == (1.5, 1.3)
    m = build(plan, p)
    rep = validate(m, p)
    assert rep['ok'], [e['message'] for e in rep['errors']]
    assert rep['stats']['duration_s'] == pytest.approx(plan['duration_s'] / 1.5)


def test_compressed_walk_becomes_a_run_only_when_compression_caused_it():
    plan = story('The Spring Pad Shortcut')
    slow = build(plan, default_prefs(pace=1.0, speech_rate=1.0))
    fast = build(plan, default_prefs())
    a_slow = {a['id']: a for a in slow['tracks']['actions']}
    a_fast = {a['id']: a for a in fast['tracks']['actions']}
    assert a_slow['a4']['type'] == 'walk' and a_slow['a8']['type'] == 'walk'
    assert a_fast['a4']['type'] == 'run' and a_fast['a4']['converted_from'] == 'walk'
    assert any('walk a4' in n and 'performed as a run' in n for n in fast['notes'])
    assert validate(fast, default_prefs())['ok']
    # A walk that was already too fast in story time stays a walk, and the validator reports it.
    bad = copy.deepcopy(plan)
    walk = next(a for a in bad['actions'] if a.get('id', '') == 'a4' or a['type'] == 'walk')
    walk['main_frames'] = 8
    m = build(bad, default_prefs())
    assert all(a.get('converted_from') is None for a in m['tracks']['actions'] if a['main_frames'] <= 6)
    rep = validate(m, default_prefs())
    assert 'implausible_speed' in codes(rep)


def test_compressed_jump_keeps_a_plausible_airtime():
    plan = story('The Perfect Speedrun')
    p = default_prefs()
    m = build(plan, p)
    jump = next(a for a in m['tracks']['actions'] if a['type'] == 'jump')
    authored = next(a for a in plan['actions'] if a['type'] == 'jump')
    assert jump['main_frames'] > round(authored['main_frames'] / 1.5)
    assert any('keeps' in n and 'airtime' in n for n in m['notes'])
    rep = validate(m, p)
    assert 'implausible_speed' not in codes(rep) and rep['ok']
    # Landing key moved with the airtime: the character arrives exactly when the jump lands.
    keys = m['tracks']['characters'][jump['character']]['keys']
    land = jump['start_frame'] + jump['anticipation_frames'] + jump['main_frames']
    assert any(k['frame'] == land and 'position' in k['explicit'] for k in keys)
    # An already implausible jump is not rescued.
    bad = copy.deepcopy(plan)
    next(a for a in bad['actions'] if a['type'] == 'jump')['main_frames'] = 9
    rep = validate(build(bad, p), p)
    assert 'implausible_speed' in codes(rep)


def test_tight_line_is_fitted_up_to_the_pace_never_above_one_and_a_half():
    plan = demo.plan()
    p = default_prefs()
    rep = validate(build(plan, p), p)
    tempo = [w['message'] for w in rep['warnings'] if w['code'] == 'dialogue_tempo']
    assert tempo and 'limit 1.50x' in tempo[0], rep['warnings']
    assert 'dialogue_fit' not in codes(rep)
    # The same line with a few more words needs more than 1.5x in total: an error, as before.
    long = copy.deepcopy(plan)
    ln = next(x for x in long['lines'] if x['id'] == 'l6')
    ln['text'] = ln['text'] + ' Seriously, every single time.'
    rep = validate(build(long, p), p)
    assert 'dialogue_fit' in codes(rep)
    # At real time there is no allowance at all (unchanged behaviour).
    p1 = default_prefs(pace=1.0, speech_rate=1.0)
    m1 = build(long, p1)
    l6 = next(x for x in m1['lines'] if x['id'] == 'l6')
    assert l6['est_frames'] > l6['window_end_frame'] - l6['start_frame']
    assert 'dialogue_fit' in codes(validate(m1, p1))


def test_voice_faster_than_one_and_a_half_gets_no_fitting_allowance():
    m = build(demo.plan(), default_prefs(pace=2.0, speech_rate=1.6, min_seconds=15))
    assert C.line_fit_allowance(m) == 1.0 and C.tempo_cap(m, 1.15) == 1.0


def test_expression_minimum_is_not_compressed():
    plan = demo.plan()
    keys = plan['performance']['hero']
    relieved = next(k for k in keys if k.get('expression') == 'relieved')
    nxt = keys[keys.index(relieved) + 1]
    nxt['t'] = relieved['t'] + 0.7  # 21 story frames: 11 on screen at real time, 7 at pace 1.5
    assert validate(build(plan, default_prefs(pace=1.0, speech_rate=1.0)), default_prefs())['ok']
    rep = validate(build(plan, default_prefs()), default_prefs())
    short = [e['message'] for e in rep['errors'] if e['code'] == 'expression_too_short']
    assert short and '"relieved"' in short[0] and '(minimum 8)' in short[0]


def test_duration_message_explains_the_pace():
    p = default_prefs(min_seconds=30, target_seconds=42)
    rep = validate(build(demo.plan(), p), p)
    msg = next(e for e in rep['errors'] if e['code'] == 'duration')
    assert '20.00s (30.00s of story played at pace 1.5)' in msg['message'] and '45-90s' in msg['fix']
