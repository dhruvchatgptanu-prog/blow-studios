"""Deterministic validation of a compiled manifest.

A passing manifest is necessary, not sufficient: QA still inspects the rendered
video, because a valid plan does not prove the renderer followed it.
"""
import math

from . import geometry as G, schema as S
from .compile import pose_at, tc, words
from .geometry import camera_azimuth, face_visible

FULL_BODY = {'walk', 'run', 'jump', 'hop', 'crouch', 'stand_up', 'stumble', 'fall_down', 'get_up', 'turn',
             'celebrate', 'flinch', 'cower', 'dance', 'idle'}
HEAD_ONLY = {'nod', 'head_shake', 'look_around'}

# Platform tops used for "is the character standing on something" checks.
GROUND_PRESETS = {'town_street', 'classroom', 'night_forest', 'bedroom', 'studio'}


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.unsupported = []

    def err(self, code, msg, frames=None, fix=None, where=None):
        self.errors.append(self._item(code, msg, frames, fix, where))

    def warn(self, code, msg, frames=None, fix=None, where=None):
        self.warnings.append(self._item(code, msg, frames, fix, where))

    @staticmethod
    def _item(code, msg, frames, fix, where):
        return {'code': code, 'message': msg, 'frames': list(frames) if frames else None, 'fix': fix, 'where': where}

    def as_dict(self, stats):
        return {'ok': not self.errors, 'errors': self.errors, 'warnings': self.warnings,
                'unsupported': self.unsupported, 'stats': stats}


def _in(v, allowed):
    return v in allowed


def _range(r, path, value, lo, hi, where):
    if not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        r.err('out_of_range', f'{path} = {value!r} is outside {lo}..{hi}', where=where)


def validate(m, prefs=None, characters=None, measured=False):
    """Return a report dict. ``characters`` maps character_id -> bible (optional)."""
    r = Report()
    fps = m.get('fps')
    D = m.get('duration_frames', 0)
    pr = (prefs or {}).get('production', {}) if prefs else {}
    min_s, max_s = pr.get('min_seconds', 5), pr.get('max_seconds', 180)
    min_expr = pr.get('min_expression_frames', 8)
    if fps not in (24, 25, 30, 60):
        r.err('fps', f'Unsupported fps {fps}')
        return r.as_dict({})
    if not (min_s * fps <= D <= max_s * fps):
        r.err('duration', f'Duration {D / fps:.2f}s is outside the configured {min_s}-{max_s}s',
              fix='Adjust duration_s or the length settings')
    _structure(r, m)
    if r.errors:
        return r.as_dict({'duration_s': round(D / fps, 3)})
    _timeline(r, m)
    _actions(r, m)
    _dialogue(r, m, measured)
    _physics(r, m)
    _expressions(r, m, min_expr)
    _continuity(r, m)
    _captions(r, m, prefs)
    _story(r, m)
    stats = {
        'duration_s': round(D / fps, 3), 'frames': D, 'shots': len(m['shots']), 'beats': len(m['beats']),
        'lines': len(m['lines']), 'words': sum(len(words(l['text'])) for l in m['lines']),
        'actions': len(m['tracks']['actions']), 'captions': len(m['captions']),
        'renderers': sorted({s['renderer'] for s in m['shots']}),
    }
    return r.as_dict(stats)


