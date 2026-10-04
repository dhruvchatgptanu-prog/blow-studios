"""Encrypted secret storage.

Compatible with the original release: secrets are stored in the ``settings``
table under ``secret:<NAME>`` and encrypted with the Fernet key in
``data/vault.key``. ``BLOX_VAULT_KEY`` may supply the key instead, so the key can
live outside the data volume. Back up the key separately from database dumps.
"""
import json
import os

from cryptography.fernet import Fernet, InvalidToken

from . import config, db as dbmod

# Every credential the studio understands. The UI and the readiness checks use
# this list; values never leave the server.
KNOWN = {
    'OPENAI_API_KEY': 'OpenAI API key (scripts, vision review, TTS, transcription)',
    'RUNWAYML_API_SECRET': 'Runway API key (optional generative shots)',
    'ELEVENLABS_API_KEY': 'ElevenLabs API key (optional TTS with timestamps)',
    'YOUTUBE_API_KEY': 'YouTube Data API key (research of public videos)',
    'GOOGLE_CLIENT_ID': 'Google OAuth client ID',
    'GOOGLE_CLIENT_SECRET': 'Google OAuth client secret',
    'TRANSCRIPT_PROVIDER_URL': 'Authorized transcript provider endpoint (optional)',
    'TRANSCRIPT_PROVIDER_KEY': 'Authorized transcript provider key (optional)',
}
INTERNAL = {'YOUTUBE_REFRESH_TOKEN', 'YOUTUBE_ACCESS_TOKEN'}


def _key_path():
    return config.DATA_DIR / 'vault.key'


def ensure_key():
    if os.environ.get('BLOX_VAULT_KEY'):
        return
    config.ensure_dirs()
    try:
        fd = os.open(_key_path(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(Fernet.generate_key())
    except FileExistsError:
        pass


def _fernet():
    k = os.environ.get('BLOX_VAULT_KEY')
    if k:
        return Fernet(k.encode())
    return Fernet(_key_path().read_bytes())


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise ValueError('Stored secret cannot be decrypted with the current vault key')


def get(name, d=None):
    d = d or dbmod.get()
    row = d.one('SELECT value FROM settings WHERE key=?', ('secret:' + name,))
    if row:
        val = json.loads(row['value'])
        if val:
            return decrypt(val)
        return ''
    if name in INTERNAL:
        return ''
    return os.environ.get(name, '')


def put(name, value, d=None):
    d = d or dbmod.get()
    stored = encrypt(value) if value else ''
    d.execute('INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              ('secret:' + name, json.dumps(stored)))


def configured(name):
    try:
        return bool(get(name))
    except ValueError:
        return False


def status():
    return {k: configured(k) for k in KNOWN}


def redact(text):
    """Remove any known secret values from text before logging or showing it."""
    if not text:
        return text
    out = str(text)
    for name in list(KNOWN) + list(INTERNAL):
        try:
            v = get(name)
        except Exception:
            continue
        if v and len(v) >= 6:
            out = out.replace(v, '[redacted]')
    return out

