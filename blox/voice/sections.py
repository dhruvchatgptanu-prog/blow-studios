"""Which part of the story a voiced line belongs to: 'hook', 'body' or 'end'.

The narrator's delivery changes by section (a quicker hook, a slower ending); the voice settings and the
pace checks use the same split. A line belongs to the section of the beat it starts in: hook beats give
'hook'; payoff, button and loop beats give 'end'; every other beat (setup, escalation, mid-gag, twist...)
gives 'body'. A line outside every beat is 'body'.
"""
HOOK_PURPOSES = ('hook',)
END_PURPOSES = ('payoff', 'button', 'loop')


def line_section(m, line):
    start = line['start_frame']
    for b in m.get('beats') or ():
        if b['start_frame'] <= start < b['end_frame']:
            if b['purpose'] in HOOK_PURPOSES:
                return 'hook'
            if b['purpose'] in END_PURPOSES:
                return 'end'
            return 'body'
    return 'body'
