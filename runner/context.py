"""Opt-in vLLM chat-template accounting; no byte-estimate fallback."""
import json
import os
import urllib.parse
import urllib.request
from runner.http_deadline import POLICY as DEADLINE_HTTP, request_json


def tokenize_chat(config, messages, timeout):
    endpoint = config['endpoint_url'].rstrip('/')
    url = urllib.parse.urlsplit(endpoint)
    if url.path != '/v1' or url.query or url.fragment:
        raise ValueError('vLLM tokenizer requires an endpoint ending in /v1')
    payload = {
        'model': config['model'], 'messages': messages,
        'add_generation_prompt': True, 'continue_final_message': False,
        'add_special_tokens': False,
        'chat_template_kwargs': config.get('chat_template_kwargs', {}),
    }
    headers = {'Content-Type': 'application/json'}
    key = os.environ.get(config.get('api_key_env', 'FUSE_API_KEY'))
    if key:
        headers['Authorization'] = 'Bearer ' + key
    if config.get('http_transport') == DEADLINE_HTTP:
        result = request_json(config,
            urllib.parse.urlunsplit((url.scheme, url.netloc, '/tokenize', '', '')),
            payload, headers, timeout)
    else:
        request = urllib.request.Request(
            urllib.parse.urlunsplit((url.scheme, url.netloc, '/tokenize', '', '')),
            data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    count, ceiling = result.get('count'), result.get('max_model_len')
    if type(count) is not int or count < 0 or type(ceiling) is not int or ceiling <= 0:
        raise ValueError('tokenizer must return measured count and model limit')
    return {'count': count, 'max_model_len': ceiling}


def bounded_feedback(output, limit):
    """Bound model-visible streams without modifying the retained raw tool log."""
    result = dict(output)
    shortened = False
    for stream in ('stdout', 'stderr'):
        value = output.get(stream, '')
        if not isinstance(value, str):
            raise ValueError('tool stream must be text')
        encoded = value.encode('utf-8')
        if len(encoded) > limit:
            result[stream] = encoded[:limit].decode('utf-8', errors='ignore')
            shortened = True
    result['feedback_truncated'] = shortened
    return result
