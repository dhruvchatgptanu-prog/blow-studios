"""Test helpers that put the studio into specific, explicit states (no network)."""
import os

from blox import config, prefs, repo, store, vault, videos


def connect_youtube(d, confirmed=True):
    vault.put('GOOGLE_CLIENT_ID', 'test-client.apps.googleusercontent.com', d)
    vault.put('GOOGLE_CLIENT_SECRET', 'test-secret', d)
    vault.put('YOUTUBE_REFRESH_TOKEN', '1//test-refresh', d)
    store.put('channel', {'id': 'UC_test', 'title': 'Test channel', 'uploads_playlist': 'UU_test',
                          'confirmed': confirmed}, d)
    store.put('youtube_scopes', ['https://www.googleapis.com/auth/youtube.upload',
                                 'https://www.googleapis.com/auth/youtube.readonly'], d)


def ready_to_publish(d, mode='autopilot'):
    connect_youtube(d)
    p = prefs.get(d)
    p['publishing'].update(made_for_kids=False, synthetic_disclosure=True)
    p['autopilot'].update(enabled=True, mode=mode, paused=False, emergency_stop=False)
    prefs.put(p, d)
    return p


def approved_video(d, size=1000, owner_approved=False, title='Test short'):
    vid = videos.create(title, 'manual', status='approved', d=d,
                        metadata={'publish_metadata': {'title': title, 'description': 'An original animated story.',
                                                       'tags': ['story']}, 'owner_approved': owner_approved})
    rel = f'work/{vid}/final.mp4'
    path = os.path.join(config.DATA_DIR, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(bytes(range(256)) * (size // 256) + bytes(size % 256))
    repo.save_render(vid, 'mf_test', 'done', file=rel, d=d)
    return vid


def run_one(kind_role, worker='w1'):
    """Claim the next task for a role and run it through the real worker outcome mapping."""
    from blox import jobs
    from blox.tasks import ensure_loaded
    from blox.worker import run_task
    ensure_loaded()
    t = jobs.claim(worker, [kind_role])
    if not t:
        return None, None
    return t, run_task(t, worker)
