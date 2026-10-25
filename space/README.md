---
title: PlantAgentBench · Try the Agent
emoji: 🌱
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: 6.30.0
python_version: 3.12
app_file: app.py
fullWidth: true
header: mini
license: mit
models:
  - Qwen/Qwen3.5-9B
tags:
  - plants
  - image-text-to-text
  - zerogpu
short_description: Upload a plant photo and ask a diagnosis or care question.
---

# PlantAgentBench — Try the Agent

Upload a plant photo, choose diagnosis or care, and ask your own question.
The app currently runs the **official Qwen3.5-9B in thinking mode**.
It does **not** currently run the fine-tuned **Ours** model in the recorded examples.

[Recorded examples](https://changcv2021.github.io/PlantAgentBench/#examples)

This is an open-ended photo consultation. The model sees your photo and question;
it has no benchmark evidence database, search tools, Gold answers, or task score.
Actual generated thinking is displayed separately from the final response.
An unfinished thinking response is reported as incomplete, never relabelled as a final answer.

The app does not write uploaded photos or questions to a dataset or application log.
Uploads are processed on Hugging Face; Gradio may keep temporary uploaded files, with
the app cache configured for cleanup. This is not a claim of immediate platform-wide deletion.

## Runtime

ZeroGPU is required, or a compatible dedicated GPU. The deployment uses BF16,
thinking enabled, up to 4,096 output tokens, and a bounded generation duration.
The time limit may end a response before a final answer. This app is not a benchmark run.
The official model is pinned to revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`.

To switch to the approved fine-tuned checkpoint, set Space variables `MODEL_ID`,
`MODEL_REVISION` (an immutable commit), and `MODEL_LABEL` together. Put access
credentials for a private model in the Space's `HF_TOKEN` secret. No credential
belongs in the source code or the GitHub Pages frontend. Verify a real image
request before changing the public website label to Ours.

The model weights retain the model owner's license. This Space's application code
is MIT licensed.
