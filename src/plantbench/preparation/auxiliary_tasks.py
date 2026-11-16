"""Use protected source tables without fabricating plant joins or code meanings.

All extracted cells retain their original annotation as a canonical reference.
Date conflicts are scored as recorded conflicts, blanks remain missing, and a
source treatment/processing group is never relabeled an individual plant.
"""
import collections
import datetime
import json
import math
import re
from .taskpack import Task,slot,cell,digest
from .extended_records import build_constraints


def cleaned(value):
    return re.sub(r'\s+',' ',str('' if value is None else value)).strip()


def row_number(record):
    match=re.search(r'/row-(\d+)',record['source_record_key'])
    return int(match[1]) if match else 0


def date_label(value):
    """Return only an explicit month/day; no source year is invented."""
    match=re.match(r'^\s*(\d{1,2})[./](\d{1,2})(?:\D|$)',str(value or ''))
    if not match:return None
    month,day=map(int,match.groups())
    try:datetime.date(2000,month,day)
    except ValueError:return None
    return f'{month:02d}-{day:02d}'


def attention(value):
    text=cleaned(value)
    return bool(text and text.casefold()!='no')


def located(record, **address):
    return {**record, '_auxiliary_cell_address':address}


def retain_addresses(task, records):
    entries=task.gold.setdefault('source_cell_addresses', [])
    for record in records:
        entry={'canonical_ref':record['record_id'],'source_record_key':record['source_record_key'],
               **record.get('_auxiliary_cell_address',{})}
        if entry not in entries:entries.append(entry)


def cohort_phase(task,rows,phase,*,at='final'):
    targets={};positives=[];all_refs=[]
    table_rows=[]
    for index,(reference,group,date,values) in enumerate(rows):
        # Duplicate processing codes at a date remain separate source rows;
        # neutral row handles avoid implying independent biological identity.
        label='record_'+str(index+1).zfill(3)
        fields={'record':label,'source_processing_code':group,'source_date_label':date or 'not recorded',**values}
        table_rows.append((reference,label,[cell(key,value) for key,value in fields.items()]))
        all_refs.append(reference['record_id'])
        if any(attention(values.get(key)) for key in ['insect_injury','disease']):positives.append(label)
    for start in range(0,len(table_rows),16):
        packet=table_rows[start:start+16]
        uid=task.table([r[0] for r in packet],[r[2] for r in packet],phase=phase,initial=start==0,
            description='Source processing-group records '+packet[0][1]+' to '+packet[-1][1])
        targets.update({r[1]:uid for r in packet})
        retain_addresses(task,[r[0] for r in packet])
    routes=[[targets[label]] for label in positives] if positives else [list(targets.values())]
    task.rule('attention_present','boolean',bool(positives),all_refs,at=at,routes=routes)
    task.rule('attention_records','category_set',positives,all_refs,at=at,routes=routes,
        match='nonempty_subset' if positives else 'exact',support_by_label={label:[targets[label]] for label in positives})
    if at=='final':
        task.gold.update(evidence_goals=[{'routes':routes}],evidence_coverage_status='complete_for_declared_source_code_screen')
        task.gold['metric_eligibility']['B2']=True
        initial=set(task.episode['phases'][phase]['initial_evidence_refs'])
        task.gold['stopping_class']='initial_sufficient' if any(set(route)<=initial for route in routes) else 'resolvable_with_queries'


