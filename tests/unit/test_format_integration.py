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
