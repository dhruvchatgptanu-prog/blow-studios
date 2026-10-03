# Blox Studio

A standalone, single-owner Roblox-inspired animation studio. Built independently of Lovable. This is a runnable source-code package, **not a hosted service**. You retain the files and can deploy them on your computer or a server.

## Start here

1. Install Docker Desktop (or Docker Engine + Compose on a server) and Python 3.12+.
2. Unzip this folder and open a terminal inside it.
3. Run `python setup.py` (or `python3 setup.py`). Choose your studio password.
4. Run `docker compose up --build -d`.
5. Open **http://localhost:8000**. Sign in using your studio password.

The web app and background worker share the `data/` directory. The worker processes the queue while the app is running. Closing the browser does not stop it; shutting down or sleeping the computer does. For continuous creation, run on an always-on server. This package does not provision or purchase hosting for you.

Stop: `docker compose down`. View logs: `docker compose logs -f`. Update after editing code: `docker compose up --build -d`.

## What works without external accounts

- Password-protected studio, private media and persistent SQLite projects.
- Editable character identity, costume and scene directions: setting, expression, action and camera.
- Reorder scenes, trim uploaded footage, save drafts and duplicate projects.
- Upload owned/licensed PNG/JPEG/WebP references, MP4/MOV/WebM footage and MP3/WAV/M4A music.
- FFmpeg MP4 assembly, captions from scene narration text and music mixing.
- Local renders using uploaded clips with AI narration disabled.

Create a project, add scenes, upload clips in Media library, select one for each scene, and uncheck AI narrator to render without API services. Scene duration is 5 or 10 seconds. Clips must cover the chosen trim + duration. There is a 24-scene limit: up to four minutes. Output is 720×1280 portrait or 1280×720 landscape, 30 fps. Original clip audio is intentionally replaced by narration/music; this edition does not provide a multitrack source-audio editor.

## Connect generation and publishing

Open **Connections** in the app. Enter secrets there, not into chat. Values are encrypted with a locally generated Fernet key; the page only returns configured/not-configured status. Protect and back up the entire `data/` folder because it contains both the encryption key and encrypted tokens. Encryption does not protect against someone who has access to both.

### OpenAI

Add an OpenAI API key with API billing enabled. The script model defaults to `gpt-4.1-mini` and is editable. Speech uses `gpt-4o-mini-tts`. A ChatGPT subscription is not a substitute for API access. Keys and model access were not available during this build, so live generation was not tested.

**Write scenes with AI** generates an original four-scene story. **Create video** runs animation, narration and final rendering. You can edit the script before generation. Narration is limited by the scene length; the renderer stops when narration would require more than 1.35× speed. Shorten the line instead of accepting unintelligible speech.

### Runway

Add a Runway developer API key with model access and credits. This adapter uses the documented `gen4.5` image-to-video endpoint, which also accepts text-only generation, with the `2024-11-06` API version header. Task IDs are persisted and polled; completed clips are downloaded promptly.

You can supply your own original block-avatar reference image. The app sends the same reference and character description along with each scene's expression, action and camera direction. The reference is a first-frame guide, not a trained character identity lock. Facial acting, fine hand motion, consistent identity and lip synchronization are **not guaranteed**. This version creates AI animation, not recordings from the Roblox game client. It does not play Roblox or scrape other creators' footage. Generated narration is a voiceover; this version does not promise synchronized character speech.

For best results, keep each shot to one clear action, give expressions concrete eye/brow/mouth cues, and use medium shots or close-ups when faces matter. Preview a sample before enabling public autopilot.

### YouTube

1. Create a project in Google Cloud and enable **YouTube Data API v3**.
2. Configure the Google OAuth consent screen. While testing, add your Google account as a test user.
3. Create an OAuth **Web application** client. Add the exact redirect URI displayed in Blox Studio's Connections screen. Local default: `http://localhost:8000/oauth/callback`.
4. Enter the OAuth client ID and client secret in Connections, save, then click **Connect YouTube**.
5. Select your account/channel and authorize `youtube.upload` and `youtube.readonly`.
6. Choose visibility, made-for-kids audience and synthetic-content disclosure in Autopilot settings. These saved selections also apply to manually approved uploads.

