"""Private reference trajectories prove executability, not model performance."""
import json
from plantbench.contracts import EpisodeSpec
from plantbench.environment import PlantEnv,ResetOptions
from plantbench.trace import _FrozenMediaIdentity
from plantbench.agent_scoring import score_agent_trace

def typed_value(rule):
    if rule.get('determination')=='undetermined':return None
    kind=rule['kind'];expected=rule['expected']
    if kind=='category_set':
        if rule.get('match') in {'groups','spanning_sets'}:expected=sorted({group[0] for group in rule['groups']})
        if rule.get('match')=='nonempty_subset':expected=expected[:1]
        return {'kind':kind,'labels':expected}
    if kind in {'category','comparison'}:return {'kind':kind,'label':expected}
    if kind=='number':return {'kind':kind,'value':expected,'unit':rule['unit']}
    return {'kind':kind,'value':expected}

def reference_trajectory(episode,gold,*,resolver=None,verify=True,negative=False,reverse=False,mutate_early=False):
    if isinstance(episode,dict):episode=EpisodeSpec.model_validate(episode)
    env=PlantEnv(episode,resolver or _FrozenMediaIdentity(),result_origin='scripted_validation')
    env.reset(seed=0,options=ResetOptions(episode_handle=episode.task.neutral_episode_handle,variant=episode.identity.variant_id))
    units={u.evidence_uid:u for u in episode.evidence};actions=[]
    if episode.knowledge_corpus:
        from plantbench.knowledge import KnowledgeResolver
        knowledge=KnowledgeResolver(episode.knowledge_corpus)
        units.update({u.evidence_uid:u for u in knowledge.all_units()})
    def step(action):
        actions.append(action);response=env.step(json.dumps(action),request_id='oracle_'+str(len(actions)))
        if response[0].info.error_code:raise ValueError('Oracle action failed: '+response[0].info.error_code)
    for phase in range(len(episode.phases)):
        stage='final' if phase==len(episode.phases)-1 else 'phase_'+str(phase)
        rules=[r for r in gold['slots'] if r.get('evaluate_at','final')==stage]
        for query in gold.get('oracle_search_queries',[]):
            if phase==0:step({'action':'SEARCH_SOURCE','query':query,'top_k':3})
        required=set(gold.get('oracle_acquire',[]))
        required={uid for uid in required if units[uid].release_phase<=phase}
        for rule in rules:
            if rule.get('checker')=='conditions_by_action':continue
            if rule.get('support_routes'):
                routes=rule['support_routes'];visible=set(env._state()['revealed'])
                required.update(min(routes,key=lambda r:(len(set(r)-visible),tuple(r))))
            answer=typed_value(rule)
            if rule.get('support_by_label') and answer:
                for label in answer.get('labels',[]):required.update(rule['support_by_label'].get(label,[]))
        targets=[target for target in episode.catalog if set(target.evidence_refs)&required and target.release_phase<=phase]
        if reverse:targets.reverse()
        for target in targets:
            if not set(target.evidence_refs)<=set(env._state()['revealed']):step({'action':target.action_type,'target_id':target.target_id})
        visible=env._state()['revealed'];citations=[{'evidence_id':units[uid].public_evidence_id,'relation':'support'} for uid in visible]
        answers=[];decision=None
        for rule in rules:
            value=typed_value(rule)
            if negative and stage=='final':
                if value and value['kind']=='boolean':value['value']=not value['value']
                elif value and value['kind']=='category_set':value['labels']=[] if value['labels'] else ['wrong']
                elif value and value['kind'] in {'category','comparison'}:value['label']='wrong' if value['kind']=='category' else 'unchanged' if value['label']!='unchanged' else 'increased'
                elif value and value['kind']=='number':value['value']+=10000
                else:value={'kind':rule['kind'],'label':'wrong'} if rule['kind']=='category' else {'kind':'boolean','value':False}
            determination=rule.get('determination','supported')
            if negative and value is not None:determination='supported'
            if mutate_early and stage!='final':value=None;determination='undetermined'
            location=rule.get('location')
            if location:
                if decision is None:decision={'disposition':'report_only','actions':[],'evidence_ids':[c['evidence_id'] for c in citations]}
                if location=='decision.disposition':decision['disposition']=('report_only' if rule['expected']!='report_only' else 'defer') if negative else value['label']
                elif location=='decision.actions':decision['actions']=[] if negative else [{'code':code,'conditions':rule.get('conditions_by_action',{}).get(code,[])} for code in value['labels']]
            else:
                citation_field={'evidence_links':citations} if rule.get('require_relations') else {'evidence_ids':[c['evidence_id'] for c in citations]}
                answers.append({'slot_id':rule['slot_id'],'determination':determination,'value':value,**citation_field})
        report={'answers':answers,'limitations':[]}
        if stage=='final':
            if decision is not None:report['decision']=decision
            step({'action':'SUBMIT','report':report})
        else:
            step({'action':'ASSESS','assessment':report});step({'action':'ADVANCE_RECORD'})
    trace=env._private_trace();score=score_agent_trace(episode,gold,trace,verify=verify);env.close()
    return actions,trace,score
