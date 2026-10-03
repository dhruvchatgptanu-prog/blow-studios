"""Vocabulary of the production manifest and the rig capabilities behind it.

The same lists are used by the LLM prompt (as allowed values), the validator,
the director's-script writer and the Blender renderer, so a direction the rig
cannot perform is rejected up front instead of silently replaced.
"""
SCHEMA_VERSION = '1.0'

COORDINATE_SYSTEM = {
    'world': ('Scene units are metres. Right-handed. Ground plane is z = 0. In the establishing camera view, '
              '+x points screen-right, +y points away from the camera (depth) and +z points up. '
              'Character positions are [x, y] on the ground plane.'),
    'facing': ('Facing is a yaw angle in degrees about +z. 0 = facing the establishing camera (-y), '
               '90 = facing screen-right (+x), -90 = facing screen-left (-x), 180 = facing away.'),
    'head': 'Head yaw/pitch/roll in degrees relative to the torso. +yaw turns toward the character\'s left; +pitch looks up.',
    'screen': 'Normalised frame coordinates [0, 1]; x to the right, y downward, (0, 0) is the top-left corner.',
    'time': 'Integer frame indices are authoritative. Timestamps are derived as frame / fps.',
}

PURPOSES = ['hook', 'setup', 'inciting', 'escalation', 'reaction', 'reveal', 'climax', 'payoff', 'button',
            'transition']

SETTING_PRESETS = {
    'sky_obby': 'Floating studded platforms above a sea of clouds, open blue sky',
    'lava_obby': 'Studded platforms over a glowing lava floor, warm under-light',
    'town_street': 'Blocky suburban street with simple houses, trees and a road',
    'classroom': 'Block-style classroom with desks and a board',
    'night_forest': 'Blocky trees at night, moonlight and a campfire',
    'bedroom': 'Cosy block bedroom with bed, desk and window',
    'studio': 'Neutral gradient stage (tests and character turnarounds)',
}
TIME_OF_DAY = ['morning', 'noon', 'sunset', 'night']
LIGHTING = ['natural', 'warm_key_cool_fill', 'dramatic_rim', 'soft_overcast', 'spooky_low']

PROP_TYPES = ['platform', 'checkpoint_flag', 'coin', 'key', 'chest', 'door', 'button', 'sign', 'trophy', 'ball',
              'crate', 'lava_block', 'spring_pad', 'phone', 'gift', 'pizza', 'cup', 'bed', 'desk', 'chair', 'tree',
              'house', 'campfire', 'lamp', 'rock']
# Props a hand can hold (attach to hand bone).
HOLDABLE = {'coin', 'key', 'ball', 'phone', 'gift', 'pizza', 'cup', 'trophy', 'sign'}

MOUTH_SHAPES = ['neutral', 'smile', 'grin', 'open_smile', 'frown', 'o', 'gasp', 'grimace', 'smirk', 'pout', 'flat']
ARM_POSES = ['rest', 'hips', 'chest', 'point', 'wave', 'raise', 'reach', 'cover_mouth', 'head_scratch', 'fist_pump',
             'arms_out', 'cross', 'hold_prop', 'shrug', 'thumbs_up', 'facepalm', 'chin']
POSTURES = ['upright', 'slumped', 'crouch', 'ready', 'proud']
STANCES = ['neutral', 'wide', 'narrow', 'step_forward', 'step_back']
WEIGHTS = ['left', 'center', 'right']

# action -> description of what the rig really does. Anything not listed is
# rejected as unsupported.
ACTIONS = {
    'idle': 'Breathing and subtle weight shift in place',
    'talk': 'Conversational beat gestures of the head and the free arm while speaking',
    'walk': 'Walk cycle from the start position to the end position with planted feet',
    'run': 'Run cycle from start to end position with planted feet and arm swing',
    'jump': 'Crouch anticipation, take-off, ballistic arc to the end position, knee-bend landing',
    'hop': 'Small two-foot hop in place',
    'crouch': 'Lower the hips into a crouch and hold',
    'stand_up': 'Rise from crouch to standing',
    'stumble': 'Trip forward, windmill arms, recover balance',
    'fall_down': 'Lose balance and sit down hard onto the ground',
    'get_up': 'Push up from sitting back to standing',
    'turn': 'Rotate in place from the start facing to the end facing with stepping feet',
    'look_around': 'Head scans left and right',
    'nod': 'Head nods',
    'head_shake': 'Head shakes side to side',
    'wave': 'Raise a hand and wave (params.hand)',
    'point': 'Point with a hand toward the eye-line target (params.hand)',
    'celebrate': 'Jump with fists raised, land, fist pump',
    'shrug': 'Raise shoulders and turn palms up',
    'flinch': 'Quick startled recoil: torso back, arms up, eyes wide',
    'reach': 'Extend a hand toward a prop (params.prop, params.hand)',
    'grab': 'Reach a prop and attach it to the hand at contact (params.prop, params.hand)',
    'drop': 'Release the held prop and let it fall to the ground (params.hand)',
    'push': 'Press a button or object with one hand (params.prop, params.hand)',
    'facepalm': 'Hand to forehead, head dips',
    'think': 'Hand to chin, head tilt, eyes up',
    'cower': 'Crouch with arms raised to protect the head',
    'dance': 'Bouncy two-step dance in place',
}
HAND_ACTIONS = {'wave', 'point', 'reach', 'grab', 'drop', 'push'}
LOCOMOTION = {'walk', 'run', 'jump'}

