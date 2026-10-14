"""Research task handlers."""
from .. import config, db as dbmod
from ..tasks import handler
from . import analysis, service, transcripts


@handler('research.discover')
def discover(ctx):
    return service.discover()


@handler('research.snapshot')
def snapshot(ctx):
    return service.snapshot()


@handler('research.reference')
def reference(ctx):
    """Transcript + analysis for one reference (YouTube id or owner asset)."""
    d = dbmod.get()
    p = ctx.payload
    if p.get('asset'):
        path = config.MEDIA_DIR / p['asset']
        subject = 'asset:' + p['asset']
        tr = None
        try:
            tr = transcripts.from_asr(subject, path)
        except Exception as e:  # ASR is optional; record why
            transcripts.save(subject, 'asr', 'Speech recognition not run', 'owner_confirmed_upload', [], 'none',
                             status='unavailable', note=str(e)[:300])
        aid = analysis.analyse_media(subject, path, 'owner_confirmed_upload')
        return {'transcript': tr, 'analysis': aid}
    vid = p['video_id']
    tr = transcripts.from_provider(vid, d)
    aid = analysis.metadata_only(vid, d)
    return {'transcript': tr, 'analysis': aid}
