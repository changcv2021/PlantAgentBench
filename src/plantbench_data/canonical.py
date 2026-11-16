"""Validated, disk-backed deterministic canonical table writer and reader."""

import hashlib
import json
import os
import sqlite3
import fcntl
from pathlib import Path

from .common import sha256, write_json
from .models import TABLE_MODELS, logical_json


PRIMARY = {"subjects": "subject_id", "assets": "asset_id", "observations": "observation_id", "annotations": "annotation_id",
           "events": "event_id", "text_turns": "turn_id", "knowledge_entries": "knowledge_id", "links": "link_id",
           "provenance": "provenance_id", "capabilities": "capability_id"}
REFERENCE_FIELDS = {"provenance_refs", "source_annotation_refs", "parent_subject_ids", "identity_evidence_refs", "parent_asset_id",
                    "subject_ref", "spatial_context_refs", "derivation_input_refs", "target_observation_refs", "from_ref", "to_ref",
                    "evidence_refs", "input_provenance_refs", "available_record_refs", "anchor_ref", "calibration_refs"}
REFERENCE_TABLES = {"provenance_refs": "provenance", "input_provenance_refs": "provenance",
                    "source_annotation_refs": "annotations", "parent_subject_ids": "subjects", "subject_ref": "subjects",
                    "identity_evidence_refs": "provenance", "parent_asset_id": "assets", "target_observation_refs": "observations"}


def arrow_schema(table):
    import pyarrow as pa
    # Nested fields remain strict model-validated JSON; the schema is linked in parquet metadata.
    fields = [("record_id", pa.string()), (PRIMARY[table], pa.string()), ("dataset_id", pa.string()),
              ("release_id", pa.string()), ("source_record_key", pa.string()), ("source_split", pa.string()),
              ("processing_status", pa.string()), ("synthetic_fixture", pa.bool_()),
              ("subject_ref", pa.string()), ("provenance_refs", pa.list_(pa.string())), ("record_json", pa.large_string())]
    schema_json = json.dumps(TABLE_MODELS[table].model_json_schema(), sort_keys=True, separators=(",", ":"))
    return pa.schema(fields, metadata={b"plantbench.schema_version": b"1.0.0", b"plantbench.record_schema_sha256": hashlib.sha256(schema_json.encode()).hexdigest().encode()})


