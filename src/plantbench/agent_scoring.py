"""Version 2 plant outcomes and observable agent metrics; never model feedback."""
import json
import math
import re
from .contracts import logical_hash
from .trace import verify_trace

def normalize(value):
    return ' '.join(re.findall(r'[^\W_]+',str(value).casefold()))

def match_value(actual,rule):
    expected=rule.get('expected')
    if rule.get('determination','supported')=='undetermined':return actual is None
    if not isinstance(actual,dict) or actual.get('kind')!=rule['kind']:return False
    kind=rule['kind']
    if kind=='number':
        value=actual.get('value')
        return (type(value) in {int,float} and math.isfinite(value) and actual.get('unit')==rule.get('unit')
            and math.isclose(value,expected,rel_tol=rule.get('relative_tolerance',0),abs_tol=rule.get('absolute_tolerance',1e-6)))
    if kind=='boolean':return type(actual.get('value')) is bool and actual['value']==expected
    if kind in {'category','comparison'}:
        return normalize(actual.get('label','')) in {normalize(v) for v in rule.get('alternatives',[expected])}
    if kind=='category_set':
        labels=actual.get('labels')
        if not isinstance(labels,list) or any(not isinstance(label,str) for label in labels):return False
        found={normalize(v) for v in labels};wanted={normalize(v) for v in expected}
        if found & {normalize(v) for v in rule.get('explicit_contradictions',[])}:return False
        mode=rule.get('match','exact')
        if mode=='groups':return all(found & {normalize(v) for v in group} for group in rule['groups'])
        if mode=='nonempty_subset':return bool(found) and found<=wanted
        if mode=='spanning_sets':return found<=wanted and all(found & {normalize(v) for v in group} for group in rule['groups'])
        return wanted<=found if mode=='contains' else wanted==found
    raise ValueError('Unimplemented score kind: '+kind)

def metric(numerator,denominator,reason):
    return {'value':numerator/denominator if denominator else None,'numerator':numerator,
        'denominator':denominator,'eligible':bool(denominator),'na_reason':None if denominator else reason}

def _answer(report,rule):
    if report is None:return None
    location=rule.get('location')
    if location:
        decision=report.get('decision')
        if decision is None:return None
        value=({'kind':'category','label':decision['disposition']} if location=='decision.disposition'
            else {'kind':'category_set','labels':[a['code'] for a in decision['actions']]})
        return {'determination':'supported','value':value,'evidence_ids':decision['evidence_ids'],'evidence_links':[]}
    answers=[a for a in report.get('answers',[]) if a['slot_id']==rule['slot_id']]
    return answers[0] if len(answers)==1 else None

