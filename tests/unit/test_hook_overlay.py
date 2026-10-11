"""On-screen hook text: the plan field, its window, the top-band layout, face and caption avoidance, the ASS
event that draws it, and the places that describe it (director's script, LLM schema and rules, settings)."""
import copy
import json
import os

import pytest

from blox import captions_layout as CL, demo, prefs as P
from blox.assembly import hook_overlay, write_hook
from blox.manifest import compile as C, director, schema as S, validate as V
from blox.story import generate as G

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
with open(os.path.join(ROOT, 'stories', 'batch-2026-10-11-mainstream.json')) as _f:
    PLANS = json.load(_f)
W, H = 1080, 1920


def prefs(**production):
    p = copy.deepcopy(P.DEFAULTS)
    p['production'].update(production)
    return p


def build(plan=None, p=None, **kw):
    p = p or prefs()
    return C.compile_plan(plan or demo.plan(), fps=30, width=W, height=H, **(C.pace_kwargs(p) | kw))


def test_constants_and_default_setting():
    assert S.HOOK_TEXT_MAX_WORDS == 6 and S.HOOK_POP_FRAMES == 4 and S.HOOK_FADE_FRAMES == 6
    assert S.HOOK_OVERLAY_MAX_STORY_S == 3.75     # 2.5 finished seconds at pace 1.5
    assert P.DEFAULTS['production']['hook_text_overlay'] is True
    assert C.pace_kwargs(P.DEFAULTS)['hook_overlay'] is True
    assert C.pace_kwargs(prefs(hook_text_overlay=False))['hook_overlay'] is False
    bad = prefs(hook_text_overlay='yes')
    with pytest.raises(ValueError):
        P.validate(bad)


def test_plan_field_and_owner_default():
    m = build()
    assert m['hook']['on_screen'] is True and m['hook']['overlay'] == {'start_frame': 0, 'end_frame': 60}
    assert len(C.words(m['hook']['text'])) <= S.HOOK_TEXT_MAX_WORDS
    off = demo.plan()
    off['hook']['on_screen'] = False
    assert build(off)['hook']['on_screen'] is False
    assert build(p=prefs(hook_text_overlay=False))['hook']['on_screen'] is False
    on = demo.plan()
    on['hook']['on_screen'] = True
    assert build(on, p=prefs(hook_text_overlay=False))['hook']['on_screen'] is True
    for plan in [demo.plan(), off, on]:
        assert V.validate(build(plan), prefs())['ok']


def test_window_follows_the_hook_within_bounds():
    plan = demo.plan()
    plan['hook']['t_end'] = 10.0
    assert build(plan, pace=1.0)['hook']['overlay']['end_frame'] == C.fr(3.75, 30)
    assert build(plan, pace=1.5)['hook']['overlay']['end_frame'] == C.fr(2.5, 30)
    plan['hook']['t_end'] = 0.2
    assert build(plan, pace=1.0)['hook']['overlay']['end_frame'] == 30   # a readable minimum
    plan['hook']['t_end'] = 2.0
    assert build(plan, pace=1.0)['hook']['overlay']['end_frame'] == 60


def test_long_hook_text_stays_off_screen_unless_the_plan_insists():
    plan = demo.plan()
    plan['hook']['text'] = 'Bloxy reaches the edge and the next platform is gone.'
    m = build(plan)
    assert m['hook']['on_screen'] is False and any('not shown on screen' in n for n in m['notes'])
    assert V.validate(m, prefs())['ok']
    plan['hook']['on_screen'] = True
    rep = V.validate(build(plan), prefs())
    assert [e['code'] for e in rep['errors']] == ['hook_text_long']
    plan['hook']['text'] = 'Supercalifragilisticexpialidociousness antidisestablishmentarianism'
    rep = V.validate(build(plan), prefs())
    assert 'hook_text_fit' in [e['code'] for e in rep['errors']]


@pytest.mark.parametrize('size', [(1080, 1920), (540, 960), (270, 480)])
def test_every_mainstream_hook_fits_the_band_larger_than_the_captions(size):
    pr = prefs()['production']
    cap = CL.metrics(size[0], size[1], pr)['font_size']
    for plan in PLANS:
        text = plan['hook']['text']
        assert len(text.split()) <= S.HOOK_TEXT_MAX_WORDS, text
        ok, info = CL.hook_fits(text, size[0], size[1], pr)
        assert ok, (text, info)
        lines, mt, _ = CL.hook_wrap(text, size[0], size[1], pr)
        assert 1 <= len(lines) <= CL.HOOK_MAX_LINES and mt['font_size'] >= cap
        assert ' '.join(lines) == ' '.join(text.split())
        band = CL.hook_band(text, size[0], size[1], pr)
        x0, y0, x1, y1 = band['box_norm']
        assert y0 >= pr['safe_area']['top'] - 0.01 and y1 < 0.5 and x0 >= pr['safe_area']['left'] - 0.01
        assert x1 <= 1 - pr['safe_area']['left'] + 0.01


