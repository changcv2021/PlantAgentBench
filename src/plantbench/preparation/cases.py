"""Complete case/evidence inventory and finite anchors; never call these tasks.

Read the verified source index in a single pass, without loading images or
requiring human review. Source reference material remains private. Builders
must subsequently decide each episode's disclosure and scoring scope.
"""
import collections
import hashlib
import json
import sqlite3
from pathlib import Path

from plantbench_data.common import read_json, require_compute, sha256, utc_now, write_json


def case_id(dataset, subject):
    return "case_" + hashlib.sha256((dataset + "\0" + subject).encode()).hexdigest()[:24]


def finite_anchor(row):
    """Group calendar observations by real source day, retain experiment indices.

    A daily window is a benchmark analysis unit, not a newly asserted source
    timestamp. Never convert years, infer transplant dates, or pair all frames.
    """
    if row.get("processing_status") != "accepted" or row.get("availability") != "available":
        return None
    times = row.get("times", {})
    point = next((p for p in (times.get("event_time"), times.get("collection_time"))
                  if p and p.get("value") is not None and p.get("kind") != "unknown"), None)
    if point is None:
        return None
    kind, value = point["kind"], point["value"]
    if kind in {"date", "datetime"}:
        return "calendar_day", str(value)[:10]
    return kind, str(value)


class ParquetSink:
    def __init__(self, path, fields):
        import pyarrow as pa
        import pyarrow.parquet as pq
        self.pa = pa
        self.schema = pa.schema(fields)
        self.writer = pq.ParquetWriter(path, self.schema, compression="zstd")
        self.rows = []
        self.count = 0

    def add(self, row):
        self.rows.append(row)
        self.count += 1
        if len(self.rows) >= 4096:
            self.flush()

    def flush(self):
        if self.rows:
            self.writer.write_table(self.pa.Table.from_pylist(self.rows, schema=self.schema))
            self.rows.clear()

    def close(self):
        self.flush()
        self.writer.close()


