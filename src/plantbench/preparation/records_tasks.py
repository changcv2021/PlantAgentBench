"""Enumerate real record anchors for growth, cultivation and follow-up tasks."""
import collections
import json
import math
from .taskpack import Task,cell,slot,timepoint

# Explicit benchmark investigation thresholds, never asserted crop optima.
LIMITS={'environment.air_temperature':('degC',30.0),'environment.relative_humidity':('%',85.0),
    'environment.co2':('ppm',1000.0),'environment.solution_ec':('dS/m',2.5),
    'environment.solution_temperature':('degC',26.0),'substrate.temperature':('degC',28.0),
    'substrate.water_content_volumetric':('%vol',45.0)}

def value(row):return row['value']['value']
def stamp(row):
    point=timepoint(row)
    return None if point is None else str(point['value'])
def refs(records):return [r['record_id'] for r in records]
def daily(records):
    buckets=collections.defaultdict(list)
    for row in records:
        t=stamp(row)
        if t is None or row.get('unit') is None or type(value(row)) not in {int,float} or not math.isfinite(value(row)):continue
        buckets[(t[:10],row['variable_id'],row['unit'])].append(row)
    result=collections.defaultdict(dict)
    for (day,var,unit),rows in sorted(buckets.items()):
        result[day][var]={'records':rows,'mean':sum(value(r) for r in rows)/len(rows),'unit':unit,'date':day}
    return dict(result)

def images_for(writer,subject,day):
    """Exact source-day images or source-proven shared-image links; no inferred crosswalk."""
    store=writer.store;ds=writer.dataset;found=[]
    for row in store.rows("SELECT payload FROM observations WHERE dataset=? AND subject=? AND kind='asset_ref' AND day=? ORDER BY id",(ds,subject,day)):
        found.append((json.loads(row[0]),[]))
    # Horti links carry the explicit source table's image reference at the anchor.
    if ds=='horti_m3_tomato':
        for row in store.rows('SELECT payload FROM links WHERE dataset=? AND from_ref=?',(ds,subject)):
            link=json.loads(row[0]);fields=link.get('source_fields',{})
            anchor=fields.get('anchor_source_time')
            if isinstance(anchor,dict):anchor=anchor.get('value')
            if anchor is None or str(anchor)[:10]!=day:continue
            asset=link['to_ref']
            for obs in store.rows('SELECT payload FROM observations WHERE dataset=? AND asset=?',(ds,asset)):
                found.append((json.loads(obs[0]),[link['record_id']]))
    # Lettuce source explicitly maps camera positions to reference plants. Never
    # mix 2022 biomass images with 2024 environmental observations.
    if ds=='lettuce_rgb_env_biomass':
        for row in store.rows('SELECT payload FROM links WHERE dataset=? AND (from_ref=? OR to_ref=?)',(ds,subject,subject)):
            link=json.loads(row[0]);other=link['to_ref'] if link['from_ref']==subject else link['from_ref']
            for obs in store.rows("SELECT payload FROM observations WHERE dataset=? AND subject=? AND kind='asset_ref' AND day=? ORDER BY id",(ds,other,day)):
                found.append((json.loads(obs[0]),[link['record_id']]))
    unique={row['value']['value']:(row,links) for row,links in found}
    return list(unique.values())

def growth_pack(task,current,previous,phase,images):
    variables=sorted(current.keys() & previous.keys())
    rows=[];allrecords=[]
    for var in variables:
        now=current[var];allrecords+=now['records']
        rows.append([cell('variable',var),cell('source_date',now['date']),cell('record_mean',now['mean'],now['unit']),cell('source_record_count',len(now['records']))])
    overview=task.table(allrecords,rows,phase=phase,initial=True)
    past={};directions={}
    for i,var in enumerate(variables):
        old=previous[var];now=current[var]
        past[var]=task.table(old['records'],[[cell('variable',var),cell('source_date',old['date']),cell('record_mean',old['mean'],old['unit']),cell('source_record_count',len(old['records']))]],
            phase=phase,initial=i==0,description='Earlier recorded '+var,variable=var)
        directions[var]='decreased' if now['mean']<old['mean'] else 'increased' if now['mean']>old['mean'] else 'unchanged'
    for i,(row,links) in enumerate(images):task.image(row,phase=phase,description='Shared/source image '+str(i+1)+'; no unverified individual ROI',links=links)
    return overview,past,directions

