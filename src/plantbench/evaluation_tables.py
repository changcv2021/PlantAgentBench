"""Four main tables plus one milestone figure per model/scope/basis/suite."""
import csv
import json
from pathlib import Path
from .aggregation import mean,AGENT_METRICS

def write_csv(path,rows):
    keys=list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=keys);writer.writeheader();writer.writerows(rows)

def render_tables(reports,output,*,figures=True):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    tables=[[],[],[],[]];breakdown=[]
    for report in reports:
        scopes=[report,*report.get('split_reports',[])]
        for scope in scopes:
            context={'model':scope['model'],'mode':scope.get('mode',report.get('mode','model_run')),
                'evaluation_scope':scope.get('evaluation_scope','all_catalog')}
            rows=scope['plant_success_and_progress']
            for suite,basis in sorted({(r['suite'],r['answer_basis']) for r in rows}):
                selected=[r for r in rows if (r['suite'],r['answer_basis'])==(suite,basis)]
                # Explicit missing P rows prevent an observed-only five-P macro.
                expanded=[]
                for p in ['P1','P2','P3','P4','P5']:
                    row=next((r for r in selected if r['primary_p']==p),None)
                    if row:expanded.append(row)
                    else:expanded.append({'suite':suite,'answer_basis':basis,'primary_p':p,'complete':True,
                        'planned_tasks':0,'completed_tasks':0,'independent_groups':0,'successes':0,
                        'task_success':None,'milestone_progress':None,'metrics':{}})
                base={**context,'suite':suite,'answer_basis':basis}
                for row in expanded:
                    common={**base,**{k:row[k] for k in ['primary_p','complete','planned_tasks','completed_tasks','independent_groups']}}
                    tables[0].append({**common,'task_success':row['task_success'],'success_count':row['successes'],
                        'pooled_denominator':row['completed_tasks'],'aggregation':'group then source macro'})
                    pm=row['metrics'].get('milestone_progress',{})
                    tables[1].append({**common,'milestone_progress':row['milestone_progress'],
                        'pooled_passed_milestones':pm.get('pooled_numerator',0),'pooled_required_milestones':pm.get('pooled_denominator',0),
                        'aggregation':'group then source macro; pooled counts diagnostic only'})
                    for index,names in [(2,AGENT_METRICS[:4]),(3,AGENT_METRICS[4:])]:
                        agent={**common}
                        for name in names:
                            m=row['metrics'].get(name,{})
                            agent[name]=m.get('value')
                            for key in ['eligible_completed_tasks','eligible_groups','pooled_numerator','pooled_denominator']:
                                agent[name+'_'+key]=m.get(key,0)
                        if index==2:
                            for name in ['content_all_required','evidence_requirements','report_completeness']:
                                agent[name]=row['metrics'].get(name,{}).get('value')
                            coverage=row['metrics'].get('content_coverage',{})
                            agent['content_coverage']=coverage.get('value')
                            agent['content_present_rules']=coverage.get('pooled_numerator',0)
                            agent['content_required_rules']=coverage.get('pooled_denominator',0)
                        else:agent['task_success']=row['task_success']
                        tables[index].append(agent)
                    for mid in ['M1','M2','M3']:
                        m=row['metrics'].get(mid,{})
                        breakdown.append({**common,'milestone':mid,'pass_rate':m.get('value'),
                            **{component+'_pass_rate':row['metrics'].get(mid+'_'+component,{}).get('value')
                               for component in ['content','evidence','stage']},
                            'passed_tasks':m.get('pooled_numerator',0),'eligible_tasks':m.get('pooled_denominator',0),
                            'eligible_groups':m.get('eligible_groups',0),
                            'coverage':m.get('pooled_denominator',0)/row['completed_tasks'] if row['completed_tasks'] else None})
                eligible=[r for r in expanded if r['planned_tasks']]
                complete=all(r['complete'] for r in eligible)
                for index,name in [(0,'task_success'),(1,'milestone_progress')]:
                    # This row is explicitly a covered-P macro; full five-P macro
                    # is NA unless all five plants task categories are present.
                    tables[index].append({**base,'primary_p':'Macro','complete':complete,
                        'planned_tasks':sum(r['planned_tasks'] for r in eligible),
                        'completed_tasks':sum(r['completed_tasks'] for r in eligible),
                        'independent_groups':scope['independent_groups_by_panel'][suite+'/'+basis],
                        name:mean([r[name] for r in eligible]) if complete else None,
                        'p_coverage':len(eligible),'five_p_macro':mean([r[name] for r in eligible]) if complete and len(eligible)==5 else None,
                        'aggregation':'equal covered-P macro; unique cross-P group count in evaluation_report.json'})
    filenames=['table1_task_success.csv','table2_milestone_progress.csv',
        'table3_execution_failure_diagnosis.csv','table4_tools_information_seeking.csv']
    for name,rows in zip(filenames,tables):write_csv(output/name,rows)
    write_csv(output/'milestone_breakdown.csv',breakdown)
    (output/'evaluation_report.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n')
    if figures:render_heatmaps(breakdown,output)
    return tables,breakdown

def render_heatmaps(rows,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    keys=['model','mode','evaluation_scope','suite','answer_basis']
    for key in sorted({tuple(r[k] for k in keys) for r in rows}):
        selected=[r for r in rows if tuple(r[k] for k in keys)==key]
        data=np.full((5,3),np.nan);labels={}
        for row in selected:
            i=int(row['primary_p'][1])-1;j=int(row['milestone'][1])-1
            value=row['pass_rate'];data[i,j]=np.nan if value is None else value
            labels[i,j]='N/A' if value is None else f'{value:.1%}\n{row["eligible_tasks"]}/{row["completed_tasks"]} eligible'
        fig,ax=plt.subplots(figsize=(7,5));cmap=plt.get_cmap('YlGnBu').copy();cmap.set_bad('#eeeeee')
        im=ax.imshow(data,vmin=0,vmax=1,cmap=cmap)
        ax.set(xticks=range(3),xticklabels=['M1','M2','M3'],yticks=range(5),yticklabels=['P1','P2','P3','P4','P5'])
        for (i,j),label in labels.items():ax.text(j,i,label,ha='center',va='center',fontsize=8,color='white' if data[i,j]>.65 else 'black')
        title=key[0].replace('_archived_structured','')
        if key[1]=='offline_protocol_replay':title+=' - OFFLINE TRACE REPLAY (no new inference)'
        ax.set_title(title+'\n'+' | '.join(key[2:]),fontsize=9)
        fig.colorbar(im,ax=ax,label='Necessary outcome pass rate')
        fig.tight_layout();name='milestones_'+'_'.join(key).replace('/','_')
        fig.savefig(Path(output)/(name+'.png'),dpi=180);fig.savefig(Path(output)/(name+'.svg'));plt.close(fig)
