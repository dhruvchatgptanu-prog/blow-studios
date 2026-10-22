"""First-person story format kit: reference titles from the owner's capture rows, the title closeness check,
twist rotation in the backlog, packaging of the description and tags, and learning features.

All data here is synthetic (no real creator's titles or transcripts); nothing touches the network.
"""
import copy
import difflib
import itertools
import json
import re

import pytest

from blox import demo, learning, pipeline, prefs, repo, videos
from blox.manifest import compile as C
from blox.research import notes
from blox.story import backlog, originality
from blox.util import Blocked, new_id, now
from blox.youtube import publisher

from .helpers import approved_video

OWN_HOOK = 'I built a tower so tall the clouds filed a formal complaint against me that very morning'


def capture_row(i, title, channel='Sample Channel', own=False, **over):
    row = {'id': f'Vid{i:04d}-abc', 'channel': channel, 'handle': '@' + channel.replace(' ', '').lower(),
           'own_channel': own, 'title': title, 'views': 1000 + i, 'likes': 10, 'duration_s': 31.5,
           'mechanism': 'world_status', 'wps_speech': 4.21, 'wps_net': 3.9, 'first_word_s': 0.0,
           'hook_excerpt': f'OTHER CREATOR HOOK {i}', 'ending_excerpt': f'OTHER CREATOR ENDING {i}',
           'cuts_per_10s': 4.2, 'max_shot_s': 2.8, 'transcript': f'FULL TRANSCRIPT TEXT {i}'}
    row.update(over)
    return row


def reference_jsonl():
    rows = [
        capture_row(1, "I Built The World's Biggest Lego Tower 😳", channel='Owner Films', own=True,
                    hook_excerpt=OWN_HOOK, ending_excerpt='So now the tower has its own postcode.'),
        capture_row(2, 'I Lied That I Was A Doctor 😳', channel='Other Creator', mechanism='Impossible Opponent'),
        capture_row(3, 'Views are not a number', views='lots'),                      # skipped: views
        capture_row(4, 'Bad id row', id='bad id!'),                                   # skipped: id
        capture_row(5, "I Changed My Teacher's Password 😳", mechanism='role_swap_or_debt'),  # kept, flagged
        capture_row(6, 'Odd numbers row', duration_s=-4, first_word_s=True, wps_speech=99, max_shot_s='2',
                    mechanism='crime_then_reversal'),
        capture_row(7, 'Views hidden row', views=None, mechanism='Constraint or Curse'),  # kept: views null
    ]
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    lines.append(json.dumps({'note': 'loose object', 'hook_excerpt': 'LOOSE OTHER EXCERPT'}))
    lines.append('{"id": "broken", "hook_excerpt": "BROKEN OTHER EXCERPT", "transcript": "BROKEN TRANSCRIPT", ')
    lines.append('{"id": "cut", "views": 3, "hook_excerpt": "UNTERMINATED OTHER WORDS')
    lines.append('{"id": "obj", "ending_excerpt": {"text": "OBJECT OTHER WORDS", "n": [1, "NESTED OTHER"]}, "x": ')
    return '\n'.join(lines) + '\n'


def story(n, title=None, twist=None, **story_keys):
    p = demo.plan()
    p['title'] = title or f'Kit story {n}'
    p['metadata'] = dict(p.get('metadata') or {}, title=p['title'])
    p['lines'] = [dict(ln, text=['Whoa.', 'Hey!', 'Hmm.', 'Okay.', 'Look!', 'Oh no.', 'Ha!'][i] + f' {n}')
                  for i, ln in enumerate(p['lines'])]
    p['story'] = dict(p['story'], **story_keys)
    if twist:
        p['story']['twist_type'] = twist
    return p


