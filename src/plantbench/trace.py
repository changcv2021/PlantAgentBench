"""Offline environment replay. This verifies execution, not model authorship."""
import base64
import copy
import hashlib

from .contracts import logical_hash,RunTrace
from .environment import PlantEnv, ResetOptions


class TraceIntegrityError(ValueError):
    pass


class _FrozenMediaIdentity:
    # The acquisition worker verifies real render bytes before delivery.
    # Replay checks its frozen content identity without loading those bytes.
    def read(self, episode_id, media_id, expected_sha256):
        return b""


def verify_trace(episode, trace):
    try:
        RunTrace.model_validate(trace)
    except ValueError as exc:
        raise TraceIntegrityError("INVALID_TRACE_SCHEMA") from exc
    if trace.get("episode_hash") != logical_hash(episode) or trace.get("episode_id") != episode.episode_id:
        raise TraceIntegrityError("TRACE_EPISODE_IDENTITY_MISMATCH")
    if not isinstance(trace.get("events"), list) or len(trace["events"]) > episode.budget.max_actions + int(episode.budget.reserve_terminal_action):
        raise TraceIntegrityError("INVALID_EVENT_COUNT")
    env = PlantEnv(episode, _FrozenMediaIdentity(), result_origin=trace["result_origin"])
    try:
        env.reset(seed=trace["seed"],options=ResetOptions(episode_handle=episode.task.neutral_episode_handle,variant=episode.identity.variant_id))
        for recorded in trace["events"]:
            raw = base64.b64decode(recorded["action_raw_base64"],validate=True)
            if recorded["raw_storage_complete"]:
                if len(raw) != recorded["action_raw_length"] or hashlib.sha256(raw).hexdigest() != recorded["action_raw_hash"]:
                    raise TraceIntegrityError("RAW_ACTION_INTEGRITY_FAILURE")
            elif len(raw) != 65537 or recorded["action_raw_length"] <= 65537:
                raise TraceIntegrityError("INVALID_OVERSIZE_ACTION_RECORD")
            env.step(raw,request_id=recorded["request_id"])
            actual = env._private_trace()["events"][-1]
            expected = copy.deepcopy(recorded)
            if not recorded["raw_storage_complete"]:
                # Both payloads exceed the protocol limit; their only allowed
                # semantics is the same size-error event. The original raw
                # hash remains an audit identifier, not a replayable payload.
                for key in ("action_raw_hash","action_raw_length","raw_storage_complete"):
                    expected[key] = actual[key]
            if actual != expected:
                raise TraceIntegrityError("EVENT_REPLAY_MISMATCH")
        expected_trace = env._private_trace()
        for key in expected_trace:
            if key != "events" and expected_trace[key] != trace.get(key):
                raise TraceIntegrityError("TERMINAL_REPLAY_MISMATCH")
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        if isinstance(exc, TraceIntegrityError):
            raise
        raise TraceIntegrityError("INVALID_TRACE_CONTRACT") from exc
    finally:
        env.close()
    return {"status":"passed","trace_hash":logical_hash(trace),"episode_hash":logical_hash(episode),
            "verification":"deterministic_environment_replay","model_authorship_verified":False}
