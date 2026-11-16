"""Record acquisition state machine. Gold is never loaded by this module."""
import base64
import copy
import hashlib
import json
from pathlib import Path
from typing import Literal

from .contracts import Assessment, Contract, EpisodeSpec, Payload, SourceTime, TaskContract, logical_hash
from .protocol import ProtocolError, parse_action


class EpisodeClosedError(RuntimeError):
    pass


class ResetRequiredError(RuntimeError):
    pass


class RequestConflictError(RuntimeError):
    pass


class ResolverError(RuntimeError):
    pass


def response_identity(observation):
    """Media bytes have their own verified SHA; avoid duplicating them in logs."""
    public = observation.model_dump(mode="json")
    for evidence in public["evidence"]:
        if evidence["payload"]["kind"] == "media":
            evidence["payload"]["media_base64"] = None
    return logical_hash(public)


class ResetOptions(Contract):
    episode_handle: str
    variant: Literal["interactive", "full_current_evidence", "initial_only", "revision_final_fresh"]


class PublicTool(Contract):
    target_id: str
    action_type: str
    description: str
    variable_id: str | None
    scope: str
    time_description: str
    cost: int
    status: Literal["available"] = "available"
    result_kind: str


class PublicEvidence(Contract):
    evidence_id: str
    scope: str
    variable_id: str | None
    modality: str | None
    evidence_kind: str
    record_time: SourceTime | None
    collection_time: SourceTime | None
    evidence_time_policy: str
    payload: Payload


class PublicInfo(Contract):
    action_status: str
    error_code: str | None
    phase_index: int
    remaining_cost: int
    remaining_actions: int


class PublicObservation(Contract):
    task: TaskContract
    evidence: list[PublicEvidence]
    newly_revealed_ids: list[str]
    available_tools: list[PublicTool]
    latest_assessment: Assessment | None
    phase_description: str
    phase_count: int
    info: PublicInfo


class MediaResolver:
    """Explicit per-episode media allowlist, no arbitrary path API."""
    def __init__(self, root, episode_id, assets: dict[str, str]):
        self.root = Path(root).resolve(strict=True)
        self.episode_id = episode_id
        self.assets = dict(assets)

    def read(self, episode_id, media_id, expected_sha256):
        if episode_id != self.episode_id or media_id not in self.assets:
            raise ResolverError("MEDIA_UNAVAILABLE")
        relative = Path(self.assets[media_id])
        if relative.is_absolute() or ".." in relative.parts:
            raise ResolverError("MEDIA_UNAVAILABLE")
        try:
            path = (self.root / relative).resolve(strict=True)
            if not path.is_relative_to(self.root) or not path.is_file():
                raise ResolverError("MEDIA_UNAVAILABLE")
            # The allowlist and frozen render hash determine the exact bytes.
            data = path.read_bytes()
        except OSError as exc:
            raise ResolverError("MEDIA_UNAVAILABLE") from exc
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ResolverError("MEDIA_INTEGRITY_FAILURE")
        return data


