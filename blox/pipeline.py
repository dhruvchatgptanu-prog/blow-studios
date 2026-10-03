"""Production task handlers: develop -> voice -> shots -> assemble -> QA -> repair.

Every handler is restartable: progress is persisted after each paid or slow
step, and re-running a handler skips work that is already done.
"""
import json
import os

from . import budget, config, db as dbmod, jobs, llm, prefs as prefsmod, production, repo, store, videos
from .animation import blender as BL
from .manifest import compile as C, director, validate as V
from .qa.run import run_all as run_qa
from .research import service as research
from .story import backlog, generate as G, originality, template
from .tasks import handler
from .util import Blocked, Waiting, new_id, now, stable_hash


def work_dir(video_id, *parts):
    p = config.WORK_DIR / video_id
    for x in parts:
        p = p / x
    os.makedirs(p, exist_ok=True)
    return str(p)


def cast_slug(name, taken):
    base = ''.join(ch for ch in name.lower() if ch.isalnum())[:12] or 'char'
    s, i = base, 2
    while s in taken:
        s, i = f'{base}{i}', i + 1
    return s


# ---------------------------------------------------------------- develop
def build_brief(p, d=None):
    d = d or dbmod.get()
    cands = research.top(30, d)
    sources = []
    for c in cands:
        sig = c['shorts'].get('signals', {})
        if sig.get('reupload_signals') or c['shorts'].get('injection_flags'):
            continue
        tr = d.one("SELECT text FROM transcripts WHERE subject=? AND status='available' ORDER BY created_at DESC",
                   ('yt:' + c['video_id'],))
        an = d.one("SELECT findings FROM ref_analyses WHERE subject=? AND status='done' ORDER BY created_at DESC",
                   ('yt:' + c['video_id'],))
        last = c['snapshots'][-1] if c['snapshots'] else {}
        sources.append({'id': c['video_id'], 'title': c['title'], 'description': c['description'][:600],
                        'duration_s': c['duration_s'], 'views': last.get('views'),
                        'transcript': tr['text'] if tr else None,
                        'analysis': json.loads(an['findings']) if an else None,
                        'evidence': 'transcript' if tr else ('frames' if an else 'metadata_only')})
        if len(sources) >= 8:
            break
    return {'sources': sources, 'created_at': now(),
            'note': ('Top candidates in the monitored sample.' if sources else
                     'No research data yet; the brief uses only the channel niche and identity.')}


def _recent_own(d, exclude=None, limit=30):
    """Recent own stories (premise, ending, script) per video, excluding ``exclude`` and cancelled videos."""
    rows = d.query('''SELECT v.id, c.premise, c.ending, m.body FROM videos v
                      LEFT JOIN concepts c ON c.id = v.concept_id
                      LEFT JOIN manifests m ON m.id = v.manifest_id
                      WHERE v.id <> ? AND v.status <> 'cancelled'
                        AND (v.concept_id IS NOT NULL OR v.manifest_id IS NOT NULL)
                      ORDER BY v.created_at DESC LIMIT ?''', (exclude or '', limit))
    own = []
    for r in rows:
        body = json.loads(r['body']) if r['body'] else {}
        story = body.get('story') or {}
        own.append({'id': r['id'], 'premise': r['premise'] or story.get('premise', ''),
                    'ending': r['ending'] or story.get('ending', ''),
                    'script': ' '.join(ln['text'] for ln in body.get('lines', []))})
    return own


def _embedder(video_id):
    if not llm.available():
        return None
    return lambda texts: llm.embed(f'emb:{video_id}:{stable_hash(texts)}', texts, video_id)


