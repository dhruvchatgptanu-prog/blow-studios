"""Production manifest: compilation to authoritative frames, validation and the director's script."""
import copy

import pytest

from blox import demo, prefs
from blox.manifest import compile as C, director, schema as S
from blox.manifest.validate import validate


def build(plan=None, **over):
    p = copy.deepcopy(plan or demo.plan())
    p.update(over)
    return C.compile_plan(p, fps=30, width=1080, height=1920)


def codes(rep):
    return {e['code'] for e in rep['errors']}


@pytest.fixture
def P():
    return copy.deepcopy(prefs.DEFAULTS)


def test_demo_compiles_and_validates(P):
    m = build()
    rep = validate(m, P)
    assert rep['ok'], rep['errors']
    assert rep['unsupported'] == []


def test_frames_are_authoritative_and_beats_cover_timeline():
    m = build()
    D, fps = m['duration_frames'], m['fps']
    assert D == 900
    assert m['beats'][0]['start_frame'] == 0 and m['beats'][-1]['end_frame'] == D
    for a, b in zip(m['beats'], m['beats'][1:]):
        assert a['end_frame'] == b['start_frame']
    for b in m['beats']:
        assert 0 < b['end_frame'] - b['start_frame'] <= fps
        assert b['start_tc'] == C.tc(b['start_frame'], fps)
        # Every beat states both endpoints of every on-screen character's pose.
        for ch in b['characters']:
            for k in ('expression', 'posture', 'position', 'facing', 'head', 'brows', 'eyes', 'mouth', 'arms',
                      'feet', 'eye_target'):
                assert k in ch['start'] and k in ch['end'], (b['id'], ch['id'], k)
        if b['camera']:
            assert {'framing', 'angle', 'side', 'subject'} <= set(b['camera']['start'])
    # Beats split at shot boundaries.
    cuts = {s['start_frame'] for s in m['shots']}
    assert cuts <= {b['start_frame'] for b in m['beats']}


def test_action_phases_are_contiguous():
    m = build()
    for a in m['tracks']['actions']:
        total = a['anticipation_frames'] + a['main_frames'] + a['follow_through_frames'] + a['hold_frames']
        assert a['end_frame'] - a['start_frame'] == total
    seen = 0
    for b in m['beats']:
        for ch in b['characters']:
            for act in ch['actions']:
                ph = act['phases']
                assert [p['phase'] for p in ph][0] in ('anticipation', 'main', 'follow_through', 'hold')
                for x, y in zip(ph, ph[1:]):
                    assert x['end_frame'] == y['start_frame']
                seen += 1
    assert seen


def test_locomotion_end_state_propagates_to_later_keys():
    m = build()
    keys = m['tracks']['characters']['hero']['keys']
    after_jump = [k for k in keys if k['frame'] > int(12.4 * 30) + 40]
    assert after_jump and all(abs(k['pose']['position'][0] - 3.0) < 1e-6 for k in after_jump[:2])


def test_unsupported_action_is_reported_not_faked(P):
    p = demo.plan()
    p['actions'].append({'character': 'hero', 'type': 'backflip', 't': 5.0, 'main_frames': 20})
    rep = validate(build(p), P)
    assert 'unsupported_action' in codes(rep)
    assert {'kind': 'action', 'value': 'backflip'} in rep['unsupported']


def test_teleport_and_impossible_jump(P):
    p = demo.plan()
    p['performance']['hero'].append({'t': 6.0, 'position': [-8.0, 0.0]})
    assert codes(validate(build(p), P)) & {'teleport', 'implausible_speed', 'unsupported_ground'}
    p = demo.plan()
    p['actions'][2]['params']['to'] = [9.5, 0.4]
    assert codes(validate(build(p), P)) & {'implausible_jump', 'unsupported_ground'}


def test_same_body_part_overlap(P):
    p = demo.plan()
    p['actions'].append({'character': 'pip', 'type': 'point', 't': 3.9, 'main_frames': 12, 'params': {'hand': 'left'}})
    assert 'action_overlap' in codes(validate(build(p), P))


def test_get_up_without_falling(P):
    p = demo.plan()
    p['actions'].append({'character': 'pip', 'type': 'get_up', 't': 9.0, 'main_frames': 12})
    assert 'action_sequence' in codes(validate(build(p), P))