def growth_cohorts(writer,subject,origin,rows):
    bydate=collections.defaultdict(list)
    for row in rows:bydate[row[2]].append(row)
    domain=['record_'+str(i+1).zfill(3) for i in range(max(map(len,bydate.values()),default=0))]
    if not domain:return
    goal=('Review the source processing-group growth records. Flag whether any supplied insect-injury or disease cell contains a nonempty value other than the literal No (ignoring case/outer whitespace). '
        'A non-No code needs record review; its biological meaning is not inferred. An empty cell is missing, not a negative observation. '
        'Cite at least one qualifying record if present, otherwise check the complete available cohort. '
        'These are source processing groups, not independently identified plants. The criterion is a public record-screening rule, not a diagnosis.')
    slots=[slot('attention_present','Any recorded non-No, nonempty attention code?','boolean'),
           slot('attention_records','At least one qualifying neutral record ID, or an empty set.','category_set',domain)]
    ordered=[]
    for date,records in sorted(bydate.items(),key=lambda pair:pair[0] or ''):
        anchor='aux-growth:'+digest(origin)[:16]+':'+(date or 'undated')
        task=Task(writer,'G02','P1',subject,anchor,goal,slots)
        task.episode['task']['subject_description']='A cohort of processing-group records from one source growth table'
        task.episode['task']['target_time_description']='Source month/day label '+str(date)+'; year not inferred from the filename'
        cohort_phase(task,records,0)
        task.gold['auxiliary_scope']={'source_table':origin,'blank_date_not_filled':True,'record_code_meaning_not_invented':True}
        task.conclude(kind='stop_control' if task.gold['stopping_class']=='initial_sufficient' and task.episode['catalog'] else None)
        if date is not None:ordered.append((date,records))
    for (date0,rows0),(date1,rows1) in zip(ordered,ordered[1:]):
        common={r[1] for r in rows0}&{r[1] for r in rows1}
        before=[r for r in rows0 if r[1] in common];after=[r for r in rows1 if r[1] in common]
        if not before or not after:continue
        task=Task(writer,'L01','P5',subject,'aux-growth:'+digest(origin)[:16]+':'+date0+'->'+date1,
            goal+' Assess the earlier source date, then ADVANCE_RECORD and update the same screening for the later recorded date. '
            'Only processing codes present at both dates are included; the answer need not change.',slots,subtype='P5a')
        cohort_phase(task,before,0,at='phase_0');task.phase('Next actual source month/day label '+date1)
        cohort_phase(task,after,1);task.conclude()


def farming_calendar(writer,subject,origin,records):
    groups=collections.defaultdict(list)
    for record in records:
        cells=record['source_label']['cells']
        if not cleaned(cells.get('C')):continue
        groups[cleaned(cells.get('B')) or 'source stage unspecified'].append(record)
    for stage,items in sorted(groups.items()):
        task=Task(writer,'E02','P2',subject,'aux-farming:'+digest((origin,stage))[:20],
            'Audit the recorded farming calendar for source stage '+stage+'. Find any activity whose recorded closing date precedes its recorded starting date. '
            'If none is reversed, distinguish complete dates from missing/unparseable dates. Source dates are preserved even when the filename or narrative suggests a different year. '
            'This is calendar-record integrity, not proof that an intervention was performed on a particular plant. Only date fields determine this check.',
            [slot('calendar_status','Reversed interval, complete nonreversed records, or insufficient dates.','category',
                  ['recorded_reversed_interval','no_reversed_interval','insufficient_recorded_dates']),
             slot('activity_witnesses','One or more reversed/missing record IDs if applicable, otherwise empty.','category_set',
                  ['activity_'+str(i+1).zfill(2) for i in range(len(items))])])
        uids={};reversed_ids=[];missing=[];refs=[]
        for index,record in enumerate(sorted(items,key=row_number)):
            cells=record['source_label']['cells'];label='activity_'+str(index+1).zfill(2);refs.append(record['record_id'])
            uid=task.table([record],[[cell('activity_id',label),cell('source_activity',cleaned(cells.get('C'))),
                cell('recorded_start',cells.get('F')),cell('recorded_close',cells.get('G'))]],initial=index==0,
                description='Calendar fields of farming activity '+label)
            uids[label]=uid
            retain_addresses(task,[located(record,columns=['C','F','G'])])
            try:
                start=datetime.datetime.fromisoformat(str(cells.get('F')));end=datetime.datetime.fromisoformat(str(cells.get('G')))
                if end<start:reversed_ids.append(label)
            except (ValueError,TypeError):missing.append(label)
        expected='recorded_reversed_interval' if reversed_ids else 'insufficient_recorded_dates' if missing else 'no_reversed_interval'
        witnesses=reversed_ids or missing
        routes=[[uids[key]] for key in reversed_ids] if reversed_ids else [list(uids.values())]
        task.rule('calendar_status','category',expected,refs,routes=routes)
        task.rule('activity_witnesses','category_set',witnesses,refs,routes=routes,
            match='nonempty_subset' if witnesses else 'exact',support_by_label={key:[uids[key]] for key in witnesses})
        task.gold.update(evidence_goals=[{'routes':routes}],evidence_coverage_status='complete_for_source_calendar_fields')
        task.gold['metric_eligibility']['B2']=True;task.conclude()


