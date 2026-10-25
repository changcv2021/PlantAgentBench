# Source credits and data terms

The MIT license in this repository applies to the original viewer code and authored documentation. It does not override the terms of source images, measurements, consultation text, knowledge entries, or adaptations of those materials.

The demo exports cite their source on each evidence card and retain episode and evidence identifiers. Images are copied byte-for-byte from the rendered evidence used in the original evaluation; no additional image generation or retouching is performed for this website. Tables and text reflect the benchmark's existing record packets. Model outputs are labelled separately from source evidence.

The visitor gallery also includes frozen episode images that the model could have requested but did not open. These are labelled separately from images actually observed during the saved run. No images from unrelated cases are added to record-only tasks.

| Source | Attribution | Source terms and link |
|---|---|---|
| Eschikon Plant Stress Phenotyping Dataset | Raghav Khanna, Lukas Schmid, Juan Nieto, Roland Siegwart, Frank Liebisch; ETH Zurich | [Dataset record](https://doi.org/10.3929/ethz-c-000788562); [In Copyright – Non-Commercial Use Permitted](https://rightsstatements.org/page/InC-NC/1.0/?language=en). Included for this non-commercial research demonstration. |
| Horti-M3-Tomato / 2023–2025 Tomato dataset | Yifei He, Yu Gong, Xuefeng Zhang | [CC BY 4.0; Zenodo release](https://doi.org/10.5281/zenodo.17217565) |
| Lettuce RGB–Environment–Biomass | Saeed Karimzadeh, Md Shamim Ahamed | [CC BY 4.0; Zenodo release](https://doi.org/10.5281/zenodo.17041810) |
| ToBRFV-LMID | Feyza Yılmaz, Rifat Edizkan, Pelin Keleş Öztürk, Şefika Yavuz, Hasan Serhan Yavuz, Ömer Nezih Gerek, Serkan Önder, Davut Keleş | [CC BY 4.0; Zenodo release](https://doi.org/10.5281/zenodo.17244968) |
| MIRAGE | MIRAGE Benchmark contributors and original agricultural consultation contributors | [CC BY-SA 4.0; dataset and original attributions](https://huggingface.co/datasets/MIRAGE-Benchmark/MIRAGE) |
| PlantExpertVQA | Syed Nazmus Sakib and PlantExpertVQA contributors; original image contributors credited in the upstream dataset | [CC BY 4.0 as declared by the dataset card; upstream provenance](https://huggingface.co/datasets/SyedNazmusSakib/PlantExpertVQA) |

General reference knowledge returned by `SEARCH_SOURCE` comes from PlantExpertVQA's authored knowledge cards even when the individual case originates in another source. Its separate attribution is retained in the replay.

Except for Eschikon-derived files, the selected replay exports in `docs/cases/` are offered under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), subject to the underlying evidence terms. Unmodified media retain their original source licenses. Consult the upstream source for original image-level provenance and any notices applicable to individual assets; this demo does not claim new ownership of upstream material or imply contributor endorsement.

Changes made in this public export: reorganizing the recorded interactions for a browser viewer, excluding private evaluation references and service metadata, and removing infrastructure identifiers if present. Model reasoning, tool choices, and evidence contents are not rewritten to improve the demonstration. Raw source answers used as private evaluation targets are not exported.

The 50 recorded examples are deliberately curated, not a representative statistical sample. Their IDs are in `demo_manifest.json` and are publicly exposed by this release. Neither their success badges nor model-generated care recommendations constitute independent expert validation or evidence that an intervention was carried out.

## Eschikon-specific terms

The source DOI metadata declares InC-NC 1.0. The linked rights statement permits non-commercial uses without additional rights-holder permission; it is a rights statement, not a Creative Commons license. This academic demo retains that statement and author attribution. Eschikon images, measurements and the replay files containing them are **not** offered under the repository's MIT code license or the CC BY-SA replay grant. Other uses must follow the upstream rights statement.

The five Eschikon files are identified by `source_id: eschikon_stress` in the case data and the public manifest. Their images are copied from the frozen benchmark render, with SHA-256 provenance, and displayed without additional retouching. Recorded observations are reorganized for browser replay; Gold and private service metadata are excluded. No source rights or scientific scores are changed by this presentation.
