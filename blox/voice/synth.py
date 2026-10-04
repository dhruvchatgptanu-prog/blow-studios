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


def _formants(x, peaks):
    """Resonances [(centre Hz, bandwidth Hz, gain)] applied by FFT filtering: the vowel colour of a voiced
    source (used for the synthesised crowd and laugh; no recorded voices)."""
    n = len(x)
    if n == 0:
        return x
    f = np.fft.rfftfreq(n, 1 / SR)
    H = sum(g / (1 + ((f - fc) / bw) ** 2) for fc, bw, g in peaks)
    return np.fft.irfft(np.fft.rfft(x) * H, n)


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
    if cue == 'stinger':
        # A bright brass-like chord hit for the punchline: detuned saws through a closing filter, a sub
        # thump and a short noise splash.
        t = _t(0.95)
        chord = (0, 4, 7, 12, 16)
        saw = sum(2 * ((midi(60 + c) * d * t) % 1) - 1 for c in chord for d in (0.997, 1.003)) / (2 * len(chord))
        bright = _onepole_vec(saw * np.exp(-t * 2.2), 3800) * 0.8 + _onepole_vec(saw * np.exp(-t * 9), 7000) * 0.5
        sub = np.sin(2 * np.pi * np.cumsum(40 + 70 * np.exp(-t * 25)) / SR) * np.exp(-t * 7) * 0.7
        splash = _highpass(_noise(len(t), seed), 3000) * np.exp(-t * 14) * 0.25
        return (bright + sub + splash) * np.minimum(1, t / 0.004)
    if cue == 'bonk':
        # Cartoon knock on a block head: two hollow partials that drop in pitch, and a click.
        t = _t(0.3)
        drop = 1 + 0.35 * np.exp(-t * 30)
        knock = (np.sin(2 * np.pi * np.cumsum(520 * drop) / SR) + 0.5 * np.sin(2 * np.pi * np.cumsum(1310 * drop) / SR))
        click = _highpass(_noise(len(t), seed), 2500) * np.exp(-t * 300) * 0.6
        return (knock * np.exp(-t * 22) + click) * 0.9
    if cue == 'stomp':
        # A heavy foot: sub boom, a low thud of the body and a short grit of the floor.
        t = _t(0.5)
        boom = np.sin(2 * np.pi * np.cumsum(38 + 60 * np.exp(-t * 20)) / SR) * np.exp(-t * 7)
        body = _onepole_vec(_onepole_vec(_onepole_vec(_noise(len(t), seed), 350), 350), 350) * np.exp(-t * 24) * 9.0
        grit = _highpass(_onepole_vec(_noise(len(t), seed + 1), 3500), 1500) * np.exp(-t * 110) * 0.18
        return (boom + body + grit) * 0.9
    if cue == 'door_chime':
        # A two-tone entrance chime (ding-dong): bell partials, the second tone a third lower.
        t = _t(1.7)
        out = np.zeros_like(t)
        for f, st in ((659.3, 0.0), (523.3, 0.42)):
            m = t >= st
            tt = t[m] - st
            out[m] += sum(a * np.sin(2 * np.pi * f * p * tt) * np.exp(-tt * (2.2 + p)) for p, a in
                          ((1.0, 1.0), (2.0, 0.35), (3.01, 0.15), (4.2, 0.08)))
        return out * 0.5
    if cue == 'crowd_ooh':
        # A small crowd going "oooh": voiced pulse sources at scattered pitches that swell and fall together,
        # shaped by the vowel resonances of "oo" sliding toward "oh".
        dur = 1.5
        t = _t(dur)
        rng = np.random.default_rng(seed)
        src = np.zeros_like(t)
        contour = 1 + 0.12 * np.sin(np.pi * np.clip(t / 1.1, 0, 1)) - 0.08 * np.clip((t - 0.9) / 0.6, 0, 1)
        for _ in range(14):
            f0 = rng.uniform(105, 290)
            vib = 1 + 0.012 * np.sin(2 * np.pi * rng.uniform(4.5, 6.5) * t + rng.uniform(0, 6.28))
            ph = np.cumsum(f0 * contour * vib) / SR
            delay = rng.uniform(0.0, 0.12)
            amp = np.clip((t - delay) / 0.3, 0, 1) * rng.uniform(0.6, 1.0)
            src += (2 * (ph % 1) - 1) * amp
        src += _onepole_vec(_noise(len(t), seed + 3), 1200) * 0.6  # breath
        voiced = _formants(src, ((330, 70, 1.0), (820, 110, 0.55), (2300, 200, 0.12)))
        env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 0.8
        return voiced * env * 0.5
    if cue == 'laugh_burst':
        # A short cartoon laugh, "ha-ha-ha-ha-ha", each syllable a breathy onset and a voiced "ah" that steps
        # down in pitch; a strong octave partial gives it the cartoon colour.
        syll, rate = 6, 7.0
        dur = syll / rate + 0.25
        t = _t(dur)
        rng = np.random.default_rng(seed)
        out = np.zeros_like(t)
        for i in range(syll):
            st = i / rate + rng.uniform(-0.006, 0.006)
            m = (t >= st) & (t < st + 0.13)
            tt = t[m] - st
            f0 = 300 * (1 - 0.045 * i) * (1 + 0.08 * np.exp(-tt * 25))
            ph = np.cumsum(f0) / SR
            voiced = (2 * (ph % 1) - 1) + 0.45 * (2 * ((2 * ph) % 1) - 1)
            env = np.clip(tt / 0.012, 0, 1) * np.exp(-tt * 18) * (1 - 0.08 * i)
            breath = _noise(len(tt), seed + i) * np.exp(-tt * 70) * 0.8
            out[m] += voiced * env + breath
        out = _formants(out, ((780, 110, 1.0), (1250, 140, 0.6), (2600, 260, 0.2)))
        # Gentle saturation evens the syllables out so the laugh is as present as the other cues.
        return np.tanh(out / (np.max(np.abs(out)) or 1.0) * 2.5) * 0.8
    raise ValueError('Unknown sound cue ' + str(cue))


