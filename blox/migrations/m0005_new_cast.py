"""Add the new recurring characters Rook, Dot and Ms. Tally.

Only ids that are missing are inserted; an existing row (for example one the
owner already created or edited on the Characters page) is never overwritten,
and Bloxy and Pip are left exactly as they are.
"""
import json
import time


def up(d):
    from ..demo import CHARACTERS, NEW_CAST_IDS
    t = time.time()
    for c in CHARACTERS:
        if c['id'] not in NEW_CAST_IDS or d.one('SELECT 1 AS x FROM characters WHERE id=?', (c['id'],)):
            continue
        d.execute('INSERT INTO characters(id, name, bible, voice, active, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
                  (c['id'], c['name'], json.dumps(c['bible']), json.dumps(c['voice']), 1, t, t))
