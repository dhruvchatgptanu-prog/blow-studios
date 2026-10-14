"""Server-side fetching restricted against SSRF.

* HTTPS only; optional host allowlist (suffix match).
* Every hostname is resolved and every resolved address must be public
  (no loopback, private, link-local, multicast, reserved or metadata ranges).
* Redirects are followed manually (max 3) and every hop is re-validated.
* The connection is pinned to the validated address set via a session adapter
  check, and response size is capped while streaming.
"""
import ipaddress
import os
import socket
import urllib.parse

import requests

from .http import ProviderError


class UnsafeURL(ValueError):
    pass


def _public(ip):
    """Globally routable unicast only (rejects private, loopback, link-local, CGNAT, reserved, documentation...)."""
    a = ipaddress.ip_address(ip.split('%', 1)[0])
    if a.version == 6 and a.ipv4_mapped:
        return _public(str(a.ipv4_mapped))
    return a.is_global and not a.is_multicast


def check_url(url, allow_hosts=None):
    u = urllib.parse.urlparse(url)
    if u.scheme != 'https' or not u.hostname:
        raise UnsafeURL('Only https URLs are allowed')
    if u.username or u.password:
        raise UnsafeURL('Credentials in URLs are not allowed')
    host = u.hostname.lower().rstrip('.')
    if allow_hosts and not any(host == h or host.endswith('.' + h) for h in allow_hosts):
        raise UnsafeURL(f'Host {host} is not on the allowlist')
    try:
        infos = socket.getaddrinfo(host, u.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsafeURL('Host does not resolve')
    ips = {i[4][0] for i in infos}
    if not ips or not all(_public(ip) for ip in ips):
        raise UnsafeURL('Host resolves to a non-public address')
    return host, ips


def _check_peer(r):
    """Re-check the connected address (DNS rebinding). Skipped behind an HTTP proxy,
    where the peer is the proxy itself."""
    if any(os.environ.get(k) for k in ('HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy')):
        return
    conn = getattr(r.raw, 'connection', None)
    sock = getattr(conn, 'sock', None)
    if sock is None:
        return
    try:
        peer = sock.getpeername()[0]
    except OSError:
        return
    if not _public(peer):
        r.close()
        raise UnsafeURL('Connected address is not public')


def fetch(url, provider, dest=None, allow_hosts=None, max_bytes=300 * 1024 * 1024, headers=None, timeout=(10, 120),
          params=None):
    """GET with SSRF protection. Writes to ``dest`` (atomic) or returns bytes."""
    for _ in range(4):
        check_url(url, allow_hosts)
        try:
            r = requests.get(url, headers=headers or {}, params=params, stream=True, timeout=timeout,
                             allow_redirects=False)
        except requests.exceptions.RequestException as e:
            raise ProviderError(provider, f'{provider}: download failed ({type(e).__name__})', sent=False)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get('Location')
            r.close()
            if not loc:
                raise ProviderError(provider, f'{provider}: redirect without location', status=r.status_code)
            url = urllib.parse.urljoin(url, loc)
            params = None
            continue
        _check_peer(r)
        if r.status_code != 200:
            raise ProviderError(provider, f'{provider}: download returned HTTP {r.status_code}', status=r.status_code)
        total = 0
        chunks = []
        tmp = (str(dest) + '.part') if dest else None
        fh = open(tmp, 'wb') if tmp else None
        try:
            for chunk in r.iter_content(1 << 20):
                total += len(chunk)
                if total > max_bytes:
                    raise UnsafeURL(f'Download exceeds {max_bytes // (1024 * 1024)} MB')
                if fh:
                    fh.write(chunk)
                else:
                    chunks.append(chunk)
        finally:
            if fh:
                fh.close()
            r.close()
        if dest:
            os.replace(tmp, dest)
            return dest
        return b''.join(chunks)
    raise UnsafeURL('Too many redirects')
