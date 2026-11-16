# Thinking–Action supervised fine-tuning

The code and recipe document the **completed** Kimi-K2.6 → Qwen3.5-9B experiment.
Full teacher trajectories and fine-tuned weights are separate artifacts, not
bundled here. This release does not claim an implemented RL experiment.

| Task | Collection pool | Qualified | Train | Internal validation |
|---|---:|---:|---:|---:|
| P1 | 1,000 | 1,000 | 1,000 | 0 |
| P2 | 1,500 | 1,496 | 1,426 | 70 |
| P3 | 2,000 | 1,543 | 1,489 | 54 |
| P4 | 4,000 | 1,554 | 1,508 | 46 |
| P5 | 1,500 | 1,372 | 1,318 | 54 |
| Total | 10,000 | **6,965** | **6,741** | **224** |

There are 6,820 strict trajectories and 145 recovered trajectories with invalid
assistant turns masked. Internal validation uses group hashing, not P-stratified
sampling; P1 has zero internal validation examples.

## Supervision

Each complete multi-turn episode is **one training sample**. Inputs contain the
public contract, observations, images and previous interactions. Supervision
covers exposed teacher reasoning, tool names, arguments, assessments and the
final SUBMIT. Environment feedback is input context, not a prediction target.

System/user/tool and image representation tokens receive no loss. Invalid
assistant turns retain context but mask both thinking and actions. The
`ignore_think_prefix` setting masks the opening prefix, not all thinking text.
Vision encoder and aligner are frozen; images still condition language training.

The exact filter, `plantbench_training.quality.quality`, requires submitted
success, necessary milestones and grounding. Missing reasoning, truncated output,
non-single-call structure and unreceived citations reject a trajectory.
`qwen_mask.py` builds reference token labels; `check_swift_align.py` checks them
against the actual ms-swift encoding.

```text
teacher trajectory + quality verdict
 → qwen_sft.convert → to_swift.convert
 → group-separated train/validation → language-side full SFT
```

The teacher converter expects the original `context`, `steps`, `task_meta` and
`split` collection schema. Public evaluation traces preserve model turns but are
not automatically that training schema. Supply the teacher export or a checked
conversion; do not reinterpret ordinary final answers as teacher reasoning.

For a prepared, isolated teacher JSONL:

```bash
python -m plantbench_training.build_dataset \
  --sft teacher.reasoning.jsonl --out-dir training-data --val-permille 30
python -m plantbench_training.check_swift_align \
  --sft teacher.reasoning.jsonl --model /path/to/pinned/Qwen3.5-9B
```

Run token/image checks in a compute allocation with the recorded training stack.
The alignment check treats the known ms-swift end-of-message newline difference
separately.

## Actual training configuration

| Item | Setting |
|---|---|
| Base | Qwen/Qwen3.5-9B; revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a` |
| Method | Language-side full SFT; vision and aligner frozen; no LoRA |
| Data | 6,741 train / 224 validation full trajectories |
| Epochs / steps | 2 / 844 |
| LR / scheduler | 1e-5 / cosine; warmup ratio 0.03 |
| Optimizer | `adamw_torch_fused`, mapped to DeepSpeedCPUAdam in AdamW mode by optimizer offload; betas 0.9/0.95; epsilon 1e-8; weight decay 0.1 |
| Batch | Per-device 1; accumulation 4; effective global 16 |
| Hardware / parallelism | 16 A100 40 GB, 4 nodes; sequence parallel 4; data parallel 4 |
| Sequence | 65,536 tokens; padding-free; no packing; overlong samples deleted |
| Memory | BF16, FlashAttention, checkpointing, Liger, ZeRO-3 optimizer CPU offload |
| Images | 64–16,384 image tokens |
| Loss | `ignore_think_prefix`; valid assistant reasoning and action spans |
| Seed / clipping | 42 / 1.0 |
| Checkpoint | Step 844, export `export-844` |
| Recorded duration | 13 h 00 min 25 s |

`environment.txt` records software versions. `train.sh` replaces operator paths
and distributed launch variables and uses effective values of repeated original
flags: accumulation **4** and optimizer-offload config. Set MODEL_PATH, DATA_DIR,
OUTPUT_DIR and distributed variables, then launch once per allocated node. The
script does not submit jobs or set a lab-wide GPU allowance. Changed parallelism
changes the effective batch and defines a new configuration.

Base and Ours evaluate autonomously in the same PlantEnv/native interface with
Thinking and the same 32,768-token budget/sampling settings. Weights and registered
sampling namespaces differ. Use the Thinking config with distinct services and
run labels, never replay teacher actions as model evaluation.
