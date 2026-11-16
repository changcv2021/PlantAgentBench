"""Offline transport and supervision tests. No real API, no teacher data."""
import base64
import json
import ast
import importlib
from pathlib import Path
from argparse import Namespace

import pytest

from plantbench_public.cli import check_endpoint, selection, validate_config, merge, write_json
from plantbench_public.native.kimi_client import KimiClient, KimiStore
from plantbench_public.native.tools import action_from_message
from plantbench_training.to_swift import convert


def test_native_arguments_are_not_repaired():
    message = {'tool_calls': [{'type': 'function', 'function': {'name': 'QUERY', 'arguments': '{"target_id":"first","target_id":"second"}'}}]}
    raw, info = action_from_message(message)
    assert raw.count('"target_id"') == 2
    assert info['kind'] == 'spliced'


def test_reasoning_is_not_turned_into_a_tool():
    raw, _ = action_from_message({'content': None, 'reasoning': '{"action":"STOP"}'})
    assert raw == ''


def test_first_200_is_reused_and_identity_is_immutable(tmp_path):
    client = KimiClient('http://127.0.0.1:8000/v1', None, max_attempts=1)
    body = {'model': 'fixture', 'messages': [{'role': 'user', 'content': 'test'}]}
    envelope = {'choices': [{'message': {'role': 'assistant', 'content': 'invalid action'}}]}
    calls = []
    def post(_):
        calls.append(1)
        return {'http_status': 200, 'response_base64': base64.b64encode(json.dumps(envelope).encode()).decode(),
                'elapsed_seconds': 0, 'headers': {}}
    client._post = post
    store = KimiStore(tmp_path / 'requests.sqlite', {'model': 'fixture'})
    assert client.complete(store, 'r1', body)['envelope'] == envelope
    assert client.complete(store, 'r1', body)['replayed_from_store'] is True
    assert len(calls) == 1
    with pytest.raises(ValueError):
        client.complete(store, 'r1', {**body, 'model': 'changed'})
    store.close()


def test_masked_thinking_and_actions_stay_masked():
    sample = {'id': 'synthetic', 'images': ['fixture.png'], 'tools': [], 'primary_p': 'P1', 'isolation_group': 'fixture',
      'messages': [
        {'role': 'user', 'content': [{'type': 'image', 'image': 'fixture.png'}, {'type': 'text', 'text': 'Observation'}]},
        {'role': 'assistant', 'reasoning_content': 'Fixture reasoning', 'content': '', 'loss': False,
         'tool_calls': [{'function': {'name': 'QUERY', 'arguments': {'target_id': 'fixture'}}}]},
        {'role': 'tool', 'content': 'Environment feedback'},
        {'role': 'assistant', 'reasoning_content': 'Next fixture thought', 'content': '', 'loss': True,
         'tool_calls': [{'function': {'name': 'SUBMIT', 'arguments': {}}}]}]}
    converted = convert(sample)
    assert converted['images'] == sample['images']
    predicted = [m for m in converted['messages'] if m['role'] in {'assistant', 'tool_call'}]
    assert [m['loss'] for m in predicted] == [False, False, True, True]
    assert 'Fixture reasoning' in predicted[0]['content']
    assert all('loss' not in m for m in converted['messages'] if m['role'] in {'user', 'tool_response'})


def test_endpoint_and_config_boundaries():
    check_endpoint('http://127.0.0.1:8000/v1')
    with pytest.raises(ValueError):
        check_endpoint('https://example.org/v1?api_key=fixture')
    with pytest.raises(ValueError):
        validate_config({'protocol': 'raw', 'request_params': {'model': 'x', 'api_key': 'fixture', 'max_tokens': 100}})


def test_disjoint_shards_and_overlap_rejection(tmp_path):
    class D:
        def ids(self): return iter(['a_01', 'a_02', 'a_03', 'a_04'])
    picks = [selection(D(), Namespace(ids=None, limit=None, shard_index=i, shards=3)) for i in range(3)]
    assert sorted(sum(picks, [])) == list(D().ids())
    run = tmp_path / 'one'
    write_json(run / 'run.json', {'config': {'label': 'fixture'}, 'ids': ['a_01']})
    write_json(run / 'planned.json', [{'id': 'a_01'}])
    with pytest.raises(ValueError):
        merge(Namespace(runs=[str(run), str(run)], output=str(tmp_path / 'bad')))
    assert not (tmp_path / 'bad').exists()


def test_all_released_modules_import_without_running_a_model():
    root = Path(__file__).resolve().parents[1] / 'src'
    for path in sorted(root.rglob('*.py')):
        ast.parse(path.read_text())
        relative = path.relative_to(root).with_suffix('')
        parts = list(relative.parts)
        if parts[-1] == '__init__':
            parts.pop()
        if parts:
            importlib.import_module('.'.join(parts))


def test_preserved_source_identities():
    import hashlib
    root = Path(__file__).resolve().parents[1]
    for record in json.loads((root / 'research_docs/source_provenance.json').read_text()):
        assert hashlib.sha256((root / record['path']).read_bytes()).hexdigest() == record['released_sha256']
