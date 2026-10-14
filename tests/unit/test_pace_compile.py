"""Production pace: compiling divides every authored time quantity by the pace (and nothing else)."""
import copy
import json
import re
from pathlib import Path

import pytest

from blox import demo
from blox.manifest import compile as C

ROOT = Path(__file__).resolve().parents[2]
FPS = 30

# Compiled fields that are deliberately NOT story time: speech estimates follow the speech rate, captions are
# derived from them (with a real-time minimum display), automatic blinks follow a natural real-time interval,
# and beat/QA/note lists depend on those. Everything else whose name says it is a frame or a millisecond count
# must scale by exactly 1 / pace.
NOT_STORY_TIME = {'est_frames', 'est_end_frame', 'captions', 'blinks', 'dialogue', 'vocal', 'qa', 'notes'}
TIME_KEY = re.compile(r'(^|_)(frame|frames|ms)$')
# Inside a beat, the per-character action phases, keyframes and cue lists are views of the tracks clipped to the
# beat; the tracks are compared directly, and rounding can move a one-frame sliver across a beat edge.
BEAT = re.compile(r'\.beats\[\d+\]$')
BEAT_FIELDS = {'start_frame', 'end_frame'}


def timed_plan():
    """The demo plan plus every optional time field compile_plan reads, with distinctive non-zero values."""
    p = demo.plan()
    p['cover_t'] = 4.5
    p['blinks'] = {'hero': [5.1, 17.4], 'pip': [8.25]}
    p['props_events'] = [{'t': 26.4, 'prop': 'coin', 'event': 'detach', 'character': 'pip'}]
    p['music'] = [{'t': 0, 'cue': 'playful'}, {'t': 12.0, 'cue': 'tension'}, {'t': 21.6, 'cue': 'triumph'}]
    p['sfx'] = (p.get('sfx') or []) + [{'t': 9.3, 'cue': 'pop'}]
    p['lines'][1]['pause_after_ms'] = 450
    p['performance']['hero'][2]['blend_frames'] = 18
    for a in p['actions']:
        a['hold_frames'] = a.get('hold_frames') or 3
        if a['type'] == 'jump':
            a['main_frames'] = 26  # slow enough to stay a plausible jump when compressed
    return p


AUTHORED_TIME_FIELDS = [
    ('duration_s',), ('hook', 't_end'), ('payoff', 't_start'), ('cover_t',), ('shots', '*', 'start_s'),
    ('shots', '*', 'end_s'), ('performance', '*', '*', 't'), ('performance', '*', '*', 'blend_frames'),
    ('actions', '*', 't'), ('actions', '*', 'anticipation_frames'), ('actions', '*', 'main_frames'),
    ('actions', '*', 'follow_through_frames'), ('actions', '*', 'hold_frames'), ('props_events', '*', 't'),
    ('blinks', '*', '*'), ('lines', '*', 't'), ('lines', '*', 'pause_after_ms'), ('sfx', '*', 't'),
    ('music', '*', 't'), ('beats', '*', 'start_s'),
]


def _present(node, path):
    if not path:
        return node not in (None, 0, [], {})
    head, rest = path[0], path[1:]
    if head == '*':
        items = node.values() if isinstance(node, dict) else node
        return any(_present(x, rest) for x in items)
    return isinstance(node, dict) and head in node and _present(node[head], rest)


def test_the_test_plan_uses_every_authored_time_field():
    p = timed_plan()
    missing = [path for path in AUTHORED_TIME_FIELDS if not _present(p, path)]
    assert not missing, f'extend timed_plan() with {missing}'


def _compare(a, b, pace, path, out):
    if isinstance(a, dict):
        assert isinstance(b, dict), path
        for k, v in a.items():
            if k in NOT_STORY_TIME or (BEAT.search(path) and k not in BEAT_FIELDS):
                continue
            assert k in b, f'{path}.{k} disappeared at pace {pace}'
            if TIME_KEY.search(k) and isinstance(v, (int, float)) and not isinstance(v, bool):
                tol = 1.0 if k.endswith('_ms') else 1.5
                assert abs(b[k] - v / pace) <= tol, f'{path}.{k}: {b[k]} at pace {pace}, expected ~{v / pace:.2f}'
                out.append(f'{path}.{k}')
            else:
                _compare(v, b[k], pace, f'{path}.{k}', out)
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), f'{path}: {len(a)} vs {len(b) if isinstance(b, list) else b}'
        for i, (x, y) in enumerate(zip(a, b)):
            _compare(x, y, pace, f'{path}[{i}]', out)


