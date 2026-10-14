"""OpenAI chat client with strict JSON-schema output and measured cost.

Every call goes through ``paid.run`` (idempotency, budget reservation,
circuit breaker) and returns parsed JSON. Cost is computed from the token usage
the API reports, priced with the editable price table ("measured" usage).
"""
import base64
import json

from . import paid, prefs as prefsmod, vault
from .http import request
from .untrusted import SYSTEM_RULE
from .util import Blocked

URL = 'https://api.openai.com/v1/chat/completions'


def available():
    return vault.configured('OPENAI_API_KEY')


def image_part(path, detail='low'):
    with open(path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    mime = 'image/png' if str(path).endswith('.png') else 'image/jpeg'
    return {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}', 'detail': detail}}


def estimate(tokens_in, tokens_out, prices):
    return tokens_in / 1e6 * prices['openai_text_in_per_mtok'] + tokens_out / 1e6 * prices['openai_text_out_per_mtok']


def chat_json(key, *, system, content, schema_name, schema, model=None, max_output_tokens=6000, video_id=None,
              operation='llm', category='script', est_in=4000, est_out=3000, images=0, p=None):
    api_key = vault.get('OPENAI_API_KEY')
    if not api_key:
        raise Blocked('Connect an OpenAI API key to generate scripts and reviews', state='needs_credentials')
    p = p or prefsmod.get()
    prices = p['budget']['prices']
    model = model or p['production']['text_model']
    est = estimate(est_in, est_out, prices) + images * prices['openai_vision_per_image']
    body = {
        'model': model,
        'messages': [{'role': 'system', 'content': SYSTEM_RULE + '\n\n' + system},
                     {'role': 'user', 'content': content}],
        'response_format': {'type': 'json_schema', 'json_schema': {'name': schema_name, 'schema': schema,
                                                                    'strict': True}},
        'max_completion_tokens': max_output_tokens,
    }
    if model.startswith(('gpt-5', 'o3', 'o4')):
        body['reasoning_effort'] = 'low'

    def call():
        r = request('openai', 'POST', URL, headers={'Authorization': 'Bearer ' + api_key}, json=body,
                    timeout=(10, 240))
        j = r.json()
        choice = j['choices'][0]
        msg = choice['message']
        if msg.get('refusal'):
            raise Blocked('The model declined this request: ' + str(msg['refusal'])[:300], state='needs_review')
        if choice.get('finish_reason') == 'length':
            raise Blocked('Model output was cut off (max tokens). Increase the limit or shorten the request.',
                          state='failed')
        return {'data': json.loads(msg['content']), 'usage': j.get('usage', {}), 'model': j.get('model', model)}

    def cost(resp):
        u = resp.get('usage') or {}
        if not u:
            return None, False
        return estimate(u.get('prompt_tokens', 0), u.get('completion_tokens', 0), prices), True

    res = paid.run(key, provider='openai', operation=operation, category=category, estimate=est, fn=call,
                   video_id=video_id, cost=cost, summary={'model': model, 'schema': schema_name})
    return res['data'], res


def embed(key, texts, video_id=None, p=None):
    api_key = vault.get('OPENAI_API_KEY')
    if not api_key:
        return None
    p = p or prefsmod.get()
    model = p['production']['embedding_model']
    toks = sum(len(t) for t in texts) / 4
    est = max(0.00001, toks / 1e6 * p['budget']['prices']['openai_embed_per_mtok'])

    def call():
        r = request('openai', 'POST', 'https://api.openai.com/v1/embeddings',
                    headers={'Authorization': 'Bearer ' + api_key}, json={'model': model, 'input': texts},
                    timeout=(10, 60))
        j = r.json()
        return {'vectors': [d['embedding'] for d in j['data']], 'usage': j.get('usage', {})}

    res = paid.run(key, provider='openai', operation='embedding', category='embedding', estimate=est, fn=call,
                   video_id=video_id, cost=lambda r: ((r['usage'].get('total_tokens', 0) / 1e6 *
                                                       p['budget']['prices']['openai_embed_per_mtok']), True))
    return res['vectors']
