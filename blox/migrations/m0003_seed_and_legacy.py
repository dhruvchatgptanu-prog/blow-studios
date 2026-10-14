"""Seed the original cast and convert legacy projects (non-destructive).

* Legacy ``projects``, ``jobs``, ``assets`` and ``reservations`` rows are kept
  as they are; new rows reference them.
* Each legacy project becomes a video (origin ``legacy``). Projects whose
  scenes all use uploaded footage get a footage manifest and can be rendered
  again. Projects that relied on Runway generation from free-text scenes are
  imported for review, because the new renderer needs a cast and structured
  directions; nothing is guessed.
* A finished legacy MP4 is linked as a render; a legacy YouTube id is linked
  and verified (published only if YouTube reports it public).
* Unfinished legacy jobs are cancelled (not resumed, to avoid paying twice);
  their provider task ids are copied into the video for reconciliation.
* Legacy budget reservations are carried into the ledger as estimated spend.
"""
import json
import time
import secrets


def _id(prefix):
    return prefix + secrets.token_hex(10)


def up(d):
    from ..demo import CHARACTERS
    t = time.time()
    if not d.one('SELECT 1 AS x FROM characters'):
        for c in CHARACTERS:
            d.execute('INSERT INTO characters(id, name, bible, voice, active, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
                      (c['id'], c['name'], json.dumps(c['bible']), json.dumps(c['voice']), 1, t, t))
    for row in d.query('SELECT * FROM projects'):
        if d.one('SELECT 1 AS x FROM videos WHERE legacy_project_id=?', (row['id'],)):
            continue
        p = json.loads(row['body'])
        scenes = p.get('scenes') or []
        all_clips = bool(scenes) and all(s.get('clip') for s in scenes)
        vid = _id('v_')
        meta = {'legacy': {'topic': p.get('topic', ''), 'character': p.get('character', ''),
                           'format': p.get('format'), 'scenes': scenes, 'description': p.get('description', ''),
                           'reference': p.get('reference', ''), 'music': p.get('music', '')},
                'publish_metadata': {'title': p.get('title', 'Untitled')[:100], 'description': p.get('description', ''),
                                     'tags': []}}
        status, reason = 'needs_review', ''
        manifest = None
        if all_clips and p.get('format', 'shorts') == 'shorts':
            t0 = 0.0
            shots, lines, beats = [], [], []
            for i, s in enumerate(scenes):
                dur = float(s.get('duration', 5))
                shots.append({'id': f's{i + 1}', 'start_s': t0, 'end_s': t0 + dur, 'renderer': 'clip',
                              'clip': {'asset': s['clip'], 'trim_s': float(s.get('trim', 0))},
                              'camera': {'subject': 'none', 'framing_start': 'medium', 'framing_end': 'medium'}})
                if (s.get('narration') or '').strip() and p.get('narration', True):
                    lines.append({'id': f'l{i + 1}', 'speaker': 'narrator', 'text': s['narration'].strip()[:400],
                                  't': t0 + 0.2, 'emotion': 'neutral', 'pace': 'normal', 'volume': 'normal'})
                for k in range(int(dur)):
                    sec = int(t0) + k
                    purpose = 'hook' if sec < 3 else ('payoff' if i == len(scenes) - 1 else 'setup')
                    beats.append({'start_s': sec, 'purpose': purpose,
                                  'description': (s.get('action') or '')[:200] if k == 0 else ''})
                t0 += dur
            manifest = {'source': 'legacy_import', 'title': p.get('title', 'Untitled'), 'duration_s': t0,
                        'hook': {'text': (scenes[0].get('narration') or scenes[0].get('action') or '')[:200],
                                 'question': '', 't_end': 3.0},
                        'payoff': {'text': (scenes[-1].get('narration') or scenes[-1].get('action') or '')[:200],
                                   't_start': max(0.0, t0 - 5)},
                        'setting': {'preset': 'studio', 'time_of_day': 'noon', 'lighting': 'natural', 'props': []},
                        'cast': [], 'shots': shots, 'beats': beats, 'lines': lines, 'music': [{'t': 0, 'cue': 'none'}],
                        'metadata': meta['publish_metadata']}
            reason = 'Imported from the previous version (footage scenes). Review and render again.'
        else:
            reason = ('Imported from the previous version. Its scenes were free-text directions for Runway; choose a '
                      'cast and renderer in the editor before producing it again.')
        if p.get('youtube_id'):
            status = 'uploaded_private'
            reason = 'Uploaded by the previous version; verifying its status on YouTube.'
        d.execute('''INSERT INTO videos(id, title, status, status_reason, origin, metadata, features, settings,
                     legacy_project_id, youtube_video_id, youtube_url, created_at, updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (vid, p.get('title', 'Untitled')[:100], status, reason, 'legacy', json.dumps(meta), '{}', '{}',
                   row['id'], p.get('youtube_id') or None,
                   f'https://www.youtube.com/watch?v={p["youtube_id"]}' if p.get('youtube_id') else None,
                   row['updated'], t))
        d.execute('INSERT INTO video_events(id, video_id, at, from_status, to_status, actor, note) VALUES (?,?,?,?,?,?,?)',
                  (_id('ev_'), vid, t, None, status, 'migration', reason))
        if manifest:
            from ..manifest.compile import compile_plan
            from ..manifest.director import script
            try:
                m = compile_plan(manifest, fps=30, width=1080, height=1920)
                mid = _id('mf_')
                d.execute('INSERT INTO manifests(id, video_id, version, source, body, director_script, validation, '
                          'created_at) VALUES (?,?,?,?,?,?,?,?)',
                          (mid, vid, 1, 'legacy_import', json.dumps(m), script(m), '{}', t))
                meta['resume_status'] = 'scripted'
                d.execute('UPDATE videos SET manifest_id=?, metadata=? WHERE id=?', (mid, json.dumps(meta), vid))
            except (ValueError, KeyError, TypeError) as e:
                meta['legacy_import_error'] = str(e)[:300]
                d.execute('UPDATE videos SET metadata=? WHERE id=?', (json.dumps(meta), vid))
        if p.get('output'):
            rid = _id('rd_')
            d.execute('INSERT INTO renders(id, video_id, manifest_id, status, file, detail, created_at) VALUES (?,?,?,?,?,?,?)',
                      (rid, vid, '', 'legacy', 'files/' + p['output'], json.dumps({'legacy': True}), t))
            d.execute('UPDATE videos SET render_id=? WHERE id=?', (rid, vid))
        if p.get('youtube_id'):
            uid = _id('up_')
            d.execute('''INSERT INTO uploads(id, video_id, status, marker, youtube_video_id, requested, observed,
                         created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                      (uid, vid, 'uploaded', 'legacy' + uid[3:13], p['youtube_id'], json.dumps({'status': {}}),
                       json.dumps({'legacy': True}), t, t))
            d.execute('''INSERT INTO tasks(id, kind, role, video_id, payload, status, priority, due_at, attempts,
                         max_attempts, idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (_id('tk_'), 'video.verify', 'publish', vid, json.dumps({'upload_id': uid}), 'queued', 100, t, 0,
                       6, 'verify:' + uid, t, t))
    for j in d.query("SELECT * FROM jobs WHERE status IN ('queued','running','waiting','review')"):
        v = d.one('SELECT id, metadata FROM videos WHERE legacy_project_id=?', (j['project'],))
        if v:
            meta = json.loads(v['metadata'])
            meta['legacy_job'] = {'id': j['id'], 'kind': j['kind'], 'status': j['status'], 'state': json.loads(j['state']),
                                  'note': 'Cancelled during upgrade. Provider task ids above can be checked in the '
                                          'provider dashboard; no task was resubmitted.'}
            d.execute('UPDATE videos SET metadata=? WHERE id=?', (json.dumps(meta), v['id']))
        d.execute("UPDATE jobs SET status='cancelled', error=? WHERE id=?",
                  ('Superseded by Blox Studio 2 during upgrade; not resumed to avoid duplicate paid requests.', j['id']))
    for r in d.query('SELECT * FROM reservations'):
        key = 'legacy:' + r['job']
        if not d.one('SELECT 1 AS x FROM budget_ledger WHERE key=?', (key,)):
            d.execute('''INSERT INTO budget_ledger(id, key, video_id, category, provider, status, estimate, actual,
                         actual_kind, local_day, local_month, created_at, updated_at, note) VALUES
                         (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (_id('bl_'), key, None, 'other', 'legacy', 'committed', r['estimate'], r['estimate'], 'estimated',
                       r['day'], r['day'][:7], t, t, 'Legacy per-job reservation (estimate)'))
