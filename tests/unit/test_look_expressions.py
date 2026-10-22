"""Big-reaction expression presets, the optional pupil control and face swaps that snap on the cut."""
import copy
import hashlib
import json

import pytest

from blox import demo, prefs
from blox.animation import rig as R, solver as SV
from blox.manifest import compile as C, schema as S
from blox.manifest.validate import validate

NEW = ('screaming', 'smug_max', 'mischief', 'frozen')
# sha256 of the 18 presets as they were before the big-reaction presets were added (sorted JSON).
ORIGINAL_PRESETS_SHA = '8138201e4f8989029430f2a9c3af6147eeeb99f422fc4dd9be269a8934c2e01b'


def build(plan, **kw):
    return C.compile_plan(plan, fps=30, width=1080, height=1920, **kw)


def P():
    return copy.deepcopy(prefs.DEFAULTS)


def test_existing_presets_are_unchanged():
    old = {k: v for k, v in S.EXPRESSIONS.items() if k not in NEW}
    assert len(old) == 18
    assert hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest() == ORIGINAL_PRESETS_SHA
    assert S.MOUTH_SHAPES[:11] == ['neutral', 'smile', 'grin', 'open_smile', 'frown', 'o', 'gasp', 'grimace',
                                   'smirk', 'pout', 'flat']


@pytest.mark.parametrize('name', NEW)
def test_new_presets_are_complete_and_in_range(name):
    e = S.EXPRESSIONS[name]
    assert e['mouth']['shape'] in S.MOUTH_SHAPES and e['mouth']['shape'] in R.MOUTH_SHAPES
    for (part, k), (lo, hi) in S.RANGES.items():
        if part in e:
            assert lo <= e[part][k] <= hi, (name, part, k)
    for (part, k), (lo, hi) in S.OPTIONAL_RANGES.items():
        if k in e.get(part, {}):
            assert lo <= e[part][k] <= hi


def test_presets_read_as_intended():
    E = S.EXPRESSIONS
    assert E['screaming']['eyes']['open'] == S.RANGES[('eyes', 'open')][1] and E['screaming']['eyes']['pupil'] < 1
    assert E['frozen']['eyes']['pupil'] < E['screaming']['eyes']['pupil']
    assert abs(E['smug_max']['brows']['asym']) > abs(E['smug']['brows']['asym'])
    assert E['mischief']['brows']['inner'] < 0 < E['mischief']['brows']['outer']
    assert R.MOUTH_SHAPES['scream'][2] > R.MOUTH_SHAPES['gasp'][2]  # wider open than a gasp


def _plan_with(expr_keys, style=None):
    p = demo.plan()
    p['performance']['hero'] += expr_keys
    if style:
        p['style'] = style
    return p


def test_new_expressions_validate_and_solve_with_pupil_size():
    p = _plan_with([{'t': 17.5, 'expression': 'screaming'}, {'t': 19.0, 'expression': 'frozen'}])
    m = build(p)
    rep = validate(m, P())
    assert not [e for e in rep['errors'] if 'expression' in e['code']], rep['errors']
    assert rep['unsupported'] == []
    keys = m['tracks']['characters']['hero']['keys']
    frozen = next(k for k in keys if k['pose']['expression'] == 'frozen')
    assert frozen['pose']['eyes']['pupil'] == 0.45
    # Blending into a preset without the control eases the pupil back to 1 instead of popping.
    mid = C.interp_pose(frozen['pose'], S.DEFAULT_POSE | {'eyes': {'open': 1.0, 'squint': 0.0}}, 0.5)
    assert 0.45 < mid['eyes']['pupil'] < 1.0
    bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id']) for c in m['cast']}
    solved = SV.solve(m, bibles)
    hero = solved['characters']['hero']
    assert hero[frozen['frame'] + 1]['face']['pupil_size'] == pytest.approx(0.45, abs=0.01)
    assert 'pupil_size' not in hero[0]['face']  # presets without the control leave the solved face unchanged


