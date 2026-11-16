# PlantAgentBench

**An interactive benchmark for plant investigation, diagnosis, care and follow-up.**

[Examples](https://changcv2021.github.io/PlantAgentBench/) ·
[Core-3K](https://huggingface.co/datasets/benchmarkanon/PlantAgentBench-Core3K) ·
[Full](https://huggingface.co/datasets/benchmarkanon/PlantAgentBench-Full) · [中文](README_zh.md)

Six plant-data sources become **318,760 Agent episodes**, with **5,830 component
tasks** reported separately. The frozen **Core-3K** contains 3,000 Agent episodes.
An agent receives an initial observation, acquires allowed evidence through tools,
and submits a report. Longitudinal episodes may require assessments before later
archived records become available.

| Family | Plant task | Templates |
|---|---|---|
| P1 | Growth monitoring | G01–G03 |
| P2 | Environment investigation | E01–E03 |
| P3 | Active diagnosis | D01–D03 |
| P4 | Care decisions | C01–C03 |
| P5 | Longitudinal follow-up | L01–L03 |

Full covers all 15 templates. Core covers 13; G02 and G03 are absent, so Core
P1-M3 is N/A. Core is a subset of Full, **not a disjoint training pool**.
Use cumulative protected groups before selecting SFT/RL data.

## Included

- **Environment and Harness:** frozen PlantEnv, observations, action parsing,
  evidence access, budgets, stage transitions and the formal P4 care protocol.
- **Model interfaces:** raw JSON, public JSON Schema constraints, and the native
  function-calling adapter used for Thinking Base and SFT evaluation.
- **Offline scoring:** all formal metrics, four tables and P1–P5 × M1–M3 heatmaps,
  with eligible denominators and content/evidence diagnostics.
- **Construction:** six-source adapters, 15 templates, Gold construction,
  normalization and grouping. Rebuilding requires original canonical/feature
  inputs; it is unnecessary for evaluating frozen releases.
- **Thinking–Action SFT:** actual filtering, conversion, loss-mask checks,
  grouped train/validation split and completed training recipe.
- **Recorded demo:** the existing 50-case multi-model gallery.

Episodes, offline Gold and image archives are distributed on Hugging Face. This
code release excludes full teacher trajectories, fine-tuned weights, credentials
and laboratory deployment files. It performs no new model inference and does not
change frozen episodes, Gold or scoring rules.

## Quick start

Use Python 3.11+. On clusters, run downloads, inference, scoring and training in
a suitable compute allocation.

```bash
git clone https://github.com/changcv2021/PlantAgentBench.git
cd PlantAgentBench
python -m pip install -e '.[download,report]'
plantagentbench download --dataset core --output data/core
plantagentbench inspect --dataset data/core
```

Both datasets are public. `download` pins the verified publication commits by
default. For Full (about 74.6 GB including images, catalog, offline Gold and
provenance), use `plantagentbench download --dataset full --output data/full`.
Its 318,760 Agent episodes and 5,830 components are stored separately; the CLI
evaluates Agent episodes by default.

Start a compatible multimodal server separately, with the served model name
matching the configuration. For a local Chat Completions endpoint:

```bash
# Small integration check; remove --limit 5 and use a new directory for all Core.
plantagentbench evaluate --dataset data/core \
  --config configs/qwen35-9b-thinking.json \
  --api-base http://127.0.0.1:8000/v1 --output runs/qwen-smoke --limit 5
plantagentbench score --dataset data/core --run runs/qwen-smoke
plantagentbench summarize --runs runs/qwen-smoke --output outputs/qwen-smoke
```

Gold is opened only by the separate offline scoring command. Unchanged runs
resume using the first saved HTTP 200 per request and skip completed episodes.
Invalid actions are never regenerated because of their score. Independent shards
need separate output directories. The runner defaults to one transport attempt
and stops on infrastructure errors; `--on-error continue` records them separately.
It has **no automatic dollar-budget controller**: set provider spending limits
and test a small task list before a paid run.

For remote APIs edit `configs/api-native.example.json`, use supported parameters,
and pass `--api-key-env YOUR_KEY_VARIABLE`. Never put keys in config files.
The CLI supports OpenAI-compatible **Chat Completions**; provider-specific
Responses and Anthropic Messages integrations are not drop-in compatible.

## Documentation

| Guide | Contents |
|---|---|
| [Evaluation](research_docs/EVALUATION.md) | Protocols, resume, errors, tables and heatmaps |
| [Construction](research_docs/CONSTRUCTION.md) | Adapters, templates, Gold, rebuild prerequisites |
| [Training](training/README.md) | Student supervision, 6,965 trajectories, actual settings |
| [Isolation](research_docs/ISOLATION.md) | Protected evaluation cases and evidence |
| [Release notes](research_docs/RELEASE.md) | Provenance, portability changes, validation scope |
| [Recorded demo](DEMO.md) | Gallery interpretation and application status |

## Licensing and validation

Original software is [MIT licensed](LICENSE). Images, source text, reference Gold
and replays retain upstream terms; see [DATA_TERMS.md](DATA_TERMS.md) and the HF
source notices. The environment reveals recorded evidence; it does not simulate
new growth, treatment outcomes or future observations.

The validation suite installs the package, checks the native-call boundary and training
conversion, and exercises representative frozen Core episodes with scripted
actions, including image access and offline scoring. It calls no models. GPU
serving and a new SFT run are outside this CPU suite. A GitHub Actions template is
provided at `ci/research-code.yml`; activation requires repository workflow-write
permission and placing it under `.github/workflows/`.
