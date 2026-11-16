"""Exact-identity grouping and cumulative project exposure, with no score input."""
import collections
import json
from pathlib import Path
from plantbench.catalog import readonly
from .taskpack import DATA, FEATURES, digest


class Groups:
    def __init__(self):
        self.parent = {}
        self.exposures = collections.defaultdict(set)

    def find(self, value):
        parent = self.parent
        parent.setdefault(value,value)
        current=value
        while parent[current]!=current:
            current=parent[current]
        while parent[value]!=value:
            prior=parent[value]
            parent[value]=current
            value=prior
        return current

    def union(self, a, b):
        a,b=self.find(a),self.find(b)
        if a!=b:
            # Deterministic identity, independent of file/worker completion order.
            low,high=sorted((a,b))
            self.parent[high]=low

    def expose(self, node, reason):
        self.find(node)
        self.exposures[node].add(reason)

    def seal(self):
        reasons=collections.defaultdict(set)
        for node,values in self.exposures.items():
            reasons[self.find(node)].update(values)
        result={}
        for node in self.parent:
            root=self.find(node)
            group='ag_'+digest(root)[:32]
            exposed=sorted(reasons[root])
            bucket=int(digest('guide06-split-v1:'+group)[:8],16)%10
            split='development' if exposed or bucket<2 else 'pilot_holdout' if bucket<8 else 'reserve'
            result[node]={'group':group,'split':split,'reasons':exposed,
                'exposure':'project_development_exposed' if exposed else 'no_recorded_project_case_exposure_pre_freeze'}
        return result


def subject_mentions(value):
    """Read explicit source identity keys, never infer identities from prose."""
    if isinstance(value,dict):
        for key,item in value.items():
            if key in {'subject_id','subject_ref','case'} and isinstance(item,str):
                yield item
            elif key=='subject_refs' and isinstance(item,list):
                yield from (v for v in item if isinstance(v,str))
            else:
                yield from subject_mentions(item)
    elif isinstance(value,list):
        for item in value:
            yield from subject_mentions(item)


def collect_exposure(groups, *, extra_registrations=()):
    receipts=[]
    prepared=DATA/'artifacts/agent-centric-v03'
    for name in ['development-tasks01','development-pe01']:
        root=prepared/name
        # Fixed bounded registry of known development artifacts.
        for source in ['horti_m3_tomato','lettuce_rgb_env_biomass','eschikon_stress','tobrfv_lmid','mirage','plantexpertvqa']:
            path=root/(source+'.tasks.sqlite')
            if not path.exists():continue
            db=readonly(path)
            for subject,group in db.execute('SELECT DISTINCT subject,case_group FROM tasks'):
                groups.expose('s:'+subject,name)
                groups.expose('g:'+group,name)
            db.close();receipts.append(str(path))
    for source in ['horti_m3_tomato','lettuce_rgb_env_biomass','eschikon_stress','tobrfv_lmid','mirage']:
        path=prepared/'source-index-v1/private'/(source+'.samples.json')
        if path.exists():
            for subject in subject_mentions(json.loads(path.read_text())):
                groups.expose('s:'+subject,'source_inventory_design_inspection')
            receipts.append(str(path))
    path=prepared/'task-profile-v1/mirage_examples.private.json'
    if path.exists():
        for subject in subject_mentions(json.loads(path.read_text())):
            groups.expose('s:'+subject,'MIRAGE_design_examples')
        receipts.append(str(path))
    path=prepared/'horti_auxiliary_audit01.json'
    if path.exists():
        for subject in subject_mentions(json.loads(path.read_text())):
            groups.expose('s:'+subject,'Horti_auxiliary_source_schema_inspection')
        receipts.append(str(path))
    path=prepared/'plantexpert_gap_audit01.json'
    if path.exists():
        for subject in subject_mentions(json.loads(path.read_text())):
            groups.expose('s:'+subject,'PlantExpert_candidate_label_inspection')
        receipts.append(str(path))
    from .plantexpert_tasks import RELEASE
    for name in ['51517fa78a37207e.jpg','fdf20db891df9a3e.jpg','c346dd5a8f7e3b26.jpg']:
        for value in [name,Path(name).stem]:
            groups.expose('s:'+digest(('plantexpertvqa',RELEASE,value)),'PlantExpert_source_QA_design_inspection')
    old=DATA/'benchmarks/bench-v0.2.0-visual-reviewed01'
    path=old/'manifests/benchmark.lock.json'
    if path.exists():
        lock=json.loads(path.read_text());receipts.append(str(path))
        for relative in lock['artifacts']:
            if not relative.startswith('private/episodes/') or not relative.endswith('/episode.json'):continue
            ep=json.loads((old/relative).read_text())
            for subject in ep['source_scope']['subject_refs']:
                groups.expose('s:'+subject,'old_visual_benchmark_design_or_model_exposure')
            groups.expose('g:'+ep['identity']['leakage_group_id'],'old_visual_benchmark_design_or_model_exposure')
            for unit in ep['evidence']:
                if unit['payload']['kind']=='media':
                    groups.expose('render:'+unit['payload']['media_sha256'],'old_visual_benchmark_design_or_model_exposure')
    registrations=[prepared/'qwen9b-runtime01/registration.json',prepared/'qwen9b-runtime02/registration.json',*map(Path,extra_registrations)]
    for path in registrations:
        if not path.exists():continue
        data=json.loads(path.read_text());receipts.append(str(path))
        for row in data['episodes']:
            ep=row['episode']
            for subject in ep['source_scope']['subject_refs']:
                groups.expose('s:'+subject,'actual_Qwen_runtime_integration')
            groups.expose('g:'+ep['identity']['leakage_group_id'],'actual_Qwen_runtime_integration')
    return sorted(set(receipts))
