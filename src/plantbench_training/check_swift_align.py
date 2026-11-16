"""Token-level check: ms-swift's encoding of a converted sample must equal the reference built from
the HF processor + the model's chat_template.jinja (kimi_teacher.qwen_mask.build, verified on all 6965).

Compares input_ids and labels (-100 = no loss) exactly and reports the first difference of each kind
with decoded context from both sides. Samples: every --stride-th line plus all accepted_masked ones
(up to --masked), so turns with loss=false are covered.
"""
import argparse
import collections
import json
import sys

from .to_swift import convert


def first_diff(a, b):
    for j, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return j
    return None if len(a) == len(b) else min(len(a), len(b))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--sft', required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--stride', type=int, default=35)
    ap.add_argument('--masked', type=int, default=20)
    ap.add_argument('--shard', help='k/n: check EVERY line with line_number %% n == k (overrides --stride/--masked)')
    ap.add_argument('--show', type=int, default=3, help='print context for this many mismatches')
    ap.add_argument('--loss-scale', default='ignore_think_prefix', help='swift --loss_scale used for training')
    args = ap.parse_args(argv)
    from .qwen_mask import build
    from swift import get_processor, get_template
    processor = get_processor(args.model, model_type='qwen3_5')
    template = get_template(processor, template_type='qwen3_5', loss_scale=args.loss_scale)
    template.set_mode('train')
    tok = processor.tokenizer
    im_end_id = tok.convert_tokens_to_ids('<|im_end|>')
    nl_ids = tok('\n', add_special_tokens=False)['input_ids']
    assert len(nl_ids) == 1
    nl_id = nl_ids[0]

    picked, masked = [], 0
    shard = tuple(map(int, args.shard.split('/'))) if args.shard else None
    for n, line in enumerate(open(args.sft)):
        if shard:
            if n % shard[1] == shard[0]:
                picked.append(line)
        elif n % args.stride == 0:
            picked.append(line)
        elif masked < args.masked and '"verdict": "accepted_masked"' in line:
            picked.append(line)
            masked += 1

    stats, shown, rows = collections.Counter(), 0, []
    for line in picked:
        sample = json.loads(line)
        ref = build(processor, sample)
        enc = template.encode(convert(sample))
        ids, labels = list(enc['input_ids']), list(enc['labels'])
        # Known, benign difference: swift trains the '\n' of its '<|im_end|>\n' suffix. Generation stops at
        # <|im_end|>, so that token is never produced at inference. Count it, then mask it to compare the rest.
        for j in range(1, len(labels)):
            if (labels[j] != -100 and ids[j] == nl_id and ids[j - 1] == im_end_id
                    and j < len(ref['labels']) and ref['labels'][j] == -100):
                labels[j] = -100
                stats['suffix_newline_tokens'] += 1
        d_ids = first_diff(ref['input_ids'], ids)
        d_lab = first_diff(ref['labels'], labels)
        ok = d_ids is None and d_lab is None
        stats['ok' if ok else 'mismatch'] += 1
        stats['masked_samples' if sample['verdict'] == 'accepted_masked' else 'strict_samples'] += 1
        rows.append({'id': sample['id'], 'verdict': sample['verdict'], 'ok': ok, 'ref_len': len(ref['input_ids']),
                     'swift_len': len(ids), 'first_id_diff': d_ids, 'first_label_diff': d_lab,
                     'ref_trained': sum(l != -100 for l in ref['labels']), 'swift_trained': sum(l != -100 for l in labels)})
        if not ok and shown < args.show:
            shown += 1
            j = d_ids if d_ids is not None else d_lab
            lo = max(0, j - 40)
            print('=' * 30, sample['id'], 'first diff at token', j, '(ids)' if d_ids is not None else '(labels only)')
            print('--- REF   :', repr(tok.decode(ref['input_ids'][lo:j + 40])))
            print('--- SWIFT :', repr(tok.decode(ids[lo:j + 40])))
            if d_ids is None:
                print('--- REF labels  :', ['T' if l != -100 else '.' for l in ref['labels'][lo:j + 40]])
                print('--- SWIFT labels:', ['T' if l != -100 else '.' for l in labels[lo:j + 40]])
    print(json.dumps({'checked': len(picked), **stats}, indent=1))
    print(json.dumps(rows[:10], indent=1))
    return 0 if stats['mismatch'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
