# Blox Studio

A self-hosted production system for original, Roblox-style animated YouTube Shorts:
research → original story → frame-indexed director's manifest → deterministic 3D character animation
(Blender) with natural voices → assembly → automated QA with bounded repairs → scheduled publishing
with verification → analytics learning. It is operated from a single-owner web studio.

> **What this is not.** It does not capture or claim gameplay, it does not copy other creators'
> stories, voices, thumbnails or branding, and it cannot promise views, virality or monetisation.
> Research covers *a monitored sample* of YouTube, not all of it.

## Contents

1. [Start it](#1-start-it)
2. [Connect your accounts](#2-connect-your-accounts)
3. [Make a first video](#3-make-a-first-video)
4. [Autopilot: enable, pause, stop](#4-autopilot-enable-pause-stop)
5. [Costs and capacity for 12 videos a day](#5-costs-and-capacity-for-12-videos-a-day)
6. [What is implemented and verified](#6-what-is-implemented-and-verified)
7. [Live versus mocked integrations](#7-live-versus-mocked-integrations)
8. [Limits of animation and QA](#8-limits-of-animation-and-qa)
9. [Tests](#9-tests)
10. [Layout and further docs](#10-layout-and-further-docs)

---

## 1. Start it

### Docker (recommended)

```bash
python3 setup.py                 # writes .env: password hash, session secret, database password
mkdir -p data && sudo chown -R 10001:10001 data   # the container runs as uid 10001
docker compose up --build -d     # PostgreSQL, web, and five role-separated workers
open http://localhost:8000       # sign in with the password you chose
```

The web port is bound to `127.0.0.1`. To reach it from elsewhere put it behind an HTTPS reverse proxy
and set `PUBLIC_URL`, `COOKIE_SECURE=1` and `TRUSTED_PROXIES=1` (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

Upgrading from the previous Blox Studio: keep your existing `data/` folder (it holds the old
`studio.db`, uploaded files and `vault.key`). See [Upgrade](docs/DEPLOYMENT.md#upgrading-from-the-previous-release).

### Without Docker (Ubuntu 24.04)

```bash
sudo apt install python3-venv ffmpeg fonts-dejavu-core blender libegl1 libegl-mesa0 libgl1-mesa-dri libgles2
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock
python -m blox.cli install-voice                      # free Piper voice model (checksum-verified)
export ADMIN_PASSWORD='choose-a-long-password'       # or ADMIN_PASSWORD_HASH from: python -m blox.cli hash-password
python -m blox.cli migrate                            # SQLite in ./data unless DATABASE_URL is set
gunicorn -b 127.0.0.1:8000 app:app &                  # web
python worker.py --roles all                          # worker (scheduler runs in its own thread)
```

A local render with no accounts at all (Blender + the built-in robotic **test** voice, never publishable):

```bash
python -m blox.cli demo --quality preview --voice local_test --out data/demo
python -m blox.cli qa-demo --dir data/demo
```

## 2. Connect your accounts

Everything is entered on **Connections** in the studio (stored encrypted with `data/vault.key` or
`BLOX_VAULT_KEY`; values are never shown again). Nothing paid runs without these.

### Zero-cost setup (no paid AI services)

Blox can run with **no paid API at all**:

* **Voices:** the default voice engine is **Piper**, which is free and runs offline. It uses a
  LibriTTS voice model (CC BY 4.0, credited automatically in each video description) and is bundled
  in the Docker image; elsewhere run `python -m blox.cli install-voice`. Pick each character's voice
  (0–903), pitch and rate and listen on **Characters → Hear free voice**. A second free offline
  engine, **Kokoro-82M** (Apache-2.0 weights through `kokoro-onnx`; install with
  `python -m blox.cli install-voice --engine kokoro`, checksum-pinned), can be chosen per character.
  **Speechify** voices are optional, paid with your own key and off by default. The **voice base** on
  Characters measures reference clips you have the right to use (pitch, pace, pauses, energy; the
  audio is deleted right after measuring and never sent to any engine), scores the catalogue voices
  against them and runs a **bake-off** of every candidate voice on one test script. No voice is cloned.
* **Stories:** with no LLM API key, autopilot takes stories from the **Story backlog**. The
  repository includes 24 original, validated stories (`stories/`: a first batch and a mainstream
  batch of storytime and skits written to the style guide in `docs/research/`). You can write your
  own in the editor, or ask Claude in a normal chat for a new batch using the prompt in `stories/README.md`
  (covered by your existing Claude plan). Note that a ChatGPT or Claude *subscription* does not
  include API access, so the studio cannot call those services automatically.
* **Research and publishing:** the YouTube Data API and uploads are free within Google's daily
  quota.

What you need then is: a YouTube Data API key, a Google OAuth client, your channel connected and
confirmed, and the audience and disclosure choices. At 12 videos a day, a backlog of 12 stories lasts
one day (the two included batches last two days); the dashboard shows how many days are left. If it runs out, Blox stops starting new videos
and tells you, rather than publishing anything unfinished.

| Credential | Used for | Required for autopilot | How to get it |
|---|---|---|---|
| OpenAI API key (paid) | automatic story writing, line rewrites, OpenAI voices (`gpt-4o-mini-tts`), measured alignment and dialogue checks (`whisper-1`), optional vision QA, embeddings | Only if you choose AI-written stories or OpenAI voices | platform.openai.com → API keys. Set a monthly budget limit there too. |
| YouTube Data API key | trend research (search, video and channel statistics) | Yes | Google Cloud Console → enable *YouTube Data API v3* → Credentials → API key (restrict it to that API). |
| Google OAuth client (ID + secret) | uploading to and reading your channel | Yes | Same project → OAuth consent screen → Credentials → *OAuth client ID*, type **Web application**, authorised redirect URI exactly `<PUBLIC_URL>/oauth/callback` (shown on Connections). Enable *YouTube Analytics API* for analytics. |
| YouTube channel authorisation | upload as your channel | Yes | Connections → *Connect YouTube* → choose the channel → **confirm** it in the studio. Scopes: `youtube.upload`, `youtube.readonly`, optional `yt-analytics.readonly`. |
| Runway API secret | optional generative shots (image-to-video) | No | dev.runwayml.com |
| ElevenLabs API key | optional voices with character-level timestamps | No | elevenlabs.io; use catalogue voices or voices you have rights to |
| Transcript provider URL + key | optional transcripts of reference videos from a provider you are licensed to use | No | Your provider; must be HTTPS. Blox never downloads other creators' captions through the Data API. |

**Important YouTube restrictions (check Google's current documentation):** API projects that have not
passed YouTube's API compliance audit can upload only *private* videos, so scheduled public
publishing needs an audited project. Uploads use a separate daily quota bucket and channels have
their own daily upload limits; 12 per day is not guaranteed for every account. Blox detects when
YouTube drops the scheduled time or refuses an upload and pauses publishing with the reason instead of
retrying blindly.

Also choose on **Settings → Publishing**: *made for kids* (yes/no) and *altered/synthetic content*
disclosure. New installs default to a general audience (comments on) with the synthetic-content
disclosure set, as the owner decided; both stay editable, and clearing either blocks approval.

Your own research can go in too: **Trend research → Notes** takes pasted text or `.md`, `.txt`,
`.csv`, `.json`, `.jsonl` files (or a zip of them). Titles, video ids, numbers (views, words per
second, durations, beat spacing, hook times) and rule-like lines are extracted locally, kept as
untrusted data, shown read-only in the research brief and passed to story generation as pattern
hints, never as instructions.

## 3. Make a first video

1. **Director's editor → New from demo story** creates a hand-authored 30-second story (labelled as
   demo). Inspect the director's script, the 1-second beat timeline, and the plan JSON.
2. **Produce** runs it through the real pipeline: voices → Blender shots → assembly → QA.
3. **Quality control** shows every check with time codes, evidence and confidence. Click a time to
   jump the player there.
4. If the verdict is *approved*, **Approve for publishing** (review mode) assigns it to the next
   slot; nothing is uploaded unless YouTube is connected and confirmed.
5. **Story backlog** → paste `stories/batch-2026-10-03-claude.json` or
   `stories/batch-2026-10-11-mainstream.json` → *Validate and add* (or
   `python -m blox.cli import-stories <file>`). Autopilot (or **New researched video**) then produces
   those stories in order when no LLM key is connected.

**New researched video** does the full autonomous path (research brief → patterns → original concepts
→ originality screen → manifest with validation → production).

### What a finished video looks like (production style)

* **Pace.** Stories are written in story time and played **1.5× faster** (Settings → Production →
  pace), with voices spoken natively faster (speech rate 1.3) rather than sped up. A 30-second story
  becomes a 20-second video. Each voice's measured words per second (not a planning constant) decides
  how much dialogue fits, so finished speech runs at roughly 4.4–5.4 words per second.
* **Hook on screen.** The plan's hook text (at most six words) is drawn big in a top band over the
  opening, in the caption style, placed off the faces the renderer reports; QA confirms it is visible.
  The cover is the biggest reaction face in the hook or the payoff with the hook text on it.
* **Look.** Costumes (hats, hair, glasses, outfits) on the block characters, big reaction faces, comedy
  edit effects (zoom punch, impact shake, freeze), Shorts-style captions with a heavy outline and a
  yellow active word, a sound bed with cues, whooshes and drop-outs.
* **Sets.** Eight 360-degree sets (sky obby, lava obby, town street, classroom, night forest,
  bedroom, home, park) with walkable ground flush with the feet; the camera never sits inside or looks
  through scenery.
* **Motion.** Idle life (weight shifts, breathing, glances), gestures on stressed words, listening
  reactions, a heel-toe gait and overlapping action under the directed poses.
* **Camera.** Framings measure each character by its visual top (bun, spikes, bow, hat) so tall costumes
  keep their headroom; close-ups swing toward the side the face turns to and avoid other characters.
* **Voices.** Each cast member has a voice, pitch and rate (Piper or Kokoro by default); storytime
  narration is a voice-over kind of its own, never lip-synced.

### Channel kit

**Connections → Channel kit** builds what a new channel needs before the first upload: name and handle
suggestions (original; no "roblox" in the name), an 800×800 profile picture (Bloxy's face rendered by
Blender on a transparent studio background), a 2560×1440 banner with the cast line-up and the name inside
the 1546×423 safe area, a description with the AI-assist disclosure and cadence, the default upload
settings, and the steps to create the channel and connect it. Blox cannot sign in to Google for you, so
the channel itself is created by you with the kit.

## 4. Autopilot: enable, pause, stop

| Action | Where | Effect |
|---|---|---|
| **Enable** | Settings → *Autopilot activation*: choose a mode, type `ENABLE` | Refused with a precise list until stories (backlog or LLM), voices, YouTube Data API, OAuth client, a *confirmed* channel, Blender, FFmpeg, audience + disclosure and live workers are all in place. OpenAI is required only if you chose AI-written stories or OpenAI voices. |
| Mode *review* | same | Production runs ahead; each QA-approved video waits for **Approve for publishing**. |
| Mode *autopilot* | same | QA-approved videos are scheduled without per-video approval. *Hold*/*blocked* videos are never published, and demo videos always wait for your approval. |
| **Pause** / Resume | Dashboard | No new paid generation or uploads start; running steps stop at the next safe checkpoint; research and verification continue. Slots that pass while paused are skipped with the reason. |
| **Emergency stop** | red button in the sidebar | Cancels queued paid and publishing tasks, kills running renders at a safe point, leaves only read-only work. Clearing it leaves autopilot *paused* until you resume. |
| Turn off | Dashboard / Settings | Stops scheduling new work. |

Schedule rules (Settings → Schedule): slots every 120 minutes on local wall-clock time in
Australia/Adelaide (00:00, 02:00, … 22:00), at most 12 per rolling 24 hours. On the spring-forward
night the non-existent local time has no slot (shown on the calendar); on the fall-back night a repeated
time is used once. Each slot is filled by one QA-approved video uploaded `upload_lead_minutes`
(default 180) ahead as **private with a scheduled publish time**; production keeps
`buffer_target` (default 3) approved videos ready. A slot that cannot be filled safely is **skipped with
the reason** (no catch-up bursts after downtime). The automatic global circuit breaker pauses autopilot
when providers keep failing.

## 5. Costs and capacity for 12 videos a day

**Method** (also shown on Budget & learning, and used for the per-video reservation before work starts):

```
per video = script LLM calls (input tokens × in-price + output tokens × out-price)
          + voices (spoken minutes × TTS price; or characters × ElevenLabs price)
          + alignment ASR (spoken minutes × ASR price)
          + QA (final-mix minutes × ASR price + vision images × image price + review tokens)
          + generative seconds × Runway price (0 for Blender shots)
          + repair allowance (repair_share × voice and generative cost)
per day   = per video × videos per day (12 at a 2-hour cadence)
```

Prices live in an editable table (Settings → Budget) because providers change them. The defaults are
planning figures; **check them against each provider's current pricing**. Each paid call reserves its
estimate atomically against the per-video, daily and monthly caps; afterwards the ledger records the
measured cost when the provider reports usage (tokens), otherwise the estimate, and shows both.

Worked example with the default price table and the built-in 30-second demo story (29 spoken words;
a 45-second story with ~100 words roughly triples the voice part):

| Configuration | Estimated per video | × 12 per day | × 30 days |
|---|---|---|---|
| Backlog stories + Blender shots + Piper voices (default without keys) | $0 in API fees | $0 | $0 |
| AI-written stories + Blender shots + OpenAI voices | ≈ $0.07 | ≈ $0.85 | ≈ $26 |
| AI-written stories + Blender shots + ElevenLabs voices | ≈ $0.11 | ≈ $1.36 | ≈ $41 |
| Three Runway shots (12 s) + OpenAI voices | ≈ $2.23 | ≈ $27 | ≈ $800 |

These figures come from `pipeline.estimate_video` plus the script-call estimates; they exclude your
server, YouTube (free within quota) and failed/ambiguous calls held for review.

**Compute is the real constraint.** Blender renders run on the worker's CPU unless you give it a GPU.
Measured on this development machine (4 vCPU, software EGL, no GPU), EEVEE at final quality:
3.2 s per frame at 540×960 and 7.4 s per frame at 1080×1920, so a 30-second, 900-frame video takes
about 50 minutes or **about 1 hour 50 minutes** of rendering. Twelve full-resolution videos per day
therefore need about 22 render-hours a day before repairs: **two to three such render machines, or a
GPU render worker**, or a lower render resolution. Blox will otherwise skip the slots it cannot fill
and say why. See
[docs/OPERATIONS.md](docs/OPERATIONS.md#capacity-planning).

## 6. What is implemented and verified

Verified here means exercised end to end in this environment with real tools, not just unit-tested.

| Area | Implemented | Verified how |
|---|---|---|
| Database, migrations, legacy upgrade | SQLite and PostgreSQL, forward-only migrations, non-destructive import of old projects | Unit tests on both engines (PostgreSQL 16); legacy upgrade test |
| Durable queue | leases, heartbeats, idempotency, retries, dead letters, concurrency keys, leader lock | Concurrency and crash-recovery tests on both engines |
| Paid-call safety and budgets | exactly-once ledger, ambiguous-call holds with owner reconciliation, reservations, breakers, global auto-pause | Unit tests with simulated provider failures |
| Research | YouTube Data API v3 client, quota ledger (Pacific-midnight reset), caching, Shorts signals, explainable ranking, emerging topics, watchlist, references; owner research notes import (local extraction, untrusted) | Fake-API tests seeded with a real, dated sample of 12 Roblox story Shorts ([docs/RESEARCH_SNAPSHOT.md](docs/RESEARCH_SNAPSHOT.md)); notes import unit tests with zip and csv fixtures. **Not run against your API key.** |
| Transcripts and analysis | upload with rights confirmation, authorised provider contract, ASR of your own media, metadata-only labelling | Unit tests; no provider configured |
| Stories and originality | story backlog (import, validation, de-duplication, oldest-first claiming); LLM path: pattern extraction, concepts, manifest generation with validation and repair; originality screening against references and recent own videos | Backlog: a backlog story produced end to end through the queued pipeline here with Piper voices (render, QA, three automatic repair rounds). 12 original stories written in this session, all valid and mutually distinct. LLM path tested with mocked OpenAI; **no live generation was run** |
| Manifest and director's script | authoring plan → frame-indexed manifest, 1-second beats with full start/end poses, validator, script | Unit tests on the demo story and invalid variants |
| Deterministic animation | joint-hierarchy rig with costumes, analytic + iterative IK, footstep planning, springs, idle life and speech gestures, expressions, visemes, amplitude-driven jaw, eight 360-degree sets, camera solver with headroom from the visual top and occlusion-avoiding swings, Blender scene build with telemetry | Real Blender renders; solver tests (planted feet, contacts, jump landing, determinism, cameras never inside or blocked, feet planted at the production pace); four complete videos rendered through the queued pipeline, reviewed frame by frame |
| Voices | free offline Piper voices (default) and Kokoro-82M, OpenAI TTS with direction, ElevenLabs with timestamps, optional Speechify (owner key, paid ledger), per-character pitch and rate, measured words per second per voice, reference voice profiles and bake-off, local test voice, alignment, pronunciation overrides, gentle fitting | Piper run for real (checksum-verified install, CLI, inside the Docker image, in four pipeline videos); Kokoro runner tested with the pinned checksums; OpenAI/ElevenLabs/Speechify request code **not run live** |
| Assembly | captions in the safe area (face-aware placement, heavy outline, yellow active word), hook text band over the opening, ducking, loudness normalisation (short-line fallback), transitions and comedy effects, reaction-face cover and thumbnails | Real FFmpeg tests; four full assemblies |
| QA and repairs | technical, motion (feet, actions, expressions, lip-sync with scene and rendered-pixel evidence, hand occlusion, sight lines against characters and scenery, head in frame, face framing per shot, camera, props, identity), caption, hook-text, story, optional vision checks; verdicts; bounded repair loop whose repairs must change the shot | Real QA on four complete rendered videos, findings checked frame by frame. Those reviews found defects QA had missed or misjudged (camera inside a character, no-op repairs, a miscalibrated gesture check, cropped heads, cheek-only close-ups); all fixed with regression tests ([docs/AUDIT.md](docs/AUDIT.md#defects-in-this-rebuild-found-by-its-own-end-to-end-runs)) |
| Scheduling and autopilot | slots, DST, rolling cap, buffer, skips, pause, emergency stop | Unit tests including Adelaide DST dates |
| YouTube publishing | OAuth (PKCE, state, encrypted tokens), resumable upload with resume and reconciliation, scheduled private uploads, processing/scheduled/published verification, restriction detection | Tests against a simulated YouTube; **no upload to a real channel was performed** |
| Analytics learning | YouTube Analytics reports, retention, findings with bootstrap intervals labelled finding vs hypothesis | Unit-level only; needs your channel |
| Studio UI | dashboard, research (with notes), director's editor with beat inspector, pose editing and hook field, QC, calendar, characters with voice base and bake-off, library, queue, budget & learning, connections with channel kit, settings | Every view driven in headless Chromium against a running server, no console errors, phone width checked |
| Deployment | Dockerfile (non-root, health checks), compose with PostgreSQL and role workers, backups | Image built here (Ubuntu 24.04, Python 3.12, Blender 4.0.2); the test suite passes inside it; the full compose stack (PostgreSQL + migrate + web + 5 workers) came up healthy and created slots |

**Not done / incomplete**

* No live call to any paid provider or to your YouTube channel was made (no credentials or spending
  authorisation were provided). The first live run should be the opt-in live tests in `tests/live`.
* **No video has reached QA approval in this environment yet.** Four complete videos have been produced
  through the queued pipeline here with free voices. The latest two, on the current code, were held by
  QA rather than published: "Whoever Laughs Loses" (classroom) because heads were cropped at the top and
  two close-ups showed a cheek, which led to the headroom and face-framing checks; "My Little Sister
  Hopped Every Time She Lied" (bedroom, hook text on screen) because three close-ups were aimed at the
  back of a head or half off the face. That camera-aim defect is being fixed with regression tests on the
  real plans. Expect some videos to be held or blocked; that costs a slot, not a defective upload.
* Piper voices are clear but calmer and less expressive than instruction-following cloud voices;
  emotion is approximated through pace and variation.
* The story backlog needs refilling: about one batch of 12 stories per day at the default cadence.
* No GPU rendering was available here; full-resolution throughput for 12/day is unproven (see §5).
* Generative (Runway) shots are implemented but untested live; the continuity they achieve depends on
  the model and is reviewed as *uncertain* by QA unless the vision review passes.
* Thumbnail upload to YouTube is off by default (Shorts may ignore custom thumbnails).
* The vision-model review is optional and its judgements are probabilistic.
* Analytics learning needs weeks of published videos before any comparison becomes a *finding*.

## 7. Live versus mocked integrations

| Integration | In this repository's tests | Live code path exists | Run live by |
|---|---|---|---|
| Blender 4.0 (headless) | **Live** (integration test + demo render) | yes | `pytest tests/integration` |
| FFmpeg 6 (assembly, loudness, QA, test voice) | **Live** | yes | default test run |
| PostgreSQL 16 | **Live** when `BLOX_TEST_DATABASE_URL` is set | yes | see §9 |
| OpenAI (chat, TTS, ASR, vision, embeddings) | Mocked HTTP | yes | `LIVE_TESTS=1 OPENAI_API_KEY=… pytest tests/live -k openai` |
| YouTube Data API v3 | Mocked HTTP (real sample data) | yes | `LIVE_TESTS=1 YOUTUBE_API_KEY=… pytest tests/live -k research` |
| Google OAuth + YouTube upload | Mocked HTTP (simulated resumable server) | yes | `LIVE_TESTS=1 LIVE_UPLOAD=1 … pytest tests/live -k upload` (private test video) |
| YouTube Analytics | Not exercised | yes | after connecting with the analytics scope |
| Runway, ElevenLabs, transcript provider | Not exercised (request code reviewed) | yes | connect the key and produce one video in review mode |

## 8. Limits of animation and QA

* **Animation is deterministic keyframe/procedural animation of simple block characters**, not motion
  capture and not captured gameplay. Supported actions are a fixed vocabulary
  (`GET /api/vocabulary`); anything else is rejected by the validator and listed as *unsupported*,
  never silently approximated. Expressions are shape-key faces (brows, lids, pupils, mouth shapes).
* **Lip sync** is viseme timing from word alignment plus an audio-amplitude-driven jaw. It reads as
  speech on simple faces; it is not phoneme-accurate facial animation. When a line has no
  measured alignment, timing is estimated and labelled so, and QA checks the result.
* Contacts (feet, hand-to-face, props) are solved with IK and checked in the evaluated Blender scene to
  centimetre tolerances; collisions between characters are only checked at positions, not full meshes.
* **Occlusion**: cameras are kept out of other characters and QA checks the sight line to each shot's
  subject, with bodies approximated as cylinders. To get a clear view the camera swings toward the side
  the subject is looking at, which can cross the line between two characters (screen direction flips
  for that shot). Set pieces (walls, furniture, trees) are checked too: the camera never sits inside or
  looks through scenery, and QA checks the sight line to the subject against the set. Small props and
  set details are not modelled for occlusion; that relies on the optional vision review or your own
  review.
* **Framing**: characters are framed by their visual top (hair, hat, bow) so tall costumes keep their
  headroom. QA fails a cropped head in whole-head framings and a close-up whose subject's face is off
  screen or turned away for most of the shot, and asks for a wider or re-aimed camera. Faces that cannot
  turn toward the camera (a character directed to look behind themselves) are a known limit: the camera
  can only show what the pose allows.
* **Hook text** is drawn by the assembler, not by the renderer, so it is checked as a caption-style
  overlay; it never covers a face the renderer reported.
* **Generative shots** (Runway) do not follow second-by-second directions exactly; identity and costume
  continuity are reviewed, and uncertain results are held rather than published. Upscaled generative
  output is labelled as upscaled.
* **QA** measures the actual file: decode, dimensions, frame rate, duration, A/V drift, loudness, true
  peak, silences, black frames and freezes, foot sliding, action completion, expression visibility,
  lip-sync correlation, camera jumps, prop contact, caption placement and timing, dialogue versus
  script (ASR), and claim screening. It cannot judge humour, emotional impact or whether a story is
  good; the optional vision review is probabilistic. Uncertain important checks **hold** the video for
  you. A skipped slot is preferred to publishing a known defect.

## 9. Tests

```bash
pip install -r requirements-dev.txt
pytest                                    # mocked unit tests + local Blender/FFmpeg integration (no network, no cost)
BLOX_TEST_DATABASE_URL=postgresql://user@host/db pytest   # also run the database tests on PostgreSQL
LIVE_TESTS=1 pytest tests/live            # real provider calls with your credentials; see tests/live/README.md
```

The unit suite blocks all outbound network access; any unexpected request fails the test. On
2026-10-11 the unit and integration suites gave 785 passed and 6 skipped locally (the skips are
PostgreSQL-only and optional-engine tests) and the CI run on the same commit was green.

## 10. Layout and further docs

```
blox/            application package
  web/           Flask app, security, uploads          migrations/  forward-only schema migrations
  research/      Data API client, ranking, transcripts  story/       patterns, concepts, originality
  manifest/      schema, compiler, validator, script    animation/   rig, solver, life, sets, Blender scene, Runway
  voice/         Piper/Kokoro/Speechify TTS, profiles,  qa/          technical, motion, story, vision
                 bake-off, audio, synthesised SFX/music
  research/notes.py  owner research notes (import)     channel_kit.py  name, profile picture, banner, description
  youtube/       OAuth, publisher, analytics            pipeline.py  production steps
  orchestrator.py  slots, buffer, autopilot             worker.py    queue worker
templates/, static/   studio UI (no external scripts)
tests/unit, tests/integration, tests/live
```

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, state machine, manifest, data model
* [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): Docker, reverse proxy, Google setup, upgrades
* [docs/OPERATIONS.md](docs/OPERATIONS.md): daily operation, backups, incidents, capacity
* [docs/AUDIT.md](docs/AUDIT.md): defects found in the supplied version and how they were fixed
* [docs/RESEARCH_SNAPSHOT.md](docs/RESEARCH_SNAPSHOT.md): the real research sample used in tests
* [docs/research/](docs/research/README.md): creator and trend research, the mainstream style guide
  and the ranked improvement plan behind the current production style