class CanonicalWriter:
    def __init__(self, staging, destination, dataset, release, fixture=False, resume=False, input_lock=None):
        self.staging, self.destination = Path(staging), Path(destination)
        self.dataset, self.release, self.fixture = dataset, release, fixture
        if self.destination.exists():
            raise ValueError("Canonical version already exists; validate it or choose a new version")
        self.resume = resume
        existed = self.staging.exists()
        if existed and not resume:
            raise ValueError("Staging already exists; explicit locked resume is required")
        self.staging.mkdir(parents=True, exist_ok=resume)
        self.guard = (self.staging / ".writer.lock").open("a")
        try:
            fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.guard.close()
            raise ValueError("Another writer owns this canonical staging directory")
        lock = {"dataset_id": dataset, "release_id": release, "synthetic_fixture": fixture, "inputs": input_lock}
        lock_path = self.staging / "input.lock.json"
        if existed:
            if not lock_path.exists() or json.loads(lock_path.read_text()) != lock or not (self.staging / "records.sqlite").is_file():
                self.guard.close()
                raise ValueError("Resume input lock missing or changed")
        else:
            write_json(lock_path, lock)
        self.db = sqlite3.connect(self.staging / "records.sqlite")
        self.db.execute("PRAGMA cache_size=-131072")
        self.db.execute("CREATE TABLE IF NOT EXISTS records (table_name TEXT, id TEXT PRIMARY KEY, payload TEXT, logical TEXT, accepted INTEGER)")
        self.db.execute("CREATE TABLE IF NOT EXISTS refs (origin TEXT, target TEXT, expected_table TEXT)")
        self.db.execute("CREATE INDEX IF NOT EXISTS ref_target ON refs(target)")
        # Cover the foreign-key check without random reads of multi-kilobyte
        # payload rows; cover per-table sorting without rescanning every table.
        self.db.execute("CREATE INDEX IF NOT EXISTS record_identity ON records(id,accepted,table_name)")
        self.db.execute("CREATE INDEX IF NOT EXISTS record_table_order ON records(table_name,id)")
        self.emitted = 0

    def checkpoint(self):
        self.db.commit()

    def close(self):
        self.db.close()
        self.guard.close()

    def add(self, table, record):
        model = TABLE_MODELS[table]
        record = model.model_validate(record)
        if record.dataset_id != self.dataset or record.release_id != self.release or record.synthetic_fixture != self.fixture:
            raise ValueError("Canonical source, version or fixture isolation mismatch")
        obj = record.model_dump(mode="json")
        if obj[PRIMARY[table]] != record.record_id:
            raise ValueError("Envelope and table primary key disagree")
        logical = logical_json(record)
        prior = self.db.execute("SELECT logical FROM records WHERE id=?", (record.record_id,)).fetchone()
        if prior:
            if prior[0] != logical:
                raise ValueError("Stable ID collision with conflicting content")
            return
        payload = record.model_dump_json()
        accepted = record.processing_status == "accepted"
        self.db.execute("INSERT INTO records VALUES (?,?,?,?,?)", (table, record.record_id, payload, logical, accepted))
        if accepted:
            if table != "provenance" and not record.provenance_refs:
                raise ValueError("Accepted rows require source provenance")
            for key in REFERENCE_FIELDS:
                values = obj.get(key)
                if values is not None:
                    for target in values if isinstance(values, list) else [values]:
                        self.db.execute("INSERT INTO refs VALUES (?,?,?)", (record.record_id, target, REFERENCE_TABLES.get(key)))
            if table == "observations" and obj["value"]["kind"] in {"asset_ref", "timeseries_ref", "vector_ref"}:
                self.db.execute("INSERT INTO refs VALUES (?,?,?)", (record.record_id, obj["value"]["value"], "assets"))
        self.emitted += 1
        if self.emitted % 10000 == 0:
            self.db.commit()

    def finish(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        self.db.commit()
        missing = self.db.execute("SELECT refs.origin, refs.target FROM refs LEFT JOIN records ON refs.target=records.id WHERE records.id IS NULL OR records.accepted != 1 OR (refs.expected_table IS NOT NULL AND refs.expected_table != records.table_name) LIMIT 20").fetchall()
        if missing:
            raise ValueError(f"Unresolved accepted foreign keys: {missing}")
        output = self.staging / "tables"
        output.mkdir(exist_ok=self.resume)
        if set(p.name for p in output.iterdir()) - {*(f"{t}.parquet" for t in TABLE_MODELS), "canonical_manifest.json"}:
            raise ValueError("Unknown files in canonical table staging")
        table_reports = {}
        for table in TABLE_MODELS:
            schema = arrow_schema(table)
            path = output / f"{table}.parquet"
            count, digest = 0, hashlib.sha256()
            with pq.ParquetWriter(path, schema, compression="zstd") as writer:
                cursor = self.db.execute("SELECT payload,logical FROM records WHERE table_name=? ORDER BY id", (table,))
                while batch := cursor.fetchmany(10000):
                    rows = []
                    for payload, logical in batch:
                        obj = json.loads(payload)
                        rows.append({**{k: obj.get(k) for k in schema.names if k != "record_json"}, "record_json": payload})
                        digest.update(logical.encode() + b"\n")
                    writer.write_table(pa.Table.from_pylist(rows, schema=schema))
                    count += len(rows)
                if count == 0:
                    writer.write_table(pa.Table.from_pylist([], schema=schema))
            table_reports[table] = {"rows": count, "file_sha256": sha256(path), "logical_sha256": digest.hexdigest(),
                                    "empty_reason": "No records emitted from audited source scope" if not count else None}
        write_json(output / "canonical_manifest.json", {"dataset_id": self.dataset, "release_id": self.release,
                   "synthetic_fixture": self.fixture, "tables": table_reports, "foreign_keys_valid": True})
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        os.rename(output, self.destination)
        self.close()
        return table_reports


def iter_table(directory, table, batch_size=10000):
    import pyarrow.parquet as pq
    for batch in pq.ParquetFile(Path(directory) / f"{table}.parquet").iter_batches(batch_size=batch_size, columns=["record_json"]):
        for value in batch.column(0).to_pylist():
            yield TABLE_MODELS[table].model_validate_json(value)


def export_schemas(directory):
    directory = Path(directory)
    for table, model in TABLE_MODELS.items():
        write_json(directory / f"{table}.schema.json", model.model_json_schema())