def _structure(r, m):
    cast_ids = []
    for c in m.get('cast', []):
        if not isinstance(c, dict) or not c.get('id'):
            r.err('cast', 'Every cast member needs an id')
            continue
        if c['id'] in cast_ids:
            r.err('cast', f'Duplicate cast id {c["id"]}')
        cast_ids.append(c['id'])
    if not cast_ids and any(s.get('renderer') != 'clip' for s in m.get('shots', [])):
        r.err('cast', 'At least one character is required for animated shots')
    st = m.get('setting', {})
    if st.get('preset') not in S.SETTING_PRESETS:
        r.err('unsupported_setting', f'Setting preset {st.get("preset")!r} is not available',
              fix='Use one of: ' + ', '.join(S.SETTING_PRESETS))
        r.unsupported.append({'kind': 'setting', 'value': st.get('preset')})
    if st.get('time_of_day') not in S.TIME_OF_DAY:
        r.err('enum', f'time_of_day {st.get("time_of_day")!r} unknown')
    if st.get('lighting') not in S.LIGHTING:
        r.err('enum', f'lighting {st.get("lighting")!r} unknown')
    prop_ids = set()
    for p in st.get('props', []):
        if not p.get('id') or p['id'] in prop_ids:
            r.err('props', f'Prop ids must be unique and present ({p.get("id")!r})')
        prop_ids.add(p.get('id'))
        if p.get('type') not in S.PROP_TYPES:
            r.err('unsupported_prop', f'Prop type {p.get("type")!r} is not in the asset library', where=p.get('id'))
            r.unsupported.append({'kind': 'prop', 'value': p.get('type')})
        for v in p['position']:
            if abs(v) > S.WORLD_LIMIT:
                r.err('out_of_range', f'Prop {p.get("id")} is outside the {S.WORLD_LIMIT} m world')
    shot_ids = set()
    for s in m.get('shots', []):
        if s['id'] in shot_ids:
            r.err('shots', f'Duplicate shot id {s["id"]}')
        shot_ids.add(s['id'])
        if s['renderer'] not in ('blender', 'runway', 'clip'):
            r.err('renderer', f'Unknown renderer {s["renderer"]!r} in shot {s["id"]}')
        if s['renderer'] == 'clip' and not (s.get('clip') or {}).get('asset'):
            r.err('clip', f'Shot {s["id"]} uses footage but no clip asset is selected')
        cam = s['camera']
        if s['renderer'] == 'clip':
            continue
        for k, allowed in (('framing_start', S.FRAMINGS), ('framing_end', S.FRAMINGS), ('angle', S.CAMERA_ANGLES),
                           ('side', S.CAMERA_SIDES), ('move', S.CAMERA_MOVES), ('ease', S.EASES)):
            if not _in(cam.get(k), allowed):
                r.err('unsupported_camera', f'Shot {s["id"]}: camera {k} {cam.get(k)!r} is not supported',
                      fix='Use one of: ' + ', '.join(allowed))
                r.unsupported.append({'kind': 'camera.' + k, 'value': cam.get(k)})
        subj = cam.get('subject')
        if subj not in cast_ids and subj != 'two_shot' and not (isinstance(subj, str) and subj.startswith('prop:')
                                                                  and subj[5:] in prop_ids):
            r.err('camera_subject', f'Shot {s["id"]}: camera subject {subj!r} does not exist')
        if s.get('transition_in') not in S.TRANSITIONS:
            r.err('enum', f'Shot {s["id"]}: transition {s.get("transition_in")!r} unknown')
        _range(r, 'camera.shake', cam.get('shake', 0), 0, 1, s['id'])
    for cid, tr in m['tracks']['characters'].items():
        if cid not in cast_ids:
            r.err('track', f'Track for unknown character {cid}')
        for k in tr['keys']:
            _pose(r, k['pose'], f'{cid}@{tc(k["frame"], m["fps"])}', prop_ids, cast_ids)
    for a in m['tracks']['actions']:
        if a['character'] not in cast_ids:
            r.err('action', f'Action {a["id"]} refers to unknown character {a["character"]!r}')
        if a['type'] not in S.ACTIONS:
            r.err('unsupported_action', f'Action {a["type"]!r} ({a["id"]}) is not supported by the rig',
                  frames=(a['start_frame'], a['end_frame']), fix='Supported: ' + ', '.join(S.ACTIONS))
            r.unsupported.append({'kind': 'action', 'value': a['type']})
        if a['main_frames'] < 2:
            r.err('action_timing', f'Action {a["id"]} needs at least 2 main frames')
        if a['type'] in S.HAND_ACTIONS and a['params'].get('hand', 'right') not in ('left', 'right'):
            r.err('action_params', f'Action {a["id"]}: hand must be left or right')
        if a['type'] in ('reach', 'grab', 'push') and a['params'].get('prop') not in prop_ids:
            r.err('action_params', f'Action {a["id"]} targets missing prop {a["params"].get("prop")!r}')
        if a['type'] in S.LOCOMOTION and a['params'].get('to') is None:
            r.err('action_params', f'Action {a["id"]} ({a["type"]}) needs params.to = [x, y]')
        if a['type'] == 'turn' and a['params'].get('to_facing') is None:
            r.err('action_params', f'Action {a["id"]} (turn) needs params.to_facing')
    line_ids = set()
    for ln in m['lines']:
        if ln['id'] in line_ids:
            r.err('lines', f'Duplicate line id {ln["id"]}')
        line_ids.add(ln['id'])
        if ln['speaker'] not in cast_ids and ln['speaker'] != 'narrator':
            r.err('lines', f'Line {ln["id"]} has unknown speaker {ln["speaker"]!r}')
        if not ln['text']:
            r.err('lines', f'Line {ln["id"]} is empty')
        if ln['emotion'] not in S.EMOTIONS:
            r.err('enum', f'Line {ln["id"]}: emotion {ln["emotion"]!r} unknown')
        if ln['pace'] not in S.PACES:
            r.err('enum', f'Line {ln["id"]}: pace {ln["pace"]!r} unknown')
        if ln['volume'] not in S.VOLUMES:
            r.err('enum', f'Line {ln["id"]}: volume {ln["volume"]!r} unknown')
    for x in m['sfx']:
        if x['cue'] not in S.SFX_CUES:
            r.err('unsupported_sfx', f'Sound cue {x["cue"]!r} is not in the sound library')
            r.unsupported.append({'kind': 'sfx', 'value': x['cue']})
    for x in m['music']:
        if x['cue'] not in S.MUSIC_CUES:
            r.err('enum', f'Music cue {x["cue"]!r} unknown')
    for b in m['beats']:
        if b['purpose'] not in S.PURPOSES:
            r.err('beat_purpose', f'Beat {b["id"]} ({b["start_tc"]}) has no valid narrative purpose',
                  frames=(b['start_frame'], b['end_frame']), fix='Give every second a purpose')


