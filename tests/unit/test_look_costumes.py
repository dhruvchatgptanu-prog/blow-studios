"""Costume vocabulary shared by bibles, the editor and the renderer; costume parts in the QA inventory."""
import ast
import copy
from pathlib import Path

import pytest

from blox import demo
from blox.animation import blender as BL, rig as R
from blox.manifest import compile as C
from blox.manifest.validate import validate
from blox.qa import motion as MO
from blox.qa.report import Checks

SCENE = Path(BL.SCRIPT).read_text()


def _scene_constant(name):
    """A module-level constant of blender_scene.py (it imports bpy, so it is read, not imported)."""
    for node in ast.parse(SCENE).body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


def test_vocabulary_matches_the_shared_list():
    co = R.COSTUME_OPTIONS
    assert co['hair'] == ['messy_block', 'short_block', 'bun', 'pigtails', 'spiky', 'long_block', 'bald']
    assert co['hat'] == [None, 'cap', 'cap_backwards', 'crown', 'bow', 'beanie']
    assert co['eyewear'] == [None, 'glasses', 'sunglasses']
    assert co['top'] == ['hoodie', 'tee', 'jacket', 'vest', 'cardigan']
    assert co['tie'] == [False, True] and co['badge'] == [None, 'star', 'diamond']
    assert {'accessory', 'top2'} <= set(R.PALETTE_SLOTS) and R.OPTIONAL_PALETTE_SLOTS >= {'accessory', 'top2'}


def test_blender_builder_uses_the_same_rules_as_the_rig():
    assert _scene_constant('LAYER_TOPS') == R.LAYER_TOPS
    assert _scene_constant('COVERING_HATS') == R.COVERING_HATS
    assert _scene_constant('OPTIONAL_SLOTS') == R.OPTIONAL_PALETTE_SLOTS
    tops = _scene_constant('HAIR_TOP')
    assert set(tops) == set(R.COSTUME_OPTIONS['hair'])
    # Every item of the vocabulary has a branch in the builder.
    for key in ('hair', 'hat', 'eyewear', 'top', 'badge'):
        for v in R.COSTUME_OPTIONS[key]:
            if v:
                assert f"'{v}'" in SCENE, (key, v)


@pytest.mark.parametrize('costume,parts', [
    ({'top': 'hoodie', 'hair': 'messy_block', 'hat': None, 'badge': 'star'}, ['hood', 'hair', 'badge']),
    ({'top': 'tee', 'hair': 'short_block', 'hat': 'cap', 'badge': None}, ['collar', 'hair', 'hat']),
    ({'top': 'jacket', 'hair': 'spiky', 'hat': 'crown', 'tie': True}, ['jacket', 'hair', 'hat', 'tie']),
    ({'top': 'cardigan', 'hair': 'bun', 'eyewear': 'glasses'}, ['cardigan', 'hair', 'eyewear']),
    ({'top': 'vest', 'hair': 'bald', 'eyewear': 'sunglasses', 'badge': 'diamond'}, ['vest', 'eyewear', 'badge']),
    ({}, []),
])
def test_costume_parts(costume, parts):
    assert R.costume_parts(costume) == parts
    assert set(parts) <= set(R.OPTIONAL_PARTS)


def test_costume_problems():
    assert R.costume_problems({'hair': 'pigtails', 'hat': 'bow', 'eyewear': None, 'top': 'tee', 'tie': False}) == []
    assert R.costume_problems({'hair': None, 'top': None}) == []
    assert R.costume_problems({'hat': 'helmet'}) and R.costume_problems({'cape': 'red'})
    assert R.costume_problems({'tie': 'yes'})


def test_existing_cast_costumes_are_in_the_vocabulary():
    for ch in demo.CHARACTERS:
        assert R.costume_problems(ch['bible']['costume']) == []


def test_new_mouth_shapes_exist_in_the_rig_and_fit_the_face():
    for name in ('scream', 'wide_grin', 'smirk_wide', 'tiny'):
        wf, corner, opening, q, asym = R.MOUTH_SHAPES[name]
        # The lower lip (68 % of the opening hangs below the mouth line, the same curve the Blender mouth
        # uses) stays above the chin at head-local z 0; the mouth stays narrower than the eyes' span.
        lowest = min(corner * t * t + asym * t - 0.0085 - 0.68 * opening * max(0.0, 1 - abs(t) ** q) ** (1 / q)
                     for t in [i / 12 - 1 for i in range(25)])
        assert R.FACE['mouth_z'] + lowest > 0.015, name
        assert wf * R.FACE['mouth_w'] / 2 < R.FACE['eye_x'] + R.FACE['eye_w'], name


