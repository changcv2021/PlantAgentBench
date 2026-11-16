"""Full-enumeration task construction primitives; private gold is a separate DB.

All public media use a fixed 768-pixel RGB transform, never source filenames.
The packed resolver serves real verified bytes and has a per-episode allowlist.
"""
import collections
import functools
import hashlib
import io
import json
import os
import sqlite3
from pathlib import Path

from PIL import Image, ImageOps
from plantbench.contracts import EpisodeSpec, logical_hash
from plantbench.environment import ResolverError

DATA=Path(os.environ.get('PAB_SOURCE_ROOT', 'source-data')).resolve()
FEATURES=Path(os.environ.get('PAB_FEATURE_ROOT', str(DATA/'artifacts/agent-centric-v03/features-v1')))
BENCH='bench-v0.4.0-evaluation01'
SOURCES=['horti_m3_tomato','lettuce_rgb_env_biomass','eschikon_stress','tobrfv_lmid','mirage']

def digest(value):return hashlib.sha256(str(value).encode()).hexdigest()
def dump(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)

@functools.lru_cache(maxsize=1)
def knowledge_metadata():return json.loads((FEATURES/'feature_report.json').read_text())

class SourceStore:
    def __init__(self):
        self.db=sqlite3.connect('file:'+os.environ.get('PAB_FEATURE_DB',str(FEATURES/'features.sqlite'))+'?mode=ro',uri=True)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA cache_size=-65536')
    def rows(self,sql,params=()):
        return self.db.execute(sql,params)
    def payload(self,table,ident):
        if table not in {'subjects','assets','observations','annotations','texts','links'}:raise ValueError(table)
        row=self.db.execute('SELECT payload FROM '+table+' WHERE id=?',(ident,)).fetchone()
        if row is None:raise KeyError((table,ident))
        return json.loads(row[0])
    def group(self,subject):
        row=self.db.execute('SELECT * FROM groups WHERE subject=?',(subject,)).fetchone()
        if row is None:raise ValueError('Missing audited source group')
        return dict(row)

class PackedMediaResolver:
    def __init__(self,db_path,episode):
        paths=db_path if isinstance(db_path,(list,tuple)) else [db_path]
        self.dbs=[sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=60) for path in paths]
        self.db=self.dbs[0]
        self.episode_id=episode.episode_id
        self.allow={u.payload.media_id:u.payload.media_sha256 for u in episode.evidence if u.payload.kind=='media'}
    def read(self,episode_id,media_id,expected_sha256):
        if episode_id!=self.episode_id or self.allow.get(media_id)!=expected_sha256:raise ResolverError('MEDIA_UNAVAILABLE')
        row=None
        for db in self.dbs:
            row=db.execute('SELECT bytes FROM renders WHERE media_id=?',(media_id,)).fetchone()
            if row is not None:break
        if row is None:raise ResolverError('MEDIA_UNAVAILABLE')
        if hashlib.sha256(row[0]).hexdigest()!=expected_sha256:raise ResolverError('MEDIA_INTEGRITY_FAILURE')
        return row[0]
    def close(self):
        for db in self.dbs:db.close()

