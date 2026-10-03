"""Motion and visual checks from Blender telemetry and rendered pixels.

Telemetry is read back from Blender's evaluated scene after each frame was
applied, so it reflects what was rendered, not what was planned. Pixel checks
read the encoded video to confirm that changes are visible in the image.
"""
import json
import math

import numpy as np

from .. import config, media
from ..animation import rig as R
from ..manifest import schema as S
from ..voice import audio as A

GW, GH = 216, 384


def load_telemetry(paths):
    tele = {}
    for p in paths:
        with open(p) as f:
            for line in f:
                rec = json.loads(line)
                tele[rec['frame']] = rec
    return tele


def decode_gray(path, w=GW, h=GH):
    out, _ = media.run([config.FFMPEG_BIN, '-v', 'error', '-i', str(path), '-vf', f'scale={w}:{h},format=gray',
                        '-f', 'rawvideo', '-'], timeout=900)
    a = np.frombuffer(out, dtype=np.uint8)
    n = len(a) // (w * h)
    return a[:n * w * h].reshape(n, h, w).astype(np.float32)


def decode_rgb_frame(path, frame, fps):
    t = frame / float(fps)
    info = media.video_stream(media.probe(path))
    w, h = int(info['width']), int(info['height'])
    out, _ = media.run([config.FFMPEG_BIN, '-v', 'error', '-ss', f'{t:.4f}', '-i', str(path), '-frames:v', '1',
                        '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], timeout=120)
    return np.frombuffer(out, dtype=np.uint8)[:w * h * 3].reshape(h, w, 3)


def on_screen(rec, min_area=0.004):
    if not rec or not rec.get('in_front_of_camera', True):
        return False
    x0, y0, x1, y1 = rec['bbox2d']
    ix = max(0.0, min(1.0, x1) - max(0.0, x0))
    iy = max(0.0, min(1.0, y1) - max(0.0, y0))
    return ix * iy >= min_area


def ranges(frames):
    out = []
    for f in sorted(frames):
        if out and f == out[-1][1] + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return out


def shot_at(m, f):
    return next((s for s in m['shots'] if s['start_frame'] <= f < s['end_frame']), None)


def run(ck, m, solved, tele, prefs, joined_path, dialog_by_line):
    q = prefs['qa']
    D = m['duration_frames']
    frames = sorted(tele)
    missing_tele = [f for f in range(D) if f not in tele]
    ck.add('telemetry_coverage', 'Render telemetry covers every frame', 'motion',
           'pass' if not missing_tele else 'fail', 'major', {'missing_frames': len(missing_tele)}, 0.99, 'telemetry',
           repair={'action': 're_render_shot', 'shot': shot_at(m, missing_tele[0])['id']} if missing_tele else None)
    if not frames:
        return
    gray = None
    try:
        gray = decode_gray(joined_path)
    except media.MediaError:
        gray = None
    for c in m['cast']:
        cid = c['id']
        plan = solved['characters'].get(cid, [])
        scale = solved['scales'].get(cid, 1.0)
        recs = {f: tele[f]['characters'].get(cid) for f in frames if cid in tele[f]['characters']}
        _integrity(ck, m, cid, recs)
        _feet(ck, m, cid, recs, plan, q, scale)
        _actions(ck, m, cid, recs, plan, tele, scale)
        _expressions(ck, m, cid, recs, gray, prefs)
        _lipsync(ck, m, cid, recs, gray, dialog_by_line)
    _camera(ck, m, tele)
    _sightlines(ck, m, tele, solved)
    _props(ck, m, tele, solved)
    _identity(ck, m, tele, joined_path)


def _integrity(ck, m, cid, recs):
    required = set(R.REQUIRED_PARTS)
    missing = [f for f, r in recs.items() if r and not required.issubset(set(r.get('parts_visible', [])))]
    counts = [r['object_count'] for r in recs.values() if r]
    mode = max(set(counts), key=counts.count) if counts else 0
    odd = [f for f, r in recs.items() if r and r['object_count'] != mode]
    status = 'fail' if (missing or odd) else 'pass'
    ck.add(f'integrity:{cid}', f'{cid}: model integrity (no missing/extra limbs, no duplicated parts)', 'visual', status,
           'critical', {'frames_missing_parts': len(missing), 'frames_with_unexpected_object_count': len(odd),
                        'object_count': mode, 'required_parts': len(required)}, 0.97, 'telemetry',
           frames=(min(missing + odd), max(missing + odd)) if (missing or odd) else None,
           target={'kind': 'shot', 'id': shot_at(m, min(missing + odd))['id']} if (missing or odd) else None,
           repair={'action': 're_render_shot', 'shot': shot_at(m, min(missing + odd))['id']} if (missing or odd) else None)


def _feet(ck, m, cid, recs, plan, q, scale):
    slide, floating, sink = [], [], []
    max_slide = 0.0
    prev = None
    for f in sorted(recs):
        r = recs[f]
        if f >= len(plan) or not r:
            prev = None
            continue
        p = plan[f]
        for side in ('l', 'r'):
            if not p['feet'][side]['contact_expected']:
                continue
            sole = r['sole_' + side]
            if sole[2] > 0.03 * scale:
                floating.append(f)
            if sole[2] < -0.03:
                sink.append(f)
            if prev and prev[0] == f - 1 and prev[1][side] is not None and plan[f - 1]['feet'][side]['contact_expected']:
                d = math.dist(sole[:2], prev[1][side][:2]) * 1000
                max_slide = max(max_slide, d)
                if d > q['foot_slide_mm_per_frame']:
                    slide.append(f)
        prev = (f, {s: r['sole_' + s] for s in ('l', 'r')})
    visible_issue = [f for f in slide + floating + sink if on_screen(recs.get(f))]
    status = 'fail' if visible_issue else 'pass'
    sev = 'major'
    first = min(visible_issue) if visible_issue else None
    ck.add(f'feet:{cid}', f'{cid}: grounded feet (no floating, sliding or sinking while planted)', 'motion', status,
           sev, {'sliding_frames': len(slide), 'floating_frames': len(floating), 'sinking_frames': len(sink),
                 'max_slide_mm_per_frame': round(max_slide, 2), 'threshold_mm': q['foot_slide_mm_per_frame'],
                 'on_screen_issue_ranges': ranges(visible_issue)[:6],
                 'note': 'Measured on evaluated foot soles while the plan expects contact.'}, 0.92, 'telemetry',
           frames=(first, first + 1) if first is not None else None,
           target={'kind': 'shot', 'id': shot_at(m, first)['id']} if first is not None else None,
           repair={'action': 're_render_shot', 'shot': shot_at(m, first)['id'],
                   'params': {'pelvis_drop_extra': 0.03, 'character': cid}} if first is not None else None)


def _chin(rec, scale):
    if 'chin' in rec:
        return rec['chin']
    x, y, z = rec['face']
    return [x, y, z - 0.24 * scale]


def _actions(ck, m, cid, recs, plan, tele, scale):
    props = {p['id']: p for p in m['setting'].get('props', [])}
    for a in m['tracks']['actions']:
        if a['character'] != cid:
            continue
        a0 = a['start_frame']
        a1 = a0 + a['anticipation_frames']
        a2 = a1 + a['main_frames']
        a4 = a['end_frame']
        main = [f for f in range(a1, a2) if recs.get(f)]
        whole = [f for f in range(a0, a4) if recs.get(f)]
        if not main:
            continue
        t = a['type']
        ok, ev, conf = None, {}, 0.85
        r0 = recs.get(a0) or recs[main[0]]
        if t in ('jump', 'hop', 'celebrate'):
            zmax = max(recs[f]['root'][2] for f in main)
            need = (0.25 if t == 'jump' else 0.12) * scale
            ev = {'max_root_height_m': round(zmax, 3), 'required_m': need}
            ok = zmax >= need
            if t == 'jump' and a['params'].get('to'):
                land = recs.get(min(a4 - 1, a2 + 2))
                if land:
                    err = math.dist(land['root'][:2], a['params']['to'])
                    ev['landing_error_m'] = round(err, 3)
                    ok = ok and err < 0.25
        elif t in ('walk', 'run'):
            start, end = recs[main[0]]['root'], recs[main[-1]]['root']
            planned = math.dist(a['params'].get('from', start[:2]), a['params']['to'])
            moved = math.dist(start[:2], end[:2])
            ev = {'moved_m': round(moved, 3), 'planned_m': round(planned, 3)}
            ok = moved >= 0.8 * planned
        elif t == 'turn':
            ys = [recs[f].get('yaw') for f in whole if recs[f].get('yaw') is not None]
            if ys:
                dy = abs(((ys[-1] - ys[0]) + 180) % 360 - 180)
                want = abs(((a['params']['to_facing'] - a['params'].get('from_facing', 0)) + 180) % 360 - 180)
                ev = {'turned_deg': round(dy, 1), 'planned_deg': round(want, 1)}
                ok = dy >= 0.8 * want
            else:
                ok, conf = None, 0.3
        elif t == 'wave':
            side = 'l' if a['params'].get('hand') == 'left' else 'r'
            # Hand clearly above shoulder height (about 0.25 m below the face centre).
            above = [f for f in main if recs[f]['palm_' + side][2] > recs[f]['face_z'] - 0.25 * scale] if 'face_z' in recs[main[0]] else []
            xs = [recs[f]['palm_' + side][0] for f in main]
            ev = {'frames_hand_raised': len(above), 'main_frames': len(main), 'hand_x_std_m': round(float(np.std(xs)), 4)}
            ok = (len(above) >= 0.5 * len(main) if above or 'face_z' in recs[main[0]] else True) and np.std(xs) > 0.01
            if 'face_z' not in recs[main[0]]:
                conf = 0.6
        elif t == 'point':
            side = 'l' if a['params'].get('hand') == 'left' else 'r'
            ext = max(math.dist(recs[f]['palm_' + side][:2], recs[f]['root'][:2]) for f in whole)
            ev = {'max_hand_reach_m': round(ext, 3)}
            ok = ext >= 0.45 * scale
        elif t in ('reach', 'grab', 'push'):
            side = 'l' if a['params'].get('hand') == 'left' else 'r'
            prop = props.get(a['params'].get('prop'))
            contact = min(a4 - 1, a2)
            pr = tele.get(contact, {}).get('props', {}).get(prop['id']) if prop else None
            if pr and recs.get(contact):
                d = math.dist(recs[contact]['palm_' + side], pr['location'])
                ev = {'palm_to_prop_m': round(d, 3), 'contact_frame': contact}
                ok = d <= 0.2 * scale
            else:
                ok, conf = None, 0.3
        elif t in ('facepalm', 'think'):
            # facepalm: the palm covers the face; think: the hand rests at the chin, which is about
            # 0.26 m from the face centre, so it is measured against the chin point.
            side = 'l' if a['params'].get('hand') == 'left' else 'r'
            frames = [f for f in range(a2, a4) if recs.get(f)]
            if t == 'facepalm':
                ds = [math.dist(recs[f]['palm_' + side], recs[f]['face']) for f in frames]
                ev = {'min_palm_to_face_m': round(min(ds), 3) if ds else None}
            else:
                ds = [math.dist(recs[f]['palm_' + side], _chin(recs[f], scale)) for f in frames]
                ev = {'min_palm_to_chin_m': round(min(ds), 3) if ds else None}
                if frames and 'chin' not in recs[frames[0]]:
                    ev['chin'] = 'estimated from the face centre (older telemetry)'
                    conf = 0.6
            ok = bool(ds) and min(ds) <= 0.2 * scale
        elif t in ('crouch', 'cower', 'fall_down'):
            z0 = r0.get('face_z', r0['face'][2])
            zmin = min(recs[f].get('face_z', recs[f]['face'][2]) for f in range(a1, a4) if recs.get(f))
            ev = {'head_drop_m': round(z0 - zmin, 3)}
            ok = z0 - zmin >= 0.18 * scale
        elif t == 'stumble':
            base = r0['root']
            disp = max(math.dist(recs[f]['root'][:2], base[:2]) for f in main)
            ev = {'max_displacement_m': round(disp, 3)}
            ok = disp >= 0.05
        else:
            fz = [recs[f]['face'] for f in whole]
            spread = float(np.max(np.std(np.array(fz), axis=0))) if fz else 0
            ev = {'face_motion_std_m': round(spread, 4)}
            ok = spread > 0.002
            conf = 0.55
        visible = sum(1 for f in main if on_screen(recs.get(f)))
        ev['on_screen_fraction'] = round(visible / max(1, len(main)), 2)
        subject = any(s['camera']['subject'] in (cid, 'two_shot') for s in m['shots']
                      if s['start_frame'] <= a1 < s['end_frame'])
        if ok is None:
            status, sev = 'uncertain', 'minor'
        elif not ok:
            status, sev = 'fail', 'major'
        elif visible < 0.5 * len(main):
            status, sev = ('fail', 'major') if subject else ('pass', 'info')
            ev['note'] = 'Action happens mostly off-screen' + ('' if subject else ' (character is not the shot subject)')
        else:
            status, sev = 'pass', 'major'
        shot = shot_at(m, a1)
        ck.add(f'action:{a["id"]}', f'{cid} visibly performs {t} ({a["id"]})', 'motion', status, sev, ev, conf,
               'telemetry', frames=(a0, a4), target={'kind': 'shot', 'id': shot['id'] if shot else None},
               repair={'action': 're_render_shot', 'shot': shot['id'],
                       'params': {'character': cid, 'camera_wider': True}} if status == 'fail' and shot else None)


def _crop(gray, f, cx, cy, half_w, half_h):
    if gray is None or f >= len(gray):
        return None
    h, w = gray.shape[1], gray.shape[2]
    x0, x1 = int((cx - half_w) * w), int((cx + half_w) * w)
    y0, y1 = int((cy - half_h) * h), int((cy + half_h) * h)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return gray[f, y0:y1, x0:x1]


def _expressions(ck, m, cid, recs, gray, prefs):
    min_frames = prefs['production']['min_expression_frames']
    keys = m['tracks']['characters'][cid]['keys']
    for i, k in enumerate(keys):
        expr = k['pose'].get('expression')
        prev = keys[i - 1]['pose'].get('expression') if i else None
        if expr in (None, 'neutral') or expr == prev:
            continue
        shot = shot_at(m, k['frame'])
        if not shot or shot['camera']['subject'] not in (cid, 'two_shot'):
            continue
        end = keys[i + 1]['frame'] if i + 1 < len(keys) else m['duration_frames']
        start = k['frame']
        hold = [f for f in range(start, min(end, shot['end_frame'])) if recs.get(f)]
        readable = []
        for f in hold:
            r = recs[f]
            fx, fy = r['face2d'][0], r['face2d'][1]
            if r['face_dot'] >= 0.35 and r['face_height_frac'] >= 0.08 and 0.03 <= fx <= 0.97 and 0.03 <= fy <= 0.8:
                readable.append(f)
        evidence = {'expression': expr, 'readable_frames': len(readable), 'required_frames': min_frames,
                    'hold_frames': len(hold)}
        if hold:
            evidence['face_dot_min'] = round(min(recs[f]['face_dot'] for f in hold), 3)
            evidence['face_height_frac_min'] = round(min(recs[f]['face_height_frac'] for f in hold), 3)
        # Pixel evidence: did the face region actually change on screen?
        pix = None
        before = max(0, start - (k.get('blend_frames') or 10) - 2)
        after = min(start + 6, m['duration_frames'] - 1)
        rb, ra = recs.get(before), recs.get(after)
        if gray is not None and rb and ra and shot_at(m, before) is shot:
            hw = max(0.04, ra['face_height_frac'] * 9 / 16 * 0.5)
            hh = max(0.03, ra['face_height_frac'] * 0.5)
            c1 = _crop(gray, before, rb['face2d'][0], rb['face2d'][1], hw, hh)
            c2 = _crop(gray, after, ra['face2d'][0], ra['face2d'][1], hw, hh)
            if c1 is not None and c2 is not None:
                hgt = min(c1.shape[0], c2.shape[0])
                wid = min(c1.shape[1], c2.shape[1])
                pix = float(np.mean(np.abs(c1[:hgt, :wid] - c2[:hgt, :wid])))
                evidence['face_pixel_change'] = round(pix, 2)
        ok = len(readable) >= min_frames
        status = 'pass' if ok else 'fail'
        conf = 0.85 if pix is None else (0.9 if pix > 2.0 else 0.7)
        if ok and pix is not None and pix < 1.0:
            status = 'uncertain'
            evidence['note'] = 'Face pixels barely changed; the expression may not read on screen'
        ck.add(f'expression:{cid}:{start}', f'{cid} "{expr}" expression is readable', 'visual', status, 'major',
               evidence, conf, 'telemetry+pixel', frames=(start, end),
               target={'kind': 'shot', 'id': shot['id']},
               repair={'action': 're_render_shot', 'shot': shot['id'],
                       'params': {'camera_tighter': True, 'face_camera': cid}} if status == 'fail' else None)


def _hand_over_mouth(rec, aspect):
    """True when a hand is in front of the face, across it (forehead to just below the mouth) and within
    its width: the hand or forearm then likely covers part of the mouth. A hand on the chest does not count."""
    mouth = rec.get('mouth2d')
    if not mouth:
        return False
    fh = rec.get('face_height_frac', 0.1)
    for side in ('l', 'r'):
        palm = rec.get('palm2d_' + side)
        if not palm or len(palm) < 3 or palm[2] > mouth[2] + 0.05:
            continue
        dx = (palm[0] - mouth[0]) * aspect  # in units of image height
        dy = palm[1] - mouth[1]             # image y grows downward
        if abs(dx) < 0.75 * fh and -1.2 * fh < dy < 0.25 * fh:
            return True
    return False


def _lipsync(ck, m, cid, recs, gray, dialog_by_line):
    for ln in m['lines']:
        if ln['speaker'] != cid:
            continue
        if not S.lip_synced(ln):
            _voiceover(ck, m, cid, ln, recs, dialog_by_line)
            continue
        a, b = ln['start_frame'], ln['est_end_frame']
        shot = shot_at(m, a)
        vis = [f for f in range(a, b) if recs.get(f) and on_screen(recs[f]) and recs[f]['face_dot'] > 0.2
               and recs[f]['face_height_frac'] > 0.05]
        if len(vis) < 0.5 * max(1, b - a):
            ck.add(f'lipsync:{ln["id"]}', f'Lip sync for line {ln["id"]}', 'visual', 'skipped', 'info',
                   {'reason': 'Speaker face not on screen for most of the line; voice plays off-screen'}, 0.9,
                   'telemetry', frames=(a, b), target={'kind': 'line', 'id': ln['id']})
            continue
        env = dialog_by_line.get(ln['id'])
        mouth = np.array([recs[f]['mouth_open'] if recs.get(f) else 0 for f in range(a, b)])
        corr, lag = None, None
        if env is not None and len(env) and mouth.std() > 1e-5:
            e = np.interp(np.arange(len(mouth)), np.linspace(0, len(mouth) - 1, len(env)), env)
            best = (-2, 0)
            for L in range(-4, 5):
                if L >= 0:
                    x, y = mouth[L:], e[:len(e) - L]
                else:
                    x, y = mouth[:L], e[-L:]
                if len(x) > 5 and x.std() > 1e-6 and y.std() > 1e-6:
                    c = float(np.corrcoef(x, y)[0, 1])
                    if c > best[0]:
                        best = (c, L)
            corr, lag = round(best[0], 3), best[1]
        # Pixel evidence, independent of telemetry: how much of the mouth crop is dark (mouth interior
        # against skin) in each frame, tracked at that frame's mouth position, correlated with the voice.
        pix_corr, pix_frames, occluded = None, 0, 0
        if gray is not None and env is not None and len(env):
            opening = []
            for f in range(a, b):
                r = recs.get(f)
                crop = None
                if r and _hand_over_mouth(r, m['width'] / m['height']):
                    occluded += 1
                    opening.append(np.nan)
                    continue
                if r and on_screen(r) and r['face_dot'] > 0.2:
                    hw = max(0.012, r['mouth_width_px'] / max(1, m['width']) * 0.75)
                    crop = _crop(gray, f, r['mouth2d'][0], r['mouth2d'][1], hw, hw * 0.8)
                if crop is None or crop.size < 30:
                    opening.append(np.nan)
                    continue
                ref = float(np.percentile(crop, 75))
                opening.append(float(np.mean(crop < 0.6 * ref)) if ref > 0.05 else np.nan)
            o = np.array(opening)
            ok_idx = ~np.isnan(o)
            pix_frames = int(ok_idx.sum())
            if pix_frames >= max(8, 0.5 * len(o)) and np.nanstd(o) <= 1e-4 and mouth.std() > 0.05:
                pix_corr = 0.0  # the scene says the mouth moves, but the rendered pixels never change
            elif pix_frames >= max(8, 0.5 * len(o)) and np.nanstd(o) > 1e-4:
                e = np.interp(np.arange(len(o)), np.linspace(0, len(o) - 1, len(env)), env)
                best = -1.0
                for L in range(-4, 5):
                    x = o[max(0, L):len(o) + min(0, L)]
                    y = e[max(0, -L):len(e) - max(0, L)]
                    k = ~np.isnan(x)
                    if k.sum() > 5 and x[k].std() > 1e-6 and y[k].std() > 1e-6:
                        best = max(best, float(np.corrcoef(x[k], y[k])[0, 1]))
                pix_corr = round(best, 3)
        ev = {'mouth_envelope_correlation': corr, 'best_lag_frames': lag, 'pixel_mouth_voice_correlation': pix_corr,
              'pixel_frames_measured': pix_frames, 'frames_mouth_covered_by_hand': occluded, 'visible_frames': len(vis),
              'method': 'scene mouth opening vs voice envelope, and rendered mouth darkness vs voice envelope'}
        if corr is None:
            status, conf = 'uncertain', 0.4
        else:
            ok = corr >= 0.3 and abs(lag) <= 2 and (pix_corr is None or pix_corr >= 0.25)
            status, conf = ('pass' if ok else 'fail'), 0.8 if pix_corr is not None else 0.6
        repair = None
        if status == 'fail' and shot:
            # The repair changes the solved mouth for this line: shift it by the measured lag (the mouth
            # trailed the voice by `lag` frames when lag > 0) and/or open it wider when the rendered mouth
            # barely followed the voice. Repeating a re-render with unchanged inputs cannot help.
            fix = {}
            if abs(lag) > 2:
                fix['mouth_shift_frames'] = -lag
            if corr < 0.3 or (pix_corr is not None and pix_corr < 0.25):
                fix['mouth_gain'] = 1.3
            repair = {'action': 're_render_shot', 'shot': shot['id'], 'params': {'lines': {ln['id']: fix}}}
        ck.add(f'lipsync:{ln["id"]}', f'Lip sync for line {ln["id"]} ({cid})', 'visual', status, 'major', ev, conf,
               'telemetry+pixel+audio', frames=(a, b), target={'kind': 'shot', 'id': shot['id'] if shot else None},
               repair=repair)


def _voiceover(ck, m, cid, ln, recs, dialog_by_line):
    """Narration is voice-over: lip sync does not apply, and the narrator's mouth must not mouth it on screen."""
    a, b = ln['start_frame'], ln['est_end_frame']
    ck.add(f'lipsync:{ln["id"]}', f'Lip sync for line {ln["id"]}', 'visual', 'skipped', 'info',
           {'reason': 'Narration is voice-over; the narrator does not lip-sync it'}, 0.95, 'manifest',
           frames=(a, b), target={'kind': 'line', 'id': ln['id']})
    vis = [f for f in range(a, b) if recs.get(f) and on_screen(recs[f]) and recs[f]['face_dot'] > 0.2
           and recs[f]['face_height_frac'] > 0.05]
    env = dialog_by_line.get(ln['id'])
    if len(vis) < 0.5 * max(1, b - a) or env is None or not len(env):
        ck.add(f'voiceover:{ln["id"]}', f'Narrator stays silent on screen during narration {ln["id"]}', 'visual',
               'skipped', 'info', {'reason': 'Narrator face not on screen for most of the narration'}, 0.9,
               'telemetry', frames=(a, b), target={'kind': 'line', 'id': ln['id']})
        return
    mouth = np.array([recs[f]['mouth_open'] if recs.get(f) else 0 for f in range(a, b)])
    e = np.interp(np.arange(len(mouth)), np.linspace(0, len(mouth) - 1, len(env)), env)
    corr = 0.0
    if mouth.std() > 0.02:
        for L in range(-4, 5):
            x, y = (mouth[L:], e[:len(e) - L]) if L >= 0 else (mouth[:L], e[-L:])
            if len(x) > 5 and x.std() > 1e-6 and y.std() > 1e-6:
                corr = max(corr, float(np.corrcoef(x, y)[0, 1]))
    ok = corr < 0.5
    shot = shot_at(m, a)
    ck.add(f'voiceover:{ln["id"]}', f'Narrator stays silent on screen during narration {ln["id"]} ({cid})', 'visual',
           'pass' if ok else 'fail', 'minor',
           {'mouth_voice_correlation': round(corr, 3), 'mouth_open_std': round(float(mouth.std()), 4),
            'visible_frames': len(vis), 'method': 'scene mouth opening vs narration voice envelope (should not follow)'},
           0.7, 'telemetry+audio', frames=(a, b), target={'kind': 'shot', 'id': shot['id'] if shot else None})


def _camera(ck, m, tele):
    jumps = []
    for s in m['shots']:
        prev = None
        for f in range(s['start_frame'], s['end_frame']):
            r = tele.get(f)
            if not r:
                prev = None
                continue
            loc = r['camera']['location']
            fw = r['camera'].get('forward')
            if prev:
                d = math.dist(loc, prev[0])
                ang = 0.0
                if fw and prev[1]:
                    ang = math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(fw, prev[1]))))))
                if d > 0.3 or ang > 10:
                    jumps.append({'frame': f, 'shot': s['id'], 'move_m': round(d, 3), 'turn_deg': round(ang, 2)})
            prev = (loc, fw)
    ck.add('camera_jumps', 'Smooth camera within shots (no jumps)', 'visual', 'fail' if jumps else 'pass', 'major',
           {'jumps': jumps[:8], 'thresholds': {'move_m_per_frame': 0.3, 'turn_deg_per_frame': 10}}, 0.9, 'telemetry',
           frames=(jumps[0]['frame'] - 1, jumps[0]['frame']) if jumps else None,
           target={'kind': 'shot', 'id': jumps[0]['shot']} if jumps else None,
           repair={'action': 're_render_shot', 'shot': jumps[0]['shot'], 'params': {'camera_smooth': True}} if jumps else None)