# ---------------------------------------------------------------- reference rows in the notes import
def test_reference_rows_parsed_and_other_creators_excerpts_dropped(db):
    nid = notes.add(reference_jsonl(), title='reference.jsonl', kind='jsonl', d=db)
    row = notes.get(nid, db)
    ex = row['extracted']
    refs = ex['reference_titles']
    ids = ['Vid0001-abc', 'Vid0002-abc', 'Vid0005-abc', 'Vid0006-abc', 'Vid0007-abc']
    assert [r['id'] for r in refs] == ids
    # Skipped and counted: the row whose views are text and the row with a bad id.
    assert ex['reference_titles_count'] == 5 and ex['reference_rows_skipped'] == 2
    own, other, flagged, odd, hidden = refs
    base = {'id', 'channel', 'handle', 'own', 'title', 'views', 'mechanism', 'duration_s', 'wps_speech',
            'first_word_s', 'max_shot_s'}
    assert set(other) == base and set(odd) == base and set(hidden) == base
    assert set(own) == base | {'hook_excerpt', 'ending_excerpt'}
    assert own['own'] is True and other['own'] is False
    assert own['hook_excerpt'] == ' '.join(OWN_HOOK.split()[:12])
    assert other == {'id': 'Vid0002-abc', 'channel': 'Other Creator', 'handle': '@othercreator', 'own': False,
                     'title': 'I Lied That I Was A Doctor 😳', 'views': 1002, 'mechanism': 'impossible_opponent',
                     'duration_s': 31.5, 'wps_speech': 4.21, 'first_word_s': 0.0, 'max_shot_s': 2.8}
    assert odd['duration_s'] is None and odd['first_word_s'] is None and odd['wps_speech'] is None
    assert odd['max_shot_s'] is None
    # The capture tool's mechanism names map to the format's names; one with no counterpart becomes None.
    assert odd['mechanism'] == 'crime_reversal' and hidden['mechanism'] == 'constraint_curse'
    assert flagged['mechanism'] is None
    # A null view count keeps the row, so its title still counts in the title check. A title that trips the
    # injection screen is kept too, marked, for the local check only.
    assert hidden['views'] is None and hidden['title'] == 'Views hidden row'
    assert flagged['flagged'] is True and set(flagged) == base | {'flagged'}
    # Nothing of other creators' excerpts or any transcript is stored, not even in the note text, including from
    # lines that are not valid JSON (an unterminated string, an object value).
    stored = row['text'] + json.dumps(ex)
    for secret in ('OTHER CREATOR HOOK', 'OTHER CREATOR ENDING', 'FULL TRANSCRIPT', 'LOOSE OTHER EXCERPT',
                   'BROKEN OTHER EXCERPT', 'BROKEN TRANSCRIPT', 'UNTERMINATED OTHER WORDS', 'OBJECT OTHER WORDS',
                   'NESTED OTHER'):
        assert secret not in stored, secret
    assert '{"id": "cut", "views": 3, ' in row['text'] and '{"id": "obj", "x":' in row['text']
    assert 'formal complaint' in row['text'] and 'own postcode' in row['text']
    # The listing carries a count only; the rows are read back newest first, one per video id.
    listed = notes.list_notes(db)[0]['extracted']
    assert 'reference_titles' not in listed and listed['reference_titles_count'] == 5
    assert [r['id'] for r in notes.reference_titles(db)] == ids


def test_scrub_removes_words_only(db):
    # An ordinary research note: a subscriber count and timing-only segments are not transcripts and stay as written.
    plain = json.dumps({'channel': 'X', 'subs': 1200000, 'segments': [{'name': 'hook', 'start_s': 0, 'end_s': 2.2}],
                        'hook_excerpt': 12})
    for kind in ('json', 'jsonl'):
        row = notes.get(notes.add(plain, kind=kind, d=db), db)
        assert row['text'] == plain and 'reference_titles' not in row['extracted']
    # Values that carry words go, whatever their shape: a string, a list, cue objects, a language map.
    worded = {'channel': 'X', 'transcript': ['LIST WORDS'], 'segments': [{'start': 0, 'text': 'CUE WORDS'}],
              'captions': {'en': 'MAP WORDS'}, 'subtitles': {'en': [{'segs': [{'utf8': 'DEEP WORDS'}]}]},
              'cues': [[0.5, 'PAIR WORDS']], 'keep': 'ordinary text'}
    row = notes.get(notes.add(json.dumps(worded), kind='json', d=db), db)
    assert json.loads(row['text']) == {'channel': 'X', 'keep': 'ordinary text'}
    # An own row keeps its excerpt (cut to 12 words) but never a transcript.
    own = {'own_channel': True, 'hook_excerpt': OWN_HOOK, 'transcript': 'OWN TRANSCRIPT'}
    row = notes.get(notes.add(json.dumps(own), kind='jsonl', d=db), db)
    assert json.loads(row['text']) == {'own_channel': True, 'hook_excerpt': ' '.join(OWN_HOOK.split()[:12])}