class PackWriter:
    def __init__(self,root,dataset,store,*,auto_flush=True):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.dataset=dataset;self.store=store
        self.auto_flush=auto_flush
        self.public=sqlite3.connect(self.root/(dataset+'.tasks.sqlite'))
        self.private=sqlite3.connect(self.root/(dataset+'.gold.sqlite'))
        self.media=sqlite3.connect(self.root/(dataset+'.media.sqlite'))
        if (self.root/'build.lock.json').exists():raise ValueError('Frozen pack cannot be modified')
        self.public.executescript('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY,primary_p TEXT,template TEXT,subject TEXT,anchor TEXT,case_group TEXT,split TEXT,kind TEXT,episode TEXT); CREATE INDEX IF NOT EXISTS tasks_subject ON tasks(subject); CREATE TABLE IF NOT EXISTS completed_subjects (subject TEXT PRIMARY KEY);')
        self.private.executescript('CREATE TABLE IF NOT EXISTS gold (id TEXT PRIMARY KEY,gold TEXT,oracle TEXT,reference_claims TEXT); CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY,template TEXT,subject TEXT,anchor TEXT,status TEXT,reason TEXT);')
        self.private.execute('CREATE INDEX IF NOT EXISTS candidates_subject ON candidates(subject)')
        self.media.executescript('CREATE TABLE IF NOT EXISTS renders (media_id TEXT PRIMARY KEY,sha TEXT,bytes BLOB); CREATE TABLE IF NOT EXISTS assets (asset TEXT PRIMARY KEY,media_id TEXT,metadata TEXT); CREATE TABLE IF NOT EXISTS failures (asset TEXT PRIMARY KEY,reason TEXT);')
        self.counts=collections.Counter({a+':'+b:n for a,b,n in self.private.execute('SELECT template,status,count(*) FROM candidates GROUP BY template,status')});self.render_cache={};self.render_failures={}
        for asset,meta in self.media.execute('SELECT asset,metadata FROM assets'):self.render_cache[asset]=json.loads(meta)
        for asset,reason in self.media.execute('SELECT asset,reason FROM failures'):self.render_failures[asset]=reason
        self.subject_cache={};self.group_cache={}
    def subject(self,ident):
        if ident not in self.subject_cache:self.subject_cache[ident]=self.store.payload('subjects',ident)
        return self.subject_cache[ident]
    def render(self,asset):
        if asset in self.render_failures:return None
        if asset in self.render_cache:return self.render_cache[asset]
        row=self.store.payload('assets',asset)
        if hasattr(self.store,'read_asset'):
            raw=self.store.read_asset(row)
        else:
            path=(DATA/row['storage_uri']).resolve(strict=True)
            if not path.is_relative_to(DATA.resolve()):raise ValueError('Source asset escaped data root')
            raw=path.read_bytes()
        source_sha=hashlib.sha256(raw).hexdigest()
        if source_sha!=row['content_sha256']:raise ValueError('Source bytes changed: '+asset)
        try:
            with Image.open(io.BytesIO(raw)) as img:
                img.seek(0)
                if img.mode not in {'RGB','RGBA','L','LA','P'}:
                    raise ValueError('requires_separate_validated_numerical_render')
                image=ImageOps.exif_transpose(img).convert('RGB');image.thumbnail((768,768),Image.Resampling.LANCZOS)
                clean=Image.new('RGB',image.size);clean.paste(image)
                stream=io.BytesIO();clean.save(stream,format='PNG',compress_level=3)
                data=stream.getvalue();sha=hashlib.sha256(data).hexdigest();media_id='m_'+sha
                meta={'media_id':media_id,'sha256':sha,'width':clean.width,'height':clean.height,
                    'source_sha256':source_sha,'profile':'rgb-agentic-768-v1','source_asset':asset}
        except (ValueError,OSError) as exc:
            reason=type(exc).__name__+':'+str(exc)
            self.media.execute('INSERT INTO failures VALUES (?,?)',(asset,reason));self.render_failures[asset]=reason
            return None
        self.media.execute('INSERT OR IGNORE INTO renders VALUES (?,?,?)',(media_id,sha,data))
        self.media.execute('INSERT INTO assets VALUES (?,?,?)',(asset,media_id,dump(meta)))
        self.render_cache[asset]=meta
        if len(self.render_cache)%100==0:self.media.commit()
        return meta
    def candidate(self,template,subject,anchor,status,reason):
        ident=digest((self.dataset,template,subject,anchor))
        self.private.execute('INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?,?)',(ident,template,subject,str(anchor),status,reason))
        self.counts[template+':'+status]+=1
    def write(self,episode,gold,oracle,claims=None):
        episode=EpisodeSpec.model_validate(episode)
        episode.content_hash=logical_hash(episode.model_copy(update={'content_hash':''}))
        ident=episode.episode_id
        self.public.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',(ident,episode.agentic.primary_p,episode.agentic.template_id,
            episode.source_scope.subject_refs[0],episode.source_scope.anchor_ref,episode.identity.leakage_group_id,episode.identity.split,
            episode.agentic.task_kind,episode.model_dump_json()))
        self.private.execute('INSERT INTO gold VALUES (?,?,?,?)',(ident,dump(gold),dump(oracle),dump(claims or [])))
        self.candidate(episode.agentic.template_id,episode.source_scope.subject_refs[0],episode.source_scope.anchor_ref,'compiled',episode.admission.role)
        if self.auto_flush and sum(v for k,v in self.counts.items() if k.endswith(':compiled'))%100==0:self.flush()
    def flush(self):
        self.public.commit();self.private.commit();self.media.commit()
    def finish(self):
        self.flush()
        counts={a+':'+b:n for a,b,n in self.private.execute('SELECT template,status,count(*) FROM candidates GROUP BY template,status')}
        report={'source':self.dataset,'counts':counts,'rendered_assets':len(self.render_cache),'render_failures':dict(collections.Counter(self.render_failures.values()))}
        (self.root/(self.dataset+'.build.json')).write_text(json.dumps(report,indent=2)+'\n')
        self.public.close();self.private.close();self.media.close()
        return report