@pytest.mark.parametrize('pace', [1.25, 1.5, 2.0])
def test_every_frame_field_scales_with_pace(pace):
    plan = timed_plan()
    base = C.compile_plan(copy.deepcopy(plan), fps=FPS, width=1080, height=1920)
    fast = C.compile_plan(copy.deepcopy(plan), fps=FPS, width=1080, height=1920, pace=pace)
    assert not [n for n in fast['notes'] if 'jump' in n or 'run' in n], 'test plan must avoid gait/airtime changes'
    compared = []
    _compare(base, fast, pace, 'm', compared)
    names = {re.sub(r'\[\d+\]', '[]', c) for c in compared}
    for expect in ('m.duration_frames', 'm.hook.end_frame', 'm.payoff.start_frame', 'm.cover_frame',
                   'm.shots[].start_frame', 'm.shots[].end_frame', 'm.tracks.characters.hero.keys[].frame',
                   'm.tracks.characters.hero.keys[].blend_frames', 'm.tracks.actions[].start_frame',
                   'm.tracks.actions[].anticipation_frames', 'm.tracks.actions[].main_frames',
                   'm.tracks.actions[].follow_through_frames', 'm.tracks.actions[].hold_frames',
                   'm.tracks.actions[].end_frame', 'm.tracks.props[].frame', 'm.lines[].start_frame',
                   'm.lines[].window_end_frame', 'm.lines[].pause_after_ms', 'm.sfx[].frame', 'm.music[].frame',
                   'm.beats[].start_frame', 'm.beats[].end_frame'):
        assert expect in names, f'{expect} was not compared'
    # Authored blinks are story time; the automatic ones are a real-time rhythm.
    for cid, ts in plan['blinks'].items():
        for t in ts:
            assert C.fr(t / pace, FPS) in fast['tracks']['blinks'][cid]


def test_defaults_compile_at_real_time_and_record_the_pace():
    plan = demo.plan()
    a = C.compile_plan(copy.deepcopy(plan), fps=FPS, width=1080, height=1920)
    b = C.compile_plan(copy.deepcopy(plan), fps=FPS, width=1080, height=1920, pace=1.0, speech_rate=1.0)
    assert a == b and a['pace'] == {'timeline': 1.0, 'speech_rate': 1.0}
    c = C.compile_plan(copy.deepcopy(plan), fps=FPS, width=1080, height=1920, pace=1.5, speech_rate=1.3)
    assert c['pace'] == {'timeline': 1.5, 'speech_rate': 1.3}
    assert c['duration_frames'] == 600 and all(ln['speech_rate'] == 1.3 for ln in c['lines'])
    assert c == C.compile_plan(copy.deepcopy(plan), fps=FPS, width=1080, height=1920, pace=1.5, speech_rate=1.3)


@pytest.mark.parametrize('kw', [{'pace': 0.9}, {'pace': 2.1}, {'pace': float('nan')}, {'pace': 'fast'},
                                {'speech_rate': 0.7}, {'speech_rate': 1.7}])
def test_pace_outside_the_supported_range_is_rejected(kw):
    with pytest.raises(ValueError):
        C.compile_plan(demo.plan(), fps=FPS, width=1080, height=1920, **kw)


def test_line_estimate_follows_the_speech_rate():
    line = {'text': 'Wait, where is the next platform? I can not see it at all!', 'pace': 'normal'}
    normal = C.estimate_line_frames(line, FPS)
    quick = C.estimate_line_frames(line, FPS, speech_rate=1.3)
    assert C.estimate_line_frames(dict(line, speech_rate=1.3), FPS) == quick
    # Speech (words and punctuation pauses) is 1.3x quicker; the fixed 0.15 s tail is not.
    assert quick == pytest.approx((normal - 0.15 * FPS) / 1.3 + 0.15 * FPS, abs=1.0)


def test_implicit_blend_is_compressed_like_an_explicit_one():
    plan = demo.plan()
    plan['performance']['hero'][2]['blend_frames'] = 12
    slow = C.compile_plan(copy.deepcopy(plan), fps=FPS, width=1080, height=1920)
    fast = C.compile_plan(copy.deepcopy(plan), fps=FPS, width=1080, height=1920, pace=1.5)
    k_slow, k_fast = slow['tracks']['characters']['hero']['keys'], fast['tracks']['characters']['hero']['keys']
    assert k_slow[2]['blend_frames'] == 12 and k_fast[2]['blend_frames'] == 8
    assert 'blend_frames' not in k_slow[1]  # real time keeps the implicit 10-frame default
    assert k_fast[1]['blend_frames'] == round(C.DEFAULT_BLEND_FRAMES / 1.5)
    # Every consumer (solver, validator, QA) reads the same transition.
    for f in range(k_fast[1]['frame'] - 9, k_fast[1]['frame'] + 1):
        assert C.pose_at(k_fast, f) == C.pose_at(k_fast, f, round(C.DEFAULT_BLEND_FRAMES / 1.5))


def test_beats_are_one_story_second_each():
    plan = json.loads((ROOT / 'stories' / 'batch-2026-10-03-claude.json').read_text())[0]
    m = C.compile_plan(copy.deepcopy(plan), fps=FPS, width=1080, height=1920, pace=1.5)
    by_second = {}
    for b in m['beats']:
        assert b['end_frame'] - b['start_frame'] <= FPS
        by_second.setdefault(int(round(b['start_frame'] * 1.5 / FPS - 0.49)), b)
    # Every authored beat keeps its own slot: same purposes in the same order as the plan.
    authored = [b['purpose'] for b in sorted(plan['beats'], key=lambda b: b['start_s'])]
    assert [b['purpose'] for b in m['beats'] if not b['purpose_inherited']][:len(authored)] == authored[:len(
        [b for b in m['beats'] if not b['purpose_inherited']])]
    assert len([b for b in m['beats'] if not b['purpose_inherited']]) >= len(authored)
