"""Private episode/gold and public protocol are distinct strict contracts."""
import hashlib
import json
from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, validate_assignment=True)


class Category(Contract):
    kind: Literal["category"] = "category"
    label: str


class CategorySet(Contract):
    kind: Literal["category_set"] = "category_set"
    labels: list[str]


class Number(Contract):
    kind: Literal["number"] = "number"
    value: StrictInt | StrictFloat
    unit: str


class Comparison(Contract):
    kind: Literal["comparison"] = "comparison"
    label: Literal["increased", "decreased", "unchanged"]


class Boolean(Contract):
    kind: Literal["boolean"] = "boolean"
    value: StrictBool


TypedAnswer = Annotated[Union[Category, CategorySet, Number, Comparison, Boolean], Field(discriminator="kind")]


class EvidenceLink(Contract):
    evidence_id: str
    relation: Literal["support", "contradict", "context"]


class Answer(Contract):
    slot_id: str
    determination: Literal["supported", "undetermined"] = "supported"
    value: TypedAnswer | None
    evidence_ids: list[str] = Field(default_factory=list)
    # Exposed only for explicit relation-identification tasks.
    evidence_links: list[EvidenceLink] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_determination(self):
        if (self.determination == "undetermined") != (self.value is None):
            raise ValueError("Undetermined requires null; supported requires a typed answer")
        return self


class Limitation(Contract):
    code: Literal["MISSING_DECISIVE_RECORD", "INSUFFICIENT_CURRENT_EVIDENCE", "UNRESOLVABLE_IN_PACKAGE", "SCOPE_LIMITATION", "TIME_LIMITATION", "EXTERNAL_REVIEW_REQUIRED"]
    evidence_ids: list[str]


class Assessment(Contract):
    answers: list[Answer]
    limitations: list[str | Limitation] = Field(default_factory=list)


ActionCode = Literal["continue_recorded_plan", "monitor_with_followup", "request_external_measurement", "seek_domain_expert", "adjust_water_increase", "adjust_water_decrease", "improve_drainage", "adjust_nutrients_review", "adjust_light_review", "adjust_environment_review", "isolation_review", "sanitation_review"]


class DecisionAction(Contract):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    target: str | None = None
    conditions: list[str] = Field(default_factory=list)


class Decision(Contract):
    disposition: Literal["report_only", "defer", "external_check", "refer", "care_plan"]
    actions: list[DecisionAction] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class FinalReport(Assessment):
    answers: list[Answer] = Field(default_factory=list)
    decision: Decision | None = None


class GetAction(Contract):
    action: Literal["INSPECT", "QUERY", "ASK"]
    target_id: str = Field(pattern=r"^t_[a-z0-9]+$")


class SearchAction(Contract):
    action: Literal["SEARCH_SOURCE"]
    query: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=3, ge=1, le=3)


class AssessAction(Contract):
    action: Literal["ASSESS"]
    assessment: Assessment


class AdvanceAction(Contract):
    action: Literal["ADVANCE_RECORD"]


class SubmitAction(Contract):
    action: Literal["SUBMIT"]
    report: FinalReport


class StopAction(Contract):
    action: Literal["STOP"]
    report: FinalReport | None = None


AgentAction = Annotated[Union[GetAction, SearchAction, AssessAction, AdvanceAction, SubmitAction, StopAction], Field(discriminator="action")]


class TableCell(Contract):
    column: str
    value: StrictInt | StrictFloat | StrictBool | str | None
    unit: str | None = None


class Payload(Contract):
    kind: Literal["scalar", "table", "text", "availability_statement", "media"]
    value: StrictInt | StrictFloat | StrictBool | str | None = None
    unit: str | None = None
    rows: list[list[TableCell]] = Field(default_factory=list)
    media_id: str | None = None
    media_mime: Literal["image/jpeg", "image/png"] | None = None
    media_sha256: str | None = None
    media_width: int | None = Field(default=None, ge=1)
    media_height: int | None = Field(default=None, ge=1)
    media_base64: str | None = None

    @model_validator(mode="after")
    def check_kind(self):
        if self.kind == "media" and not all((self.media_id, self.media_mime, self.media_sha256, self.media_width, self.media_height)):
            raise ValueError("Media requires a real render identity and dimensions")
        if self.kind != "media" and any(x is not None for x in (self.media_id, self.media_mime, self.media_sha256, self.media_width, self.media_height, self.media_base64)):
            raise ValueError("Non-media payload cannot contain a media channel")
        if self.kind != "table" and self.rows:
            raise ValueError("Rows require a table payload")
        return self