def growth_rules(task,current,previous,pack,*,at='final',mandatory=True):
    overview,past,directions=pack;decreased=[v for v,d in directions.items() if d=='decreased']
    routes=[[overview,past[v]] for v in decreased] if decreased else [[overview,*past.values()]]
    source=refs([r for v in past for r in current[v]['records']+previous[v]['records']])
    task.rule('recorded_decline','boolean',bool(decreased),source,at=at,mandatory=mandatory,routes=routes)
    task.rule('attention_variables','category_set',decreased,source,at=at,mandatory=mandatory,routes=routes,
        match='nonempty_subset' if decreased else 'exact',support_by_label={v:[overview,past[v]] for v in decreased})
    if at=='final':
        task.gold['evidence_goals']=[{'routes':routes}];task.gold['evidence_coverage_status']='complete_for_public_record_predicate'
        task.gold['metric_eligibility']['B2']=True
        initial=set(task.episode['phases'][-1]['initial_evidence_refs'])
        task.gold['stopping_class']='initial_sufficient' if any(set(r)<=initial for r in routes) else 'resolvable_with_queries'

def build_growth(writer,subject,records):
    measurements=[r for r in records if r['variable_id'].startswith(('growth.','phenotype.')) or r['variable_id']=='container.total_mass']
    days=daily(measurements);history={};anchors=[]
    for day,current in sorted(days.items()):
        previous={v:history[v] for v in current if v in history and history[v]['unit']==current[v]['unit']}
        if not previous:
            writer.candidate('G01',subject,day,'component_material','No earlier comparable measurement at this anchor')
        else:
            images=images_for(writer,subject,day)
            variables=sorted(previous)
            goal=('Inspect this source-record unit. Flag whether any listed growth/phenotype/container measurement mean decreased relative to its latest earlier recorded date. '
                'If yes, report at least one supported decreasing variable; otherwise report an empty set. Means use all nonmissing records at the source date. '
                'The task criterion is a recorded decrease, not a disease or failed-care diagnosis. Container mass remains container mass; shared images have no validated individual ROI.')
            slots=[slot('recorded_decline','Does any comparable measurement decrease?','boolean'),
                slot('attention_variables','At least one supported decreasing variable, or empty if none.','category_set',variables)]
            t=Task(writer,'G01','P1',subject,day,goal,slots)
            pack=growth_pack(t,current,previous,0,images);growth_rules(t,current,previous,pack)
            t.conclude(kind='stop_control' if t.gold['stopping_class']=='initial_sufficient' and len(t.episode['catalog'])>=2 else None)
            anchors.append((day,current,previous,images))
        history.update(current)
    for before,after in zip(anchors,anchors[1:]):
        day0,current0,previous0,images0=before;day1,current1,previous1,images1=after
        variables=sorted(set(previous0)|set(previous1))
        t=Task(writer,'L01','P5',subject,day0+' -> '+day1,
            'At each of two actual successive observation dates, update the growth-record triage: whether any listed measurement mean fell versus its latest earlier date, and at least one supporting variable if so. '
            'Submit an ASSESS checkpoint before ADVANCE_RECORD, then submit the final date report. This is temporal monitoring of actual records, not an intervention outcome or a required change of opinion.',
            [slot('recorded_decline','Any comparable measurement decline at the current phase date?','boolean'),slot('attention_variables','Supported declining variables or empty if none.','category_set',variables)],subtype='P5a')
        p0=growth_pack(t,current0,previous0,0,images0);growth_rules(t,current0,previous0,p0,at='phase_0')
        t.phase('Next actual source observation date: '+day1)
        p1=growth_pack(t,current1,previous1,1,images1);growth_rules(t,current1,previous1,p1)
        t.conclude()

def environment_pack(task,window,threshold,phase_for_chunk):
    chunks=[]
    for start in range(0,len(window),16):
        rows=window[start:start+16];phase=phase_for_chunk(start)
        table=[[cell('record','r'+str(start+j).zfill(3)),cell('source_time',stamp(row)),cell('recorded_value',value(row),row['unit'])] for j,row in enumerate(rows)]
        uid=task.table(rows,table,phase=phase,initial=start==0,description='Recorded positions r'+str(start).zfill(3)+' to r'+str(start+len(rows)-1).zfill(3),variable=rows[0]['variable_id'])
        chunks.append(uid)
    return chunks

