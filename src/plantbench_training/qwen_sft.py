"""Raw Teacher Trajectory -> Qwen3.5 thinking-mode multimodal SFT sample.

Message layout (OpenAI/HF chat format consumed by Qwen3.5's chat_template.jinja):
  system     harness system prompt + function-calling note       (context)
  user       public action schema                                (context)
  user       initial PlantEnv observation, text + images         (context)
  assistant  reasoning_content=<Kimi reasoning>, tool_calls=[1]  (loss if trainable)
  tool       PlantEnv result for that call, text + images        (context)
  ...
  assistant  final SUBMIT call carrying the report               (loss)
All environment feedback is a `tool` message, so the Qwen template treats the
episode as one user query and keeps every step's <think> block in context,
exactly as the teacher kept its own reasoning (thinking.keep="all").
Only assistant turns may carry loss; gold and scores are never in messages.
"""
import argparse
import glob
import json
import os

from .io import read_json_gz

SFT_VERSION = 'qwen35-thinking-tools-sft-1.0.0'


def blocks(refs, image_root, images):
    out = []
    for ref in refs:
        if ref['type'] == 'image':
            path = os.path.join(image_root, ref['path'])
            images.append(path)
            out.append({'type': 'image', 'image': path})
        else:
            out.append({'type': 'text', 'text': ref['text']})
    return out


def convert(traj, quality, image_root, *, variant='reasoning', include_masked=True):
    if variant not in {'reasoning', 'action_only'}:
        raise ValueError(variant)
    ctx = traj['context']
    images = []
    messages = [{'role': 'system', 'content': ctx['system_prompt']},
                {'role': 'user', 'content': blocks(ctx['public_action_schema_message'], image_root, images)},
                {'role': 'user', 'content': blocks(ctx['observations'][0], image_root, images)}]
    trainable = {t['index']: t['trainable'] for t in quality['turns']}
    for step in traj['steps']:
        m = step['model']
        calls = []
        for call in m['tool_calls'] or []:
            fn = call['function']
            calls.append({'type': 'function', 'function': {'name': fn['name'], 'arguments': json.loads(fn['arguments'] or '{}')}})
        if len(calls) != 1:
            raise ValueError('Only single-tool-call turns are convertible (quality filter rejects others)')
        loss = bool(trainable.get(step['index']))
        if not loss and not include_masked:
            raise ValueError('Trajectory has masked turns')
        messages.append({'role': 'assistant',
                         'reasoning_content': (m['reasoning_content'] or '') if variant == 'reasoning' else '',
                         'content': m['content'] or '', 'tool_calls': calls, 'loss': loss})
        nxt = step.get('observation_after_index')
        if nxt is not None:
            messages.append({'role': 'tool', 'content': blocks(ctx['observations'][nxt], image_root, images)})
    if messages[-1]['role'] != 'assistant' or messages[-1]['tool_calls'][0]['function']['name'] != 'SUBMIT':
        raise ValueError('Trajectory does not end with the assistant SUBMIT')
    return {'id': traj['run_id'] + ':' + traj['episode_id'] + ':' + variant, 'sft_version': SFT_VERSION, 'variant': variant,
            'episode_id': traj['episode_id'], 'teacher_run_id': traj['run_id'], 'verdict': quality['verdict'],
            'primary_p': traj['task_meta']['primary_p'], 'source': traj['task_meta']['source'],
            'isolation_group': traj['split']['isolation_group'], 'partition': traj['split']['partition'],
            'tools': ctx['tools'], 'messages': messages, 'images': images,
            'loss_policy': 'assistant_messages_with_loss_true_only; system/user/tool/images never trained; '
                           'span = text after the generation prompt "<|im_start|>assistant\\n<think>\\n" through <|im_end|>'}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--verdicts', default='accepted_strict,accepted_masked')
    ap.add_argument('--variant', default='reasoning', choices=['reasoning', 'action_only'])
    args = ap.parse_args(argv)
    cfg = json.load(open(args.config))
    keep = set(args.verdicts.split(','))
    traj_root = os.path.join(cfg['work_dir'], 'trajectories', cfg['run_id'])
    score_root = os.path.join(cfg['work_dir'], 'scores', cfg['run_id'])
    out_path = os.path.join(cfg['work_dir'], 'sft', cfg['run_id'] + '.' + args.variant + '.jsonl')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    counts = {}
    with open(out_path + '.tmp', 'w') as out:
        for spath in sorted(glob.glob(os.path.join(score_root, '*', '*.json.gz'))):
            rec = read_json_gz(spath)
            verdict = rec['quality']['verdict']
            counts[verdict] = counts.get(verdict, 0) + 1
            if verdict not in keep:
                continue
            eid = rec['summary']['episode_id']
            traj = read_json_gz(os.path.join(traj_root, eid[2:4], eid + '.json.gz'))
            out.write(json.dumps(convert(traj, rec['quality'], os.path.join(cfg['work_dir'], 'images'), variant=args.variant),
                                 ensure_ascii=False) + '\n')
    os.replace(out_path + '.tmp', out_path)
    print(json.dumps({'output': out_path, 'verdict_counts': counts, 'exported_verdicts': sorted(keep)}))


if __name__ == '__main__':
    main()
