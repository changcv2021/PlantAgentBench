# PlantAgentBench — Recorded Agent Demo

**Watch a plant agent investigate, acquire evidence, and decide.**

[Open the demo](https://changcv2021.github.io/PlantAgentBench/)

This initial release contains a static episode viewer and **50 real, successful recordings** from **Qwen3.5-9B fine-tuned on Kimi2.6-generated trajectories**, checkpoint `export-844`. There are 10 examples for each PlantAgentBench task family:

| Task | What the replay demonstrates |
|---|---|
| P1 · Growth monitoring | Comparing recorded measurements and investigating growth trends |
| P2 · Environment investigation | Querying environmental records against a stated task criterion |
| P3 · Active diagnosis | Selecting visual and contextual evidence for a plant condition |
| P4 · Care decisions | Consulting evidence and references to form a care or response decision |
| P5 · Longitudinal follow-up | Recording an assessment, revealing later archived records, and reassessing |

## Explore a recording

Choose a task tab and a case. Use **Next**, **Back**, the timeline, or **Play** to follow the run. Each step shows the recorded action, environment feedback, current evidence, remaining budget, and stage. Expand the model's recorded reasoning or exact action when useful. Images can be enlarged; the final report includes clickable evidence references. A replay can be downloaded as JSON.

The viewer renders only evidence received by that replay position. All steps are saved locally in the demo assets, so this is an explanatory replay, not a live or secure evaluation environment.

## What this release does and does not include

- The frontend code and 50 selected public replay exports are included.
- These are curated successes from frozen **Core-3K v2**, using native tool calls with thinking. They are **not** a random sample, a new evaluation, or an estimate of overall model accuracy.
- Recorded model reasoning is model-generated text, not a guarantee that its biological claims are correct. Success means the existing task evaluator accepted the submission.
- Private Gold, source-reference answer tables, the full benchmark, raw API envelopes, credentials, and model weights are not part of this release.
- Image bytes are the exact rendered images received during evaluation. Source records may already contain documented aggregation or processing. The environment does not simulate plant growth or care outcomes.
- `demo_manifest.json` lists all 50 exposed episode IDs. They should no longer be treated as unexposed evaluation examples. This export does not change the original frozen benchmark or its prior results.

## Run locally

Open `docs/index.html` in a modern browser. No Node installation, build step, API key, or model server is required. The site uses local scripts and media and makes no inference requests. It can also be served by any ordinary static web server.

GitHub Pages publishes the `docs/` directory on the `main` branch. Relative asset paths work under the project URL and when opening the page locally.

## Planned: photo-based P3 / P4 experience

The next phase is a model release and a GPU-backed Hugging Face Space where visitors can provide a plant image and their own question. The intended initial scope is **P3 diagnosis** and **P4 care decisions**, since these are approachable with an image and a short description.

P1, P2, and P5 typically require measurements, environmental context, or records over time. A single uploaded image does not supply that context. Live uploads and hosted inference are **not enabled** in this release; no Hugging Face endpoint is connected.

## Attribution and licensing

Original viewer code is MIT licensed. The included source-derived evidence and replay data are governed separately; see [DATA_TERMS.md](DATA_TERMS.md). In particular, MIRAGE-derived materials retain CC BY-SA 4.0 terms. The code license does not relicense upstream images, text, or knowledge entries.

[中文说明](README_zh.md)
