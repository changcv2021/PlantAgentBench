# Release validation — 2026-11-16

**12 tests passed; 0 failures, errors or skips.** The suite took 15.05 seconds
after installation and packaging. Validation used Python 3.12.11 on Linux CPU;
no GPU, live model API or teacher checkpoint was used.

The source distribution and wheel built successfully, and the public CLI and
training shell syntax checks passed. Full adapter import checks include the
optional `construction` dependencies (including `openpyxl`).

| Area | Verified scope |
|---|---|
| Frozen Core | Representative episodes of all 13 Core templates |
| Evidence | Registered image bytes match digests; phase-zero public tool targets return without errors; frozen knowledge resolves |
| Environment/scorer | Scripted STOP traces pass formal trace verification and offline scoring |
| P4 | Formal care-report 2.1 view is applied |
| Protocols | Raw, Structured and Native CLI paths with mocked responses |
| Resume | Completed episodes are skipped; first HTTP 200 is reused; changed request identity is rejected |
| Result boundary | Scripted fixture scores cannot enter model-result aggregates |
| Reporting | Four CSV tables, milestone data and PNG/SVG heatmaps are generated |
| Training conversion | Masked thinking/action spans remain masked; tool feedback remains context; image order is preserved |
| Source fidelity | Curated module digests match the release provenance manifest |
| Packaging | All released modules import with the declared optional dependencies |

These are software integration checks, not new model measurements. Scripted
STOP and phase-zero query checks do not demonstrate a successful autonomous
policy or exhaust every possible stage/action combination. They do not rerun
old predictions or change frozen data/Gold/scoring.

The runtime tests retain nine Matplotlib deprecation warnings from the unchanged
formal figure renderer. Packaging also reports a future setuptools license-field
deprecation; neither prevented the tested build. GPU serving, provider-specific
APIs, full raw-data reconstruction and a new SFT run are outside this validation.

Both frozen datasets were independently published and verified:

- Core-3K: `a6334d083abebddbd9b6dfdd05afb746b79ef562`.
- Full: `49a5fc79b7078d84f01d2143a813dbe73eaf9654`.

The GitHub Actions template is supplied in `ci/research-code.yml`; these checks
were run in an allocated CPU environment because the publication credential
does not have workflow-write permission.
