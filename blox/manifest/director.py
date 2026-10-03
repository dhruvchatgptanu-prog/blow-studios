"""Readable director's script generated from the compiled manifest.

The prose is derived from the same resolved poses the renderer uses, so the
script cannot silently drift from the animation plan.
"""
from . import schema as S
from .compile import tc

ARM_TEXT = {
    'rest': 'relaxes at the side', 'hips': 'rests on the hip', 'chest': 'rises to chest height',
    'point': 'points forward', 'wave': 'lifts to wave', 'raise': 'shoots up overhead', 'reach': 'reaches forward',
    'cover_mouth': 'covers the mouth', 'head_scratch': 'scratches the back of the head', 'fist_pump': 'pumps a fist',
    'arms_out': 'swings out wide', 'cross': 'folds across the chest', 'hold_prop': 'holds the prop',
    'shrug': 'turns palm-up', 'thumbs_up': 'gives a thumbs-up', 'facepalm': 'presses to the forehead',
    'chin': 'rests on the chin',
}
MOUTH_TEXT = {
    'neutral': 'relaxes the mouth', 'smile': 'smiles', 'grin': 'breaks into a wide grin',
    'open_smile': 'smiles open-mouthed', 'frown': 'frowns', 'o': 'rounds the mouth into an "o"',
    'gasp': 'gasps', 'grimace': 'grimaces through clenched teeth', 'smirk': 'smirks to one side',
    'pout': 'pouts', 'flat': 'sets the mouth in a flat line',
}
POSTURE_TEXT = {'upright': 'stands upright', 'slumped': 'slumps', 'crouch': 'drops into a crouch',
                'ready': 'settles into a ready stance', 'proud': 'puffs up proudly'}
FRAMING_TEXT = {k: k.replace('_', ' ') + ' shot' for k in S.FRAMINGS}
FRAMING_TEXT.update({'close_up': 'close-up', 'extreme_close_up': 'extreme close-up', 'medium_close': 'medium close-up'})


def _target_text(et, names):
    kind = (et or {}).get('kind')
    if kind == 'camera':
        return 'toward the camera'
    if kind == 'character':
        return 'toward ' + names.get(et.get('id'), et.get('id', 'the other character'))
    if kind == 'prop':
        return 'toward the ' + str(et.get('id', 'prop')).replace('_', ' ')
    if kind == 'point':
        return 'toward a point off to the side'
    return 'straight ahead'


def _facing_text(deg):
    d = ((deg + 180) % 360) - 180
    if abs(d) < 20:
        return 'the camera'
    if abs(d) > 160:
        return 'away from the camera'
    if 20 <= d <= 160:
        return 'screen-right'
    return 'screen-left'