def timepoint(record):
    times=record.get('times',{})
    point=next((p for p in (times.get('event_time'),times.get('collection_time')) if p and p.get('value') is not None),None)
    if not point or point['kind'] not in {'date','datetime','experiment_day','dialog_turn','unknown'}:return None
    return {'kind':point['kind'],'value':point['value'],'precision':str(point.get('precision','source')),
        'timezone_status':point.get('timezone_status','unknown')}

class Task:
    def __init__(self,writer,template,p,subject,anchor,goal,slots,*,basis='deterministic_record',subtype=None,knowledge=False):
        self.writer=writer;self.subject=subject;self.template=template;self.p=p;self.anchor=str(anchor);self.basis=basis
        self.ident='a_'+digest((writer.dataset,template,subject,self.anchor))[:32]
        source=writer.subject(subject);self.scope=source['subject_type'];group=writer.store.group(subject)
        # Every construction source is conservatively development until a final
        # group/exposure audit assigns untouched groups. No held-out claim here.
        self.episode={'benchmark_version':BENCH,'episode_id':self.ident,'foundation_ref':'foundation-candidate-v2',
            'builder_id':'guide06-enumeration','builder_version':'0.3.0','source_scope':{'dataset_id':writer.dataset,
            'subject_refs':[subject],'scope':self.scope,'anchor_ref':self.anchor,'canonical_record_refs':[], 'provenance_refs':[]},
            'identity':{'base_case_id':self.ident,'leakage_group_id':group['strong_group'],'inference_group_id':group['experimental_group'] or group['strong_group'],
                'context_group_ids':[group['context_group']] if group['context_group'] else [],'split':'development','variant_id':'interactive','is_fixture':False},
            'admission':{'role':'record_agent' if basis=='deterministic_record' else 'reference_agent',
                'task_mask':{'T1':True,'T2':True,'T3':True,'T4':p=='P4','T5':p=='P5'},
                'scoring_mask':{'T1':True,'T2':True,'T3':True,'T4':p=='P4','T5':p=='P5'},
                'reason_codes':['guide06_source_grounded','human_review_pending_nonblocking'],'review_tier':basis,
                'modalities_present':[],'modalities_required_by_validated_task':[],'visual_role':'supporting'},
            'task':{'neutral_episode_handle':'case_'+digest(self.ident)[:16],'subject_description':'One source-record '+self.scope.replace('_',' '),
                'scope':self.scope,'target_time_description':self.anchor,'goal_text':goal,'assessment_slots':slots,
                'withholding_rationale':'Materials are organized into source-grounded records. Select what is needed for the stated goal; stop when sufficient.',
                'knowledge_search_available':knowledge,'knowledge_scope_description':'Fixed independent general plant knowledge, never case-specific measurements or hidden source answers.' if knowledge else None},
            'evidence':[],'catalog':[],'phases':[{'phase_id':'phase_0','initial_evidence_refs':[],
                'require_checkpoint_before_advance':False,'description':'Source record review'}],
            'budget':{},'visibility_policy_ref':'guide06-public-evidence-private-reference-v1','render_profile_ref':'rgb-agentic-768-v1',
            'license_scope_refs':['internal_research:'+writer.dataset],
            'agentic':{'primary_p':p,'template_id':template,'task_kind':'investigative','answer_basis':basis,'subtype':subtype,
                'human_review_status':'pending','exposure_status':'development_conservative_pending_group_audit',
                'choice_opportunity':'Select source material, search independently when available, and submit once the goal is supported.'}}
        if knowledge:
            report=knowledge_metadata()
            self.episode['knowledge_corpus']={'path':str(FEATURES/'knowledge_corpus.json'),'sha256':report['knowledge_sha256'],'namespace':'plantexpertvqa-authored-kb'}
        self.gold={'episode_id':self.ident,'primary_p':p,'answer_basis':basis,'slots':[],'milestones':[],
            'evidence_goals':None,'evidence_coverage_status':'not_adjudicated','stopping_class':'not_adjudicated',
            'metric_eligibility':{},
            'human_review_status':'pending','human_review_blocks_execution':False}
        self.oracle=[];self.claims=[]
    def phase(self,description):
        self.episode['phases'][-1]['require_checkpoint_before_advance']=True
        self.episode['task']['require_phase_checkpoints']=True
        i=len(self.episode['phases']);self.episode['phases'].append({'phase_id':'phase_'+str(i),'initial_evidence_refs':[],
            'require_checkpoint_before_advance':False,'description':description})
        return i
    def unit(self,records,payload,*,phase=0,initial=False,description='Source material',variable=None,links=(),scope=None,modality=None):
        uid='u_'+digest((self.ident,len(self.episode['evidence'])))[:24]
        provenance=sorted({p for r in records for p in r.get('provenance_refs',[])})
        refs=[r.get('record_id',r.get('asset_id',r.get('subject_id'))) for r in records]
        if not all(refs):raise ValueError('Evidence requires canonical source identities')
        unit={'evidence_uid':uid,'public_evidence_id':'e_'+digest(uid)[:16],'canonical_refs':refs,
            'provenance_refs':provenance,'subject_scope':scope or self.scope,'subject_refs':list({r.get('subject_ref',self.subject) for r in records}),
            'variable_id':variable,'scope_link_refs':list(links),'modality':modality,'payload':payload,
            'measurement_method':'verbatim_or_declared_record_aggregation','evidence_kind':'source_record',
            'record_time':timepoint(records[-1]),'evidence_time_policy':'curated_record_review','release_phase':phase,
            'access_class':'observation_candidate','permission_status':'allowed','quality_status':'accepted',
            'semantic_atom_ids':refs,'future_dependence':None}
        self.episode['evidence'].append(unit)
        src=self.episode['source_scope'];src['canonical_record_refs']=sorted(set(src['canonical_record_refs']+refs+list(links)))
        src['provenance_refs']=sorted(set(src['provenance_refs']+provenance))
        if initial:self.episode['phases'][phase]['initial_evidence_refs'].append(uid)
        else:
            self.episode['catalog'].append({'target_id':'t_'+digest(uid)[:16],
                'action_type':'INSPECT' if payload['kind']=='media' else 'QUERY','description':description,
                'variable_id':variable,'scope':scope or self.scope,'time_description':'Source archive material for phase '+str(phase),
                'result_kind':payload['kind'],'evidence_refs':[uid],'release_phase':phase})
        return uid
    def text(self,records,text,**kw):return self.unit(records,{'kind':'text','value':text},**kw)
    def table(self,records,rows,**kw):return self.unit(records,{'kind':'table','rows':rows},**kw)
    def image(self,observation,*,phase=0,initial=False,description='Source image',links=()):
        asset=observation['value']['value'];meta=self.writer.render(asset)
        if meta is None:return None
        return self.unit([observation],{'kind':'media','media_id':meta['media_id'],'media_mime':'image/png',
            'media_sha256':meta['sha256'],'media_width':meta['width'],'media_height':meta['height']},
            phase=phase,initial=initial,description=description,modality='RGB_display',links=links,scope=observation['scope'])
    def rule(self,slot,kind,expected,refs,*,at='final',mandatory=True,routes=None,**extra):
        rule={'id':slot+'_'+at,'slot_id':slot,'kind':kind,'expected':expected,'source_refs':refs,
            'evaluate_at':at,'mandatory':mandatory,'citation_policy':'verified_support' if routes else 'visible_only',
            'support_routes':routes or [],**extra}
        self.gold['slots'].append(rule)
        return rule
    def conclude(self,*,kind=None):
        e=self.episode;count=len(e['catalog']);phases=len(e['phases'])
        if kind is None:kind='investigative' if count>=2 or e['task']['knowledge_search_available'] or phases>1 else 'component'
        e['agentic']['task_kind']=kind
        if kind=='component':e['admission']['role']='component_only'
        modalities=sorted({u['modality'] or ('record_text' if u['payload']['kind']=='text' else 'record_table') for u in e['evidence']})
        e['admission']['modalities_present']=modalities
        e['budget']={'profile_id':'guide06-finite-context-v1','max_actions':max(16,min(count+phases*2+4,40)),
            'query_cost_budget':max(12,min(count+4,32)),'max_phases':phases,'max_query_targets':count,
            'max_unique_media_assets':len({u['payload'].get('media_id') for u in e['evidence'] if u['payload']['kind']=='media'}),
            'max_total_visible_table_values':max(512,sum(sum(len(row) for row in u['payload'].get('rows',[])) for u in e['evidence'])),
            'max_total_visible_text_chars':max(24000,sum(len(str(u['payload'].get('value',''))) for u in e['evidence'])),
            'reserve_terminal_action':True}
        from .evaluation_contract import compile_evaluation
        episode,gold=compile_evaluation(e,self.gold)
        self.writer.write(episode.model_dump(mode='json'),gold,self.oracle,self.claims)

def cell(column,value,unit=None):return {'column':column,'value':value,'unit':unit}
def slot(ident,question,kind,domain=(),unit=None):return {'slot_id':ident,'question':question,'value_kind':kind,'candidate_domain':list(domain),'unit':unit}
