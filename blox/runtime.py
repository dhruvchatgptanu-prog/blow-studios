"""Process start-up: directories, vault key, migrations, legacy preference import."""
import json
import threading

from . import config, db as dbmod, logs, migrations, prefs as prefsmod, store, vault

_done = False
_lock = threading.Lock()


def import_legacy_preferences(d):
    """Map the original release's ``preferences`` row onto the new prefs once.

    Autopilot is deliberately *not* re-enabled: after upgrading, the owner must
    review the new cadence and budget and activate it again.
    """
    if store.get('legacy_prefs_imported', False, d):
        return False
    row = d.one("SELECT value FROM settings WHERE key='preferences'")
    if not row:
        store.put('legacy_prefs_imported', True, d)
        return False
    old = json.loads(row['value'])
    p = prefsmod.get(d)
    if isinstance(old.get('audience'), bool):
        p['publishing']['made_for_kids'] = old['audience']
    if isinstance(old.get('synthetic'), bool):
        p['publishing']['synthetic_disclosure'] = old['synthetic']
    if isinstance(old.get('timezone'), str):
        p['schedule']['timezone'] = old['timezone']
    if isinstance(old.get('text_model'), str):
        p['production']['text_model'] = old['text_model']
    for src, dst in (('daily_estimate_cap', 'daily_usd'), ('estimate_per_video', 'per_video_usd')):
        if isinstance(old.get(src), (int, float)) and old[src] > 0:
            p['budget'][dst] = float(old[src])
    if old.get('mode') in ('review', 'autopilot'):
        p['autopilot']['mode'] = old['mode']
    p['autopilot']['enabled'] = False
    try:
        prefsmod.put(p, d)
    except ValueError:
        pass
    store.put('legacy_prefs_imported', True, d)
    store.audit('legacy_preferences_imported', {'autopilot_reenabled': False}, d=d)
    return True


def init():
    global _done
    with _lock:
        if _done:
            return dbmod.get()
        logs.setup()
        config.ensure_dirs()
        vault.ensure_key()
        d = dbmod.get()
        migrations.migrate(d)
        import_legacy_preferences(d)
        _done = True
        return d


def reset_for_tests():
    """Forget initialisation and the cached database handle (tests, and CLI commands that switch databases)."""
    global _done
    _done = False
    dbmod.reset()
