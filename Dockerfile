# syntax=docker/dockerfile:1
# Blox Studio: web app + workers in one image (the command decides the role).
# Ubuntu 24.04 provides Blender 4.0 and an FFmpeg build with libass and flite.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    BLOX_DATA=/app/data \
    BLENDER_BIN=/usr/bin/blender \
    FFMPEG_BIN=/usr/bin/ffmpeg \
    FFPROBE_BIN=/usr/bin/ffprobe

# blender: deterministic character renderer (headless, EEVEE via EGL or Cycles on CPU)
# ffmpeg: assembly, loudness, QA filters, local test voice; fonts-dejavu-core: captions
# libegl/mesa: lets EEVEE render without a display (software or GPU)
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv ca-certificates tzdata tini curl \
        ffmpeg fonts-dejavu-core \
        blender libegl1 libegl-mesa0 libgl1-mesa-dri libgles2 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv

# Free offline voice model (Piper, LibriTTS, CC BY 4.0), checksum-pinned. Kept outside the data volume.
# Behind a TLS-inspecting proxy, pass its CA bundle: docker build --secret id=proxy_ca,src=/path/ca.pem .
ENV BLOX_VOICES_DIR=/opt/blox-voices
RUN --mount=type=secret,id=proxy_ca,required=false \
    mkdir -p /opt/blox-voices/en-us-libritts-high \
    && CA="" && if [ -s /run/secrets/proxy_ca ]; then CA="--cacert /run/secrets/proxy_ca"; fi \
    && curl -fsSL $CA -o /tmp/voice.tar.gz \
       https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-libritts-high.tar.gz \
    && echo "328e3e9cb573a43a6c5e1aeca386e971232bdb1418a74d4674cf726c973a0ea8  /tmp/voice.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/voice.tar.gz -C /opt/blox-voices/en-us-libritts-high \
         en-us-libritts-high.onnx en-us-libritts-high.onnx.json MODEL_CARD \
    && rm /tmp/voice.tar.gz

WORKDIR /app
COPY requirements.lock requirements.txt ./
RUN pip install --no-cache-dir -r requirements.lock

COPY . .
RUN groupadd --system --gid 10001 blox && useradd --system --uid 10001 --gid blox --home /app blox \
    && mkdir -p /app/data && chown -R blox:blox /app/data
USER blox

EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
# Web by default; workers override the command (see compose.yaml).
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "120", \
     "--access-logfile", "-", "app:app"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz >/dev/null || exit 1
