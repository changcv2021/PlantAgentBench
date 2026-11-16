"""Versioned evaluation compilation from source Gold, never from model scores.

This is the upgrade used by builders and the offline catalog migration alike.
Unknown outcomes fail closed: no guessed milestone or invented support route.
"""
import copy
from plantbench.contracts import EpisodeSpec, logical_hash

VERSION = 'agent-gold-2.0.0'
NORMALIZATION = ['outer_json_fence', 'surrounding_whitespace',
    'ordinary_evidence_links_to_ids', 'explicit_undetermined_category',
    'identical_legacy_care_duplicate','unrequested_answers_retained_as_diagnostics']
LABELS = {
    'P1': ['识别生长状态或异常变化', '核查相关表型与测量', '完成巡检和异常分流成果'],
    'P2': ['定位环境事件或变化', '核查环境及管理记录', '完成有依据的调查成果'],
    'P3': ['识别症状或目标状态', '获取并利用诊断相关证据', '完成诊断或合理不确定判断'],
    'P4': ['识别需处理的问题', '形成来源支持的护理或处置措施', '完成具有必要适用条件的方案'],
    'P5': ['完成前阶段评估', '理解真实新增记录', '正确维持、更新或修订判断'],
}
SLOTS = {
    'P1': [['recorded_decline','attention_present','mixed_directions','yield_status'],
           ['attention_variables','attention_heads','attention_records','direction_witnesses','zero_yield_records'], ['specific_cause']],
    'P2': [['event_present','constraint_violation','investigation_outcome','calendar_status'],
           ['first_record','violating_channels','activity_witnesses'], ['causal_diagnosis','plan_execution_verified']],
    'P3': [['measured_direction','crop'], ['nitrogen_assignment','water_assignment'],
           ['reference_entity','source_group','diagnosis_determination']],
    'P4': [['source_condition'], ['decision_actions'], ['disposition','decision_conditions']],
    'P5': [[], ['attention_variables','first_record','attention_heads','attention_records'],
           ['recorded_decline','event_present','next_disposition','attention_present']],
}

def milestone_id(p, rule):
    if rule.get('semantic_milestone') in {'M1','M2','M3'}:
        return rule['semantic_milestone']
    if p == 'P5' and rule.get('evaluate_at','final') != 'final':
        return 'M1'
    for index, slots in enumerate(SLOTS[p], 1):
        if rule['slot_id'] in slots:
            return 'M'+str(index)
    raise ValueError('Unmapped semantic outcome: '+p+'/'+rule['slot_id'])

