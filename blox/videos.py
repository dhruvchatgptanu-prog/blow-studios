"""Persisted production state machine.

discovered -> researched -> concept_selected -> scripted -> storyboarded ->
generating -> rendering -> checking -> (repairing -> ...) -> approved ->
upload_started -> uploaded_private -> processing_verified -> scheduled -> published

Hold states (blocked, needs_credentials, needs_review) remember where the video
was so it can resume. failed and cancelled are terminal. Every transition is
compare-and-set and recorded in ``video_events``.
"""
import json

from . import db as dbmod
from .util import new_id, now

PIPELINE = ['discovered', 'researched', 'concept_selected', 'scripted', 'storyboarded', 'generating',
            'rendering', 'checking', 'repairing', 'approved', 'upload_started', 'uploaded_private',
            'processing_verified', 'scheduled', 'published']
HOLDS = ['blocked', 'needs_credentials', 'needs_review']
TERMINAL = ['failed', 'cancelled', 'published']
STATES = PIPELINE + HOLDS + ['failed', 'cancelled']

FORWARD = {
    'discovered': {'researched'},
    'researched': {'concept_selected'},
    'concept_selected': {'scripted', 'researched'},
    'scripted': {'storyboarded', 'concept_selected', 'scripted'},
    'storyboarded': {'generating', 'scripted'},
    'generating': {'rendering', 'generating', 'scripted'},
    'rendering': {'checking', 'generating', 'scripted'},
    'checking': {'approved', 'repairing', 'scripted'},
    'repairing': {'generating', 'rendering', 'checking', 'scripted'},
    'approved': {'upload_started', 'scripted', 'checking'},
    'upload_started': {'uploaded_private'},
    'uploaded_private': {'processing_verified'},
    'processing_verified': {'scheduled', 'published'},
    'scheduled': {'published'},
    'published': set(),
}
# States from which the owner may still edit the story (nothing uploaded yet).
EDITABLE = {'discovered', 'researched', 'concept_selected', 'scripted', 'storyboarded', 'generating', 'rendering',
            'checking', 'repairing', 'approved', 'blocked', 'needs_review', 'needs_credentials'}
UPLOADED = {'uploaded_private', 'processing_verified', 'scheduled', 'published'}
IN_PRODUCTION = {'discovered', 'researched', 'concept_selected', 'scripted', 'storyboarded', 'generating',
                 'rendering', 'checking', 'repairing'}


class InvalidTransition(ValueError):
    pass


def _row(r):
    if not r:
        return None
    for k in ('metadata', 'features', 'settings'):
        r[k] = json.loads(r[k] or '{}')
    return r


def get(video_id, d=None):
    d = d or dbmod.get()
    v = _row(d.one('SELECT * FROM videos WHERE id=?', (video_id,)))
    if not v:
        raise ValueError('Video not found')
    return v


def create(title, origin, status='discovered', metadata=None, settings=None, legacy_project_id=None, d=None,
           actor='system'):
    d = d or dbmod.get()
    vid = new_id('v_')
    t = now()
    d.execute('''INSERT INTO videos(id, title, status, origin, metadata, settings, legacy_project_id, created_at,
                 updated_at) VALUES (?,?,?,?,?,?,?,?,?)''',
              (vid, title[:100], status, origin, json.dumps(metadata or {}), json.dumps(settings or {}),
               legacy_project_id, t, t))
    d.execute('INSERT INTO video_events(id, video_id, at, from_status, to_status, actor, note) VALUES (?,?,?,?,?,?,?)',
              (new_id('ev_'), vid, t, None, status, actor, 'created'))
    return vid


def allowed(frm, to):
    if to in ('failed', 'cancelled'):
        return frm not in TERMINAL
    if to in HOLDS:
        return frm not in TERMINAL
    if frm in HOLDS:
        return to in PIPELINE  # resume (checked against resume_status by caller)
    return to in FORWARD.get(frm, set())


