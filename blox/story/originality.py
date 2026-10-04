"""Originality screening for concepts and scripts.

Signals (each a screening signal, not proof of originality):
* text overlap: share of the candidate's word 4-grams found in any single
  reference text (title, description, transcript);
* semantic similarity: embedding cosine when an embedding model is connected,
  otherwise a local TF-IDF cosine (different thresholds; method is reported);
* self-similarity: against previous Blox scripts;
* repeated premises and endings: against recent Blox concepts;
* single-reference dependence: one reference far closer than all others, or
  an inspiration record that names only one source when several were offered.
Outcome: pass, rewrite (automatic, bounded) or hold (owner review).
"""
import math
import re
from collections import Counter

DISCLAIMER = 'Similarity scores are screening signals, not proof of originality.'

THRESHOLDS = {
    'embedding': {'ref': 0.86, 'self': 0.88, 'premise': 0.9, 'ending': 0.9},
    'tfidf': {'ref': 0.45, 'self': 0.55, 'premise': 0.6, 'ending': 0.6},
}
OVERLAP_FAIL = 0.08
DEPENDENCE_GAP = {'embedding': 0.12, 'tfidf': 0.2}


def words(text):
    return re.findall(r"[a-z0-9']+", (text or '').lower())


def shingles(text, n=4):
    w = words(text)
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def containment(cand, ref, n=4):
    a = shingles(cand, n)
    if not a:
        return 0.0
    return len(a & shingles(ref, n)) / len(a)


class TfIdf:
    def __init__(self, docs):
        self.df = Counter()
        for d in docs:
            self.df.update(set(words(d)))
        self.n = max(1, len(docs))

    def vec(self, text):
        tf = Counter(words(text))
        return {t: (c / max(1, sum(tf.values()))) * math.log((1 + self.n) / (1 + self.df.get(t, 0)) + 1) for t, c in tf.items()}

    @staticmethod
    def cos(a, b):
        num = sum(v * b.get(k, 0) for k, v in a.items())
        den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
        return num / den if den else 0.0


def _cos(a, b):
    num = sum(x * y for x, y in zip(a, b))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0


def similarity_matrix(cands, others, embed=None):
    """Returns (method, sims[i][j])."""
    if embed:
        try:
            vecs = embed(cands + others)
            if vecs:
                cv, ov = vecs[:len(cands)], vecs[len(cands):]
                return 'embedding', [[_cos(c, o) for o in ov] for c in cv]
        except Exception:
            pass
    tf = TfIdf(cands + others)
    cv = [tf.vec(c) for c in cands]
    ov = [tf.vec(o) for o in others]
    return 'tfidf', [[TfIdf.cos(c, o) for o in ov] for c in cv]


def check(candidate, references, own, embed=None, inspiration_sources=None):
    """candidate: {'premise','hook','ending','script'}; references: [{'id','text'}]; own: [{'id','premise','ending','script'}]."""
    report = {'checks': [], 'disclaimer': DISCLAIMER}
    full = ' '.join([candidate.get('premise', ''), candidate.get('hook', ''), candidate.get('ending', ''),
                     candidate.get('script', '')])
    decision = 'pass'

    def add(name, value, limit, status, detail):
        report['checks'].append({'check': name, 'value': None if value is None else round(value, 3),
                                 'threshold': limit, 'status': status, 'detail': detail})

    # Text overlap
    worst = (0.0, None)
    for r in references:
        c = containment(full, r['text'])
        if c > worst[0]:
            worst = (c, r['id'])
    st = 'fail' if worst[0] > OVERLAP_FAIL else 'pass'
    add('text_overlap_with_reference', worst[0], OVERLAP_FAIL, st,
        f'Share of our 4-word phrases found in reference {worst[1]}' if worst[1] else 'No reference text available')
    if st == 'fail':
        decision = 'rewrite'
    # Semantic similarity to references
    method = 'none'
    if references:
        method, sims = similarity_matrix([full], [r['text'] for r in references], embed)
        row = sims[0]
        best = max(row)
        idx = row.index(best)
        lim = THRESHOLDS[method]['ref']
        st = 'fail' if best > lim else 'pass'
        add('semantic_similarity_to_reference', best, lim, st, f'Closest reference {references[idx]["id"]} ({method})')
        if st == 'fail':
            decision = 'rewrite'
        if len(row) >= 2:
            srt = sorted(row, reverse=True)
            gap = srt[0] - srt[1]
            glim = DEPENDENCE_GAP[method]
            st = 'warn' if gap > glim and srt[0] > lim * 0.7 else 'pass'
            add('single_reference_dependence', gap, glim, st,
                f'Gap between the closest and second-closest reference ({method})')
            if st == 'warn' and decision == 'pass':
                decision = 'hold'
    if inspiration_sources is not None and len(references) >= 2:
        n_src = len(set(inspiration_sources))
        st = 'pass' if n_src >= 2 else 'warn'
        add('inspiration_breadth', n_src, 2, st, 'Distinct references named as inspiration')
        if st == 'warn' and decision == 'pass':
            decision = 'hold'
    # Self-similarity and repeated premise/ending
    if own:
        m2, sims = similarity_matrix([candidate.get('script', '') or full], [o.get('script', '') or o.get('premise', '')
                                                                             for o in own], embed)
        best = max(sims[0])
        lim = THRESHOLDS[m2]['self']
        st = 'fail' if best > lim else 'pass'
        add('similarity_to_previous_blox_videos', best, lim, st, f'Closest previous video ({m2})')
        if st == 'fail':
            decision = 'rewrite'
        for field in ('premise', 'ending'):
            vals = [o.get(field, '') for o in own if o.get(field)]
            if not vals or not candidate.get(field):
                continue
            m3, s3 = similarity_matrix([candidate[field]], vals, embed)
            best = max(s3[0])
            lim = THRESHOLDS[m3][field]
            st = 'fail' if best > lim else 'pass'
            add(f'repeated_{field}', best, lim, st, f'Closest recent {field} ({m3})')
            if st == 'fail':
                decision = 'rewrite'
    report['method'] = method
    report['decision'] = decision
    return report
