# Deployment

## Components

| Service (compose.yaml) | Command | Role |
|---|---|---|
| `db` | `postgres:16` | All state except media. Health-checked with `pg_isready`. |
| `migrate` | `python -m blox.cli migrate` | Runs forward-only migrations once, then exits. Everything else waits for it. |
| `web` | `gunicorn app:app` | Studio UI and JSON API on port 8000 (bound to 127.0.0.1). Health: `GET /healthz`. |
| `worker-orchestrate` | `worker.py --roles orchestrate` | Slot calendar, buffer, assignment and skips (leader-locked), story development, repair planning. |
| `worker-research` | `worker.py --roles research,analytics` | YouTube Data API research, snapshots, reference analysis, YouTube Analytics. |
| `worker-render` | `worker.py --roles render` | Voices, Blender shots, assembly. CPU/GPU heavy; scale this one. |
| `worker-qa` | `worker.py --roles qa` | QA on rendered output. |
| `worker-publish` | `worker.py --roles publish` | Uploads and publication verification, one upload at a time. |

Workers are interchangeable and safe to run concurrently (database leases). Their health check
(`python -m blox.cli healthcheck --worker`) fails when the container's heartbeat is older than 90 s.
A single machine can also run one worker with `--roles all`; the scheduler then runs in its own thread
so long renders do not delay slot handling.

All containers run as uid 10001 under `tini`. Media, renders and the vault key live in `./data`.

## First start

```bash
python3 setup.py                         # creates .env (password hash, secrets) - never commit it
mkdir -p data && sudo chown -R 10001:10001 data
docker compose up --build -d
docker compose ps                        # all services should become "healthy"
```

`.env.example` documents every variable. Required: `ADMIN_PASSWORD_HASH` (or `ADMIN_PASSWORD`),
`SESSION_SECRET`, `POSTGRES_PASSWORD`. Docker Compose treats `$` in env files as interpolation, so the
password hash is stored with `$$`; Blox accepts either form.

## HTTPS and a public address

Keep port 8000 on localhost and put a TLS-terminating reverse proxy in front, for example Caddy:

```
studio.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Then set in `.env` and restart:

```
PUBLIC_URL=https://studio.example.com
COOKIE_SECURE=1
TRUSTED_PROXIES=1      # number of proxies that append X-Forwarded-For
```

`TRUSTED_PROXIES` makes login rate limiting use the real client address. Do not set it unless a proxy
you control is in front, otherwise clients could spoof their address. Consider an additional access
control layer (VPN, IP allowlist or SSO at the proxy): the studio has a single owner password.

## Google Cloud setup for YouTube

1. Create a Google Cloud project (or reuse one).
2. *APIs & Services → Library*: enable **YouTube Data API v3** and (for analytics) **YouTube Analytics API**.
3. *Credentials → Create credentials → API key*. Restrict it to the YouTube Data API v3. Paste it on
   Connections as the YouTube Data API key (used for research only).
4. *OAuth consent screen*: user type *External* (or *Internal* for Workspace), add your Google account as
   a test user while the app is in testing.
5. *Credentials → Create credentials → OAuth client ID → Web application*. Authorised redirect URI:
   exactly the value shown on Connections, i.e. `<PUBLIC_URL>/oauth/callback`.
6. Paste the client ID and secret on Connections, click **Connect YouTube**, pick the channel, then
   **confirm** the channel in the studio. Blox requests `youtube.upload`, `youtube.readonly` and,
   optionally, `yt-analytics.readonly` with offline access; it cannot delete or edit existing videos.
7. **Audit.** Videos uploaded through an API project that has not completed YouTube's API compliance
   audit are restricted to private viewing. To publish on a schedule, apply for the audit from the
   YouTube API Services pages. Until then, Blox uploads remain private and the studio reports that
   YouTube did not keep the scheduled time. Check Google's current documentation; these rules change.
8. **Quota.** Uploads, searches and other calls have daily quotas per project (visible in Cloud Console).
   Enter your project's figures on Settings → Research → quota if they differ from the defaults.

While the consent screen is in *testing*, Google may expire refresh tokens after about a week; Blox
then shows "Reconnect YouTube" and pauses publishing instead of failing silently.

## Upgrading from the previous release

The previous Blox Studio stored everything in `data/` (`studio.db`, `files/`, `vault.key`). Keep that
folder: the vault key decrypts your saved credentials.

**Option A: stay on SQLite** (simplest, single machine). In `.env` set
`DATABASE_URL=sqlite:////app/data/studio.db` and start the stack; the `migrate` service upgrades the
old database in place (see [AUDIT.md](AUDIT.md#upgrade-path)). Run one worker with `--roles all` or
keep the role workers (SQLite with WAL handles a few concurrent workers on one host).

**Option B: move to PostgreSQL** (recommended for the role-separated workers):

```bash
docker compose up -d db
# Upgrades the old SQLite file to the current schema, then copies every table into PostgreSQL.
docker compose run --rm migrate sh -c \
    'python -m blox.cli copy-to-postgres --from /app/data/studio.db --to "$DATABASE_URL"'
docker compose up -d
```

`copy-to-postgres` refuses to write into a database that already holds studio data. The SQLite file is
kept (upgraded in place) so you can go back.

After upgrading, autopilot is **off**. Review Settings (schedule, budget, audience, disclosure) and
enable it again.

## Running without Docker

Ubuntu 24.04 packages: `python3-venv ffmpeg fonts-dejavu-core blender libegl1 libegl-mesa0
libgl1-mesa-dri libgles2`. Then `pip install -r requirements.lock`, set the environment variables from
`.env.example`, and run `python -m blox.cli migrate`, `gunicorn app:app` and `python worker.py --roles all`
under a process supervisor (systemd units restarting on failure).

## GPU rendering

EEVEE renders through EGL. Without a GPU it falls back to software rendering (slow but correct). To
use an NVIDIA GPU in Docker, install the NVIDIA Container Toolkit and add to `worker-render`:

```yaml
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, count: 1, capabilities: [gpu, graphics]}]
```

and set `NVIDIA_DRIVER_CAPABILITIES=all`. Verify with a single shot render before relying on it.
