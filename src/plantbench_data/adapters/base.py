import collections
import json
import mimetypes
from pathlib import Path

from ..canonical import CanonicalWriter
from ..common import read_json, sha256, write_json
from ..models import stable_id


class AdapterContext:
    def __init__(self, root, data_root, dataset, release, build_id, resume=False):
        self.root, self.data_root = Path(root), Path(data_root)
        self.dataset, self.release = dataset, release
        self.mapping = read_json(self.root / f"configs/mappings/{dataset}.yaml")
        self.mapping_version = self.mapping["mapping_version"]
        if dataset == "eschikon_stress":
            self.source_root = self.data_root / "raw" / dataset / release
            audit = read_json(self.root / f"reports/source_audits/{dataset}.materialized.json")
            if audit["release_id"] != release:
                raise ValueError("Materialization release differs from locked source")
            downloads = read_json(self.root / f"data/manifests/raw_files.{dataset}.json")
            self.files = {}
            for row in downloads:
                if row["release_id"] != release or row["status"] != "verified":
                    raise ValueError("Incomplete raw source receipt")
                if not row["path"].lower().endswith(".zip"):
                    self.files["downloads/" + row["path"]] = row
            for archive in audit["archives"]:
                extracted = Path(archive["extracted_root"])
                prefix = extracted.relative_to(self.source_root)
                for row in read_json(extracted / ".plantbench_extraction.json")["files"]:
                    key = str(prefix / row["path"])
                    if key in self.files:
                        raise ValueError("Conflicting source member paths")
                    self.files[key] = row
        elif dataset == "mirage":
            self.source_root = self.data_root / "raw" / dataset / release / "downloads"
            receipts = read_json(self.root / f"data/manifests/raw_files.{dataset}.json")
            if any(r["release_id"] != release or r["status"] != "verified" for r in receipts):
                raise ValueError("Incomplete HF raw receipt")
            self.files = {r["path"]: r for r in receipts}
        else:
            audit = read_json(self.root / f"reports/source_audits/{dataset}.materialized.json")
            if audit["release_id"] != release or len(audit["archives"]) != 1:
                raise ValueError("Expected one verified materialized archive at locked release")
            self.source_root = Path(audit["archives"][0]["extracted_root"])
            receipt = read_json(self.source_root / ".plantbench_extraction.json")
            self.files = {r["path"]: r for r in receipt["files"]}
        import hashlib
        import platform
        package = self.root / "src/plantbench_data"
        transformation_files = [*sorted((package / "adapters").glob("*.py")),
                                *(package / name for name in ("models.py", "canonical.py", "media.py", "common.py")),
                                self.root / "scripts/convert_source.py"]
        inputs = {"python_version": platform.python_version(), "mapping_sha256": sha256(self.root / f"configs/mappings/{dataset}.yaml"),
                  "dependencies_sha256": sha256(self.root / "requirements.lock.txt"),
                  "source_member_logical_sha256": hashlib.sha256(json.dumps(
                      sorted((key, row["sha256"]) for key, row in self.files.items()), separators=(",", ":")).encode()).hexdigest(),
                  "code": {str(p.relative_to(self.root)): sha256(p) for p in sorted(transformation_files)}}
        self.writer = CanonicalWriter(self.data_root / "staging" / "canonical" / build_id / dataset,
                                      self.data_root / "canonical" / build_id / dataset, dataset, release, resume=resume, input_lock=inputs)
        self.counts = collections.Counter()
        self.issues = []
        self.capabilities = collections.defaultdict(list)
        self.subject_cache = {}

    def key(self, kind, source_key):
        return stable_id(self.dataset, self.release, kind, source_key)

    def envelope(self, kind, key, provenance, **updates):
        result = {"dataset_id": self.dataset, "release_id": self.release, "record_id": self.key(kind, key),
                  "source_record_key": key, "provenance_refs": provenance,
                  "processing_status": "accepted", "review_status": "not_reviewed", "source_split": getattr(self, "current_split", None)}
        result.update(updates)
        return result

    def add(self, table, record):
        self.writer.add(table, record)
        self.counts[table] += 1
        return record["record_id"]

    def provenance(self, relative, kind="file", **locator):
        record = self.files[relative]
        # Audit-derived hashes must still agree with current bytes at conversion.
        key = json.dumps([relative, kind, locator], ensure_ascii=False, sort_keys=True)
        env = self.envelope("provenance", key, [])
        return self.add("provenance", {**env, "provenance_id": env["record_id"],
            "locator": {"kind": kind, "relative_path": str((self.source_root / relative).relative_to(self.data_root)),
                        "file_sha256": record["sha256"], **locator},
            "parser_id": self.dataset, "parser_version": "0.1.0", "mapping_rule_id": f"{self.dataset}/mapping-v{self.mapping_version}"})

    def subject(self, key, scope, provenance, cycle=None, resolution="exact", source_fields=None):
        if key in self.subject_cache:
            existing, signature = self.subject_cache[key]
            if signature != (scope, cycle, resolution):
                raise ValueError("Conflicting source subject identity scope")
            return existing
        env = self.envelope("subject", key, [provenance], source_fields=source_fields or {})
        result = self.add("subjects", {**env, "subject_id": env["record_id"], "subject_type": scope,
            "source_subject_id": key, "cycle_id": cycle, "identity_evidence_refs": [provenance],
            "identity_resolution": resolution})
        self.subject_cache[key] = (result, (scope, cycle, resolution))
        return result

    def external_provenance(self, relative):
        from ..transfer import local_path
        path = local_path(self.data_root, relative)
        key = "source-documentation/" + relative
        env = self.envelope("provenance", key, [])
        return self.add("provenance", {**env, "provenance_id": env["record_id"],
            "locator": {"kind": "file", "relative_path": relative, "file_sha256": sha256(path)},
            "parser_id": self.dataset, "parser_version": "0.1.0", "mapping_rule_id": f"{self.dataset}/mapping-v{self.mapping_version}"})

    def asset(self, relative, provenance, times=None, scope="image_only_case", modality="rgb", access_class="restricted_raw",
              source_fields=None, wavelength_metadata=None, additional_provenance=(), localization_status=None):
        from ..media import fingerprint, inspect_media
        path = self.source_root / relative
        record = self.files[relative]
        before = fingerprint(path)
        prior = getattr(self, "verified_fingerprints", {}).get(relative)
        digest = record["sha256"] if prior == before else sha256(path)
        if digest != record["sha256"]:
            raise ValueError("Raw extracted file changed after verification")
        media = inspect_media(path)
        if before != fingerprint(path):
            raise ValueError("Source image changed during decoding")
        env = self.envelope("asset", relative, [provenance, *additional_provenance])
        return self.add("assets", {**env, "asset_id": env["record_id"], "content_sha256": digest,
            "media_type": mimetypes.guess_type(relative)[0] or "application/octet-stream",
            "source_relative_path": relative, "storage_uri": str(path.relative_to(self.data_root)),
            **{k: media[k] for k in ("width", "height", "channels", "dtype", "bit_depth", "page_count")}, "modality": modality, "subject_scope": scope,
            "localization_status": localization_status or ("not_applicable" if modality == "reference_figure" else "group_view" if scope == "camera_view" else "unresolved"), "times": times or {},
            "availability": "available", "decode_status": "decoded", "license_chain_refs": [f"{self.dataset}/{self.release}/repository-license"],
            "privacy_status": "not_reviewed", "access_class": access_class, "wavelength_metadata": wavelength_metadata,
            "source_fields": {"pillow_mode": media["pillow_mode"], **({"native_page_metadata": media["native_page_metadata"]} if media["page_count"] > 1 else {}), **(source_fields or {})}})

    def observation(self, key, subject, variable, value, source_value, unit, provenance, scope, evidence, times=None, flags=None, source_unit=None, source_fields=None):
        env = self.envelope("observation", key, [provenance], source_fields={"quality_flags": flags or [], **(source_fields or {})})
        record = {**env, "observation_id": env["record_id"], "subject_ref": subject, "variable_id": variable,
                  "value": value, "unit": unit, "source_value": source_value, "source_unit": source_unit if source_unit is not None else unit,
                  "unit_status": "not_applicable" if value["kind"] == "asset_ref" else "resolved" if unit else "unresolved",
                  "scope": scope, "times": times or {}, "availability": "not_recorded_in_release" if value["value"] is None else "available", "evidence_kind": evidence,
                  "access_class": "observation_candidate"}
        if value["kind"] == "scalar" and value["value"] is None and source_value not in (None, ""):
            record["availability"] = "unresolved_metadata"
        ident = self.add("observations", record)
        if record["availability"] == "available":
            self.capabilities[(subject, variable, scope, evidence, record["unit_status"], bool(flags))].append((ident, provenance))
        return ident

    def link(self, key, source, target, relation, provenance, scope, status="source_group", temporal_policy="not_applicable", future_dependence=False, source_fields=None):
        env = self.envelope("link", key, provenance)
        return self.add("links", {**env, "link_id": env["record_id"], "from_ref": source, "to_ref": target,
            "relation": relation, "scope": scope, "evidence_refs": provenance, "link_status": status,
            "temporal_policy": temporal_policy, "future_dependence": future_dependence, "source_fields": source_fields or {}})

    def finish(self):
        for (subject, variable, scope, evidence, unit_status, flagged), refs in self.capabilities.items():
            key = f"{subject}/{variable}/{scope}/{evidence}/{unit_status}/{flagged}"
            env = self.envelope("capability", key, sorted({p for _, p in refs}))
            capability_key = {"growth.fresh_mass": "phenotype.biomass_measurement", "environment.solution_ec": "nutrient_solution.electrical_conductivity"}.get(variable, variable)
            self.add("capabilities", {**env, "capability_id": env["record_id"], "scope_level": "subject",
                "subject_ref": subject, "anchor_ref": None, "capability_key": capability_key, "source_presence": "present",
                "parse_status": "parsed", "link_status": "linked", "permission_status": "allowed",
                "quality_status": "flagged" if flagged else "accepted", "available_record_refs": [r for r, _ in refs],
                "evidence_kinds": [evidence], "measurement_scope": scope, "unit_status": unit_status,
                "observed_points": len(refs), "usable_as_data": not flagged and unit_status != "unresolved",
                "reason_codes": ["SOURCE_SEMANTICS_REQUIRE_REVIEW"] if flagged else []})
        return self.writer.finish()