def compile_evaluation(episode, gold):
    """Keep scientific predicates/routes; remove duplicate P4 encoding only."""
    e=copy.deepcopy(episode.model_dump(mode='json') if isinstance(episode,EpisodeSpec) else episode)
    g=copy.deepcopy(gold)
    if g.get('schema_version') == VERSION:
        # Recompile public requirements/milestones after source normalization.
        prior=g.get('migration',{})
    else:
        prior={'source_episode_hash':logical_hash(e),'source_gold_hash':logical_hash(g)}
    p=g['primary_p']; rules=g['slots']; removed=[]
    if p=='P4':
        for duplicate, location in [('actions','decision.actions'), ('next_disposition','decision.disposition')]:
            primary=next((r for r in rules if r.get('location')==location),None)
            copies=[r for r in rules if r['slot_id']==duplicate and not r.get('location')]
            for old in copies:
                if primary is None or any(old.get(k)!=primary.get(k) for k in
                    ['kind','expected','match','groups','alternatives','explicit_contradictions','support_routes','citation_policy']):
                    raise ValueError('Nonidentical care outcomes cannot be deduplicated')
                rules.remove(old); removed.append(old['id'])
        # Conditions have a separate scientific predicate, not a second action match.
        primary=next((r for r in rules if r.get('location')=='decision.actions'),None)
        if primary and primary.get('conditions_by_action'):
            existing=next((r for r in rules if r.get('location')=='decision.conditions'),None)
            condition=copy.deepcopy(primary)
            condition.update(id='decision_conditions_final',slot_id='decision_conditions',
                location='decision.conditions',checker='conditions_by_action')
            # Action correctness and evidence support are checked once in M2.
            condition.update(citation_policy='not_required',citation_required=False,support_routes=[])
            if existing: rules.remove(existing)
            rules.append(condition)
        e['task']['goal_text']=e['task']['goal_text'].replace(
            'Report proposed action codes in the actions answer and in decision.actions;',
            'Report proposed action codes only in decision.actions;').replace(
            'submit the same management option codes in actions and decision.actions.',
            'submit management option codes only in decision.actions.').replace(
            'report next_disposition and decision.disposition', 'report only decision.disposition')
    public={s['slot_id']:s for s in e['task']['assessment_slots']}
    slots=[]
    for rule in rules:
        ident=rule['slot_id']; location=rule.get('location','answers')
        slot=next((s for s in slots if s['slot_id']==ident),None)
        if slot is None:
            slot=copy.deepcopy(public.get(ident) or {'slot_id':ident,'value_kind':rule['kind'],
                'question': {'decision.actions':'Proposed source-supported actions; include applicable public condition codes.',
                    'decision.disposition':'Final disposition.', 'decision.conditions':'Necessary conditions on each proposed action.'}.get(location,'Requested outcome.')})
            slot.update(output_location=location,citation_requirements=[])
            slots.append(slot)
        policy=rule.get('citation_policy','visible_only')
        slot['citation_requirements'].append({'evaluate_at':rule.get('evaluate_at','final'),
            'policy':policy,'required':rule.get('citation_required',True) if policy!='not_required' else False,
            'kinds':rule.get('citation_kinds',[]),'scopes':rule.get('citation_scopes',[]),
            'require_relations':rule.get('require_relations',False),
            'explanation':{'not_required':'No citation is required for this stated scope or condition outcome; any supplied IDs must be visible.',
                'visible_only':'Cite received evidence in the requested scope. This task has no adjudicated semantic support routes.',
                'verified_support':'Cite received evidence sufficient to support the outcome, including the complete relevant range for an absence claim; registered alternative evidence routes are accepted.'}[policy]})
    e['task'].update(protocol_version='plantenv-2.0.0',report_protocol='task-slots-evidence-ids-v2',
        assessment_slots=slots,normalization_policy=NORMALIZATION)
    e['benchmark_version']='bench-v0.4.0-evaluation01'; e['builder_version']='0.4.0'
    g['milestones']=[]
    for index,label in enumerate(LABELS[p],1):
        mid='M'+str(index)
        owned=[r for r in rules if milestone_id(p,r)==mid]
        required=[r['id'] for r in owned if r.get('mandatory',True)]
        diagnostic=[r['id'] for r in owned if not r.get('mandatory',True)]
        g['milestones'].append({'id':mid,'label':label,'rule_ids':required,
            'diagnostic_rule_ids':diagnostic,'required':bool(required),
            'na_reason':None if required else ('diagnostic_only' if diagnostic else 'no_independent_source_annotation')})
    if not any(m['required'] for m in g['milestones']):raise ValueError('No necessary outcomes')
    if not any(r.get('mandatory',True) and r.get('evaluate_at','final')=='final' for r in rules):
        raise ValueError('At least one necessary terminal outcome is required')
    g.update(schema_version=VERSION,checker_version='semantic-outcomes-2.0.0',
        migration={**prior,'compiler':VERSION,'removed_duplicate_rule_ids':sorted(set(prior.get('removed_duplicate_rule_ids',[])+removed))})
    g['metric_eligibility']={'task_success':True,'milestone_progress':True,'format_validity':True,
        'content_correctness':True,'evidence_grounding':any(r.get('citation_policy')=='verified_support' for r in rules),
        'valid_tool_use':'at_least_one_recognizable_acquisition_action',
        'evidence_acquisition':bool(g.get('evidence_goals')),'query_cost':True}
    e['content_hash']=''; ep=EpisodeSpec.model_validate(e); ep.content_hash=logical_hash(ep)
    return ep,g