class SourceTime(Contract):
    kind: Literal["date", "datetime", "experiment_day", "dialog_turn", "unknown"]
    value: str | StrictInt | StrictFloat | None
    precision: str
    timezone_status: Literal["unknown", "source_declared", "not_applicable"]


class EvidenceUnit(Contract):
    evidence_uid: str
    public_evidence_id: str = Field(pattern=r"^e_[a-z0-9]+$")
    canonical_refs: list[str]
    provenance_refs: list[str]
    subject_scope: str
    subject_refs: list[str]
    variable_id: str | None = None
    scope_link_refs: list[str] = Field(default_factory=list)
    modality: str | None = None
    text_role: str | None = None
    payload: Payload
    measurement_method: str
    evidence_kind: str
    record_time: SourceTime | None = None
    collection_time: SourceTime | None = None
    available_time: SourceTime | None = None
    evidence_time_policy: Literal["asof_evidence", "curated_record_review"]
    release_phase: int = Field(ge=0)
    prerequisite_evidence_refs: list[str] = Field(default_factory=list)
    access_class: Literal["observation_candidate", "reference_annotation", "restricted_raw", "internal_metadata"]
    permission_status: Literal["allowed", "blocked", "unreviewed"]
    quality_status: Literal["accepted", "flagged", "quarantined", "unreviewed"]
    render_asset_ref: str | None = None
    render_transform_refs: list[str] = Field(default_factory=list)
    semantic_atom_ids: list[str] = Field(min_length=1)
    future_dependence: bool | None = None


class ToolTarget(Contract):
    target_id: str = Field(pattern=r"^t_[a-z0-9]+$")
    action_type: Literal["INSPECT", "QUERY", "ASK"]
    description: str
    variable_id: str | None = None
    scope: str
    time_description: str
    cost: int = Field(default=1, ge=1)
    result_kind: Literal["media", "scalar", "table", "text", "availability_statement"]
    evidence_refs: list[str] = Field(min_length=1)
    release_phase: int = Field(ge=0)
    prerequisite_evidence_refs: list[str] = Field(default_factory=list)


class CitationRequirement(Contract):
    evaluate_at: str = "final"
    policy: Literal["not_required", "visible_only", "verified_support"]
    required: bool = True
    kinds: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    require_relations: bool = False
    explanation: str


class Slot(Contract):
    slot_id: str
    question: str
    value_kind: Literal["category", "category_set", "number", "comparison", "boolean"]
    unit: str | None = None
    candidate_domain: list[str] = Field(default_factory=list)
    output_location: str = "answers"
    citation_requirements: list[CitationRequirement] = Field(default_factory=list)


class TaskContract(Contract):
    protocol_version: Literal["plantenv-1.0.0", "plantenv-2.0.0"] = "plantenv-1.0.0"
    neutral_episode_handle: str
    subject_description: str
    scope: str
    target_time_description: str
    target_time: SourceTime | None = None
    goal_text: str
    assessment_slots: list[Slot]
    require_evidence: bool = True
    require_decision: bool = False
    require_phase_checkpoints: bool = False
    withholding_rationale: str
    external_measurement_domain: list[str] = Field(default_factory=list)
    knowledge_search_available: bool = False
    knowledge_scope_description: str | None = None
    decision_action_domain: list[str] = Field(default_factory=list)
    decision_condition_domain: list[str] = Field(default_factory=list)
    report_protocol: str = "legacy-v1"
    normalization_policy: list[str] = Field(default_factory=list)


class Budget(Contract):
    profile_id: str = "core-v0"
    max_actions: int = Field(default=16, ge=1)
    query_cost_budget: int = Field(default=8, ge=0)
    max_phases: int = Field(default=3, ge=1)
    max_query_targets: int = Field(default=12, ge=0)
    max_unique_media_assets: int = Field(default=8, ge=0)
    max_total_visible_table_values: int = Field(default=512, ge=0)
    max_total_visible_text_chars: int = Field(default=24000, ge=0)
    reserve_terminal_action: bool = False


class Identity(Contract):
    base_case_id: str
    leakage_group_id: str
    inference_group_id: str
    context_group_ids: list[str]
    split: Literal["engineering", "development", "pilot_holdout", "reserve"]
    variant_id: Literal["interactive", "full_current_evidence", "initial_only", "revision_final_fresh"]
    is_fixture: bool


