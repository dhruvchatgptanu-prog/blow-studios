"""The first-person story batch (2026-10-22) in the owner's "I ... 😳" format.

Every plan compiles at the production pace with the demo cast (and its measured voices, as the backlog import
does) and validates with no errors; it carries the format's labels and beats; the twist types rotate; the
titles follow the format's grammar and none is a close copy of another creator's titles. The cameras and
feet are checked with the solver like the mainstream batch.
"""
import copy
import difflib
import json
import os
import re

import numpy as np
import pytest

from blox import demo, prefs as P
from blox.animation import rig as R, solver as SV
from blox.manifest import compile as C, validate as V
from blox.story import formats as FM

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BATCH = os.path.join(ROOT, 'stories', 'batch-2026-10-22-first-person.json')
with open(BATCH, encoding='utf-8') as _f:
    PLANS = json.load(_f)
TITLES = [p['title'] for p in PLANS]
ROWS = {c['id']: {'name': c['name'], 'bible': c['bible'], 'voice': c['voice']} for c in demo.CHARACTERS}

# Another creator's published titles. They are listed here ONLY so this test can make sure no title in the
# batch copies or closely varies them; they are never used as titles or ideas.
OTHER_CREATOR_TITLES = (
    'Chat Made Me Marry a Stranger \U0001F633',
    "I Can't Say One Word \U0001F633",
    "I Broke My Boss's Windshield \U0001F633",
    'I Rescued a Man From a Crate \U0001F633',
    '100 Man Vs 1 Gorilla \U0001F633',
    'A Slow Walker Owes Me His Life \U0001F633',
    'I Punched the Free Hugs Guy \U0001F633',
    "I Don't Believe in Trash \U0001F633",
    'I Stole a Truck and the Owner Thanked Me \U0001F633',
    'My Search History Got Me Tackled \U0001F633',
)


def _norm(title):
    """Lower-case words only: emoji and punctuation removed."""
    return ' '.join(re.findall(r"[a-z0-9']+", title.lower().replace('’', "'")))


def _jaccard(a, b):
    x, y = set(_norm(a).split()), set(_norm(b).split())
    return len(x & y) / len(x | y)


def _compiled(plan):
    return C.compile_plan(plan, fps=30, width=540, height=960, characters=ROWS, **C.pace_kwargs(P.DEFAULTS))


def _validate(m):
    return V.validate(m, P.DEFAULTS, characters=ROWS, calibration=copy.deepcopy(FM.DEFAULTS))


def test_batch_has_twelve_distinct_plans_that_validate_without_errors():
    assert len(PLANS) == 12 and len(set(TITLES)) == 12
    for plan in PLANS:
        m = _compiled(plan)
        assert m['pace'] == {'timeline': 1.5, 'speech_rate': 1.3}
        assert all(ln['estimate'] == 'measured_voice' for ln in m['lines']), plan['title']
        rep = _validate(m)
        assert not rep['errors'], (plan['title'], rep['errors'][:3])
        assert not [w for w in rep['warnings'] if w['code'].startswith('format_')], (plan['title'], rep['warnings'])
        assert 28.0 <= m['duration_frames'] / m['fps'] <= 40.0, plan['title']


@pytest.mark.parametrize('title', TITLES)
def test_plan_carries_the_first_person_format(title):
    plan = next(p for p in PLANS if p['title'] == title)
    m = _compiled(plan)
    st = plan['story']
    assert plan['format'] == FM.FIRST_PERSON and m['format'] == FM.FIRST_PERSON
    assert st['mode'] in FM.MODES and st['mechanism'] in FM.MECHANISMS
    assert st['twist_type'] in FM.TWIST_TYPES and st['loop_type'] in FM.LOOP_TYPES
    assert {k: m['story'][k] for k in FM.STORY_KEYS} == {k: st[k] for k in FM.STORY_KEYS}
    narrator = plan['narrator']
    assert narrator in {c['id'] for c in plan['cast']}
    first = min(plan['lines'], key=lambda ln: ln['t'])
    assert first['speaker'] == narrator and first['kind'] == 'narration' and first['t'] <= 0.2
    assert FM.words(first['text'])[0] == 'I'
    purposes = [b['purpose'] for b in sorted(plan['beats'], key=lambda b: b['start_s'])]
    assert purposes[0] == 'hook' and purposes[-1] == 'loop'
    for p in ('mid_gag', 'twist', 'payoff'):
        assert p in purposes, (title, p)
    # Other characters keep to short lines; the narration is the narrator's.
    for ln in plan['lines']:
        if ln['speaker'] != narrator:
            assert ln['kind'] == 'dialogue' and len(FM.words(ln['text'])) <= 5, (title, ln['text'])
    # The mid-video gag asks for a like or a subscribe in the narrator's voice, never with "smash".
    gag = next(b['start_s'] for b in plan['beats'] if b['purpose'] == 'mid_gag')
    gag_lines = [ln for ln in plan['lines'] if gag <= ln['t'] < gag + 3 and ln['speaker'] == narrator]
    assert any(FM.LIKE_SUB.search(ln['text']) for ln in gag_lines), title
    assert not any('smash' in ln['text'].lower() for ln in plan['lines'])


