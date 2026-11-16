"""Kimi native function-calling adapter for the unmodified agent_harness.run_episode.

run_episode owns the loop: it builds the public history (system prompt, public
action schema, PlantEnv observations), asks this adapter for the next action
text, and executes that text in the real PlantEnv. This adapter only
  1. re-expresses that same public history in Kimi's native tool format,
     replaying every earlier assistant turn exactly as Kimi returned it
     (content, reasoning_content, tool_calls) and every observation as the
     tool result of the call that caused it;
  2. calls the official API once per step (durable, first HTTP 200 is final);
  3. maps the returned tool call to PlantEnv's action text without repair.
It never sees the episode object, gold, or anything the history does not show.
"""
import base64
import hashlib
import json
import os

from .tools import INTERFACE_NOTE, INTERFACE_PREAMBLE, INTERFACE_REMINDER, action_from_message, function_definitions

ADAPTER_VERSION = 'kimi-native-tools-adapter-2.0.0'  # 2.0: interface preamble before the harness prompt


class ImageStore:
    """Content-addressed image files shared by raw trajectories and SFT data."""
    def __init__(self, root):
        self.root = root

    def put(self, data_url):
        header, payload = data_url.split(',', 1)
        raw = base64.b64decode(payload, validate=True)
        sha = hashlib.sha256(raw).hexdigest()
        mime = header[5:].split(';')[0]
        ext = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}[mime]
        rel = os.path.join(sha[:2], sha + '.' + ext)
        path = os.path.join(self.root, rel)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + '.tmp%d' % os.getpid()
            with open(tmp, 'wb') as handle:
                handle.write(raw)
            os.replace(tmp, path)
        return {'sha256': sha, 'mime': mime, 'path': rel, 'bytes': len(raw)}


def blocks_to_refs(content, images):
    """Public message blocks with image bytes replaced by content-addressed refs."""
    out = []
    for block in content if isinstance(content, list) else [{'type': 'text', 'text': content}]:
        if block['type'] == 'image_url':
            out.append({'type': 'image', **images.put(block['image_url']['url'])})
        else:
            out.append({'type': 'text', 'text': block['text']})
    return out