def test_reference_rows_capped_read_before_text_cap_and_json_lists(db, clock):
    rows = [capture_row(i, f'Generated title number {i}') for i in range(notes.REFERENCE_ROWS_MAX + 5)]
    data = '\n'.join(json.dumps(r) for r in rows).encode()
    assert len(data) > notes.TEXT_CAP  # rows come from the whole upload, not the size-capped stored text
    nid = notes.add_file('reference.jsonl', data, db)[0]
    ex = notes.get(nid, db)['extracted']
    assert ex['reference_titles_count'] == notes.REFERENCE_ROWS_MAX and ex['reference_rows_skipped'] == 5
    assert len(notes.get(nid, db)['text']) <= notes.TEXT_CAP
    clock.advance(5)
    newer = [capture_row(1, 'Generated title number 1, renamed'), capture_row(9001, 'A new video')]
    notes.add_file('reference.json', json.dumps({'rows': newer}).encode(), db)
    titles = {r['id']: r['title'] for r in notes.reference_titles(db)}
    assert titles['Vid0001-abc'] == 'Generated title number 1, renamed' and titles['Vid9001-abc'] == 'A new video'
    assert len(titles) == notes.REFERENCE_ROWS_MAX + 1
    # Ordinary notes are untouched: no reference rows are invented.
    other = notes.add(json.dumps({'id': 'Pp7Qq8-Rr9S', 'title': 'Zipped'}), kind='jsonl', d=db)
    assert 'reference_titles' not in notes.get(other, db)['extracted']


def test_deeply_nested_or_broken_json_is_stored_without_failing(db):
    deep = '[' * 50000 + ']' * 50000
    nid = notes.add(deep, kind='json', d=db)
    assert 'reference_titles' not in notes.get(nid, db)['extracted']
    nid = notes.add(deep + '\n' + json.dumps(capture_row(1, 'A fine row')), kind='jsonl', d=db)
    ex = notes.get(nid, db)['extracted']
    assert ex['reference_titles_count'] == 1 and ex['reference_titles'][0]['title'] == 'A fine row'
    nid = notes.add('{"title": "x", "hook_excerpt": "BROKEN OTHER EXCERPT", ', kind='json', d=db)
    assert 'BROKEN OTHER EXCERPT' not in notes.get(nid, db)['text']
    # Broken JSON over several lines: a list value spanning lines and a string never closed are both cut out.
    broken = '{\n "title": "x",\n "transcript": [\n  "MULTI LINE WORDS"\n ],\n "ok": 1,\n "hook_excerpt": "OPEN WORDS'
    text = notes.get(notes.add(broken, kind='json', d=db), db)['text']
    assert 'MULTI LINE WORDS' not in text and 'OPEN WORDS' not in text and '"ok": 1' in text
    huge = '{"id": "Big1", "title": "Huge views", "channel": "C", "views": 1%s, "duration_s": NaN}' % ('0' * 400)
    ex = notes.get(notes.add(huge, kind='jsonl', d=db), db)['extracted']
    assert ex['reference_titles'] == [] and ex['reference_rows_skipped'] == 1


# ---------------------------------------------------------------- title closeness
REFS = [{'id': 'a', 'title': 'I Lied That I Was A Doctor 😳', 'channel': 'Other Creator', 'own': False},
        {'id': 'b', 'title': "I Built The World's Biggest Lego Tower 😳", 'channel': 'Owner Films', 'own': True},
        {'id': 'c', 'title': 'My Dog Became The Mayor', 'channel': 'Third Channel', 'own': False},
        {'id': 'd', 'title': 'I Robbed A Bank 😳', 'channel': 'Fourth Channel', 'own': False}]

# The owner's capture tool's title check (capture_analyse.py check_title), restated: difflib ratio over the characters
# of the lower-cased words joined by spaces, and word Jaccard; 0.6 or more against another creator is too close.
_TOOL_EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿⭐⭕]')
_TOOL_WORD = re.compile(r"[a-z0-9']+")


def capture_tool_score(title, other):
    a, b = (_TOOL_WORD.findall(_TOOL_EMOJI.sub(' ', t.lower())) for t in (title, other))
    ratio = difflib.SequenceMatcher(None, ' '.join(a), ' '.join(b)).ratio()
    return max(ratio, len(set(a) & set(b)) / max(1, len(set(a) | set(b))))


FORMAT_TITLES = ['I Lied That I Was A Doctor 😳', 'I Lied That I Was A Pilot 😳', 'I Robbed A Bank 😳',
                 'I Robbed A Shop 😳', "I Ate The World's Biggest Burger 😳", "I Built The World's Biggest Lego Tower 😳",
                 "I Became The World's Strongest Baby 😳", 'I Stole A Police Car And The Cop Thanked Me 😳',
                 'I Stole A Fire Truck And The Chief Hired Me 😳', 'My Dog Became The Mayor', 'My Cat Became The Mayor',
                 'I Paid My Rent In Pennies And The Landlord Cried 😳', 'I Sold My Neighbour A Haunted Fridge 😳',
                 "I Changed My Teacher's Password 😳", 'I Refused To Leave The Elevator For A Week 😳',
                 'Every Time I Sneezed A Cat Appeared 😳', 'I Only Spoke In Questions For A Day 😳',
                 'I Became The World’s Strongest Dad 😳', 'I Opened A Café On The Moon 😳',
                 "Dads' Night Out Went Wrong 😳", 'I Spent $1,000,000 On Gum 😳', 'I opened a cafe on the moon']


