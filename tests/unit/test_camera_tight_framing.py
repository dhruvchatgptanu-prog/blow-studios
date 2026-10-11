"""Close-ups swing toward the side the subject's face points to, so a cheek never fills the frame."""
import json
import os

from blox import demo, prefs as P
from blox.animation import solver as SV
from blox.manifest import compile as C

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BATCH = os.path.join(ROOT, 'stories', 'batch-2026-10-11-mainstream.json')


def _solve(title):
    plan = next(p for p in json.load(open(BATCH)) if p['title'] == title)
    m = C.compile_plan(plan, fps=30, width=540, height=960, **C.pace_kwargs(P.DEFAULTS))
    bibles = {c['id']: next(ch['bible'] for ch in demo.CHARACTERS if ch['id'] == c['character_id']) for c in m['cast']}
    chars = {c['id']: SV.CharacterSolver(m, c['id'], bibles.get(c['id']), m['duration_frames']) for c in m['cast']}
    for cs in chars.values():
        cs.others = chars  # eye targets on other characters resolve only with the cast attached
    return m, chars, SV.solve_camera(m, chars, m['duration_frames'])


def test_every_close_up_in_the_batch_faces_the_subject():
    for plan in json.load(open(BATCH)):
        m, chars, cams = _solve(plan['title'])
        for s in m['shots']:
            subj = s['camera']['subject']
            if subj not in chars or not ({s['camera']['framing_start'], s['camera']['framing_end']} & {'close_up', 'extreme_close_up'}):
                continue
            frames = cams[s['start_frame']:s['end_frame']]
            score = SV._facing_camera_score(frames, chars, subj, s['start_frame'])
            assert score >= 0.35, (plan['title'], s['id'], round(score, 2))


def test_the_cheek_shot_from_the_real_render_now_turns_to_the_face():
    m, chars, cams = _solve(next(p['title'] for p in json.load(open(BATCH)) if p['title'].startswith('Whoever Laughs Loses')))
    s13 = next(s for s in m['shots'] if s['id'] == 's13')
    frames = cams[s13['start_frame']:s13['end_frame']]
    assert frames[0].get('face_turned_deg') is not None
    assert SV._facing_camera_score(frames, chars, s13['camera']['subject'], s13['start_frame']) >= 0.5
