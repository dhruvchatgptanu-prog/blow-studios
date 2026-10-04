"""Create a local .env without collecting any provider credentials.

    python setup.py

Writes random SESSION_SECRET and POSTGRES_PASSWORD values and a scrypt hash of the studio password.
Provider keys are entered later on the Connections page (stored encrypted).
"""
import getpass
import secrets
from pathlib import Path

from werkzeug.security import generate_password_hash

path = Path(__file__).parent / '.env'
if path.exists():
    raise SystemExit('.env already exists. Edit it directly; setup will not overwrite it.')
password = getpass.getpass('Choose a studio password (at least 12 characters): ')
if len(password) < 12:
    raise SystemExit('Use at least 12 characters.')
if password != getpass.getpass('Repeat it: '):
    raise SystemExit('The passwords do not match.')
lines = [
    'ADMIN_PASSWORD_HASH=' + generate_password_hash(password, method='scrypt').replace('$', '$$'),
    'SESSION_SECRET=' + secrets.token_urlsafe(48),
    'POSTGRES_PASSWORD=' + secrets.token_urlsafe(24),
    'PUBLIC_URL=http://localhost:8000',
    'COOKIE_SECURE=0',
    'TRUSTED_PROXIES=0',
]
path.write_text('\n'.join(lines) + '\n')
path.chmod(0o600)
print('Saved .env. Next: docker compose up --build -d, then open http://localhost:8000')
