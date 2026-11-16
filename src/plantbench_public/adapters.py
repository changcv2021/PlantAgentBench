"""Portable transports. Public prompts/actions and offline scoring remain frozen."""
import copy


class JSONAdapter:
    """Raw or schema-constrained JSON, with no answer repair or tool selection."""
    origin = 'model_run'

    def __init__(self, client, store, *, request_params, response_format=None):
        self.client, self.store = client, store
        self.params = copy.deepcopy(request_params)
        self.response_format = response_format
        self.turns = []

    def generate(self, messages, *, request_id, seed):
        body = {**self.params, 'messages': messages}
        if body.get('seed') == 'harness':
            body['seed'] = seed
        if self.response_format is not None:
            body['response_format'] = self.response_format
        result = self.client.complete(self.store, request_id, body)
        envelope = result['envelope']
        choices = envelope.get('choices') or []
        if len(choices) != 1 or (choices[0].get('message') or {}).get('role') != 'assistant':
            raise ValueError('Single assistant choice required')
        message = choices[0]['message']
        content = message.get('content')
        # Preserve malformed/empty output as a failed action. Never mine reasoning
        # for an answer or turn native calls into JSON in the raw protocol.
        raw = content if isinstance(content, str) else ''
        self.turns.append({'request_id': request_id, **result})
        return {'content_raw': raw, 'response_origin': self.origin,
                'response_sha256': result['response_sha256'],
                'finish_reason': choices[0].get('finish_reason'),
                'usage': envelope.get('usage')}
