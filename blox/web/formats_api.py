"""Story format endpoints: the first-person story calibration.

The owner measures his own channel on his own computer and uploads calibration.json and/or pace_profile.json.
The files are untrusted data: size-capped, parsed as JSON only, every value checked for type and range
(blox.story.formats), nothing else kept. Preview shows the parsed values next to the defaults without saving;
saving and resetting are audited.
"""
import json

from flask import jsonify, request

from .. import db as dbmod, store, untrusted
from ..story import formats as F

UPLOAD_CAP = 512 * 1024
FILES = (('calibration', 'calibration.json'), ('pace_profile', 'pace_profile.json'))


def _load(name, label, body):
    f = request.files.get(name)
    if f is not None and f.filename:
        data = f.stream.read(UPLOAD_CAP + 1)
        if len(data) > UPLOAD_CAP:
            raise ValueError(f'{label} is larger than {UPLOAD_CAP // 1024} KB')
        try:
            return json.loads(data.decode('utf-8-sig'))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise ValueError(f'{label} is not valid JSON') from None
    return body.get(name) if isinstance(body, dict) else None


def _parse():
    """Parsed, cleaned values of the uploaded files (nothing stored)."""
    body = {}
    if request.is_json:
        if (request.content_length or 0) > 2 * UPLOAD_CAP:
            raise ValueError('The upload is too large')
        try:
            body = request.get_json(silent=True)
        except RecursionError:
            body = None
        if not isinstance(body, dict):
            raise ValueError('Send the files, or a JSON object with calibration and/or pace_profile')
    channel = request.form.get('channel') or body.get('channel') or None
    if channel is not None and (not isinstance(channel, str) or len(channel) > 100):
        raise ValueError('Unknown channel')
    raw = {name: _load(name, label, body) for name, label in FILES}
    if raw['calibration'] is None and raw['pace_profile'] is None:
        raise ValueError('Choose calibration.json and/or pace_profile.json')
    values, dropped, used = {}, [], []
    channels, needs_channel = [], False
    if raw['calibration'] is not None:
        if not isinstance(raw['calibration'], dict):
            raise ValueError('calibration.json must hold a JSON object')
        channels = F.calibration_channels(raw['calibration'])
        if 'per_channel' in raw['calibration'] and len(channels) > 1 and channel not in channels:
            needs_channel = True
        else:
            values.update(F.clean_calibration(raw['calibration'], channel=channel, dropped=dropped))
            used.append('calibration.json' + (f' ({untrusted.clean(channel, 60)})' if channel else ''))
    if raw['pace_profile'] is not None:
        values.update(F.clean_pace_profile(raw['pace_profile'], dropped=dropped))
        used.append('pace_profile.json')
    return {'parsed': values, 'dropped': [untrusted.clean(x, 160) for x in dropped],
            'channels': channels, 'channel': channel,
            'needs_channel': needs_channel, 'source': ' + '.join(used)}


def _state(d):
    cal = F.calibration(d)
    return {'format': F.FIRST_PERSON, 'active': cal, 'defaults': F.DEFAULTS,
            'imported': cal.get('source') != F.DEFAULTS['source'],
            'vocab': {'formats': list(F.FORMATS), 'modes': list(F.MODES), 'mechanisms': list(F.MECHANISMS),
                      'twist_types': list(F.TWIST_TYPES), 'loop_types': list(F.LOOP_TYPES)}}


def register(app):
    @app.get('/api/format-calibration')
    def format_calibration():
        return jsonify(_state(dbmod.get()))

    @app.post('/api/format-calibration/preview')
    def format_calibration_preview():
        d = dbmod.get()
        res = _parse()
        res['after_save'] = F.merged(res['parsed'], F.calibration(d))
        res['defaults'] = F.DEFAULTS
        return jsonify(res)

    @app.post('/api/format-calibration')
    def format_calibration_save():
        d = dbmod.get()
        res = _parse()
        if res['needs_channel']:
            raise ValueError('Choose which channel of calibration.json to use')
        if not res['parsed']:
            raise ValueError('No usable values in the files; nothing was saved')
        F.save_calibration(res['parsed'], res['source'], d)
        store.audit('format_calibration_imported', {'format': F.FIRST_PERSON, 'keys': sorted(res['parsed']),
                                                    'source': res['source'], 'dropped': len(res['dropped'])},
                    actor='owner', d=d)
        return jsonify(dict(_state(d), saved=sorted(res['parsed']), dropped=res['dropped']))

    @app.delete('/api/format-calibration')
    def format_calibration_reset():
        d = dbmod.get()
        F.reset_calibration(d)
        store.audit('format_calibration_reset', {'format': F.FIRST_PERSON}, actor='owner', d=d)
        return jsonify(_state(d))
