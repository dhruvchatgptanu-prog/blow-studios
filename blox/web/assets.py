"""Owner media library: validated uploads of images, footage and music."""
import os
import secrets
from pathlib import Path

from flask import jsonify, request
from PIL import Image

from .. import config, db as dbmod, media, store
from ..util import now, sha256_file

KINDS = {'.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.webp': 'image', '.mp4': 'video', '.mov': 'video',
         '.webm': 'video', '.mp3': 'audio', '.wav': 'audio', '.m4a': 'audio', '.srt': 'text', '.vtt': 'text',
         '.txt': 'text'}
LIMITS = {'image': 15, 'video': 250, 'audio': 60, 'text': 2}
MAGIC = {'image': [b'\x89PNG', b'\xff\xd8\xff', b'RIFF'], 'audio': [b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2',
                                                                       b'RIFF', b'\x00\x00\x00'],
         'video': [b'\x00\x00\x00', b'\x1aE\xdf\xa3']}


def validate_file(path, kind):
    size = os.path.getsize(path)
    if size > LIMITS[kind] * 1024 * 1024:
        raise ValueError(f'{kind.title()} files are limited to {LIMITS[kind]} MB')
    with open(path, 'rb') as f:
        head = f.read(16)
    if kind in MAGIC and not any(head.startswith(m) or (m == b'\x00\x00\x00' and head[4:8] == b'ftyp') for m in MAGIC[kind]):
        if not (kind == 'audio' and head[4:8] == b'ftyp'):
            raise ValueError('File content does not match its extension')
    if kind == 'image':
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            if im.width * im.height > 40_000_000:
                raise ValueError('Image dimensions are too large')
            im.thumbnail((2048, 2048))
            im.convert('RGB').save(path, 'PNG')
        return {'width': None}
    if kind in ('video', 'audio'):
        info = media.probe(path)
        dur = float(info['format'].get('duration') or 0)
        if dur <= 0 or dur > 3600:
            raise ValueError('Media must be between 0 and 60 minutes long')
        meta = {'duration_s': round(dur, 2)}
        vs = media.video_stream(info)
        if kind == 'video':
            if not vs:
                raise ValueError('No video stream found')
            meta.update(width=int(vs['width']), height=int(vs['height']))
        elif not media.audio_stream(info):
            raise ValueError('No audio stream found')
        return meta
    with open(path, 'rb') as f:
        f.read().decode('utf-8')
    return {}


def register(app):
    @app.get('/api/assets')
    def list_assets():
        d = dbmod.get()
        return jsonify(assets=d.query('SELECT id, name, kind, rights, created_at, bytes, meta FROM assets '
                                      'ORDER BY COALESCE(created_at, 0) DESC'))

    @app.post('/api/assets')
    def upload_asset():
        if request.form.get('rights') != 'yes':
            raise ValueError('Confirm that you own this file or have permission to use it')
        purpose = request.form.get('purpose', 'production')
        f = request.files.get('file')
        if not f or not f.filename:
            raise ValueError('Choose a file')
        ext = Path(f.filename).suffix.lower()
        kind = KINDS.get(ext)
        if not kind:
            raise ValueError('Unsupported file type')
        aid = secrets.token_hex(16) + ('.png' if kind == 'image' else ext)
        path = config.MEDIA_DIR / aid
        f.save(path)
        try:
            meta = validate_file(path, kind)
        except Exception as e:
            path.unlink(missing_ok=True)
            raise ValueError('File could not be validated: ' + str(e)[:200])
        digest = sha256_file(path)
        d = dbmod.get()
        dup = d.one('SELECT id FROM assets WHERE sha256=?', (digest,))
        if dup:
            path.unlink(missing_ok=True)
            return jsonify(id=dup['id'], duplicate=True)
        import json as _json
        d.execute('INSERT INTO assets(id, name, kind, rights, created_at, bytes, sha256, meta) VALUES (?,?,?,?,?,?,?,?)',
                  (aid, f.filename[:200], kind, 'owner_confirmed:' + purpose[:40], now(), os.path.getsize(path), digest,
                   _json.dumps(meta)))
        store.audit('asset_uploaded', {'id': aid, 'kind': kind, 'purpose': purpose}, actor='owner')
        return jsonify(id=aid, kind=kind, meta=meta)