OAuth state is session-bound and expires after 10 minutes. Refresh tokens are encrypted on the server. Disconnect revokes the token and pauses autopilot. Google quota, app verification and YouTube API audit restrictions apply; some unaudited projects are restricted to private uploads. Check current provider requirements. No claim of monetization eligibility is made.

## Autopilot

1. Make a creative-brief project with a topic, character description and optional reference/music.
2. Open Autopilot, select that project, and choose the recurrence (every 1–168 hours).
3. Choose **Create, then wait for my review** or **Create and upload automatically**.
4. Set video-count and estimated-budget caps, visibility, audience and disclosure.
5. Enable recurring creation and save. The first job starts about one minute later.

The worker copies the creative brief and writes a fresh four-scene story on each run. It does not repeat the template's existing scenes. A persistent next-run time prevents catch-up bursts after downtime. Recurrence is elapsed hours; the selected timezone determines daily budget resets and is not a local-wall-clock posting calendar. Upload happens when production completes, so the interval schedules creation, not a guaranteed publication minute.

Each production job reserves the configured **estimated cost per video** before making generation calls. The app prevents new jobs after the daily count or estimated allowance is exhausted. These are estimates, not measured charges: set separate billing limits with providers for a firm ceiling. Failed generation jobs retain reservations; retries do not reserve the same job twice. Script-only jobs are outside this production estimate, so provider billing controls still matter. Larger manually edited projects may cost more than the four-scene default.

The pipeline saves its progress across restarts. Only one worker is allowed per data directory. Network reads and resumable upload steps retry with backoff. If a paid submission times out before its task ID is known, the job blocks rather than paying for an automatic duplicate. Check the provider dashboard before making a new job in that case. For an expired or ambiguous YouTube upload session, check YouTube Studio before creating another upload; the app will not silently initiate a replacement session.

**Pause autopilot** prevents new recurring jobs and gates automatic uploads at the next processing step. Already queued generation may finish; an upload already sent cannot be recalled. **Cancel** stops future local steps but does not cancel or refund a provider task already submitted. Review-mode jobs offer an explicit Approve & upload button.

## Hosting securely

Docker Compose binds port 8000 to localhost by default. Do not expose the raw development server to the internet. For a server deployment, keep this binding and put a TLS reverse proxy in front, set `PUBLIC_URL` to the HTTPS domain and `COOKIE_SECURE=1`, update the Google redirect URI, and use a strong unique `ADMIN_PASSWORD`. This is single-owner software, not a multi-tenant SaaS. Do not share the password or data volume with untrusted people.

Use a persistent disk, not ephemeral serverless storage. Back up `data/` and `.env` securely. Do not commit either to a public repository. The included `.gitignore` excludes them. No paid services, hosting or real upload schedules were activated during creation.

## Local development without Docker

Install FFmpeg (including libass caption support) and DejaVu Sans, then:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export ADMIN_PASSWORD='a-unique-local-password'
export SESSION_SECRET='a-random-string-at-least-32-characters-long'
python app.py
```

Run `python worker.py` in another terminal with the same environment. Native `.env` loading is not automatic; Docker Compose reads it. The worker uses a Unix file lock; Windows users should use Docker or WSL.

## Validation and known limits

Run `pip install pytest` then `python -m pytest -q`.

Thirteen checks passed during this build: authentication/CSRF, secret encryption and redaction, edit validation and active-job locking, setup gates, atomic queue claiming, scheduler deduplication, budget reservations, uncertain paid-request protection, paused-upload gating, resumable-upload completion recovery, OAuth state validation, private file boundaries, and a real FFmpeg render with captions/music and checked dimensions/duration. JavaScript syntax and Python compilation also passed.

Provider interactions in tests are mocked. **Live OpenAI/Runway generation, Google OAuth, YouTube uploads and long-running hosted operation have not been tested with your accounts.** The browser UI has not received a live browser end-to-end test in this environment. Docker build/deployment is included but was not executed here. No thumbnail generator or thumbnail upload is included in this edition.

## Official integration references

- Runway generation examples: https://docs.dev.runwayml.com/guides/using-the-api/
- OpenAI API reference: https://platform.openai.com/docs/api-reference
- YouTube resumable protocol: https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
- YouTube insert endpoint: https://developers.google.com/youtube/v3/docs/videos/insert

Provider APIs, model access and prices can change. Review these docs when deploying.