def yield_cohort(writer,subject,origin,records):
    """Actual source yield rows; no unit or individual-plant crosswalk guessed."""
    if not records:return
    packets=[records[start:start+16] for start in range(0,len(records),16)]
    labels=['record_'+str(i+1).zfill(3) for i in range(len(records))]
    task=Task(writer,'G02','P1',subject,'aux-yield:'+digest(origin)[:20],
        'Review the actual reported yield-per-tree column in this source harvest table. Find any explicitly recorded numeric zero yield. '
        'Blank/unparseable entries are missing, not zero. If no zero is found, distinguish a complete numeric table from one with missing values. '
        'This screens harvested-yield records; it does not diagnose a cause or infer units/cross-year plant identity.',
        [slot('yield_status','Recorded zero, complete table with no zero, or incomplete table with no recorded zero.','category',
              ['recorded_zero_yield','no_zero_complete','no_zero_with_missing']),
         slot('zero_yield_records','One or more witnessed zero-yield record IDs, or an empty set.','category_set',labels)])
    zeros=[];missing=[];uids={};all_uids=[];refs=[]
    for index,packet in enumerate(packets):
        rows=[];members=[]
        for offset,(reference,name,raw,unit) in enumerate(packet):
            label=labels[index*16+offset];refs.append(reference['record_id']);members.append(label)
            rows.append([cell('record',label),cell('source_plant_token',name),cell('source_yield',raw,unit)])
            try:
                number=float(raw)
                if not math.isfinite(number):raise ValueError('Nonfinite source value')
                if number==0:zeros.append(label)
            except (TypeError,ValueError):missing.append(label)
        uid=task.table([row[0] for row in packet],rows,initial=index==0,description='Source harvest record block '+str(index+1))
        retain_addresses(task,[row[0] for row in packet])
        all_uids.append(uid);uids.update({label:uid for label in members})
    routes=[[uids[key]] for key in zeros] if zeros else [all_uids]
    expected='recorded_zero_yield' if zeros else 'no_zero_with_missing' if missing else 'no_zero_complete'
    task.rule('yield_status','category',expected,refs,routes=routes)
    task.rule('zero_yield_records','category_set',zeros,refs,routes=routes,match='nonempty_subset' if zeros else 'exact',
        support_by_label={key:[uids[key]] for key in zeros})
    task.gold.update(evidence_goals=[{'routes':routes}],evidence_coverage_status='complete_for_recorded_zero_yield_check')
    task.gold['metric_eligibility']['B2']=True;task.conclude()


class JointWriter:
    def __init__(self,writer,subjects,origin):
        self.writer=writer;self.subjects=subjects;self.origin=origin
    def __getattr__(self,key):return getattr(self.writer,key)
    def write(self,episode,gold,oracle,claims=None):
        episode['source_scope']['subject_refs']=self.subjects
        episode['task']['subject_description']='A bundle of source sensor channels with the same published process label in the same workbook scope'
        episode['task']['goal_text']+=' Channels are grouped only by their explicit shared source process label and workbook scope; this is not an individual-plant sensor or inferred plant/treatment crosswalk.'
        gold['joint_channel_scope']={'source_scope':self.origin,'subject_refs':self.subjects,'basis':'explicit source process labels within the same workbook channel key'}
        self.writer.write(episode,gold,oracle,claims)


def build_channel_bundles(writer):
    bundles=collections.defaultdict(list)
    for ident,payload in writer.store.rows("SELECT id,payload FROM subjects WHERE dataset='horti_m3_tomato'"):
        row=json.loads(payload);key=row['source_record_key']
        if '/column-' not in key:continue
        label=key.rsplit('/column-',1)[1];match=re.search(r'\bprocess\s*(CK|\d+)\b',label,re.I)
        if not match:continue
        bundles[(key.rsplit('/column-',1)[0],match[1].upper())].append(ident)
    for index,(origin,subjects) in enumerate(sorted(bundles.items())):
        if len(subjects)<2:continue
        records=[]
        for subject in subjects:
            records.extend(json.loads(row[0]) for row in writer.store.rows("SELECT payload FROM observations WHERE dataset='horti_m3_tomato' AND subject=? AND kind='scalar'",(subject,)))
        subjects=sorted(subjects)
        build_constraints(JointWriter(writer,subjects,origin),subjects[0],records,anchor_prefix='channel_bundle:'+digest(origin)[:16]+':')
        if index%10==0:
            writer.flush();print(json.dumps({'channel_bundles_processed':index+1,'candidate_counts':dict(writer.counts)}),flush=True)


