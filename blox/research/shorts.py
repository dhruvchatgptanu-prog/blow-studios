"""Likely-Short classification from available, documented metadata.

The Data API has no "is a Short" field. Signals used:
* duration from contentDetails (Shorts can be up to 3 minutes since Oct 2024);
* player aspect ratio from the ``player`` part requested with ``maxHeight``
  (vertical/square embeds point to vertical video);
* live/premiere status (live streams are excluded);
* a #shorts hashtag in the title/description (weak; anyone can add it).
No single signal is treated as proof. The output is a confidence with reasons.
"""
import re

REUPLOAD = re.compile(r'video credit|credits? to|clips are (selectively )?sourced|compilation|not my video|'
                      r'reupload|ranking top \d|all rights (belong|go) to', re.I)
DUR = re.compile(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')


def iso_duration(s):
    if not s:
        return None
    m = DUR.fullmatch(s)
    if not m:
        return None
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def classify(item):
    sn = item.get('snippet', {})
    dur = iso_duration(item.get('contentDetails', {}).get('duration'))
    pl = item.get('player', {}) or {}
    w, h = pl.get('embedWidth'), pl.get('embedHeight')
    try:
        w, h = (int(w), int(h)) if w and h else (None, None)
    except (TypeError, ValueError):
        w, h = None, None
    live = sn.get('liveBroadcastContent', 'none') != 'none' or bool(item.get('liveStreamingDetails'))
    text = (sn.get('title', '') + ' ' + sn.get('description', '')).lower()
    tag = '#shorts' in text or '#short ' in text
    reasons = []
    signals = {'duration_s': dur, 'player_w': w, 'player_h': h, 'live_or_premiere': live, 'shorts_hashtag': tag}
    if live:
        conf = 0.02
        reasons.append('Live stream or premiere')
    elif dur is None:
        conf = 0.2
        reasons.append('Duration unavailable')
    elif dur > 180:
        conf = 0.02
        reasons.append(f'{dur}s is longer than the 3-minute Shorts limit')
    else:
        vertical = (w and h and h > w * 1.2)
        square = (w and h and abs(h - w) <= w * 0.2)
        horizontal = (w and h and w > h * 1.2)
        if vertical:
            conf = 0.9 if dur <= 60 else 0.8
            reasons.append(f'Vertical player ({w}x{h}) and {dur}s')
        elif square:
            conf = 0.6
            reasons.append(f'Square player ({w}x{h}) and {dur}s')
        elif horizontal:
            conf = 0.1
            reasons.append(f'Horizontal player ({w}x{h}); probably a regular video')
        else:
            conf = 0.5 if dur <= 60 else 0.35
            reasons.append(f'{dur}s; player aspect ratio unavailable')
        if tag and not horizontal:
            conf = min(0.95, conf + 0.05)
            reasons.append('#shorts hashtag present (weak signal)')
    reupload = [m.group(0) for m in REUPLOAD.finditer(text)][:3]
    if reupload:
        reasons.append('Re-upload/compilation wording found; excluded as an inspiration source')
    signals['reupload_signals'] = reupload
    conf = round(conf, 2)
    label = 'likely_short' if conf >= 0.7 else ('possible_short' if conf >= 0.4 else 'unlikely_short')
    return {'label': label, 'confidence': round(conf, 2), 'reasons': reasons, 'signals': signals}
