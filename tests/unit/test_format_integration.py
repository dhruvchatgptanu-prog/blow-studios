"""Integration of the first-person format with QA, settings and the other format parts (lead's follow-ups)."""
import copy

import pytest

from blox import prefs as P
from blox.qa import story as QS
from blox.qa.report import Checks, verdict
from tests.unit.test_format_first_person import build


def _consistency(m, monkeypatch, errors):
    monkeypatch.setattr(QS.V, 'validate', lambda *a, **k: {'ok': not errors, 'errors': errors, 'warnings': []})
    ck = Checks(m['fps'])
    QS.manifest_consistency(ck, m, P.DEFAULTS)
    return {c['id']: c for c in ck.items}


def test_voiced_format_timing_is_reported_minor_and_never_blocks(monkeypatch):
    m = build()
    items = _consistency(m, monkeypatch, [
        {'code': 'format_hook_line', 'message': 'The hook line ends at 2.52 s; it must end by 2.4 s'},
        {'code': 'format_mid_gag', 'message': 'The mid-video gag starts at 71% of the video'}])
    assert items['manifest_consistency']['status'] == 'pass'
    assert items['format_rules_voiced']['severity'] == 'minor'
    assert len(items['format_rules_voiced']['evidence']['errors']) == 2
    assert verdict(list(items.values()), P.DEFAULTS)[0] == 'approved'


def test_real_contradictions_still_fail_major_with_a_rewrite(monkeypatch):
    m = build()
    items = _consistency(m, monkeypatch, [
        {'code': 'teleport', 'message': 'hero moves 6 m in one frame'},
        {'code': 'format_hook_line', 'message': 'The hook line ends at 2.52 s'}])
    mc = items['manifest_consistency']
    assert mc['status'] == 'fail' and mc['severity'] == 'major'
    assert mc['repair'] == {'action': 'rewrite_script'}
    assert mc['evidence']['errors'] == ['hero moves 6 m in one frame']
    assert 'format_rules_voiced' in items


def test_the_real_validator_passes_the_fixture_through_qa():
    m = build()
    ck = Checks(m['fps'])
    QS.manifest_consistency(ck, m, P.DEFAULTS)
    items = {c['id']: c for c in ck.items}
    assert items['manifest_consistency']['status'] == 'pass', items['manifest_consistency']['evidence']
    assert 'format_rules_voiced' not in items


def test_a_loop_beat_only_counts_as_the_ending_in_the_first_person_format():
    m = build()
    assert 'loop' in QS.ending_purposes(m)
    other = copy.deepcopy(m)
    other['format'] = 'storytime'
    assert QS.ending_purposes(other) == ('payoff', 'button')
    other.pop('format')
    assert QS.ending_purposes(other) == ('payoff', 'button')


@pytest.mark.parametrize('value,ok', [('', True), ('first_person_story', True), ('skit', True), ('storytime', True),
                                      ('hadel', False), ('<script>', False)])
def test_story_format_setting_is_validated(value, ok):
    p = copy.deepcopy(P.DEFAULTS)
    p['production']['story_format'] = value
    if ok:
        assert P.validate(p)['production']['story_format'] == value
    else:
        with pytest.raises(ValueError):
            P.validate(p)


def test_defaults_never_time_stretch_and_let_the_llm_choose_the_format():
    assert P.DEFAULTS['production']['time_stretch_speech'] is False
    assert P.DEFAULTS['production']['story_format'] == ''


def test_first_person_description_uses_the_teaser_and_the_owners_question(db):
    import json as _json
    from blox.youtube import publisher as PUB
    plan = {'format': 'first_person_story', 'logline': 'Bloxy finds out why she always wins: he narrates out loud.',
            'metadata': {'title': 'I Hid From the Best Seeker on Earth 😳', 'description': '',
                         'teaser': 'She has never lost, and I have lost 400 in a row.',
                         'tags': ['hide and seek', 'funny', 'animation']}}
    v = {'title': plan['metadata']['title'], 'manifest_id': None,
         'metadata': {'plan': plan, 'publish_metadata': plan['metadata'], 'voice_credits': []}}
    p = _json.loads(_json.dumps(P.DEFAULTS))
    p['publishing']['story_cta'] = 'Would you have kept hiding? 😳'
    snippet = PUB.build_metadata(v, 1_900_000_000, p, d=db)['snippet']
    desc = snippet['description']
    assert desc.startswith('She has never lost, and I have lost 400 in a row.\n\nWould you have kept hiding? 😳\n\n#shorts')
    assert 'narrates out loud' not in desc          # the logline (which spoils the twist) is not used
    assert desc.lower().count('#shorts') == 1
    assert '#hideandseek' in desc
    assert {'funny animation', 'storytime', 'animated story'} <= set(snippet['tags'])


def test_the_llm_plan_schema_asks_for_a_teaser_only_in_the_format():
    from blox.story import generate as G
    fp = G.plan_schema(['hero'], ['ch_hero'], 'first_person_story')['properties']['metadata']
    assert 'teaser' in fp['properties'] and 'teaser' in fp['required']
    other = G.plan_schema(['hero'], ['ch_hero'])['properties']['metadata']
    assert 'teaser' not in other['properties']


def test_the_batch_has_teasers_and_empty_descriptions():
    import json as _json
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    batch = _json.load(open(os.path.join(root, 'stories', 'batch-2026-10-22-first-person.json'), encoding='utf-8'))
    for p in batch:
        md = p['metadata']
        assert md['description'] == '' and 0 < len(md['teaser']) <= 140, p['title']
        assert 'shorts' not in [t.lower() for t in md['tags']]
