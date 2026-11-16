# Research code release 1.0.0

Research code is added to the existing demo repository. `docs/` and `space/`
remain intact; Pages continues to use `main:/docs`. This release does not enable
live HF GPU inference.

`source_provenance.json` records each curated module's source identity. Formal
environment behavior, parsing, P4 protocol and scoring are preserved. The public
storage adapter relocates catalog, images and knowledge without changing payloads.

Portability changes: the historical raw-client default model alias becomes
`plantagent-model`; construction paths become explicit environment variables;
training imports become package-relative; the pure quality filter is extracted
verbatim. The new CLI connects these modules, separates execution from Gold
scoring and records a frozen run identity.

This CLI is not a copy of laboratory paid-API controllers. It does not inherit
their financial budgets or provider-specific error-as-wrong policies. Each
transport/reasoning/protocol setting must be registered explicitly.

The validation suite checks package installation, native-call conversion, first
response reuse, training conversion, real image access, representative public
tools and the frozen scorer. Scripted integration traces are marked
`scripted_validation`; no model outcomes are generated. The Actions configuration
is supplied as `ci/research-code.yml`, not activated: the publication credential
does not have GitHub's `workflow` scope. It can be installed by a repository
maintainer with that permission. Local compute validation uses the same tests.

To run the full suite, include optional construction dependencies because it
imports the six source adapters, including Excel-based record readers:

```bash
python -m pip install -e '.[report,construction,test]'
PAB_TEST_DATASET=/path/to/frozen/core python -m pytest tests -q
```

The `construction` extra is unnecessary for ordinary frozen-data evaluation.

No benchmark models are rerun, no weights trained, no Gold or episodes modified.
A clean raw-to-Full rebuild and all-provider serving compatibility are not claimed.
Dataset metadata discloses an existing Core parent-lock fingerprint discrepancy;
the selected episode and Gold identities were independently verified.

GitHub distributes code and the existing curated demo. HF distributes frozen
episodes, evidence/images, separate offline Gold and source notices. Full teacher
outputs and checkpoints need separate releases. Credentials, private endpoints
and lab job settings are excluded; source data retain upstream terms.
