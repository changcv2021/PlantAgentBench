"""Complete source-context enumeration, preserving actual archived prefixes.

These are explicitly source-author reconstructions, not authenticated new human
answers. Gold is the source disposition at each actual archived context.
"""
import collections,json,re
from .taskpack import Task,slot,digest
from .reference_tasks import canonical_list,unique_images,consultation_materials
from .references import clean_text

def context_candidates(writer,subject):
    texts=canonical_list(writer.store,'texts','mirage',subject)
    annotations=canonical_list(writer.store,'annotations','mirage',subject)
    fields={a['source_record_key'].rsplit('/',1)[0]:a for a in annotations if a['annotation_type']=='source_benchmark_fields'}
    contexts=[]
    for row in texts:
        if row['original_role']!='source_benchmark_context':continue
        key=row['source_record_key'].rsplit('/',1)[0];annotation=fields.get(key)
        if annotation is None or annotation['source_label'].get('decision') not in {'<Clarify>','<Respond>'}:
            writer.candidate('C02',subject,key,'retained_reference_material','Missing actual source disposition annotation');continue
        clean=clean_text(row['text']);contexts.append({'key':key,'text':row,'clean':clean,'annotation':annotation,
            'turns':annotation['source_label'].get('dialog_turns'),'disposition':'defer' if annotation['source_label']['decision']=='<Clarify>' else 'report_only'})
    grouped=collections.defaultdict(list)
    for ctx in contexts:grouped[ctx['clean']].append(ctx)
    canonical=[]
    for clean,rows in grouped.items():
        if len({r['disposition'] for r in rows})>1:
            for row in rows:writer.candidate('C02',subject,row['key'],'retained_reference_material','Same archived context has contradictory source dispositions; preserved without majority gold')
            continue
        rows.sort(key=lambda r:('MMMT_Decomp' not in r['key'],r['key']))
        canonical.append(rows[0])
        for row in rows[1:]:writer.candidate('C02',subject,row['key'],'deduplicated','Exact cleaned context duplicate across source configurations; distinct contexts are retained')
    plant_annotation=any(str(a['source_label'].get('entity_type','')).casefold() in {'plant','crop','tree','disease','plant disease'} for a in annotations if a['annotation_type']=='source_benchmark_fields' and isinstance(a['source_label'],dict))
    plant_context=plant_annotation or any(re.search(r'\b(?:plant|tree|leaf|leaves|garden|lawn|crop|stem|root|flower|fruit|shrub|vegetable|tomato|pepper|grape|orchid|hydrangea|rose)\b',ctx['clean'],re.I) for ctx in canonical)
    return sorted(canonical,key=lambda r:(len(r['clean']),r['clean'],r['key'])),plant_context

def row_images(writer,observations,ctx):
    rows=[row for row in observations if row['source_record_key'].rsplit('/',1)[0]==ctx['key']]
    return unique_images(writer,rows)

def build_consultation_followup(writer,subject,observations):
    contexts,plant_related=context_candidates(writer,subject)
    for ctx in contexts:
        t=Task(writer,'C02','P4',subject,ctx['key'],
            'Review this source-author reconstructed consultation context and decide whether the next response should request clarification (defer) or provide a response (report_only). '
            'Archived notes and the images attached to THIS source context are queryable. Match the source disposition reference; source reconstruction is not an authenticated new human reply. '
            'Do not invent missing facts. Different archived contexts of the same case remain in one leakage group.',
            [slot('next_disposition','defer for clarification or report_only to respond.','category',['defer','report_only'])],basis='source_reference',knowledge=True)
        t.episode['task']['require_decision']=True
        consultation_materials(t,ctx['text'],row_images(writer,observations,ctx),context=True)
        t.rule('next_disposition','category',ctx['disposition'],[ctx['annotation']['record_id']])
        t.rule('disposition','category',ctx['disposition'],[ctx['annotation']['record_id']],location='decision.disposition')
        t.gold.update(source_reference_kind='dataset_author_reconstructed_disposition',
            source_missing_goal_annotation=ctx['annotation']['source_label'].get('missing_goal'),
            stopping_class='unresolvable_in_record' if ctx['disposition']=='defer' else 'not_adjudicated')
        if not plant_related:t.episode['admission']['reason_codes'].append('plant_domain_unresolved_component')
        t.conclude(kind=None if plant_related else 'component')
    # Finite nearest-successor relation, not all O(n^2) prefix pairs.
    for earlier in contexts:
        successors=[ctx for ctx in contexts if len(ctx['clean'])>len(earlier['clean']) and ctx['clean'].startswith(earlier['clean'])
            and isinstance(ctx['turns'],int) and isinstance(earlier['turns'],int) and ctx['turns']>earlier['turns']]
        if not successors:
            writer.candidate('L03',subject,earlier['key'],'retained_reference_material','No longer exact archived context prefix with later declared source turn count');continue
        later=min(successors,key=lambda r:(len(r['clean']),r['key']))
        anchor=earlier['key']+' -> '+later['key']
        t=Task(writer,'L03','P5',subject,anchor,
            'Re-evaluate the SAME consultation-sufficiency rule as an actual archived source context expands: should the next response clarify missing information (defer) or respond (report_only)? '
            'The two stages are source-author reconstructed records with their own source disposition references, not newly generated user facts or independently authenticated original turns. '
            'Submit an ASSESS checkpoint, then ADVANCE_RECORD to receive the real later context suffix. Maintain or revise the disposition based on the released record. '
            'An early error can be corrected for terminal success; the strict before/after pair is scored separately.',
            [slot('next_disposition','Whether the currently released consultation context calls for clarification or response.','category',['defer','report_only'])],basis='source_reference',subtype='P5b',knowledge=True)
        consultation_materials(t,earlier['text'],row_images(writer,observations,earlier),context=True)
        t.rule('next_disposition','category',earlier['disposition'],[earlier['annotation']['record_id']],at='phase_0',mandatory=False)
        t.phase('Later archived source context; declared source dialog turns '+str(later['turns']))
        suffix=later['clean'][len(earlier['clean']):].strip()
        if not suffix:raise ValueError('Empty actual follow-up suffix')
        t.text([later['text']],'Additional SOURCE-AUTHOR reconstructed context (not a newly simulated reply):\n'+suffix,phase=1,initial=True)
        before_sha={asset['content_sha256'] for _,asset in row_images(writer,observations,earlier)}
        for index,(row,asset) in enumerate(row_images(writer,observations,later)):
            if asset['content_sha256'] not in before_sha:t.image(row,phase=1,description='Additional image attached to later source context '+str(index+1))
        t.rule('next_disposition','category',later['disposition'],[later['annotation']['record_id']])
        t.gold['source_reference_kind']='exact_prefix_extension_of_archived_source_author_contexts'
        t.gold['transition_provenance']={'earlier_context_ref':earlier['text']['record_id'],'later_context_ref':later['text']['record_id'],
            'earlier_cleaned_text_sha256':digest(earlier['clean']),'later_cleaned_text_sha256':digest(later['clean']),
            'suffix_start_in_cleaned_later_context':len(earlier['clean']),'source_turns':[earlier['turns'],later['turns']],
            'original_user_role_authenticated':False,'case_facts_generated_by_builder':False}
        if not plant_related:t.episode['admission']['reason_codes'].append('plant_domain_unresolved_component')
        t.conclude(kind=None if plant_related else 'component')