def build_auxiliary(writer):
    grouped=collections.defaultdict(list);documents=[];audit=[]
    for payload, in writer.store.rows("SELECT payload FROM annotations WHERE dataset='horti_m3_tomato' AND kind IN ('source_tabular_row_unmapped','source_documentation_unmapped')"):
        row=json.loads(payload)
        if row['annotation_type']=='source_documentation_unmapped':documents.append(row)
        else:grouped[row['source_record_key'].split('/row-')[0]].append(row)
    for origin,records in sorted(grouped.items()):
        records.sort(key=row_number);subject=records[0]['subject_ref'];header=records[0]['source_label']['header']
        handled=False
        if 'Farming Records' in origin:
            farming_calendar(writer,subject,origin,records);handled=True
        elif 'Production Data' in origin and any('yield per tree' in str(h).lower() for h in header):
            yield_cohort(writer,subject,origin,[(located(r,columns=['A','B']),str(r['source_label']['cells'].get('A','')),
                         r['source_label']['cells'].get('B'),None) for r in records]);handled=True
        elif 'Growth Record Table' in origin and origin.endswith('/Sheet2'):
            parsed=[];started=False
            for record in records:
                cells=record['source_label']['cells']
                if cleaned(cells.get('C')).casefold()=='insect injury' and 'disease' in cleaned(cells.get('D')).casefold():started=True;continue
                group=cleaned(cells.get('B'))
                if not started or not re.fullmatch(r'\d+|CK',group,re.I):continue
                parsed.append((located(record,columns=['A','B','C','D','E','F','I','K']),group,date_label(cells.get('A')),
                    {'insect_injury':cells.get('C'),'disease':cells.get('D'),'growth_state_code':cells.get('E'),
                     'recorded_leaf_number':cells.get('F'),'recorded_flowering':cells.get('I'),'recorded_fruit_set':cells.get('K')}))
            if parsed:growth_cohorts(writer,subject,origin,parsed);handled=True
        audit.append({'source':origin,'rows':len(records),'status':'compiled_available_record_checks' if handled else 'retained_source_material',
                      'reason':None if handled else 'Stage-only calendar or unresolved multirow headers; source dates/codes remain available without an invented schema'})
    for record in documents:
        origin=record['source_record_key'];data=record['source_label'];subject=record['subject_ref']
        if not isinstance(data,dict):
            audit.append({'source':origin,'status':'general_reference_retained','reason':'Source README scope and reference material; no new per-plant measurement or execution inferred'})
            continue
        if '2023 Tomato Growth Record Table' in origin:
            table=data['tables'][1];rows=[]
            for row_index,values in enumerate(table[2:],start=2):
                if len(values)<12 or not re.fullmatch(r'\d+|CK',cleaned(values[1]),re.I):continue
                rows.append((located(record,table_index=1,row_index=row_index,column_indices=[0,1,2,3,4,5,8,11]),cleaned(values[1]),date_label(values[0]),{'insect_injury':values[2],'disease':values[3],
                    'growth_state_code':values[4],'recorded_leaf_number':values[5],'recorded_flowering':values[8],'recorded_fruit_set':values[11]}))
            growth_cohorts(writer,subject,origin,rows)
            audit.append({'source':origin,'rows':len(rows),'status':'compiled_available_record_checks'})
        elif '2023 Tomato Production Data' in origin:
            table=data['tables'][1]
            yield_cohort(writer,subject,origin,[(located(record,table_index=1,row_index=i,column_indices=[0,6]),row[0],row[6],'g') for i,row in enumerate(table[1:],start=1) if len(row)>6])
            audit.append({'source':origin,'rows':len(table)-1,'status':'yield_checks_compiled_quality_and_unlabeled_units_retained'})
        else:
            audit.append({'source':origin,'status':'general_reference_retained','reason':'Fertilization reference contains no per-plant executed-dose log; not treated as case measurements'})
    build_channel_bundles(writer)
    return audit
