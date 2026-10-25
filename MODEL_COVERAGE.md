# Saved open-model run coverage

Read-only verification of registered full-catalog runs and the same 50 public demo cases. No inference or rescoring was performed. Full-run progress is read from durable evaluation-status checkpoints; demo availability is verified by indexed task-ID queries across all eligible shards. Counts are completed tasks, not successful tasks. The full catalog contains 318,760 Agent episodes and 5,830 component tasks (324,590 total).

| Model | Completed formal tasks at saved checkpoint / full catalog | Available demo recordings | Status |
|---|---:|---:|---|
| Qwen3.5-9B | 324,590 / 324,590 | 50 / 50 | Full catalog completed |
| Qwen3.5-27B | 311,472 / 324,590 | 50 / 50 | Partial saved formal run |
| Gemma4-31B | 324,590 / 324,590 | 50 / 50 | Full catalog completed |
| Muse Glimmer 30B | 2,994 / 324,590 | 1 / 50 | Partial saved formal run |
| Llama 4 Scout | 0 / 324,590 | 0 / 50 | No completed formal tasks |
| Qwen3.8-Flash-Next | 0 / 324,590 | 0 / 50 | 26 completed integration-test cases; not formal results |

Qwen3.5-9B is the full-catalog JSON/thinking-off configuration (4,096 output tokens). Qwen3.5-9B (Thinking) is the native-tool-call/thinking Core-3K configuration (32,768 output tokens). Ours is fine-tuned from the thinking base. The runs also differ in sampling settings; this is not an isolated ablation of thinking mode.

Integration-test results are not substituted for formal recordings. Models with no formal match are absent from the replay picker. The 1/50 Muse recording is a partial saved run, not evidence that its full benchmark completed.
