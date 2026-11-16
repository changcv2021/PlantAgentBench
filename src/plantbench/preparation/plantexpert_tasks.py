"""PlantExpertVQA image aggregation and source-knowledge reference tasks.

Repository-declared permissive/noncommercial permission is retained for internal
noncommercial evaluation. Upstream image-level attribution is not claimed fixed;
publication/redistribution remains unavailable pending that separate audit.
"""
import collections,hashlib,json,re,sqlite3,zipfile,os
from pathlib import Path
from plantbench_data.common import sha256
from plantbench.contracts import KnowledgeCorpus
from plantbench.knowledge import KnowledgeResolver
from .taskpack import DATA,FEATURES,Task,slot,digest,dump,knowledge_metadata
from .references import extract_advice,ACTION_DESCRIPTIONS,CONDITION_PATTERNS,clean_text

RELEASE='e21eacc1eff9e1db38966d78d0e8af2f22706313'
RAW=DATA/'raw/plantexpertvqa'/RELEASE/'downloads'
BASE=Path(os.environ.get('PAB_SOURCE_PROJECT', str(DATA))).resolve()

def norm(value):return ' '.join(re.findall(r'[^\W_]+',str(value).casefold()))

# These are missing-value markers, never competing biological labels.
UNKNOWN_LABELS={'', 'unknown', 'none', 'null', 'n a', 'na', 'not available', 'unspecified'}

def agreed_label(values):
    known={norm(v):v for v in values if norm(v) not in UNKNOWN_LABELS}
    return next(iter(known.values())) if len(known)==1 else None

