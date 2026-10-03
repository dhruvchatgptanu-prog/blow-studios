# Live tests

These call real provider APIs with **your** credentials and may cost money or use quota.
They are never collected by the default `pytest` run (see `pytest.ini`), and each test also
skips itself unless `LIVE_TESTS=1` and the credentials it needs are present.

| Test | Needs | Approximate cost |
| --- | --- | --- |
| `test_live_youtube_research.py` | `YOUTUBE_API_KEY` | 1 search call + 1 videos.list unit of Data API quota |
| `test_live_openai.py` | `OPENAI_API_KEY` | One short TTS line + one transcription + one small JSON chat (well under $0.01 at current list prices; check yours) |
| `test_live_youtube_upload.py` | `LIVE_UPLOAD=1`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `LIVE_YOUTUBE_REFRESH_TOKEN` | Uploads one 3-second **private** test video (1 upload call). Delete it in YouTube Studio afterwards. |

```bash
LIVE_TESTS=1 YOUTUBE_API_KEY=... pytest tests/live -m live -v
```

Nothing here publishes publicly. The upload test requests `privacyStatus=private` with no `publishAt`.
