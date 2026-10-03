# Operations

## Daily check (two minutes)

1. **Dashboard**: autopilot state, next slot (Adelaide time), approved buffer vs target, budget today
   (reserved / measured / estimated), *Why publishing would not happen now*, alerts, workers.
2. **Publishing calendar**: every slot with its status (open, assigned, uploaded, scheduled, published,
   skipped, failed) and the reason for skips and failures. DST gaps are listed.
3. **Alerts** link to videos that need you (see below).

## Videos that need you

| State | Typical cause | What to do |
|---|---|---|
| `needs_review` | QA uncertain on an important check; an ambiguous paid request; YouTube dropped the scheduled time; a repair that needs a script change; validation errors in an edited plan | Open the video. Quality control shows the checks with time codes. For paid requests, see below. Fix and **Resume**, or **Cancel**. |
| `needs_credentials` | A key was rejected, the YouTube authorisation expired or was revoked | Connections: re-enter the key or **Reconnect YouTube**, then **Resume** the video. |
| `blocked` | QA found a critical defect, repair limits reached, budget cap reached, processing failed on YouTube | Read the reason. Regenerate a shot / re-voice a line, raise a limit, or cancel. A blocked video is never published automatically. |

**Ambiguous paid requests.** When a paid request may have reached the provider but no answer came back
(timeout, dropped connection), Blox does not retry it, so you are never charged twice. The video
overview lists *Paid requests awaiting reconciliation*. Check the provider's dashboard or usage page:

* not there → **Not submitted (retry)**: the reservation is released and the step runs again;
* charged → **Charged — discard**: the cost is kept and the step continues under a new request.

## Story backlog

With no LLM API key (or with Settings → Production → story source set to *backlog*), every new video
takes the oldest *ready* story from **Story backlog**. The page and the dashboard show how many stories
are left and how many days they last at your cadence.

* **Add stories:** paste a JSON list of plans (format: the Director's editor *Plan JSON*) and press
  *Validate and add*, or run `python -m blox.cli import-stories file.json --source claude`. Invalid
  plans are rejected with the validator's reasons; exact duplicates are skipped.
* **Get more without API costs:** ask Claude in a normal chat with the prompt in `stories/README.md`.
* **Reject** a story you do not want; **Restore** brings it back. A story whose video is cancelled
  before upload returns to the backlog automatically.
* At production time each story is screened for originality against research references and your
  recent videos; a near-duplicate is held for review instead of produced.
* When the backlog is empty, autopilot starts no new videos and the dashboard says why. Slots without a
  ready video are skipped with the reason.

## Voices

The default voice engine is Piper (free, offline). The Docker image includes the LibriTTS model; on
other installs run `python -m blox.cli install-voice` (downloads from the Piper releases on GitHub
and verifies the checksum). Choose each character's speaker number on **Characters** and listen with
*Hear free voice*. Every video that uses it credits the voice model's licence (CC BY 4.0) in its
description. To use OpenAI or ElevenLabs voices instead, connect the key and change Settings →
Production → voice provider.

## Autopilot controls

* **Pause** (dashboard): no new paid generation or uploads; running renders stop at a safe point;
  research and publication verification continue. Slots passing while paused are skipped with a reason.
* **Emergency stop** (sidebar): also cancels queued paid/publishing tasks and kills running renders.
  Clearing it leaves autopilot paused; resume deliberately.
* **Global circuit breaker**: 25 provider failures within an hour pause autopilot automatically with a
  reason on the dashboard. Per-provider breakers back off (1 → 5 → 15 → 60 minutes) and allow a single
  trial request before closing.

Videos already scheduled on YouTube stay scheduled when you pause; to stop one, change it in YouTube
Studio (Blox cannot edit or delete videos with the scopes it requests).

## Queue

**Work queue** lists tasks with attempts and their last note. *Dead* tasks exhausted their retries and
held their video; fix the cause, then **Requeue**. Waiting is normal for Runway jobs and YouTube
processing (the task re-checks on a timer without counting as a failure).

## Quota

YouTube Data API quota is tracked in three buckets (shared units, search calls, upload calls) per
Pacific-time day. Research keeps a reserve of shared units for publishing and verification. When
YouTube itself reports `quotaExceeded`, the bucket is marked exhausted until midnight Pacific. Edit
the limits on Settings → Research if your project has different quotas.

## Backups

```bash
docker compose exec web python -m blox.cli backup               # data/backups/blox-backup-<time>.tar.gz
docker compose exec db pg_dump -U blox -Fc blox > blox-$(date +%F).dump   # PostgreSQL database
```

The archive holds the SQLite database (when used), uploaded files and `data/work` (shots, voices, renders;
frame folders excluded). The **vault key** (`data/vault.key`, or `BLOX_VAULT_KEY`) is excluded unless
you pass `--include-key`; store it separately (password manager). Without it, saved credentials cannot be
decrypted (you can re-enter them).

Restore:

```bash
python -m blox.cli restore blox-backup-<time>.tar.gz --force    # current SQLite file is moved aside, not deleted
docker compose exec -T db pg_restore -U blox -d blox --clean < blox-<date>.dump   # PostgreSQL
```

Restore never overwrites existing files and rejects archives with unsafe paths.

## Secrets and passwords

* Change the studio password: `python -m blox.cli hash-password`, put the result in
  `ADMIN_PASSWORD_HASH` (write each `$` as `$$` in a Compose `.env`), restart `web`.
* Rotating `SESSION_SECRET` signs everyone out.
* Changing `BLOX_VAULT_KEY` makes saved credentials unreadable; Connections then shows them as not set
  and you re-enter them.
* Logs are JSON on stderr (`docker compose logs -f worker-render`); bearer tokens, API keys, OAuth
  tokens and query-string secrets are scrubbed.

## Updating

```bash
git pull
docker compose up --build -d     # the migrate service applies new migrations before web and workers start
```

Migrations are forward-only; take a backup first.

## Capacity planning

Production must stay ahead of the schedule: at 12 slots per day, about one video every two hours has to
pass QA. The bottleneck is rendering.

Measured on the development machine (4 vCPU, no GPU, EEVEE through software EGL):

| Resolution | Seconds per frame | 30-second video (900 frames) |
|---|---|---|
| 540×960 | about 3.8 | about 57 minutes |
| 1080×1920 | RENDER_1080_SPF | RENDER_1080_VIDEO |

Plan for repairs (a re-rendered shot costs its share again) and for the voice, assembly and QA steps
(minutes each). With these figures, 12 full-resolution videos a day need roughly RENDER_WORKERS such
CPU render workers, or a GPU. Options, in order of effect:

1. a GPU for `worker-render` (see DEPLOYMENT.md), typically an order of magnitude faster for EEVEE;
2. more render workers on more machines (`--scale worker-render=N` against the same PostgreSQL and a
   shared `data/` volume);
3. a lower production resolution (Settings → Production) or shorter videos;
4. a longer interval (Settings → Schedule).

If capacity is short, Blox skips slots with "No QA-approved video was ready in time" rather than
publishing something unfinished.
