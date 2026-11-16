"""Source-reference tasks; no source answer is placed in the tool catalog."""
import collections
import json
import re
from .taskpack import Task,slot,cell,digest
from .references import clean_text,extract_advice,ACTION_DESCRIPTIONS,CONDITION_PATTERNS
from .records_tasks import stamp,refs,daily,images_for

def canonical_list(store,table,dataset,subject):
    return [json.loads(r[0]) for r in store.rows('SELECT payload FROM '+table+' WHERE dataset=? AND subject=? ORDER BY id',(dataset,subject))]

def case_question(text):
    # Remove the old dataset's output-format instruction, not user case facts.
    cleaned=clean_text(text)
    marker=re.search(r'\bUser:\s*',cleaned)
    if marker and cleaned.startswith('Please answer the following user question.'):
        cleaned=cleaned[marker.end():]
    return cleaned

def unique_images(writer,observations):
    result={}
    for row in observations:
        if row['value']['kind']!='asset_ref':continue
        asset=writer.store.payload('assets',row['value']['value'])
        result.setdefault(asset['content_sha256'],(row,asset))
    return list(result.values())

def consultation_materials(task,question,images,*,context=False):
    text=case_question(question['text'])
    # Natural sentence packets preserve every source character after cleaning.
    # The packet policy depends on text length, not on reference answers.
    sentences=re.findall(r'.+?(?:[.!?](?:\s+|$)|$)',text)
    packets=[];part=''
    for sentence in sentences:
        if part and len(part)+len(sentence)>700:packets.append(part);part=''
        part+=sentence
    if part:packets.append(part)
    if not packets:packets=[text]
    uids=[]
    for i,part in enumerate(packets):
        uids.append(task.text([question],('Source-author reconstructed context; not an authenticated original user turn.\n' if context else 'Source consultation text (formatting cleaned).\n')+part,
            initial=i==0,description='Case note continuation '+str(i+1)))
    for i,(row,asset) in enumerate(images):
        uid=task.image(row,description='Case image '+str(i+1)+'; image index is not a timestamp')
        if uid:uids.append(uid)
    return uids

def plan_task(writer,subject,row_key,question,answer,images,*,template='C01',context=False):
    advice=extract_advice(answer['text'])
    if not advice['required_action_groups']:
        writer.candidate(template,subject,row_key,'retained_reference_material','No safely mapped prescriptive group in the registered action ontology; full source answer retained')
        return
    goal=('Review the consultation, available images and case notes; use the independent knowledge library as useful. '
        'Submit a care_plan decision matching the source-supported action categories and their stated conditions. '
        'This task measures scoped agreement with an existing source reference, not optimal care, clinical certainty or intervention outcomes. '
        'Report proposed action codes in the actions answer and in decision.actions; attach condition codes to each decision action. '
        'Action meanings: '+json.dumps(ACTION_DESCRIPTIONS,ensure_ascii=False)+'. Conditions: '+', '.join(CONDITION_PATTERNS)+'. '
        'The checker covers mapped action categories/conditions, not exact quantities; do not invent missing case measurements.')
    t=Task(writer,template,'P4',subject,row_key,goal,
        [slot('actions','Proposed source-supported action category set.','category_set',ACTION_DESCRIPTIONS)],basis='source_reference',knowledge=True)
    t.episode['task'].update(require_decision=True,decision_action_domain=list(ACTION_DESCRIPTIONS),decision_condition_domain=list(CONDITION_PATTERNS))
    consultation_materials(t,question,images,context=context)
    expected=sorted({code for group in advice['required_action_groups'] for code in group})
    common={'match':'groups','groups':advice['required_action_groups'],'explicit_contradictions':advice['explicit_contradictions']}
    t.rule('actions','category_set',expected,[answer['record_id']],**common)
    t.rule('decision_actions','category_set',expected,[answer['record_id']],location='decision.actions',
        conditions_by_action=advice['conditions_by_action'],**common)
    t.rule('disposition','category','care_plan',[answer['record_id']],location='decision.disposition')
    t.claims=[{'reference_turn_ref':answer['record_id'],'source_text_origin':answer['text_origin'],
        'cleaned_source_text':clean_text(answer['text']),'cleaned_text_sha256':digest(clean_text(answer['text'])),
        'span_coordinate_system':'clean_text-v1 HTML-unescaped, whitespace-normalized, URLs/contact-redacted text',**advice}]
    t.gold['reference_scope']=advice['reference_scope'];t.conclude()

