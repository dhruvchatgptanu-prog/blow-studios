"""Original sound effects and background music synthesised in code.

Everything here is generated from oscillators and noise at render time, so
there is no third-party audio to license. Output is deterministic for a given
cue, duration and seed.
"""
import math

import numpy as np

SR = 48000


def _t(dur):
    return np.arange(int(SR * dur)) / SR


def _env(n, attack=0.005, release=0.1, dur=None):
    t = np.arange(n) / SR
    dur = dur or n / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    r = np.clip((dur - t) / max(release, 1e-4), 0, 1)
    return a * r


def _noise(n, seed):
    return np.random.default_rng(seed).uniform(-1, 1, n)


def _onepole_vec(x, cutoff):
    # Vectorised approximation of a one-pole low-pass via FFT filtering.
    n = len(x)
    if n == 0:
        return x
    f = np.fft.rfftfreq(n, 1 / SR)
    H = 1 / np.sqrt(1 + (f / max(1.0, cutoff)) ** 2)
    return np.fft.irfft(np.fft.rfft(x) * H, n)


def _highpass(x, cutoff):
    return x - _onepole_vec(x, cutoff)


def sfx(cue, seed=1):
    if cue == 'whoosh':
        t = _t(0.55)
        n = _noise(len(t), seed)
        sweep = np.interp(t, [0, 0.3, 0.55], [400, 3200, 900])
        out = np.zeros_like(n)
        # Sweep a band-pass by mixing low-passes.
        for i in range(0, len(t), 2400):
            seg = n[i:i + 2400]
            c = sweep[i]
            out[i:i + len(seg)] = _onepole_vec(seg, c) - _onepole_vec(seg, c * 0.35)
        return out * np.sin(np.pi * t / 0.55) ** 2 * 1.8
    if cue == 'swoosh_up':
        t = _t(0.4)
        n = _highpass(_noise(len(t), seed), 800)
        return n * (t / 0.4) ** 2 * np.exp(-np.maximum(0, t - 0.3) * 30) * 0.8
    if cue == 'pop':
        t = _t(0.12)
        f = 900 * np.exp(-t * 25) + 200
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 35)
    if cue == 'boing':
        t = _t(0.7)
        f = 180 + 120 * np.exp(-t * 4) * np.sin(2 * np.pi * 9 * t)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 4) * 0.8
    if cue == 'thud':
        t = _t(0.35)
        body = np.sin(2 * np.pi * np.cumsum(90 * np.exp(-t * 6) + 45) / SR) * np.exp(-t * 14)
        click = _onepole_vec(_noise(len(t), seed), 2500) * np.exp(-t * 60) * 0.6
        return (body + click) * 1.1
    if cue == 'footstep':
        t = _t(0.12)
        return _onepole_vec(_noise(len(t), seed), 1500) * np.exp(-t * 45)
    if cue == 'ding':
        t = _t(1.2)
        partials = [(1.0, 1.0), (2.76, 0.4), (5.4, 0.2), (8.93, 0.1)]
        return sum(a * np.sin(2 * np.pi * 1320 * p * t) * np.exp(-t * (3 + p)) for p, a in partials) * 0.5
    if cue == 'coin':
        t1, t2 = _t(0.07), _t(0.25)
        sq = lambda f, t: np.sign(np.sin(2 * np.pi * f * t))
        return np.concatenate([sq(988, t1) * 0.3, sq(1319, t2) * 0.3 * np.exp(-t2 * 8)])
    if cue == 'click':
        t = _t(0.05)
        return _highpass(_noise(len(t), seed), 2000) * np.exp(-t * 120)
    if cue == 'beep':
        t = _t(0.2)
        return np.sin(2 * np.pi * 1000 * t) * _env(len(t), 0.005, 0.03) * 0.5
    if cue == 'rumble':
        t = _t(1.5)
        return _onepole_vec(_noise(len(t), seed), 120) * np.sin(np.pi * t / 1.5) * 3.0
    if cue == 'sizzle':
        t = _t(1.0)
        return _highpass(_noise(len(t), seed), 4000) * (0.6 + 0.4 * np.sin(2 * np.pi * 13 * t)) * np.exp(-t * 2) * 0.5
    if cue == 'sparkle':
        t = _t(0.9)
        out = np.zeros_like(t)
        rng = np.random.default_rng(seed)
        for _ in range(9):
            st = rng.uniform(0, 0.6)
            f = rng.uniform(2500, 5200)
            m = t >= st
            tt = t[m] - st
            out[m] += np.sin(2 * np.pi * f * tt) * np.exp(-tt * 18) * 0.25
        return out
    if cue == 'gasp_sting':
        t = _t(0.9)
        chord = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (220, 261.6, 311.1, 415.3)) / 4
        return (_onepole_vec(chord, 2200) * 0.7 + _onepole_vec(_noise(len(t), seed), 900) * 0.4) * np.exp(-t * 4)
    if cue == 'fail_horn':
        out = []
        for i, f in enumerate((392, 370, 349, 311)):
            dur = 0.32 if i < 3 else 0.9
            t = _t(dur)
            vib = 1 + (0.012 * np.sin(2 * np.pi * 5.5 * t) if i == 3 else 0)
            saw = 2 * ((f * vib * t) % 1) - 1
            out.append(_onepole_vec(saw, 1400) * _env(len(t), 0.02, 0.08 if i < 3 else 0.4))
        return np.concatenate(out) * 0.6
    if cue == 'drumroll':
        t = _t(1.4)
        out = np.zeros_like(t)
        n = _onepole_vec(_noise(len(t), seed), 3000)
        for k in range(int(1.4 * 22)):
            st = int(k / 22 * SR)
            seg = slice(st, st + 1200)
            ln = len(out[seg])
            out[seg] += n[seg] * np.exp(-np.arange(ln) / SR * 80)
        return out * np.linspace(0.4, 1.0, len(t))
    if cue == 'tada':
        t = _t(1.3)
        out = np.zeros_like(t)
        for f, st in ((523.3, 0.0), (659.3, 0.12), (784.0, 0.24), (1046.5, 0.36)):
            m = t >= st
            tt = t[m] - st
            saw = 2 * ((f * tt) % 1) - 1
            out[m] += _onepole_vec(saw, 2500) * np.exp(-tt * 2.2) * 0.25
        return out
    raise ValueError('Unknown sound cue ' + str(cue))


