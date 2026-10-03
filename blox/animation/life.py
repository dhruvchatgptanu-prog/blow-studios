"""Procedural performance layers for the block characters: what they do between authored actions.

Hand-tuned rules for stylised animation (not motion capture), evaluated on the host so they stay
deterministic and testable:

* idle life: breathing, slow weight shifts, relaxed asymmetric arms, head drift, eye saccades;
* speech: beat gestures, head nods/tilts and brow raises timed to the stressed syllables of each line
  (the voice envelope when there is audio, word timing and text stress otherwise), a slight lean
  toward the listener;
* listening: small back-channel nods, a head tilt, steadier eye contact.

Every variation comes from seeds derived from ids (character, line, action), so the same manifest
always gives the same performance. The solver decides *where* these layers may act (free arms, planted
feet, no head action) and authored actions and poses always win over them.
"""
import math
import re
import zlib

import numpy as np

FUNCTION_WORDS = {'a', 'an', 'the', 'to', 'of', 'and', 'or', 'but', 'in', 'on', 'at', 'for', 'is', 'are', 'was',
                  'be', 'it', 'its', "it's", 'i', "i'm", 'me', 'my', 'we', 'you', 'your', 'he', 'she', 'they',
                  'them', 'this', 'that', 'with', 'as', 'so', 'do', 'just', 'uh', 'um', 'oh', 'by', 'if', 'then'}
ENERGETIC = {'excited', 'angry', 'startled', 'scared', 'happy', 'proud', 'laughing', 'determined'}
SUBDUED = {'sad', 'bored', 'embarrassed', 'worried', 'relieved'}
VOLUME_GAIN = {'whisper': 0.5, 'soft': 0.7, 'normal': 1.0, 'loud': 1.2, 'shout': 1.4}
STYLES = ('beat', 'open', 'point', 'flick', 'both', 'still')

# Gesture arm poses for the LEFT arm, same convention as rig.ARM_POSES: shoulder (x, y, z), elbow x; the
# right arm mirrors Y and Z. 'hold' is the gesture-space pose (upper arm nearly hanging, forearm forward,
# hand in front of the belly and inside the shoulder line), 'stroke' the displacement at the peak of a
# beat, 'wrist' the wrist rotation (x, y, z) at the peak (x < 0 tips the hand up, z turns the palm).
GESTURES = {
    'beat': {'hold': (-12.0, -8.0, -14.0, -84.0), 'stroke': (4.0, 0.0, 0.0, 12.0), 'wrist': (10.0, 0.0, 0.0)},
    'open': {'hold': (-10.0, -14.0, 8.0, -76.0), 'stroke': (-4.0, -12.0, 22.0, 10.0), 'wrist': (-6.0, 0.0, 30.0)},
    'point': {'hold': (-30.0, -4.0, -14.0, -54.0), 'stroke': (-10.0, 0.0, 0.0, 18.0), 'wrist': (-6.0, 0.0, 0.0)},
    'flick': {'hold': (-6.0, -8.0, -8.0, -64.0), 'stroke': (0.0, 0.0, 0.0, 6.0), 'wrist': (-34.0, 0.0, 8.0)},
}
GESTURES['both'] = GESTURES['beat']


def seed(*parts):
    return zlib.crc32('|'.join(str(p) for p in parts).encode())


def rand01(*parts):
    return (seed(*parts) % 100003) / 100003.0


