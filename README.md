# PlantAgentBench — Recorded Agent Demo

**Watch a plant agent investigate, acquire evidence, and decide.**

[Open the demo](https://changcv2021.github.io/PlantAgentBench/)

This static viewer presents **50 shared cases**, with **501 existing recordings across 11 model configurations**. Switch models on the same case: **Ours · Qwen3.5-9B** (green), **open-weight models** (blue), and **closed-source models** (purple). Ours is Qwen3.5-9B fine-tuned on Kimi2.6 trajectories, checkpoint `export-844`.

Models with all 50 recordings: Ours, Qwen3.5-9B (native tool calls), Qwen3.5-9B (JSON actions), Qwen3.5-27B, Kimi2.6, Gemma4-31B, Claude Opus 5.5, GPT-5.4, GPT-6-Astra, and Grok 4.7. Muse Glimmer 30B has **1/50** matched recordings; all other cases are explicitly unavailable. These counts describe this gallery, not whole-benchmark completion.

There are 10 cases per task family:

| Task | What the replay demonstrates |
|---|---|
| P1 · Growth monitoring | Comparing recorded measurements and investigating growth trends |
| P2 · Environment investigation | Querying environmental records against a stated task criterion |
| P3 · Active diagnosis | Selecting visual and contextual evidence for a plant condition |
| P4 · Care decisions | Consulting evidence and references to form a care or response decision |
| P5 · Longitudinal follow-up | Recording an assessment, revealing later archived records, and reassessing |

## Explore a recording

Choose a model, task tab and case. Switching the model keeps the same case and resets the replay to the initial observation. Use **Next**, **Back**, the timeline, or **Play** to follow the run. Each step shows the recorded action, environment feedback, current evidence, remaining budget, and stage. Expand the model's recorded reasoning or exact action when useful. Images can be enlarged; the final report includes clickable evidence references. A replay can be downloaded as JSON.

**Case photos are visible immediately**, beside the recorded investigation. This gallery contains all images registered in that frozen episode, including images the model did not request. Each photo indicates when the agent opened it, or that it was not opened. Click a photo to enlarge it or jump to its recorded inspection step. Record-only episodes explicitly state that they have no images. The page opens on a P3 visual case by default.

The replay observation panels render only evidence received by that replay position. The separate visitor photo gallery is a preview and does not change the recorded agent observations. All steps are saved locally in the demo assets, so this is an explanatory replay, not a live or secure evaluation environment.

## What this release does and does not include

- The frontend code and each available model recording of the fixed 50 cases are included. Failures are retained; missing recordings are never replaced by another model’s output.
- These are curated cases from frozen **Core-3K v2**. Run protocols differ and are labelled. The native tool-call Qwen3.5-9B base is shown separately from the JSON-action configuration. They are **not** a random sample, a new evaluation, or an estimate of overall model accuracy.
- Recorded model reasoning is model-generated text, not a guarantee that its biological claims are correct. Success means the existing task evaluator accepted the submission.
- Private Gold, source-reference answer tables, the full benchmark, raw API envelopes, credentials, and model weights are not part of this release.
- Images use the exact frozen benchmark display bytes. The photo gallery includes both requested images and registered images not requested during the saved run, with explicit labels. Source records may already contain documented aggregation or processing. The environment does not simulate plant growth or care outcomes.
- `demo_manifest.json` lists the active 50 episode IDs and preserves the IDs exposed by previous demo versions. They should no longer be treated as unexposed evaluation examples. This export does not change the original frozen benchmark or its prior results.

## Run locally

Open `docs/index.html` in a modern browser. No Node installation, build step, API key, or model server is required. The site uses local scripts and media and makes no inference requests. It can also be served by any ordinary static web server.

GitHub Pages publishes the `docs/` directory on the `main` branch. Relative asset paths work under the project URL and when opening the page locally.

## Planned: photo-based P3 / P4 experience

The next phase is a model release and a GPU-backed Hugging Face Space where visitors can provide a plant image and their own question. The intended initial scope is **P3 diagnosis** and **P4 care decisions**, since these are approachable with an image and a short description.

P1, P2, and P5 typically require measurements, environmental context, or records over time. A single uploaded image does not supply that context. Live uploads and hosted inference are **not enabled** in this release; no Hugging Face endpoint is connected.

## Attribution and licensing

Original viewer code is MIT licensed. The included source-derived evidence and replay data are governed separately; see [DATA_TERMS.md](DATA_TERMS.md). In particular, MIRAGE-derived materials retain CC BY-SA 4.0 terms. The code license does not relicense upstream images, text, or knowledge entries.

[中文说明](README_zh.md)

## Fixed case selection (retained from v4)

45 cases from the v3 gallery are retained. Five P2 cases are replaced with original image-bearing Eschikon investigations from the same frozen Core-3K v2 and saved SFT run. Selection allows unsuccessful outcomes; each case displays its actual saved task outcome. A run without a final report is labelled explicitly. No new model inference or scoring is performed.

P1 remains record-based. P2 contains five image-bearing and five record-only cases. In all five image-bearing Ours runs, the agent used QUERY → QUERY → SUBMIT and did not inspect the images; those images are explicitly labelled as unobserved. Their saved task outcomes are successful. Images the model did not open are labelled as visitor previews. See [the selection table](DEMO_SELECTION.md) for registered and observed image counts.

Eschikon materials are included for this non-commercial research demonstration under the source's **In Copyright – Non-Commercial Use Permitted** statement. They are excluded from the CC BY-SA replay grant and the MIT code license; see [DATA_TERMS.md](DATA_TERMS.md). Original outputs, scores and frozen tasks remain unchanged.

## Multi-model replay (v5)

Every recording retains its own actions, outcome, final report, image visibility and query cost. Recorded reasoning is displayed only when available; returned reasoning summaries are labelled as summaries. The gallery does not reconstruct hidden reasoning. Different protocols and curated cases prevent interpreting this as a controlled model ranking. No inference, rescoring, Gold edits or task changes were performed.