FRAMINGS = ['extreme_wide', 'wide', 'full', 'medium_wide', 'medium', 'medium_close', 'close_up', 'extreme_close_up']
# Fraction of the subject's height (feet to head-top) visible vertically.
FRAMING_HEIGHT = {'extreme_wide': 6.0, 'wide': 3.0, 'full': 1.35, 'medium_wide': 1.0, 'medium': 0.72,
                  'medium_close': 0.52, 'close_up': 0.40, 'extreme_close_up': 0.25}
FACE_READABLE = {'full', 'medium_wide', 'medium', 'medium_close', 'close_up', 'extreme_close_up'}
CAMERA_ANGLES = ['eye', 'low', 'high', 'overhead']
CAMERA_SIDES = ['front', 'three_quarter_left', 'three_quarter_right', 'profile_left', 'profile_right',
                'over_shoulder_left', 'over_shoulder_right', 'back']
CAMERA_MOVES = ['static', 'push_in', 'pull_out', 'pan_left', 'pan_right', 'truck_left', 'truck_right',
                'orbit_left', 'orbit_right', 'follow', 'crane_up', 'crane_down']
EASES = ['linear', 'in', 'out', 'in_out']
TRANSITIONS = ['cut', 'fade_in', 'whip']

EMOTIONS = ['neutral', 'happy', 'excited', 'startled', 'worried', 'sad', 'angry', 'determined', 'smug', 'scared',
            'confused', 'embarrassed', 'laughing', 'proud', 'bored', 'disgusted', 'relieved', 'curious']
PACES = ['slow', 'normal', 'fast']
VOLUMES = ['whisper', 'soft', 'normal', 'loud', 'shout']
# Speaking rate used for planning, words per second.
PACE_WPS = {'slow': 2.1, 'normal': 2.7, 'fast': 3.3}

# Production pace. Plans are authored in "story time"; compiling divides every
# time quantity by the timeline pace, so pace 1.5 plays the same story in two
# thirds of the time. Voices speak natively faster by speech_rate (no time
# stretching); speech much faster than ~1.3x stops sounding natural, so the
# remaining compression comes out of the pauses between lines.
PACE_RANGE = (1.0, 2.0)
SPEECH_RATE_RANGE = (0.8, 1.6)
# Ceiling on the total speed-up of a voiced line: native speech_rate times any
# tempo the line-fitting step applies afterwards.
MAX_LINE_SPEEDUP = 1.5

SFX_CUES = ['whoosh', 'pop', 'boing', 'thud', 'ding', 'coin', 'click', 'rumble', 'sizzle', 'swoosh_up', 'fail_horn',
            'sparkle', 'footstep', 'gasp_sting', 'drumroll', 'tada', 'beep']
MUSIC_CUES = ['playful', 'tension', 'triumph', 'sad', 'mystery', 'chill', 'none']