def develop_step(v, p, d, chars):
    vid = v['id']
    st = v['status']
    meta = v['metadata']
    if st == 'discovered':
        brief = build_brief(p, d)
        videos.merge_metadata(vid, {'brief': brief}, d)
        videos.transition(vid, 'researched', brief['note'], expect='discovered', d=d)
        return True
    if st == 'researched':
        brief = meta.get('brief') or build_brief(p, d)
        if story_source(p) == 'backlog':
            item = backlog.take(vid, d)
            if item:
                plan = item['plan']
                story = plan.get('story') or {}
                cid = new_id('cn_')
                d.execute('''INSERT INTO concepts(id, created_at, status, premise, hook, ending, features, inspiration,
                             originality, method) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                          (cid, now(), 'selected', story.get('premise') or plan.get('logline', ''),
                           (plan.get('hook') or {}).get('text', ''), story.get('ending', ''),
                           json.dumps({'source': 'backlog', 'backlog_id': item['id'], 'written_by': item['source']}),
                           json.dumps(plan.get('inspiration') or {}), json.dumps({}), 'backlog'))
                videos.merge_metadata(vid, {'backlog_id': item['id']}, d)
                videos.transition(vid, 'concept_selected', f'Story "{item["title"]}" taken from the backlog',
                                  expect='researched', concept_id=cid, title=item['title'][:100], d=d)
                return True
            if not p['production']['allow_template_stories']:
                raise Blocked('The story backlog is empty. Add stories on the Story backlog page, or connect an '
                              'LLM API key and set the story source to automatic.', state='blocked')
        if not llm.available():
            if not p['production']['allow_template_stories']:
                raise Blocked('Connect OpenAI to write original stories (or allow template stories for dry runs)',
                              state='needs_credentials')
            seed = int(stable_hash([vid])[:8], 16)
            plan = template.make(seed)
            cid = new_id('cn_')
            d.execute('''INSERT INTO concepts(id, created_at, status, premise, hook, ending, features, inspiration,
                         originality, method) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                      (cid, now(), 'selected', plan['story']['premise'], plan['hook']['text'], plan['story']['ending'],
                       json.dumps({'hook_type': 'danger', 'ending_type': 'twist', 'source': 'template', 'seed': seed}),
                       json.dumps(plan['inspiration']), json.dumps({'decision': 'pass', 'note': 'template'}), 'template'))
            videos.merge_metadata(vid, {'template_seed': seed}, d)
            videos.transition(vid, 'concept_selected', 'Template concept (offline dry run)', expect='researched',
                              concept_id=cid, d=d)
            return True
        srcs = brief['sources']
        src_hash = stable_hash([s['id'] for s in srcs])
        cache = store.get('patterns_cache', {}, d)
        if cache.get('hash') == src_hash and now() - cache.get('at', 0) < 12 * 3600:
            patterns = cache['patterns']
        else:
            patterns = G.extract_patterns(f'patterns:{src_hash}', srcs, p, vid) if srcs else {'patterns': [], 'caveats': [
                'No reference videos available; concepts rely on the channel brief only.']}
            store.put('patterns_cache', {'hash': src_hash, 'at': now(), 'patterns': patterns}, d)
            d.execute('INSERT INTO patterns(id, created_at, sources, method, body) VALUES (?,?,?,?,?)',
                      (new_id('pt_'), now(), json.dumps([s['id'] for s in srcs]), 'llm', json.dumps(patterns)))
        own = _recent_own(d, exclude=vid)
        recent = [{'premise': o['premise'], 'ending': o['ending']} for o in own]
        from .learning import brief_notes
        attempt = int(meta.get('concept_attempt', 0))
        concepts = G.generate_concepts(f'concepts:{vid}:{attempt}', patterns, p, chars, recent, brief_notes(d), vid)
        refs = [{'id': s['id'], 'text': ' '.join(filter(None, [s['title'], s['description'], s.get('transcript')]))}
                for s in srcs]
        emb = _embedder(vid)
        chosen, reports = None, []
        for c in concepts:
            rep = originality.check({'premise': c['premise'], 'hook': c['hook'], 'ending': c['ending'],
                                     'script': c['premise'] + ' ' + c['reveal']}, refs, own, emb,
                                    [i['source_id'] for i in c['inspiration']])
            reports.append({'title': c['title'], 'report': rep})
            if rep['decision'] == 'pass' and chosen is None:
                chosen = (c, rep)
        if chosen is None:
            if attempt < 1:
                videos.merge_metadata(vid, {'concept_attempt': attempt + 1, 'originality_rejections': reports}, d)
                return True
            videos.merge_metadata(vid, {'originality_rejections': reports}, d)
            raise Blocked('No concept passed the originality screen after a rewrite; review the candidates.',
                          state='needs_review')
        c, rep = chosen
        cid = new_id('cn_')
        d.execute('''INSERT INTO concepts(id, created_at, status, premise, hook, ending, features, inspiration,
                     originality, method) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                  (cid, now(), 'selected', c['premise'], c['hook'], c['ending'],
                   json.dumps({'hook_type': c['hook_type'], 'ending_type': c['ending_type'],
                               'setting': c['setting_preset'], 'cast': c['cast'], 'patterns': c['pattern_names_used']}),
                   json.dumps({'sources': c['inspiration'], 'patterns': c['pattern_names_used'],
                               'brief_sources': [s['id'] for s in srcs]}), json.dumps(rep), 'llm'))
        videos.merge_metadata(vid, {'concept': c, 'originality_concept': rep}, d)
        videos.transition(vid, 'concept_selected', f'Concept "{c["title"]}" passed originality screening',
                          expect='researched', concept_id=cid, title=c['title'][:100], d=d)
        return True
    if st == 'concept_selected':
        pr = p['production']
        if meta.get('backlog_id'):
            item = backlog.get(meta['backlog_id'], d)
            m = C.compile_plan(item['plan'], fps=pr['fps'], width=pr['width'], height=pr['height'],
                               **C.pace_kwargs(p))
            rep = V.validate(m, p)
            if not rep['ok']:
                raise Blocked('Backlog story no longer validates with the current settings: ' +
                              rep['errors'][0]['message'], state='needs_review')
            refs = [{'id': s['id'], 'text': ' '.join(filter(None, [s['title'], s['description'], s.get('transcript')]))}
                    for s in (meta.get('brief') or {}).get('sources', [])]
            orep = originality.check({'premise': m['story']['premise'], 'hook': m['hook']['text'],
                                      'ending': m['story']['ending'], 'script': ' '.join(ln['text'] for ln in m['lines'])},
                                     refs, _recent_own(d, exclude=vid), _embedder(vid))
            videos.merge_metadata(vid, {'originality_script': orep}, d)
            if orep['decision'] != 'pass':
                raise Blocked('This backlog story is too close to a reference or a recent video; review it or reject '
                              'it in the backlog.', state='needs_review')
            source = 'backlog'
        elif meta.get('template_seed') is not None:
            plan = template.make(meta['template_seed'])
            m = C.compile_plan(plan, fps=pr['fps'], width=pr['width'], height=pr['height'], **C.pace_kwargs(p))
            rep = V.validate(m, p)
            if not rep['ok']:
                raise Blocked('Template plan failed validation: ' + rep['errors'][0]['message'], state='failed')
            source = 'template'
        else:
            concept = meta['concept']
            cast_map, taken = {}, set()
            for ch_id in concept['cast'] or list(chars)[:2]:
                if ch_id in chars:
                    cast_map[cast_slug(chars[ch_id]['name'], taken)] = ch_id
                    taken.add(list(cast_map)[-1])
            if not cast_map:
                raise Blocked('No active characters available for the cast', state='needs_review')
            sub_chars = {k: chars[k] for k in cast_map.values()}
            attempt = int(meta.get('script_attempt', 0))
            plan, m, rep = G.plan_with_validation(f'plan:{vid}:{v["concept_id"]}:{attempt}', concept, p, sub_chars,
                                                  cast_map, vid)
            refs = [{'id': s['id'], 'text': ' '.join(filter(None, [s['title'], s['description'], s.get('transcript')]))}
                    for s in (meta.get('brief') or {}).get('sources', [])]
            script_text = ' '.join(ln['text'] for ln in m['lines'])
            orep = originality.check({'premise': m['story']['premise'], 'hook': m['hook']['text'],
                                      'ending': m['story']['ending'], 'script': script_text}, refs, _recent_own(d, exclude=vid),
                                     _embedder(vid))
            videos.merge_metadata(vid, {'originality_script': orep}, d)
            if orep['decision'] == 'rewrite' and attempt < 1:
                videos.merge_metadata(vid, {'script_attempt': attempt + 1}, d)
                return True
            if orep['decision'] != 'pass':
                raise Blocked('The script did not pass the originality screen; review it before production.',
                              state='needs_review')
            m['inspiration'] = (meta.get('concept') or {}).get('inspiration', [])
            source = 'llm'
        names = {c['id']: chars.get(c['character_id'], {}).get('name', c['id']) for c in m['cast']}
        mid = repo.save_manifest(vid, m, source, director.script(m, names), rep, d)
        meta_md = m.get('metadata') or {}
        videos.merge_metadata(vid, {'publish_metadata': {'title': meta_md.get('title') or m['title'],
                                                         'description': meta_md.get('description', ''),
                                                         'tags': meta_md.get('tags', [])}}, d)
        videos.transition(vid, 'scripted', f'Production manifest validated ({source})', expect='concept_selected',
                          manifest_id=mid, title=(meta_md.get('title') or m['title'])[:100], d=d)
        return True
    if st == 'scripted':
        storyboard(v, p, d)
        return False
    return False


def story_source(p):
    """'backlog' when configured, or automatically when no LLM API is connected; otherwise 'llm'."""
    src = p['production'].get('story_source', 'auto')
    if src == 'backlog' or (src == 'auto' and not llm.available()):
        return 'backlog'
    return 'llm'


def estimate_video(m, p):
    pr, prices = p['production'], p['budget']['prices']
    words = sum(len(C.words(ln['text'])) for ln in m['lines'])
    # Not reduced by the speech rate: gpt-4o-mini-tts only takes the rate as an instruction, so the
    # conservative real-time estimate is kept. Duration-based items below use the compiled (paced) length.
    speech_min = words / 150.0
    est = {}
    if pr['tts_provider'] == 'openai':
        est['tts'] = speech_min * prices['openai_tts_per_min'] + speech_min * prices['openai_asr_per_min']
    elif pr['tts_provider'] == 'elevenlabs':
        est['tts'] = sum(len(ln['text']) for ln in m['lines']) / 1000 * prices['elevenlabs_per_kchar']
    else:
        est['tts'] = 0.0
    gen_s = sum((s['end_frame'] - s['start_frame']) / m['fps'] for s in m['shots'] if s['renderer'] == 'runway')
    est['animation'] = gen_s * prices['runway_per_second']
    local_min = sum((s['end_frame'] - s['start_frame']) for s in m['shots'] if s['renderer'] == 'blender') * 2.0 / 60
    est['local_render'] = local_min * prices['local_render_per_min']
    dur_min = m['duration_frames'] / m['fps'] / 60
    # QA uses paid speech recognition and vision review only when an OpenAI key is connected.
    est['qa'] = (dur_min * prices['openai_asr_per_min'] + 10 * prices['openai_vision_per_image'] +
                 (3000 / 1e6 * prices['openai_text_out_per_mtok'])) if llm.available() else 0.0
    est['repairs_allowance'] = (est['tts'] + est['animation']) * min(1.0, p['budget']['repair_share'])
    est['total'] = round(sum(est.values()), 4)
    return {k: round(v, 4) for k, v in est.items()}


def storyboard(v, p, d):
    vid = v['id']
    mf = repo.manifest(v['manifest_id'], d)
    m = mf['body']
    rep = V.validate(m, p)
    if not rep['ok']:
        raise Blocked('Manifest no longer validates: ' + rep['errors'][0]['message'], state='needs_review')
    for s in m['shots']:
        repo.upsert_shot(vid, mf['id'], s['id'], s['renderer'], d)
    for ln in m['lines']:
        repo.upsert_line(vid, mf['id'], ln, d)
    est = estimate_video(m, p)
    spent = budget.spent(d, video_id=vid)
    if spent + est['total'] - est['repairs_allowance'] > p['budget']['per_video_usd'] + 1e-9:
        raise Blocked(f'Estimated cost ${est["total"]:.2f} (plus ${spent:.2f} already spent) exceeds the per-video '
                      f'budget of ${p["budget"]["per_video_usd"]:.2f}.', state='blocked')
    summary = budget.summary(p, d)
    if summary['daily_remaining'] < est['total'] - est['repairs_allowance']:
        raise Blocked('Not enough daily budget left for this video; production resumes when the day rolls over.',
                      state='blocked')
    videos.merge_metadata(vid, {'cost_estimate': est}, d)
    videos.transition(vid, 'storyboarded', f'Estimated cost ${est["total"]:.2f}', expect='scripted', d=d)
    jobs.enqueue('video.voice', {'manifest_id': mf['id']}, video_id=vid, idempotency_key=f'voice:{mf["id"]}:0',
                 ckey='openai' if p['production']['tts_provider'] == 'openai' else None, d=d)


@handler('video.develop')
def develop(ctx):
    d = dbmod.get()
    p = prefsmod.get(d)
    chars = repo.characters(d=d)
    for _ in range(8):
        ctx.beat()
        v = videos.get(ctx.video_id, d)
        if v['status'] not in ('discovered', 'researched', 'concept_selected', 'scripted'):
            return {'status': v['status']}
        if not develop_step(v, p, d, chars):
            break
    return {'status': videos.get(ctx.video_id, d)['status']}


# ---------------------------------------------------------------- voice
def _cast(m, d):
    return production.cast_characters(m, repo.characters(active_only=False, d=d))


@handler('video.voice')
def voice(ctx):
    d = dbmod.get()
    p = prefsmod.get(d)
    vid = ctx.video_id
    mid = ctx.payload['manifest_id']
    mf = repo.manifest(mid, d)
    m = mf['body']
    cast = _cast(m, d)
    wd = work_dir(vid, 'voice')
    for ln in m['lines']:
        ctx.beat()
        row = repo.upsert_line(vid, mid, ln, d)
        if row['status'] == 'done' and row['text'] == ln['text'] and not ctx.payload.get('force', {}).get(ln['id']):
            continue
        who = cast.get(ln['speaker']) if ln['speaker'] != 'narrator' else {'name': 'Narrator', 'voice': {}}
        attempt = row['repair_attempts']
        res = production.tts.synthesize_line(ln, who, p, wd, vid, attempt=attempt)
        repo.upsert_line(vid, mid, ln, d, status='done', file=repo.rel(res['file']), duration_s=res['duration_s'],
                         spoken_text=res['spoken_text'], alignment_kind=res['alignment_kind'], spec_hash=res['spec_hash'],
                         text=ln['text'], voice=res['voice'],
                         alignment={'words': res['words'], 'provider': res['provider'], 'test_voice': res['test_voice'],
                                    'asr_text': res['asr_text'], 'voiced_regions': res['voiced_regions'],
                                    'peak_dbfs': res['peak_dbfs'], 'internal_silence_s': res['internal_silence_s'],
                                    'voice_meta': res.get('voice_meta')})
    credits = sorted({(r['alignment'] or {}).get('voice_meta', {}).get('attribution')
                      for r in repo.lines(mid, d) if ((r['alignment'] or {}).get('voice_meta') or {}).get('attribution')})
    videos.merge_metadata(vid, {'voice_credits': credits}, d)
    results = repo.line_results(mid, d)
    m2, fitted, report = production.retime(m, results, p)
    too_long = [r for r in report if r['action'] == 'rewrite_needed']
    if too_long:
        n_rw = int(videos.get(vid, d)['metadata'].get('line_rewrites', 0))
        if n_rw < 2 and llm.available():
            m3 = rewrite_lines(m, too_long, p, vid, n_rw)
            names = {c['id']: c['id'] for c in m3['cast']}
            new_mid = repo.save_manifest(vid, m3, 'line_rewrite', director.script(m3, names), V.validate(m3, p), d)
            videos.merge_metadata(vid, {'line_rewrites': n_rw + 1}, d)
            for s in m3['shots']:
                repo.upsert_shot(vid, new_mid, s['id'], s['renderer'], d)
            jobs.enqueue('video.voice', {'manifest_id': new_mid}, video_id=vid,
                         idempotency_key=f'voice:{new_mid}:0', d=d)
            return {'rewrite': [r['line'] for r in too_long]}
        raise Blocked('Lines too long for their slots after gentle fitting: ' +
                      ', '.join(f'{r["line"]} ({r["needed_s"]}s > {r["window_s"]}s)' for r in too_long),
                      state='needs_review')
    for lid, r in fitted.items():
        if r.get('tempo'):
            ln = next(x for x in m2['lines'] if x['id'] == lid)
            row = next(x for x in repo.lines(mid, d) if x['line_key'] == lid)
            al = row['alignment']
            al['words'] = r['words']
            al['tempo'] = r['tempo']
            repo.upsert_line(vid, mid, ln, d, file=repo.rel(r['file']), duration_s=r['duration_s'], alignment=al)
    rep = V.validate(m2, p, measured=True)
    names = {c['id']: cast.get(c['id'], {}).get('name', c['id']) for c in m2['cast']}
    d.execute('UPDATE manifests SET body=?, director_script=?, validation=? WHERE id=?',
              (json.dumps(m2), director.script(m2, names), json.dumps(rep), mid))
    store.audit('manifest_retimed', {'video': vid, 'manifest': mid, 'report': report}, d=d)
    v = videos.get(vid, d)
    if v['status'] == 'storyboarded':
        videos.transition(vid, 'generating', 'Voices recorded; rendering shots', d=d)
    if v['manifest_id'] != mid:
        d.execute('UPDATE videos SET manifest_id=? WHERE id=?', (mid, vid))
    for s in m2['shots']:
        repo.upsert_shot(vid, mid, s['id'], s['renderer'], d)
        sh = next(x for x in repo.shots(mid, d) if x['shot_key'] == s['id'])
        if sh['status'] != 'done' or ctx.payload.get('rerender'):
            jobs.enqueue('shot.render', {'manifest_id': mid, 'shot': s['id']}, video_id=vid,
                         idempotency_key=f'shot:{mid}:{s["id"]}:{sh["repair_attempts"]}',
                         ckey='blender' if s['renderer'] == 'blender' else s['renderer'], d=d)
    _maybe_assemble(vid, mid, d)
    return {'lines': len(m2['lines']), 'retime': report}


def rewrite_lines(m, too_long, p, vid, attempt):
    import copy
    schema = G.obj({'lines': G.arr(G.obj({'id': G.STR, 'text': G.STR}))})
    payload = {'instruction': 'Rewrite each line to be shorter so it fits the stated seconds at the stated pace. '
                              'Keep meaning, character voice and any setup/payoff words. Return only these lines.',
               'lines': [{'id': r['line'], 'text': next(l['text'] for l in m['lines'] if l['id'] == r['line']),
                          'seconds_available': r['window_s'], 'currently_needs': r['needed_s']} for r in too_long]}
    data, _ = llm.chat_json(f'rewrite:{vid}:{attempt}', system='You are a concise dialogue editor.',
                            content=json.dumps(payload), schema_name='line_rewrite', schema=schema, video_id=vid,
                            operation='line_rewrite', category='script', est_in=800, est_out=300, p=p)
    m2 = copy.deepcopy(m)
    new = {x['id']: x['text'] for x in data['lines']}
    for ln in m2['lines']:
        if ln['id'] in new and new[ln['id']].strip():
            ln['text'] = new[ln['id']].strip()[:400]
            ln['est_frames'] = C.estimate_line_frames(ln, m2['fps'])
            ln['est_end_frame'] = ln['start_frame'] + ln['est_frames']
    m2['captions'] = C.captions_for(m2['lines'], m2['fps'])
    return m2


# ---------------------------------------------------------------- shots
def solved_for(vid, mid, m, d):
    """Solve motion once per manifest timing + alignment + repair params; cached on disk."""
    results = repo.line_results(mid, d)
    repair = {'shots': {}, 'characters': {}, 'lines': {}}
    for sh in repo.shots(mid, d):
        rp = (sh['detail'] or {}).get('repair_params') or {}
        if rp:
            repair['shots'][sh['shot_key']] = rp
            repair['lines'].update(rp.get('lines') or {})
            if rp.get('pelvis_drop_extra') and rp.get('character'):
                s = next(x for x in m['shots'] if x['id'] == sh['shot_key'])
                repair['characters'].setdefault(rp['character'], {'ranges': []})['ranges'].append(
                    {'start': s['start_frame'], 'end': s['end_frame'], 'pelvis_drop_extra': rp['pelvis_drop_extra']})
    from .animation import solver as SV
    # The setting and title decide the set layout (and so where the camera may stand), so they are part
    # of the key too.
    key = stable_hash([m['lines'], m['tracks'], m['shots'], [(k, r['words']) for k, r in sorted(results.items())], repair,
                       m.get('setting'), m.get('title'), SV.code_version()])
    path = os.path.join(work_dir(vid, mid), f'solved_{key}.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f), path
    cast = _cast(m, d)
    if repair['shots'] or repair['characters'] or repair['lines']:
        solved = SV.solve(m, {cid: c['bible'] for cid, c in cast.items()}, production.alignments(results), repair,
                          envelopes=production.envelopes(m, results))
    else:
        solved = production.solve(m, cast, results)
    with open(path, 'w') as f:
        json.dump(solved, f)
    return solved, path


@handler('shot.render')
def render_shot(ctx):
    d = dbmod.get()
    p = prefsmod.get(d)
    vid, mid, key = ctx.video_id, ctx.payload['manifest_id'], ctx.payload['shot']
    m = repo.manifest(mid, d)['body']
    s = next(x for x in m['shots'] if x['id'] == key)
    sh = next(x for x in repo.shots(mid, d) if x['shot_key'] == key)
    out_dir = work_dir(vid, mid, 'shots', f'{key}_r{sh["repair_attempts"]}')
    if s['renderer'] == 'blender':
        solved, solved_path = solved_for(vid, mid, m, d)
        cast = _cast(m, d)
        res = BL.render_range(m, {cid: c['bible'] for cid, c in cast.items()}, solved, s['start_frame'], s['end_frame'],
                              out_dir, p, quality=ctx.payload.get('quality', 'final'),
                              on_progress=lambda _msg: ctx.beat(), should_stop=ctx.stop_reason)
        detail = dict(sh['detail'] or {}, engine=res['engine'], solved=repo.rel(solved_path), frames=res['frames'])
        repo.upsert_shot(vid, mid, key, 'blender', d, status='done', file=repo.rel(res['video']),
                         telemetry_file=repo.rel(res['telemetry']), preview_file=repo.rel(res['preview']),
                         native_w=res['width'], native_h=res['height'], attempts=sh['attempts'] + 1, detail=detail,
                         error='')
    elif s['renderer'] == 'runway':
        from .animation import generative
        res = generative.render_shot(ctx, vid, mid, m, s, sh, out_dir, p)
        if res is None:
            raise Waiting('Waiting for the generative provider', delay=15)
    elif s['renderer'] == 'clip':
        from .animation import clips
        res = clips.render_shot(m, s, out_dir)
        repo.upsert_shot(vid, mid, key, 'clip', d, status='done', file=repo.rel(res['video']),
                         native_w=res['width'], native_h=res['height'], attempts=sh['attempts'] + 1)
    _maybe_assemble(vid, mid, d)
    return {'shot': key}


def _maybe_assemble(vid, mid, d):
    rows = repo.shots(mid, d)
    if rows and all(r['status'] == 'done' for r in rows) and all(
            r['status'] == 'done' for r in repo.lines(mid, d)):
        stamp = stable_hash([(r['shot_key'], r['file'], r['attempts']) for r in rows] +
                            [(r['line_key'], r['file']) for r in repo.lines(mid, d)])
        jobs.enqueue('video.assemble', {'manifest_id': mid}, video_id=vid, idempotency_key=f'assemble:{mid}:{stamp}', d=d)


# ---------------------------------------------------------------- assemble + QA
@handler('video.assemble')
def assemble(ctx):
    d = dbmod.get()
    p = prefsmod.get(d)
    vid, mid = ctx.video_id, ctx.payload['manifest_id']
    v = videos.get(vid, d)
    if v['status'] in ('generating', 'repairing'):
        videos.transition(vid, 'rendering', 'Assembling final video', d=d)
    m = repo.manifest(mid, d)['body']
    shot_files = {r['shot_key']: repo.absp(r['file']) for r in repo.shots(mid, d)}
    results = repo.line_results(mid, d)
    music = None
    if p['production']['music'] == 'asset' and p['production']['music_asset']:
        music = str(config.MEDIA_DIR / p['production']['music_asset'])
    rid_hint = new_id('rd_')
    tele = [repo.absp(r['telemetry_file']) for r in repo.shots(mid, d) if r['telemetry_file']]
    out = production.assemble(m, shot_files, results, p, work_dir(vid, mid, 'render', rid_hint),
                              (v['metadata'].get('publish_metadata') or {}).get('title') or m['title'], music,
                              telemetry_paths=tele)
    rid = repo.save_render(vid, mid, 'assembled', repo.rel(out['final']), repo.rel(out['cover']),
                           repo.rel(out['thumbnail']), {k: out[k] for k in ('width', 'height', 'captions_file')} |
                           {'joined': repo.rel(out['joined']), 'dir': repo.rel(os.path.dirname(out['final'])),
                            'mix': {'paths': {k: repo.rel(x) for k, x in out['mix']['paths'].items()},
                                    'placements': out['mix']['placements'], 'music_source': out['mix']['music_source'],
                                    'ducking': out['mix']['ducking']},
                            'captions': out['captions'], 'thumbnail_16x9': repo.rel(out['thumbnail_16x9']),
                            'native_render_size': out['native_render_size'],
                            'shot_native_sizes': [{'shot': r['shot_key'], 'renderer': r['renderer'],
                                                   'native': [r['native_w'], r['native_h']],
                                                   'upscaled': bool((r['detail'] or {}).get('upscaled'))}
                                                  for r in repo.shots(mid, d)]}, d)
    if videos.get(vid, d)['status'] == 'rendering':
        videos.transition(vid, 'checking', 'Running quality control', d=d)
    jobs.enqueue('video.qa', {'manifest_id': mid, 'render_id': rid}, video_id=vid, idempotency_key=f'qa:{rid}', d=d)
    return {'render': rid}


def assembly_from_render(r):
    det = r['detail']
    mix = det['mix']
    return {'final': repo.absp(r['file']), 'joined': repo.absp(det['joined']), 'width': det['width'],
            'height': det['height'], 'captions': det.get('captions', []),
            'mix': {'paths': {k: repo.absp(x) for k, x in mix['paths'].items()}, 'placements': mix['placements']},
            'native_render_size': det.get('native_render_size'),
            'shot_native_sizes': det.get('shot_native_sizes', [])}


@handler('video.qa')
def qa(ctx):
    d = dbmod.get()
    p = prefsmod.get(d)
    vid, mid, rid = ctx.video_id, ctx.payload['manifest_id'], ctx.payload['render_id']
    m = repo.manifest(mid, d)['body']
    r = repo.render(rid, d)
    solved, _ = solved_for(vid, mid, m, d)
    tele = [repo.absp(s['telemetry_file']) for s in repo.shots(mid, d) if s['telemetry_file']]
    report = run_qa(m, solved, tele, repo.line_results(mid, d), assembly_from_render(r), p, vid, rid)
    qid = repo.save_qa(vid, rid, report, d)
    verdict = report['verdict']
    if verdict == 'approved':
        videos.transition(vid, 'approved', 'QA passed: ' + json.dumps(report['summary']), d=d)
    elif verdict == 'repair':
        videos.transition(vid, 'repairing', 'QA found repairable issues: ' + ', '.join(report['reasons'][:5]), d=d)
        jobs.enqueue('video.repair', {'qa_report_id': qid, 'manifest_id': mid}, video_id=vid,
                     idempotency_key=f'repair:{qid}', d=d)
    elif verdict == 'hold':
        videos.hold(vid, 'needs_review', 'QA is uncertain about: ' + ', '.join(report['reasons'][:6]), d=d)
    else:
        videos.hold(vid, 'blocked', 'QA blocked publishing: ' + ', '.join(report['reasons'][:6]), d=d)
    return {'verdict': verdict, 'qa_report': qid}


# ---------------------------------------------------------------- repair
@handler('video.repair')
def repair(ctx):
    d = dbmod.get()
    p = prefsmod.get(d)
    vid, mid = ctx.video_id, ctx.payload['manifest_id']
    q = repo.qa(ctx.payload['qa_report_id'], d)['checks']
    v = videos.get(vid, d)
    rounds = int(v['metadata'].get('repair_rounds', 0)) + 1
    if rounds > p['qa']['max_repair_rounds']:
        videos.hold(vid, 'blocked', f'Repair limit reached ({p["qa"]["max_repair_rounds"]} rounds); skipping this '
                                    'video rather than publishing a known defect.', d=d)
        return {'blocked': 'repair rounds'}
    videos.merge_metadata(vid, {'repair_rounds': rounds}, d)
    fails = [c for c in q['checks'] if c['status'] == 'fail' and c.get('repair')]
    shots_to_render, lines_to_voice, reassemble, retry_only = {}, set(), False, set()
    limit = p['qa']['max_repairs_per_target']
    for c in fails:
        rp = c['repair']
        act = rp['action']
        if act in ('rewrite_script', 're_render_all_final_quality'):
            videos.hold(vid, 'needs_review', f'Check "{c["name"]}" needs a script or setting change: {act}', d=d)
            return {'held': act}
        if act == 're_render_shot' and rp.get('shot'):
            sh = next((x for x in repo.shots(mid, d) if x['shot_key'] == rp['shot']), None)
            if not sh:
                continue
            if sh['repair_attempts'] >= limit:
                videos.hold(vid, 'blocked', f'Shot {rp["shot"]} still fails "{c["name"]}" after {limit} repairs', d=d)
                return {'blocked': rp['shot']}
            # Several checks can ask for the same shot; their adjustments are combined.
            prev = shots_to_render.get(rp['shot'])
            params = prev[1] if prev else json.loads(json.dumps((sh['detail'] or {}).get('repair_params') or {}))
            merge_repair_params(params, rp.get('params') or {})
            if not rp.get('params'):
                retry_only.add(rp['shot'])  # a plain retry (render glitch), not a settings change
            shots_to_render[rp['shot']] = (sh, params, prev[2] if prev else c)
        elif act == 'revoice_line' and rp.get('line'):
            lines_to_voice.add(rp['line'])
        elif act in ('remix', 'rebuild_captions', 're_assemble', 're_encode'):
            reassemble = True
    voice_rows = {}
    for lid in lines_to_voice:
        row = next(x for x in repo.lines(mid, d) if x['line_key'] == lid)
        if row['repair_attempts'] >= limit:
            videos.hold(vid, 'blocked', f'Line {lid} still fails after {limit} re-voicing attempts', d=d)
            return {'blocked': lid}
        voice_rows[lid] = row
    # The renderer is deterministic: a settings repair that leaves a shot's settings as they were would
    # reproduce the same frames, so it is skipped and the video goes to the owner instead. Plain retries
    # (black frames, missing telemetry) stay allowed up to the per-shot limit.
    unchanged = [k for k, (sh, params, c) in shots_to_render.items()
                 if k not in retry_only and params == ((sh['detail'] or {}).get('repair_params') or {})]
    if unchanged and not voice_rows and len(unchanged) == len(shots_to_render):
        videos.hold(vid, 'needs_review', 'QA failures remain that an automatic re-render cannot change (shot ' +
                    ', '.join(unchanged) + '): ' + ', '.join(c['name'] for c in fails[:5]), d=d)
        return {'held': 'repair would not change the output'}
    for k in unchanged:
        shots_to_render.pop(k)
    if not (shots_to_render or voice_rows or reassemble):
        videos.hold(vid, 'needs_review', 'QA asked for a repair that Blox cannot perform automatically: ' +
                    ', '.join(c['name'] for c in fails[:5]), d=d)
        return {'held': 'no automatic repair'}
    # Change state before queuing work so a fast worker never sees the old state.
    videos.transition(vid, 'generating' if (shots_to_render or voice_rows) else 'rendering',
                      f'Repair round {rounds}: {len(shots_to_render)} shot(s), {len(voice_rows)} line(s)', d=d)
    for key, (sh, params, c) in shots_to_render.items():
        repo.upsert_shot(vid, mid, key, sh['renderer'], d, status='pending', repair_attempts=sh['repair_attempts'] + 1,
                         detail=dict(sh['detail'] or {}, repair_params=params))
        _record_repair(d, vid, ctx.payload['qa_report_id'], f'shot:{key}', 're_render_shot', sh['repair_attempts'] + 1,
                       {'check': c['id'], 'params': params})
        jobs.enqueue('shot.render', {'manifest_id': mid, 'shot': key}, video_id=vid,
                     idempotency_key=f'shot:{mid}:{key}:{sh["repair_attempts"] + 1}', ckey='blender', d=d)
    for lid, row in voice_rows.items():
        ln = next(x for x in repo.manifest(mid, d)['body']['lines'] if x['id'] == lid)
        repo.upsert_line(vid, mid, ln, d, status='pending', repair_attempts=row['repair_attempts'] + 1)
        _record_repair(d, vid, ctx.payload['qa_report_id'], f'line:{lid}', 'revoice_line', row['repair_attempts'] + 1, {})
    if voice_rows:
        jobs.enqueue('video.voice', {'manifest_id': mid, 'rerender': True}, video_id=vid,
                     idempotency_key=f'voice:{mid}:repair{rounds}', d=d)
    if reassemble and not shots_to_render and not voice_rows:
        _record_repair(d, vid, ctx.payload['qa_report_id'], 'mix', 're_assemble', rounds, {})
        jobs.enqueue('video.assemble', {'manifest_id': mid}, video_id=vid, idempotency_key=f'assemble:{mid}:repair{rounds}',
                     d=d)
    return {'round': rounds}


def merge_repair_params(params, new):
    """Combine a QA repair request into a shot's accumulated repair settings (in place)."""
    for k, val in new.items():
        if k == 'pelvis_drop_extra':
            params[k] = round(params.get(k, 0) + val, 3)
        elif k == 'lines':
            lines = params.setdefault('lines', {})
            for lid, fix in val.items():
                cur = lines.setdefault(lid, {})
                if 'mouth_shift_frames' in fix:  # QA measures the remaining lag after earlier shifts
                    cur['mouth_shift_frames'] = max(-6, min(6, cur.get('mouth_shift_frames', 0) + fix['mouth_shift_frames']))
                if 'mouth_gain' in fix:
                    cur['mouth_gain'] = round(min(2.0, cur.get('mouth_gain', 1.0) * fix['mouth_gain']), 3)
        else:
            params[k] = val
    return params


def _record_repair(d, vid, qid, target, action, attempt, detail):
    d.execute('INSERT INTO repairs(id, video_id, qa_report_id, target, action, status, attempt, detail, created_at, '
              'updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)', (new_id('rp_'), vid, qid, target, action, 'queued', attempt,
                                                          json.dumps(detail), now(), now()))
