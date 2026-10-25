# Source credits and data terms

The MIT license in this repository applies to the original viewer code and authored documentation. It does not override the terms of source images, measurements, consultation text, knowledge entries, or adaptations of those materials.

The demo exports cite their source on each evidence card and retain episode and evidence identifiers. Images are copied byte-for-byte from the rendered evidence used in the original evaluation; no additional image generation or retouching is performed for this website. Tables and text reflect the benchmark's existing record packets. Model outputs are labelled separately from source evidence.

| Source | Attribution | Source terms and link |
|---|---|---|
| Horti-M3-Tomato / 2023–2025 Tomato dataset | Yifei He, Yu Gong, Xuefeng Zhang | [CC BY 4.0; Zenodo release](https://doi.org/10.5281/zenodo.17217565) |
| Lettuce RGB–Environment–Biomass | Saeed Karimzadeh, Md Shamim Ahamed | [CC BY 4.0; Zenodo release](https://doi.org/10.5281/zenodo.17041810) |
| ToBRFV-LMID | Feyza Yılmaz, Rifat Edizkan, Pelin Keleş Öztürk, Şefika Yavuz, Hasan Serhan Yavuz, Ömer Nezih Gerek, Serkan Önder, Davut Keleş | [CC BY 4.0; Zenodo release](https://doi.org/10.5281/zenodo.17244968) |
| MIRAGE | MIRAGE Benchmark contributors and original agricultural consultation contributors | [CC BY-SA 4.0; dataset and original attributions](https://huggingface.co/datasets/MIRAGE-Benchmark/MIRAGE) |
| PlantExpertVQA | Syed Nazmus Sakib and PlantExpertVQA contributors; original image contributors credited in the upstream dataset | [CC BY 4.0 as declared by the dataset card; upstream provenance](https://huggingface.co/datasets/SyedNazmusSakib/PlantExpertVQA) |

General reference knowledge returned by `SEARCH_SOURCE` comes from PlantExpertVQA's authored knowledge cards even when the individual case originates in another source. Its separate attribution is retained in the replay.

The selected replay exports in `docs/cases/` are offered under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), subject to the underlying evidence terms. Unmodified media retain their original source licenses. Consult the upstream source for original image-level provenance and any notices applicable to individual assets; this demo does not claim new ownership of upstream material or imply contributor endorsement.

Changes made in this public export: reorganizing the recorded interactions for a browser viewer, excluding private evaluation references and service metadata, and removing infrastructure identifiers if present. Model reasoning, tool choices, and evidence contents are not rewritten to improve the demonstration. Raw source answers used as private evaluation targets are not exported.

The 50 successful examples are deliberately curated, not a representative statistical sample. Their IDs are in `demo_manifest.json` and are publicly exposed by this release. Neither their success badges nor model-generated care recommendations constitute independent expert validation or evidence that an intervention was carried out.
