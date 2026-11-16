"""Additional deliverables using real shared context, never inferred causality."""
import collections,json
from .taskpack import Task,slot,cell
from .records_tasks import daily,refs,stamp,LIMITS,images_for,growth_pack

def build_constraints(writer,subject,records,*,anchor_prefix=''):
    byday=daily([r for r in records if r['variable_id'] in LIMITS and r['unit']==LIMITS[r['variable_id']][0]])
    for day,variables in sorted(byday.items()):
        if len(variables)<2:
            writer.candidate('E02',subject,anchor_prefix+day,'retained_record_material','Only one compatible channel in the actual source scope');continue
        names=sorted(variables)
        t=Task(writer,'E02','P2',subject,anchor_prefix+day,
            'Check this source scope against the explicitly supplied investigation constraints for the source date. '
            'A channel satisfies its constraint only when every available recorded value is at or below its stated upper bound. '
            'Find at least one violating channel, or report no violation after checking all channels. '
            'Also state whether these sensor records alone verify execution of a cultivation plan. No execution log is included, so do not invent actions from environmental values. '
            'These are task constraints, not crop-optimal thresholds: '+json.dumps({v:LIMITS[v] for v in names}),
            [slot('constraint_violation','Any available channel violates the declared constraint?','boolean'),
                slot('violating_channels','One or more supported violating channels; empty if none.','category_set',names),
                slot('plan_execution_verified','Do the supplied records establish plan execution?','boolean')])
        uids={};violations=[]
        for i,var in enumerate(names):
            item=variables[var];rows=item['records'];values=[r['value']['value'] for r in rows]
            uids[var]=t.table(rows,[[cell('source_date',day),cell('variable',var),cell('record_min',min(values),item['unit']),
                cell('record_max',max(values),item['unit']),cell('nonmissing_record_count',len(rows))]],initial=i==0,description='Daily source-record summary for '+var,variable=var)
            if max(values)>LIMITS[var][1]:violations.append(var)
        routes=[[uids[v]] for v in violations] if violations else [list(uids.values())]
        source=refs([r for item in variables.values() for r in item['records']])
        t.rule('constraint_violation','boolean',bool(violations),source,routes=routes)
        t.rule('violating_channels','category_set',violations,source,routes=routes,match='nonempty_subset' if violations else 'exact',support_by_label={v:[uids[v]] for v in violations})
        t.rule('plan_execution_verified','boolean',False,source,routes=[list(uids.values())[:1]])
        t.gold['evidence_goals']=[{'routes':routes}];t.gold['metric_eligibility']['B2']=True;t.gold['evidence_coverage_status']='complete_for_record_constraint_predicate'
        t.conclude()

def build_growth_scope(writer,subject,records):
    days=daily([r for r in records if r['variable_id'].startswith(('growth.','phenotype.')) or r['variable_id']=='container.total_mass'])
    history={}
    for day,current in sorted(days.items()):
        previous={v:history[v] for v in current if v in history and current[v]['unit']==history[v]['unit']}
        images=images_for(writer,subject,day) if previous else []
        if not images:
            writer.candidate('G03',subject,day,'retained_record_material','No exact source-link image with comparable measurements at this anchor')
        else:
            t=Task(writer,'G03','P1',subject,day,
                'Review images and actual record means at this anchor. Determine whether the recorded variables include both an increase and a decrease against their latest earlier dates. '
                'If mixed directions occur, identify at least one variable in each direction; otherwise report an empty set. '
                'State whether the supplied measurement changes and an image, without a causal diagnosis reference, establish a specific cause. '
                'This checks recorded change and its explanatory limits; it does not score an unverified visual diagnosis or assume a shared image localizes an individual.',
                [slot('mixed_directions','Do both increased and decreased measurements occur?','boolean'),slot('direction_witnesses','Variables spanning both directions, or empty when absent.','category_set',sorted(previous)),
                    slot('specific_cause','Specific causal diagnosis (undetermined unless established).','category')])
            overview,past,directions=growth_pack(t,current,previous,0,images)
            increasing=[v for v in past if directions[v]=='increased'];decreasing=[v for v in past if directions[v]=='decreased'];mixed=bool(increasing and decreasing)
            routes=[[overview,past[a],past[b]] for a in increasing for b in decreasing] if mixed else [[overview,*past.values()]]
            source=refs([r for v in past for r in current[v]['records']+previous[v]['records']])
            t.rule('mixed_directions','boolean',mixed,source,routes=routes)
            t.rule('direction_witnesses','category_set',increasing+decreasing if mixed else [],source,routes=routes,
                match='spanning_sets' if mixed else 'exact',groups=[increasing,decreasing] if mixed else [],support_by_label={v:[overview,past[v]] for v in past})
            t.rule('specific_cause','category',None,source,determination='undetermined',routes=[[overview]])
            t.gold['evidence_goals']=[{'routes':routes}];t.gold['metric_eligibility']['B2']=True;t.gold['evidence_coverage_status']='measurement_direction_and_scope_only'
            t.conclude()
        history.update(current)

