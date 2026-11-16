"""Build and verify Qwen3.5 SFT labels with the real processor and chat template.

Spans: for each assistant message i with loss=True, the trained text is
  render(messages[:i+1])[len(render(messages[:i], add_generation_prompt=True)) : end of its <|im_end|>]
i.e. reasoning + '</think>' + content + <tool_call> + <|im_end|>; the generation
prompt '<|im_start|>assistant\\n<think>\\n' itself is not trained (inference
supplies it). Text is cut into (segment, trainable) pieces; each piece is
tokenized with image pads expanded by the real image processor, and the
concatenation must equal the processor's own input_ids for the whole sample.
"""
import argparse
import json
import sys

IGNORE = -100


def hf_messages(sample):
    out = []
    for m in sample['messages']:
        m = {k: v for k, v in m.items() if k != 'loss'}
        out.append(m)
    return out


def build(processor, sample):
    from PIL import Image
    tok = processor.tokenizer
    msgs, tools = hf_messages(sample), sample['tools']
    render = lambda ms, gen=False: processor.apply_chat_template(ms, tools=tools, tokenize=False, add_generation_prompt=gen)
    full = render(msgs)
    spans = []
    for i, m in enumerate(sample['messages']):
        if m['role'] != 'assistant' or not m.get('loss'):
            continue
        prefix = render(msgs[:i], gen=True)
        if not full.startswith(prefix):
            raise AssertionError('Chat template is not prefix-consistent at message %d' % i)
        end = full.index('<|im_end|>', len(prefix)) + len('<|im_end|>')
        spans.append((len(prefix), end, i))
    pieces, cursor = [], 0
    for a, b, i in spans:
        pieces.append((full[cursor:a], False, None))
        pieces.append((full[a:b], True, i))
        cursor = b
    pieces.append((full[cursor:], False, None))
    images = [Image.open(p).convert('RGB') for p in sample['images']]
    pad = '<|image_pad|>'
    if full.count(pad) != len(images):
        raise AssertionError('Image placeholder count %d != images %d' % (full.count(pad), len(images)))
    grid = processor.image_processor(images=images, return_tensors='pt')['image_grid_thw'] if images else []
    merge = processor.image_processor.merge_size ** 2
    counts = [int(g.prod()) // merge for g in grid]
    ids, labels, owner, k = [], [], [], 0
    for text, train, idx in pieces:
        n = text.count(pad)
        if train and n:
            raise AssertionError('Image inside a trained span')
        expanded = text
        if n:
            parts = text.split(pad)
            expanded = parts[0] + ''.join(pad * counts[k + j] + parts[j + 1] for j in range(n))
            k += n
        piece = tok(expanded, add_special_tokens=False)['input_ids']
        ids += piece
        labels += piece if train else [IGNORE] * len(piece)
        owner += [idx] * len(piece)
    ref = processor(text=[full], images=images or None, return_tensors='pt')['input_ids'][0].tolist()
    if ref != ids:
        bad = next((j for j, (x, y) in enumerate(zip(ref, ids)) if x != y), min(len(ref), len(ids)))
        raise AssertionError('Segmented tokenization differs from processor at token %d (len %d vs %d)' % (bad, len(ref), len(ids)))
    return {'full_text': full, 'input_ids': ids, 'labels': labels, 'owner': owner, 'image_token_counts': counts, 'spans': spans}


def check(processor, sample, built):
    tok = processor.tokenizer
    ids, labels = built['input_ids'], built['labels']
    image_id = tok.convert_tokens_to_ids('<|image_pad|>')
    report = {'id': sample['id'], 'tokens': len(ids), 'trained_tokens': sum(l != IGNORE for l in labels),
              'image_tokens': sum(t == image_id for t in ids), 'images': len(sample['images']),
              'image_tokens_trained': sum(t == image_id and l != IGNORE for t, l in zip(ids, labels)),
              'turns': []}
    assert report['image_tokens_trained'] == 0
    for a, b, i in built['spans']:
        msg = sample['messages'][i]
        span_ids = [t for t, o in zip(ids, built['owner']) if o == i]
        text = tok.decode(span_ids)
        reasoning = (msg.get('reasoning_content') or '').strip()
        call = msg['tool_calls'][0]['function']
        ok = {'starts_with_reasoning': text.startswith(reasoning[:200]) if reasoning else text.startswith('\n</think>'),
              'contains_think_close': '</think>' in text,
              'contains_tool_call': ('<tool_call>\n<function=' + call['name'] + '>') in text,
              'ends_with_im_end': text.endswith('<|im_end|>'),
              'no_user_or_tool_text': '<|im_start|>' not in text and '<tool_response>' not in text}
        report['turns'].append({'message_index': i, 'action': call['name'], 'tokens': len(span_ids), **ok,
                                'head': text[:160], 'tail': text[-200:]})
        assert all(ok.values()), (i, ok)
    trained_msgs = {i for _, _, i in built['spans']}
    expected = {i for i, m in enumerate(sample['messages']) if m['role'] == 'assistant' and m.get('loss')}
    assert trained_msgs == expected
    # Every non-assistant token is masked: trained tokens belong to some span owner.
    assert all((l == IGNORE) == (o is None) for l, o in zip(labels, built['owner']))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--sft', required=True)
    ap.add_argument('--model', required=True)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--shard', default='0/1', help='k/n: check samples whose line number %% n == k')
    ap.add_argument('--render', help='write a human-readable masked rendering of the first sample here')
    args = ap.parse_args(argv)
    shard_k, shard_n = map(int, args.shard.split('/'))
    from transformers import AutoProcessor
    processor = AutoProcessor.from_pretrained(args.model)
    reports = []
    for n, line in enumerate(open(args.sft)):
        if args.limit and n >= args.limit:
            break
        if n % shard_n != shard_k:
            continue
        sample = json.loads(line)
        built = build(processor, sample)
        reports.append(check(processor, sample, built))
        if args.render and n == 0:
            tok = processor.tokenizer
            out, cur, buf = [], None, []
            for t, l in zip(built['input_ids'], built['labels']):
                state = l != IGNORE
                if state != cur and buf:
                    out.append(('[[TRAIN]]' if cur else '[[CTX]]') + tok.decode(buf))
                    buf = []
                cur = state
                buf.append(t)
            out.append(('[[TRAIN]]' if cur else '[[CTX]]') + tok.decode(buf))
            text = '\n'.join(out)
            import re
            text = re.sub(r'(<\|image_pad\|>)+', lambda m: '<|image_pad|>x%d' % (len(m.group(0)) // len('<|image_pad|>')), text)
            open(args.render, 'w').write(text)
    print(json.dumps({'samples': len(reports), 'all_checks_passed': True, 'reports': reports}, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(main())