def test_mix_of_modes_mechanisms_and_twists():
    stories = [p['story'] for p in PLANS]
    modes = [s['mode'] for s in stories]
    assert modes.count('world_scale') == 6 and modes.count('grounded') == 6
    assert len({s['mechanism'] for s in stories}) >= 6
    twists = [s['twist_type'] for s in stories]
    assert twists.count('authority_from_home') <= 1
    for i in range(len(twists) - 4):
        window = twists[i:i + 5]
        assert len(set(window)) == 5, (i + 1, window)


@pytest.mark.parametrize('title', TITLES)
def test_title_follows_the_format_and_copies_no_other_creator(title):
    plan = next(p for p in PLANS if p['title'] == title)
    tr = FM.title_report(title)
    assert title.startswith('I ') and tr['flushed_last'] and not tr['hashtags'], title
    assert 5 <= tr['words'] <= 9, title
    assert plan['metadata']['title'] == title
    for other in OTHER_CREATOR_TITLES:
        ratio = difflib.SequenceMatcher(None, _norm(title), _norm(other)).ratio()
        assert ratio < 0.6 and _jaccard(title, other) < 0.6, (title, other, ratio)


@pytest.mark.parametrize('title', TITLES)
def test_cameras_never_inside_or_blocked(title):
    plan = next(p for p in PLANS if p['title'] == title)
    m = _compiled(plan)
    bibles = {c['id']: ROWS[c['character_id']]['bible'] for c in m['cast']}
    chars = {c['id']: SV.CharacterSolver(m, c['id'], bibles.get(c['id']), m['duration_frames']) for c in m['cast']}
    cams = SV.solve_camera(m, chars, m['duration_frames'])
    for s in m['shots']:
        subj = s['camera']['subject']
        if subj not in chars:
            continue
        frames = cams[s['start_frame']:s['end_frame']]
        for i, fr in enumerate(frames):
            f = s['start_frame'] + i
            for oid, o in chars.items():
                d = np.hypot(fr['location'][0] - o.root_xy[f][0], fr['location'][1] - o.root_xy[f][1])
                assert oid == subj or d > 0.42 * o.scale, (title, s['id'], f, oid)
        blocked = SV._blocked_frames(frames, chars, subj, s['start_frame'])
        assert len(blocked) <= max(15, 0.25 * len(frames)), (title, s['id'], len(blocked))


@pytest.mark.parametrize('title', TITLES)
def test_planted_feet_neither_float_nor_slide(title):
    plan = next(p for p in PLANS if p['title'] == title)
    m = _compiled(plan)
    res = SV.solve(m, {c['id']: ROWS[c['character_id']]['bible'] for c in m['cast']})
    for cid, frames in res['characters'].items():
        sc = res['scales'][cid]
        prev, floats, slides = None, 0, 0
        for fr in frames:
            k = SV.fk({'root': fr['root'], 'yaw': fr['yaw'], 'rot': fr['rot'], 'loc': fr['loc']}, sc)
            cur = {}
            for side in 'lr':
                if not fr['feet'][side]['contact_expected']:
                    cur[side] = None
                    continue
                ank = k['ankle_' + side][1]
                ground = fr['feet'][side].get('ground_z', 0.0)
                if abs(ank[2] - ground - R.ANKLE_HEIGHT * sc) > 0.012:
                    floats += 1
                if prev and prev.get(side) is not None and np.linalg.norm(ank[:2] - prev[side][:2]) > 0.006:
                    slides += 1
                cur[side] = ank
            prev = cur
        assert floats == 0 and slides == 0, (title, cid, floats, slides)
