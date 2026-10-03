"""Original block-character rig shared by the solver and the Blender builder.

The rig is a joint hierarchy (FK skeleton). Each joint has an offset from its
parent in character space at scale 1 and an Euler XYZ rotation (Blender
convention, R = Rz * Ry * Rx). Character space: facing -y, +x is the
character's left, +z up. Rigid block parts hang off joints, like classic
block avatars. Knees and elbows are solved with analytic two-bone IK.

Rotation sign conventions (character space):
* hip/shoulder/elbow/knee X: positive swings the limb backward (+y).
  A forward leg swing or a forward arm raise is negative X; knees bend positive.
  Elbows bend negative (forearm forward/up).
* Left shoulder Y: negative raises the arm out to the side; right is mirrored.
* Root Z: facing angle (0 = toward camera). Head Z: +yaw toward the left.
* Head/neck X: negative looks up.
"""
JOINTS = [
    # name, parent, offset (x, y, z)
    ('root', None, (0.0, 0.0, 0.0)),
    ('pelvis', 'root', (0.0, 0.0, 0.86)),
    ('spine', 'pelvis', (0.0, 0.0, 0.06)),
    ('chest', 'spine', (0.0, 0.0, 0.26)),
    ('neck', 'chest', (0.0, 0.0, 0.28)),
    ('head', 'neck', (0.0, 0.0, 0.04)),
    ('shoulder_l', 'chest', (0.47, 0.0, 0.20)),
    ('elbow_l', 'shoulder_l', (0.0, 0.0, -0.32)),
    ('wrist_l', 'elbow_l', (0.0, 0.0, -0.30)),
    ('shoulder_r', 'chest', (-0.47, 0.0, 0.20)),
    ('elbow_r', 'shoulder_r', (0.0, 0.0, -0.32)),
    ('wrist_r', 'elbow_r', (0.0, 0.0, -0.30)),
    ('hip_l', 'pelvis', (0.18, 0.0, -0.02)),
    ('knee_l', 'hip_l', (0.0, 0.0, -0.40)),
    ('ankle_l', 'knee_l', (0.0, 0.0, -0.38)),
    ('hip_r', 'pelvis', (-0.18, 0.0, -0.02)),
    ('knee_r', 'hip_r', (0.0, 0.0, -0.40)),
    ('ankle_r', 'knee_r', (0.0, 0.0, -0.38)),
]
JOINT_NAMES = [j[0] for j in JOINTS]
PARENT = {j[0]: j[1] for j in JOINTS}
OFFSET = {j[0]: j[2] for j in JOINTS}

UPPER_ARM = 0.32
FOREARM = 0.30
HAND_REACH = 0.10   # wrist to the palm centre
THIGH = 0.40
SHIN = 0.38
ANKLE_HEIGHT = 0.06
HIP_HEIGHT = 0.84   # hip joint above the ground in rest pose
HEIGHT = 2.10       # feet to top of head (without hair)
FACE_CENTER_Z = 1.73  # between eyes and mouth, standing
HEAD_HALF_DEPTH = 0.29
HEAD_CENTER = (0.0, 0.0, 0.30)     # from head joint
FACE_FRONT_Y = -0.29               # head front surface (local to head joint)

# Rigid parts: joint, part name, size (w, d, h), centre offset, colour slot, bevel
PARTS = [
    ('pelvis', 'hips', (0.70, 0.36, 0.24), (0.0, 0.0, -0.03), 'pants', 0.04),
    ('spine', 'belly', (0.72, 0.36, 0.28), (0.0, 0.0, 0.13), 'top', 0.04),
    ('chest', 'torso', (0.76, 0.38, 0.32), (0.0, 0.0, 0.13), 'top', 0.05),
    ('neck', 'neck', (0.22, 0.22, 0.10), (0.0, 0.0, 0.02), 'skin', 0.02),
    ('head', 'head', (0.62, 0.58, 0.60), (0.0, 0.0, 0.30), 'skin', 0.09),
    ('shoulder_l', 'upper_arm_l', (0.25, 0.25, 0.36), (0.0, 0.0, -0.14), 'top', 0.05),
    ('elbow_l', 'forearm_l', (0.23, 0.23, 0.32), (0.0, 0.0, -0.14), 'top', 0.05),
    ('wrist_l', 'hand_l', (0.20, 0.17, 0.19), (0.0, 0.0, -0.08), 'skin', 0.05),
    ('shoulder_r', 'upper_arm_r', (0.25, 0.25, 0.36), (0.0, 0.0, -0.14), 'top', 0.05),
    ('elbow_r', 'forearm_r', (0.23, 0.23, 0.32), (0.0, 0.0, -0.14), 'top', 0.05),
    ('wrist_r', 'hand_r', (0.20, 0.17, 0.19), (0.0, 0.0, -0.08), 'skin', 0.05),
    ('hip_l', 'thigh_l', (0.31, 0.33, 0.44), (0.0, 0.0, -0.18), 'pants', 0.05),
    ('knee_l', 'shin_l', (0.29, 0.31, 0.38), (0.0, 0.0, -0.17), 'pants', 0.05),
    ('ankle_l', 'foot_l', (0.29, 0.42, 0.12), (0.0, -0.07, 0.0), 'shoes', 0.04),
    ('hip_r', 'thigh_r', (0.31, 0.33, 0.44), (0.0, 0.0, -0.18), 'pants', 0.05),
    ('knee_r', 'shin_r', (0.29, 0.31, 0.38), (0.0, 0.0, -0.17), 'pants', 0.05),
    ('ankle_r', 'foot_r', (0.29, 0.42, 0.12), (0.0, -0.07, 0.0), 'shoes', 0.04),
]
# Parts every character must show in every frame (QA inventory: "missing limbs").
REQUIRED_PARTS = [p[1] for p in PARTS]