def _sightlines(ck, m, tele, solved):
    """The shot's subject must not be hidden behind (or have the camera inside) another character.

    Uses the evaluated scene: camera position, the subject's face and the other characters' positions,
    with each body approximated by an upright cylinder.
    """
    scales = solved.get('scales', {})
    for s in m['shots']:
        subj = s['camera']['subject']
        frames = [f for f in range(s['start_frame'], s['end_frame']) if tele.get(f)]
        if not frames or subj not in tele[frames[0]]['characters']:
            continue
        blocked = []
        for f in frames:
            r = tele[f]
            cam = np.array(r['camera']['location'], dtype=float)
            face = np.array(r['characters'][subj]['face'], dtype=float)
            seg = face - cam
            length = float(np.linalg.norm(seg))
            stop = max(0.0, 1.0 - 0.35 / max(length, 1e-6))
            for oid, o in r['characters'].items():
                if oid == subj:
                    continue
                sc = scales.get(oid, 1.0)
                ctr, z0 = np.array(o['root'][:2], dtype=float), float(o['root'][2])
                top = z0 + 2.22 * sc
                pts = cam + np.outer(np.linspace(0.0, stop, 48), seg)
                inside = (np.hypot(pts[:, 0] - ctr[0], pts[:, 1] - ctr[1]) < 0.42 * sc) & \
                         (pts[:, 2] >= z0 - 0.05) & (pts[:, 2] <= top)
                if inside.any():
                    blocked.append((f, oid, bool(inside[0])))
                    break
        # The camera inside a character is always a defect; the subject briefly passing behind someone
        # (a walk past) is normal staging unless it hides them for a large part of the shot.
        inside = sum(1 for b in blocked if b[2])
        bad = inside >= 3 or len(blocked) > max(15, 0.25 * len(frames))
        ev = {'subject': subj, 'frames_blocked': len(blocked), 'shot_frames': len(frames),
              'blocked_by': sorted({b[1] for b in blocked}), 'camera_inside_character': inside > 0,
              'frames_camera_inside': inside, 'tolerance_frames': int(max(15, 0.25 * len(frames))),
              'method': 'camera-to-face line vs character body cylinders in the evaluated scene'}
        ck.add(f'sightline:{s["id"]}', f'Shot {s["id"]}: {subj} is not hidden behind another character', 'visual',
               'fail' if bad else 'pass', 'critical', ev, 0.9, 'telemetry',
               frames=(blocked[0][0], blocked[-1][0] + 1) if bad else None, target={'kind': 'shot', 'id': s['id']},
               repair={'action': 're_render_shot', 'shot': s['id'], 'params': {'camera_clear': True}} if bad else None)
    _scenery_sightlines(ck, m, tele, solved)