# ------------------------------------------------------------------ music
MOODS = {
    # tempo, root midi, chord progression (semitone offsets of triads), scale for arpeggio, timbre
    # 'playful' is a separate groove (see _playful); its entry gives the tempo and harmony.
    'playful': (140, 60, [(0, 4, 7), (9, 12, 16), (5, 9, 12), (7, 11, 14)], 'square'),
    'tension': (100, 57, [(0, 3, 7), (0, 3, 7), (1, 4, 8), (0, 3, 6)], 'saw'),
    'triumph': (124, 62, [(0, 4, 7), (5, 9, 12), (7, 11, 14), (0, 4, 7)], 'square'),
    'sad': (72, 57, [(0, 3, 7), (8, 12, 15), (3, 7, 10), (10, 14, 17)], 'sine'),
    'mystery': (84, 55, [(0, 3, 7), (1, 5, 8), (0, 3, 7), (6, 10, 13)], 'triangle'),
    'chill': (90, 60, [(0, 4, 7, 11), (9, 12, 16, 19), (5, 9, 12, 16), (7, 11, 14, 17)], 'sine'),
}
# A new section (an act turn) lifts the key and changes the arrangement; section 0 is the opening.
SECTION_SHIFT = [0, 2, 5, 0]


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


def _add(out, t_rel, sig):
    """Mix ``sig`` into ``out`` starting at ``t_rel`` seconds (clipped at both ends)."""
    s0 = int(round(t_rel * SR))
    a = max(0, -s0)
    s0 = max(0, s0)
    if s0 >= len(out) or a >= len(sig):
        return
    e = min(len(out), s0 + len(sig) - a)
    out[s0:e] += sig[a:a + e - s0]


def _events(t0, dur, step):
    """Grid times k*step (absolute) that fall in [t0 - step, t0 + dur): note starts relative to t0."""
    k0 = int(math.floor(t0 / step)) - 1
    k1 = int(math.ceil((t0 + dur) / step))
    return [(k, k * step - t0) for k in range(k0, k1)]


