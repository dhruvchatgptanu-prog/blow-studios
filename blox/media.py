"""Safe media subprocess helpers (FFmpeg/ffprobe/Blender).

* Commands are argument lists, never shell strings, so file names and text
  cannot inject shell syntax.
* Every process has a timeout and runs with a reduced environment.
* Long-running processes stream stdout so callers can heartbeat their lease
  and abort (pause/emergency stop) between lines.
"""
import json
import os
import subprocess
import threading
import time

from . import config


class MediaError(Exception):
    pass


def _env():
    keep = {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'XDG_RUNTIME_DIR', 'PYTHONPATH', 'LD_LIBRARY_PATH'}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.setdefault('LANG', 'C.UTF-8')
    return env


def run(args, timeout=None, cwd=None, input_bytes=None):
    timeout = timeout or config.MEDIA_TIMEOUT_S
    try:
        r = subprocess.run([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                           cwd=cwd, env=_env(), input=input_bytes)
    except subprocess.TimeoutExpired:
        raise MediaError(f'{os.path.basename(str(args[0]))} timed out after {timeout}s')
    except FileNotFoundError:
        raise MediaError(f'{args[0]} is not installed')
    if r.returncode:
        tail = r.stderr.decode(errors='replace')[-800:]
        raise MediaError(f'{os.path.basename(str(args[0]))} failed: {tail}')
    return r.stdout, r.stderr


def stream(args, on_line=None, should_stop=None, timeout=None, cwd=None):
    """Run a process, calling on_line(text) for each stdout line.

    ``should_stop()`` returning a reason string kills the process (used for
    pause/emergency stop/lost lease)."""
    timeout = timeout or config.BLENDER_TIMEOUT_S
    try:
        p = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd,
                             env=_env(), bufsize=1, text=True, errors='replace')
    except FileNotFoundError:
        raise MediaError(f'{args[0]} is not installed')
    tail = []
    killed = {'reason': None}
    start = time.time()

    def watchdog():
        while p.poll() is None:
            if time.time() - start > timeout:
                killed['reason'] = f'timed out after {timeout}s'
                p.kill()
                return
            if should_stop:
                reason = should_stop()
                if reason:
                    killed['reason'] = reason
                    p.kill()
                    return
            time.sleep(2)

    th = threading.Thread(target=watchdog, daemon=True)
    th.start()
    for line in p.stdout:
        line = line.rstrip('\n')
        tail.append(line)
        if len(tail) > 60:
            tail.pop(0)
        if on_line:
            on_line(line)
    p.wait()
    th.join(timeout=5)
    if killed['reason']:
        raise MediaError(f'{os.path.basename(str(args[0]))} stopped: {killed["reason"]}')
    if p.returncode:
        raise MediaError(f'{os.path.basename(str(args[0]))} failed: ' + '\n'.join(tail[-15:])[-1200:])
    return tail


def probe(path):
    out, _ = run([config.FFPROBE_BIN, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                 timeout=120)
    return json.loads(out)


def duration(path):
    j = probe(path)
    return float(j['format'].get('duration') or 0)


def video_stream(info):
    return next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)


def audio_stream(info):
    return next((s for s in info.get('streams', []) if s.get('codec_type') == 'audio'), None)


def fraction(s):
    if not s or s in ('0/0',):
        return 0.0
    if '/' in s:
        a, b = s.split('/')
        return float(a) / float(b) if float(b) else 0.0
    return float(s)


def encode_frames(pattern, out, fps, start_number=0, crf=16):
    run([config.FFMPEG_BIN, '-y', '-v', 'error', '-framerate', str(fps), '-start_number', str(start_number),
         '-i', str(pattern), '-c:v', 'libx264', '-preset', 'medium', '-crf', str(crf), '-pix_fmt', 'yuv420p',
         '-r', str(fps), '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
         '-movflags', '+faststart', str(out)])
    return out


def extract_frame(video, frame_index, fps, out_png):
    t = frame_index / float(fps)
    run([config.FFMPEG_BIN, '-y', '-v', 'error', '-ss', f'{t:.4f}', '-i', str(video), '-frames:v', '1', str(out_png)])
    return out_png