def _scenery_sightlines(ck, m, tele, solved):
    """The camera is never inside scenery, and scenery never hides the shot subject's face.

    Scenery is the set layout the solver used (solved['set'], recomputed deterministically from the
    manifest for older solved files) plus the big static story props, as the same boxes the camera solver
    avoids. Camera and face positions come from the evaluated scene (telemetry). The Blender ray test
    toward each subject face (telemetry 'face_blocker') is reported alongside.
    """
    from ..animation import sets as SETS
    lay = solved.get('set')
    if lay is None and (m.get('setting') or {}).get('preset') and m.get('tracks'):
        lay = SETS.layout(m, solved.get('scales'))
    if lay is None:
        return
    set_boxes = SETS.piece_boxes(lay)
    face_boxes = SETS.box_arrays(set_boxes + SETS.prop_boxes(m))
    prop_boxes_only = SETS.box_arrays(set_boxes)
    for s in m['shots']:
        subj = s['camera']['subject']
        frames = [f for f in range(s['start_frame'], s['end_frame']) if tele.get(f)]
        if not frames:
            continue
        faces = subj in tele[frames[0]]['characters'] or subj == 'two_shot'
        boxes = face_boxes if faces else prop_boxes_only
        if boxes is None:
            continue
        cams = np.array([tele[f]['camera']['location'] for f in frames], dtype=float)
        inside = SETS.inside(boxes, cams).any(axis=1)
        p0, p1, owner = [], [], []
        for i, f in enumerate(frames):
            chars = tele[f]['characters']
            if subj in chars:
                targets = [chars[subj]['face']]
            elif subj == 'two_shot':
                targets = [c['face'] for c in chars.values()]
            elif isinstance(subj, str) and subj.startswith('prop:') and subj[5:] in tele[f].get('props', {}):
                loc = tele[f]['props'][subj[5:]]['location']
                targets = [[loc[0], loc[1], loc[2] + 0.15]]
            else:
                targets = []
            for t in targets:
                t = np.array(t, dtype=float)
                d = t - cams[i]
                n = float(np.linalg.norm(d))
                if n > 0.1:
                    p0.append(cams[i])
                    p1.append(t - d / n * 0.05)
                    owner.append(i)
        hidden = np.zeros(len(frames), dtype=bool)
        if p0:
            for i, h in zip(owner, SETS.segment_hits(boxes, np.array(p0), np.array(p1)).any(axis=1)):
                hidden[i] |= bool(h)
        rays = sum(1 for f in frames if str((tele[f]['characters'].get(subj) or {}).get('face_blocker') or '')
                   .startswith('set:'))
        n_inside, n_hidden = int(inside.sum()), int(hidden.sum())
        # Scenery is laid out around the camera reach, so any frame inside it or hidden by it is a layout
        # or solver defect; a couple of frames are tolerated only where a face grazes a box edge.
        tol = int(max(3, 0.05 * len(frames)))
        bad = n_inside > 0 or n_hidden > tol
        hit = [frames[i] for i in np.nonzero(inside | hidden)[0].tolist()]
        ev = {'subject': subj, 'frames_camera_inside': n_inside, 'frames_face_hidden': n_hidden,
              'shot_frames': len(frames), 'tolerance_frames': tol, 'rendered_ray_hits': rays,
              'scenery_boxes': len(boxes[0]),
              'method': 'camera point and camera-to-face segment vs set piece and static prop boxes'}
        ck.add(f'scenery:{s["id"]}', f'Shot {s["id"]}: scenery never swallows the camera or hides {subj}', 'visual',
               'fail' if bad else 'pass', 'critical', ev, 0.9, 'telemetry',
               frames=(hit[0], hit[-1] + 1) if bad else None, target={'kind': 'shot', 'id': s['id']},
               repair={'action': 're_render_shot', 'shot': s['id'], 'params': {'camera_clear': True}} if bad else None)


