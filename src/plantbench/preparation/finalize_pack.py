"""Normalize a completed source pack, recover newly mappable plans, audit it.

Runs only on a compute node. Original construction packs are never modified.
Every new or semantically changed task gets a new legal reference execution;
unchanged tasks inherit their full-enumeration producer's per-task certificate.
"""
import collections
import json
import os
import re
import sqlite3
import time
import zipfile
from pathlib import Path

from plantbench.catalog import pack, readonly
from plantbench.contracts import EpisodeSpec, KnowledgeCorpus, logical_hash
from plantbench.knowledge import KnowledgeResolver
from plantbench_data.common import sha256
from .taskpack import SourceStore, PackWriter, PackedMediaResolver, FEATURES, digest, dump, knowledge_metadata
from .normalization import normalize_task, VERSION
from .evaluation_contract import compile_evaluation
from .oracles import reference_trajectory
from .reference_tasks import canonical_list, plan_task, unique_images
from .plantexpert_tasks import build_expert_case, build_expert_plan, norm, ExpertStore, RAW, RELEASE, UNKNOWN_LABELS


TASK_SCHEMA = '''CREATE TABLE tasks (
 id TEXT PRIMARY KEY,source TEXT,primary_p TEXT,template TEXT,subject TEXT,anchor TEXT,
 case_group TEXT,original_group TEXT,split TEXT,exposure TEXT,role TEXT,kind TEXT,basis TEXT,subtype TEXT,
 media_db TEXT,episode BLOB,episode_hash TEXT,independent_group TEXT);
 CREATE INDEX task_panel ON tasks(source,primary_p,template,kind);
 CREATE INDEX task_subject ON tasks(subject);
 CREATE TABLE group_edges (a TEXT,b TEXT,reason TEXT,PRIMARY KEY(a,b,reason));
 CREATE TABLE subjects (id TEXT PRIMARY KEY,source TEXT,scope TEXT,original_group TEXT,source_splits TEXT);
 CREATE TABLE candidates (id TEXT PRIMARY KEY,template TEXT,subject TEXT,anchor TEXT,status TEXT,reason TEXT);
 CREATE TABLE adaptations (id TEXT PRIMARY KEY,changes TEXT);
 CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT);
'''
GOLD_SCHEMA = '''CREATE TABLE gold (id TEXT PRIMARY KEY,gold BLOB,gold_hash TEXT,claims BLOB,oracle BLOB);
 CREATE TABLE retained_claims (id TEXT PRIMARY KEY,claims BLOB,reason TEXT);
'''


class ReuseRenderer:
    """Reuse verified source renders; render newly recovered task images once."""
    def __init__(self, source, extra, store):
        self.store = store
        self.render_cache, self.render_failures = {}, {}
        db = readonly(source)
        for asset, payload in db.execute('SELECT asset,metadata FROM assets'):
            self.render_cache[asset] = json.loads(payload)
        self.render_failures.update(db.execute('SELECT asset,reason FROM failures'))
        db.close()
        self.media = sqlite3.connect(extra)
        self.media.execute('PRAGMA cache_size=-65536')
        self.media.executescript('CREATE TABLE renders (media_id TEXT PRIMARY KEY,sha TEXT,bytes BLOB); CREATE TABLE assets (asset TEXT PRIMARY KEY,media_id TEXT,metadata TEXT); CREATE TABLE failures (asset TEXT PRIMARY KEY,reason TEXT);')

    render = PackWriter.render

    def close(self):
        self.media.commit()
        self.media.close()


