"""Kokoro inference in a separate process (``python -m blox.voice.kokoro_runner``).

This is the only place that imports ``kokoro_onnx`` and, through it, the GPL-3.0 ``phonemizer`` and espeak-ng
library. It is started by ``blox.voice.kokoro.synthesize`` with a text file in and a WAV path out, the same way
Piper is started with ``python -m piper``; nothing from here is imported by the studio.
"""
import argparse
import json
import logging
import os
import sys
import wave

import numpy as np


def main(argv=None):
    ap = argparse.ArgumentParser(prog='kokoro_runner')
    ap.add_argument('--model')
    ap.add_argument('--voices', required=True)
    ap.add_argument('--voice')
    ap.add_argument('--speed', type=float, default=1.0)
    ap.add_argument('--lang', default='en-us')
    ap.add_argument('-i', '--input')
    ap.add_argument('-f', '--output')
    ap.add_argument('--list-voices', action='store_true')
    ap.add_argument('--batch', help='JSON file: [{"text", "output", "voice", "speed", "lang"}, ...]')
    a = ap.parse_args(argv)
    logging.disable(logging.WARNING)
    if a.list_voices:
        names = sorted(np.load(a.voices).keys())
        sys.stdout.write(json.dumps(names))
        return 0
    if a.batch:
        with open(a.batch, encoding='utf-8') as fh:
            items = json.load(fh)
        if not a.model:
            ap.error('--model is required')
    else:
        if not (a.model and a.voice and a.input and a.output):
            ap.error('--model, --voice, -i and -f are required')
        with open(a.input, encoding='utf-8') as fh:
            text = fh.read().strip()
        items = [{'text': text, 'output': a.output, 'voice': a.voice, 'speed': a.speed, 'lang': a.lang}]
    from kokoro_onnx import Kokoro  # GPL-licensed phonemiser loads here, in this process only
    k = Kokoro(a.model, a.voices)
    done = []
    for it in items:
        text = str(it['text']).strip()
        if not text:
            ap.error('empty input text')
        samples, sr = k.create(text, voice=it['voice'], speed=min(2.0, max(0.5, float(it.get('speed', 1.0)))),
                               lang=it.get('lang', 'en-us'))
        s = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
        tmp = it['output'] + '.part'
        with wave.open(tmp, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(sr))
            w.writeframes((s * 32767).astype('<i2').tobytes())
        os.replace(tmp, it['output'])
        done.append({'output': it['output'], 'samples': int(len(s)), 'sample_rate': int(sr)})
    sys.stdout.write(json.dumps(done))
    return 0


if __name__ == '__main__':
    sys.exit(main())