def test_title_check_scores():
    same = originality.title_check("I lied that I was a doctor!! 😳", refs=REFS)
    assert same['score'] == 1.0 and same['blocked'] is True
    assert same['closest'] == {'title': 'I Lied That I Was A Doctor 😳', 'channel': 'Other Creator', 'own': False}
    assert 'too close' in same['reason'] and 'Other Creator' in same['reason']
    # A one-noun swap of a short title is a copy: every word counts, the small ones (I, a, that, was) included.
    pilot = originality.title_check('I Lied That I Was A Pilot 😳', refs=REFS)
    assert pilot['blocked'] is True and pilot['score'] == 0.863 and pilot['closest']['channel'] == 'Other Creator'
    shop = originality.title_check('I Robbed A Shop 😳', refs=REFS)
    assert shop['blocked'] is True and shop['score'] == 0.75 and shop['closest']['channel'] == 'Fourth Channel'
    unrelated = originality.title_check('I Sold My Neighbour A Haunted Fridge And He Thanked Me 😳', refs=REFS)
    assert unrelated['score'] < 0.4 and unrelated['blocked'] is False and not unrelated['note']
    own = originality.title_check("I Built The World's Biggest Lego Castle 😳", refs=REFS)
    assert own['blocked'] is False and own['score'] >= 0.6
    assert own['closest']['own'] is True and own['note'].startswith('A variation of your own video')
    cat = originality.title_check('My Cat Became The Mayor', refs=REFS)
    assert cat['blocked'] is True and cat['closest']['channel'] == 'Third Channel'
    assert originality.title_check('', refs=REFS) == {'score': 0.0, 'closest': None, 'blocked': False, 'note': '',
                                                     'reason': '', 'threshold': 0.6}
    assert originality.title_check('Anything at all', refs=[])['closest'] is None
    worst = originality.titles_check(['Something Original', 'I Lied That I Was A Doctor'], refs=REFS)
    assert worst['blocked'] is True
    assert originality.title_words("World's Strongest Dads 😳!") == ['worlds', 'strongest', 'dads']
    assert originality.title_words('I Lied That I Was A Doctor 😳') == ['i', 'lied', 'that', 'i', 'was', 'a', 'doctor']


def test_title_check_blocks_whatever_the_capture_tool_blocks():
    # Every pair of format titles scores at least what the owner's capture tool gives, so a title his tool calls
    # too close is blocked here too. The shared grammar alone can be enough, as in his tool.
    for a, b in itertools.permutations(FORMAT_TITLES, 2):
        ours, tool = originality.title_similarity(a, b), capture_tool_score(a, b)
        assert ours >= tool - 1e-9, (a, b, ours, tool)
        refs = [{'id': 'x', 'title': b, 'channel': 'Other Creator', 'own': False}]
        assert originality.title_check(a, refs=refs)['blocked'] is (ours >= 0.6)
    assert capture_tool_score("I Became The World's Strongest Baby", "I Ate The World's Biggest Burger") > 0.6
    assert originality.title_check("I Became The World's Strongest Baby 😳", refs=[
        {'id': 'x', 'title': "I Ate The World's Biggest Burger 😳", 'channel': 'Other Creator'}])['blocked'] is True
    assert originality.title_similarity("I Lied That I Was A Doctor", "I Lied That I Was A Pilot") == pytest.approx(
        capture_tool_score("I Lied That I Was A Doctor", "I Lied That I Was A Pilot"))
    # Titles with no Latin letters or digits: the capture tool compares two empty strings (a score of 1); here
    # only their own words count.
    assert capture_tool_score('Я ограбил банк', 'Я съел торт') == 1.0
    assert originality.title_similarity('Я ограбил банк', 'Я съел торт') < 0.4
    assert originality.title_similarity('Я ограбил банк', 'я ограбил банк!') == 1.0
    # The pruned search over many reference titles finds the same best score as checking each one.
    refs = [{'id': str(i), 'title': t, 'channel': 'C', 'own': False} for i, t in enumerate(FORMAT_TITLES)]
    for t in ('I Lied That I Was A Nurse', 'My Hamster Became The Mayor', 'Totally Different Words Here'):
        best = max(originality.title_similarity(t, r['title']) for r in refs)
        assert originality.title_check(t, refs=refs)['score'] == round(best, 3)