class PlantEnv:
    def __init__(self, episode: EpisodeSpec, resolver: MediaResolver | None = None, *, result_origin="scripted_validation"):
        if result_origin not in {"scripted_validation", "model_run", "deployment_smoke"}:
            raise ValueError("Unrecognized result origin")
        if result_origin == "model_run" and episode.identity.is_fixture:
            raise ValueError("Fixtures must use deployment_smoke or scripted_validation")
        self._episode = EpisodeSpec.model_validate_json(episode.model_dump_json())
        self._resolver = resolver
        from .knowledge import KnowledgeResolver
        self._knowledge = KnowledgeResolver(episode.knowledge_corpus) if episode.knowledge_corpus else None
        self._result_origin = result_origin
        self._active = False

    def reset(self, *, seed: int, options: ResetOptions):
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ProtocolError("INVALID_RESET")
        if options.episode_handle != self._episode.task.neutral_episode_handle or options.variant != self._episode.identity.variant_id:
            raise ProtocolError("UNKNOWN_EPISODE_HANDLE")
        self._units = {e.evidence_uid:e for e in self._episode.evidence}
        self._seed = seed
        self._targets = {t.target_id:t for t in self._episode.catalog}
        self._phase = len(self._episode.phases) - 1 if options.variant == "revision_final_fresh" else 0
        self._revealed = set(self._episode.phases[self._phase].initial_evidence_refs)
        if options.variant in {"full_current_evidence", "revision_final_fresh"}:
            self._reveal_all_current()
            if self._knowledge:
                for unit in self._knowledge.all_units():
                    self._units[unit.evidence_uid] = unit
                    self._revealed.add(unit.evidence_uid)
        self._actions = self._cost = 0
        self._assessment, self._assessment_view, self._final = None, None, None
        self._checkpoints, self._events, self._cache = [], [], {}
        self._terminated = self._truncated = False
        self._reason = None
        self._active = True
        observation = self._view("reset", None, self._revealed)
        return observation, observation.info.model_copy(deep=True)

    def _reveal_all_current(self):
        changed = True
        while changed:
            changed = False
            for target in self._episode.catalog:
                if self._allowed(target) and not set(target.evidence_refs) <= self._revealed:
                    self._revealed.update(target.evidence_refs)
                    changed = True

    def _allowed(self, target):
        return target.release_phase <= self._phase and set(target.prerequisite_evidence_refs) <= self._revealed and all(
            self._units[uid].release_phase <= self._phase and set(self._units[uid].prerequisite_evidence_refs) <= self._revealed
            for uid in target.evidence_refs)

    def available_actions(self) -> list[PublicTool]:
        if not self._active:
            raise ResetRequiredError("RESET_REQUIRED")
        if self._terminated or self._truncated or self._episode.identity.variant_id != "interactive":
            return []
        result = [PublicTool(target_id=t.target_id, action_type=t.action_type, description=t.description,
            variable_id=t.variable_id, scope=t.scope, time_description=t.time_description,
            cost=t.cost, result_kind=t.result_kind) for t in self._episode.catalog if self._allowed(t)]
        if self._knowledge:
            result.append(PublicTool(target_id='t_knowledge', action_type='SEARCH_SOURCE',
                description='Search the frozen independent plant knowledge library using query text (top_k 1 to 3). These documents are general knowledge, not case history.',
                variable_id=None,scope='general_knowledge',time_description='frozen source library',cost=1,result_kind='text'))
        return result

    def _public_evidence(self, uid):
        unit = self._units[uid]
        payload = unit.payload.model_copy(deep=True)
        if payload.kind == "media":
            if self._resolver is None:
                raise ResolverError("MEDIA_RESOLVER_REQUIRED")
            data = self._resolver.read(self._episode.episode_id, payload.media_id, payload.media_sha256)
            payload.media_base64 = base64.b64encode(data).decode("ascii")
        return PublicEvidence(evidence_id=unit.public_evidence_id, scope=unit.subject_scope,
            variable_id=unit.variable_id, modality=unit.modality, evidence_kind=unit.evidence_kind, payload=payload,
            record_time=unit.record_time.model_copy(deep=True) if unit.record_time else None,
            collection_time=unit.collection_time.model_copy(deep=True) if unit.collection_time else None,
            evidence_time_policy=unit.evidence_time_policy)

    def _view(self, status, error, newly):
        info = PublicInfo(action_status=status, error_code=error, phase_index=self._phase,
            remaining_cost=self._episode.budget.query_cost_budget-self._cost,
            remaining_actions=max(0,self._episode.budget.max_actions-self._actions))
        return PublicObservation(task=self._episode.task.model_copy(deep=True),
            evidence=[self._public_evidence(uid) for uid in sorted(self._revealed, key=lambda uid:self._units[uid].public_evidence_id)],
            newly_revealed_ids=sorted(self._units[uid].public_evidence_id for uid in newly),
            available_tools=self.available_actions(), latest_assessment=self._assessment.model_copy(deep=True) if self._assessment else None,
            phase_description=self._episode.phases[self._phase].description, phase_count=len(self._episode.phases), info=info)

    def _state(self):
        return {"phase":self._phase, "revealed":sorted(self._revealed), "actions":self._actions, "cost":self._cost,
            "assessment":self._assessment.model_dump(mode="json") if self._assessment else None,
            "assessment_view":sorted(self._assessment_view) if self._assessment_view is not None else None,
            "checkpoints":copy.deepcopy(self._checkpoints),"final":self._final.model_dump(mode="json") if self._final else None,
            "terminated":self._terminated,"truncated":self._truncated,"reason":self._reason}

    def _restore(self, state):
        from .contracts import FinalReport
        self._phase,self._revealed = state["phase"],set(state["revealed"])
        self._actions,self._cost = state["actions"],state["cost"]
        self._assessment = Assessment.model_validate(state["assessment"]) if state["assessment"] is not None else None
        self._assessment_view = set(state["assessment_view"]) if state["assessment_view"] is not None else None
        self._checkpoints = copy.deepcopy(state["checkpoints"])
        self._final = FinalReport.model_validate(state["final"]) if state["final"] is not None else None
        self._terminated,self._truncated,self._reason = state["terminated"],state["truncated"],state["reason"]

    def step(self, raw: str | bytes, *, request_id: str):
        if not self._active:
            raise ResetRequiredError("RESET_REQUIRED")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ProtocolError("INVALID_REQUEST_ID")
        data = raw.encode("utf-8", errors="surrogatepass") if isinstance(raw, str) else raw
        if not isinstance(data, bytes):
            raise ProtocolError("INVALID_TRANSPORT_PAYLOAD")
        raw_hash = hashlib.sha256(data).hexdigest()
        if request_id in self._cache:
            digest, response = self._cache[request_id]
            if digest != raw_hash:
                raise RequestConflictError("REQUEST_ID_CONTENT_CONFLICT")
            return copy.deepcopy(response)
        if self._terminated or self._truncated:
            raise EpisodeClosedError("EPISODE_CLOSED")
        before = self._state()
        phase_before = self._phase
        self._actions += 1
        status, error, action, newly, action_cost = "ok", None, None, set(), 0
        audit={}; penalty=0
        try:
            action, audit = parse_action(data,task=self._episode.task,with_audit=True)
            name = action.action
            if self._episode.budget.reserve_terminal_action and before['actions'] >= self._episode.budget.max_actions and name not in {'SUBMIT','STOP'}:
                raise ProtocolError('ONLY_TERMINAL_ACTION_REMAINS')
            if name == 'SEARCH_SOURCE':
                if self._knowledge is None or self._episode.identity.variant_id != 'interactive':
                    raise ProtocolError('KNOWLEDGE_SEARCH_UNAVAILABLE')
                if self._cost >= self._episode.budget.query_cost_budget:
                    error,status='BUDGET_EXHAUSTED','error'
                else:
                    for unit in self._knowledge.search(action.query,action.top_k):
                        self._units[unit.evidence_uid]=unit
                        if unit.evidence_uid not in self._revealed:
                            newly.add(unit.evidence_uid)
                        self._revealed.add(unit.evidence_uid)
                    action_cost=1
                    status='revealed' if newly else 'no_new_results'
            elif name in {"INSPECT", "QUERY", "ASK"}:
                target = self._targets.get(action.target_id)
                if self._episode.identity.variant_id != "interactive" or target is None or name != target.action_type or not self._allowed(target):
                    raise ProtocolError("TARGET_UNAVAILABLE")
                if self._cost + target.cost > self._episode.budget.query_cost_budget:
                    error, status = "BUDGET_EXHAUSTED", "error"
                else:
                    # Read/verify media before committing revealed state. A
                    # resolver failure is infrastructure, never a model error.
                    for uid in target.evidence_refs:
                        self._public_evidence(uid)
                    newly = set(target.evidence_refs) - self._revealed
                    self._revealed.update(target.evidence_refs)
                    action_cost = target.cost
                    status = "revealed" if newly else "already_revealed"
            elif name == "ASSESS":
                self._assessment = action.assessment.model_copy(deep=True)
                self._assessment_view = set(self._revealed)
            elif name == "ADVANCE_RECORD":
                if self._phase + 1 >= len(self._episode.phases) or self._episode.identity.variant_id == "revision_final_fresh":
                    raise ProtocolError("RECORD_ADVANCE_UNAVAILABLE")
                phase = self._episode.phases[self._phase]
                if phase.require_checkpoint_before_advance and (self._assessment is None or self._assessment_view != self._revealed):
                    raise ProtocolError("CHECKPOINT_REQUIRED")
                self._checkpoints.append({"phase":self._phase,"visible_uids":sorted(self._revealed),
                    "assessment":self._assessment.model_dump(mode="json") if self._assessment else None})
                self._phase += 1
                newly = set(self._episode.phases[self._phase].initial_evidence_refs) - self._revealed
                self._revealed.update(newly)
                if self._episode.identity.variant_id == "full_current_evidence":
                    self._reveal_all_current()
                    newly = self._revealed - set(before["revealed"])
                self._assessment, self._assessment_view = None, None
            elif name in {"SUBMIT", "STOP"}:
                if action.report is not None and action.report.decision is not None:
                    for proposed in action.report.decision.actions:
                        if (self._episode.agentic is not None or self._episode.task.decision_action_domain) and proposed.code not in self._episode.task.decision_action_domain:
                            raise ProtocolError('INVALID_PUBLIC_DECISION_CODE')
                        if (self._episode.agentic is not None or self._episode.task.decision_condition_domain) and not set(proposed.conditions) <= set(self._episode.task.decision_condition_domain):
                            raise ProtocolError('INVALID_PUBLIC_DECISION_CONDITION')
                        if proposed.code == "request_external_measurement":
                            if proposed.target not in self._episode.task.external_measurement_domain:
                                raise ProtocolError("INVALID_PUBLIC_DECISION_TARGET")
                        elif proposed.target is not None:
                            raise ProtocolError("INVALID_PUBLIC_DECISION_TARGET")
                self._final = action.report.model_copy(deep=True) if action.report is not None else None
                self._terminated = True
                self._reason = "submitted" if self._final is not None else "abandoned_without_report"
        except ProtocolError as exc:
            audit=getattr(exc,'protocol_audit',audit)
            error, status = str(exc), "error"
            if self._episode.agentic is not None and getattr(exc,'public_details',None):
                status='error; public action schema details: '+json.dumps(exc.public_details,ensure_ascii=True,separators=(',',':'))
            action_cost = min(1, self._episode.budget.query_cost_budget - self._cost)
            penalty=action_cost
        except ResolverError:
            # Failed delivery is infrastructure. Preserve exactly the prior
            # state so a runner can retry the same transport request safely.
            self._restore(before)
            raise
        self._cost += action_cost
        limit=self._episode.budget.max_actions+int(self._episode.budget.reserve_terminal_action)
        if not self._terminated and self._actions >= limit:
            self._truncated, self._reason = True, "action_limit"
        try:
            observation = self._view(status, error, newly)
        except ResolverError:
            self._restore(before)
            raise
        response = (observation, 0.0, self._terminated, self._truncated, observation.info.model_copy(deep=True))
        event = {"event_index":len(self._events), "request_id":request_id,"action_raw_hash":raw_hash,
            "protocol_audit":audit,"tool_cost":action_cost if error is None else 0,"invalid_action_penalty":penalty,
            "parsed_action":action.model_dump(mode="json") if action else None,"parse_status":"parsed" if action else "invalid",
            "pre_state_hash":logical_hash(before),"post_state_hash":logical_hash(self._state()),
            "phase_before":phase_before,"phase_after":self._phase,"newly_revealed_uids":sorted(newly),
            "visible_uids_after":sorted(self._revealed),"action_cost":action_cost,"cumulative_cost":self._cost,"action_count":self._actions,
            "terminated":self._terminated,"truncated":self._truncated,"termination_reason":self._reason,
            "public_error":error,"public_response_hash":response_identity(observation),
            "action_raw_base64":base64.b64encode(data[:65537]).decode("ascii"),
            "action_raw_length":len(data),"raw_storage_complete":len(data) <= 65537,
            "asset_payload_hashes":[e.payload.media_sha256 for e in observation.evidence if e.payload.kind == "media"]}
        # Keep feedback text and media identities, not duplicate image bytes.
        feedback=observation.model_dump(mode='json')
        for evidence in feedback['evidence']:
            evidence['payload'].pop('media_base64',None)
        event['public_feedback']=feedback
        self._events.append(event)
        self._cache[request_id] = (raw_hash, copy.deepcopy(response))
        return response

    def _private_trace(self):
        """Not part of the public RPC allowlist; evaluator/test process only."""
        return {"episode_id":self._episode.episode_id,"episode_hash":logical_hash(self._episode),
            "protocol_version":self._episode.task.protocol_version,"variant":self._episode.identity.variant_id,
            "seed":self._seed,"seed_policy":"frozen_deterministic_episode_no_runtime_randomness",
            "result_origin":self._result_origin, "events":copy.deepcopy(self._events),"state":copy.deepcopy(self._state())}

    def close(self):
        self._active = False
