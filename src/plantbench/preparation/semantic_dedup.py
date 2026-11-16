"""Exact observable-task and reference equivalence for image-only QA cases.

Only PlantExpert image cases are eligible: their published goal contains no
individual history. Real temporal anchors and other source workflows are not
merged merely because a shared image was used in more than one task.
"""
from plantbench.contracts import logical_hash


def image_case_key(episode, gold):
    if episode.source_scope.dataset_id!='plantexpertvqa' or episode.agentic.template_id not in {'D02','C03'}:
        return None
    indexes={unit.evidence_uid:index for index,unit in enumerate(episode.evidence)}
    def neutral(value):
        if isinstance(value,str):return indexes.get(value,value)
        if isinstance(value,list):return [neutral(item) for item in value]
        if isinstance(value,dict):return {key:neutral(item) for key,item in value.items()}
        return value
    task=episode.task.model_dump(mode='json');task.pop('neutral_episode_handle')
    units=[]
    for unit in episode.evidence:
        units.append({key:getattr(unit,key) for key in ['subject_scope','variable_id','modality','evidence_kind',
            'evidence_time_policy','release_phase','text_role']})
        units[-1].update(payload=unit.payload.model_dump(mode='json'),
            record_time=unit.record_time.model_dump(mode='json') if unit.record_time else None,
            collection_time=unit.collection_time.model_dump(mode='json') if unit.collection_time else None)
    catalog=[]
    for target in episode.catalog:
        fields=target.model_dump(mode='json');fields.pop('target_id');catalog.append(neutral(fields))
    rules=[neutral({key:value for key,value in rule.items() if key not in {'id','source_refs'}}) for rule in gold['slots']]
    return logical_hash({'source':'plantexpertvqa','template':episode.agentic.template_id,
        'task':task,'evidence':units,'catalog':catalog,
        'phases':neutral([phase.model_dump(mode='json') for phase in episode.phases]),
        'budget':episode.budget.model_dump(mode='json'),'kind':episode.agentic.task_kind,
        'basis':episode.agentic.answer_basis,'rules':rules,
        'knowledge':episode.knowledge_corpus.model_dump(mode='json') if episode.knowledge_corpus else None})
