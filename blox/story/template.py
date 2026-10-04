"""Offline template stories for dry runs and tests (no LLM, no paid calls).

Output is labelled ``source: template``. It recombines a fixed set of
hand-written beats and lines, so it is formulaic by design; production
autopilot uses LLM-written stories unless the owner explicitly enables
templates (Settings -> allow template stories).
"""
import copy
import random

from .. import demo

VARIANTS = {
    'opener': [
        ("Wait... where's the next platform?!", 'Wait... where did the bridge go?!', 'Uh oh. That gap was NOT there before.'),
    ],
    'friend_call': ['Over here! Just jump!', 'Come on, you can make it!', 'Jump! I believe in you!'],
    'worry': ["That's way too far!", 'I am NOT jumping that.', 'Nope. Nope. Nope.'],
    'psych': ['Okay. You got this.', 'Deep breath. Three, two...', 'For the checkpoint!'],
    'reveal': ["Uh... there's a door right behind you.", 'You know the exit was behind you, right?',
               'Why not just use the door?'],
    'deadpan': ['I jumped... for nothing.', 'Every. Single. Time.', 'Nobody saw that.'],
    'button': ['Again?', 'Same time tomorrow?', 'Classic.'],
}
SETTINGS = [('sky_obby', 'sunset', 'warm_key_cool_fill'), ('sky_obby', 'noon', 'natural'),
            ('sky_obby', 'morning', 'natural'), ('lava_obby', 'noon', 'dramatic_rim')]
PLATFORM_COLORS = ['#4CAF50', '#2196F3', '#FF9800', '#E91E63', '#9C27B0', '#00BCD4', '#FFC107']


def make(seed):
    rnd = random.Random(seed)
    p = demo.plan()
    p['source'] = 'template'
    preset, tod, light = rnd.choice(SETTINGS)
    p['setting']['preset'] = preset
    p['setting']['time_of_day'] = tod
    p['setting']['lighting'] = light
    cols = rnd.sample(PLATFORM_COLORS, 3)
    for prop, col in zip([x for x in p['setting']['props'] if x['type'] == 'platform'], cols):
        prop['color'] = col
    lines = {ln['id']: ln for ln in p['lines']}
    lines['l1']['text'] = rnd.choice(VARIANTS['opener'][0])
    lines['l2']['text'] = rnd.choice(VARIANTS['friend_call'])
    lines['l3']['text'] = rnd.choice(VARIANTS['worry'])
    lines['l4']['text'] = rnd.choice(VARIANTS['psych'])
    lines['l5']['text'] = rnd.choice(VARIANTS['reveal'])
    lines['l6']['text'] = rnd.choice(VARIANTS['deadpan'])
    lines['l7']['text'] = rnd.choice(VARIANTS['button'])
    titles = ['The Missing Platform', 'The Long Way Round', 'Leap of Faith (Not Needed)', 'Bloxy vs The Gap',
              'One Jump Too Many']
    p['title'] = rnd.choice(titles)
    p['metadata'] = {'title': p['title'], 'description': 'Bloxy takes the hard way. Again.',
                     'tags': ['obby', 'animation', 'funny']}
    p['inspiration'] = {'note': 'Template story (offline dry run); no reference video used.', 'sources': []}
    return copy.deepcopy(p)
