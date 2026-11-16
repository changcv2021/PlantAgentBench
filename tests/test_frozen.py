"""Representative published Core integration. No model calls or old-result rescoring."""
import hashlib
import json
import os
from pathlib import Path
from argparse import Namespace
import base64

import pytest

from plantbench.agent_harness import ReplayAdapter
from plantbench.aggregation import EvaluationAggregates
from plantbench.evaluation_tables import render_tables
from plantbench.knowledge import KnowledgeResolver
from plantbench_public.cli import planned_row
from plantbench_release import Dataset


@pytest.fixture(scope='module')
def dataset():
    root = os.environ.get('PAB_TEST_DATASET')
    if not root:
        pytest.skip('Set PAB_TEST_DATASET to the pinned Core snapshot')
    ds = Dataset(root)
    yield ds
    ds.close()


def test_each_core_template_real_media_tools_and_scoring(dataset, tmp_path):
    ds = dataset
    assert ds.release['agent_count'] == 3000
    templates = ds.release['representative_ids_by_template']
    assert len(templates) == 13
    rows = [planned_row(ds, ident) for ident in templates.values()]
    agg = EvaluationAggregates(rows, model='SCRIPTED INTEGRATION ONLY', mode='offline_protocol_replay')
    media_count = 0
    for template, ident in templates.items():
        ep = ds.episode(ident)
        if ep.agentic.primary_p == 'P4':
            assert '2.1' in ep.task.report_protocol
        resolver = ds.resolver(ep)
        for e in ep.evidence:
            if e.payload.kind == 'media':
                data = resolver.read(ident, e.payload.media_id, e.payload.media_sha256)
                assert hashlib.sha256(data).hexdigest() == e.payload.media_sha256
                media_count += 1
        resolver.close()
        if ep.knowledge_corpus:
            assert KnowledgeResolver(ep.knowledge_corpus).all_units()
        for target in ep.catalog:
            if target.release_phase or target.prerequisite_evidence_refs:
                continue
            env, _, _ = ds.environment(ident, result_origin='scripted_validation')
            try:
                env.step(json.dumps({'action': target.action_type, 'target_id': target.target_id}), request_id='fixture')
                assert env._private_trace()['events'][-1]['public_error'] is None
            finally:
                env.close()
        result = ds.run(ident, ReplayAdapter([{'action': 'STOP'}]), run_id='fixture_' + template)
        score = ds.score(ident, result['trace'])
        assert score['result_origin'] == 'scripted_validation'
        assert score['task_success'] == 0
        agg.add(ident, 'completed', score)
    assert media_count > 0
    render_tables([agg.report()], tmp_path, figures=True)
    assert len(list(tmp_path.glob('table*.csv'))) == 4
    assert (tmp_path / 'milestone_breakdown.csv').exists()
    assert list(tmp_path.glob('milestones_*.svg'))


@pytest.mark.parametrize('protocol', ['raw', 'structured', 'native'])
def test_cli_saved_response_resume_and_separate_scoring(dataset, tmp_path, monkeypatch, protocol):
    from plantbench_public.cli import evaluate, score, summarize, read_json, write_json, task_path
    from plantbench_public.adapters import JSONAdapter
    from plantbench_public.native.adapter import KimiToolAdapter
    from plantbench_public.native.kimi_client import KimiClient
    # Mock transport; these are explicitly scripted fixtures, never model results.
    monkeypatch.setattr(JSONAdapter, 'origin', 'scripted_validation')
    monkeypatch.setattr(KimiToolAdapter, 'origin', 'scripted_validation')
    calls = []
    def post(self, body):
        calls.append(body)
        message = {'role': 'assistant', 'content': json.dumps({'action': 'STOP'})}
        if protocol == 'native':
            message = {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'fixture-call', 'type': 'function', 'function': {'name': 'STOP', 'arguments': '{}'}}]}
        envelope = {'choices': [{'message': message, 'finish_reason': 'stop'}], 'model': 'fixture'}
        return {'http_status': 200, 'response_base64': base64.b64encode(json.dumps(envelope).encode()).decode(),
                'elapsed_seconds': 0, 'headers': {}}
    monkeypatch.setattr(KimiClient, '_post', post)
    ident = dataset.release['representative_ids_by_template']['C01']
    ids = tmp_path / 'ids.txt'
    ids.write_text(ident + '\n')
    config = tmp_path / 'config.json'
    write_json(config, {'label': 'SCRIPTED CLI FIXTURE', 'protocol': protocol,
                       'request_params': {'model': 'fixture', 'max_tokens': 100, 'seed': 'harness'}})
    run = tmp_path / 'run'
    args = Namespace(dataset=str(dataset.root), config=str(config), api_base='http://127.0.0.1:8000/v1',
                     api_key_env=None, output=str(run), ids=str(ids), limit=None, shards=1, shard_index=0,
                     transport_attempts=1, timeout=30, on_error='stop')
    evaluate(args)
    assert len(calls) == 1
    assert not (run / 'scores').exists()
    evaluate(args)
    assert len(calls) == 1  # completed task is not requested twice
    score(Namespace(dataset=str(dataset.root), run=str(run)))
    saved = read_json(task_path(run, ident, 'scores'))
    assert saved['result_origin'] == 'scripted_validation'
    with pytest.raises(ValueError, match='actual model score'):
        summarize(Namespace(runs=[str(run)], output=str(tmp_path / 'must-not-report'), no_figures=True))