def test_title_check_reads_imported_references_and_endpoint(db, client):
    notes.add(reference_jsonl(), kind='jsonl', d=db)
    assert originality.title_check('I Lied That I Was A Doctor', db)['blocked'] is True
    r = client.get('/api/title-check', query_string={'title': 'I lied that I was a doctor 😳'})
    body = r.get_json()
    assert r.status_code == 200 and body['blocked'] is True and body['references'] == 5
    assert body['closest']['channel'] == 'Other Creator'
    # A flagged reference title still counts in the check.
    flagged = originality.title_check("I Changed My Teacher's Password Again 😳", db)
    assert flagged['blocked'] is True and flagged['closest']['title'] == "I Changed My Teacher's Password 😳"
    r = client.get('/api/title-check', query_string={'title': "I Built The World's Biggest Lego Tower"})
    assert r.get_json()['blocked'] is False and 'your own video' in r.get_json()['note']
    assert client.get('/api/title-check?title=').status_code == 400
    assert client.get('/api/title-check', query_string={'title': 'x' * 301}).status_code == 400
    from blox.web.app import create_app
    anon = create_app().test_client()
    assert anon.get('/api/title-check?title=hello').status_code == 401


# ---------------------------------------------------------------- backlog: title rejection, rotation, batch warning
def test_backlog_rejects_close_title_and_notes_own_variation(db):
    p = prefs.get(db)
    notes.add(reference_jsonl(), kind='jsonl', d=db)
    res = backlog.add([story(1, title='I Lied That I Was A Doctor'),
                       story(2, title="I Built The World's Biggest Lego Tower"),
                       story(3, title='Kit story three')], 'claude', p, db)
    assert [r['status'] for r in res] == ['rejected', 'ready', 'ready']
    assert 'too close' in res[0]['reason'] and 'I Lied That I Was A Doctor 😳' in res[0]['reason']
    assert res[0]['closest'] == {'title': 'I Lied That I Was A Doctor 😳', 'channel': 'Other Creator',
                                 'own': False}
    assert res[1]['note'].startswith('A variation of your own video')
    assert backlog.get(res[1]['id'], db)['validation']['warnings'][0] == res[1]['note']
    assert 'note' not in res[2] and 'warnings' not in res[2]


def test_backlog_batch_warns_on_repeated_twist(db):
    p = prefs.get(db)
    twists = ['backfire', 'role_flip', 'backfire', 'scale_flip', 'hidden_reason', 'karma_loop', 'role_flip',
              'scale_flip']
    res = backlog.add([story(i, twist=t) for i, t in enumerate(twists)], 'claude', p, db)
    assert all(r['status'] == 'ready' for r in res)
    warned = [i for i, r in enumerate(res) if r.get('warnings')]
    # backfire at 0 and 2; role_flip at 1 and 6 (5 apart: fine); scale_flip at 3 and 7 (within 5).
    assert warned == [2, 7]
    assert 'backfire' in res[2]['warnings'][0] and '"Kit story 0"' in res[2]['warnings'][0]
    assert backlog.get(res[0]['id'], db)['validation']['story'] == {'twist_type': 'backfire'}


def test_backlog_batch_warns_on_repeated_mechanism(db):
    p = prefs.get(db)
    mechs = ['identity_lie', 'world_status', 'identity_lie', 'supernatural', 'absurd_belief', 'mild_mischief',
             'world_status']
    twists = ['backfire', 'role_flip', 'scale_flip', 'hidden_reason', 'karma_loop', 'lie_becomes_true', 'backfire']
    res = backlog.add([story(10 + i, twist=t, mechanism=m) for i, (m, t) in enumerate(zip(mechs, twists))],
                      'claude', p, db)
    assert all(r['status'] == 'ready' for r in res)
    # identity_lie at 0 and 2 (within 5): a warning; world_status at 1 and 6 (5 apart) and backfire at 0 and 6: none.
    assert [i for i, r in enumerate(res) if r.get('warnings')] == [2]
    assert res[2]['warnings'] == [
        'Mechanism "identity_lie" repeats within 5 stories in a row (also in "Kit story 10"). A mix of mechanisms '
        'keeps the channel varied; reorder the batch or change one story if this was not meant.']
    both = backlog.add([story(30, twist='backfire', mechanism='supernatural'),
                        story(31, twist='backfire', mechanism='supernatural')], 'claude', p, db)
    assert [w.split(' "')[0] for w in both[1]['warnings']] == ['Twist type', 'Mechanism']


def _produced(db, twist, clock):
    clock.advance(10)
    vid = videos.create('Produced', 'autopilot', status='scripted', d=db)
    repo.save_manifest(vid, {'story': {'twist_type': twist}}, 'backlog', d=db)
    return vid