def prepare(config):
    import pyarrow as pa
    execution = require_compute()
    index, output = Path(config["source_index"]), Path(config["build_output"])
    inventory = read_json(index / "source_inventory.json")
    if inventory["status"] != "COMPLETE":
        raise ValueError("Source index not complete")
    if config.get("pending_human_review_blocks_execution") is not False:
        raise ValueError("guide06 requires human review last")
    if config.get("production_cases_per_source") is not None:
        raise ValueError("Production source caps are not allowed")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    db = sqlite3.connect("file:" + str(index / "private/source_records.sqlite") + "?mode=ro", uri=True)
    cases = {}
    for dataset, subject, payload in db.execute("SELECT dataset,id,payload FROM records WHERE kind='subjects'"):
        row = json.loads(payload)
        cases[(dataset, subject)] = {"case_id": case_id(dataset, subject), "dataset_id": dataset,
            "subject_ref": subject, "subject_type": row["subject_type"], "source_record_key": row["source_record_key"],
            "records": collections.Counter(), "variables": set(), "source_splits": set(),
            "source_fields": row.get("source_fields", {}), "human_review_status": "pending",
            "identity": {k: row.get(k) for k in ("source_subject_id", "cycle_id", "site_id", "parent_subject_ids", "identity_resolution", "identity_evidence_refs")}}
    fields = [("evidence_id", pa.string()), ("case_id", pa.string()), ("dataset_id", pa.string()),
              ("record_kind", pa.string()), ("record_ref", pa.string()), ("reference_scope", pa.string()),
              ("processing_status", pa.string()), ("provenance_refs", pa.list_(pa.string()))]
    evidence = ParquetSink(output / "evidence.parquet", fields)
    references = ParquetSink(output / "reference_units.parquet", fields)
    anchors = {}
    unresolved = collections.Counter()
    total = 0
    for dataset, kind, record_id, subject, payload in db.execute("SELECT dataset,kind,id,subject,payload FROM records WHERE kind IN ('observations','annotations','text_turns','events','knowledge_entries')"):
        total += 1
        row = json.loads(payload)
        case = cases.get((dataset, subject))
        if case is None:
            unresolved[dataset + ":" + kind + ":no_subject_link"] += 1
            continue
        case["records"][kind] += 1
        if row.get("source_split"):
            case["source_splits"].add(row["source_split"])
        scope = ("source_reference" if kind == "annotations" or row.get("text_origin") == "reference_answer"
                 else "general_knowledge" if kind == "knowledge_entries" else "source_record")
        entry = {"evidence_id": record_id, "case_id": case["case_id"], "dataset_id": dataset,
                 "record_kind": kind, "record_ref": record_id, "reference_scope": scope,
                 "processing_status": row["processing_status"], "provenance_refs": row["provenance_refs"]}
        (references if scope != "source_record" else evidence).add(entry)
        if kind == "observations":
            variable = row["variable_id"]
            case["variables"].add(variable)
            point = finite_anchor(row)
            if point:
                key = (case["case_id"], *point)
                anchor = anchors.setdefault(key, {"case_id": case["case_id"], "dataset_id": dataset,
                    "time_kind": point[0], "time_value": point[1], "record_count": 0, "variables": set(),
                    "anchor_ref": record_id})
                anchor["record_count"] += 1
                anchor["variables"].add(variable)
            else:
                unresolved[dataset + ":observations:no_usable_time_anchor"] += 1
        if total % 100000 == 0:
            print(json.dumps({"stage": "prepare-agent-cases", "source_records": total}), flush=True)
    evidence.close()
    references.close()
    case_sink = ParquetSink(output / "cases.parquet", [("case_id", pa.string()), ("dataset_id", pa.string()),
        ("subject_ref", pa.string()), ("subject_type", pa.string()), ("source_record_key", pa.string()),
        ("records_json", pa.string()), ("variables", pa.list_(pa.string())), ("source_splits", pa.list_(pa.string())),
        ("source_fields_json", pa.string()), ("identity_json", pa.string()), ("human_review_status", pa.string()), ("group_status", pa.string())])
    counts = collections.Counter()
    for case in cases.values():
        case_sink.add({k: case[k] for k in ("case_id", "dataset_id", "subject_ref", "subject_type", "source_record_key", "human_review_status")} | {
            "records_json": json.dumps(case["records"], sort_keys=True), "variables": sorted(case["variables"]),
            "source_splits": sorted(case["source_splits"]), "source_fields_json": json.dumps(case["source_fields"], sort_keys=True),
            "identity_json": json.dumps(case["identity"], sort_keys=True),
            "group_status": "source_identity_preserved_exposure_and_context_grouping_pending"})
        counts[case["dataset_id"]] += 1
    case_sink.close()
    anchor_sink = ParquetSink(output / "anchor_manifest.parquet", [("anchor_id", pa.string()), ("case_id", pa.string()),
        ("dataset_id", pa.string()), ("time_kind", pa.string()), ("time_value", pa.string()), ("record_count", pa.int64()),
        ("variables", pa.list_(pa.string())), ("anchor_ref", pa.string())])
    anchor_counts = collections.Counter()
    for key in sorted(anchors):
        anchor = anchors[key]
        anchor_sink.add({**anchor, "variables": sorted(anchor["variables"]),
                        "anchor_id": "anchor_" + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]})
        anchor_counts[anchor["dataset_id"]] += 1
    anchor_sink.close()
    db.close()
    report = {"status": "CASE_PREPARATION_COMPLETE_TASK_BUILD_IN_PROGRESS", "execution": execution,
        "created_at_utc": utc_now(), "implementation_guide": config["implementation_guide"],
        "cases_by_source": dict(counts), "anchors_by_source": dict(anchor_counts),
        "source_evidence_records": evidence.count, "source_reference_units": references.count,
        "unresolved_counts": dict(unresolved), "source_index_inventory_sha256": sha256(index / "source_inventory.json"),
        "human_review_status": "pending", "human_review_blocks_execution": False,
        "case_definition": "canonical source entity; not necessarily one independent plant or one task",
        "evidence_visibility": "private source inventory only; per-task disclosure not yet assigned; original responses may contain current gold",
        "anchor_definition": "all real calendar days or experiment indices; daily windows are benchmark groupings",
        "formal_tasks_compiled": 0, "model_evaluation_started": False,
        "remaining": ["source-specific TaskCards and case association repair", "reference claim extraction",
            "P1-P5 candidate builders and GoldSpec", "group and exposure propagation", "environment/scoring integration",
            "scripted validation", "real model smoke and full 4B/9B/27B evaluation", "final human review"]}
    report["artifacts"] = {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size} for p in output.glob("*.parquet")}
    write_json(output / "preparation_report.json", report)
    print(json.dumps({"status": report["status"], "cases": sum(counts.values()), "anchors": sum(anchor_counts.values())}), flush=True)
    return report
