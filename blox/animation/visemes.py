"""Text -> phoneme groups -> viseme keys, timed by word alignment.

This is a rule-based English grapheme-to-viseme approximation, not a full
pronunciation dictionary. Timing comes from the word timings of the actual
audio: provider timestamps or ASR alignment when available (labelled
"measured"), otherwise an energy-based estimate (labelled "estimated").
"""
import re

# Ordered digraph/trigraph rules first.
RULES = [
    ('tion', ['CDG', 'E', 'CDG']), ('sion', ['CDG', 'E', 'CDG']), ('ough', ['O']), ('igh', ['AI']),
    ('th', ['L']), ('sh', ['CDG']), ('ch', ['CDG']), ('ph', ['FV']), ('wh', ['WQ']), ('qu', ['WQ']),
    ('ck', ['CDG']), ('ng', ['CDG']), ('oo', ['U']), ('ou', ['O']), ('ow', ['O']), ('oa', ['O']),
    ('ee', ['E']), ('ea', ['E']), ('ai', ['AI']), ('ay', ['AI']), ('oi', ['O', 'E']), ('oy', ['O', 'E']),
    ('au', ['O']), ('aw', ['O']), ('ie', ['AI']), ('ei', ['AI']),
]
SINGLE = {
    'a': 'AI', 'e': 'E', 'i': 'AI', 'o': 'O', 'u': 'U', 'y': 'E',
    'b': 'MBP', 'm': 'MBP', 'p': 'MBP', 'f': 'FV', 'v': 'FV', 'l': 'L', 'w': 'WQ', 'r': 'WQ',
    'c': 'CDG', 'd': 'CDG', 'g': 'CDG', 'k': 'CDG', 'n': 'CDG', 's': 'CDG', 't': 'CDG', 'x': 'CDG',
    'z': 'CDG', 'j': 'CDG', 'q': 'WQ', 'h': None,
}
VOWELS = {'AI', 'E', 'O', 'U'}


def word_visemes(word):
    w = re.sub(r'[^a-z]', '', word.lower())
    out = []
    i = 0
    while i < len(w):
        for g, vs in RULES:
            if w.startswith(g, i):
                out.extend(vs)
                i += len(g)
                break
        else:
            v = SINGLE.get(w[i])
            # Silent final e.
            if not (w[i] == 'e' and i == len(w) - 1 and len(w) > 2):
                if v:
                    out.append(v)
            i += 1
    # Collapse repeats so double letters do not stutter.
    dedup = []
    for v in out:
        if not dedup or dedup[-1] != v:
            dedup.append(v)
    return dedup or ['CDG']


def weight_of(v):
    return 1.6 if v in VOWELS else 1.0


def viseme_keys(words, offset_s=0.0):
    """words: [{'word', 'start', 'end'}] seconds -> [{'t', 'viseme'}] with absolute times."""
    keys = []
    for w in words:
        vs = word_visemes(w['word'])
        dur = max(0.04, w['end'] - w['start'])
        total = sum(weight_of(v) for v in vs)
        t = w['start']
        for v in vs:
            span = dur * weight_of(v) / total
            keys.append({'t': offset_s + t + span * 0.4, 'viseme': v})
            t += span
        keys.append({'t': offset_s + w['end'] + 0.02, 'viseme': 'rest', 'soft': True})
    keys.sort(key=lambda k: k['t'])
    return keys


def frame_weights(keys, fps, n_frames, start_frame=0):
    """Per-frame dict viseme->weight with simple coarticulation smoothing."""
    frames = [dict() for _ in range(n_frames)]
    for k in keys:
        center = k['t'] * fps - start_frame
        radius = 2.2 if k['viseme'] not in ('MBP',) else 1.4
        lo, hi = int(center - radius - 1), int(center + radius + 2)
        for f in range(max(0, lo), min(n_frames, hi)):
            w = max(0.0, 1.0 - abs(f - center) / radius)
            if k.get('soft'):
                w *= 0.6
            if w > frames[f].get(k['viseme'], 0):
                frames[f][k['viseme']] = w
    out = []
    for fr in frames:
        total = sum(fr.values())
        if total > 1.0:
            fr = {k: v / total for k, v in fr.items()}
        out.append({k: round(v, 3) for k, v in fr.items() if v > 0.02})
    return out


def estimate_words(text, start_s, duration_s):
    """Fallback word timing proportional to syllable-ish length (estimated)."""
    ws = re.findall(r"[A-Za-z0-9']+", text)
    if not ws:
        return []
    weights = [max(1, len(re.findall(r'[aeiouy]+', w.lower()))) + 0.3 for w in ws]
    total = sum(weights)
    t = start_s
    out = []
    for w, wt in zip(ws, weights):
        d = duration_s * wt / total
        out.append({'word': w, 'start': t, 'end': t + d * 0.92})
        t += d
    return out