def test_take_rotates_twist_types_and_falls_back(db, clock):
    p = prefs.get(db)
    for t in ['backfire', 'role_flip', 'scale_flip', 'hidden_reason', 'karma_loop']:
        _produced(db, t, clock)
    for n, twist in ((1, 'backfire'), (2, 'role_flip'), (3, 'lie_becomes_true'), (4, None)):
        clock.advance(1)
        backlog.add([story(n, twist=twist)], 'claude', p, db)
    assert backlog.recent_twist_types(db) == ['karma_loop', 'hidden_reason', 'scale_flip', 'role_flip', 'backfire']
    a = backlog.take('x1', db)
    assert a['title'] == 'Kit story 3' and 'not used' in a['rotation']
    assert backlog.take('x2', db)['title'] == 'Kit story 4'  # no twist type: always eligible
    c = backlog.take('x3', db)
    assert c['title'] == 'Kit story 1' and c['rotation'] == 'oldest ready story'  # all twists recent: oldest
    # Story 2's twist (role_flip) is still recent, but it is the only story left, so it is taken anyway.
    assert backlog.take('x4', db)['title'] == 'Kit story 2'
    assert backlog.take('x5', db) is None


def test_take_counts_claimed_backlog_stories_and_skips_cancelled(db, clock):
    p = prefs.get(db)
    backlog.add([story(1, twist='backfire')], 'claude', p, db)
    clock.advance(1)
    backlog.add([story(2, twist='backfire')], 'claude', p, db)
    clock.advance(1)
    backlog.add([story(3, twist='role_flip')], 'claude', p, db)
    v1 = videos.create('v1', 'autopilot', status='researched', d=db)
    assert backlog.take(v1, db)['title'] == 'Kit story 1'
    clock.advance(10)
    v2 = videos.create('v2', 'autopilot', status='researched', d=db)
    # v1 has no manifest yet but took a "backfire" story, so it counts.
    assert backlog.recent_twist_types(db, exclude=v2) == ['backfire']
    assert backlog.take(v2, db)['title'] == 'Kit story 3'
    db.execute("UPDATE videos SET status='cancelled' WHERE id=?", (v1,))
    assert backlog.recent_twist_types(db) == ['role_flip']


# ---------------------------------------------------------------- development: title screen and stored story fields
def _develop(db, vid, steps=4):
    from blox.tasks import ensure_loaded
    ensure_loaded()
    for _ in range(steps):
        v = videos.get(vid, db)
        if v['status'] in ('scripted', 'storyboarded') or v['status'] in videos.HOLDS:
            break
        pipeline.develop_step(v, prefs.get(db), db, repo.characters(d=db))
    return videos.get(vid, db)


def test_development_stores_story_fields(db):
    p = prefs.get(db)
    backlog.add([story(7, mechanism='everyday_accident', mode='grounded', twist='backfire', loop_type='callback')],
                'claude', p, db)
    vid = videos.create('Untitled (researching)', 'autopilot', d=db)
    v = _develop(db, vid)
    assert v['status'] == 'scripted'
    fs = v['metadata']['format_story']
    assert {k: fs[k] for k in ('mechanism', 'mode', 'twist_type', 'loop_type')} == {
        'mechanism': 'everyday_accident', 'mode': 'grounded', 'twist_type': 'backfire', 'loop_type': 'callback'}
    assert fs['logline'] == demo.plan()['logline']
    assert v['metadata']['title_check']['blocked'] is False
    feats = learning.video_features(db, vid)
    assert feats['mechanism'] == 'everyday_accident' and feats['twist_type'] == 'backfire'
    assert feats['mode'] == 'grounded' and feats['narration_wps'] > 0


def test_development_holds_a_title_close_to_another_creator(db, clock):
    p = prefs.get(db)
    backlog.add([story(8, title='I Lied That I Was A Doctor')], 'claude', p, db)
    clock.advance(5)
    notes.add(reference_jsonl(), kind='jsonl', d=db)  # imported after the story was accepted
    vid = videos.create('Untitled (researching)', 'autopilot', d=db)
    with pytest.raises(Blocked, match='too close.*Reject this story on the Story backlog page'):
        _develop(db, vid)
    v = videos.get(vid, db)
    assert v['metadata']['title_check']['blocked'] is True and v['manifest_id'] is None


# ---------------------------------------------------------------- packaging
LOGLINE = ('I paid a whole year of rent in pennies. The landlord did not see it coming, and neither did the bank '
           'manager who had to count them.')


