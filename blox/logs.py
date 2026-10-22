"""Structured JSON logging with secret scrubbing."""
import json
import logging
import re
import sys
import time

_PATTERNS = [
    (re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._\-~+/=]{8,}'), r'\1[redacted]'),
    (re.compile(r'\bsk-[A-Za-z0-9_\-]{8,}'), '[redacted-key]'),
    (re.compile(r'\bya29\.[A-Za-z0-9._\-]+'), '[redacted-token]'),
    (re.compile(r'\b1//[A-Za-z0-9._\-]{10,}'), '[redacted-token]'),
    (re.compile(r'(?i)([?&](?:key|access_token|refresh_token|code|client_secret|upload_id)=)[^&\s"\']+'), r'\1[redacted]'),
    (re.compile(r'(?i)("?(?:api[_-]?key|authorization|client_secret|refresh_token|access_token|password|xi-api-key)"?\s*[:=]\s*"?)[^",\s}]+'), r'\1[redacted]'),
]


def scrub(text):
    if text is None:
        return text
    s = str(text)
    for rx, rep in _PATTERNS:
        s = rx.sub(rep, s)
    return s


class JsonFormatter(logging.Formatter):
    RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {'message', 'asctime'}

    def format(self, record):
        out = {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(record.created)) + f'.{int(record.msecs):03d}Z',
            'level': record.levelname.lower(),
            'logger': record.name,
            'msg': scrub(record.getMessage()),
        }
        for k, v in record.__dict__.items():
            if k not in self.RESERVED and not k.startswith('_'):
                out[k] = scrub(v) if isinstance(v, str) else v
        if record.exc_info:
            out['exc'] = scrub(self.formatException(record.exc_info))[-4000:]
        return json.dumps(out, default=str)


_configured = False


def setup(level='INFO'):
    global _configured
    if _configured:
        return
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
    logging.getLogger('urllib3').setLevel('WARNING')
    logging.getLogger('werkzeug').setLevel('WARNING')
    _configured = True


def get(name):
    return logging.getLogger(name)
