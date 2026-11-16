"""Convert the merged Kimi-teacher SFT set to ms-swift jsonl and split train/val by isolation_group.

A sample goes to val when sha256(isolation_group) mod 1000 < val_permille, so samples sharing a group never
straddle the split. Training files carry only swift's columns (messages, images, tools); the per-line
provenance (id, P, verdict, attempt, group) is written alongside as *.meta.jsonl in the same order.
"""
import argparse
import collections
import hashlib
import json
import os

from .to_swift import convert


def is_val(group, permille):
    return int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 1000 < permille


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--sft', required=True)
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--val-permille', type=int, default=30)
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)
    files = {k: (open(os.path.join(args.out_dir, k + '.jsonl.tmp'), 'w'),
                 open(os.path.join(args.out_dir, k + '.meta.jsonl.tmp'), 'w')) for k in ('train', 'val')}
    stats = collections.defaultdict(collections.Counter)
    groups = collections.defaultdict(set)
    for line in open(args.sft):
        sample = json.loads(line)
        split = 'val' if is_val(sample['isolation_group'], args.val_permille) else 'train'
        rec = convert(sample)
        data, meta = files[split]
        data.write(json.dumps({k: rec[k] for k in ('messages', 'images', 'tools')}, ensure_ascii=False) + '\n')
        meta.write(json.dumps({'id': sample['id'], 'primary_p': sample['primary_p'], 'verdict': sample['verdict'],
                               'attempt': sample.get('attempt'), 'isolation_group': sample['isolation_group']}) + '\n')
        stats[split][sample['primary_p']] += 1
        stats[split]['total'] += 1
        groups[split].add(sample['isolation_group'])
    for k, (data, meta) in files.items():
        data.close()
        meta.close()
        for suffix in ('.jsonl', '.meta.jsonl'):
            os.replace(os.path.join(args.out_dir, k + suffix + '.tmp'), os.path.join(args.out_dir, k + suffix))
    assert not groups['train'] & groups['val'], 'isolation_group leaked across the split'
    report = {'source': args.sft, 'val_permille': args.val_permille,
              'by_split': {k: dict(sorted(v.items())) for k, v in stats.items()},
              'groups': {k: len(v) for k, v in groups.items()}}
    json.dump(report, open(os.path.join(args.out_dir, 'split_report.json'), 'w'), indent=1)
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