def test_out_of_range_pupil_is_rejected():
    p = _plan_with([{'t': 15.0, 'expression': 'frozen', 'eyes': {'pupil': 2.0}}])
    assert 'out_of_range' in {e['code'] for e in validate(build(p), P())['errors']}


def test_no_automatic_blinks_while_screaming_or_frozen():
    p = _plan_with([{'t': 14.0, 'expression': 'screaming'}, {'t': 17.0, 'expression': 'happy'}])
    m = build(p)
    keys = m['tracks']['characters']['hero']['keys']
    a = next(k['frame'] for k in keys if k['pose']['expression'] == 'screaming')
    b = next(k['frame'] for k in keys if k['frame'] > a)
    assert not [f for f in m['tracks']['blinks']['hero'] if a <= f < b]


def test_expressions_blend_by_default():
    m = build(demo.plan())
    assert m['style']['expression_snap'] == 'blend'
    assert not any(k.get('snap') for tr in m['tracks']['characters'].values() for k in tr['keys'])


def test_expression_changes_snap_onto_the_cut():
    plan = demo.plan()
    base = build(copy.deepcopy(plan))
    m = build(_plan_with([], {'expression_snap': 'cut'}))
    cuts = {s['start_frame'] for s in m['shots'] if s['start_frame'] > 0}
    snapped = [(cid, k) for cid, tr in m['tracks']['characters'].items() for k in tr['keys'] if k.get('snap')]
    assert snapped
    for cid, k in snapped:
        assert k['frame'] in cuts and k['blend_frames'] == 1
        keys = m['tracks']['characters'][cid]['keys']
        i = keys.index(k)
        # The old face holds to the last frame of the outgoing shot; the new one is on the cut frame.
        assert C.pose_at(keys, k['frame'] - 1)['expression'] == keys[i - 1]['pose']['expression']
        assert C.pose_at(keys, k['frame'])['expression'] == k['pose']['expression']
    # pip's "excited" (authored 10 frames after the cut at 3.5 s) moves back onto the cut.
    pip = next(k for k in m['tracks']['characters']['pip']['keys'] if k['pose']['expression'] == 'excited')
    assert pip['frame'] == 105 and pip['authored_frame'] == 120
    # Keys that place or turn a character, and automatic keys, keep their timing.
    for cid, tr in m['tracks']['characters'].items():
        for k0, k1 in zip(base['tracks']['characters'][cid]['keys'], tr['keys']):
            if k0.get('explicit') or k0.get('auto'):
                assert k0['frame'] == k1['frame']
    assert validate(m, P())['ok']


def test_a_key_can_snap_on_its_own():
    m = build(_plan_with([{'t': 15.2, 'expression': 'mischief', 'snap': True}]))
    k = next(k for k in m['tracks']['characters']['hero']['keys'] if k['pose']['expression'] == 'mischief')
    assert k['blend_frames'] == 1
    assert C.pose_at(m['tracks']['characters']['hero']['keys'], k['frame'] - 1)['expression'] != 'mischief'


@pytest.mark.parametrize('pace', [1.5, 2.0])
def test_snapped_frames_follow_the_pace(pace):
    p = _plan_with([], {'expression_snap': 'cut'})
    a, b = build(copy.deepcopy(p)), build(copy.deepcopy(p), pace=pace)
    sa = [k for tr in a['tracks']['characters'].values() for k in tr['keys'] if k.get('snap')]
    sb = [k for tr in b['tracks']['characters'].values() for k in tr['keys'] if k.get('snap')]
    cuts_b = {s['start_frame'] for s in b['shots']}
    assert len(sa) == len(sb) and all(k['frame'] in cuts_b for k in sb)
    for x, y in zip(sa, sb):
        assert abs(y['frame'] - x['frame'] / pace) <= 1.5
