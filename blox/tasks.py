"""Task handler registry and the execution context handed to handlers."""
import importlib
import time

from . import jobs, prefs as prefsmod

HANDLERS = {}


def handler(kind):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


class LeaseLost(Exception):
    pass


class Ctx:
    def __init__(self, task, worker_id, lease_s=300):
        self.task = task
        self.worker_id = worker_id
        self.lease_s = lease_s
        self._last = 0.0

    @property
    def payload(self):
        return self.task['payload']

    @property
    def video_id(self):
        return self.task['video_id']

    def beat(self, force=False):
        """Extend the lease (rate-limited). Raises LeaseLost if another owner took over."""
        if not force and time.time() - self._last < 20:
            return
        self._last = time.time()
        if not jobs.heartbeat(self.task['id'], self.worker_id, self.lease_s):
            raise LeaseLost('Task lease lost (cancelled, paused or taken over)')

    def stop_reason(self):
        """Reason to abort a long subprocess now, or None."""
        p = prefsmod.get()
        if p['autopilot']['emergency_stop']:
            return 'emergency stop'
        if p['autopilot']['paused'] and self.task['kind'] in jobs.PAID_OR_PUBLISHING:
            return 'paused'
        try:
            self.beat()
        except LeaseLost:
            return 'lease lost'
        return None


def ensure_loaded():
    """Import modules that register handlers."""
    for name in ('pipeline', 'research.tasks', 'youtube.analytics', 'youtube.publisher'):
        importlib.import_module(f'{__package__}.{name}')
