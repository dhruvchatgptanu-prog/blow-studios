"""Process configuration from environment variables.

Only deployment-level values live here. Studio preferences (budgets, cadence,
voices, ...) are stored in the database and edited from the UI; see
``blox.prefs``.
"""
import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

# BLOX_DATA is kept from the original release so existing volumes keep working.
DATA_DIR = Path(os.environ.get('BLOX_DATA', APP_ROOT / 'data')).resolve()
# Original release stored uploaded assets and renders in data/files; keep it.
MEDIA_DIR = DATA_DIR / 'files'
WORK_DIR = DATA_DIR / 'work'
BACKUP_DIR = DATA_DIR / 'backups'


def database_url():
    return os.environ.get('DATABASE_URL') or 'sqlite:///' + str(DATA_DIR / 'studio.db')


def public_url():
    return os.environ.get('PUBLIC_URL', 'http://localhost:8000').rstrip('/')


def env_flag(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ('1', 'true', 'yes', 'on')


BLENDER_BIN = os.environ.get('BLENDER_BIN', 'blender')
FFMPEG_BIN = os.environ.get('FFMPEG_BIN', 'ffmpeg')
FFPROBE_BIN = os.environ.get('FFPROBE_BIN', 'ffprobe')

# Upper bounds for any single media process, so a hostile or corrupt file
# cannot pin a worker forever.
MEDIA_TIMEOUT_S = int(os.environ.get('BLOX_MEDIA_TIMEOUT_S', '1800'))
BLENDER_TIMEOUT_S = int(os.environ.get('BLOX_BLENDER_TIMEOUT_S', '5400'))


def ensure_dirs():
    for d in (DATA_DIR, MEDIA_DIR, WORK_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass
