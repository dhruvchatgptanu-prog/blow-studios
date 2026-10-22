"""Shorts captions: 1-3 word chunks, heavy outline, low-centre placement and the yellow active word."""
import copy
import re

import pytest

from blox import captions_layout as CL, demo, prefs
from blox.assembly import caption_segments, write_captions
from blox.manifest import compile as C

W, H = 1080, 1920


def P():
    return copy.deepcopy(prefs.DEFAULTS)


def build(plan=None, **kw):
    return C.compile_plan(plan or demo.plan(), fps=30, width=W, height=H, **kw)


def test_chunks_are_one_to_three_words_and_cover_every_word():
    m = build()
    for ln in m['lines']:
        caps = [c for c in m['captions'] if c['line_id'] == ln['id']]
        assert ' '.join(c['text'] for c in caps).split() == ln['text'].split()
        for c in caps:
            assert 1 <= len(c['text'].split()) <= 3, c['text']
    assert C.split_caption_groups("Wait... where's the next platform?!") == ["Wait... where's", 'the next', 'platform?!']


def test_every_caption_has_contiguous_word_timing():
    m = build()
    for c in m['captions']:
        ws = c['words']
        assert [w['text'] for w in ws] == c['text'].split()
        assert ws[0]['start_frame'] == c['start_frame'] and ws[-1]['end_frame'] == c['end_frame']
        for a, b in zip(ws, ws[1:]):
            assert a['end_frame'] == b['start_frame'] and a['start_frame'] <= b['start_frame']
    # Longer words take longer to say in the estimate.
    c = next(c for c in m['captions'] if c['text'] == 'platform?!')
    assert len(c['words']) == 1
    c = next(c for c in m['captions'] if c['text'] == "Wait... where's")
    assert c['words'][1]['end_frame'] - c['words'][1]['start_frame'] >= c['words'][0]['end_frame'] - c['words'][0]['start_frame'] - 1


def test_aligned_word_timing_is_used_when_measured():
    m = build()
    ws = [{'word': w, 'start': 0.3 * i, 'end': 0.3 * i + 0.25} for i, w in enumerate(['Wait', "where's", 'the',
                                                                                     'next', 'platform'])]
    m2 = C.update_line_timing(m, {'l1': {'duration_s': 1.6, 'words': ws}})
    caps = [c for c in m2['captions'] if c['line_id'] == 'l1']
    assert all(c['timing'] == 'aligned' for c in caps)
    off = m2['lines'][0]['start_frame']
    assert caps[0]['words'][1]['start_frame'] == off + round(0.3 * 30)    # "where's" starts on its aligned time
    assert caps[1]['words'][1]['start_frame'] == off + round(0.9 * 30)    # "next"


def test_token_spans_share_timing_with_punctuation_tokens():
    spans = C._token_spans(['Oh', '—', 'no!'], [(0, 5), (5, 12)])
    assert spans == [[0, 5], [0, 5], [5, 12]]
    assert C._token_spans(['...', 'hey'], [(3, 9)]) == [[3, 9], [3, 9]]


def test_heavy_outline_and_shadow_are_inside_every_box():
    pr = P()['production']
    mt = CL.metrics(W, H, pr)
    assert mt['outline'] >= mt['font_size'] * 0.15 and mt['shadow'] >= 1
    sa = pr['safe_area']
    for text in ('Wait... where\'s', 'platform?!', 'Unbelievably-long-word'):
        ok, info = CL.fits(text, W, H, pr)
        if not ok:
            continue
        for pos in ('bottom', 'top'):
            x0, y0, x1, y1 = CL.box(text, W, H, pr, pos)[0]
            assert x0 >= sa['left'] * W - 1 and x1 <= sa['right'] * W + 1
            assert y0 >= sa['top'] * H - 1 and y1 <= sa['bottom'] * H + 1
    # Low in the frame: the bottom caption sits just above the Shorts UI zone (the safe area's bottom edge).
    y1 = CL.box('platform?!', W, H, pr)[0][3]
    assert sa['bottom'] * H - 0.03 * H <= y1 <= sa['bottom'] * H


def test_two_line_captions_are_balanced():
    pr = P()['production']
    # Greedy wrapping would strand "the" on the second line.
    lines, _ = CL.wrap("Wait... where's the", W, H, pr)
    assert lines == ['Wait...', "where's the"]


def test_ass_highlights_the_active_word_in_yellow(tmp_path):
    m = build()
    p = P()
    path = str(tmp_path / 'c.ass')
    layout = write_captions(m, p, path, W, H)
    text = open(path, encoding='utf-8').read()
    mt = CL.metrics(W, H, p['production'])
    assert f',{mt["outline"]},{mt["shadow"]},2,' in text  # heavy outline + shadow, bottom-centre alignment
    assert CL.ass_colour(CL.ACTIVE_COLOUR) == '&H000AD6FF' and CL.ass_colour('#000000') == '&H00000000'
    events = [l for l in text.splitlines() if l.startswith('Dialogue:')]
    assert len(events) == sum(len(caption_segments(c)) for c in m['captions'])
    # Each event of a caption highlights exactly one word, and the highlight moves word by word.
    c0 = m['captions'][0]
    ev = events[:len(c0['words'])]
    for i, e in enumerate(ev):
        assert e.count(r'\1c' + CL.ass_colour(CL.ACTIVE_COLOUR)) == 1
        hi = re.search(r'\{\\1c&H000AD6FF&\}([^{]+)\{', e).group(1)
        assert hi == c0['words'][i]['text']
    assert r'\fscx86' in ev[0] and r'\fad(40,0)' in ev[0] and r'\fad(0,40)' in ev[-1]
    assert [x['words'] for x in layout] == [c['words'] for c in m['captions']]


def test_segments_cover_the_caption_without_gaps():
    m = build()
    for c in m['captions']:
        segs = caption_segments(c)
        assert segs[0][0] == c['start_frame'] and segs[-1][1] == c['end_frame']
        for a, b in zip(segs, segs[1:]):
            assert a[1] == b[0]
    legacy = {'start_frame': 10, 'end_frame': 30, 'text': 'old caption'}
    assert caption_segments(legacy) == [(10, 30, 0)]


@pytest.mark.parametrize('ratio', [0.042, 0.06])
def test_caption_safe_area_validation_still_applies(ratio):
    from blox.manifest.validate import validate
    p = P()
    p['production']['caption_font_size_ratio'] = ratio
    assert 'caption_safe_area' not in {e['code'] for e in validate(build(), p)['errors']}
    p['production']['caption_font_size_ratio'] = 0.2
    assert 'caption_safe_area' in {e['code'] for e in validate(build(), p)['errors']}
