"""P4-only public interface revision. No reference answers enter this module."""
import base64
import json
from .contracts import logical_hash

PROTOCOL = 'care-report-2.1.0'
SCORER = 'agent-score-care-2.1.0'
POLICY = {'protocol': PROTOCOL, 'scorer': SCORER,
          'gold_changed': False, 'non_p4_changed': False,
          'formal_p4_requires_fresh_model_outputs': True}

def enabled(task):
    return task.report_protocol == PROTOCOL

def incomplete_submission(event,task):
    """Score typed partial content; do not execute, fill or repair an action.

    Only the explicitly identified missing-decision error is eligible. JSON,
    ordinary slot types and public constraints must independently parse.
    Evidence visibility is supplied by the recorded event, never later state.
    """
    if not str(event.get('public_error','')).startswith('CARE_DECISION_REQUIRED'):
        return None
    from .protocol import parse_action,ProtocolError
    partial_task=task.model_copy(update={'report_protocol':'task-slots-evidence-ids-v2'})
    try:
        action=parse_action(base64.b64decode(event['action_raw_base64'],validate=True),task=partial_task)
    except (ProtocolError,ValueError):return None
    if action.action not in {'SUBMIT','STOP'} or action.report is None:return None
    return action.report.model_dump(mode='json')

def revise_episode(episode):
    if episode.agentic is None or episode.agentic.primary_p != 'P4':
        return episode
    if episode.task.protocol_version != 'plantenv-2.0.0':
        raise ValueError('Care revision requires the frozen v2 task contract')
    result = episode.model_copy(deep=True)
    result.task.report_protocol = PROTOCOL
    result.content_hash = ''
    result.content_hash = logical_hash(result)
    return result

def prompt(task, base):
    if not enabled(task):
        return base
    text = base.replace('SUBMIT contains report.answers and optional decision.',
                        'SUBMIT follows the task-specific care report contract below.')
    text = text.replace('SUBMIT: {"action":"SUBMIT","report":{"answers":[<your answer objects>],"limitations":[]}}', '')
    locations = {s.output_location for s in task.assessment_slots}
    text += '\nCare report protocol '+PROTOCOL+':\n'
    if task.require_decision:
        text += 'SUBMIT.report.decision is REQUIRED and cannot be null. Choose its disposition yourself.\n'
    text += ('Put only ordinary slots whose output_location is answers in report.answers. '
             'Never put disposition, decision_actions or decision_conditions in answers.\n')
    if 'decision.actions' in locations:
        text += ('Put proposed action codes in decision.actions; put applicable condition codes '
                 'on each action. The public action and condition domains are vocabularies, '
                 'not recommendations. You must select the measures and conditions.\n')
    else:
        text += 'This task requests a disposition only; action and condition lists are not required.\n'
    text += ('Cite only received evidence. Use decision.evidence_ids for the decision and '
             'each ordinary answer\'s evidence_ids for that answer.\n'
             'Care SUBMIT syntax (all placeholders must be replaced by your own choices):\n')
    example={'action':'SUBMIT','report':{'answers':[],
        'decision':{'disposition':'<your disposition>','evidence_ids':['<received evidence ID>']}}}
    for slot in task.assessment_slots:
        if slot.output_location!='answers':continue
        values={'category':{'kind':'category','label':'<your condition label>'},
                'category_set':{'kind':'category_set','labels':['<your labels>']}}
        if slot.value_kind not in values:raise ValueError('Unregistered P4 ordinary answer kind')
        example['report']['answers'].append({'slot_id':slot.slot_id,'value':values[slot.value_kind],
            'evidence_ids':['<received evidence ID>']})
    if 'decision.actions' in locations:
        example['report']['decision']['actions']=[{'code':'<your action code>','conditions':['<applicable condition code>']}]
    return text + json.dumps(example,ensure_ascii=False) + '\n'

def constrain_schema(task, definitions):
    if not enabled(task):
        return
    report = definitions['FinalReport']
    if task.require_decision:
        report['properties']['decision'] = {'$ref': '#/$defs/Decision'}
        report['required'] = list(dict.fromkeys([*report.get('required', []), 'decision']))
    locations = {s.output_location for s in task.assessment_slots}
    if 'decision.actions' in locations:
        decision = definitions['Decision']
        decision['required'] = list(dict.fromkeys([*decision.get('required', []), 'actions']))
    # Only public vocabulary is constrained, never the reference subset.
    if task.decision_action_domain:
        definitions['DecisionAction']['properties']['code']['enum'] = list(task.decision_action_domain)
    if task.decision_condition_domain:
        definitions['DecisionAction']['properties']['conditions']['items'] = {
            'type': 'string', 'enum': list(task.decision_condition_domain)}

def check_report(value, task):
    """Same requirements for raw and structured output; no semantic repair."""
    if not enabled(task) or value.get('action') not in {'SUBMIT', 'STOP'}:
        return
    report = value.get('report')
    if report is None:  # STOP without a report remains a legitimate abandonment.
        return
    from .protocol import ProtocolError
    decision = report.get('decision') if isinstance(report, dict) else None
    if task.require_decision and not isinstance(decision, dict):
        raise ProtocolError('CARE_DECISION_REQUIRED: put disposition and care fields in report.decision')
    if not isinstance(decision, dict):
        return
    if any(s.output_location == 'decision.actions' for s in task.assessment_slots) and 'actions' not in decision:
        raise ProtocolError('CARE_ACTIONS_FIELD_REQUIRED: use decision.actions; choose the actions yourself')
    for item in decision.get('actions', []) if isinstance(decision.get('actions', []), list) else []:
        if not isinstance(item, dict):
            continue  # The typed parser reports malformed action containers.
        if task.decision_action_domain and item.get('code') not in task.decision_action_domain:
            raise ProtocolError('CARE_ACTION_NOT_IN_PUBLIC_DOMAIN')
        if task.decision_condition_domain and isinstance(item.get('conditions', []), list):
            if any(c not in task.decision_condition_domain for c in item.get('conditions', [])):
                raise ProtocolError('CARE_CONDITION_NOT_IN_PUBLIC_DOMAIN')