def _first_person_video(db, description='', tags=None, where='stored', logline=LOGLINE):
    md = {'publish_metadata': {'title': 'I Paid My Rent In Pennies And The Landlord Cried 😳',
                               'description': description, 'tags': ['rent', 'Landlord Story', 'shorts', 'funny']
                               if tags is None else tags}}
    if where == 'stored':
        md['format_story'] = {'format': 'first_person_story', 'logline': logline, 'twist_type': 'backfire'}
    elif where == 'plan':
        md['plan'] = {'format': 'first_person_story', 'logline': logline}
    vid = videos.create('Pennies', 'manual', status='approved', d=db, metadata=md)
    if where == 'manifest':
        repo.save_manifest(vid, {'format': 'first_person_story', 'logline': logline, 'story': {}}, 'owner_edit', d=db)
    return videos.get(vid, db)


def test_first_person_packaging_exact(db):
    p = prefs.get(db)
    footer = p['publishing']['description_footer'].strip()
    for where in ('stored', 'plan', 'manifest'):
        v = _first_person_video(db, where=where)
        md = publisher.build_metadata(v, 1_800_000_000, p, db)
        desc = md['snippet']['description']
        assert desc == ('I paid a whole year of rent in pennies.\n\nWhat would you have done? 😳\n\n'
                        '#shorts #rent #landlordstory #funny\n\n' + footer), where
        assert desc.lower().count('#shorts') == 1
        assert md['snippet']['title'] == 'I Paid My Rent In Pennies And The Landlord Cried 😳'
        assert md['snippet']['tags'] == ['rent', 'Landlord Story', 'shorts', 'funny', 'roblox', 'animation', 'obby',
                                         'story', 'funny animation', 'storytime', 'animated story']
        assert sum(len(t) + 2 for t in md['snippet']['tags']) <= 450


def test_first_person_packaging_edges(db):
    p = prefs.get(db)
    footer = p['publishing']['description_footer'].strip()
    # No topic tags: topped up to three hashtags from the format's own tags.
    v = _first_person_video(db, tags=[], logline='x ' * 200)
    desc = publisher.build_metadata(v, 1_800_000_000, p, db)['snippet']['description']
    teaser, cta, tags_line = desc.split('\n\n')[:3]
    assert len(teaser) <= 140 and teaser.endswith('…')
    assert tags_line == '#shorts #funnyanimation #storytime'
    # Five hashtags at most; a written description is kept and its own #shorts is not repeated.
    assert publisher.hashtags(['a b', 'c', 'd', 'e', 'f', 'g']) == ['#shorts', '#ab', '#c', '#d', '#e']
    v = _first_person_video(db, description='My own words. #Shorts')
    desc = publisher.build_metadata(v, 1_800_000_000, p, db)['snippet']['description']
    assert desc == 'My own words. #Shorts\n\n' + footer
    # The question comes from the publishing setting; an empty setting leaves it out.
    p2 = prefs.update({'publishing': {'story_cta': 'Would you have paid?'}}, db)
    v = _first_person_video(db)
    assert '\n\nWould you have paid?\n\n' in publisher.build_metadata(v, 1_800_000_000, p2, db)['snippet']['description']
    p3 = prefs.update({'publishing': {'story_cta': '  '}}, db)
    desc = publisher.build_metadata(_first_person_video(db), 1_800_000_000, p3, db)['snippet']['description']
    assert desc.startswith('I paid a whole year of rent in pennies.\n\n#shorts #rent')
    # Huge tag lists still respect the 450-character budget.
    v = _first_person_video(db, tags=['tag%d' % i for i in range(200)])
    assert sum(len(t) + 2 for t in publisher.build_metadata(v, 1_800_000_000, p, db)['snippet']['tags']) <= 450


def test_other_formats_keep_todays_description(db):
    p = prefs.get(db)
    v = videos.get(approved_video(db), db)
    md = publisher.build_metadata(v, 1_800_000_000, p, db)
    assert md['snippet']['description'] == ('An original animated story.\n\n' + p['publishing']['description_footer']
                                            + '\n\n#Shorts')
    assert md['snippet']['tags'] == ['story', 'roblox', 'animation', 'shorts', 'obby']


def test_story_cta_pref_validation(db):
    assert prefs.get(db)['publishing']['story_cta'] == 'What would you have done? 😳'
    with pytest.raises(ValueError):
        prefs.update({'publishing': {'story_cta': 'x' * 201}}, db)
    with pytest.raises(ValueError):
        prefs.update({'publishing': {'story_cta': 'two\nlines'}}, db)
    with pytest.raises(ValueError):
        prefs.update({'publishing': {'story_cta': 5}}, db)
    assert prefs.update({'publishing': {'story_cta': '  Your move?  '}}, db)['publishing']['story_cta'] == 'Your move?'