def describe_change(a, b, name, names):
    """Ordered list of clauses describing the change from pose a to pose b."""
    parts = []
    if a['feet']['weight'] != b['feet']['weight'] and b['feet']['weight'] != 'center':
        parts.append(f'shifts weight onto the {b["feet"]["weight"]} foot')
    elif a['feet']['weight'] != b['feet']['weight']:
        parts.append('settles the weight evenly')
    if a['feet']['stance'] != b['feet']['stance']:
        parts.append('moves into a ' + b['feet']['stance'].replace('_', ' ') + ' stance')
    if a['posture'] != b['posture']:
        parts.append(POSTURE_TEXT[b['posture']])
    dl = b['torso']['lean_forward'] - a['torso']['lean_forward']
    if dl > 6:
        parts.append('leans forward')
    elif dl < -6:
        parts.append('leans back')
    ds = b['shoulders']['raise'] - a['shoulders']['raise']
    if ds > 0.25:
        parts.append('hunches the shoulders up')
    elif ds < -0.25:
        parts.append('lowers the shoulders')
    di = b['brows']['inner'] - a['brows']['inner']
    do = b['brows']['outer'] - a['brows']['outer']
    if di > 0.3:
        parts.append('raises the inner eyebrows')
    elif di < -0.3:
        parts.append('pulls the brows down and together')
    if do > 0.3:
        parts.append('lifts the outer brows')
    elif do < -0.3:
        parts.append('lets the outer brows droop')
    da = abs(b['brows'].get('asym', 0)) - abs(a['brows'].get('asym', 0))
    if da > 0.3:
        parts.append('cocks one eyebrow')
    elif da < -0.3:
        parts.append('evens out the brows')
    de = b['eyes']['open'] - a['eyes']['open']
    tgt = _target_text(b['eye_target'], names)
    if de > 0.15:
        parts.append('widens the eyes ' + tgt)
    elif de < -0.15:
        parts.append('narrows the eyes ' + tgt)
    elif a['eye_target'] != b['eye_target']:
        parts.append('looks ' + tgt)
    if b['eyes']['squint'] - a['eyes']['squint'] > 0.3:
        parts.append('squints')
    if a['mouth']['shape'] != b['mouth']['shape']:
        m = MOUTH_TEXT[b['mouth']['shape']]
        if b['mouth']['shape'] in ('o', 'gasp') and b['mouth']['open'] < 0.4:
            m = 'parts the mouth slightly'
        parts.append(m)
    elif b['mouth']['open'] - a['mouth']['open'] > 0.3:
        parts.append('opens the mouth')
    dyaw = b['head']['yaw'] - a['head']['yaw']
    if abs(dyaw) > 12:
        parts.append('turns the head to the ' + ('left' if dyaw > 0 else 'right'))
    dp = b['head']['pitch'] - a['head']['pitch']
    if dp > 10:
        parts.append('tips the head up')
    elif dp < -10:
        parts.append('drops the head')
    if abs(b['head']['roll'] - a['head']['roll']) > 10:
        parts.append('tilts the head')
    arm_sentences = []
    for side in ('left', 'right'):
        if a['arms'][side] != b['arms'][side]:
            arm_sentences.append(f'{side.capitalize()} hand {ARM_TEXT[b["arms"][side]]}.')
    if abs(((b['facing'] - a['facing'] + 180) % 360) - 180) > 20:
        parts.append('turns to face ' + _facing_text(b['facing']))
    return parts, arm_sentences


ACTION_PHASE_TEXT = {'anticipation': 'anticipation', 'main': 'main movement', 'follow_through': 'follow-through',
                     'hold': 'held reaction'}


def beat_text(m, beat, names):
    fps = m['fps']
    out = []
    a_s = beat['start_frame'] // fps
    head = (f'{tc(beat["start_frame"], fps)}–{tc(beat["end_frame"], fps)}  [frames {beat["start_frame"]}–{beat["end_frame"]}]'
            f'  {beat["purpose"].upper() or "BEAT"} · shot {beat["shot"]}')
    out.append(head)
    if beat['description']:
        out.append('  ' + beat['description'])
    st = beat['setting']
    props = ', '.join(st['props_visible']) or 'none'
    out.append(f'  Setting: {st["preset"]} at {st["time_of_day"]}, {st["lighting"]} lighting. Props: {props}.')
    for ch in beat['characters']:
        n = names.get(ch['id'], ch['id'])
        parts, arms = describe_change(ch['start'], ch['end'], n, names)
        text = f'  {n.upper()} ({ch["expression_label"]}): '
        pos = ch['end']['position']
        text += (n + ' ' + ', '.join(parts) + '. ') if parts else f'{n} holds the {ch["expression_label"]} pose. '
        text += ' '.join(arms)
        text += f' Ends at x={pos[0]:.2f} m, depth={pos[1]:.2f} m, facing {_facing_text(ch["end"]["facing"])}.'
        out.append(text.rstrip())
        for act in ch['actions']:
            ph = '; '.join(f'{ACTION_PHASE_TEXT[p["phase"]]} {p["start_frame"]}–{p["end_frame"]}' for p in act['phases'])
            label = act['type'] + (f' ({act["params"].get("hand")} hand)' if act['params'].get('hand') else '')
            out.append(f'    Action {label}: {S.ACTIONS.get(act["type"], "")}. {ph}.')
        if ch['blinks']:
            out.append('    Blinks at frames ' + ', '.join(str(f) for f in ch['blinks']) + '.')
    cam = beat.get('camera')
    if cam:
        s, e = cam['start'], cam['end']
        frm = FRAMING_TEXT[s['framing']]
        if e['framing'] != s['framing']:
            frm += ' → ' + FRAMING_TEXT[e['framing']]
        out.append(f'  Camera: {frm}, {cam["move"].replace("_", " ")} ({cam["ease"]}), {s["angle"]} angle, '
                   f'{s["side"].replace("_", " ")} of {s["subject"]}. Renderer: {cam["renderer"]}.')
    lines = {l['id']: l for l in m['lines']}
    for lid in beat['dialogue']:
        ln = lines[lid]
        if ln['start_frame'] >= beat['start_frame']:
            spk = names.get(ln['speaker'], ln['speaker']).upper()
            if ln.get('kind') == 'narration':
                # Storytime voice-over: captioned, no lip sync, may play over any shot.
                spk += ' (V.O. narration, mouth closed)'
            note = f', {ln["delivery"]}' if ln['delivery'] else ''
            pause = f' Pause {ln["pause_after_ms"]} ms after.' if ln['pause_after_ms'] else ''
            out.append(f'  {spk} ({ln["emotion"]}, {ln["pace"]}, {ln["volume"]}{note}) at {tc(ln["start_frame"], fps)}: '
                       f'"{ln["text"]}"{pause}')
    sfx = {x['id']: x for x in m['sfx']}
    if beat['sfx']:
        out.append('  SFX: ' + ', '.join(f'{sfx[i]["cue"]} at {tc(sfx[i]["frame"], fps)}' for i in beat['sfx']))
    if beat['music'] and (beat['index'] == 0 or any(mu['frame'] == beat['start_frame'] for mu in m['music'])):
        out.append(f'  Music: {beat["music"]}')
    caps = {c['id']: c for c in m['captions']}
    starting = [caps[c] for c in beat['captions'] if caps[c]['start_frame'] >= beat['start_frame']]
    if starting:
        out.append('  Captions: ' + ' | '.join(f'"{c["text"]}" ({c["timing"]})' for c in starting))
    if beat['continuity']:
        out.append('  Continuity: ' + '; '.join(map(str, beat['continuity'])))
    qa = [q for q in beat['qa'] if not q.get('auto')]
    auto = [q for q in beat['qa'] if q.get('auto')]
    if qa or auto:
        out.append('  QA: ' + '; '.join(_qa_text(q) for q in qa + auto))
    _ = a_s
    return '\n'.join(out)


