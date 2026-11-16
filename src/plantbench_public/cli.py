"""Portable entrypoints; model execution and Gold scoring are separate commands."""
import argparse
import fcntl
import gzip
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from plantbench_release import Dataset

CORE_REVISION = 'a6334d083abebddbd9b6dfdd05afb746b79ef562'
FULL_REVISION = '49a5fc79b7078d84f01d2143a813dbe73eaf9654'


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(temporary, 'wt', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.write('\n')
    temporary.replace(path)


def read_json(path):
    path = Path(path)
    with (gzip.open if path.suffix == '.gz' else open)(path, 'rt', encoding='utf-8') as stream:
        return json.load(stream)


def task_path(root, ident, category):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', ident):
        raise ValueError('Unsafe task ID')
    return Path(root) / category / ident[2:4] / (ident + '.json.gz')


def planned_row(ds, ident):
    row = ds.db.execute('SELECT source,primary_p,template,kind,case_group,independent_group FROM tasks WHERE id=?', (ident,)).fetchone()
    if row is None:
        raise KeyError(ident)
    ep = ds.episode(ident)
    return dict(id=ident, **dict(zip(['source', 'primary_p', 'template', 'kind', 'case_group', 'independent_group'], row)),
                basis=ep.agentic.answer_basis, split=ep.identity.split)


def selection(ds, args):
    if args.limit is not None and args.limit <= 0:
        raise ValueError('limit must be positive')
    if not 0 <= args.shard_index < args.shards:
        raise ValueError('Require 0 <= shard-index < shards')
    ids = list(ds.ids()) if not args.ids else [line.strip() for line in Path(args.ids).read_text().splitlines() if line.strip()]
    if len(ids) != len(set(ids)):
        raise ValueError('Repeated episode IDs')
    ids = sorted(i for i in ids if int(hashlib.sha256(i.encode()).hexdigest()[:16], 16) % args.shards == args.shard_index)
    return ids[:args.limit] if args.limit is not None else ids


def validate_config(config):
    if set(config) - {'label', 'protocol', 'request_params', 'image_channel', 'environment_seed', 'sampling_namespace'}:
        raise ValueError('Unknown configuration keys')
    if config['protocol'] not in {'raw', 'structured', 'native'}:
        raise ValueError('Unknown protocol')
    params = config['request_params']
    if not params.get('model') or set(params) & {'messages', 'tools', 'response_format', 'api_key', 'authorization', 'headers'}:
        raise ValueError('Model required; messages/tools/schema/credentials are not request parameters')
    if params.get('n', 1) != 1 or params.get('stream', False):
        raise ValueError('Only one non-streaming completion per action is supported')
    if max(params.get('max_tokens', 0), params.get('max_completion_tokens', 0)) <= 0:
        raise ValueError('Specify a finite positive output-token ceiling')


def check_endpoint(url):
    p = urlsplit(url)
    if p.username or p.password or p.query or p.fragment or not p.hostname:
        raise ValueError('Use a credential-free API base URL')
    if p.scheme != 'https' and not (p.scheme == 'http' and p.hostname in {'localhost', '127.0.0.1', '::1'}):
        raise ValueError('Remote endpoints require HTTPS; HTTP is supported only on loopback')


def evaluate(args):
    from plantbench.agent_harness import run_episode
    from plantbench.public_schema import action_schema
    from plantbench.structured_actions import response_format
    from .adapters import JSONAdapter
    from .native.adapter import KimiToolAdapter, ImageStore
    from .native.kimi_client import KimiClient, KimiStore, load_api_key
    config = read_json(args.config)
    validate_config(config)
    check_endpoint(args.api_base)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'MERGED.json').exists():
        raise ValueError('Merged reports are not inference resume directories')
    # One writer per output directory; independent shards use different dirs.
    with (out / '.writer.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ds = Dataset(args.dataset)
        store = None
        try:
            ids = selection(ds, args)
            planned = [planned_row(ds, i) for i in ids]
            identity = {'code_release': '1.0.0', 'config': config, 'api_base': args.api_base,
                        'release': ds.release, 'ids': ids, 'transport_attempts': args.transport_attempts,
                        'timeout': args.timeout, 'on_error': args.on_error}
            if (out / 'run.json').exists() and read_json(out / 'run.json') != identity:
                raise ValueError('Configuration/dataset/selection changed: use a new output directory')
            write_json(out / 'run.json', identity)
            write_json(out / 'planned.json', planned)
            key = load_api_key(args.api_key_env, None)
            client = KimiClient(args.api_base, key, timeout=args.timeout, max_attempts=args.transport_attempts)
            store = KimiStore(out / 'requests.sqlite', identity)
            for ident in ids:
                target = task_path(out, ident, 'trajectories')
                if target.exists():
                    print(ident, 'saved', flush=True)
                    continue
                if task_path(out, ident, 'errors').exists():
                    print(ident, 'previous_error; preserved', flush=True)
                    if args.on_error == 'stop':
                        raise RuntimeError('Prior error preserved; inspect it before choosing a new run')
                    continue
                ep = ds.episode(ident)
                if config['protocol'] == 'native':
                    adapter = KimiToolAdapter(client, store, ImageStore(str(out / 'images')),
                        public_action_schema=action_schema(ep.task), request_params=config['request_params'],
                        image_channel=config.get('image_channel', 'tool'))
                else:
                    adapter = JSONAdapter(client, store, request_params=config['request_params'],
                        response_format=response_format(task=ep.task) if config['protocol'] == 'structured' else None)
                resolver = ds.resolver(ep)
                try:
                    result = run_episode(ep, resolver, adapter, run_id=ident,
                        environment_seed=config.get('environment_seed', 0),
                        sampling_namespace=config.get('sampling_namespace', 'guide06-default'))
                    write_json(target, {'episode_id': ident, 'result': result, 'turns': adapter.turns})
                    print(ident, result['trace']['state'].get('termination_reason'), flush=True)
                except Exception as exc:
                    # Raw envelopes stay in the private request store; avoid echoing
                    # provider bodies or URLs into public console logs.
                    write_json(task_path(out, ident, 'errors'), {'episode_id': ident, 'error_type': type(exc).__name__,
                              'status': 'infrastructure_or_runner_error', 'turns': adapter.turns})
                    print(ident, type(exc).__name__, file=sys.stderr, flush=True)
                    if args.on_error == 'stop':
                        raise RuntimeError('Run stopped; inspect the private error record/request store') from None
                finally:
                    resolver.close()
        finally:
            if store is not None:
                store.close()
            ds.close()


def score(args):
    root = Path(args.run)
    if (root / 'MERGED.json').exists():
        raise ValueError('Score each original shard before merging')
    ds = Dataset(args.dataset)
    try:
        if ds.release != read_json(root / 'run.json')['release']:
            raise ValueError('Scoring release differs from the executed dataset')
        for row in read_json(root / 'planned.json'):
            path = task_path(root, row['id'], 'trajectories')
            if path.exists():
                target = task_path(root, row['id'], 'scores')
                if target.exists():
                    continue
                record = read_json(path)
                write_json(target, ds.score(row['id'], record['result']['trace']))
    finally:
        ds.close()


def summarize(args):
    from plantbench.aggregation import EvaluationAggregates
    from plantbench.evaluation_tables import render_tables
    reports = []
    for name in args.runs:
        root = Path(name)
        config = read_json(root / 'run.json')['config']
        agg = EvaluationAggregates(read_json(root / 'planned.json'), model=config['label'])
        for row in agg.planned.values():
            path = task_path(root, row['id'], 'scores')
            agg.add(row['id'], 'completed', read_json(path)) if path.exists() else agg.add(row['id'], 'not_completed')
        reports.append(agg.report())
    render_tables(reports, args.output, figures=not args.no_figures)


def merge(args):
    """Join already-scored, disjoint shards without rewriting their results."""
    import shutil
    output = Path(args.output)
    if output.exists():
        raise ValueError('Merge output must be new')
    inputs, combined, seen, identity = [], [], set(), None
    for name in args.runs:
        root = Path(name)
        run = read_json(root / 'run.json')
        common = {k: v for k, v in run.items() if k != 'ids'}
        if identity is None:
            identity = common
        if common != identity:
            raise ValueError('Shard settings or dataset differ')
        rows = read_json(root / 'planned.json')
        ids = {r['id'] for r in rows}
        if len(ids) != len(rows) or ids & seen or ids != set(run['ids']):
            raise ValueError('Overlapping or inconsistent shard membership')
        seen.update(ids)
        combined.extend(rows)
        inputs.append((root, rows))
    output.mkdir(parents=True)
    write_json(output / 'MERGED.json', {'report_only': True, 'source_runs': [str(p) for p, _ in inputs]})
    write_json(output / 'run.json', {**identity, 'ids': sorted(seen)})
    write_json(output / 'planned.json', sorted(combined, key=lambda r: r['id']))
    for root, rows in inputs:
        for row in rows:
            src = task_path(root, row['id'], 'scores')
            if src.exists():
                dst = task_path(output, row['id'], 'scores')
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('download', help='Download a frozen HF dataset snapshot, including offline Gold')
    p.add_argument('--dataset', choices=['core', 'full'], default='core')
    p.add_argument('--revision')
    p.add_argument('--output', required=True)
    p = sub.add_parser('inspect', help='Show release metadata or the public initial observation')
    p.add_argument('--dataset', required=True)
    p.add_argument('--episode')
    p = sub.add_parser('evaluate', help='Execute model actions; never open offline Gold')
    p.add_argument('--dataset', required=True)
    p.add_argument('--config', required=True)
    p.add_argument('--api-base', required=True)
    p.add_argument('--api-key-env', help='Environment variable containing credentials; omit for local vLLM')
    p.add_argument('--output', required=True)
    p.add_argument('--ids', help='One frozen episode ID per line')
    p.add_argument('--limit', type=int)
    p.add_argument('--shards', type=int, default=1)
    p.add_argument('--shard-index', type=int, default=0)
    p.add_argument('--timeout', type=int, default=600)
    p.add_argument('--transport-attempts', type=int, default=1)
    p.add_argument('--on-error', choices=['stop', 'continue'], default='stop')
    p = sub.add_parser('score', help='Offline formal evaluation of saved model traces')
    p.add_argument('--dataset', required=True)
    p.add_argument('--run', required=True)
    p = sub.add_parser('summarize', help='Generate formal tables, denominators and milestone heatmaps')
    p.add_argument('--runs', nargs='+', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--no-figures', action='store_true')
    p = sub.add_parser('merge', help='Merge disjoint scored shards for reporting only')
    p.add_argument('--runs', nargs='+', required=True)
    p.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    if args.command == 'download':
        from huggingface_hub import snapshot_download
        name = 'PlantAgentBench-Core3K' if args.dataset == 'core' else 'PlantAgentBench-Full'
        snapshot_download('benchmarkanon/' + name, repo_type='dataset', local_dir=args.output,
                          revision=args.revision or (CORE_REVISION if args.dataset == 'core' else FULL_REVISION))
    elif args.command == 'inspect':
        ds = Dataset(args.dataset)
        try:
            if not args.episode:
                print(json.dumps(ds.release, indent=2))
            else:
                env, observation, _ = ds.environment(args.episode, result_origin='scripted_validation')
                try:
                    view = observation.model_dump(mode='json')
                    for item in view['evidence']:
                        if item['payload']['kind'] == 'media':
                            item['payload']['media_base64'] = '<image bytes omitted>'
                    print(json.dumps(view, indent=2, ensure_ascii=False))
                finally:
                    env.close()
        finally:
            ds.close()
    else:
        {'evaluate': evaluate, 'score': score, 'summarize': summarize, 'merge': merge}[args.command](args)


if __name__ == '__main__':
    main()