class SourceScope(Contract):
    dataset_id: str
    subject_refs: list[str]
    scope: str
    anchor_ref: str
    canonical_record_refs: list[str]
    provenance_refs: list[str]


class Mask(Contract):
    T1: bool = True
    T2: bool = True
    T3: bool = True
    T4: bool = False
    T5: bool = False


class Admission(Contract):
    role: Literal["core", "auxiliary", "blocked", "record_agent", "reference_agent", "component_only", "engineering_sanity"]
    task_mask: Mask
    scoring_mask: Mask
    reason_codes: list[str]
    review_tier: Literal["deterministic_record", "source_reference", "domain_reviewed", "unverified_draft"]
    modalities_present: list[str]
    modalities_required_by_validated_task: list[str]
    visual_role: Literal["required", "supporting", "decorative", "unvalidated"]


class Phase(Contract):
    phase_id: str
    initial_evidence_refs: list[str]
    require_checkpoint_before_advance: bool
    description: str


class KnowledgeCorpus(Contract):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    namespace: str


class AgenticMetadata(Contract):
    primary_p: Literal["P1", "P2", "P3", "P4", "P5"]
    template_id: str
    task_kind: Literal["investigative", "stop_control", "component"]
    answer_basis: Literal["deterministic_record", "source_reference", "domain_validated"]
    subtype: str | None = None
    human_review_status: Literal["pending", "completed"] = "pending"
    exposure_status: str
    choice_opportunity: str