def _kick():
    t = _t(0.18)
    f = 48 + 90 * np.exp(-t * 32)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 16)


def _clap(seed):
    t = _t(0.16)
    n = _noise(len(t), seed)
    body = _highpass(_onepole_vec(n, 5200), 1100)
    # Three quick re-triggers make it a clap rather than a snare.
    env = np.exp(-t * 28) + sum(np.where(t >= d, np.exp(-(t - d) * 90), 0) * 0.6 for d in (0.008, 0.017))
    return body * env * 0.9


def _hat(seed, open_=False):
    t = _t(0.09 if open_ else 0.035)
    return _highpass(_noise(len(t), seed), 7000) * np.exp(-t * (30 if open_ else 110))


def _pluck(f, dur, bright=1.0):
    t = _t(dur)
    sq = np.where((f * t) % 1.0 < 0.5, 1.0, -1.0)
    tone = _onepole_vec(sq, 1800 * bright) * 0.6 + np.sin(2 * np.pi * f * 2 * t) * 0.25
    return tone * np.exp(-t * 11) * np.minimum(1, t / 0.003)


def _playful(dur, section, t0):
    """A bouncy 140 BPM bed: kick and clap, eighth hats, octave-jumping bass, off-beat chord stabs and a
    plucky call-and-response lead on a major pentatonic. Every note sits on the absolute beat grid, so
    sections joined at an act turn stay in time."""
    tempo, root, prog, _ = MOODS['playful']
    root += SECTION_SHIFT[section % len(SECTION_SHIFT)]
    beat = 60.0 / tempo
    n = int(SR * dur)
    out = np.zeros(n)
    kick, clap = _kick(), _clap(11)
    hats = [_hat(20 + i) for i in range(4)] + [_hat(30, True)]
    penta = [0, 2, 4, 7, 9, 12, 14, 16]
    busy = section in (1, 3)
    for k, t in _events(t0, dur, beat):
        bar, b = divmod(k, 4)
        chord = prog[bar % len(prog)]
        if b in (0, 2) or (b == 3 and bar % 2 == 1 and section != 2):
            _add(out, t if b != 3 else t + beat / 2, kick * (0.9 if b == 0 else 0.75))
        if b in (1, 3) and section != 2:
            _add(out, t, clap * 0.55)
        if section == 2 and b == 2:
            _add(out, t, clap * 0.6)  # half-time feel for the climax/reveal
        # Bass: root on the beat, octave on the off-beat, short and bouncy.
        bass_note = root - 24 + chord[0]
        for half, oct_ in ((0.0, 0), (0.5, 12)):
            bt = _t(beat * 0.42)
            tone = _osc('triangle', midi(bass_note + oct_), bt) * 0.8 + _osc('square', midi(bass_note + oct_), bt) * 0.25
            _add(out, t + half * beat, _onepole_vec(tone, 900) * np.exp(-bt * 7) * 0.42)
        # Off-beat chord stabs.
        st = _t(beat * 0.22)
        stab = sum(_osc('square', midi(root + 12 + c), st) for c in chord) / len(chord)
        _add(out, t + beat / 2, _onepole_vec(stab, 2600) * np.exp(-st * 20) * 0.16)
        # Hats: eighths (sixteenths when busy), an open hat at the end of every other bar.
        steps = 4 if busy else 2
        for h in range(steps):
            idx = 4 if (b == 3 and h == steps - 1 and bar % 2) else (k + h) % 4
            _add(out, t + h * beat / steps, hats[idx] * (0.07 if h % 2 == 0 else 0.045))
        # Lead: a seeded two-bar phrase per chord, answering itself every other bar.
        rng = np.random.default_rng(1000 + (bar // 2) % 4 * 7 + section)
        for e in range(2):
            if rng.random() < (0.35 if bar % 2 else 0.55):
                continue
            note = root + 12 + penta[int(rng.integers(0, len(penta)))]
            if bar % 2:
                note -= 0 if rng.random() < 0.5 else 5
            _add(out, t + e * beat / 2, _pluck(midi(note), beat * 0.45, 1.3 if section == 3 else 1.0) * 0.17)
    out = _onepole_vec(out, 9000)
    out = np.tanh(out * 1.6) / np.tanh(1.6)
    peak = np.max(np.abs(out)) or 1.0
    return out / peak * 0.5


def music(mood, dur, section=0, t0=0.0):
    """An original looping cue for ``dur`` seconds starting at absolute time ``t0`` (bars are counted from
    0 s, so cue sections joined at any time stay on the beat). ``section`` > 0 is a variation for a later
    act: a key lift and a busier or sparser arrangement."""
    if mood == 'none' or dur <= 0:
        return np.zeros(int(SR * max(0, dur)))
    if mood == 'playful':
        return _playful(dur, section, t0)
    tempo, root, prog, timbre = MOODS.get(mood, MOODS['playful'])
    root += SECTION_SHIFT[section % len(SECTION_SHIFT)]
    beat = 60.0 / tempo
    n = int(SR * dur)
    out = np.zeros(n)
    bar = beat * 4
    for bi, b0 in _events(t0, dur, bar):
        chord = prog[bi % len(prog)]
        # Pad
        tt = _t(bar)
        pad = sum(np.sin(2 * np.pi * midi(root + 12 + c) * tt) for c in chord) / len(chord)
        _add(out, b0, pad * 0.18 * np.minimum(1, tt / 0.15) * np.minimum(1, (bar - tt) / 0.15))
        # Bass on beats 1 and 3
        for k in (0, 2):
            tb = _t(beat * 0.9)
            _add(out, b0 + k * beat, _osc('triangle', midi(root - 12 + chord[0]), tb) * np.exp(-tb * 3) * 0.35)
        # Arpeggio in eighths
        if mood not in ('sad',):
            for k in range(8):
                ta = _t(beat / 2 * 0.8)
                note = root + 12 + chord[k % len(chord)] + (12 if k % 4 == 3 else 0)
                _add(out, b0 + k * beat / 2, _osc(timbre, midi(note), ta) * np.exp(-ta * 9) * 0.12)
        # Soft hat on off-beats for energetic moods
        if mood in ('triumph', 'chill'):
            for k in range(4):
                ln = 1500
                _add(out, b0 + k * beat + beat / 2,
                     _highpass(_noise(ln, bi * 10 + k), 6000) * np.exp(-np.arange(ln) / SR * 70) * 0.05)
    out = _onepole_vec(out, 6000)
    peak = np.max(np.abs(out)) or 1.0
    return out / peak * 0.5


def music_track(changes, total_s, crossfade=0.6):
    """changes: [(start_s, mood)] or [(start_s, mood, section)] -> one continuous track with crossfades."""
    n = int(SR * total_s)
    track = np.zeros(n)
    segs = sorted((c[0], c[1], c[2] if len(c) > 2 else 0) for c in changes) or [(0.0, 'playful', 0)]
    for i, (st, mood, section) in enumerate(segs):
        en = segs[i + 1][0] if i + 1 < len(segs) else total_s
        a = max(0.0, st - crossfade / 2)
        b = min(total_s, en + crossfade / 2)
        seg = music(mood, b - a, section, a)
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


def apply_dropouts(track, drops, fade=0.025):
    """Silence the music in each (start_s, end_s) window with short fades (no clicks): the beat of
    silence before a punchline."""
    out = np.array(track, dtype=float, copy=True)
    k = max(1, int(fade * SR))
    for a, b in drops:
        s0, s1 = int(a * SR), int(b * SR)
        if s1 <= s0 or s0 >= len(out):
            continue
        s1 = min(len(out), s1)
        lo, hi = max(0, s0 - k), min(len(out), s1 + k)
        out[lo:s0] *= np.linspace(1, 0, s0 - lo)
        out[s0:s1] = 0.0
        out[s1:hi] *= np.linspace(0, 1, hi - s1)
    return out
