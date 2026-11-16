"""Strict canonical contracts. Source facts must be supplied, never inferred here."""

import hashlib
import json
from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictFloat, StrictInt, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)


class TimePoint(Contract):
    kind: Literal["date", "datetime", "experiment_day", "days_after_transplanting", "dialog_turn", "unknown"]
    value: str | StrictInt | StrictFloat | None
    precision: Literal["date", "minute", "second", "index", "unknown"]
    timezone_status: Literal["source_declared", "unknown", "not_applicable"] = "unknown"
    timezone: str | None = None
    source_value: str | None = None

    @model_validator(mode="after")
    def check_precision(self):
        if self.kind == "date":
            date.fromisoformat(str(self.value))
            if self.precision != "date" or self.timezone is not None:
                raise ValueError("A source date must not acquire a clock time or timezone")
        if self.kind == "datetime":
            parsed = datetime.fromisoformat(str(self.value))
            if self.timezone_status == "unknown" and parsed.tzinfo is not None:
                raise ValueError("Unknown source timezone cannot silently become UTC")
        if self.kind == "unknown" and self.value is not None:
            raise ValueError("Unknown time has no fabricated value")
        return self


class Times(Contract):
    event_time: TimePoint | None = None
    collection_time: TimePoint | None = None
    available_time: TimePoint | None = None
    ingested_at: datetime | None = None


class ScalarValue(Contract):
    kind: Literal["scalar"] = "scalar"
    value: StrictFloat | StrictInt | None


class CategoryValue(Contract):
    kind: Literal["category"] = "category"
    value: str | None


class BooleanValue(Contract):
    kind: Literal["boolean"] = "boolean"
    value: StrictBool | None


class TextValue(Contract):
    kind: Literal["text"] = "text"
    value: str | None


class ReferenceValue(Contract):
    kind: Literal["asset_ref", "timeseries_ref", "vector_ref"]
    value: str


TypedValue = Annotated[Union[ScalarValue, CategoryValue, BooleanValue, TextValue, ReferenceValue], Field(discriminator="kind")]
EvidenceKind = Literal["sensor_measurement", "manual_measurement", "laboratory_assay", "visual_asset", "user_report", "expert_observation", "source_annotation", "experimental_assignment", "source_interpolated", "source_processed_unknown", "deterministic_derivative", "general_knowledge"]
AccessClass = Literal["observation_candidate", "reference_annotation", "internal_metadata", "restricted_raw"]
Availability = Literal["available", "not_recorded_in_release", "missing_asset", "unresolved_metadata", "unlinked", "parse_error", "quarantined", "blocked_license", "not_applicable"]
Scope = Literal["individual_plant", "pot", "box", "plot", "cultivation_cycle", "greenhouse", "camera_view", "image_only_case", "consultation_case", "leaf_or_organ", "knowledge_entry", "tier", "reservoir", "unknown"]