# Face layout, local to the head joint (front surface at y = FACE_FRONT_Y).
FACE = {
    'eye_x': 0.135, 'eye_z': 0.31, 'eye_w': 0.15, 'eye_h': 0.19,
    'pupil_d': 0.085, 'pupil_range_x': 0.032, 'pupil_range_z': 0.04,
    'brow_z': 0.445, 'brow_w': 0.16, 'brow_h': 0.04, 'brow_raise': 0.05, 'brow_tilt_deg': 22.0,
    'mouth_z': 0.14, 'mouth_w': 0.22,
}

# Mouth shapes: (width factor, corner lift m, opening m, superellipse exponent, asymmetry m).
# Exponent 2 = elliptical opening, larger = squarer (grimace), smaller = pointed.
MOUTH_SHAPES = {
    'neutral': (1.0, 0.0, 0.0, 2.0, 0.0),
    'smile': (1.05, 0.035, 0.012, 2.4, 0.0),
    'grin': (1.2, 0.04, 0.05, 3.0, 0.0),
    'open_smile': (1.1, 0.03, 0.09, 2.2, 0.0),
    'frown': (0.95, -0.03, 0.0, 2.0, 0.0),
    'o': (0.55, 0.0, 0.08, 2.0, 0.0),
    'gasp': (0.7, -0.005, 0.13, 2.0, 0.0),
    'grimace': (1.25, -0.01, 0.05, 4.5, 0.0),
    'smirk': (0.95, 0.012, 0.0, 2.0, 0.03),
    'pout': (0.5, -0.008, 0.02, 2.0, 0.0),
    'flat': (0.9, 0.0, 0.0, 2.0, 0.0),
}
# Visemes (Preston Blair style groups) as mouth shapes.
VISEMES = {
    'rest': (1.0, 0.0, 0.0, 2.0, 0.0),
    'AI': (1.0, 0.005, 0.11, 2.2, 0.0),
    'E': (1.15, 0.01, 0.06, 3.0, 0.0),
    'O': (0.6, 0.0, 0.1, 2.0, 0.0),
    'U': (0.45, 0.0, 0.05, 2.0, 0.0),
    'MBP': (0.95, 0.0, 0.0, 2.0, 0.0),
    'FV': (1.0, 0.0, 0.025, 3.5, 0.0),
    'L': (0.95, 0.0, 0.06, 2.4, 0.0),
    'CDG': (1.05, 0.0, 0.04, 3.0, 0.0),
    'WQ': (0.5, 0.0, 0.035, 2.0, 0.0),
}

# Static arm poses for the LEFT arm in degrees: shoulder (x, y, z), elbow x.
# The right arm mirrors Y and Z. Poses that touch the body/face are solved by IK
# toward a body-relative target instead (see IK_TARGETS).
ARM_POSES = {
    'rest': ((3.0, -7.0, 0.0), -10.0),
    'hips': ((12.0, -42.0, 25.0), -85.0),
    'point': ((-84.0, -6.0, 0.0), -4.0),
    'wave': ((-14.0, -150.0, 0.0), -42.0),
    'raise': ((-8.0, -168.0, 0.0), -6.0),
    'reach': ((-78.0, -4.0, 0.0), -12.0),
    'fist_pump': ((-30.0, -38.0, 0.0), -128.0),
    'arms_out': ((-4.0, -84.0, 0.0), -6.0),
    'hold_prop': ((-40.0, -10.0, 0.0), -72.0),
    'shrug': ((6.0, -34.0, 0.0), -92.0),
    'thumbs_up': ((-52.0, -12.0, 0.0), -88.0),
}
# Body-relative hand targets (character space, scale 1) for contact poses.
IK_TARGETS = {
    'chest': (0.06, -0.30, 1.26),
    'cover_mouth': (0.02, -0.40, 1.62),
    'chin': (0.0, -0.38, 1.55),
    'facepalm': (0.03, -0.40, 1.88),
    'head_scratch': (0.30, 0.05, 1.95),
    'cross': (-0.16, -0.30, 1.18),
}

PALETTE_SLOTS = ['skin', 'top', 'top_trim', 'pants', 'shoes', 'hair', 'eyes', 'brows', 'mouth', 'badge', 'hat']