class EpisodeSpec(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    benchmark_version: str
    episode_id: str
    foundation_ref: str
    builder_id: str
    builder_version: str
    source_scope: SourceScope
    identity: Identity
    admission: Admission
    task: TaskContract
    evidence: list[EvidenceUnit]
    catalog: list[ToolTarget]
    phases: list[Phase] = Field(min_length=1)
    budget: Budget
    visibility_policy_ref: str
    render_profile_ref: str
    license_scope_refs: list[str]
    content_hash: str = ""
    agentic: AgenticMetadata | None = None
    knowledge_corpus: KnowledgeCorpus | None = None

    @model_validator(mode="after")
    def validate_graph(self):
        if self.task.knowledge_search_available != (self.knowledge_corpus is not None):
            raise ValueError("Knowledge search requires an explicit frozen source corpus")
        uid = {e.evidence_uid:e for e in self.evidence}
        if len(uid) != len(self.evidence) or len({e.public_evidence_id for e in self.evidence}) != len(self.evidence):
            raise ValueError("Evidence IDs must be unique")
        if len({t.target_id for t in self.catalog}) != len(self.catalog):
            raise ValueError("Targets must be unique")
        if len(self.catalog) > self.budget.max_query_targets or len(self.phases) > self.budget.max_phases:
            raise ValueError("Episode exceeds declared finite profile")
        if len({s.slot_id for s in self.task.assessment_slots}) != len(self.task.assessment_slots):
            raise ValueError("Assessment slots must be unique")
        for e in self.evidence:
            same_subject=bool(set(e.subject_refs)&set(self.source_scope.subject_refs))
            same_scope=e.subject_scope==self.source_scope.scope
            if (not same_subject or not same_scope) and (not e.scope_link_refs or not set(e.scope_link_refs)<=set(self.source_scope.canonical_record_refs)):
                raise ValueError('Cross-subject or shared-scope evidence requires explicit canonical link proofs')
            if e.release_phase >= len(self.phases) or not set(e.prerequisite_evidence_refs) <= uid.keys():
                raise ValueError("Invalid evidence phase or prerequisites")
            if e.access_class != "observation_candidate" or e.permission_status != "allowed" or e.quality_status != "accepted":
                raise ValueError("Ineligible evidence cannot enter an executable episode")
            if e.evidence_time_policy == "asof_evidence" and (e.available_time is None or e.available_time.kind == "unknown" or e.future_dependence is not False):
                raise ValueError("As-of requires actual availability and no future dependence")
            if e.evidence_time_policy == "asof_evidence":
                from .time_policy import available_by
                if not available_by(e.available_time,self.task.target_time):
                    raise ValueError("As-of evidence must be provably available by the target")
        for index, phase in enumerate(self.phases):
            if not set(phase.initial_evidence_refs) <= uid.keys() or any(uid[x].release_phase > index for x in phase.initial_evidence_refs):
                raise ValueError("Phase initial evidence contains invalid/future references")
        for target in self.catalog:
            if not set(target.evidence_refs + target.prerequisite_evidence_refs) <= uid.keys() or target.release_phase >= len(self.phases):
                raise ValueError("Target contains invalid references")
            if any(uid[x].release_phase > target.release_phase for x in target.evidence_refs):
                raise ValueError("Target would reveal future evidence")
            if target.action_type == "INSPECT" and any(uid[x].payload.kind != "media" for x in target.evidence_refs):
                raise ValueError("INSPECT must return media")
            if target.action_type == "ASK" and any(uid[x].text_role != "reviewed_original_user_followup" for x in target.evidence_refs):
                raise ValueError("ASK requires reviewed original followup spans")
        media = {e.payload.media_id for e in self.evidence if e.payload.kind == "media"}
        values = sum(sum(len(row) for row in e.payload.rows) + int(e.payload.kind == "scalar") for e in self.evidence)
        chars = sum(len(str(e.payload.value or "")) for e in self.evidence if e.payload.kind in {"text", "availability_statement"})
        if len(media) > self.budget.max_unique_media_assets or values > self.budget.max_total_visible_table_values or chars > self.budget.max_total_visible_text_chars:
            raise ValueError("Evidence payload exceeds frozen context profile")
        if not self.identity.is_fixture and self.identity.split == "engineering":
            raise ValueError("Real data cannot masquerade as a synthetic fixture")
        if self.admission.role == "core" and (not self.catalog or self.admission.visual_role != "required"):
            raise ValueError("Core requires real interaction and validated visual dependence")
        return self


class Requirement(Contract):
    group_id: str
    any_of: list[str] = Field(min_length=1)
    weight: float = Field(default=1, gt=0)


class Certificate(Contract):
    certificate_id: str
    requirements: list[Requirement] = Field(min_length=1)


class CitationRule(Contract):
    atom_id: str
    relation: Literal["support", "contradict", "context"]


class AcceptedAnswer(Contract):
    determination: Literal["supported", "undetermined"]
    value: TypedAnswer | None
    certificates: list[Certificate] = Field(min_length=1)
    compatible_citations: list[CitationRule]


class SlotRule(Contract):
    slot_id: str
    accepted_answer_alternatives: list[AcceptedAnswer]
    insufficiency_allowed: bool
    insufficiency_certificates: list[Certificate] = Field(default_factory=list)
    terminal_goal_policy: Literal["resolve_available_targets", "acknowledge_unresolvable", "complete_record_review"]
    support_coverage: Literal["complete_for_finite_catalog", "unreviewed"]
    absolute_tolerance: str = "0"
    relative_tolerance: str = "0"
    evidence_required: bool = True
    auxiliary_source_label: TypedAnswer | None = None

    @model_validator(mode="after")
    def precision(self):
        for value in (self.absolute_tolerance, self.relative_tolerance):
            parsed = Decimal(value)
            if not parsed.is_finite() or parsed < 0:
                raise ValueError("Tolerance must be a finite nonnegative frozen value")
        return self


class PrimitiveRating(Contract):
    atom: str
    rating: Literal["preferred", "acceptable", "suboptimal", "unsupported", "contraindicated", "not_adjudicated"]


class Plan(Contract):
    plan_id: str
    required_action_groups: list[Requirement] = Field(min_length=1)
    optional_action_atoms: list[str]
    evidence_certificates: list[Certificate]


class RiskRule(Contract):
    rule_id: str
    all_of_action_atoms: list[str] = Field(min_length=1)
    severity: Literal[1, 2]
    evidence_refs: list[str]
    review_refs: list[str]


class DecisionRubric(Contract):
    accepted_dispositions: list[str]
    acceptable_plans: list[Plan]
    primitive_ratings: list[PrimitiveRating]
    forbidden_combinations: list[list[str]]
    risk_rules: list[RiskRule]
    reference_agreement_only: bool
    safety_eligible: bool
    allowed_action_domain_fully_adjudicated: bool


class PhaseGold(Contract):
    phase_id: str
    target_time: str
    target_scope: str
    slot_rules: list[SlotRule]
    decision_rubric: DecisionRubric | None = None


class Transition(Contract):
    from_phase: int
    to_phase: int
    slot_id: str
    classification: Literal["belief_revision", "temporal_monitoring"]
    rule: Literal["resolve_uncertainty", "correct_with_new_record", "retain_under_new_record", "refine_scope", "temporal_update"]
    gold_stable: bool
    accepted_pairs: list[tuple[int, int]]
    new_evidence_certificates: list[Certificate]


class ProgressPredicate(Contract):
    predicate_id: str
    kind: Literal["valid_final", "state_slot", "grounding_slot", "terminal_slot", "decision_pass", "checkpoint_exists", "evidence_atom"]
    mode: Literal["final_snapshot", "fixed_phase_checkpoint", "event_occurred"]
    slot_id: str | None = None
    phase: int | None = None
    atom_id: str | None = None
    weight: float = Field(default=1, gt=0)
    dependencies: list[str] = Field(default_factory=list)
    mandatory_for_success: bool


class ReviewRecord(Contract):
    reviewer_id: str
    qualification: str
    reviewed_at: str
    input_hash: str
    decision: Literal["accept", "reject", "revise"]
    reason: str
    tier: Literal["technical", "domain"]
    independence_group: str | None = None
    verification_ref: str | None = None
    rule_hash: str | None = None
    is_automated: bool = False


class ReferenceCost(Contract):
    status: Literal["exact", "incomplete", "unreachable", "not_run"]
    cost: int | None
    path: list[str]
    visited_states: int
    input_hash: str


class GoldSpec(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    gold_id: str
    episode_id: str
    gold_version: str
    foundation_hash: str
    template_hash: str
    derivation_rule_refs: list[str]
    review_tier: Literal["deterministic_record", "source_reference", "domain_reviewed", "unverified_draft"]
    reviewer_records: list[ReviewRecord]
    adjudication_status: str
    scoring_mask: Mask
    phase_gold: list[PhaseGold]
    transition_rules: list[Transition]
    progress_predicates: list[ProgressPredicate]
    reference_cost_certificate: ReferenceCost
    source_refs: list[str]
    provenance_refs: list[str]
    known_limitations: list[str]
    audit_status: Literal["not_run", "passed", "failed"]
    is_fixture: bool


class MetricResult(Contract):
    metric_name: str
    value: float | None
    eligible: bool
    eligibility_reason: str
    numerator: float | None
    denominator: float | None
    level: Literal["step", "checkpoint", "episode", "group", "aggregate"]
    input_trace_hash: str
    gold_hash: str
    scoring_version: str = "plantscore-1.0.0"
    components: dict[str, StrictBool | StrictInt | StrictFloat | str | None] = Field(default_factory=dict)
    diagnostic_flags: list[str] = Field(default_factory=list)


class Checkpoint(Contract):
    phase: int = Field(ge=0)
    visible_uids: list[str]
    assessment: Assessment | None


class TraceState(Contract):
    phase: int = Field(ge=0)
    revealed: list[str]
    actions: int = Field(ge=0)
    cost: int = Field(ge=0)
    assessment: Assessment | None
    assessment_view: list[str] | None
    checkpoints: list[Checkpoint]
    final: FinalReport | None
    terminated: bool
    truncated: bool
    reason: Literal["submitted","abandoned_without_report","action_limit"] | None


class TraceEvent(Contract):
    event_index: int = Field(ge=0)
    request_id: str
    action_raw_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_raw_base64: str
    action_raw_length: int = Field(ge=0)
    raw_storage_complete: bool
    parsed_action: AgentAction | None
    parse_status: Literal["parsed","invalid"]
    pre_state_hash: str
    post_state_hash: str
    phase_before: int = Field(ge=0)
    phase_after: int = Field(ge=0)
    newly_revealed_uids: list[str]
    visible_uids_after: list[str]
    action_cost: int = Field(ge=0)
    cumulative_cost: int = Field(ge=0)
    action_count: int = Field(ge=1)
    terminated: bool
    truncated: bool
    termination_reason: Literal["submitted","abandoned_without_report","action_limit"] | None
    public_error: str | None
    public_response_hash: str
    asset_payload_hashes: list[str]
    protocol_audit: dict = Field(default_factory=dict)
    tool_cost: int = Field(default=0, ge=0)
    invalid_action_penalty: int = Field(default=0, ge=0)
    public_feedback: dict = Field(default_factory=dict)


class RunTrace(Contract):
    episode_id: str
    episode_hash: str
    protocol_version: Literal["plantenv-1.0.0", "plantenv-2.0.0"]
    variant: Literal["interactive","full_current_evidence","initial_only","revision_final_fresh"]
    seed: int
    seed_policy: Literal["frozen_deterministic_episode_no_runtime_randomness"]
    result_origin: Literal["scripted_validation","model_run","deployment_smoke"]
    events: list[TraceEvent]
    state: TraceState


def logical_hash(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
