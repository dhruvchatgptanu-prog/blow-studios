# Architecture

## Flow

```
research (YouTube Data API, watchlist, references)            ┐
  → ranked candidates in the monitored sample (explained)      │  research worker
  → transcripts/analysis only from authorised sources          ┘
brief (top sources, metadata-only flagged)                     ┐
  → abstract patterns (LLM, sources as untrusted data)         │
  → original concepts → originality screen                     │  orchestrate worker
  → authoring plan (LLM, strict schema) → compile → validate   │  (video.develop)
  → director's script + publish metadata                       ┘
storyboard: cost estimate + budget check                       ┐
voices (TTS) → alignment → fit/retime/rewrite → manifest timing│
motion solve (rig, IK, feet, face, visemes, camera)            │  render worker
Blender shots (or Runway / footage) with telemetry             │
assembly: concat, captions, mix + ducking, loudness, cover     ┘
QA on the rendered file → approved | repair | hold | blocked     qa worker; repair loop via orchestrate
slot assignment → private upload with publishAt                ┐
  → processing verified → scheduled → published (observed)     ┘  publish worker
analytics → findings (with intervals) / hypotheses → next briefs  research worker
```

Every arrow is a queued task with an idempotency key. A step that waits on something external
(Runway job, YouTube processing) re-queues itself with a delay instead of holding a worker.

## Video state machine (`blox/videos.py`)

```
discovered → researched → concept_selected → scripted → storyboarded → generating → rendering
  → checking → (repairing → generating|rendering → checking)* → approved
  → upload_started → uploaded_private → processing_verified → scheduled → published
holds (from any non-terminal state, resumable): needs_review, needs_credentials, blocked
terminal: failed, cancelled
```

Transitions are compare-and-set on the current status and recorded in `video_events`. Holds keep a
`resume_status`. "Published" is set only after the YouTube API reports `privacyStatus=public`.

## Production manifest

Authors (the LLM or the owner) write a plan in **seconds** using a fixed vocabulary
(`blox/manifest/schema.py`, also served at `/api/vocabulary`). `compile.py` converts it into the
authoritative **frame-indexed** manifest:

* `shots` with frame ranges, renderer, camera (framing, angle, side relative to the subject's facing,
  move, lens), transitions;
* `tracks.characters[cast_id].keys`: pose keys (expression, posture, head yaw/pitch/roll, brows, eyes,
  mouth, arms, stance/weight, position, facing, eye target), with locomotion end states propagated;
* `tracks.actions`: typed actions with anticipation, main, follow-through and hold phases;
* prop events, blinks, lines (estimated or measured frames), captions (aligned or estimated), SFX, music;
* `beats`: one per second (split at cuts) with each visible character's resolved start and end pose,
  camera state, dialogue, captions, sound, continuity notes and the QA checks expected there.

`validate.py` rejects structural problems (unknown vocabulary is reported as *unsupported*), timeline
gaps/overlaps, overlapping use of a body part, impossible action sequences, dialogue that does not
fit, teleports, implausible speeds and jumps, unsupported ground, unreachable props, expressions that
are too short or unreadable at the framing or turned away from camera, captions outside the safe area,
and missing hooks or payoffs. `director.py` renders the beat list as a readable second-by-second script.

Coordinate system: metres, Z up, characters face −Y at facing 0°; facing is degrees about Z.

## Animation (`blox/animation`)

* `rig.py`: block character with a joint hierarchy (pelvis → spine → chest → neck → head; shoulders →
  elbows → wrists; hips → knees → ankles), part boxes, face layout, mouth shapes and visemes.
* `solver.py` (host side, numpy): root paths for walk/run/jump/turn with anticipation and overshoot,
  footstep planning with planted feet, analytic two-bone IK for legs and arms plus iterative palm IK
  for contacts (face, props), spring-based follow-through, look-at with a lagged head, blinks,
  expression blending, viseme timing from word alignment plus an amplitude-driven jaw, and a camera
  solver (lens by framing, headroom, follow with springs, moves). Deterministic: the same manifest
  produces the same motion.
* `blender_scene.py` runs inside headless Blender: builds the set, props and cast, applies the solved
  transforms per frame, renders, and exports **telemetry from the evaluated scene** (feet, palms, face
  and mouth projected to the image, visibility, camera) used by QA.
* `generative.py`: Runway image-to-video with a deterministic Blender reference frame; `clips.py`:
  owner footage.

## QA (`blox/qa`)

Checks run on the actual output and telemetry: technical (FFmpeg filters and probes), motion (feet,
actions, expressions, lip sync, camera, props, identity), story (hook, payoff, dialogue vs ASR,
captions, claim screening) and an optional vision review of frame strips. Each record has status,
severity, frames, evidence, confidence, method and an optional repair action. The verdict:
critical failure without a repair → *blocked*; repairable failures → *repair* (bounded per target and
per video); uncertain critical/major → *hold*; otherwise *approved*.

## Reliability

* **Queue** (`jobs.py`): atomic claim (`BEGIN IMMEDIATE` / `FOR UPDATE SKIP LOCKED`), leases with
  heartbeats, lease-checked writes, retries with backoff, dead letters, concurrency keys, leader lock.
* **Paid calls** (`paid.py`): every billable request is recorded *before* sending; outcomes are
  completed, failed-not-sent (released), failed (definitive) or ambiguous (held for the owner, never
  retried automatically by default). Budget reservations are atomic (`budget.py`).
* **Circuit breakers** (`breaker.py`): per provider, plus a global breaker that pauses autopilot.
* **Pause / emergency stop**: checked at every task claim and by long-running subprocesses
  (`Ctx.stop_reason`), which are killed at a safe point.

## Security

Single owner password (scrypt hash supported), session cookie with CSRF header and Origin checks,
strict CSP without inline scripts, rate-limited login, encrypted credentials (Fernet), scrubbed JSON
logs, SSRF-safe fetching (`netsafe.py`), media served only for registered files, uploads validated by
content, FFmpeg/Blender run with argument lists, reduced environment and timeouts, external text
treated as data inside `<untrusted_data>` blocks for models without tools, schema-validated model
output, and public error responses without internal details.

## Data model (main tables)

`videos`, `video_events`, `manifests`, `shots`, `audio_lines`, `renders`, `qa_reports`, `repairs`,
`slots`, `uploads`, `tasks`, `workers`, `locks`, `paid_calls`, `budget_ledger`, `breakers`,
`quota_ledger`, `api_cache`, `research_runs`, `ref_channels`, `ref_videos`, `ref_snapshots`,
`transcripts`, `ref_analyses`, `patterns`, `concepts`, `characters`, `analytics_snapshots`,
`learning_findings`, `audit_log`, `settings` (preferences and encrypted secrets), plus the original
release's `projects`, `jobs`, `assets`, `reservations` (kept).