def score_agent_trace(episode,gold,trace,*,verify=True,care_policy=None):
    from .care_protocol import enabled,SCORER
    if care_policy not in {None,SCORER}:raise ValueError('Unregistered care scoring policy')
    revised_care=enabled(episode.task) or care_policy==SCORER
    if revised_care and (episode.agentic is None or episode.agentic.primary_p!='P4'):
        raise ValueError('Care scoring revision applies only to P4')
    if gold.get('schema_version')!='agent-gold-2.0.0' or episode.task.protocol_version!='plantenv-2.0.0':
        raise ValueError('Explicit v2 catalog migration required; score old results with the frozen v1 checkout')
    if episode.agentic is None or gold['episode_id']!=episode.episode_id:
        raise ValueError('Agent task/gold identity mismatch')
    if gold['primary_p']!=episode.agentic.primary_p or gold['answer_basis']!=episode.agentic.answer_basis:
        raise ValueError('Agent task/gold semantics mismatch')
    if verify:verify_trace(episode,trace)
    units={u.evidence_uid:u for u in episode.evidence}
    if episode.knowledge_corpus:
        from .knowledge import KnowledgeResolver
        units.update({u.evidence_uid:u for u in KnowledgeResolver(episode.knowledge_corpus).all_units()})
    if not set(trace['state']['revealed'])<=units.keys():raise ValueError('Unresolvable trace evidence')
    uid_for_id={u.public_evidence_id:uid for uid,u in units.items()}
    state=trace['state'];final=state['final']
    valid_final=bool(final is not None and state['terminated'] and state['reason']=='submitted')
    final_stage=valid_final and state['phase']==len(episode.phases)-1 and (not episode.task.require_decision or bool(final.get('decision')))
    snapshots={'final':(final,set(state['revealed']))}
    care_stage=valid_final and state['phase']==len(episode.phases)-1
    care_report_source='final' if final is not None else None
    for checkpoint in state['checkpoints']:
        snapshots['phase_'+str(checkpoint['phase'])]=(checkpoint['assessment'],set(checkpoint['visible_uids']))
    if revised_care and final is None:
        from .care_protocol import incomplete_submission
        for event in trace['events']:
            if event['phase_before']!=len(episode.phases)-1:continue
            action=event.get('parsed_action') or {}
            report=action.get('assessment') if action.get('action')=='ASSESS' and event['public_error'] is None else None
            source='final_phase_assessment'
            partial=incomplete_submission(event,episode.task)
            if partial is not None:report=partial;source='incomplete_submission_content_only'
            if report is not None:
                snapshots['final']=(report,set(event['visible_uids_after']))
                care_stage=True;care_report_source=source
    results=[]
    for rule in gold['slots']:
        stage=rule.get('evaluate_at','final');report,visible=snapshots.get(stage,(None,set()))
        answer=_answer(report,rule)
        content=bool(answer and answer.get('determination','supported')==rule.get('determination','supported') and match_value(answer['value'],rule))
        if rule.get('checker')=='conditions_by_action':
            actions=((report or {}).get('decision') or {}).get('actions',[])
            content=bool(actions) and all(set(rule['conditions_by_action'].get(a['code'],[]))<=set(a.get('conditions',[]))
                and (a.get('target') in rule['targets_by_action'][a['code']] if a['code'] in rule.get('targets_by_action',{}) else True)
                for a in actions)
        links=[] if answer is None else answer.get('evidence_links',[])
        cited=set() if answer is None else set(answer.get('evidence_ids',[]))|{x['evidence_id'] for x in links}
        exists=cited<=uid_for_id.keys();cited_uids={uid_for_id[i] for i in cited if i in uid_for_id}
        visible_ok=exists and cited_uids<=visible
        policy=rule.get('citation_policy','visible_only');required=rule.get('citation_required',True)
        citation=visible_ok and (bool(cited) or not required)
        kinds_ok=set(rule.get('citation_kinds',[]))<={units[u].payload.kind for u in cited_uids}
        scopes_ok=set(rule.get('citation_scopes',[]))<={units[u].subject_scope for u in cited_uids}
        citation=citation and kinds_ok and scopes_ok;support=None;route_covered=None
        evidence_requirement=citation
        if policy=='verified_support':
            routes=rule.get('support_routes',[])
            if not routes or any(not route or not set(route)<=units.keys() for route in routes):raise ValueError('Unresolvable/empty verified support route')
            supportive=({uid_for_id[x['evidence_id']] for x in links if x['relation']=='support' and x['evidence_id'] in uid_for_id}
                if rule.get('require_relations') else cited_uids)
            support=any(set(route)<=supportive for route in routes)
            if support and rule.get('support_by_label') and answer:
                labels=(answer['value'] or {}).get('labels',[]);by_label={normalize(k):v for k,v in rule['support_by_label'].items()}
                support=all(normalize(label) in by_label and set(by_label[normalize(label)])<=supportive for label in labels)
            route_covered=support
            evidence_requirement=bool(citation and route_covered)
            # Frozen routes support the annotated outcome, not an arbitrary
            # opposite assertion made while citing the same complete records.
            support=bool(support and content)
            citation=citation and support
        elif policy=='not_required':citation=visible_ok;evidence_requirement=visible_ok
        elif policy!='visible_only':raise ValueError('Unknown citation policy')
        stage_pass=bool(care_stage if revised_care else final_stage) if stage=='final' else report is not None
        passed=content and citation and stage_pass
        results.append({'id':rule['id'],'slot_id':rule['slot_id'],'evaluate_at':stage,'passed':passed,
            'content_pass':content,'evidence_pass':citation,'stage_pass':stage_pass,
            'answer_present':answer is not None,'answer_match':content,'citation_validity':visible_ok,
            'support_verified':support,'grounding_eligible':policy=='verified_support',
            'grounding_na_reason':None if policy=='verified_support' else policy+':no_semantic_support_annotation',
            'citation_components':{'exists':exists,'visible':visible_ok,'kinds':kinds_ok,'scopes':scopes_ok,'support_route_covered':route_covered,'support':support},
            'mandatory':rule.get('mandatory',True),'source_refs':rule['source_refs'],
            'failure_reasons':([] if content else ['missing_or_duplicate_answer' if answer is None else 'content_mismatch'])
                +([] if citation else ['evidence_requirement'])+([] if stage_pass else ['stage_or_submission_requirement'])})
        if revised_care:results[-1]['evidence_requirement_pass']=evidence_requirement
    by_id={r['id']:r for r in results};milestones=[];owned=[]
    if len(by_id)!=len(results):raise ValueError('Repeated rule ID')
    def outcome(ids):
        if not ids:return {k:None for k in ['content_pass','evidence_pass','stage_pass','milestone_pass']}
        if not set(ids)<=by_id.keys():raise ValueError('Unknown milestone outcome')
        return {k:all(by_id[i]['passed' if k=='milestone_pass' else k] for i in ids)
            for k in ['content_pass','evidence_pass','stage_pass','milestone_pass']}
    for m in gold['milestones']:
        ids=m['rule_ids'];diagnostic=m.get('diagnostic_rule_ids',[]);owned+=ids+diagnostic;checked=outcome(ids)
        milestones.append({**m,**checked,'passed':checked['milestone_pass'],'weight':1.0,'diagnostic':outcome(diagnostic)})
        if revised_care:
            milestones[-1]['evidence_requirement_pass']=all(by_id[i]['evidence_requirement_pass'] for i in ids) if ids else None
    if len(owned)!=len(set(owned)) or set(owned)!=set(by_id):raise ValueError('Milestone ownership must be unique and exhaustive')
    mandatory=[r for r in results if r['mandatory']];required=[m for m in milestones if m['required']]
    if not required or {r['id'] for r in mandatory}!={i for m in required for i in m['rule_ids']}:
        raise ValueError('Necessary milestones must exactly partition necessary outcomes')
    success=all(m['milestone_pass'] for m in required) and (bool(final_stage) if revised_care else True)
    if success!=bool(final_stage and all(r['passed'] for r in mandatory)):raise ValueError('Success/progress invariant failed')
    events=trace['events'];tools={'INSPECT','QUERY','ASK','SEARCH_SOURCE'}
    tool_events=[e for e in events if e['protocol_audit'].get('recognized_action') in tools]
    successful=[e for e in tool_events if e['parse_status']=='parsed' and e['public_error'] is None]
    keys=[json.dumps(e['parsed_action'],sort_keys=True) for e in successful]
    goals=gold.get('evidence_goals') or []
    if any(not goal['routes'] or any(not route or not set(route)<=units.keys() for route in goal['routes']) for goal in goals):
        raise ValueError('Unresolvable evidence acquisition goal')
    def acquisition(visible):return sum(any(set(route)<=visible for route in g['routes']) for g in goals)
    initial=set(episode.phases[0].initial_evidence_refs)
    if episode.identity.variant_id in {'full_current_evidence','revision_final_fresh'}:initial={uid for uid,u in units.items() if u.release_phase==0}
    grounded=[r for r in mandatory if r['grounding_eligible']];content_scorable=[r for r in mandatory if r['answer_present']]
    cost=sum(e['tool_cost'] for e in events);penalty=sum(e['invalid_action_penalty'] for e in events)
    metrics={
        'format_raw':metric(sum(e['protocol_audit']['raw_protocol_valid'] for e in events),len(events),'no_model_actions'),
        'format_normalized':metric(sum(e['protocol_audit']['normalized_protocol_valid'] for e in events),len(events),'no_model_actions'),
        'content_correctness':metric(sum(r['content_pass'] for r in content_scorable),len(content_scorable),'no_scorable_submitted_content'),
        'evidence_grounding':metric(sum(r['evidence_pass'] for r in grounded),len(grounded),'no_verified_support_annotation'),
        'valid_tool_use':metric(len(successful),len(tool_events),'no_recognizable_acquisition_action'),
        'evidence_acquisition':metric(acquisition(set(state['revealed'])),len(goals),'no_frozen_evidence_goals'),
        'query_cost':metric(cost,1,'')}
    before=[r for r in results if r['evaluate_at']!='final'];after=[r for r in results if r['evaluate_at']=='final']
    coverage=metrics['evidence_acquisition']['value'];stop=gold.get('stopping_class','not_adjudicated')
    if revised_care:
        metrics.update(content_all_required=metric(sum(r['content_pass'] for r in mandatory),len(mandatory),''),
            evidence_requirements=metric(sum(r['evidence_requirement_pass'] for r in grounded),len(grounded),'no_verified_support_annotation'),
            report_completeness=metric(sum(r['answer_present'] for r in mandatory) if valid_final else 0,len(mandatory),''),
            milestone_content_progress=metric(sum(m['content_pass'] for m in required),len(required),''),
            milestone_evidence_progress=metric(sum(m['evidence_requirement_pass'] for m in required),len(required),''))
    result={'schema_version':'agent-score-2.0.0','scorer_version':SCORER if revised_care else 'agent-score-2.0.0','episode_id':episode.episode_id,
        'base_task_id':episode.identity.base_case_id,'case_group_id':episode.identity.inference_group_id,
        'leakage_group_id':episode.identity.leakage_group_id,'dataset_id':episode.source_scope.dataset_id,
        'primary_p':episode.agentic.primary_p,'template_id':episode.agentic.template_id,'answer_basis':episode.agentic.answer_basis,
        'result_origin':trace['result_origin'],'human_review_status':episode.agentic.human_review_status,
        'success_name':'ReferenceTaskSuccess' if episode.agentic.answer_basis=='source_reference' else 'RecordTaskSuccess',
        'task_success':int(success),'valid_final':valid_final,'slot_results':results,'milestone_results':milestones,
        'milestone_progress':sum(m['milestone_pass'] for m in required)/len(required),
        'metrics':metrics,'content_coverage':metric(len(content_scorable),len(mandatory),'no_mandatory_rules'),
        'content_all_required':metric(sum(r['content_pass'] for r in mandatory),len(mandatory),'no_mandatory_rules'),
        'behavior':{'action_validity':metrics['format_normalized']['value'],'action_attempts':len(events),
            'tool_validity':metrics['valid_tool_use']['value'],'tool_attempts':len(tool_events),
            'evidence_acquisition':coverage,'initial_evidence_coverage':acquisition(initial)/len(goals) if goals else None,
            'evidence_coverage_status':gold.get('evidence_coverage_status','unavailable'),
            'queries':len(successful),'query_cost':cost,'invalid_action_penalty':penalty,'budget_total_cost':state['cost'],
            'duplicate_queries':len(keys)-len(set(keys)),'queries_on_success':len(successful) if success else None},
        'diagnostics':{'format_failures':sum(e['parse_status']=='invalid' for e in events),
            'execution_failures':sum(e['parse_status']=='parsed' and e['public_error'] is not None for e in events),
            'action_limit':state['reason']=='action_limit','query_budget_exhausted':state['cost']>=episode.budget.query_cost_budget,
            'invalid_action_penalty':penalty,'model_action_count':len(events),
            'normalized_action_count':sum(bool(e['protocol_audit']['normalizations']) for e in events)},
        'specialized':{'stopping_class':stop,
            'premature_submit':bool(valid_final and (not final_stage or coverage<1)) if stop=='resolvable_with_queries' and coverage is not None else None,
            'appropriate_deferral':bool(success) if stop=='unresolvable_in_record' else None,
            'revision':{'pair_success':bool(before and after and all(r['passed'] for r in before+after)),
                'final_success':bool(after and all(r['passed'] for r in after))} if episode.agentic.subtype=='P5b' else None,
            'risk':None,'sampling_reliability':None},
        'metric_eligibility_with_reasons':gold['metric_eligibility'],'trace_hash':logical_hash(trace),'gold_hash':logical_hash(gold)}
    if revised_care:
        result['care_revision']={'report_source':care_report_source,'protocol':episode.task.report_protocol,
            'valid_terminal_submission':bool(valid_final and state['phase']==len(episode.phases)-1),
            'required_decision_present':not episode.task.require_decision or bool((final or {}).get('decision')),
            'report_complete':bool(final_stage and all(r['answer_present'] for r in mandatory)),
            'gold_changed':False,'strict_task_success_preserved':True}
        if care_policy is not None and not enabled(episode.task):
            result['source_result_origin']=result['result_origin'];result['result_origin']='offline_rescore'
    return result