class ExpertStore:
    def __init__(self,output,*,development=False):
        import pyarrow.parquet as pq
        self.db=sqlite3.connect(output/'plantexpertvqa.source.sqlite')
        self.db.executescript('CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS canonical (id TEXT PRIMARY KEY,kind TEXT,payload TEXT); CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT);')
        self.cases={};self.current={};self.zipfiles={};self.members={};self.cached_raw=None
        receipts={r['path']:r for r in json.loads((BASE/'data/manifests/raw_files.plantexpertvqa.json').read_text())}
        inputs={}
        names=['README.md']+['data/'+split+'.parquet' for split in ('train','val','test')]+['images_part'+str(i)+'.zip' for i in range(1,5)]
        for name in names:
            expected=receipts[name]['sha256']
            # Development reads a few actual archive members and their CRCs;
            # formal compilation additionally verifies every raw input file.
            actual=sha256(RAW/name) if not development or not name.endswith('.zip') else None
            if actual is not None and actual!=expected:raise ValueError('PlantExpert frozen raw file changed: '+name)
            inputs[name]={'sha256':expected,'verified_now':actual is not None}
        for i in range(1,5):
            name='images_part'+str(i)+'.zip';archive=zipfile.ZipFile(RAW/name);self.zipfiles[name]=archive
            for item in archive.infolist():
                if item.is_dir():continue
                if item.filename.startswith('/') or '..' in Path(item.filename).parts:raise ValueError('Unsafe original archive path')
                self.members.setdefault(item.filename,[]).append((name,item.filename,item.CRC,item.file_size))
        counts=collections.Counter()
        columns=['qa_id','image_id','image_path','crop','disease','category','severity','question_category','dataset_source']
        for split in ['train','val','test']:
            offset=0
            for batch in pq.ParquetFile(RAW/'data'/f'{split}.parquet').iter_batches(batch_size=8192,columns=columns):
                for row in batch.to_pylist():
                    ident=row['image_id'];case=self.cases.setdefault(ident,{'id':ident,'paths':set(),'crop':set(),'disease':set(),'category':set(),'severity':set(),'dataset_source':set(),'splits':set(),'qa_refs':[]})
                    case['paths'].add(row['image_path']);case['splits'].add(split)
                    for key in ['crop','disease','category','severity','dataset_source']:
                        if row[key] is not None and str(row[key]).strip():case[key].add(str(row[key]).strip())
                    case['qa_refs'].append({'qa_id':row['qa_id'],'source_file':'data/'+split+'.parquet','row':offset,'question_category':row['question_category']})
                    offset+=1;counts['qa_rows']+=1
        for ident,case in self.cases.items():
            for key,value in case.items():
                if isinstance(value,set):case[key]=sorted(value)
            self.db.execute('INSERT OR REPLACE INTO cases VALUES (?,?)',(ident,dump(case)))
        self.metadata={'source_release':RELEASE,'inputs':inputs,'qa_rows':counts['qa_rows'],'image_cases':len(self.cases),
            'permission':'provisional_internal_noncommercial_source_declared_no_redistribution',
            'license_basis':'README declares CC BY 4.0 and constituent CC0/CC BY/CC BY-NC; preserve noncommercial scope and provenance',
            'upstream_asset_attribution':'unresolved for publication; not falsely reported as resolved by repository dataset_source field',
            'case_policy':'One image ID with all original QA locators; target-specific agreement checked independently of severity conflicts',
            'source_split_policy':'Original image split retained privately; actual byte duplicate groups reconciled before any held-out claim',
            'knowledge_relation':'Same release general knowledge, independent of individual image identity/current-case answer; not independent expert adjudication'}
        self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('source_audit',dump(self.metadata)));self.db.commit()
        corpus=KnowledgeCorpus(path=str(FEATURES/'knowledge_corpus.json'),sha256=knowledge_metadata()['knowledge_sha256'],namespace='plantexpertvqa-authored-kb')
        self.knowledge=KnowledgeResolver(corpus);self.cards=[]
        for doc in self.knowledge.docs:
            fields=json.loads(doc['text']);crop=fields['crop'];condition=fields['condition']
            self.cards.append((doc,fields,{norm(crop['common_name']),norm(crop.get('scientific_name',''))},
                {norm(condition['common_name']),norm(condition.get('scientific_name','')),*[norm(x) for x in condition.get('alt_names',[])]}))
    def payload(self,table,ident):return self.current[ident]
    def group(self,subject):
        return {'strong_group':'pe_image_'+self.image_sha,'experimental_group':'pe_image_'+self.image_sha,'context_group':None}
    def read_asset(self,row):
        if self.cached_raw is None or self.cached_raw[0]!=row['asset_id']:raise ValueError('Source image cache identity mismatch')
        raw=self.cached_raw[1]
        if hashlib.sha256(raw).hexdigest()!=row['content_sha256']:raise ValueError('Cached source image changed')
        return raw
    def save_canonical(self,ident,kind,row):
        self.db.execute('INSERT OR REPLACE INTO canonical VALUES (?,?,?)',(ident,kind,dump(row)))
    def resolve(self,case):
        self.current={};self.cached_raw=None
        if len(case['paths'])!=1:return None,'conflicting_image_paths'
        path=case['paths'][0];locations=self.members.get(path,[])
        if not locations:return None,'image_path_not_in_verified_archives'
        if len({(item[2],item[3]) for item in locations})!=1:return None,'conflicting_archive_member_bytes'
        archive,member,_,_=locations[0];raw=self.zipfiles[archive].read(member)
        self.image_sha=hashlib.sha256(raw).hexdigest()
        subject=digest(('plantexpertvqa',RELEASE,case['id']));asset=digest(('plantexpertvqa',RELEASE,path,self.image_sha));obs=digest((asset,'image-observation'))
        provenance=['plantexpertvqa:'+RELEASE+':'+archive+':'+member]
        self.current[subject]={'record_id':subject,'subject_id':subject,'subject_type':'image_only_case','subject_ref':subject,
            'source_record_key':case['id'],'provenance_refs':provenance,'source_fields':{'biological_identity':'not_established','original_splits':case['splits']}}
        self.current[asset]={'record_id':asset,'asset_id':asset,'content_sha256':self.image_sha,'storage_uri':'zip-member:'+archive+':'+member,
            'provenance_refs':provenance,'source_fields':{'source_member_sha256':self.image_sha,'source_archive':archive,'source_member':member}}
        self.current[obs]={'record_id':obs,'subject_ref':subject,'value':{'kind':'asset_ref','value':asset},'variable_id':'visual.source_image',
            'scope':'image_only_case','unit':None,'times':{},'provenance_refs':provenance,'source_fields':{}}
        self.cached_raw=(asset,raw)
        for ident,row in self.current.items():
            kind='subject' if ident==subject else 'asset' if ident==asset else 'observation'
            self.save_canonical(ident,kind,row)
        return (subject,self.current[obs]),None
    def reference(self,case,subject):
        ident=digest(('pe_reference',case['id']))
        row={'record_id':ident,'subject_ref':subject,'source_label':{key:case[key] for key in ['crop','disease','category','severity']},
            'source_qa_refs':case['qa_refs'],'provenance_refs':['plantexpertvqa:'+RELEASE+':'+r['source_file']+':row-'+str(r['row']) for r in case['qa_refs']],
            'reference_kind':'source_dataset_labels_not_case_specific_environment_or_disease_duration_truth'}
        self.save_canonical(ident,'reference_annotation',row)
        return row
    def close(self):
        self.db.commit();self.db.close()
        for archive in self.zipfiles.values():archive.close()