def _props(ck, m, tele, solved):
    """Rendered prop positions must match the plan (static, held or falling)."""
    problems = []
    expected = solved.get('props', {})
    for p in m['setting'].get('props', []):
        pid = p['id']
        plan = expected.get(pid)
        for f in sorted(tele):
            pr = tele[f]['props'].get(pid)
            if not pr:
                problems.append({'prop': pid, 'frame': f, 'issue': 'missing from the rendered scene'})
                break
            if not pr['visible']:
                problems.append({'prop': pid, 'frame': f, 'issue': 'hidden'})
                break
            if plan and f < len(plan):
                d = math.dist(pr['location'], plan[f]['location'])
                if d > 0.05:
                    problems.append({'prop': pid, 'frame': f, 'issue': f'{d:.2f} m away from its planned position'})
                    break
    ck.add('props_continuity', 'Props persist and move only when handled', 'visual',
           'fail' if problems else 'pass', 'major', {'problems': problems[:6], 'props': len(m['setting'].get('props', []))},
           0.9, 'telemetry', frames=(problems[0]['frame'], problems[0]['frame'] + 1) if problems else None,
           target={'kind': 'shot', 'id': shot_at(m, problems[0]['frame'])['id']} if problems else None,
           repair={'action': 're_render_shot', 'shot': shot_at(m, problems[0]['frame'])['id']} if problems else None)


