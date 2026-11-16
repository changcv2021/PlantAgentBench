"""Bounded parser with declared normalization; no semantic repair or execution."""
import copy
import json
import re

from pydantic import TypeAdapter, ValidationError

from .contracts import AgentAction, Answer


class ProtocolError(ValueError):
    pass


def reject_constant(value):
    raise ProtocolError("NONFINITE_JSON")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _public_check(value, task, *, canonical=False):
    if task is None or task.protocol_version != 'plantenv-2.0.0':
        return
    from .care_protocol import check_report
    check_report(value,task)
    report=value.get('report') if value.get('action') in {'SUBMIT','STOP'} else value.get('assessment')
    if not isinstance(report,dict):return
    slots={s.slot_id:s for s in task.assessment_slots if s.output_location=='answers'}
    if report.get('decision') is not None and not task.require_decision:
        raise ProtocolError('DECISION_NOT_IN_TASK_CONTRACT')
    for answer in report.get('answers',[]):
        if not isinstance(answer,dict):continue
        slot=slots.get(answer.get('slot_id'))
        if slot is None:raise ProtocolError('SLOT_NOT_IN_TASK_CONTRACT')
        relation=any(r.require_relations for r in slot.citation_requirements)
        if canonical and not relation and 'evidence_links' in answer:
            raise ProtocolError('USE_EVIDENCE_IDS')
        typed=answer.get('value')
        if typed is not None and isinstance(typed,dict) and typed.get('kind')!=slot.value_kind:
            raise ProtocolError('VALUE_KIND_NOT_IN_TASK_CONTRACT')


def _normalize_report(value, task, applied, diagnostics):
    if task is None or task.protocol_version!='plantenv-2.0.0':return value
    value=copy.deepcopy(value)
    report=value.get('report') if value.get('action') in {'SUBMIT','STOP'} else value.get('assessment')
    if not isinstance(report,dict):return value
    slots={s.slot_id:s for s in task.assessment_slots}
    for answer in report.get('answers',[]):
        if not isinstance(answer,dict):continue
        slot=slots.get(answer.get('slot_id'))
        relation=bool(slot and any(r.require_relations for r in slot.citation_requirements))
        if 'evidence_links' in answer and not relation:
            links=answer['evidence_links']
            if not isinstance(links,list) or any(not isinstance(x,dict) or set(x)!={'evidence_id','relation'}
                or not isinstance(x['evidence_id'],str) or x['relation'] not in {'support','context','contradict'} for x in links):
                raise ProtocolError('INVALID_LEGACY_EVIDENCE_LINKS')
            ids=[x['evidence_id'] for x in links]
            if 'evidence_ids' in answer and set(answer['evidence_ids'])!=set(ids):
                raise ProtocolError('CONFLICTING_EVIDENCE_ENCODINGS')
            answer['evidence_ids']=ids;del answer['evidence_links'];applied.append('ordinary_evidence_links_to_ids')
        typed=answer.get('value')
        if (slot and 'undetermined' not in slot.candidate_domain
                and typed=={'kind':'category','label':'undetermined'}
                and answer.get('determination','supported') in {'supported','undetermined'}):
            answer.update(determination='undetermined',value=None);applied.append('explicit_undetermined_category')
    # Legacy duplicate removal is allowed only when it conveys exactly the same
    # submitted actions/disposition and citations. Never fill a missing decision.
    decision=report.get('decision')
    if task.require_decision and isinstance(decision,dict):
        for answer in list(report.get('answers',[])):
            if not isinstance(answer,dict) or answer.get('slot_id') in slots:continue
            ident=answer.get('slot_id')
            if ident not in {'actions','next_disposition'}:continue
            typed=answer.get('value') or {}
            if not isinstance(typed,dict) or not isinstance(decision.get('actions',[]),list) or any(not isinstance(a,dict) for a in decision.get('actions',[])):
                raise ProtocolError('INVALID_ACTION_SCHEMA')
            equal=(typed.get('kind')=='category_set' and set(typed.get('labels',[]))=={a.get('code') for a in decision.get('actions',[])}) if ident=='actions' else (typed=={'kind':'category','label':decision.get('disposition')})
            if not equal or answer.get('determination','supported')!='supported' or set(answer.get('evidence_ids',[]))!=set(decision.get('evidence_ids',[])):
                raise ProtocolError('CONFLICTING_LEGACY_CARE_DUPLICATE')
            report['answers'].remove(answer);applied.append('identical_legacy_care_duplicate')
    # Preserve well-formed but unrequested results as diagnostics. Never rename
    # them into a required slot or fill a missing result. The scorer therefore
    # still fails a wrong-slot-only report, while irrelevant extras cannot block
    # a checkpoint containing the requested result.
    requested={s.slot_id for s in task.assessment_slots if s.output_location=='answers'}
    for answer in list(report.get('answers',[])):
        if isinstance(answer,dict) and answer.get('slot_id') not in requested:
            Answer.model_validate(answer,strict=True)
            diagnostics.append(copy.deepcopy(answer));report['answers'].remove(answer)
            applied.append('unrequested_answers_retained_as_diagnostics')
    return value