# ------------------------------------------------------------------ music
MOODS = {
    # tempo, root midi, chord progression (semitone offsets of triads), scale for arpeggio, timbre
    'playful': (118, 60, [(0, 4, 7), (7, 11, 14), (9, 12, 16), (5, 9, 12)], 'square'),
    'tension': (100, 57, [(0, 3, 7), (0, 3, 7), (1, 4, 8), (0, 3, 6)], 'saw'),
    'triumph': (124, 62, [(0, 4, 7), (5, 9, 12), (7, 11, 14), (0, 4, 7)], 'square'),
    'sad': (72, 57, [(0, 3, 7), (8, 12, 15), (3, 7, 10), (10, 14, 17)], 'sine'),
    'mystery': (84, 55, [(0, 3, 7), (1, 5, 8), (0, 3, 7), (6, 10, 13)], 'triangle'),
    'chill': (90, 60, [(0, 4, 7, 11), (9, 12, 16, 19), (5, 9, 12, 16), (7, 11, 14, 17)], 'sine'),
}


def _osc(kind, f, t):
    ph = (f * t) % 1.0
    if kind == 'square':
        return np.where(ph < 0.5, 1.0, -1.0) * 0.6
    if kind == 'saw':
        return 2 * ph - 1
    if kind == 'triangle':
        return 4 * np.abs(ph - 0.5) - 1
    return np.sin(2 * np.pi * ph)


def midi(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def music(mood, dur):
    """An original looping cue for ``dur`` seconds."""
    if mood == 'none' or dur <= 0:
        return np.zeros(int(SR * max(0, dur)))
    tempo, root, prog, timbre = MOODS.get(mood, MOODS['playful'])
    beat = 60.0 / tempo
    n = int(SR * dur)
    out = np.zeros(n)
    bar = beat * 4
    t_all = np.arange(n) / SR
    for bi in range(int(math.ceil(dur / bar))):
        chord = prog[bi % len(prog)]
        b0 = bi * bar
        # Pad
        s0, s1 = int(b0 * SR), min(n, int((b0 + bar) * SR))
        if s0 >= n:
            break
        tt = t_all[s0:s1] - b0
        pad = sum(np.sin(2 * np.pi * midi(root + 12 + c) * tt) for c in chord) / len(chord)
        out[s0:s1] += pad * 0.18 * np.minimum(1, tt / 0.15) * np.minimum(1, (bar - tt) / 0.15)
        # Bass on beats 1 and 3
        for k in (0, 2):
            bs = b0 + k * beat
            s0b, s1b = int(bs * SR), min(n, int((bs + beat * 0.9) * SR))
            if s0b >= n:
                continue
            tb = t_all[s0b:s1b] - bs
            out[s0b:s1b] += _osc('triangle', midi(root - 12 + chord[0]), tb) * np.exp(-tb * 3) * 0.35
        # Arpeggio in eighths
        if mood not in ('sad',):
            for k in range(8):
                st = b0 + k * beat / 2
                s0a, s1a = int(st * SR), min(n, int((st + beat / 2 * 0.8) * SR))
                if s0a >= n:
                    continue
                ta = t_all[s0a:s1a] - st
                note = root + 12 + chord[k % len(chord)] + (12 if k % 4 == 3 else 0)
                out[s0a:s1a] += _osc(timbre, midi(note), ta) * np.exp(-ta * 9) * 0.12
        # Soft hat on off-beats for energetic moods
        if mood in ('playful', 'triumph', 'chill'):
            for k in range(4):
                st = b0 + k * beat + beat / 2
                s0h = int(st * SR)
                if s0h >= n:
                    continue
                ln = min(n - s0h, 1500)
                out[s0h:s0h + ln] += _highpass(_noise(ln, bi * 10 + k), 6000) * np.exp(-np.arange(ln) / SR * 70) * 0.05
    out = _onepole_vec(out, 6000)
    peak = np.max(np.abs(out)) or 1.0
    return out / peak * 0.5


def music_track(changes, total_s, crossfade=0.6):
    """changes: [(start_s, mood)] -> one continuous track with crossfades."""
    n = int(SR * total_s)
    track = np.zeros(n)
    segs = sorted(changes) or [(0.0, 'playful')]
    for i, (st, mood) in enumerate(segs):
        en = segs[i + 1][0] if i + 1 < len(segs) else total_s
        a = max(0.0, st - crossfade / 2)
        b = min(total_s, en + crossfade / 2)
        seg = music(mood, b - a)
        s0 = int(a * SR)
        fade = np.ones(len(seg))
        k = int(crossfade * SR)
        if i > 0:
            fade[:k] = np.linspace(0, 1, min(k, len(seg)))[:len(fade[:k])]
        if i + 1 < len(segs):
            fade[-k:] = np.linspace(1, 0, min(k, len(seg)))[-len(fade[-k:]):]
        end = min(n, s0 + len(seg))
        track[s0:end] += (seg * fade)[:end - s0]
    return track
