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

Titles are also checked on their own (``title_check``) against the reference titles the owner imported
from his research (reference.jsonl rows in Research notes): a title too close to another creator's
video is blocked; one close to the owner's own video only gets a note.
"""
import difflib
import math
import re
import unicodedata
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


# ---------------------------------------------------------------- title closeness against imported reference titles
TITLE_BLOCK = 0.6
TITLE_MAX_CHARS = 300
# Small words every title in the format shares ("I ... the ...", "my ..."); left out so the shared title grammar
# alone does not make two different stories look close.
TITLE_STOPWORDS = frozenset('''i me my mine myself we us our you your he him his she her they them their it its
a an the and or but so to of in on at for with by from into onto up out off as than then this that these those is was
were be been am are'''.split())
_TITLE_WORD = re.compile(r'[^\W_]+')


def title_words(title):
    """Normalised title words: lower case, emoji and punctuation removed, small shared words left out and a plural
    "s" folded ("World's Strongest Dads" and "worlds strongest dad" give the same words)."""
    s = unicodedata.normalize('NFKC', str(title or ''))[:TITLE_MAX_CHARS].lower()
    s = re.sub(r"['\u2018\u2019`]", '', s)
    out = []
    for w in _TITLE_WORD.findall(s):
        if w in TITLE_STOPWORDS:
            continue
        if len(w) > 3 and w.endswith('s') and not w.endswith('ss'):
            w = w[:-1]
        out.append(w)
    return out


def title_similarity(a, b):
    """max(difflib ratio over the word sequences, word Jaccard) of two normalised word lists, 0..1."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    jac = len(sa & sb) / len(sa | sb)
    ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    return max(ratio, jac)


def prepare_reference_titles(refs):
    """Pre-normalised reference rows, so many titles can be checked against them (pass the result as ``refs``)."""
    out = []
    for r in refs or []:
        if isinstance(r, tuple):
            out.append(r)
            continue
        if not isinstance(r, dict) or not isinstance(r.get('title'), str):
            continue
        w = title_words(r['title'])
        if w:
            out.append((w, Counter(w), r))
    return out


def _refs(d, refs):
    if refs is None:
        from ..research import notes
        refs = notes.reference_titles(d)
    return prepare_reference_titles(refs)


def title_check(title, d=None, refs=None):
    """How close ``title`` is to the imported reference titles.

    Returns {'score', 'closest': {'title', 'channel', 'own'} | None, 'blocked', 'note', 'reason'}. Blocked when the
    score against another creator's title is at least 0.6; a close title of the owner's own channel only gives a
    note. ``refs`` (rows from ``notes.reference_titles``) can be passed to check many titles at once."""
    prepared = _refs(d, refs)
    words_ = title_words(title)
    best = {True: (0.0, None), False: (0.0, None)}  # own -> (score, row)
    if words_:
        cw = Counter(words_)
        for rw, rc, row in prepared:
            own = bool(row.get('own'))
            cur = best[own][0]
            common = sum((cw & rc).values())
            if not common:
                continue
            upper = 2.0 * common / (len(words_) + len(rw))
            jac = len(set(words_) & set(rw)) / len(set(words_) | set(rw))
            if max(upper, jac) <= cur:
                continue
            sc = title_similarity(words_, rw)
            if sc > cur:
                best[own] = (sc, row)
    other_score, other_row = best[False]
    own_score, own_row = best[True]
    blocked = other_row is not None and other_score >= TITLE_BLOCK
    if blocked:
        score, row = other_score, other_row
    elif own_score > other_score:
        score, row = own_score, own_row
    else:
        score, row = other_score, other_row
    closest = None
    if row is not None:
        closest = {'title': row.get('title', ''), 'channel': row.get('channel', ''), 'own': bool(row.get('own'))}
    note = reason = ''
    if blocked:
        by = other_row.get('channel') or 'another channel'
        reason = (f'The title is too close to "{other_row.get("title", "")}" by {by} (similarity {other_score:.2f}; '
                  f'the limit is {TITLE_BLOCK:.2f}). Write a different title.')
        note = reason
    elif own_row is not None and own_score >= TITLE_BLOCK:
        note = f'A variation of your own video "{own_row.get("title", "")}" (similarity {own_score:.2f}).'
    return {'score': round(score, 3), 'closest': closest, 'blocked': blocked, 'note': note, 'reason': reason,
            'threshold': TITLE_BLOCK}


def titles_check(titles, d=None, refs=None):
    """The most serious ``title_check`` result over several titles (a plan's title and its publish title)."""
    prepared = _refs(d, refs)
    results = [title_check(t, refs=prepared) for t in dict.fromkeys(t for t in titles if isinstance(t, str) and t.strip())]
    if not results:
        return title_check('', refs=prepared)
    return max(results, key=lambda r: (r['blocked'], bool(r['note']), r['score']))


def manifest_title_check(m, d=None, refs=None):
    """``titles_check`` for a compiled manifest: its title and its publish title."""
    md = m.get('metadata') if isinstance(m.get('metadata'), dict) else {}
    return titles_check([m.get('title'), md.get('title')], d, refs)
