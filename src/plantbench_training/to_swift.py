"""Kimi-teacher Qwen SFT sample (kimi_teacher.qwen_sft) -> ms-swift 4.x messages format.

  system      -> system
  user        -> user, image blocks become '<image>' in order
  assistant   -> assistant '<think>\\n{reasoning}\\n</think>\\n\\n{content}' + tool_call message,
                 both carrying the turn's `loss` flag (swift merges them into one response)
  tool        -> tool_response
User and assistant text is trimmed exactly as chat_template.jinja does (content|trim).
`images` lists every image path in order of appearance; `tools` is the JSON tool list.
"""
import argparse
import json

USER_TURN_SEP = '<|im_end|>\n<|im_start|>user\n'


def text_of(content, images):
    if isinstance(content, str):
        return content
    out = []
    for block in content:
        if block['type'] == 'image':
            images.append(block['image'])
            out.append('<image>')
        else:
            out.append(block['text'])
    return ''.join(out)


def convert(sample):
    images, messages = [], []
    for m in sample['messages']:
        role = m['role']
        if role == 'user' and messages and messages[-1]['role'] == 'user':
            # swift pairs (query, response) and would concatenate consecutive user messages with no
            # separator; the HF template renders them as two turns, so restore the turn boundary.
            messages[-1]['content'] += USER_TURN_SEP + text_of(m['content'], images).strip()
        elif role == 'user':
            messages.append({'role': role, 'content': text_of(m['content'], images).strip()})
        elif role == 'system':
            messages.append({'role': role, 'content': text_of(m['content'], images)})
        elif role == 'assistant':
            reasoning = (m.get('reasoning_content') or '').strip()
            content = '<think>\n' + reasoning + '\n</think>\n\n' + (m.get('content') or '').strip()
            messages.append({'role': 'assistant', 'content': content, 'loss': bool(m['loss'])})
            for call in m['tool_calls']:
                fn = call['function']
                messages.append({'role': 'tool_call', 'loss': bool(m['loss']),
                                 'content': json.dumps({'name': fn['name'], 'arguments': fn['arguments']},
                                                       ensure_ascii=False)})
        elif role == 'tool':
            messages.append({'role': 'tool_response', 'content': text_of(m['content'], images)})
        else:
            raise ValueError(role)
    assert images == sample['images'], 'image order changed'
    return {'id': sample['id'], 'messages': messages, 'images': images,
            'tools': json.dumps(sample['tools'], ensure_ascii=False),
            'primary_p': sample['primary_p'], 'isolation_group': sample['isolation_group']}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args(argv)
    n = 0
    with open(args.out + '.tmp', 'w') as out:
        for line in open(args.src):
            if args.limit and n >= args.limit:
                break
            out.write(json.dumps(convert(json.loads(line)), ensure_ascii=False) + '\n')
            n += 1
    import os
    os.replace(args.out + '.tmp', args.out)
    print(json.dumps({'out': args.out, 'samples': n}))


if __name__ == '__main__':
    main()
