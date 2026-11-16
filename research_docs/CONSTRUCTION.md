# Data-to-episode construction

Evaluate the frozen HF releases. Construction sources document their creation;
downloading the code does not regenerate or modify them.

| Source | Adapter / construction source |
|---|---|
| Horti-M3-Tomato | `plantbench_data/adapters/horti.py` |
| Lettuce RGB–Environment–Biomass | `plantbench_data/adapters/lettuce.py` |
| Eschikon Plant Stress | `plantbench_data/adapters/eschikon.py` |
| ToBRFV-LMID | `plantbench_data/adapters/tobrfv.py` |
| MIRAGE | `plantbench_data/adapters/mirage.py` |
| PlantExpertVQA | `plantbench/preparation/plantexpert_tasks.py`, including `ExpertStore` |

The foundation adapters produce canonical subjects, assets, observations,
annotations, texts and provenance. PlantExpertVQA uses a separate conversion at
the task-building stage. Six adapters do not imply six runtime environments.

`plantbench_data/models.py` defines canonical records. Taskpack construction
creates evidence and targets, with private Gold in separate storage. The fixed
768-pixel RGB render records source identity and verified display bytes without
exposing original filenames to the model.

All G01–G03, E01–E03, D01–D03, C01–C03 and L01–L03 implementations are retained
in `plantbench/preparation/`, principally `records_tasks.py`, `extended_records.py`,
`reference_tasks.py`, `auxiliary_tasks.py`, `consultation_followup.py` and
`plantexpert_tasks.py`. `evaluation_contract.py` compiles scoring targets;
`normalization.py` and `finalize_pack.py` produce the final contract.
`references.py` contains reference normalization and care rules; `group_audit.py`
and `semantic_dedup.py` contain grouping and duplicate checks.

Deterministic Record Gold derives from real measurements and explicit criteria.
Source Reference Gold encodes labels, consultation references or knowledge.
Reference agreement is not new independent expert adjudication. The code retains
actual thresholds, allowed values, evidence and phase requirements; no LLM judge
replaces the formal scoring in this release.

## Reconstruction prerequisites

Reconstruction needs original downloads, manifests, canonical records and the
feature database/corpus. These are not interchangeable with the evaluation-ready
HF catalog. The original acquisition scheduler is not exported as a turnkey
raw-to-Full rebuild command.

Storage roots become explicit operator options:

- `PAB_SOURCE_ROOT`: raw/canonical tree; default `source-data`.
- `PAB_FEATURE_ROOT`: directory with `features.sqlite`, `feature_report.json`,
  and `knowledge_corpus.json`.
- `PAB_FEATURE_DB`: optional explicit feature database.
- `PAB_SOURCE_PROJECT`: original project manifests, including raw-file receipts.

Frozen evaluation knowledge is resolved relative to the downloaded dataset.
Construction tools retain their original compute checks; bulk reconstruction
requires a verified Slurm allocation. A different scheduler needs a reviewed
execution adapter, not fake Slurm variables. This code publication did not rerun
a clean raw-to-Full reconstruction. Use frozen releases for benchmark evaluation.