def test_cast_specs_carry_costume_to_blender():
    m = C.compile_plan(demo.plan(), fps=30, width=270, height=480)
    bible = {'scale': 0.8, 'palette': {'top': '#123456', 'top2': '#654321', 'accessory': '#FF00FF'},
             'costume': {'hair': 'pigtails', 'hat': 'bow', 'top': 'cardigan', 'eyewear': 'glasses', 'tie': True}}
    specs = BL.cast_specs(m, {'hero': bible, 'pip': {}})
    hero = next(s for s in specs if s['id'] == 'hero')
    assert hero['costume'] == bible['costume'] and hero['palette']['top2'] == '#654321'
    assert BL.rig_spec()['palette_slots'] == R.PALETTE_SLOTS
    assert 'scream' in BL.rig_spec()['mouth_shapes']


def _recs(parts_by_frame, count=40):
    return {f: {'parts_visible': sorted(p), 'object_count': count} for f, p in enumerate(parts_by_frame)}


def _integrity(recs):
    m = C.compile_plan(demo.plan(), fps=30, width=270, height=480)
    ck = Checks(30)
    MO._integrity(ck, m, 'hero', recs)
    return ck.items[0]


def test_integrity_accepts_costume_parts():
    body = set(R.REQUIRED_PARTS)
    item = _integrity(_recs([body | {'hair', 'hat', 'eyewear', 'jacket', 'tie'}] * 6))
    assert item['status'] == 'pass', item['evidence']
    assert item['evidence']['costume_parts'] == ['eyewear', 'hair', 'hat', 'jacket', 'tie']
    # Old telemetry (body parts only) still passes.
    assert _integrity(_recs([body] * 6))['status'] == 'pass'


def test_integrity_fails_when_a_costume_piece_disappears_or_is_unknown():
    body = set(R.REQUIRED_PARTS)
    frames = [body | {'hair', 'hat'}] * 5 + [body | {'hair'}]
    item = _integrity(_recs(frames))
    assert item['status'] == 'fail' and item['evidence']['frames_costume_changed'] == 1
    item = _integrity(_recs([body | {'cape'}] * 3))
    assert item['status'] == 'fail' and item['evidence']['unknown_parts'] == ['cape']
    # Missing limbs still fail as before.
    assert _integrity(_recs([body - {'hand_l'}] * 3))['status'] == 'fail'


def test_validator_warns_when_two_cast_members_share_a_silhouette():
    m = C.compile_plan(demo.plan(), fps=30, width=1080, height=1920)
    same = {'costume': {'hair': 'messy_block', 'hat': None}}
    rep = validate(m, None, characters={'ch_bloxy': same, 'ch_pip': copy.deepcopy(same)})
    assert any(w['code'] == 'same_silhouette' for w in rep['warnings'])
    rep = validate(m, None, characters={c['id']: c['bible'] for c in demo.CHARACTERS})
    assert not any(w['code'] == 'same_silhouette' for w in rep['warnings'])


def test_character_editor_accepts_the_new_costume_items(client):
    body = {'name': 'Dot', 'active': True,
            'bible': {'scale': 0.72, 'palette': {'accessory': '#FF4D8D', 'top2': '#219EBC'},
                      'costume': {'hair': 'pigtails', 'hat': 'bow', 'eyewear': 'glasses', 'top': 'cardigan',
                                  'tie': True, 'badge': 'diamond'}},
            'voice': {'piper_speaker': 12}}
    assert client.put('/api/characters/ch_dot', json=body).status_code == 200
    r = client.get('/api/characters').get_json()
    saved = r['characters']['ch_dot']['bible']
    assert saved['costume'] == body['bible']['costume'] and saved['palette']['accessory'] == '#FF4D8D'
    assert r['vocab']['eyewear'] == [None, 'glasses', 'sunglasses'] and 'crown' in r['vocab']['hats']
    assert 'top2' in r['vocab']['palette_slots']
    body['bible']['costume']['hat'] = 'helmet'
    assert client.put('/api/characters/ch_dot', json=body).status_code == 400