def parse_action(raw: str | bytes, *, task=None, with_audit=False):
    audit={'raw_json_valid':False,'raw_protocol_valid':False,'normalized_protocol_valid':False,
        'recognized_action':None,'normalizations':[],'unrequested_answers':[]}
    try:
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        if not isinstance(data, bytes) or len(data) > 65536:
            raise ProtocolError("INVALID_OUTPUT_SIZE")
        decoded=data.decode('utf-8');text = decoded.strip()
        if decoded!=text:audit['normalizations'].append('surrounding_whitespace')
        # The entire response must be one fenced object, with no surrounding prose.
        fence=re.fullmatch(r'```(?:json)?\s*\n([\s\S]*?)\n```',text)
        if fence and task is not None and task.protocol_version=='plantenv-2.0.0':
            text=fence.group(1).strip();audit['normalizations'].append('outer_json_fence')
        depth, quoted, escaped = 0, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > 32:
                    raise ProtocolError("JSON_TOO_DEEP")
            elif char in "]}":
                depth -= 1
        value = json.loads(text, object_pairs_hook=unique_keys, parse_constant=reject_constant)
        audit['raw_json_valid']='outer_json_fence' not in audit['normalizations']
        if not isinstance(value, dict):
            raise ProtocolError("JSON_OBJECT_REQUIRED")
        if value.get('action') in {'INSPECT','QUERY','ASK','SEARCH_SOURCE','ASSESS','ADVANCE_RECORD','SUBMIT','STOP'}:
            audit['recognized_action']=value['action']
        try:
            TypeAdapter(AgentAction).validate_python(value,strict=True)
            _public_check(value,task,canonical=True)
            audit['raw_protocol_valid']=audit['raw_json_valid']
        except (ProtocolError,ValidationError):pass
        value=_normalize_report(value,task,audit['normalizations'],audit['unrequested_answers'])
        _public_check(value,task,canonical=True)
        action=TypeAdapter(AgentAction).validate_python(value, strict=True)
        audit['normalized_protocol_valid']=True
        audit['normalizations']=sorted(set(audit['normalizations']))
        return (action,audit) if with_audit else action
    except ProtocolError as exc:
        exc.protocol_audit=audit
        raise
    except json.JSONDecodeError as exc:
        error=ProtocolError('INVALID_ACTION_SCHEMA')
        error.protocol_audit=audit
        error.public_details=[{'field':f'JSON line {exc.lineno} column {exc.colno}',
            'error_type':'invalid_json','message':exc.msg}]
        raise error from None
    except ValidationError as exc:
        error=ProtocolError('INVALID_ACTION_SCHEMA')
        error.protocol_audit=audit
        error.public_details=[{'field':'.'.join(str(part) for part in item['loc']),
            'error_type':item['type'],'message':item['msg']} for item in exc.errors(include_input=False,include_url=False)[:6]]
        raise error from None
    except (ValueError, UnicodeError, json.JSONDecodeError, ValidationError, RecursionError, TypeError, AttributeError):
        error=ProtocolError('INVALID_ACTION_SCHEMA');error.protocol_audit=audit
        raise error from None
