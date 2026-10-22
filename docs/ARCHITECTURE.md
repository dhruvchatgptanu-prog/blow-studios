# Architecture

## Flow

```
research (YouTube Data API, watchlist, references)            ┐
  → ranked candidates in the monitored sample (explained)      │  research worker
  → transcripts/analysis only from authorised sources          ┘
brief (top sources, metadata-only flagged)                     ┐
  → story source: LLM API (if connected) or story backlog      │
  LLM: abstract patterns (sources as untrusted data)           │
       → original concepts → originality screen                │  orchestrate worker
       → authoring plan (strict schema) → compile → validate   │  (video.develop)
  backlog: oldest validated story → compile → validate         │
  → originality screen vs references and own recent videos     │
  → director's script + publish metadata                       ┘
storyboard: cost estimate + budget check                       ┐
voices (Piper or Kokoro offline / OpenAI / ElevenLabs / Speechify) → alignment → fit │
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

Plans are written in story time and compiled at the production **pace** (`production.pace`, default 1.5:
every action, camera move, pause and line plays 1.5× faster) with voices spoken natively at
`production.speech_rate` (1.3). Line lengths are estimated from each cast voice's measured words per
second and line overhead (migration m0006; `estimate_line_frames` gets the cast rows), optionally voiced
for real at validation time (`measure_lines_on_validate`). The plan's `hook.text` is drawn on screen
over the opening (`hook.on_screen`, default from `production.hook_text_overlay`) and composited on the
cover frame.

`validate.py` rejects structural problems (unknown vocabulary is reported as *unsupported*), timeline
gaps/overlaps, overlapping use of a body part, impossible action sequences, dialogue that does not
fit, teleports, implausible speeds and jumps, unsupported ground, unreachable props, expressions that
are too short or unreadable at the framing or turned away from camera, captions outside the safe area,
and missing hooks or payoffs. `director.py` renders the beat list as a readable second-by-second script.

**Story formats.** A plan may set `format`: `skit`, `storytime` or `first_person_story` (absent = the
behaviour before formats). The manifest carries it as `m['format']`, and `story.mechanism`, `story.mode`
(`world_scale` or `grounded`), `story.twist_type` and `story.loop_type` (`callback` or `sequel_tease`) are
copied into `m['story']` (vocabulary in `blox/story/formats.py`, also served at `/api/vocabulary`). Beat
purposes `mid_gag`, `twist` and `loop` mark the format's mid-video like/subscribe gag, its twist and the
last line that runs back into the hook. `compile.beat_segments` turns the one-second beats back into the
authored beats (an authored beat runs until the next one) for the checks below. In this format the music
drops out for 0.5 s just before the twist unless the plan places the drop-out itself.

The first-person format is checked only when `m['format'] == 'first_person_story'`, against
`formats.calibration()`: the owner's imported numbers (store key `format_calibration`, imported from
`calibration.json` and `pace_profile.json` on the Story backlog page, every value range-checked) laid over
the defaults (25-45 s, narration 4.0-4.45 words/s, hook line done by 2.4 s, shots at most 3.5 s, the
mid-video gag starting at 35-70% of the video and lasting at most 2.5 s; all finished-video time). Errors:
a narrator; the first line is the narrator's narration from the first 0.3 s and ends by the hook limit; at
least three escalation/climax steps; a twist beat; a payoff; a mid-video gag in its window with a narrator
line asking for a like or a sub; every shot within the limit; mechanism, mode and twist type set; an emoji in
the title. Warnings: title grammar ("I [did something] [one extreme thing] 😳", 3-9 words, the narrator in
it, no hashtags), the first word "I", the hook line saying the title, a final loop beat (and shared words
for a callback loop), estimated narration pace, line pace, other characters' line length, sentence length,
the drop-out before the twist and the video length. The voice is never sped up to meet the pace: the plan's
wording sets it. An IP watch warns, in every format, about well-known trademarked characters, brands and
people in the title, description, tags, hook and lines. On the rendered video, `qa/story.py` measures the
first narrated word, narration words per second by section (`voice/sections.py`: hook, body, end), the
mid-gag line's voiced time and the longest shot; these records are `minor`, so they never hold or block.

Coordinate system: metres, Z up, characters face −Y at facing 0°; facing is degrees about Z.

## Animation (`blox/animation`)

* `rig.py`: block character with a joint hierarchy (pelvis → spine → chest → neck → head; shoulders →
  elbows → wrists; hips → knees → ankles), part boxes, face layout, mouth shapes and visemes.
* `solver.py` (host side, numpy): root paths for walk/run/jump/turn with anticipation and overshoot,
  footstep planning with planted feet, analytic two-bone IK for legs and arms plus iterative palm IK
  for contacts (face, props), spring-based follow-through, look-at with a lagged head, blinks,
  expression blending, viseme timing from word alignment plus an amplitude-driven jaw, and a camera
  solver (lens by framing, headroom measured from the costume's visual top, follow with springs, moves,
  swings that keep other characters and scenery out of the line of sight, close-ups that swing toward
  the face). Deterministic: the same manifest produces the same motion. Solved motion is cached per
  manifest timing, alignment, repair parameters and `code_version()` (a fingerprint of solver.py,
  sets.py and life.py), so a code change re-solves instead of reusing stale motion.
* `life.py`: the life layers under the directed poses: idle weight shifts and breathing, glances and
  saccades, gestures on stressed words, listening reactions, a heel-toe gait and overlapping action.
* `sets.py`: 360-degree set layouts for every preset (sky obby, lava obby, town street, classroom, night
  forest, bedroom, home, park) built around a clearance box the cast walks in: walkable ground flush with
  planted feet, camera zones, piece boxes used by the camera solver and QA, time-of-day window lights.
* `rig.py` also exposes each costume's visual top (hair, hat, bow) so framings, headroom and the
  sight-line checks use the real silhouette.
* `blender_scene.py` runs inside headless Blender: builds the set, props and cast, applies the solved
  transforms per frame, renders, and exports **telemetry from the evaluated scene** (feet, palms, face
  and mouth projected to the image, visibility, camera) used by QA.
* `generative.py`: Runway image-to-video with a deterministic Blender reference frame; `clips.py`:
  owner footage.

## QA (`blox/qa`)

Checks run on the actual output and telemetry: technical (FFmpeg filters and probes), motion (feet,
actions, expressions, lip sync measured in a tight mouth box on the rendered pixels, sight lines against
characters and scenery, head in frame, face framing per shot, camera, props, identity), story (hook, hook
text on screen, payoff, dialogue vs ASR, captions, claim screening) and an optional vision review of
frame strips. Each record has status,
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
`learning_findings`, `story_backlog`, `voice_profiles` (measured reference voices), `research_notes`
(owner notes, untrusted), `audit_log`, `settings` (preferences and encrypted secrets), plus the original
release's `projects`, `jobs`, `assets`, `reservations` (kept).