def _pose(r, p, where, prop_ids, cast_ids):
    pos = p.get('position')
    if not (isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, (int, float)) for v in pos)):
        r.err('pose', f'{where}: position must be [x, y]')
    elif any(abs(v) > S.WORLD_LIMIT for v in pos):
        r.err('out_of_range', f'{where}: position {pos} is outside the world')
    for (part, k), (lo, hi) in S.RANGES.items():
        _range(r, f'{part}.{k}', (p.get(part) or {}).get(k), lo, hi, where)
    if p.get('expression') not in S.EXPRESSIONS:
        r.err('unsupported_expression', f'{where}: expression {p.get("expression")!r} has no preset',
              fix='Use one of: ' + ', '.join(S.EXPRESSIONS))
        r.unsupported.append({'kind': 'expression', 'value': p.get('expression')})
    if p['mouth'].get('shape') not in S.MOUTH_SHAPES:
        r.err('unsupported_mouth', f'{where}: mouth shape {p["mouth"].get("shape")!r} unsupported')
        r.unsupported.append({'kind': 'mouth', 'value': p['mouth'].get('shape')})
    for side in ('left', 'right'):
        if p['arms'].get(side) not in S.ARM_POSES:
            r.err('unsupported_arm_pose', f'{where}: {side} arm pose {p["arms"].get(side)!r} unsupported',
                  fix='Use one of: ' + ', '.join(S.ARM_POSES))
            r.unsupported.append({'kind': 'arm_pose', 'value': p['arms'].get(side)})
    if p.get('posture') not in S.POSTURES:
        r.err('enum', f'{where}: posture {p.get("posture")!r} unsupported')
    if p['feet'].get('stance') not in S.STANCES or p['feet'].get('weight') not in S.WEIGHTS:
        r.err('enum', f'{where}: feet stance/weight unsupported')
    et = p.get('eye_target') or {}
    kind = et.get('kind')
    if kind not in ('camera', 'character', 'prop', 'point', 'forward'):
        r.err('eye_target', f'{where}: eye_target kind {kind!r} unsupported')
    elif kind == 'prop' and et.get('id') not in prop_ids:
        r.err('eye_target', f'{where}: eye-line prop {et.get("id")!r} does not exist')
    elif kind == 'character' and et.get('id') not in cast_ids:
        r.err('eye_target', f'{where}: eye-line character {et.get("id")!r} does not exist')
    elif kind == 'point':
        pt = et.get('point')
        if not (isinstance(pt, list) and len(pt) == 3):
            r.err('eye_target', f'{where}: eye-line point must be [x, y, z]')


