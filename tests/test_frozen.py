"""Representative published Core integration. No model calls or old-result rescoring."""
import hashlib
import json
import os
from pathlib import Path

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