def event_rules(task,window,threshold,chunks,*,at='final',available=None,mandatory=True):
    available=len(window) if available is None else available
    hit=next((i for i,r in enumerate(window[:available]) if value(r)>threshold),None)
    partial=hit is None and available<len(window)
    expected=None if partial else hit is not None
    route=chunks[:(hit//16+1 if hit is not None else math.ceil(available/16))]
    source=refs(window)
    task.rule('event_present','boolean',expected,source,at=at,mandatory=mandatory,routes=[route],determination='undetermined' if partial else 'supported')
    task.rule('first_record','category',None if partial else 'none' if hit is None else 'r'+str(hit).zfill(3),source,at=at,mandatory=mandatory,routes=[route],determination='undetermined' if partial else 'supported')
    if at=='final':
        task.gold['evidence_goals']=[{'routes':[route]}];task.gold['evidence_coverage_status']='complete_for_record_event_predicate';task.gold['metric_eligibility']['B2']=True
        task.gold['stopping_class']='initial_sufficient' if hit is not None and hit<16 else 'resolvable_with_queries'

def build_environment(writer,subject,records):
    byvariable=collections.defaultdict(list)
    for row in records:
        var=row['variable_id']
        if var in LIMITS and row['unit']==LIMITS[var][0] and stamp(row) is not None and type(value(row)) in {int,float} and math.isfinite(value(row)):
            byvariable[var].append(row)
    for variable,rows in sorted(byvariable.items()):
        rows.sort(key=lambda r:(stamp(r),r['record_id']))
        threshold=LIMITS[variable][1]
        # Nonoverlapping finite windows partition the entire source sequence.
        # No per-source, per-group or temporal downsampling limit is applied.
        for start in range(0,len(rows),64):
            window=rows[start:start+64];anchor=variable+':'+str(start)+'-'+str(start+len(window)-1)
            slots=[slot('event_present','Any recorded value strictly above the investigation threshold?','boolean'),
                slot('first_record','Earliest matching record ID, or none if fully checked and absent.','category',['r'+str(i).zfill(3) for i in range(len(window))]+['none'])]
            goal=('Investigate '+variable+' in this fixed source window ('+stamp(window[0])+' through '+stamp(window[-1])+'). '
                'The public investigation criterion is a recorded value strictly greater than '+str(threshold)+' '+window[0]['unit']+'. '
                'This is an explicit benchmark constraint, not a claimed crop optimum or safety threshold. Report whether it occurs and the first source record ID; stop once the earliest occurrence is established. '
                'Records with identical timestamps retain deterministic source-record order. Missing readings are not zero and are outside this nonmissing-record window.')
            t=Task(writer,'E01','P2',subject,anchor,goal,slots)
            chunks=environment_pack(t,window,threshold,lambda _:0);event_rules(t,window,threshold,chunks)
            t.conclude(kind='stop_control' if t.gold['stopping_class']=='initial_sufficient' and len(chunks)>2 else None)
            if len(window)<32:
                writer.candidate('L02',subject,anchor,'component_material','Fewer than two complete staged source-record packets');continue
            split=((len(window)//2)//16)*16
            t=Task(writer,'L02','P5',subject,anchor,goal+' The target is the SAME complete window in both phases. '
                'Initially only its first '+str(split)+' records are released. A known occurrence establishes yes; if none is yet found, the full-window proposition is undetermined until remaining records are released. '
                'Record an ASSESS checkpoint and then ADVANCE_RECORD. Correcting an early answer may still achieve terminal success; pair consistency is reported separately.',slots,subtype='P5b')
            t.phase('Release remaining original archived records for the same target window; not newly simulated measurements')
            chunks=environment_pack(t,window,threshold,lambda i:int(i>=split))
            event_rules(t,window,threshold,chunks,at='phase_0',available=split,mandatory=False)
            event_rules(t,window,threshold,chunks)
            t.conclude()

def build_records(writer,subject,records):
    build_growth(writer,subject,records)
    build_environment(writer,subject,records)
