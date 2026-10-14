"""Authentication, CSRF, rate limiting and response headers."""
import hashlib
import hmac
import os
import secrets
import threading
import time

from flask import jsonify, redirect, request, session
from werkzeug.security import check_password_hash

_attempts = {}
_lock = threading.Lock()
WINDOW = 300
PER_IP = 8
GLOBAL = 40


def password_configured():
    h = os.environ.get('ADMIN_PASSWORD_HASH', '')
    p = os.environ.get('ADMIN_PASSWORD', '')
    return bool(h) or (bool(p) and not p.startswith('replace-'))


def check_password(candidate):
    # Compose env files need "$" written as "$$"; a real hash never contains "$$".
    h = os.environ.get('ADMIN_PASSWORD_HASH', '').replace('$$', '$')
    if h:
        try:
            return check_password_hash(h, candidate)
        except (ValueError, TypeError):
            return False
    configured = os.environ.get('ADMIN_PASSWORD', '')
    # Compare fixed-length digests so any characters (including non-ASCII) work
    # and timing does not depend on the password.
    a = hashlib.sha256(candidate.encode('utf-8', 'surrogatepass')).digest()
    b = hashlib.sha256(configured.encode('utf-8', 'surrogatepass')).digest()
    return hmac.compare_digest(a, b) and bool(configured)


def rate_limited(ip):
    """Per-IP and global sliding windows. Global limit slows guessing from many
    addresses without letting one attacker lock the owner out for long."""
    t = time.time()
    with _lock:
        for k in list(_attempts):
            _attempts[k] = [x for x in _attempts[k] if x > t - WINDOW]
            if not _attempts[k]:
                del _attempts[k]
        mine = _attempts.get(ip, [])
        total = sum(len(v) for v in _attempts.values())
        if len(mine) >= PER_IP or total >= GLOBAL:
            return True
        _attempts.setdefault(ip, []).append(t)
        return False


def reset_attempts(ip):
    with _lock:
        _attempts.pop(ip, None)


PUBLIC_PATHS = ('/login', '/healthz', '/static/')


def protect():
    path = request.path
    if path == '/login' or path == '/healthz' or path.startswith('/static/'):
        return None
    if not session.get('owner'):
        if path.startswith('/api/') or path.startswith('/media/'):
            return jsonify(error='Sign in first'), 401
        return redirect('/login')
    if session.get('exp', 0) < time.time():
        session.clear()
        return (jsonify(error='Session expired'), 401) if path.startswith('/api/') else redirect('/login')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if not hmac.compare_digest(request.headers.get('X-CSRF', ''), session.get('csrf', '!')):
            return jsonify(error='Refresh the page and try again'), 403
        origin = request.headers.get('Origin')
        if origin and origin.rstrip('/') != request.host_url.rstrip('/') and \
                origin.rstrip('/') != os.environ.get('PUBLIC_URL', '').rstrip('/'):
            return jsonify(error='Cross-site request blocked'), 403
    return None


def start_session():
    session.clear()
    session['owner'] = True
    session['csrf'] = secrets.token_hex(24)
    session['exp'] = time.time() + 12 * 3600
    session.permanent = True


def headers(r):
    r.headers['X-Content-Type-Options'] = 'nosniff'
    r.headers['X-Frame-Options'] = 'DENY'
    r.headers['Referrer-Policy'] = 'same-origin'
    r.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    r.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    r.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    if request.path.startswith('/api/') or request.path in ('/', '/login'):
        r.headers['Cache-Control'] = 'no-store'
    return r