def build_expert_case(writer,case):
    store=writer.store
    # Disagreements are target-specific: a severity disagreement does not erase
    # an agreed crop or source disease label, and no majority label is invented.
    crop=agreed_label(case['crop']);condition=agreed_label(case['disease'])
    if crop is None and condition is None:
        for template in ['D02','C03']:writer.candidate(template,digest(('plantexpertvqa',RELEASE,case['id'])),'image_case','retained_reference_material','Neither crop nor condition has an agreed known reference; missing markers ignored, actual conflicts retained')
        return
    resolved,error=store.resolve(case)
    if error:
        for template in ['D02','C03']:writer.candidate(template,digest(('plantexpertvqa',RELEASE,case['id'])),case['id'],'retained_reference_material',error)
        return
    subject,image=resolved;reference=store.reference(case,subject)
    slots=[]
    if crop is not None:slots.append(slot('crop','Host crop name.','category'))
    if condition is not None:slots.append(slot('reference_entity','Source condition name.','category'))
    target='its host crop and the condition label' if crop is not None and condition is not None else 'its host crop' if crop is not None else 'the condition label'
    t=Task(writer,'D02','P3',subject,'image_case',
        'Investigate this actual plant image. Identify '+target+' supported by the released source reference, using the image and optional general knowledge searches. '
        'This is source-reference agreement. No actual humidity, exposure history, disease duration or treatment response is supplied, even if source-generated QA prose discusses such possibilities.',
        slots,basis='source_reference',knowledge=True)
    t.text([store.current[subject]],'One archived plant image is available. No individual-plant history or independently verified diagnosis is attached.',initial=True)
    image_uid=t.image(image,description='Original plant image, with neutral source identity')
    if image_uid is None:
        for template in ['D02','C03']:writer.candidate(template,subject,'image_case','retained_reference_material','Actual image did not decode under the registered renderer')
        return
    cards=[(doc,fields) for doc,fields,crops,conditions in store.cards if crop is not None and condition is not None and norm(crop) in crops and norm(condition) in conditions]
    crop_alias=[crop];condition_alias=[condition]
    if len(cards)==1:
        fields=cards[0][1];crop_alias+=[fields['crop'].get('scientific_name','')]
        condition_alias+=[fields['condition'].get('scientific_name',''),*fields['condition'].get('alt_names',[])]
    if crop is not None:t.rule('crop','category',crop,[reference['record_id']],alternatives=[x for x in crop_alias if x],citation_kinds=['media'])
    if condition is not None:t.rule('reference_entity','category',condition,[reference['record_id']],alternatives=[x for x in condition_alias if x],citation_kinds=['media'])
    t.gold['oracle_acquire']=[image_uid];t.gold['source_qa_count']=len(case['qa_refs'])
    t.gold['reference_target_availability']={'crop':crop is not None,'condition':condition is not None,'raw_labels':reference['source_label'],
        'policy':'Ignore explicit missing markers; score agreed known fields independently; never choose a majority among conflicting known labels'}
    t.episode['admission']['visual_role']='required';t.episode['admission']['modalities_required_by_validated_task']=['RGB_display'];t.conclude()
    build_expert_plan(writer,subject,image,reference,crop,condition,condition_alias,cards)