def _timeline(r, m):
    D, fps = m['duration_frames'], m['fps']
    shots = m['shots']
    if not shots:
        r.err('shots', 'At least one shot is required')
        return
    if shots[0]['start_frame'] != 0:
        r.err('gap', f'Timeline starts with an unintended gap of {shots[0]["start_frame"]} frames',
              frames=(0, shots[0]['start_frame']))
    for a, b in zip(shots, shots[1:]):
        if b['start_frame'] > a['end_frame']:
            r.err('gap', f'Gap between shots {a["id"]} and {b["id"]} ({tc(a["end_frame"], fps)}-{tc(b["start_frame"], fps)})',
                  frames=(a['end_frame'], b['start_frame']))
        if b['start_frame'] < a['end_frame']:
            r.err('overlap', f'Shots {a["id"]} and {b["id"]} overlap', frames=(b['start_frame'], a['end_frame']))
    if shots[-1]['end_frame'] != D:
        r.err('gap' if shots[-1]['end_frame'] < D else 'overlap',
              f'Last shot ends at {tc(shots[-1]["end_frame"], fps)} but the video ends at {tc(D, fps)}')
    for s in shots:
        if s['end_frame'] - s['start_frame'] < max(6, fps // 4):
            r.err('shot_length', f'Shot {s["id"]} is shorter than a readable cut', frames=(s['start_frame'], s['end_frame']))
        if s['renderer'] == 'runway':
            secs = (s['end_frame'] - s['start_frame']) / fps
            if secs > 10:
                r.err('provider_shot_length', f'Shot {s["id"]} is {secs:.1f}s; generative shots are limited to 10s',
                      fix='Split the shot')
    beats = m['beats']
    if beats and (beats[0]['start_frame'] != 0 or beats[-1]['end_frame'] != D):
        r.err('beats', 'Beats must cover the whole timeline')
    for a, b in zip(beats, beats[1:]):
        if a['end_frame'] != b['start_frame']:
            r.err('beats', f'Beat gap/overlap at {a["end_tc"]}')
    for b in beats:
        if b['end_frame'] - b['start_frame'] > fps:
            r.err('beats', f'Beat {b["id"]} is longer than one second')
    for ln in m['lines']:
        if not 0 <= ln['start_frame'] < D:
            r.err('line_timing', f'Line {ln["id"]} starts outside the video')
    for a in m['tracks']['actions']:
        if a['start_frame'] < 0 or a['end_frame'] > D:
            r.err('action_timing', f'Action {a["id"]} runs outside the video', frames=(a['start_frame'], a['end_frame']))


def _parts(a):
    if a['type'] in FULL_BODY:
        return {'body', 'head'} if a['type'] in ('flinch', 'cower', 'celebrate', 'fall_down', 'stumble') else {'body'}
    if a['type'] in HEAD_ONLY:
        return {'head'}
    return {'arm_' + a['params'].get('hand', 'right')} if a['type'] in S.HAND_ACTIONS else {'arms'}


def _actions(r, m):
    by_char = {}
    for a in m['tracks']['actions']:
        by_char.setdefault(a['character'], []).append(a)
    for cid, acts in by_char.items():
        for i, a in enumerate(acts):
            for b in acts[i + 1:]:
                if b['start_frame'] >= a['end_frame']:
                    continue
                pa, pb = _parts(a), _parts(b)
                if 'arms' in pa:
                    pa |= {'arm_left', 'arm_right'}
                if 'arms' in pb:
                    pb |= {'arm_left', 'arm_right'}
                if pa & pb:
                    r.err('action_overlap', f'{cid}: actions {a["id"]} ({a["type"]}) and {b["id"]} ({b["type"]}) '
                                            'use the same body parts at the same time',
                          frames=(b['start_frame'], min(a['end_frame'], b['end_frame'])))
        state = 'standing'
        for a in sorted(acts, key=lambda x: x['start_frame']):
            t = a['type']
            if t == 'get_up' and state != 'sitting':
                r.err('action_sequence', f'{cid}: get_up at {tc(a["start_frame"], m["fps"])} without falling down first')
            if t == 'stand_up' and state != 'crouching':
                r.err('action_sequence', f'{cid}: stand_up without crouching first')
            if t in S.LOCOMOTION and state != 'standing':
                r.err('action_sequence', f'{cid}: {t} while {state}', frames=(a['start_frame'], a['end_frame']))
            state = {'fall_down': 'sitting', 'get_up': 'standing', 'crouch': 'crouching', 'cower': 'crouching',
                     'stand_up': 'standing'}.get(t, state)


def _dialogue(r, m, measured):
    fps = m['fps']
    lines = m['lines']
    for ln in lines:
        need = ln.get('measured_frames') or ln['est_frames']
        avail = ln['window_end_frame'] - ln['start_frame']
        if need > avail:
            kind = 'measured' if ln.get('measured_frames') else 'estimated'
            r.err('dialogue_fit', f'Line {ln["id"]} needs {need / fps:.2f}s ({kind}) but has {avail / fps:.2f}s before '
                                  'the next line or the end', frames=(ln['start_frame'], ln['window_end_frame']),
                  fix='Shorten the line, move the next line later, or extend the scene')
    by_spk = {}
    for ln in lines:
        by_spk.setdefault(ln['speaker'], []).append(ln)
    for spk, ls in by_spk.items():
        for a, b in zip(ls, ls[1:]):
            if b['start_frame'] < a['est_end_frame']:
                r.err('dialogue_overlap', f'{spk} starts {b["id"]} before finishing {a["id"]}',
                      frames=(b['start_frame'], a['est_end_frame']))
    for a, b in zip(lines, lines[1:]):
        if a['speaker'] != b['speaker'] and b['start_frame'] < a['est_end_frame']:
            r.warn('crosstalk', f'Lines {a["id"]} and {b["id"]} overlap', frames=(b['start_frame'], a['est_end_frame']))
    cast = {c['id'] for c in m['cast']}
    for ln in lines:
        if ln['speaker'] in cast:
            # Visible speech needs the speaker on screen with a readable face.
            for b in m['beats']:
                if b['start_frame'] <= ln['start_frame'] < b['end_frame'] and b['camera']:
                    cam = b['camera']['start']
                    if cam['subject'] not in (ln['speaker'], 'two_shot') and not cam['subject'].startswith('prop:'):
                        r.warn('offscreen_speech', f'Line {ln["id"]}: {ln["speaker"]} speaks while the camera frames '
                                                   f'{cam["subject"]}; lip sync will not be visible',
                               frames=(ln['start_frame'], ln['est_end_frame']))


def _ground_ok(m, pos):
    st = m['setting']
    if st.get('preset') in GROUND_PRESETS:
        return True
    for p in st.get('props', []):
        if p.get('type') in ('platform', 'spring_pad'):
            size = p.get('size') or [2.0, 2.0]
            sx = p['scale'] * size[0] / 2
            sy = p['scale'] * size[1] / 2
            px, py = p['position'][0], p['position'][1]
            if abs(pos[0] - px) <= sx + 0.15 and abs(pos[1] - py) <= sy + 0.15:
                return True
    return False


def _physics(r, m):
    fps = m['fps']
    acts = m['tracks']['actions']
    for cid, tr in m['tracks']['characters'].items():
        keys = tr['keys']
        for k0, k1 in zip(keys, keys[1:]):
            p0, p1 = k0['pose']['position'], k1['pose']['position']
            dist = math.dist(p0, p1)
            if dist < 0.05:
                continue
            cover = [a for a in acts if a['character'] == cid and a['type'] in S.LOCOMOTION | {'stumble'}
                     and a['start_frame'] >= k0['frame'] - 1 and a['start_frame'] + a['anticipation_frames'] + a['main_frames'] <= k1['frame'] + 1]
            if not cover:
                r.err('teleport', f'{cid} moves {dist:.2f} m between {tc(k0["frame"], fps)} and {tc(k1["frame"], fps)} '
                                  'without a walk, run or jump', frames=(k0['frame'], k1['frame']),
                      fix='Add a locomotion action or keep the position')
                continue
            a = cover[0]
            secs = max(1, a['main_frames']) / fps
            if dist / secs > S.MAX_SPEED.get(a['type'], 3):
                r.err('implausible_speed', f'{cid} {a["type"]} covers {dist:.2f} m in {secs:.2f}s', frames=(a['start_frame'], a['end_frame']),
                      fix='Give the action more main frames')
            if a['type'] == 'jump' and dist > S.JUMP_MAX_DISTANCE:
                r.err('implausible_jump', f'{cid} jumps {dist:.2f} m (max {S.JUMP_MAX_DISTANCE} m)')
        for k in keys:
            in_air = any(a['character'] == cid and a['type'] in ('jump', 'hop', 'celebrate')
                         and a['start_frame'] < k['frame'] < a['end_frame'] for a in acts)
            if not in_air and not _ground_ok(m, k['pose']['position']):
                r.err('unsupported_ground', f'{cid} stands at {k["pose"]["position"]} at {tc(k["frame"], fps)}, which is '
                                            'not on any platform', frames=(k['frame'], k['frame'] + 1),
                      fix='Move the character onto a platform or add one')
    # Prop contact: grabbed props must be within reach.
    props = {p['id']: p for p in m['setting'].get('props', [])}
    held = {}
    for e in m['tracks']['props']:
        p = props.get(e['prop'])
        if not p:
            r.err('prop_event', f'Prop event for missing prop {e["prop"]!r}')
            continue
        if e['event'] == 'attach':
            if p['type'] not in S.HOLDABLE:
                r.err('prop_contact', f'{p["type"]} {e["prop"]} cannot be held')
            if e['prop'] in held:
                r.err('prop_contact', f'{e["prop"]} is already held by {held[e["prop"]]}')
            keys = m['tracks']['characters'].get(e['character'], {}).get('keys', [])
            pos = pose_at(keys, e['frame'])['position']
            reach = math.dist(pos, p['position'][:2])
            if reach > 0.95:
                r.err('prop_reach', f'{e["character"]} grabs {e["prop"]} from {reach:.2f} m away (max 0.95 m)',
                      frames=(e['frame'], e['frame'] + 1), fix='Walk closer before grabbing')
            held[e['prop']] = e['character']
        elif e['event'] == 'detach':
            if held.get(e['prop']) != e['character']:
                r.err('prop_contact', f'{e["character"]} drops {e["prop"]} without holding it')
            held.pop(e['prop'], None)
    for a in acts:
        if a['type'] in ('push', 'reach') and a['params'].get('prop') in props:
            keys = m['tracks']['characters'].get(a['character'], {}).get('keys', [])
            contact = a['start_frame'] + a['anticipation_frames'] + a['main_frames']
            pos = pose_at(keys, contact)['position']
            reach = math.dist(pos, props[a['params']['prop']]['position'][:2])
            if reach > 1.0:
                r.err('prop_reach', f'{a["character"]} {a["type"]}es {a["params"]["prop"]} from {reach:.2f} m away',
                      frames=(a['start_frame'], a['end_frame']))


def _expressions(r, m, min_frames):
    fps = m['fps']
    for cid, tr in m['tracks']['characters'].items():
        keys = tr['keys']
        for i, k in enumerate(keys):
            prev = keys[i - 1]['pose'].get('expression') if i else None
            cur = k['pose'].get('expression')
            if i and cur == prev:
                continue
            end = keys[i + 1]['frame'] if i + 1 < len(keys) else m['duration_frames']
            # The next key starts blending before its frame.
            if i + 1 < len(keys):
                end -= (keys[i + 1].get('blend_frames') or min(10, end - k['frame']))
            hold = end - k['frame']
            if cur != 'neutral' and hold < min_frames:
                r.err('expression_too_short', f'{cid} "{cur}" at {tc(k["frame"], fps)} is on screen for only {hold} frames '
                                              f'(minimum {min_frames})', frames=(k['frame'], end),
                      fix='Hold the expression longer or delay the next key')
            if cur in ('neutral',):
                continue
            beat = next((b for b in m['beats'] if b['start_frame'] <= k['frame'] < b['end_frame']), None)
            if not beat or not beat['camera']:
                continue
            cam = beat['camera']['end']
            shot = next(s for s in m['shots'] if s['id'] == beat['shot'])
            subject_ok = cam['subject'] in (cid, 'two_shot')
            if not subject_ok:
                r.warn('expression_offscreen', f'{cid} changes to "{cur}" at {tc(k["frame"], fps)} while the camera frames '
                                               f'{cam["subject"]}', frames=(k['frame'], end))
                continue
            if cam['framing'] not in S.FACE_READABLE:
                r.err('expression_unreadable', f'{cid} "{cur}" at {tc(k["frame"], fps)} is framed {cam["framing"]}; '
                                               'the face is too small to read', frames=(k['frame'], end),
                      fix='Use a medium shot or closer for expression beats')
            if not face_visible(k['pose'], camera_azimuth(m, shot)):
                r.err('expression_hidden', f'{cid} "{cur}" at {tc(k["frame"], fps)}: face turned away from the camera',
                      frames=(k['frame'], end), fix='Turn the head toward the camera or change the camera side')


def _continuity(r, m):
    fps = m['fps']
    lights = {b['setting']['lighting'] for b in m['beats']}
    if len(lights) > 1:
        r.warn('lighting_change', f'Lighting changes between beats ({", ".join(sorted(map(str, lights)))}); '
                                  'make sure the story motivates it')
    shots = m['shots']
    for a, b in zip(shots, shots[1:]):
        sa, sb = a['camera']['side'], b['camera']['side']
        la = G.CAMERA_SIDE_DEG.get(sa, 0)
        lb = G.CAMERA_SIDE_DEG.get(sb, 0)
        if len(m['cast']) > 1 and la * lb < 0 and abs(la) >= 35 and abs(lb) >= 35 and a['camera']['subject'] == 'two_shot' \
                and b['camera']['subject'] == 'two_shot':
            r.warn('axis_crossing', f'Cut {a["id"]}->{b["id"]} at {tc(b["start_frame"], fps)} may cross the 180-degree line')
    # Characters exist in one place: identical positions for two characters.
    ids = list(m['tracks']['characters'])
    for i, c1 in enumerate(ids):
        for c2 in ids[i + 1:]:
            for f in range(0, m['duration_frames'], fps // 2):
                p1 = pose_at(m['tracks']['characters'][c1]['keys'], f)['position']
                p2 = pose_at(m['tracks']['characters'][c2]['keys'], f)['position']
                if math.dist(p1, p2) < 0.55:
                    r.err('collision', f'{c1} and {c2} overlap at {tc(f, fps)}', frames=(f, f + fps // 2))
                    break


def _captions(r, m, prefs):
    if not prefs:
        return
    pr = prefs['production']
    if not pr.get('captions', True):
        return
    from ..captions_layout import fits
    for c in m['captions']:
        ok, info = fits(c['text'], m['width'], m['height'], pr)
        if not ok:
            r.err('caption_safe_area', f'Caption "{c["text"]}" does not fit inside the safe area ({info})',
                  frames=(c['start_frame'], c['end_frame']), fix='Shorten the caption group')
        if c['end_frame'] - c['start_frame'] < int(0.3 * m['fps']):
            r.warn('caption_flash', f'Caption "{c["text"]}" is shown for under 0.3 s')


def _story(r, m):
    fps = m['fps']
    D = m['duration_frames']
    hooks = [b for b in m['beats'] if b['purpose'] == 'hook']
    if not hooks or hooks[0]['start_frame'] > 3 * fps:
        r.err('hook', 'No hook beat in the first three seconds', fix='Open with the hook')
    if not m['hook']['text']:
        r.err('hook', 'The hook is not described')
    payoffs = [b for b in m['beats'] if b['purpose'] in ('payoff', 'button')]
    if not payoffs:
        r.err('payoff', 'No payoff beat; the ending must pay off the hook')
    elif payoffs[-1]['end_frame'] < 0.75 * D:
        r.err('payoff', 'The payoff happens too early; the final quarter has no payoff')
    if not m['payoff']['text'] or m['payoff'].get('resolves') != 'hook':
        r.err('payoff', 'The payoff must state how it resolves the hook')
    if m['hook']['end_frame'] > 3 * fps:
        r.warn('hook_late', 'Hook resolves its setup after 3 s; most Shorts viewers decide earlier')
    if not m['lines'] and not any(b['description'] for b in m['beats']):
        r.warn('no_story_text', 'No dialogue and no beat descriptions')