# ---------------------------------------------------------------- learning
def _manifest(db, **story_keys):
    p = prefs.get(db)
    m = C.compile_plan(demo.plan(), fps=30, width=1080, height=1920,
                       characters=repo.characters(active_only=False, d=db), **C.pace_kwargs(p))
    m['format'] = 'first_person_story'
    m['story'] = dict(m['story'], **story_keys)
    return m


def _analysed(db, mech, views, avp=50.0, **story_keys):
    vid = videos.create('Published', 'autopilot', status='published', d=db)
    repo.save_manifest(vid, _manifest(db, mechanism=mech, **story_keys), 'backlog', d=db)
    db.execute('INSERT INTO analytics_snapshots(id, youtube_video_id, video_id, fetched_at, start_date, end_date, '
               'metrics) VALUES (?,?,?,?,?,?,?)', (new_id('an_'), 'yt' + vid, vid, now(), '2026-10-01', '2026-10-20',
                                                  json.dumps({'views': views, 'averageViewPercentage': avp})))
    return vid


def test_video_features_include_format_fields_and_narration_pace(db):
    vid = _analysed(db, 'impossible_opponent', 1000, mode='world_scale', twist_type='scale_flip')
    f = learning.video_features(db, vid)
    assert f['format'] == 'first_person_story' and f['mechanism'] == 'impossible_opponent'
    assert f['mode'] == 'world_scale' and f['twist_type'] == 'scale_flip'
    assert f['narration_wps'] > 0 and f['narration_pace'] in ('under 4.0', '4.0-4.45', 'over 4.45')
    # Measured voice durations take precedence over the compiled estimate.
    mf = videos.get(vid, db)['manifest_id']
    for i, (text, secs) in enumerate([('I ate the moon', 1.0), ('It tasted like cheese', 1.0)]):
        db.execute('INSERT INTO audio_lines(id, video_id, manifest_id, line_key, speaker, text, spoken_text, status, '
                   'duration_s, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (new_id('al_'), vid, mf, f'l{i}', 'narrator', text, text, 'done', secs, now()))
    f = learning.video_features(db, vid)
    assert f['narration_wps'] == 4.0 and f['narration_pace'] == '4.0-4.45'
    # Unknown values are ignored; a video without a manifest uses the copy stored at development.
    assert learning.story_fields({'format': 'vlog', 'story': {'mechanism': 'Everyday Accident', 'mode': 'huge'}}) == {
        'mechanism': 'everyday_accident'}
    v2 = videos.create('Stored', 'autopilot', d=db, metadata={'format_story': {'mechanism': 'supernatural',
                                                                               'twist_type': 'role_flip'}})
    assert learning.video_features(db, v2) == {'mechanism': 'supernatural', 'twist_type': 'role_flip'}


def test_findings_report_median_views_per_mechanism(db):
    p = prefs.get(db)
    for v in (1000, 1500, 90000):
        _analysed(db, 'impossible_opponent', v)
    _analysed(db, 'everyday_accident', 300)
    res = learning.update_findings(db, p)
    mv = [r for r in res if r['feature'] == 'mechanism_views'][0]
    assert mv['kind'] == 'hypothesis' and mv['statistic'] == 'median'
    assert mv['groups'] == {'impossible_opponent': {'n': 3, 'median_views': 1500},
                            'everyday_accident': {'n': 1, 'median_views': 300}}
    notes_ = learning.brief_notes(db)
    assert any(n.startswith('Hypothesis (not established): median views by story mechanism: '
                            'impossible_opponent 1,500 (n=3); everyday_accident 300 (n=1)') for n in notes_)
    stored = [r for r in learning.latest(db) if r['feature'] == 'mechanism_views'][0]
    assert stored['metric'] == 'views' and stored['kind'] == 'hypothesis'


def test_mechanism_views_finding_needs_enough_videos():
    data = [{'features': {'mechanism': m}, 'metrics': {'views': v}}
            for m, vs in (('a', [1000, 1100, 1200]), ('b', [10, 20, 30])) for v in vs]
    assert learning.mechanism_views(data, 3)['kind'] == 'finding'
    assert learning.mechanism_views(data, 8)['kind'] == 'hypothesis'
    noisy = copy.deepcopy(data) + [{'features': {'mechanism': 'a'}, 'metrics': {'views': True}},
                                   {'features': {}, 'metrics': {'views': 5}}]
    assert learning.mechanism_views(noisy, 3)['groups']['a']['n'] == 3
    assert learning.mechanism_views([], 3) is None