def smooth(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def noise(t, key):
    """Smooth value noise in [-1, 1] at time t (in lattice units); quintic interpolation, seeded by key."""
    i = math.floor(t)
    u = t - i
    a = rand01(key, i) * 2 - 1
    b = rand01(key, i + 1) * 2 - 1
    w = u * u * u * (u * (u * 6 - 15) + 10)
    return a + (b - a) * w


def free_weight(busy, ramp):
    """1 where a layer may act, easing to 0 over `ramp` frames toward any busy frame (both sides)."""
    n = len(busy)
    d = np.full(n, 1e9)
    last = -1e9
    for f in range(n):
        if busy[f]:
            last = f
        d[f] = f - last
    last = 1e9
    for f in range(n - 1, -1, -1):
        if busy[f]:
            last = f
        d[f] = min(d[f], last - f)
    u = np.clip(d / float(max(1, ramp)), 0.0, 1.0)
    return u * u * (3 - 2 * u)


def kernel_stroke(t):
    """Beat gesture displacement around a stressed syllable at t = 0 (frames).

    Preparation (the hand rises a little), a fast stroke landing just after the syllable, then a slower
    recoil back to the hold: the classic prepare / stroke / retract shape of conversational beats.
    """
    if t < -8 or t >= 11:
        return 0.0
    if t < -3:
        return -0.35 * smooth((t + 8) / 5.0)
    if t < 1:
        return -0.35 + 1.35 * smooth((t + 3) / 4.0)
    return 1.0 - smooth((t - 1) / 10.0)


def kernel_nod(t, down=3.0, up=9.0):
    """Quick dip that peaks `down` frames after t = 0 and recovers by `down + up`."""
    if t < 0 or t >= down + up:
        return 0.0
    if t < down:
        return smooth(t / down)
    return 1.0 - smooth((t - down) / up)


def word_stress(word, after=''):
    """Rough stress weight of a written word: content words, capitals and exclamations stand out."""
    w = word.strip("'").lower()
    if not w:
        return 0.0
    s = 0.25 if w in FUNCTION_WORDS else 0.55 + 0.05 * min(6, len(w))
    if len(word) > 1 and word.isupper():
        s += 0.5
    if '!' in after:
        s += 0.3
    elif '?' in after:
        s += 0.2
    elif '...' in after or '…' in after:
        s -= 0.1
    return s


def text_tokens(text):
    """[(word, punctuation that follows it)] in reading order."""
    return [(m.group(1), m.group(2)) for m in re.finditer(r"([A-Za-z0-9']+)([^A-Za-z0-9']*)", text or '')]


def emphasis_peaks(text, words, env, a, b, fps, key):
    """Stressed moments of one spoken line: [(frame, strength 0..1)], sorted by frame.

    words: [(start_frame, end_frame, word)] in absolute frames; env: per-frame speech amplitude for the
    whole timeline (or None). The text gives which words carry stress, the audio envelope (when there is
    one) gives exactly when the voice peaks.
    """
    n = b - a
    if n < 4:
        return []
    toks = text_tokens(text)
    sig = np.zeros(n)
    content = [i for i, (w, _) in enumerate(toks) if w.lower() not in FUNCTION_WORDS]
    for i, (ws, we, w) in enumerate(words):
        after = toks[i][1] if i < len(toks) else ''
        s = word_stress(w, after)
        if content and i == content[-1]:
            s += 0.15   # phrase-final content words carry the nuclear stress
        elif content and i == content[0]:
            s += 0.1
        c = ws + 0.3 * max(1, we - ws) - a
        for f in range(max(0, int(c) - 4), min(n, int(c) + 5)):
            sig[f] = max(sig[f], s * max(0.0, 1 - abs(f - c) / 4.0))
    if sig.max() > 0:
        sig /= sig.max()
    if env is not None and len(env) >= b and float(np.max(env[a:b])) > 0.05:
        e = np.asarray(env[a:b], dtype=float)
        e = e / max(1e-6, float(np.percentile(e, 95)))
        sig = 0.6 * np.clip(e, 0, 1.2) + 0.4 * sig
    peaks = []
    for f in range(n):
        lo, hi = max(0, f - 3), min(n, f + 4)
        if sig[f] >= 0.35 and sig[f] >= sig[lo:hi].max() - 1e-9:
            peaks.append((sig[f] + 1e-4 * rand01(key, f), f))
    keep = []
    spacing = int(0.38 * fps)
    budget = max(1, int(round(n / fps * 2.2)))
    for s, f in sorted(peaks, reverse=True):
        if len(keep) < budget and all(abs(f - g) >= spacing for _, g in keep):
            keep.append((s, f))
    if not keep:
        return []
    top = max(s for s, _ in keep)
    return sorted((a + f, round(0.4 + 0.6 * min(1.0, s / top), 3)) for s, f in keep)


def synthetic_beats(a, b, fps, key):
    """Beats for an explicit 'talk' action that has no voiced line under it (seeded rhythm)."""
    out = []
    f = a + int(fps * 0.25)
    i = 0
    while f < b - 4:
        out.append((f, round(0.55 + 0.4 * rand01(key, 'amp', i), 3)))
        f += int(fps * (0.45 + 0.3 * rand01(key, 'gap', i)))
        i += 1
    return out


def choose_style(key, emotion, text, seconds):
    """Seeded gesture style for one line, weighted by emotion, question form and length."""
    if emotion in ENERGETIC:
        w = {'beat': 3, 'open': 3, 'both': 2, 'point': 2, 'flick': 1, 'still': 0.3}
    elif emotion in SUBDUED:
        w = {'beat': 2, 'flick': 2, 'open': 1, 'still': 2, 'both': 0.3, 'point': 0.3}
    else:
        w = {'beat': 3, 'open': 2, 'flick': 2, 'point': 1, 'both': 1, 'still': 1}
    if text.rstrip().endswith('?'):
        w['open'] += 2
    if seconds < 0.8:
        w['still'] += 2
    total = sum(w.values())
    r = rand01(key, 'style') * total
    for s in STYLES:
        r -= w.get(s, 0)
        if r < 0:
            return s
    return 'beat'


def breath_track(n, fps, key, speech, rate=1.0):
    """Per-frame breath in [-1, 1] (+1 = full inhale).

    A slow sine at rest; before each spoken line a quick inhale, then a steady exhale while speaking.
    speech: [(start_frame, end_frame)] of this character's lines; rate: a number or a per-frame array
    (excited or scared characters breathe faster).
    """
    base = fps * (3.4 + 1.0 * rand01(key, 'breath'))
    rates = np.broadcast_to(np.asarray(rate, dtype=float), (n,))
    phase = 2 * math.pi * rand01(key, 'phase')
    out = np.zeros(n)
    spans = sorted(speech)
    for f in range(n):
        phase += 2 * math.pi * max(0.3, float(rates[f])) / base
        b = math.sin(phase)
        for s, e in spans:
            if s - 12 <= f < s:
                b = b + (1.0 - b) * smooth((f - (s - 12)) / 12.0)
            elif s <= f < e:
                b = 1.0 - 1.8 * (f - s) / max(1, e - s)
            elif e <= f < e + 1:
                phase = math.asin(-0.8)  # resume the cycle from the end of the exhale
                b = math.sin(phase)
        out[f] = b
    k = np.ones(5) / 5.0
    return np.convolve(np.pad(out, 2, mode='edge'), k, mode='valid')


def sway_track(n, fps, key, amp=1.0):
    """Slow weight shifts in [-1, 1] (+ = weight on the character's left foot): hold, then settle across."""
    out = np.zeros(n)
    f = 0
    cur = 0.0
    i = 0
    while f < n:
        hold = int(fps * (2.4 + 3.2 * rand01(key, 'hold', i)))
        move = int(fps * (0.9 + 0.7 * rand01(key, 'move', i)))
        choices = [c for c in (-1.0, -0.55, 0.0, 0.55, 1.0) if abs(c - cur) > 0.4]
        nxt = choices[seed(key, 'to', i) % len(choices)]
        for g in range(f, min(n, f + hold)):
            out[g] = cur
        for g in range(f + hold, min(n, f + hold + move)):
            u = (g - f - hold) / max(1, move)
            # Ease across with a little overshoot that settles, like a body finding its balance.
            e = smooth(u) + 0.08 * math.sin(math.pi * u) * u
            out[g] = cur + (nxt - cur) * e
        f += hold + move
        cur = nxt
        i += 1
    return out * amp


def saccade_track(n, fps, key, mode):
    """Per-frame pupil offsets (x, z) in pupil units: fixations joined by fast 2-frame saccades.

    mode[f] in {'idle', 'speak', 'listen'} sets the rhythm and size: listeners hold eye contact with
    small moves, speakers look about more and sometimes glance away.
    """
    out = np.zeros((n, 2))
    f = 0
    cur = np.zeros(2)
    i = 0
    while f < n:
        md = mode[min(n - 1, f)]
        if md == 'listen':
            gap, size = (0.8 + 1.4 * rand01(key, 'gap', i)), 0.10
        elif md == 'speak':
            gap, size = (0.5 + 1.1 * rand01(key, 'gap', i)), 0.16
        else:
            gap, size = (0.4 + 1.6 * rand01(key, 'gap', i)), 0.2
        ang = 2 * math.pi * rand01(key, 'dir', i)
        r = size * (0.4 + 0.6 * rand01(key, 'r', i))
        nxt = np.array([r * math.cos(ang), 0.6 * r * math.sin(ang)])
        if i % 3 == 2:
            nxt = np.zeros(2)  # return to the target itself now and then
        hold = max(4, int(fps * gap))
        for g in range(f, min(n, f + 2)):
            u = (g - f + 1) / 2.0
            out[g] = cur + (nxt - cur) * u
        for g in range(f + 2, min(n, f + hold)):
            out[g] = nxt
        cur = nxt
        f += hold
        i += 1
    return out


def gaze_aversions(lines, fps, key):
    """Speakers often glance away briefly as they start a thought: [(f0, f1, (x, z))] pupil offsets."""
    out = []
    for ln in lines:
        if rand01(key, ln['id'], 'avert') < 0.4:
            f0 = ln['start_frame'] + int(fps * 0.1)
            f1 = f0 + int(fps * (0.45 + 0.4 * rand01(key, ln['id'], 'len')))
            side = -1 if rand01(key, ln['id'], 'side') < 0.5 else 1
            out.append((f0, f1, (0.38 * side, 0.22)))
    return out


def stroke_curve(peaks, n, key, gain=1.0, lead=2):
    """Sum of beat-stroke kernels for [(frame, strength)] over n frames, with per-beat seeded variation.

    The kernels are placed `lead` frames early because the arm springs lag the target by about that much;
    the visible stroke then lands on the stressed syllable.
    """
    d = np.zeros(n)
    for j, (p, s) in enumerate(peaks):
        p -= lead
        amp = s * gain * (0.75 + 0.35 * rand01(key, 'beat', j))
        for f in range(max(0, p - 8), min(n, p + 11)):
            d[f] += amp * kernel_stroke(f - p)
    return np.clip(d, -0.6, 1.25)