# Expression presets map a label to concrete facial controls. A beat may use a
# preset and then override individual controls.
EXPRESSIONS = {
    'neutral': {'brows': {'inner': 0.0, 'outer': 0.0, 'asym': 0.0}, 'eyes': {'open': 1.0, 'squint': 0.0},
                'mouth': {'shape': 'neutral', 'open': 0.0}},
    'happy': {'brows': {'inner': 0.15, 'outer': 0.2, 'asym': 0.0}, 'eyes': {'open': 0.92, 'squint': 0.35},
              'mouth': {'shape': 'smile', 'open': 0.15}},
    'excited': {'brows': {'inner': 0.5, 'outer': 0.65, 'asym': 0.0}, 'eyes': {'open': 1.18, 'squint': 0.0},
                'mouth': {'shape': 'open_smile', 'open': 0.6}},
    'startled': {'brows': {'inner': 0.9, 'outer': 0.85, 'asym': 0.0}, 'eyes': {'open': 1.3, 'squint': 0.0},
                 'mouth': {'shape': 'o', 'open': 0.35}},
    'worried': {'brows': {'inner': 0.85, 'outer': -0.35, 'asym': 0.0}, 'eyes': {'open': 1.05, 'squint': 0.0},
                'mouth': {'shape': 'frown', 'open': 0.1}},
    'sad': {'brows': {'inner': 0.75, 'outer': -0.55, 'asym': 0.0}, 'eyes': {'open': 0.72, 'squint': 0.0},
            'mouth': {'shape': 'frown', 'open': 0.0}},
    'angry': {'brows': {'inner': -0.95, 'outer': 0.3, 'asym': 0.0}, 'eyes': {'open': 0.85, 'squint': 0.5},
              'mouth': {'shape': 'grimace', 'open': 0.25}},
    'determined': {'brows': {'inner': -0.55, 'outer': 0.0, 'asym': 0.0}, 'eyes': {'open': 0.9, 'squint': 0.3},
                   'mouth': {'shape': 'flat', 'open': 0.0}},
    'smug': {'brows': {'inner': -0.2, 'outer': 0.35, 'asym': 0.45}, 'eyes': {'open': 0.75, 'squint': 0.3},
             'mouth': {'shape': 'smirk', 'open': 0.0}},
    'scared': {'brows': {'inner': 1.0, 'outer': 0.4, 'asym': 0.0}, 'eyes': {'open': 1.3, 'squint': 0.0},
               'mouth': {'shape': 'grimace', 'open': 0.45}},
    'confused': {'brows': {'inner': 0.3, 'outer': 0.2, 'asym': 0.8}, 'eyes': {'open': 1.0, 'squint': 0.15},
                 'mouth': {'shape': 'flat', 'open': 0.05}},
    'embarrassed': {'brows': {'inner': 0.55, 'outer': -0.1, 'asym': 0.0}, 'eyes': {'open': 0.7, 'squint': 0.2},
                    'mouth': {'shape': 'smile', 'open': 0.0}},
    'laughing': {'brows': {'inner': 0.35, 'outer': 0.3, 'asym': 0.0}, 'eyes': {'open': 0.35, 'squint': 0.8},
                 'mouth': {'shape': 'open_smile', 'open': 0.85}},
    'proud': {'brows': {'inner': 0.1, 'outer': 0.3, 'asym': 0.0}, 'eyes': {'open': 0.85, 'squint': 0.2},
              'mouth': {'shape': 'grin', 'open': 0.1}},
    'bored': {'brows': {'inner': -0.1, 'outer': -0.3, 'asym': 0.0}, 'eyes': {'open': 0.55, 'squint': 0.0},
              'mouth': {'shape': 'flat', 'open': 0.0}},
    'disgusted': {'brows': {'inner': -0.6, 'outer': -0.2, 'asym': 0.3}, 'eyes': {'open': 0.7, 'squint': 0.6},
                  'mouth': {'shape': 'grimace', 'open': 0.15}},
    'relieved': {'brows': {'inner': 0.45, 'outer': 0.0, 'asym': 0.0}, 'eyes': {'open': 0.65, 'squint': 0.0},
                 'mouth': {'shape': 'smile', 'open': 0.2}},
    'curious': {'brows': {'inner': 0.35, 'outer': 0.5, 'asym': 0.4}, 'eyes': {'open': 1.08, 'squint': 0.0},
                'mouth': {'shape': 'pout', 'open': 0.05}},
}

DEFAULT_POSE = {
    'position': [0.0, 0.0],
    'facing': 0.0,
    'eye_target': {'kind': 'camera'},
    'head': {'yaw': 0.0, 'pitch': 0.0, 'roll': 0.0},
    'expression': 'neutral',
    'brows': {'inner': 0.0, 'outer': 0.0, 'asym': 0.0},
    'eyes': {'open': 1.0, 'squint': 0.0},
    'mouth': {'shape': 'neutral', 'open': 0.0},
    'shoulders': {'raise': 0.0},
    'arms': {'left': 'rest', 'right': 'rest'},
    'torso': {'lean_forward': 0.0, 'lean_side': 0.0, 'twist': 0.0},
    'posture': 'upright',
    'feet': {'stance': 'neutral', 'weight': 'center'},
}

RANGES = {
    ('head', 'yaw'): (-70, 70), ('head', 'pitch'): (-40, 40), ('head', 'roll'): (-30, 30),
    ('brows', 'inner'): (-1, 1), ('brows', 'outer'): (-1, 1), ('brows', 'asym'): (-1, 1),
    ('eyes', 'open'): (0, 1.35), ('eyes', 'squint'): (0, 1),
    ('mouth', 'open'): (0, 1), ('shoulders', 'raise'): (-1, 1),
    ('torso', 'lean_forward'): (-25, 40), ('torso', 'lean_side'): (-25, 25), ('torso', 'twist'): (-50, 50),
}
WORLD_LIMIT = 30.0  # metres from origin
MAX_SPEED = {'walk': 1.8, 'run': 5.5, 'jump': 6.0, 'stumble': 2.0, 'celebrate': 1.0}
JUMP_MAX_DISTANCE = 4.5  # metres between take-off and landing