def _qa_text(q):
    c = q.get('check')
    if c == 'expression_visible':
        return f'{q.get("character")} "{q.get("expression")}" readable on screen'
    if c == 'action_occurs':
        return f'{q.get("character")} visibly performs {q.get("action")}'
    if c == 'feet_grounded':
        return f'{q.get("character")} feet planted, no sliding'
    if c == 'prop_contact':
        return f'{q.get("character")} {q.get("hand")} hand contacts {q.get("prop")}'
    if c == 'dialogue_audible':
        return 'lines ' + ', '.join(q.get('lines', [])) + ' audible and unclipped'
    return str(q.get('check')) + (': ' + str(q.get('note')) if q.get('note') else '')


def script(m, character_names=None):
    names = {c['id']: c['id'] for c in m['cast']}
    names.update(character_names or {})
    fps = m['fps']
    head = [
        f'DIRECTOR\'S SCRIPT — {m["title"]}',
        f'{m["duration_frames"] / fps:.2f} s · {m["duration_frames"]} frames @ {fps} fps · {m["width"]}×{m["height"]}',
        f'Logline: {m["logline"]}',
        f'Hook (ends {tc(m["hook"]["end_frame"], fps)}): {m["hook"]["text"]}  Question: {m["hook"]["question"]}',
        f'Payoff (from {tc(m["payoff"]["start_frame"], fps)}): {m["payoff"]["text"]}',
        'Coordinates: ' + m['coordinate_system']['world'],
        'Facing: ' + m['coordinate_system']['facing'],
        'Timing: integer frames are authoritative; timestamps are frame/fps (MM:SS.ff).',
        '',
    ]
    pace = m.get('pace') or {}
    if m.get('narrator'):
        head.insert(-1, f'Narrator: {names.get(m["narrator"], m["narrator"])} reads the narration lines as voice-over '
                        '(past tense, captioned, no lip sync).')
    if pace.get('timeline', 1.0) != 1.0 or pace.get('speech_rate', 1.0) != 1.0:
        head.insert(-1, f'Pace: the story plays {pace.get("timeline", 1.0):g}x faster than written (beats are one '
                        f'second of story time each); voices speak {pace.get("speech_rate", 1.0):g}x faster.')
    body = [beat_text(m, b, names) for b in m['beats']]
    return '\n'.join(head) + '\n\n'.join(body) + '\n'