def build_expert_plan(writer,subject,image,reference,crop,condition,condition_alias,cards):
    """Reusable full-enumeration plan compiler; no QA prose becomes case facts."""
    store=writer.store
    if len(cards)!=1:
        writer.candidate('C03',subject,'image_case','retained_reference_material','No unique exact source crop/condition match to the general knowledge corpus');return
    doc,fields=cards[0];management=fields.get('management',{})
    statements=[]
    for key in ['cultural','biological','notes']:
        text=management.get(key,[]);statements.extend(text if isinstance(text,list) else [text])
    source_text=' '.join(str(line).rstrip('.!?')+'.' for line in statements if line)
    advice=extract_advice(source_text);allowed=sorted({code for group in advice['required_action_groups'] for code in group})
    if not allowed:
        writer.candidate('C03',subject,'image_case','retained_reference_material','No mapped source management option under the registered safe condition ontology');return
    unit=store.knowledge.unit(doc);query=crop+' '+condition
    if unit.evidence_uid not in {u.evidence_uid for u in store.knowledge.search(query,3)}:
        # A unique declared title is public-searchable; this is a feasibility
        # route kept private, not an environment selector driven by current gold.
        query=doc['title']
    if unit.evidence_uid not in {u.evidence_uid for u in store.knowledge.search(query,3)}:
        raise ValueError('Frozen matching KB entry cannot be retrieved by its title')
    t=Task(writer,'C03','P4',subject,'image_case',
        'Investigate the plant image, identify its source condition, and retrieve a matching crop/condition entry from the fixed knowledge library. '
        'Propose at least one source-supported general management option with its stated conditions, citing both the case image for identification and the relevant knowledge entry for management. '
        'This is a general source-reference plan, not evidence of this plant\'s history, an optimal treatment, or a guaranteed outcome. '
        'Use decision.disposition=care_plan and submit the same management option codes in actions and decision.actions. '
        'Action meanings: '+json.dumps(ACTION_DESCRIPTIONS)+'. Condition codes: '+', '.join(CONDITION_PATTERNS)+'.',
        [slot('source_condition','Source condition name.','category'),slot('actions','One or more supported management options.','category_set',ACTION_DESCRIPTIONS)],basis='source_reference',knowledge=True)
    t.episode['task'].update(require_decision=True,decision_action_domain=list(ACTION_DESCRIPTIONS),decision_condition_domain=list(CONDITION_PATTERNS))
    t.text([store.current[subject]],'One archived plant image; no patient-specific exposure history is supplied. General reference knowledge is queryable.',initial=True)
    uid=t.image(image,description='Original plant image, with neutral source identity')
    t.rule('source_condition','category',condition,[reference['record_id']],alternatives=[x for x in condition_alias if x],citation_kinds=['media'])
    common={'match':'nonempty_subset','explicit_contradictions':advice['explicit_contradictions'],'routes':[[unit.evidence_uid]],'citation_scopes':['knowledge_entry']}
    t.rule('actions','category_set',allowed,doc['provenance_refs'],**common)
    t.rule('decision_actions','category_set',allowed,doc['provenance_refs'],location='decision.actions',conditions_by_action=advice['conditions_by_action'],**common)
    t.rule('disposition','category','care_plan',doc['provenance_refs'],location='decision.disposition')
    t.gold.update(oracle_acquire=[uid],oracle_search_queries=[query],reference_scope=advice['reference_scope'])
    t.claims=[{'knowledge_document':doc['id'],'source_text':source_text,'cleaned_source_text':clean_text(source_text),**advice}]
    t.conclude()
