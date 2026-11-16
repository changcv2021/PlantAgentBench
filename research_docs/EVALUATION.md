# Running and evaluating plant agents

## Frozen data and visibility

Download a pinned HF snapshot. `Dataset` verifies episode and Gold identities
when opened. Images use an episode allowlist and exact byte digests; knowledge
uses the frozen corpus. Use one dataset snapshot per Python process.

The harness sends only the public task contract, allowed tools and returned
observations to the model. The full catalog and Gold must not be attached to a
model's prompt or filesystem. Public Gold enables offline reproducibility; it
is not evidence. `inspect --episode <ID>` shows the public initial observation,
omitting only image base64 from the terminal display.

## Protocol and model configuration

All protocols use the same PlantEnv and scorer. The action vocabulary is INSPECT,
QUERY, ASK, SEARCH_SOURCE, ASSESS, ADVANCE_RECORD, SUBMIT and STOP. Each task
controls availability, legal targets, budgets and phases.

| Config | Transport | Settings |
|---|---|---|
| `qwen35-raw.json` | JSON content | Thinking off; 4,096 output tokens; T=0.7, top-p=0.8 |
| `qwen35-9b-thinking.json` | Native calls | Thinking on; 32,768 output tokens; T=0.6, top-p=0.95 |
| `api-native.example.json` | Native calls | Explicit provider model, token ceiling and reasoning setting |

The raw profile supports either 9B or 27B by serving the intended weights under
its registered alias. Thinking Base and Ours use the same Thinking profile,
different weights and run labels. Record weight revisions. The public sampling
namespace is explicit; it is not the historical private run's random stream.

To test Structured Harness, copy the raw config and set `protocol` to `structured`
with a new label/directory. Its schema comes only from the public task; it does
not choose tools or answers. Providers differ in supported schema subsets.
Rejected schemas remain infrastructure errors; no automatic protocol fallback.

Native definitions derive from the same public schema. The adapter retains
returned reasoning and calls in subsequent context, and maps a single call to
action text without repair. Raw JSON reads content only, without mining reasoning
for an answer. Malformed/empty actions reach the formal parser. The runtime uses
the formal P4 `care-report-2.1.0` view, not a new Gold rewrite.

## Service boundary

Start a compatible multimodal server in your own allocation, with sufficient
context and image capacity. Native mode needs tool calling and reasoning parsing.
Test a real image-bearing episode first; server flags depend on your version.

The public CLI speaks `/chat/completions`. Provider-specific `/responses` and
Anthropic Messages campaign runners, budget ledgers and cluster scheduling
controllers are not exported as this entrypoint. A deployment-name change alone
does not establish provider compatibility. Pass credentials with `--api-key-env`;
HTTPS is required remotely, HTTP only on loopback.

Saved requests, raw responses and reasoning are private run artifacts. They may
contain user data; do not commit them. This runner has no automatic dollar cap.
Use provider spending limits and an explicit small task list before expansion.

## Resume, errors and sharding

A run freezes the release, IDs, model parameters, protocol, sampling namespace
and transport policy. Changed identities cannot reuse an output directory.
Completed episodes are skipped. An interrupted episode reuses the first saved
HTTP 200 for each logical request, even when the returned action is invalid.

Default timeout is 600 seconds, with one transport attempt. Extra attempts must
be explicit; timed-out requests can already have incurred charges. Infrastructure
and runner errors stop dispatch by default. `--on-error continue` saves them and
continues; previously recorded exceptions remain separate on resume. Never retry
because of low scores. Inspect failures before registering a separate recovery.

Use `--shards N --shard-index K` (K=0…N−1) with different directories. Hash sharding
changes placement, not eligibility. One directory has one writer lock.
After scoring shards, `plantagentbench merge --runs runs/shard-* --output runs/merged`
combines disjoint results for reporting; it rejects overlap and changed settings.
The merged directory is report-only, not an inference resume directory.

## Four tables and milestone heatmaps

`summarize` uses the unchanged formal aggregation and table renderer:

1. `table1_task_success.csv`: Task Goal Success and success counts.
2. `table2_milestone_progress.csv`: progress and necessary-milestone counts.
3. `table3_execution_failure_diagnosis.csv`: raw/normalized Format Validity,
   Content Correctness/Coverage, Evidence Grounding and related diagnostics.
4. `table4_tools_information_seeking.csv`: Valid Tool Use, Evidence Acquisition,
   Query Cost and Task Goal Success.

`milestone_breakdown.csv` retains M1–M3, content/evidence/stage components and
eligible denominators. PNG/SVG heatmaps mark missing milestones N/A. The JSON
report retains pooled counts and group/source/P macro aggregation. Pooled ratios
and macro values are different estimands. Incomplete panels retain provisional
values, not complete-benchmark scores. Provider-specific historical
error-as-wrong reporting policies are not silently reconstructed here.
