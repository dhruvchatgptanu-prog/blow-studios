# Audit of the supplied Blox Studio (before this rebuild)

The supplied archive (`Blox-Studio.zip`) is preserved unchanged in the first repository commit
("Import supplied Blox Studio source unchanged"). It was a compact Flask + SQLite app (about 740
lines across `app.py`, `core.py`, `providers.py`, `render.py`, `worker.py`, `static/app.js`) that turned free-text scenes into
Runway image-to-video clips, added OpenAI narration with FFmpeg, and uploaded to YouTube.

What worked and was kept: password login with a session nonce and CSRF header, the encrypted
credential store (Fernet, `data/vault.key`, same `secret:NAME` settings keys, so saved keys keep
working), Google OAuth with a state check, resumable upload, a "starting" guard before paid
submissions, footage scenes from uploaded clips, and the data directory layout (`data/`, `data/files`).
Existing databases are upgraded in place by migration `0003` (see "Upgrade path" below).

## Concrete defects found

| # | Where (original) | Defect | Effect | Fixed by |
|---|---|---|---|---|
| 1 | `app.py` login: `hmac.compare_digest(request.form['password'], configured)` | `compare_digest` on `str` raises `TypeError` for non-ASCII input | A password containing e.g. `é` or `✓` crashed the login with HTTP 500 | Fixed-length SHA-256 digests compared; scrypt hash option (`ADMIN_PASSWORD_HASH`); test `test_login_non_ascii_password_and_wrong_password` |
| 2 | `worker.py` `tick()` | A connect timeout (request never sent) on a paid POST left `starting=True`; the next step then reported "outcome unknown" | Requests that provably never reached Runway/OpenAI dead-ended as "check the provider"; the only way forward was a new job, which re-submitted (and re-paid for) every scene already generated | `http.py` classifies *not sent* vs *ambiguous*; `paid.run` keeps an exactly-once ledger, releases not-sent reservations, holds only truly ambiguous calls, and the owner can resolve them per call (`not_submitted` / `submitted_ref:<id>` / `charged_discard`) without losing finished work |
| 3 | `providers.download` | `requests.get(url, stream=True)` follows redirects by default; only the literal names `localhost`/`127.0.0.1` were refused | Server-side request forgery to private, link-local (cloud metadata) or DNS-rebinding targets via a provider-returned URL or a redirect | `netsafe.fetch`: HTTPS only, every DNS answer must be globally routable (`is_global`, covers CGNAT/metadata/IPv4-mapped), redirects followed manually and re-validated, connected peer re-checked, size cap; tests in `test_security.py` |
| 4 | `render.captions` | Caption groups of six words spread evenly across each scene's duration | Captions drifted from speech (could appear before or after the words) | Captions come from measured word timings (provider timestamps or ASR), fall back to estimates only when alignment is missing, and are labelled `aligned`/`estimated`; QA compares them with the final audio |
| 5 | `render.py` | `atempo` up to 1.35× to fit narration into fixed scene lengths | Audibly rushed, chipmunk-like voices | Timing adapts to the voice: max tempo 1.06×, then shift the next line ≤0.6 s, then an LLM rewrite of the line (≤2 times), then hold for review |
| 6 | `render.py` | Music mixed with `amix` at a fixed volume | Music masked dialogue | Dialogue/SFX/music stems, sidechain-style ducking envelope, two-pass EBU R128 loudness normalisation to −14 LUFS, true-peak limit; QA measures loudness and clipping |
| 7 | `worker.py` upload | Job phase set to `Uploaded` as soon as `videos.insert` returned an id; privacy came straight from settings (could be `public`) | "Uploaded" was shown although YouTube had not processed or published anything; no processing failure detection | Distinct states `uploaded_private → processing_verified → scheduled → published`; always upload `private` with `publishAt`; `published` only after YouTube reports `public`; processing failures, dropped `publishAt` (unaudited projects) and late publication are detected and surfaced |
| 8 | `worker.py` schedule | `next_run = now + interval_hours` after each run; default 24 h and `daily_video_cap` 2 | Drift (each run shifted the next), no fixed slots, no DST handling, catch-up behaviour undefined after downtime | Slot calendar on local wall-clock times (Australia/Adelaide default, 2 h → 12/day), DST gap/fold rules, rolling-24 h cap, upload lead time, past slots skipped with a reason (no catch-up bursts) |
| 9 | `app.py` login rate limit | Keyed on `request.remote_addr` | Behind a reverse proxy every visitor shares one address: one attacker could lock the owner out, and the per-IP limit did nothing against distributed guessing | `TRUSTED_PROXIES` + `ProxyFix` for the real client address, per-IP and global windows |
| 10 | budget | Daily cap compared a fixed per-video *estimate*; no record of actual spend; repairs/retries unaccounted | Spending could exceed the intended cap | Atomic reservations per paid call (per video, day, month, repair share), measured cost from provider usage where reported, estimated otherwise, both shown separately |
| 11 | `Dockerfile` | Ran as root; no health checks; single worker process with a file lock | Container compromise = root in the container; a crashed worker went unnoticed | Non-root user (uid 10001), `tini`, web and worker health checks, role-separated workers, leader lock in the database |
| 12 | `worker.py` main | A file lock allowed exactly one worker process; on start every `running` job was flipped to `waiting` | A long render blocked every other job (including uploads); work could not be spread across machines or a PostgreSQL deployment | Leased task queue in the database: atomic claim, heartbeats, lease-checked writes, idempotency keys, dead-letter state, role-separated workers, leader lock for the scheduler |
| 13 | prompts | Free-text scene fields were inserted directly into the generation prompt | Instructions hidden in imported text could steer the model | All external text is cleaned, scanned for injection patterns, and passed only as JSON inside `<untrusted_data>` with a system rule; models have no tools and outputs are schema-validated |
| 14 | content | No originality checks against references; no labelling | Risk of near-copies of other creators' stories | Shingle containment + TF-IDF/embedding similarity against references and own history (`story/originality.py`), inspiration record per concept; description states the video is animated and AI-assisted, not gameplay |
| 15 | QA | None; a finished MP4 was "Ready" | Defective outputs could be uploaded automatically | Technical, motion, lip-sync, caption, story and optional vision checks on the actual rendered output; verdict approved / repair / hold / blocked; bounded repair loop |

