"""Generate local configuration without collecting any provider credentials."""
from pathlib import Path
import secrets, getpass

path=Path(__file__).parent/'.env'
if path.exists(): raise SystemExit('.env already exists. Edit it directly; setup will not overwrite it.')
password=getpass.getpass('Choose a studio password (at least 12 characters): ')
if len(password)<12 or any(c in password for c in '\r\n#\"\' $'):
    raise SystemExit('Use at least 12 characters, without spaces, quotes, # or $.')
path.write_text('SESSION_SECRET='+secrets.token_urlsafe(48)+'\nADMIN_PASSWORD='+password+'\nPUBLIC_URL=http://localhost:8000\nCOOKIE_SECURE=0\n')
path.chmod(0o600)
print('Configuration saved. Run: docker compose up --build -d')
print('Then open http://localhost:8000 and sign in with the password you chose.')