class RecoveredExpertStore:
    def __init__(self, path, knowledge, recovery_path):
        self.db = readonly(path)
        self.recovery = sqlite3.connect(recovery_path)
        self.recovery.execute('PRAGMA cache_size=-65536')
        self.recovery.execute('CREATE TABLE canonical (id TEXT PRIMARY KEY,kind TEXT,payload TEXT)')
        self.new_canonical_ids=set()
        self.zipfiles={};self.members={};self.cached_raw=None
        self.knowledge, self.current = knowledge, {}
        self.cards = []
        for doc in knowledge.docs:
            fields = json.loads(doc['text'])
            crop, condition = fields['crop'], fields['condition']
            self.cards.append((doc, fields, {norm(crop['common_name']), norm(crop.get('scientific_name', ''))},
                {norm(condition['common_name']), norm(condition.get('scientific_name', '')),
                 *[norm(x) for x in condition.get('alt_names', [])]}))

    def payload(self, table, ident):
        if ident not in self.current:
            row = self.db.execute('SELECT payload FROM canonical WHERE id=?', (ident,)).fetchone()
            if row is None:row=self.recovery.execute('SELECT payload FROM canonical WHERE id=?',(ident,)).fetchone()
            if row is None:
                raise KeyError(ident)
            self.current[ident] = json.loads(row[0])
        return self.current[ident]

    def group(self, subject):
        return self.current_group

    def save_canonical(self,ident,kind,row):
        self.recovery.execute('INSERT OR REPLACE INTO canonical VALUES (?,?,?)',(ident,kind,dump(row)))
        self.new_canonical_ids.add(ident)

    def resolve(self,case):
        if not self.zipfiles:
            for i in range(1,5):
                name='images_part'+str(i)+'.zip'
                archive=zipfile.ZipFile(RAW/name);self.zipfiles[name]=archive
                for item in archive.infolist():
                    if item.is_dir():continue
                    if item.filename.startswith('/') or '..' in Path(item.filename).parts:raise ValueError('Unsafe archive path')
                    self.members.setdefault(item.filename,[]).append((name,item.filename,item.CRC,item.file_size))
        result,error=ExpertStore.resolve(self,case)
        if result:
            self.current_group={'strong_group':'pe_image_'+self.image_sha,'experimental_group':'pe_image_'+self.image_sha,'context_group':None}
        return result,error

    read_asset=ExpertStore.read_asset
    reference=ExpertStore.reference