def build_mirage(writer,subject,observations):
    ds=writer.dataset;store=writer.store
    texts=canonical_list(store,'texts',ds,subject);annotations=canonical_list(store,'annotations',ds,subject)
    byrow=collections.defaultdict(dict)
    for row in texts:byrow[row['source_record_key'].rsplit('/',1)[0]][row['original_role']]=row
    for row in annotations:
        if row['annotation_type']=='source_benchmark_fields':byrow[row['source_record_key'].rsplit('/',1)[0]]['fields']=row
    images=unique_images(writer,observations)
    # Same source case across standard/contextual configurations is one case.
    # Prefer richer contextual input, then the stable source key, without gold.
    single=sorted(((key,rows) for key,rows in byrow.items() if 'source_benchmark_question' in rows),
        key=lambda pair:('MMST_Contextual' not in pair[0],pair[0]))
    chosen=single[0] if single else None
    for key,rows in single[1:]:
        for template in ['D02','C01']:writer.candidate(template,subject,key,'deduplicated','Same source consultation case; selected the canonical richer input')
    if chosen:
        key,rows=chosen;question=rows['source_benchmark_question'];annotation=rows.get('fields');answer=rows.get('source_reference_answer')
        fields=annotation['source_label'] if annotation else {}
        entity=fields.get('entity_name');entity_type=fields.get('entity_type','')
        # Non-plant arthropod cases remain in the source inventory and can be
        # evaluated separately; do not silently call house-spider ID a plant task.
        plant_context=(entity_type=='plant' or bool(re.search(r'\b(?:plant|tree|leaf|leaves|garden|lawn|crop|stem|root|flower|fruit|shrub|vegetable)\b',question['text'],re.I)))
        if entity and plant_context:
            t=Task(writer,'D02','P3',subject,key,
                'Investigate this plant consultation using source case text, optional additional notes/images and the independent reference library. '
                'Identify the main plant, pest or condition named by the source reference. An accepted common or scientific name is sufficient. '
                'This is source-reference identification, not a new verified diagnosis; preserve uncertainty through limitations where appropriate.',
                [slot('reference_entity','Main source-reference entity name.','category')],basis='source_reference',knowledge=True)
            consultation_materials(t,question,images)
            aliases=[entity]+([fields['entity_scientific_name']] if fields.get('entity_scientific_name') else [])
            common=fields.get('entity_common_names',[])
            aliases+=common if isinstance(common,list) else [common]
            t.rule('reference_entity','category',entity,[annotation['record_id']],alternatives=[a for a in aliases if isinstance(a,str) and a.strip()])
            t.conclude()
        else:writer.candidate('D02',subject,key,'retained_reference_material','No resolved entity field or no plant-related source context')
        if answer and plant_context:plan_task(writer,subject,key,question,answer,images)
        else:writer.candidate('C01',subject,key,'retained_reference_material','No matched source answer or no plant-related source context')
    # Source-author reconstructed multi-turn cases are explicitly labeled.
    # known_goal/missing_goal remain private annotations, never fabricated turns.
    multi=sorted(((key,rows) for key,rows in byrow.items() if 'source_benchmark_context' in rows),key=lambda pair:('MMMT_Decomp' not in pair[0],pair[0]))
    if multi:
        key,rows=multi[0];question=rows['source_benchmark_context'];annotation=rows.get('fields')
        fields=annotation['source_label'] if annotation else {};decision=fields.get('decision')
        for key2,_ in multi[1:]:writer.candidate('C02',subject,key2,'deduplicated','Same case/context available in another reconstruction configuration')
        if decision not in {'<Clarify>','<Respond>'}:
            writer.candidate('C02',subject,key,'retained_reference_material','No registered source disposition label');return
        if not re.search(r'\b(?:plant|tree|leaf|leaves|garden|lawn|crop|stem|root|flower|fruit|shrub|vegetable)\b',question['text'],re.I):
            writer.candidate('C02',subject,key,'retained_reference_material','Context not resolved as plant-related');return
        t=Task(writer,'C02','P4',subject,key,
            'Review this explicitly source-author reconstructed consultation context. Decide whether the next response should request clarification (defer) or provide a response from the available context (report_only). '
            'Match the source disposition reference. The case notes are archived material, not newly generated user replies. '
            'The task does not require inventing missing facts or reproducing an expert answer.',
            [slot('next_disposition','defer for clarification or report_only to respond.','category',['defer','report_only'])],basis='source_reference',knowledge=True)
        t.episode['task']['require_decision']=True
        consultation_materials(t,question,images,context=True)
        target='defer' if decision=='<Clarify>' else 'report_only'
        t.rule('next_disposition','category',target,[annotation['record_id']])
        t.rule('disposition','category',target,[annotation['record_id']],location='decision.disposition')
        t.gold['source_reference_kind']='dataset_author_reconstructed_disposition'
        t.gold['source_missing_goal_annotation']=fields.get('missing_goal')
        t.gold['stopping_class']='unresolvable_in_record' if target=='defer' else 'not_adjudicated'
        t.conclude()