def test_same_speaker_overlap_and_fit(P):
    p = demo.plan()
    p['lines'].append({'id': 'l8', 'speaker': 'hero', 'text': 'And another thing!', 't': 0.6, 'emotion': 'startled'})
    assert 'dialogue_overlap' in codes(validate(build(p), P))
    p = demo.plan()
    p['lines'][-1]['text'] = ' '.join(['word'] * 40)
    assert 'dialogue_fit' in codes(validate(build(p), P))


def test_flash_expression_rejected(P):
    p = demo.plan()
    p['performance']['pip'] += [{'t': 9.0, 'expression': 'angry'}, {'t': 9.1, 'expression': 'happy'}]
    assert 'expression_too_short' in codes(validate(build(p), P))


def test_expression_in_wide_shot_is_unreadable(P):
    p = demo.plan()
    p['shots'][0]['camera'].update(framing_start='extreme_wide', framing_end='extreme_wide', move='static')
    assert 'expression_unreadable' in codes(validate(build(p), P))


def test_hook_and_payoff_rules(P):
    p = demo.plan()
    for b in p['beats']:
        if b['purpose'] == 'hook':
            b['purpose'] = 'setup'
    assert 'hook' in codes(validate(build(p), P))
    p = demo.plan()
    for b in p['beats']:
        if b['purpose'] in ('payoff', 'button'):
            b['purpose'] = 'reaction'
    assert 'payoff' in codes(validate(build(p), P))


def test_shot_gap(P):
    p = demo.plan()
    p['shots'][1]['start_s'] += 0.5
    assert 'gap' in codes(validate(build(p), P))


def test_camera_subject_must_exist(P):
    p = demo.plan()
    p['shots'][0]['camera']['subject'] = 'ghost'
    assert codes(validate(build(p), P)) & {'camera_subject', 'enum'}


def test_generative_shot_length_limit(P):
    p = demo.plan()
    p['shots'] = [{'id': 'only', 'start_s': 0, 'end_s': 30, 'renderer': 'runway',
                   'camera': {'subject': 'hero', 'framing_start': 'medium', 'framing_end': 'medium', 'move': 'static'}}]
    assert 'provider_shot_length' in codes(validate(build(p), P))


def test_duration_bounds(P):
    P['production']['min_seconds'] = 40
    assert 'duration' in codes(validate(build(), P))


def test_prop_reach(P):
    p = demo.plan()
    p['actions'].append({'character': 'hero', 'type': 'grab', 't': 4.5, 'main_frames': 12,
                         'params': {'hand': 'left', 'prop': 'flag'}})
    assert codes(validate(build(p), P)) & {'prop_reach', 'prop_contact'}


def test_caption_safe_area(P):
    P['production']['caption_font_size_ratio'] = 0.2
    assert 'caption_safe_area' in codes(validate(build(), P))


def test_director_script_lists_every_beat_with_timecodes():
    m = build()
    text = director.script(m, {'hero': 'Bloxy', 'pip': 'Pip'})
    for b in m['beats']:
        assert b['start_tc'] in text
    assert 'Bloxy' in text and 'frames' in text


def test_measured_timing_replaces_estimates():
    m = build()
    partial = [{'word': 'Wait', 'start': 0.0, 'end': 0.4}]
    m1 = C.update_line_timing(copy.deepcopy(m), {'l1': {'duration_s': 1.66, 'words': partial}})
    assert next(c for c in m1['captions'] if c['line_id'] == 'l1')['timing'] == 'estimated'
    words = [{'word': w, 'start': 0.3 * i, 'end': 0.3 * i + 0.25}
             for i, w in enumerate(['Wait', "where's", 'the', 'next', 'platform'])]
    m2 = C.update_line_timing(m, {'l1': {'duration_s': 1.66, 'words': words}})
    ln = next(x for x in m2['lines'] if x['id'] == 'l1')
    assert ln['measured_frames'] == 50 and ln['est_end_frame'] == ln['start_frame'] + 50
    first = next(c for c in m2['captions'] if c['line_id'] == 'l1')
    assert first['timing'] == 'aligned'


def test_vocabulary_is_explicit():
    assert 'backflip' not in S.ACTIONS
    for a in ('wave', 'jump', 'facepalm', 'turn', 'walk'):
        assert a in S.ACTIONS
