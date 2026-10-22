"""The director's script and the silhouette check cope with the mainstream-batch features."""
import copy

from blox import demo
from blox.manifest import compile as C, director, schema as S, validate as V
from blox import prefs as P


def _plan_with_mouth(shape):
    plan = copy.deepcopy(demo.plan())
    keys = plan['performance'][plan['cast'][0]['id']]
    keys[1]['mouth'] = {'shape': shape, 'open': 0.9}
    return plan


def test_directors_script_describes_every_mouth_shape():
    for shape in S.MOUTH_SHAPES:
        m = C.compile_plan(_plan_with_mouth(shape), fps=30, width=540, height=960)
        text = director.script(m, {c['id']: c['id'] for c in m['cast']})
        assert text and 'KeyError' not in text


def test_silhouette_check_reads_costumes_from_character_rows():
    m = C.compile_plan(demo.plan(), fps=30, width=540, height=960)
    rows = {c['id']: {'id': c['id'], 'name': c['name'], 'bible': c['bible'], 'voice': c['voice']} for c in demo.CHARACTERS}
    rep = V.validate(m, P.DEFAULTS, characters=rows)
    assert not [w for w in rep['warnings'] if w['code'] == 'same_silhouette'], rep['warnings']
    twins = copy.deepcopy(rows)
    for r in twins.values():
        r['bible']['costume'] = {'hair': 'bald', 'hat': None, 'eyewear': None, 'top': 'tee', 'tie': False, 'badge': None}
    rep = V.validate(m, P.DEFAULTS, characters=twins)
    assert [w for w in rep['warnings'] if w['code'] == 'same_silhouette']
