"""QA check records and the publish verdict."""
from ..manifest.compile import tc

SEVERITIES = ('critical', 'major', 'minor', 'info')
STATUSES = ('pass', 'fail', 'uncertain', 'skipped')


class Checks:
    def __init__(self, fps):
        self.fps = fps
        self.items = []

    def add(self, check_id, name, category, status, severity, evidence, confidence, method, frames=None,
            target=None, repair=None):
        assert status in STATUSES and severity in SEVERITIES
        item = {
            'id': check_id, 'name': name, 'category': category, 'status': status, 'severity': severity,
            'frames': list(frames) if frames else None,
            'time': (f'{tc(frames[0], self.fps)}–{tc(frames[1], self.fps)}' if frames else None),
            'evidence': evidence, 'confidence': round(float(confidence), 3), 'method': method,
            'target': target, 'repair': repair,
        }
        self.items.append(item)
        return item


IMPORTANT_UNCERTAIN = {'critical', 'major'}


def verdict(items, prefs):
    """approved | repair | hold | blocked.

    * Any critical failure blocks publishing. If it has a repair it is repaired,
      otherwise the video is blocked.
    * Major failures are repaired when possible.
    * Uncertain checks of critical/major importance hold the video for review
      (unless the owner chose ``allow_minor`` and the check is only major).
    """
    fails = [c for c in items if c['status'] == 'fail']
    crit = [c for c in fails if c['severity'] == 'critical']
    major = [c for c in fails if c['severity'] == 'major']
    repairable = [c for c in crit + major if c.get('repair')]
    unrepairable_crit = [c for c in crit if not c.get('repair')]
    if unrepairable_crit:
        return 'blocked', unrepairable_crit
    if crit or major:
        if repairable and len(repairable) == len(crit + major):
            return 'repair', repairable
        return 'blocked', [c for c in crit + major if not c.get('repair')]
    policy = prefs['qa']['uncertain_policy']
    unc = [c for c in items if c['status'] == 'uncertain' and c['severity'] in IMPORTANT_UNCERTAIN]
    if policy == 'allow_minor':
        unc = [c for c in unc if c['severity'] == 'critical']
    if unc:
        return 'hold', unc
    return 'approved', []


def summary(items):
    out = {s: 0 for s in STATUSES}
    for c in items:
        out[c['status']] += 1
    return out
