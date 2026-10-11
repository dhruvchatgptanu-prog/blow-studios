"""Channel kit endpoints: read the current pack, generate it (text only, or with Blender-rendered images)."""
from flask import jsonify, request

from .. import channel_kit, db as dbmod
from ..animation import blender as BL


def _view(kit):
    if not kit:
        return {'kit': None, 'blender': BL.available()}
    files = kit.get('files') or {}
    return {'kit': kit, 'blender': BL.available(),
            'downloads': {k: '/media/' + v for k, v in files.items()}}


def register(app):
    @app.get('/api/channel-kit')
    def channel_kit_view():
        return jsonify(**_view(channel_kit.load()))

    @app.post('/api/channel-kit/generate')
    def channel_kit_generate():
        body = request.get_json(silent=True) or {}
        images = body.get('images') is True
        name = body.get('name')
        if name is not None:
            if not isinstance(name, str) or not 3 <= len(name.strip()) <= 40:
                raise ValueError('Channel name must be 3 to 40 characters')
            name = name.strip()
            if 'roblox' in name.lower():
                raise ValueError('The channel name must not contain another company\'s brand')
        if images and not BL.available():
            raise ValueError('Blender is not installed; generate the text pack without images or install Blender')
        kit = channel_kit.generate(images=images, name=name or None, d=dbmod.get())
        return jsonify(**_view(kit))