def _identity(ck, m, tele, joined_path):
    """Colour-signature consistency of each character across shots (pixel)."""
    sig = {}
    for s in m['shots']:
        mid = (s['start_frame'] + s['end_frame']) // 2
        r = tele.get(mid)
        if not r:
            continue
        try:
            img = decode_rgb_frame(joined_path, mid, m['fps']).astype(np.float32)
        except media.MediaError:
            continue
        h, w = img.shape[:2]
        for cid, rec in r['characters'].items():
            if not on_screen(rec, 0.02):
                continue
            x0, y0, x1, y1 = rec['bbox2d']
            xa, xb = int(max(0, x0) * w), int(min(1, x1) * w)
            ya, yb = int(max(0, y0) * h), int(min(1, y1) * h)
            if xb - xa < 8 or yb - ya < 8:
                continue
            crop = img[ya:yb, xa:xb].reshape(-1, 3)
            hist, _ = np.histogramdd(crop // 32, bins=(8, 8, 8), range=((0, 8), (0, 8), (0, 8)))
            hist = hist.flatten() / max(1, hist.sum())
            sig.setdefault(cid, []).append((s['id'], hist))
    worst = []
    for cid, items in sig.items():
        if len(items) < 2:
            continue
        ref = np.mean([h for _, h in items], axis=0)
        for sid, h in items:
            bc = float(np.sum(np.sqrt(h * ref)))
            worst.append({'character': cid, 'shot': sid, 'similarity': round(bc, 3)})
    low = [w for w in worst if w['similarity'] < 0.55]
    ck.add('identity_consistency', 'Character colour identity consistent across shots', 'visual',
           'pass' if not low else 'uncertain', 'major',
           {'per_shot_similarity': worst[:20], 'low': low,
            'note': 'Bhattacharyya similarity of colour histograms inside each character\'s screen box. '
                    'Background and framing changes lower it; low values need a look, not automatic failure.'},
           0.6, 'pixel', target={'kind': 'shot', 'id': low[0]['shot']} if low else None)


def dialog_envelopes(m, line_audio):
    """Per-line RMS envelope (one value per video frame) from the voiced files."""
    fps = m['fps']
    out = {}
    for ln in m['lines']:
        la = line_audio.get(ln['id'])
        if not la:
            continue
        s = A.decode(la['file'])
        n = ln['est_end_frame'] - ln['start_frame']
        if n <= 0:
            continue
        hop = A.SR / fps
        env = np.array([np.sqrt(np.mean(s[int(i * hop):int((i + 1) * hop)] ** 2) + 1e-12) if int(i * hop) < len(s) else 0.0
                        for i in range(n)])
        out[ln['id']] = env
    return out