class Compiler:
    def __init__(self, source, root, output, *, development=False):
        self.dataset, self.root, self.output = source, Path(root), Path(output)
        self.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        receipt = self.root/(source+'.build.json')
        self.receipt = json.loads(receipt.read_text())
        if development and 'development' not in str(self.output):
            raise ValueError('Integration outputs must be explicitly development-only')
        if not self.receipt.get('formal_enumeration') and not development:
            raise ValueError('Formal finalization requires an uncapped source build')
        self.original = readonly(self.root/(source+'.tasks.sqlite'))
        self.old_gold = readonly(self.root/(source+'.gold.sqlite'))
        count = self.original.execute('SELECT count(*) FROM tasks').fetchone()[0]
        if self.receipt['validation']['all_task_reference_trajectories_passed'] != count:
            raise ValueError('Producer certificate does not cover every source task')
        self.public = sqlite3.connect(self.output/'catalog.sqlite')
        self.public.execute('PRAGMA cache_size=-65536')
        self.public.executescript(TASK_SCHEMA)
        self.private = sqlite3.connect(self.output/'gold.sqlite')
        self.private.execute('PRAGMA cache_size=-65536')
        self.private.executescript(GOLD_SCHEMA)
        self.knowledge = KnowledgeResolver(KnowledgeCorpus(path=str(FEATURES/'knowledge_corpus.json'),
            sha256=knowledge_metadata()['knowledge_sha256'], namespace='plantexpertvqa-authored-kb'))
        self.kb_refs = {ref for doc in self.knowledge.docs for ref in doc['provenance_refs']}
        self.store = RecoveredExpertStore(self.root/'plantexpertvqa.source.sqlite', self.knowledge, self.output/'source_recovery.sqlite') if source == 'plantexpertvqa' else SourceStore()
        if source == 'plantexpertvqa':
            self.canonical_ids = {row[0] for row in self.store.db.execute('SELECT id FROM canonical')}
        else:
            self.canonical_ids = set()
            for table in ['subjects', 'assets', 'observations', 'annotations', 'texts', 'links']:
                self.canonical_ids.update(row[0] for row in self.store.rows('SELECT id FROM '+table+' WHERE dataset=?', (source,)))
        self.renderer = ReuseRenderer(self.root/(source+'.media.sqlite'), self.output/'extra.media.sqlite', self.store)
        self.media_paths = [str(self.root/(source+'.media.sqlite')), str(self.output/'extra.media.sqlite')]
        self.examples = set()
        self.counts = collections.Counter()
        self.input_hashes = {str(receipt): sha256(receipt)}
        self.start = time.monotonic()
        self.superseded = source == 'mirage' and 'mirage-followup' not in str(self.root)
        for row in self.old_gold.execute('SELECT * FROM candidates'):
            if self.superseded and row[1] == 'C02':
                self.counts['superseded_C02_candidates'] += 1
                continue
            self.public.execute('INSERT INTO candidates VALUES (?,?,?,?,?,?)', row)

    def subject(self, ident):
        return self.store.payload('subjects', ident)

    def render(self, asset):
        return self.renderer.render(asset)

    def candidate(self, template, subject, anchor, status, reason):
        ident = digest((self.dataset, template, subject, str(anchor)))
        self.public.execute('INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?,?)',
                            (ident, template, subject, str(anchor), status, reason))

    def edge(self, a, b, reason):
        if a != b:
            self.public.execute('INSERT OR IGNORE INTO group_edges VALUES (?,?,?)', (a, b, reason))

    def write(self, episode, gold, oracle, claims=None, *, original=False):
        before = EpisodeSpec.model_validate(episode)
        ep, gold, claims, changes = normalize_task(before, gold, claims or [])
        if ep is not None:
            ep,gold=compile_evaluation(ep,gold)
            changes=sorted(set(changes+['evaluation_contract_v2']))
        if ep is None:
            self.candidate(before.agentic.template_id, before.source_scope.subject_refs[0], before.source_scope.anchor_ref,
                           'retained_reference_material', 'Final source-scope extraction has no safely mapped positive action')
            self.private.execute('INSERT INTO retained_claims VALUES (?,?,?)', (before.episode_id, pack(claims), changes[-1]))
            return
        ident, scope = ep.episode_id, ep.source_scope
        all_refs = set(scope.canonical_record_refs)
        for unit in ep.evidence:
            all_refs.update(unit.canonical_refs)
            all_refs.update(unit.scope_link_refs)
        for rule in gold['slots']:
            all_refs.update(rule['source_refs'])
        missing = all_refs - self.canonical_ids - self.kb_refs - getattr(self.store,'new_canonical_ids',set())
        if missing:
            raise ValueError('Unresolved canonical reference: '+ident+' '+str(sorted(missing)[:5]))
        # Completion proof inherited only for unchanged task semantics. Rebuilt
        # plans and changed scope rules are executed against the final scorer.
        semantic_change = ep.agentic.template_id in {'C01','C03'} or any('citation' in c or c=='evaluation_contract_v2' for c in changes)
        if not original or semantic_change:
            oracle, trace, score = reference_trajectory(ep, gold)
            if score['task_success'] != 1:
                failed = [r['slot_id'] for r in score['slot_results'] if not r['passed']]
                raise ValueError('Normalized reference failed: '+ident+' '+str(failed))
            self.counts['new_reference_executions'] += 1
        else:
            if not oracle:
                raise ValueError('Missing producer per-task reference certificate')
            self.counts['inherited_reference_certificates'] += 1
        key = (ep.agentic.template_id, ep.agentic.task_kind)
        if key not in self.examples:
            self.renderer.media.commit()
            resolver = PackedMediaResolver(self.media_paths, ep)
            try:
                _, _, score = reference_trajectory(ep, gold, resolver=resolver)
                if score['task_success'] != 1:
                    raise ValueError('Actual-media reference failed')
            finally:
                resolver.close()
            if reference_trajectory(ep, gold, negative=True)[2]['task_success'] != 0:
                raise ValueError('Negative reference accepted')
            if reference_trajectory(ep, gold, reverse=True)[2]['task_success'] != 1:
                raise ValueError('Legal reordered reference failed')
            self.examples.add(key)
        subject = scope.subject_refs[0]
        group = 'g:'+ep.identity.leakage_group_id
        for source_subject in scope.subject_refs:
            self.edge('s:'+source_subject, group, 'source_identity')
        source_row = self.subject(subject)
        source_splits = source_row.get('source_fields', {}).get('original_splits', [])
        self.public.execute('INSERT OR IGNORE INTO subjects VALUES (?,?,?,?,?)',
            (subject, self.dataset, scope.scope, ep.identity.leakage_group_id, dump(source_splits)))
        for unit in ep.evidence:
            if unit.payload.kind == 'media':
                self.edge(group, 'render:'+unit.payload.media_sha256, 'exact_render_shared')
            if ep.agentic.template_id == 'G02':
                for member in unit.subject_refs:
                    self.edge(group, 's:'+member, 'explicit_joint_reference_cohort')
        metadata = [ident,self.dataset,ep.agentic.primary_p,ep.agentic.template_id,subject,scope.anchor_ref,
            ep.identity.leakage_group_id,ep.identity.leakage_group_id,ep.identity.split,ep.agentic.exposure_status,
            ep.admission.role,ep.agentic.task_kind,ep.agentic.answer_basis,ep.agentic.subtype,dump(self.media_paths),
            pack(ep.model_dump(mode='json')),logical_hash(ep)]
        metadata.append(ep.identity.inference_group_id)
        self.public.execute('INSERT INTO tasks VALUES ('+','.join('?' for _ in metadata)+')', metadata)
        self.private.execute('INSERT INTO gold VALUES (?,?,?,?,?)', (ident,pack(gold),logical_hash(gold),pack(claims),pack(oracle)))
        self.public.execute('INSERT INTO adaptations VALUES (?,?)', (ident,dump(changes)))
        self.candidate(ep.agentic.template_id, subject, scope.anchor_ref, 'compiled', ep.admission.role)
        self.counts['tasks'] += 1
        if self.counts['tasks'] % 1000 == 0:
            self.flush()
            print(dump({'pack':str(self.root),'source':self.dataset,'normalized_tasks':self.counts['tasks'],
                        'elapsed_seconds':round(time.monotonic()-self.start,1)}), flush=True)

    def flush(self):
        if hasattr(self.store,'recovery'):self.store.recovery.commit()
        self.public.commit()
        self.private.commit()

    def recover_expert(self, ep, gold):
        subject = ep.source_scope.subject_refs[0]
        self.store.current = {}
        source = self.store.payload('subjects', subject)
        image_unit = next(unit for unit in ep.evidence if unit.payload.kind == 'media')
        image = self.store.payload('observations', image_unit.canonical_refs[0])
        reference = self.store.payload('annotations', gold['slots'][0]['source_refs'][0])
        rules = {rule['slot_id']:rule for rule in gold['slots']}
        crop, condition = rules['crop']['expected'], rules['reference_entity']['expected']
        cards = [(doc, fields) for doc,fields,crops,conditions in self.store.cards if norm(crop) in crops and norm(condition) in conditions]
        self.store.current_group = {'strong_group':ep.identity.leakage_group_id,
            'experimental_group':ep.identity.inference_group_id,'context_group':None}
        build_expert_plan(self,subject,image,reference,crop,condition,rules['reference_entity'].get('alternatives',[condition]),cards)

    def recover_mirage(self):
        # Same canonical source question policy as the original enumerator;
        # every source subject is revisited, including previously unmapped plans.
        for subject, in self.original.execute('SELECT subject FROM completed_subjects ORDER BY subject'):
            texts = canonical_list(self.store,'texts','mirage',subject)
            byrow = collections.defaultdict(dict)
            for row in texts:
                byrow[row['source_record_key'].rsplit('/',1)[0]][row['original_role']] = row
            chosen = sorted(((key,rows) for key,rows in byrow.items() if 'source_benchmark_question' in rows),
                key=lambda pair:('MMST_Contextual' not in pair[0],pair[0]))
            if not chosen:
                continue
            key, rows = chosen[0]
            question, answer = rows['source_benchmark_question'], rows.get('source_reference_answer')
            if answer is None:
                continue
            annotations = canonical_list(self.store,'annotations','mirage',subject)
            fields = next((r['source_label'] for r in annotations if r['annotation_type']=='source_benchmark_fields'
                           and r['source_record_key'].rsplit('/',1)[0]==key), {})
            plant_context = fields.get('entity_type')=='plant' or bool(re.search(r'\b(?:plant|tree|leaf|leaves|garden|lawn|crop|stem|root|flower|fruit|shrub|vegetable)\b',question['text'],re.I))
            if not plant_context:
                continue
            observations = [json.loads(row[0]) for row in self.store.rows("SELECT payload FROM observations WHERE dataset='mirage' AND subject=?", (subject,))]
            plan_task(self,subject,key,question,answer,unique_images(self,observations))

    def recover_expert_cases(self, expert_completed):
        # PlantExpert completion markers contain original image IDs, whereas
        # task subjects are canonical hashes. Never compare the two namespaces.
        processed={row[0] for row in self.original.execute('SELECT subject FROM completed_subjects')}
        for ident,payload in self.store.db.execute('SELECT id,payload FROM cases ORDER BY id'):
            subject=digest(('plantexpertvqa',RELEASE,ident))
            if ident not in processed or subject in expert_completed:continue
            self.public.execute("DELETE FROM candidates WHERE subject=? AND template IN ('D02','C03')",(subject,))
            build_expert_case(self,json.loads(payload))
            self.counts['revisited_missing_or_conflicting_label_cases']+=1

    def run(self):
        expert_completed=set()
        for ident, payload in self.original.execute('SELECT id,episode FROM tasks'):
            ep = EpisodeSpec.model_validate_json(payload)
            if self.superseded and ep.agentic.template_id in {'C01','C02'}:
                continue
            if self.dataset=='plantexpertvqa' and ep.agentic.template_id=='C03':
                continue
            gold, oracle, claims = self.old_gold.execute('SELECT gold,oracle,reference_claims FROM gold WHERE id=?',(ident,)).fetchone()
            gold = json.loads(gold)
            if self.dataset=='plantexpertvqa' and ep.agentic.template_id=='D02':
                # Older producers treated a literal unknown as a positive
                # target. Recompile these and all previously excluded cases.
                if any(norm(rule.get('expected','')) in UNKNOWN_LABELS for rule in gold['slots']):
                    continue
                expert_completed.add(ep.source_scope.subject_refs[0])
            self.write(ep,gold,json.loads(oracle),json.loads(claims),original=True)
            if self.dataset=='plantexpertvqa' and ep.agentic.template_id=='D02':
                self.recover_expert(ep,gold)
        if self.dataset=='plantexpertvqa':
            self.recover_expert_cases(expert_completed)
            self.store.recovery.commit()
            self.store.recovery.close()
            for archive in self.store.zipfiles.values():archive.close()
            # Final flush no longer needs this closed connection.
            del self.store.recovery
        if self.superseded:
            self.recover_mirage()
        # Exact raw image identity also propagates when two render profiles
        # happen to produce different bytes from the same original image.
        for meta in self.renderer.render_cache.values():
            self.edge('render:'+meta['sha256'],'raw:'+meta['source_sha256'],'same_original_asset')
        self.renderer.close()
        self.flush()
        report = {'source':self.dataset,'input_root':str(self.root),'output_root':str(self.output),
            'normalization_version':VERSION,'counts':dict(self.counts),'template_kind_examples':sorted(self.examples),
            'producer_report':self.receipt,'input_receipt_hashes':self.input_hashes,
            'canonical_reference_resolution':'all_episode_and_gold_refs_resolved',
            'formal_enumeration':self.receipt.get('formal_enumeration',False),
            'complete':True,'elapsed_seconds':time.monotonic()-self.start}
        self.public.execute('INSERT INTO metadata VALUES (?,?)', ('report',dump(report)))
        self.flush()
        for db in [self.public,self.private,self.original,self.old_gold,self.store.db]:
            db.close()
        (self.output/'complete.json').write_text(json.dumps(report,indent=2)+'\n')
        print(dump(report),flush=True)
        return report


def finalize_pack(arguments):
    source,root,output = arguments
    return Compiler(source,root,output).run()
