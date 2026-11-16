"""Fixed-denominator, group-first reporting for complete model evaluations."""
import collections

AGENT_METRICS=['format_raw','format_normalized','content_correctness','evidence_grounding',
    'valid_tool_use','evidence_acquisition','query_cost']
METRICS=['task_success','milestone_progress',*AGENT_METRICS,'content_coverage','M1','M2','M3',
    'action_validity','tool_validity','queries_on_success','premature_submit','appropriate_deferral','revision_pair_success',
    'content_all_required','evidence_requirements','report_completeness','milestone_content_progress','milestone_evidence_progress',
    *[m+'_'+component for m in ['M1','M2','M3'] for component in ['content','evidence','stage']]]


def mean(values):
    values=[v for v in values if v is not None]
    return sum(values)/len(values) if values else None


class Aggregate:
    def __init__(self, planned, *, model, mode='model_run'):
        self.model=model
        if mode not in {'model_run','offline_protocol_replay','offline_rescore'}:raise ValueError('Unknown aggregation mode')
        self.mode=mode
        self.planned={row['id']:row for row in planned}
        self.groups={}
        self.seen=set();self.milestones=collections.Counter();self.milestone_passes=collections.Counter()
        self.score_revisions={}
        for row in self.planned.values():
            key=self.key(row)
            group=self.groups.setdefault(key,{'planned':0,'completed':0,'infra':0,'metrics':collections.defaultdict(list),
                'action_attempts':0,'tool_attempts':0,'duplicates':0,'successful_tasks':0,
                'numerators':collections.Counter(),'denominators':collections.Counter()})
            group['planned']+=1

    @staticmethod
    def key(row):
        suite='component' if row['kind']=='component' else 'agent'
        return row['source'],row['primary_p'],row['basis'],suite,row.get('independent_group',row['case_group'])

    def add(self, ident, status, score=None):
        if ident not in self.planned or ident in self.seen:
            raise ValueError('Unexpected or repeated base task in complete evaluation')
        self.seen.add(ident);row=self.planned[ident];group=self.groups[self.key(row)]
        if status!='completed':
            group['infra']+=1
            return
        origins={'model_run','model_evaluation'} if self.mode=='model_run' else ({'offline_rescore'} if self.mode=='offline_rescore' else {'scripted_validation'})
        if score is None or score['episode_id']!=ident or score['result_origin'] not in origins:
            raise ValueError('Only actual model score reports enter model aggregates')
        if score['primary_p']!=row['primary_p'] or score['answer_basis']!=row['basis']:
            raise ValueError('Score panel differs from its frozen planned panel')
        revision=score.get('scorer_version','unspecified')
        prior=self.score_revisions.setdefault(row['primary_p'],revision)
        if prior!=revision:raise ValueError('Cannot mix scorer revisions within one P panel')
        if 'independent_group' in row and score.get('case_group_id')!=row['independent_group']:
            raise ValueError('Score statistical group differs from the frozen task')
        group['completed']+=1;group['successful_tasks']+=score['task_success']
        values={'task_success':score['task_success'],'milestone_progress':score['milestone_progress']}
        group['numerators']['task_success']+=score['task_success'];group['denominators']['task_success']+=1
        necessary=[m for m in score['milestone_results'] if m.get('required',True)]
        group['numerators']['milestone_progress']+=sum(bool(m['passed']) for m in necessary)
        group['denominators']['milestone_progress']+=len(necessary)
        for name,value in score.get('metrics',{}).items():
            values[name]=value['value']
            group['numerators'][name]+=value['numerator'];group['denominators'][name]+=value['denominator']
        if 'content_coverage' in score:
            values['content_coverage']=score['content_coverage']['value']
            group['numerators']['content_coverage']+=score['content_coverage']['numerator']
            group['denominators']['content_coverage']+=score['content_coverage']['denominator']
        for milestone in score['milestone_results']:
            if milestone['id'] in {'M1','M2','M3'}:
                values[milestone['id']]=milestone['passed']
                group['numerators'][milestone['id']]+=int(bool(milestone['passed']))
                group['denominators'][milestone['id']]+=int(milestone['passed'] is not None)
                for component,key in [('content','content_pass'),('evidence','evidence_requirement_pass'),('stage','stage_pass')]:
                    if key not in milestone:continue
                    name=milestone['id']+'_'+component;value=milestone[key]
                    values[name]=value
                    group['numerators'][name]+=int(bool(value));group['denominators'][name]+=int(value is not None)
        values.update({k:score['behavior'].get(k) for k in ['action_validity','tool_validity','evidence_acquisition','queries_on_success','query_cost']})
        specialized=score['specialized']
        values.update({k:specialized.get(k) for k in ['premature_submit','appropriate_deferral']})
        values['revision_pair_success']=None if specialized.get('revision') is None else specialized['revision']['pair_success']
        for metric,value in values.items():
            if value is not None:group['metrics'][metric].append(float(value))
        for name in ['action_attempts','tool_attempts']:group[name]+=score['behavior'][name]
        group['duplicates']+=score['behavior']['duplicate_queries']
        for milestone in score['milestone_results']:
            if milestone['passed'] is None:continue
            key=(row['source'],row['primary_p'],row['template'],milestone['id'])
            self.milestones[key]+=1;self.milestone_passes[key]+=milestone['passed']

    def report(self):
        panels=collections.defaultdict(list)
        for key,value in self.groups.items():panels[key[:-1]].append(value)
        rows=[]
        for key,groups in sorted(panels.items()):
            planned=sum(g['planned'] for g in groups);completed=sum(g['completed'] for g in groups)
            complete=planned==completed
            metrics={}
            for name in METRICS:
                group_values=[mean(g['metrics'][name]) for g in groups if g['metrics'][name]]
                metrics[name]={'value':mean(group_values) if complete else None,
                    'completed_only_provisional_value':mean(group_values),'eligible_groups':len(group_values),
                    'eligible_completed_tasks':sum(len(g['metrics'][name]) for g in groups),
                    'pooled_numerator':sum(g['numerators'][name] for g in groups),
                    'pooled_denominator':sum(g['denominators'][name] for g in groups),
                    'denominator_policy':'within-group mean then group macro; incomplete panel has no complete value'}
            tools=sum(g['tool_attempts'] for g in groups)
            rows.append({'source':key[0],'primary_p':key[1],'answer_basis':key[2],'suite':key[3],
                'planned_tasks':planned,'completed_tasks':completed,'groups':len(groups),
                'infrastructure_failures':sum(g['infra'] for g in groups),'not_completed':planned-completed,
                'successes':sum(g['successful_tasks'] for g in groups),'complete':complete,'metrics':metrics,
                'raw_denominators':{'action_attempts':sum(g['action_attempts'] for g in groups),'tool_attempts':tools,
                    'duplicate_queries':sum(g['duplicates'] for g in groups),
                    'pooled_duplicate_rate':sum(g['duplicates'] for g in groups)/tools if tools else None}})
        p_table=[]
        for suite in ['agent','component']:
            for basis in ['all_task_goals','deterministic_record','source_reference','domain_validated']:
                for primary in ['P1','P2','P3','P4','P5']:
                    selected=[row for row in rows if row['suite']==suite and row['primary_p']==primary and (basis=='all_task_goals' or row['answer_basis']==basis)]
                    if not selected:continue
                    complete=all(row['complete'] for row in selected)
                    p_table.append({'suite':suite,'answer_basis':basis,'primary_p':primary,
                        'source_panels':len(selected),'complete':complete,
                        'planned_tasks':sum(r['planned_tasks'] for r in selected),
                        'completed_tasks':sum(r['completed_tasks'] for r in selected),
                        'independent_groups':len({(k[0],k[-1]) for k in self.groups if k[3]==suite and k[1]==primary and (basis=='all_task_goals' or k[2]==basis)}),
                        'successes':sum(r['successes'] for r in selected),
                        'metrics':{name:{'value':mean([r['metrics'][name]['value'] for r in selected]) if complete else None,
                            'completed_only_provisional_value':mean([r['metrics'][name]['completed_only_provisional_value'] for r in selected]),
                            'eligible_completed_tasks':sum(r['metrics'][name]['eligible_completed_tasks'] for r in selected),
                            'eligible_groups':sum(r['metrics'][name]['eligible_groups'] for r in selected),
                            'pooled_numerator':sum(r['metrics'][name]['pooled_numerator'] for r in selected),
                            'pooled_denominator':sum(r['metrics'][name]['pooled_denominator'] for r in selected)} for name in METRICS},
                        'task_success':mean([row['metrics']['task_success']['value'] for row in selected]) if complete else None,
                        'milestone_progress':mean([row['metrics']['milestone_progress']['value'] for row in selected]) if complete else None,
                        'aggregation':'equal source-panel macro after within-case-group aggregation'})
        return {'model':self.model,'mode':self.mode,'score_revisions':self.score_revisions,'planned_tasks':len(self.planned),'observed_result_records':len(self.seen),
            'independent_groups':len({(k[0],k[-1]) for k in self.groups}),
            'independent_groups_by_panel':{suite+'/'+basis:len({(k[0],k[-1]) for k in self.groups if k[3]==suite and (basis=='all_task_goals' or k[2]==basis)})
                for suite in ['agent','component'] for basis in ['all_task_goals','deterministic_record','source_reference','domain_validated']},
            'completed_tasks':sum(g['completed'] for g in self.groups.values()),
            'complete':sum(g['completed'] for g in self.groups.values())==len(self.planned),
            'panels':rows,'plant_success_and_progress':p_table,
            'milestone_long_table':[{'source':k[0],'primary_p':k[1],'template':k[2],'milestone_id':k[3],
                'pass_count':self.milestone_passes[k],'eligible_completed_count':n} for k,n in sorted(self.milestones.items())],
            'sampling_reliability':None,'sampling_reliability_reason':'One registered trajectory per task; no repeated-run reliability estimate',
            'risk':None,'risk_reason':'No independently adjudicated risk rubric',
            'comparison_note':'Compare query efficiency on common successful tasks when comparing systems; raw success-conditioned means may have different case sets'}


class EvaluationAggregates:
    """Full-catalog coverage plus separate development and held-out reports."""
    def __init__(self,planned,*,model,mode='model_run'):
        self.planned={row['id']:row for row in planned}
        self.overall=Aggregate(planned,model=model,mode=mode)
        self.splits={split:Aggregate([row for row in planned if row['split']==split],model=model,mode=mode)
                     for split in sorted({row['split'] for row in planned})}

    def add(self,ident,status,score=None):
        self.overall.add(ident,status,score)
        self.splits[self.planned[ident]['split']].add(ident,status,score)

    def report(self):
        report=self.overall.report()
        report['evaluation_scope']='all_catalog'
        report['scope_note']='Includes development-exposed tasks; use split reports for project-held-out comparisons. No public-source pretraining-unseen claim.'
        report['split_reports']=[{'evaluation_scope':split,**aggregator.report()} for split,aggregator in self.splits.items()]
        return report