def build_tobrfv(writer,subject,observations):
    annotations=canonical_list(writer.store,'annotations',writer.dataset,subject)
    labels=[row for row in annotations if row['annotation_type']=='experimental_group']
    if len(labels)!=1:raise ValueError('Expected one source pot assignment')
    annotation=labels[0];label=annotation['source_label'];days=collections.defaultdict(list)
    for row,asset in unique_images(writer,observations):
        day=stamp(row)
        if day is not None:days[day].append((row,asset))
    for day,images in sorted(days.items(),key=lambda p:float(p[0])):
        t=Task(writer,'D01','P3',subject,'experiment_day_'+day,
            'Select among the real images/views/modalities of this source pot at the specified experimental day and predict its released source group (Healthy or Virus). '
            'This is source-group reference matching. The release label is experimental assignment, not a per-day PCR result or proof that visible symptoms establish infection. '
            'Cite at least one inspected image; no fixed number or order of views is required.',
            [slot('source_group','Released experimental source group.','category',['Healthy','Virus'])],basis='source_reference')
        image_uids=[]
        for row,asset in sorted(images,key=lambda pair:(pair[1]['modality'],pair[1]['source_fields'].get('view_angle_degrees',0),pair[0]['record_id'])):
            fields=asset['source_fields'];desc='View '+str(fields.get('view_angle_degrees','unspecified'))+' degrees; '+str(fields.get('release_modality',asset['modality']))
            uid=t.image(row,description=desc)
            if uid:image_uids.append(uid)
        if not image_uids:
            writer.candidate('D01',subject,t.anchor,'retained_reference_material','No supported actual image render');continue
        # Generic source-species context does not disclose pot IDs or group paths.
        source=writer.subject(subject)
        t.text([source],'Species: '+str(source['source_fields'].get('species_original','not recorded'))+'. Experimental day: '+day+'. Available images are genuine source views, with display rendering documented.',initial=True)
        t.rule('source_group','category',label,[annotation['record_id']],citation_kinds=['media'])
        t.gold['oracle_acquire']=[image_uids[0]]
        t.gold['metric_eligibility']['B2']='N/A: the release does not establish which views are diagnostically decisive'
        t.episode['admission']['visual_role']='required';t.episode['admission']['modalities_required_by_validated_task']=['RGB_display']
        t.conclude()

def build_eschikon_background(writer,subject,observations):
    annotations=canonical_list(writer.store,'annotations',writer.dataset,subject)
    treatment=next((a for a in annotations if a['annotation_type']=='experimental_group'),None)
    if treatment is None:return
    records=[r for r in observations if r['value']['kind']=='scalar'];byday=daily(records)
    history={}
    for day,current in sorted(byday.items()):
        comparable=[v for v in current if v in history and current[v]['unit']==history[v]['unit']]
        if not comparable:history.update(current);continue
        # Stable variable ordering is registered; each actual variable/day is a
        # separate explicit deliverable, not a variant of an identical question.
        for var in comparable:
            now=current[var];old=history[var];anchor=day+':'+var
            t=Task(writer,'D03','P3',subject,anchor,
                'Investigate the archived experimental background and the specified measurement. Report the recorded nitrogen and water assignments, '
                'the change in '+var+' from its previous recorded date, and whether those two records alone establish a specific disease diagnosis. '
                'Assignments are cultivation inputs; container total mass is not individual-plant biomass. No diagnosis reference or assay is provided in this package.',
                [slot('nitrogen_assignment','Source nitrogen assignment.','category'),slot('water_assignment','Source water assignment.','category'),
                    slot('measured_direction','Direction of the specified recorded measurement mean.','comparison'),
                    slot('diagnosis_determination','Specific disease conclusion from these records (undetermined if not established).','category')])
            t.text([treatment],'Archived treatment assignment: '+json.dumps(treatment['source_label'],ensure_ascii=False),description='Source experimental assignment, not observed disease')
            old_uid=t.table(old['records'],[[cell('date',old['date']),cell(var,old['mean'],old['unit'])]],description='Earlier recorded '+var)
            now_uid=t.table(now['records'],[[cell('date',day),cell(var,now['mean'],now['unit'])]],initial=True)
            for i,(image,links) in enumerate(images_for(writer,subject,day)):t.image(image,description='Source image '+str(i+1),links=links)
            assignment_uid=t.episode['evidence'][0]['evidence_uid']
            for slot_id,key in [('nitrogen_assignment','nitrogen_input'),('water_assignment','water_input')]:
                t.rule(slot_id,'category',str(treatment['source_label'][key]),[treatment['record_id']],routes=[[assignment_uid]])
            direction='increased' if now['mean']>old['mean'] else 'decreased' if now['mean']<old['mean'] else 'unchanged'
            t.rule('measured_direction','comparison',direction,refs(now['records']+old['records']),routes=[[now_uid,old_uid]])
            t.rule('diagnosis_determination','category',None,[treatment['record_id']],determination='undetermined',routes=[[assignment_uid,now_uid]])
            t.gold['evidence_goals']=[{'routes':[[assignment_uid,now_uid,old_uid]]}];t.gold['metric_eligibility']['B2']=True
            t.gold['evidence_coverage_status']='record_and_assignment_facts_only';t.conclude()
        history.update(current)