class KimiToolAdapter:
    origin = 'model_run'

    def __init__(self, client, store, images, *, public_action_schema, request_params, image_channel='tool', interface_version=2):
        if image_channel not in {'tool', 'user_followup'}:
            raise ValueError('image_channel must be tool or user_followup')
        self.client, self.store, self.images = client, store, images
        self.tools = function_definitions(public_action_schema)
        self.params = dict(request_params)
        self.image_channel = image_channel
        self.interface_version = interface_version  # 1 reproduces the v1 pilot requests exactly
        self.turns = []            # one record per model call, in order
        self.observations = []     # public user messages as image refs, in order

    # -- history translation -------------------------------------------------
    def _tool_messages(self, turn, content):
        calls = turn['native_message'].get('tool_calls') or []
        if self.image_channel == 'tool':
            first = content
            followup = None
        else:
            first = [b if b['type'] != 'image_url' else {'type': 'text', 'text': '[image delivered in the next message]'} for b in content]
            followup = [b for b in content if b['type'] == 'image_url']
        out = []
        for i, call in enumerate(calls):
            body = first if i == 0 else [{'type': 'text', 'text': json.dumps({'same_environment_result_as_tool_call_id': calls[0].get('id')})}]
            out.append({'role': 'tool', 'tool_call_id': call.get('id'), 'name': (call.get('function') or {}).get('name'), 'content': body})
        if followup:
            out.append({'role': 'user', 'content': [{'type': 'text', 'text': 'Images returned by the previous tool call:'}] + followup})
        return out

    def native_messages(self, messages):
        if messages[0]['role'] != 'system' or [m['role'] for m in messages[1:3]] != ['user', 'user']:
            raise ValueError('Unexpected public history layout')
        system = (INTERFACE_PREAMBLE if self.interface_version >= 2 else '') + messages[0]['content'] + INTERFACE_NOTE \
            + (INTERFACE_REMINDER if self.interface_version >= 2 else '')
        out = [{'role': 'system', 'content': system}, messages[1], messages[2]]
        step = 0
        for message in messages[3:]:
            if message['role'] == 'assistant':
                turn = self.turns[step]
                if message['content'] != turn['raw_action']:
                    raise ValueError('Public history diverged from the recorded model turn')
                out.append(turn['native_message'])
                step += 1
            else:
                prev = self.turns[step - 1]
                if prev['native_message'].get('tool_calls'):
                    out.extend(self._tool_messages(prev, message['content']))
                else:
                    out.append(message)  # No call to answer: plain public observation.
        if step != len(self.turns):
            raise ValueError('Recorded turns not all present in public history')
        return out

    # -- one model call ------------------------------------------------------
    def generate(self, messages, *, request_id, seed):
        # Record every public observation (as image refs) the first time it appears.
        users = [m for m in messages[2:] if m['role'] == 'user']
        for message in users[len(self.observations):]:
            self.observations.append(blocks_to_refs(message['content'], self.images))
        native_history = self.native_messages(messages)
        self.system_prompt = native_history[0]['content']
        self.schema_message = blocks_to_refs(messages[1]['content'], self.images)
        body = {'model': self.params['model'], 'messages': native_history, 'tools': self.tools,
                **{k: v for k, v in self.params.items() if k != 'model'}}
        if body.get('seed') == 'harness':
            body['seed'] = seed  # local server: deterministic per (task, step) seed from run_episode
        result = self.client.complete(self.store, request_id, body)
        envelope = result['envelope']
        choices = envelope.get('choices') or []
        if len(choices) != 1 or (choices[0].get('message') or {}).get('role') != 'assistant':
            raise ValueError('INVALID_KIMI_ENVELOPE_SINGLE_ASSISTANT_CHOICE_REQUIRED')
        choice = choices[0]
        message = choice['message']
        # Replay exactly what the API returned; never rewrite reasoning or calls.
        # The official API returns `reasoning_content`; vLLM returns `reasoning`
        # (and accepts either on input). Replay under the name the server used.
        native = {'role': 'assistant', 'content': message.get('content')}
        reasoning_field = next((k for k in ('reasoning_content', 'reasoning') if message.get(k) is not None), None)
        if reasoning_field:
            native[reasoning_field] = message[reasoning_field]
        if message.get('tool_calls'):
            native['tool_calls'] = message['tool_calls']
        raw_action, conversion = action_from_message(message)
        reasoning = message.get(reasoning_field) if reasoning_field else None
        self.turns.append({
            'request_id': request_id, 'request_hash': result['request_hash'], 'request_message_count': len(body['messages']),
            'native_message': native, 'raw_action': raw_action, 'conversion': conversion,
            'finish_reason': choice.get('finish_reason'), 'usage': envelope.get('usage'),
            'served_model': envelope.get('model'), 'response_id': envelope.get('id'),
            'raw_response': envelope, 'response_sha256': result['response_sha256'],
            'transport_attempt': result['attempt'], 'replayed_from_store': result['replayed_from_store'],
            'server_request_id': result['server_request_id'], 'elapsed_seconds': result['elapsed_seconds'],
            'reasoning_text': reasoning, 'reasoning_field': reasoning_field,
            'reasoning_present': isinstance(reasoning, str) and bool(reasoning.strip())})
        usage = envelope.get('usage') or {}
        return {'content_raw': raw_action, 'response_origin': 'model_run', 'logical_request_id': request_id,
                'prompt_tokens': usage.get('prompt_tokens'), 'completion_tokens': usage.get('completion_tokens'),
                'finish_reason': choice.get('finish_reason'), 'response_sha256': result['response_sha256'],
                'observed_reasoning_channel': bool(reasoning), 'reasoning_characters': len(reasoning or ''),
                'adapter_conversion': conversion['kind']}