## Missing capabilities (relative to the brief) that were added

Trend research with quota accounting and explainable ranking, transcripts/analysis with provenance,
pattern learning and originality checks, a frame-indexed production manifest with validation and a
director's script, a deterministic Blender character renderer with IK and evaluated telemetry,
natural TTS with alignment and visemes, assembly with captions/ducking/cover, QA with repairs,
autopilot state machine with buffer and slots, YouTube publishing verification, analytics learning,
the studio UI, PostgreSQL support, tests and deployment files.

## Upgrade path

Migrations are forward-only and run at start-up (`python -m blox.cli migrate` runs them explicitly):

* `0001_legacy_baseline` - the original five tables, unchanged.
* `0002_core_schema` - new tables; adds columns to `assets` (rights, size, hash) without touching rows.
* `0003_seed_and_legacy` - seeds the recurring cast and converts each legacy project into a video:
  footage-only Shorts get a manifest and can be rendered again; free-text Runway projects are
  imported for review (nothing is guessed); finished MP4s are linked; legacy YouTube ids are linked
  and *verified* before anything is called published; unfinished legacy jobs are cancelled (not
  resumed, to avoid paying twice) with their provider task ids copied into the video; legacy budget
  reservations become estimated ledger entries. Legacy rows are kept.
* `0004_story_backlog` - the story backlog table (stories for producing without an LLM API).
* Legacy preferences (audience, synthetic disclosure, timezone, budget caps, mode) are imported once.
  Autopilot is **not** re-enabled after an upgrade.

The upgrade is covered by `tests/unit/test_pipeline.py::test_legacy_database_upgrade_is_non_destructive`.

## Defects in this rebuild found by its own end-to-end runs

Real renders of complete videos, reviewed frame by frame, exposed problems that the unit tests had not.
Each was fixed and covered by a regression test:

* **Camera inside a character.** A `front` camera on a character facing another character was placed
  inside the other character's body; three shots showed only the inside of a head while every QA check
  passed. The camera solver now swings around the subject to a clear angle, and QA has a per-shot
  sight-line check (`sightline:<shot>`, critical). Every story in `stories/` is tested for it.
* **Repairs that changed nothing.** The lip-sync repair asked for a parameter nothing read, so the
  deterministic renderer reproduced the same frames each round; several failing checks on one shot
  also overwrote each other's repair settings. Repairs now carry the measured lag and mouth gain,
  are merged per shot, and a settings repair that would not change the shot holds the video for review.
* **"Think" gesture measured at the wrong point.** QA compared the hand with the face centre, 0.26 m
  from the chin the hand is meant to touch; telemetry now exports the chin point.
* **Lip-sync false positives** (earlier run): the pixel measure counted a hand over the mouth as mouth
  motion; frames with the mouth covered are now excluded and a frozen mouth fails.
* **Originality screen compared a video with itself** when screening against recent videos.