class Envelope(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    dataset_id: str
    release_id: str
    record_id: str
    source_record_key: str
    source_split: str | None = None
    provenance_refs: list[str] = Field(default_factory=list)
    source_annotation_refs: list[str] = Field(default_factory=list)
    processing_status: Literal["accepted", "preserved_unmapped", "quarantined", "rejected"]
    review_status: Literal["not_reviewed", "technical_review_passed", "technical_review_failed", "domain_review_required", "domain_review_passed"] = "not_reviewed"
    created_by_pipeline_version: str = "0.1.0"
    synthetic_fixture: bool = False
    source_fields: dict[str, JsonValue] = Field(default_factory=dict)


class Subject(Envelope):
    record_type: Literal["subject"] = "subject"
    subject_id: str
    subject_type: Scope
    source_subject_id: str
    species_name_original: str | None = None
    species_name_normalized: str | None = None
    cultivar_original: str | None = None
    cycle_id: str | None = None
    site_id: str | None = None
    parent_subject_ids: list[str] = Field(default_factory=list)
    identity_evidence_refs: list[str]
    identity_resolution: Literal["exact", "source_group", "position_only", "image_only", "unresolved"]


class Wavelength(Contract):
    band_kind: Literal["broadband", "narrowband", "unknown"]
    minimum_nm: StrictFloat | StrictInt | None = None
    maximum_nm: StrictFloat | StrictInt | None = None
    center_nm: StrictFloat | StrictInt | None = None
    fwhm_nm: StrictFloat | StrictInt | None = None
    source_description: str


class Asset(Envelope):
    record_type: Literal["asset"] = "asset"
    asset_id: str
    content_sha256: str
    media_type: str
    source_relative_path: str
    storage_uri: str
    parent_asset_id: str | None = None
    width: int | None = None
    height: int | None = None
    channels: int | None = None
    dtype: str | None = None
    bit_depth: int | None = None
    page_count: int | None = None
    modality: str
    wavelength_metadata: Wavelength | None = None
    calibration_refs: list[str] = Field(default_factory=list)
    subject_scope: Scope
    localization_status: Literal["source_localized", "group_view", "unresolved", "not_applicable"]
    times: Times = Field(default_factory=Times)
    availability: Availability
    decode_status: Literal["decoded", "failed", "not_run", "unsupported"]
    license_chain_refs: list[str]
    privacy_status: Literal["not_reviewed", "restricted", "reviewed_no_personal_data", "redacted_derivative"]
    access_class: AccessClass = "internal_metadata"


class Observation(Envelope):
    record_type: Literal["observation"] = "observation"
    observation_id: str
    subject_ref: str
    variable_id: str
    value: TypedValue
    unit: str | None
    source_value: JsonValue
    source_unit: str | None
    unit_status: Literal["resolved", "unresolved", "not_applicable"]
    scope: Scope
    spatial_context_refs: list[str] = Field(default_factory=list)
    times: Times = Field(default_factory=Times)
    availability: Availability
    evidence_kind: EvidenceKind
    measurement_method: str | None = None
    derivation_rule: str | None = None
    derivation_input_refs: list[str] = Field(default_factory=list)
    access_class: AccessClass

    @model_validator(mode="after")
    def preserve_evidence_type(self):
        if self.evidence_kind in {"source_annotation", "experimental_assignment", "general_knowledge"}:
            raise ValueError("Reference labels, treatments and general knowledge are not observations")
        if self.unit_status == "unresolved" and isinstance(self.value, ScalarValue) and self.value.value is not None:
            raise ValueError("Unknown source units cannot produce normalized scalar values")
        if self.evidence_kind == "deterministic_derivative" and not (self.derivation_rule and self.derivation_input_refs):
            raise ValueError("Derived observations require an explicit rule and inputs")
        return self


class Annotation(Envelope):
    record_type: Literal["annotation"] = "annotation"
    annotation_id: str
    subject_ref: str
    target_observation_refs: list[str] = Field(default_factory=list)
    annotation_type: str
    source_label: JsonValue
    canonical_concept_id: str | None = None
    mapping_status: Literal["exact", "broader_than_source", "ambiguous", "unmapped"]
    mapping_rule_id: str | None = None
    assertion_status: Literal["positive", "negative", "uncertain", "not_assessed"]
    assertion_scope: str
    verification_method: str | None = None
    verification_scope: Literal["record_level", "subject_level", "dataset_publication_level", "unverified"]
    verification_time: TimePoint | None = None
    source_author_or_role: str | None = None
    evidence_kind: EvidenceKind
    access_class: Literal["reference_annotation", "restricted_raw"] = "reference_annotation"


class Event(Envelope):
    record_type: Literal["event"] = "event"
    event_id: str
    subject_ref: str
    event_type: Literal["management_executed", "management_planned", "experimental_assignment", "sampling", "assay_collection", "harvest", "source_recommendation"]
    source_description: str
    times: Times
    evidence_kind: EvidenceKind
    access_class: AccessClass

    @model_validator(mode="after")
    def no_advice_as_execution(self):
        if self.event_type == "management_executed" and self.evidence_kind in {"general_knowledge", "source_annotation", "experimental_assignment"}:
            raise ValueError("A recommendation or treatment assignment is not an executed management event")
        return self


class TextTurn(Envelope):
    record_type: Literal["text_turn"] = "text_turn"
    turn_id: str
    subject_ref: str
    original_role: str
    turn_index: int | None
    text: str
    times: Times
    text_origin: Literal["source_original_turn", "source_reconstruction", "reference_answer"]
    access_class: AccessClass


class KnowledgeEntry(Envelope):
    record_type: Literal["knowledge_entry"] = "knowledge_entry"
    knowledge_id: str
    source_title: str | None
    content: str
    subject_ref: str | None = None
    evidence_kind: Literal["general_knowledge", "source_annotation"]
    access_class: Literal["reference_annotation", "restricted_raw"] = "reference_annotation"


class Link(Envelope):
    record_type: Literal["link"] = "link"
    link_id: str
    from_ref: str
    to_ref: str
    relation: str
    scope: Scope
    evidence_refs: list[str]
    link_status: Literal["exact", "source_group", "ambiguous", "unlinked"]
    temporal_policy: Literal["exact", "past_only", "interval_overlap", "not_applicable"]
    future_dependence: bool | None = False


class Locator(Contract):
    kind: Literal["csv", "xlsx", "json", "arrow", "text", "image", "file"]
    relative_path: str
    file_sha256: str
    row: int | None = None
    column: str | None = None
    sheet: str | None = None
    cell: str | None = None
    pointer: str | None = None
    batch: int | None = None
    character_start: int | None = None
    character_end: int | None = None

    @model_validator(mode="after")
    def location_is_explicit(self):
        from .common import safe_relative
        safe_relative(self.relative_path)
        if self.kind == "csv" and (self.row is None or self.row < 1):
            raise ValueError("CSV locator requires physical row number")
        if self.kind == "xlsx" and not (self.sheet and self.cell):
            raise ValueError("Workbook locator requires sheet and cell/range")
        if self.kind == "json" and self.pointer is None:
            raise ValueError("JSON locator requires JSON Pointer")
        if self.kind == "arrow" and (self.batch is None or self.row is None):
            raise ValueError("Arrow locator requires batch and row")
        if self.kind == "text" and (self.character_start is None or self.character_end is None):
            raise ValueError("Text locator requires source character span")
        return self


class Provenance(Envelope):
    record_type: Literal["provenance"] = "provenance"
    provenance_id: str
    locator: Locator
    parser_id: str
    parser_version: str
    mapping_rule_id: str
    input_provenance_refs: list[str] = Field(default_factory=list)
    transformations: list[str] = Field(default_factory=list)


class Capability(Envelope):
    record_type: Literal["capability"] = "capability"
    capability_id: str
    scope_level: Literal["dataset_inventory", "subject", "anchor"]
    subject_ref: str | None
    anchor_ref: str | None
    capability_key: str
    source_presence: Literal["present", "absent_in_audited_scope", "unknown"]
    parse_status: Literal["parsed", "unsupported", "failed", "not_run"]
    link_status: Literal["linked", "ambiguous", "unlinked", "not_applicable"]
    permission_status: Literal["allowed", "blocked", "unreviewed"]
    quality_status: Literal["accepted", "flagged", "quarantined", "unreviewed"]
    available_record_refs: list[str]
    evidence_kinds: list[EvidenceKind]
    measurement_scope: Scope
    unit_status: Literal["resolved", "unresolved", "not_applicable"]
    observed_points: int
    expected_points: int | None = None
    usable_as_data: bool
    reason_codes: list[str] = Field(default_factory=list)
    temporal_eligibility: Literal["needs_task_time_policy"] = "needs_task_time_policy"
    future_task_status: Literal["needs_task_validation"] = "needs_task_validation"

    @model_validator(mode="after")
    def usable_is_evidence_based(self):
        actual = (self.source_presence == "present" and self.parse_status == "parsed" and
                  self.link_status in {"linked", "not_applicable"} and self.permission_status == "allowed" and
                  self.quality_status == "accepted" and self.unit_status != "unresolved" and bool(self.available_record_refs))
        if self.usable_as_data != actual:
            raise ValueError("Capability availability must follow observed, parsed, linked, permitted evidence")
        return self


TABLE_MODELS = {"subjects": Subject, "assets": Asset, "observations": Observation, "annotations": Annotation,
                "events": Event, "text_turns": TextTurn, "knowledge_entries": KnowledgeEntry, "links": Link,
                "provenance": Provenance, "capabilities": Capability}


def stable_id(dataset, release, record_type, source_key):
    payload = json.dumps([dataset, release, record_type, source_key], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def logical_json(record):
    data = record.model_dump(mode="json")
    if "times" in data:
        data["times"]["ingested_at"] = None
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