def transition(video_id, to, note='', actor='system', expect=None, d=None, **fields):
    """Atomically move a video. ``expect`` guards against concurrent movers."""
    if to not in STATES:
        raise InvalidTransition('Unknown state ' + to)
    d = d or dbmod.get()
    with d.tx():
        v = get(video_id, d)
        frm = v['status']
        if expect is not None and frm not in (expect if isinstance(expect, (list, tuple, set)) else [expect]):
            raise InvalidTransition(f'Video is {frm}, expected {expect}')
        if not allowed(frm, to):
            raise InvalidTransition(f'Cannot move video from {frm} to {to}')
        meta = v['metadata']
        if to in HOLDS and frm not in HOLDS:
            meta['resume_status'] = frm
        if frm in HOLDS and to in PIPELINE:
            meta.pop('resume_status', None)
        sets = {'status': to, 'status_reason': note[:1000], 'updated_at': now(), 'metadata': json.dumps(meta)}
        if to == 'approved':
            sets['approved_at'] = now()
        for k, val in fields.items():
            if k not in ('manifest_id', 'render_id', 'qa_report_id', 'slot_id', 'youtube_video_id', 'youtube_url',
                         'concept_id', 'title'):
                raise ValueError('Field not settable via transition: ' + k)
            sets[k] = val
        cols = ', '.join(f'{k}=?' for k in sets)
        d.execute(f'UPDATE videos SET {cols} WHERE id=?', (*sets.values(), video_id))
        d.execute('INSERT INTO video_events(id, video_id, at, from_status, to_status, actor, note) '
                  'VALUES (?,?,?,?,?,?,?)', (new_id('ev_'), video_id, now(), frm, to, actor, note[:1000]))
    return to


def hold(video_id, state, reason, actor='system', d=None):
    if state not in HOLDS and state not in ('failed',):
        raise ValueError('Not a hold state')
    d = d or dbmod.get()
    v = get(video_id, d)
    if v['status'] in TERMINAL:
        return v['status']
    if v['status'] == state:
        update(video_id, d=d, status_reason=reason[:1000])
        return state
    return transition(video_id, state, reason, actor, d=d)


def resume(video_id, actor='owner', d=None):
    d = d or dbmod.get()
    v = get(video_id, d)
    if v['status'] not in HOLDS:
        raise InvalidTransition('Video is not on hold')
    target = v['metadata'].get('resume_status') or 'scripted'
    return transition(video_id, target, 'resumed', actor, d=d)


def update(video_id, d=None, **fields):
    d = d or dbmod.get()
    allowed_cols = {'title', 'status_reason', 'concept_id', 'manifest_id', 'render_id', 'qa_report_id', 'slot_id',
                    'youtube_video_id', 'youtube_url', 'metadata', 'features', 'settings'}
    sets = {}
    for k, v in fields.items():
        if k not in allowed_cols:
            raise ValueError('Unknown video field ' + k)
        sets[k] = json.dumps(v) if k in ('metadata', 'features', 'settings') else v
    sets['updated_at'] = now()
    cols = ', '.join(f'{k}=?' for k in sets)
    d.execute(f'UPDATE videos SET {cols} WHERE id=?', (*sets.values(), video_id))


def merge_metadata(video_id, patch, d=None):
    d = d or dbmod.get()
    with d.tx():
        v = get(video_id, d)
        m = v['metadata']
        m.update(patch)
        update(video_id, d=d, metadata=m)
        return m


def events(video_id, d=None):
    d = d or dbmod.get()
    return d.query('SELECT * FROM video_events WHERE video_id=? ORDER BY at', (video_id,))


def listing(status=None, limit=200, d=None):
    d = d or dbmod.get()
    if status:
        rows = d.query('SELECT * FROM videos WHERE status=? ORDER BY updated_at DESC LIMIT ?', (status, limit))
    else:
        rows = d.query('SELECT * FROM videos ORDER BY updated_at DESC LIMIT ?', (limit,))
    return [_row(r) for r in rows]


def count(statuses, d=None):
    d = d or dbmod.get()
    statuses = list(statuses)
    return int(d.scalar('SELECT COUNT(*) AS n FROM videos WHERE status IN (%s)' % ','.join('?' * len(statuses)),
                        statuses) or 0)