def build_shared_joint(writer,subject,records):
    if writer.dataset!='eschikon_stress':return
    links=[]
    for row in writer.store.rows('SELECT payload FROM links WHERE dataset=? AND (from_ref=? OR to_ref=?)',(writer.dataset,subject,subject)):
        link=json.loads(row[0])
        if 'greenhouse_contains_box' in link['relation']:links.append(link)
    if not links:return
    link=links[0];other=link['to_ref'] if link['from_ref']==subject else link['from_ref']
    source_rows=[json.loads(r[0]) for r in writer.store.rows("SELECT payload FROM observations WHERE dataset=? AND subject=? AND kind='scalar'",(writer.dataset,other))]
    environment=daily([r for r in source_rows if r['variable_id']=='environment.air_temperature' and r['unit']=='degC'])
    growth=daily([r for r in records if r['variable_id'] in {'phenotype.spad_index','container.total_mass'}]);history={}
    for day,current in sorted(growth.items()):
        for var,now in current.items():
            if var not in history or day not in environment:continue
            old=history[var];env=environment[day]['environment.air_temperature'];maximum=max(r['value']['value'] for r in env['records'])
            decline=now['mean']<old['mean'];warm=maximum>30
            t=Task(writer,'E03','P2',subject,day+':'+var,
                'Investigate this box and its explicitly shared greenhouse records. If the box '+var+' mean declined relative to the previous record, determine whether the SAME recorded date also has greenhouse temperature above 30 degC. '
                'Classify the outcome as no_recorded_decline, decline_with_shared_heat_event, or decline_without_shared_heat_event. '
                'This is a public investigation rule for co-occurring records, not proof of causation, a per-box temperature sensor, or a crop-optimal threshold.',
                [slot('investigation_outcome','Outcome of the stated conditional record investigation.','category',['no_recorded_decline','decline_with_shared_heat_event','decline_without_shared_heat_event']),
                    slot('causal_diagnosis','Does this joint record establish a specific cause? Use undetermined if absent.','category')])
            a=t.table(now['records'],[[cell('date',day),cell(var,now['mean'],now['unit'])]],initial=True)
            b=t.table(old['records'],[[cell('date',old['date']),cell(var,old['mean'],old['unit'])]],description='Earlier box measurement')
            c=t.table(env['records'],[[cell('date',day),cell('shared_greenhouse_max_temperature',maximum,'degC'),cell('source_record_count',len(env['records']))]],
                description='Shared greenhouse temperature summary for the recorded date',scope=env['records'][0]['scope'],links=[link['record_id']])
            for i,(image,proof) in enumerate(images_for(writer,subject,day)):t.image(image,description='Source box image '+str(i+1),links=proof)
            outcome='no_recorded_decline' if not decline else 'decline_with_shared_heat_event' if warm else 'decline_without_shared_heat_event'
            source=refs(now['records']+old['records']+env['records'])
            route=[a,b,c] if decline else [a,b]
            t.rule('investigation_outcome','category',outcome,source,routes=[route]);t.rule('causal_diagnosis','category',None,source,determination='undetermined',routes=[[a,b]])
            t.gold['evidence_goals']=[{'routes':[route]}];t.gold['metric_eligibility']['B2']=True;t.gold['evidence_coverage_status']='explicit_shared_scope_link_and_deterministic_joint_predicate'
            t.conclude()
        history.update(current)

def build_lettuce_cohort(writer):
    if writer.dataset!='lettuce_rgb_env_biomass':return
    store=writer.store
    for camera in store.rows("SELECT id FROM subjects WHERE dataset=? AND scope='camera_view'",(writer.dataset,)):
        camera=camera[0];linked={}
        for row in store.rows('SELECT payload FROM links WHERE dataset=? AND (from_ref=? OR to_ref=?)',(writer.dataset,camera,camera)):
            link=json.loads(row[0]);other=link['to_ref'] if link['from_ref']==camera else link['from_ref']
            if 'camera_view_contains_plant_position' in link['relation']:linked[other]=link
        dates=collections.defaultdict(dict)
        for subject in sorted(linked):
            rows=[json.loads(r[0]) for r in store.rows("SELECT payload FROM observations WHERE dataset=? AND subject=? AND variable='growth.fresh_mass'",(writer.dataset,subject))]
            previous=None
            for day,variables in sorted(daily(rows).items()):
                now=variables['growth.fresh_mass']
                if previous:dates[day][subject]=(now,previous)
                previous=now
        for day,members in sorted(dates.items()):
            if len(members)<2:continue
            names={subject:'head_'+str(i+1).zfill(2) for i,subject in enumerate(sorted(members))}
            t=Task(writer,'G02','P1',camera,day,
                'Within the explicitly mapped lettuce reference-head camera cohort, find at least one head whose recorded fresh mass declined from its latest earlier date, or report no such head after checking the cohort. '
                'Use the neutral head identifiers in this task. A shared canopy image is not an individual crop and a mass decrease is a record-based attention criterion, not a disease diagnosis.',
                [slot('attention_present','Any cohort member meets the recorded-decrease criterion?','boolean'),slot('attention_heads','At least one matching neutral head ID, or empty if none.','category_set',names.values())])
            uids={};declines=[];allrecords=[]
            for i,subject in enumerate(sorted(members)):
                now,old=members[subject];records=old['records']+now['records'];allrecords+=records;label=names[subject]
                uids[label]=t.table(records,[[cell('head',label),cell('previous_date',old['date']),cell('previous_mass',old['mean'],'g'),cell('current_date',day),cell('current_mass',now['mean'],'g')]],
                    initial=i==0,description='Recorded fresh-mass history for '+label,scope='individual_plant',links=[linked[subject]['record_id']])
                if now['mean']<old['mean']:declines.append(label)
            for i,(image,proof) in enumerate(images_for(writer,camera,day)):t.image(image,description='Mapped shared canopy image '+str(i+1),links=proof)
            routes=[[uids[label]] for label in declines] if declines else [list(uids.values())]
            t.rule('attention_present','boolean',bool(declines),refs(allrecords),routes=routes)
            t.rule('attention_heads','category_set',declines,refs(allrecords),routes=routes,match='nonempty_subset' if declines else 'exact',support_by_label={label:[uids[label]] for label in declines})
            t.gold['evidence_goals']=[{'routes':routes}];t.gold['metric_eligibility']['B2']=True;t.gold['evidence_coverage_status']='complete_for_mapped_reference_cohort'
            t.conclude()