def _tele(face_y, frames, fh=0.15):
    return {f: {'frame': f, 'characters': {'hero': {'face2d': [0.5, face_y], 'face_height_frac': fh, 'face_dot': 0.9,
                                                    'bbox2d': [0.3, face_y - 0.1, 0.7, 1.0]}}} for f in frames}


def test_band_moves_down_to_clear_a_face_and_reports_what_it_could_not_clear():
    pr = prefs()['production']
    text = 'Two doors. One is lava.'
    clear = CL.hook_band(text, W, H, pr, _tele(0.6, range(60)), range(60))
    assert clear['drop'] == 0.0 and clear['face_cover'] == 0
    high = CL.hook_band(text, W, H, pr, _tele(0.2, range(60), fh=0.12), range(60))
    assert high['drop'] > 0 and high['face_cover'] == 0 and high['y'] > clear['y']
    huge = CL.hook_band(text, W, H, pr, _tele(0.3, range(60), fh=0.9), range(60))
    assert huge['face_cover'] > 0   # nothing clears a face that fills the frame; the least-covering band is taken
    # Deterministic: the same inputs give the same band.
    assert CL.hook_band(text, W, H, pr, _tele(0.2, range(60), fh=0.12), range(60)) == high


def test_captions_never_take_the_band_while_it_is_up():
    pr = prefs()['production']
    caps = [{'id': 'c1', 'text': 'Wait what', 'start_frame': 10, 'end_frame': 40},
            {'id': 'c2', 'text': 'Wait what', 'start_frame': 100, 'end_frame': 130}]
    # A face low in the frame pushes both captions to the top...
    tele = _tele(0.7, range(140), fh=0.2)
    assert CL.choose_positions(caps, tele, W, H, pr) == {'c1': 'top', 'c2': 'top'}
    # ...unless the hook band is there: the caption shown during the band stays at the bottom.
    band = CL.hook_band('Two doors. One is lava.', W, H, pr)
    avoid = [{'start_frame': 0, 'end_frame': 60, 'box': band['box_norm']}]
    assert CL.choose_positions(caps, tele, W, H, pr, avoid=avoid) == {'c1': 'bottom', 'c2': 'top'}


def test_hook_ass_event_pops_in_over_four_frames_and_fades_out(tmp_path):
    m = build()
    p = prefs()
    ov = hook_overlay(m, p, {}, W, H, str(tmp_path))
    assert ov['text'] == m['hook']['text'] and ov['end_frame'] == 60 and ov['pop_frames'] == 4
    src = open(ov['file'], encoding='utf-8').read()
    assert f'Style: Hook,{p["production"]["caption_font"]},{ov["font_size"]},' in src and ',8,' in src
    assert ov['font_size'] > CL.metrics(W, H, p['production'])['font_size']
    assert '\\fscx55\\fscy55\\t(0,133,\\fscx100\\fscy100)' in src and '\\fad(66,200)' in src
    assert 'Dialogue: 0,0:00:00.00,0:00:02.00,Hook,,0,0,%d,,' % ov['y'] in src
    assert '\\N'.join(ov['lines']) in src
    # Off screen: nothing to draw.
    off = demo.plan()
    off['hook']['on_screen'] = False
    assert hook_overlay(build(off), p, {}, W, H, str(tmp_path)) is None
    # Deterministic file.
    again = hook_overlay(m, p, {}, W, H)
    assert open(write_hook(m, p, str(tmp_path / 'c.ass'), W, H, again), encoding='utf-8').read() == src


def test_director_script_llm_schema_and_rules_describe_the_hook_text():
    m = build()
    text = director.script(m)
    assert 'On screen 00:00.00–00:02.00: "The next platform is GONE."' in text
    off = demo.plan()
    off['hook']['on_screen'] = False
    assert 'no hook text' in director.script(build(off))
    schema = G.plan_schema(['hero'], ['ch_bloxy'])
    hook = schema['properties']['hook']
    assert hook['properties']['on_screen'] == {'type': 'boolean'} and 'on_screen' in hook['required']
    assert schema['properties']['cover_t'] == {'type': ['number', 'null']}
    rules = ' '.join(G.plan_rules(P.DEFAULTS))
    assert 'at most 6 punchy words' in rules and 'top band' in rules and 'cover' in rules


def test_cover_source_is_recorded():
    assert build()['cover_source'] == 'plan'
    plan = demo.plan()
    plan.pop('cover_t')
    m = build(plan)
    assert m['cover_source'] == 'auto' and 0 <= m['cover_frame'] < m['duration_frames']
