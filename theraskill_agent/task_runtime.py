"""Unified interactive jobs hosted by the existing workbench process."""
from __future__ import annotations
import copy
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import httpx
from .task_store import TaskStore, TaskConflict
from .skill_execution import next_skill_step, execute_tool, tool_requires_approval
from .text import redact_secrets

TERMINAL={'completed','cancelled'}


def domain_call(service, path, payload=None, *, method=None):
    from .local_services import get_services
    roles={'extraction':{'X-Extraction-Role':'researcher'},
           'contribution':{'X-Contribution-Role':'knowledge_curator','X-Contribution-Subject':'local-user'}}
    response=get_services().request(service, method or ('POST' if payload is not None else 'GET'),
                                    path, json=payload, headers=roles.get(service,{}))
    if not response.is_success:
        raise RuntimeError(f'{service} HTTP {response.status_code}: {response.text}')
    return response.json()


class TaskEngine:
    def __init__(self, store=None, *, call=domain_call, skill_step=next_skill_step, tool=execute_tool):
        self.store=store or TaskStore(); self.call=call; self.skill_step=skill_step; self.tool=tool
        self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='theraskill-task')
        self.lock=threading.RLock(); self.running=set(); self.closed=False
        for task in self.store.list(500):
            owner=task.get('worker_pid')
            live=False
            if owner:
                try:os.kill(int(owner),0);live=True
                except (OSError,ValueError):pass
            if task['status'] in {'running','queued','pending'} and not live:
                task.update(status='interrupted',error='Worker stopped. Resume from the saved checkpoint.')
                self.store.save(task)
                self.store.event(task['id'],'interrupted')

    def close(self):
        self.closed=True
        self.pool.shutdown(wait=False,cancel_futures=True)

    def create(self, kind, goal, language='zh', inputs=None):
        if kind not in {'skill','extraction','evaluation','skill_evolution'}:
            raise ValueError('Unknown task kind')
        if not goal.strip():
            raise ValueError('Task goal is required')
        if language not in {'zh','en'}:
            raise ValueError('Output language must be zh or en')
        task=self.store.create(kind,goal,language,inputs)
        self.launch(task['id'])
        return self.store.get(task['id'])

    def launch(self, task_id):
        with self.lock:
            if self.closed or task_id in self.running:
                return
            self.running.add(task_id)
        self.pool.submit(self._work,task_id)

    def action(self, task_id, action, values=None, revision=None):
        values=values or {}
        with self.lock:
            t=self.store.get(task_id)
            if revision is not None and t['revision']!=revision:
                raise TaskConflict('Task changed. Refresh before continuing.')
            if action=='cancel':
                if t['status']=='completed':
                    raise TaskConflict('A completed task cannot be cancelled')
                t.update(status='cancelled',interaction=None)
                cancellation_errors=[]
                for slot in ['trial_job','full_job']:
                    child=t['checkpoints'].get(slot)
                    if child and child.get('job_id') and child.get('status') in {'queued','running'}:
                        try:self.call('extraction','/v1/extraction/jobs/'+child['job_id']+'/cancel',{})
                        except Exception as exc:cancellation_errors.append(redact_secrets(str(exc)))
                for child in t['checkpoints'].get('resource_children',[]):
                    from .resource_jobs import cancel_job
                    try:cancel_job(child)
                    except Exception as exc:cancellation_errors.append(redact_secrets(str(exc)))
                if cancellation_errors:
                    t['checkpoints']['cancellation_errors']=cancellation_errors
                    t['error']='Task cancelled locally; child cancellation could not be confirmed. Check child job status.'
            elif action=='replan':
                if t['kind']!='extraction' or t['stage'] not in {'start','propose_plan','plan','trial','sample_review','full','extraction_review'}:
                    raise TaskConflict('This task cannot return to planning')
                if t['status']=='running':raise TaskConflict('Wait for the current stage or cancel its child extraction first')
                cp=t['checkpoints']
                cp.setdefault('previous_plans',[]).append({k:cp[k] for k in ['plan','trial_job','trial','full_job','full'] if k in cp})
                for key in ['plan','trial_job','trial','full_job','full']:cp.pop(key,None)
                cp['plan_revision']=int(cp.get('plan_revision',0))+1
                t.update(status='queued',stage='propose_plan' if cp.get('source') else 'start',interaction=None,error=None)
            elif action=='retry':
                if t['status'] not in {'failed','interrupted'}:
                    raise TaskConflict('Only failed or interrupted tasks can be retried')
                t.update(status='queued',error=None,attempt=t['attempt']+1)
            elif action=='respond':
                if t['status'] not in {'needs_input','needs_approval'}:
                    raise TaskConflict('Task is not waiting for input')
                stage=t['stage']; cp=t['checkpoints']
                if stage=='plan':
                    from plugins.theraskill.extraction_plan import validate_plan
                    plan=copy.deepcopy(cp['plan']); plan.update(values.get('plan') or {})
                    plan['source']={'path':t['inputs']['source_path']}
                    if not values.get('confirmed'):
                        raise ValueError('Confirm the extraction plan before running it')
                    validate_plan(plan)
                    cp['plan']=plan; t['stage']='trial'
                elif stage=='sample_review':
                    if not values.get('accepted'):
                        t['stage']='plan'; t.update(status='needs_approval',interaction={'kind':'plan','value':cp['plan']})
                        t=self.store.save(t); self.store.event(task_id,'sample_returned',values); return t
                    cp['sample_acceptance']=values; t['stage']='full'
                elif stage=='extraction_review':
                    if not values.get('accepted'):
                        raise ValueError('Review the extracted results before handoff')
                    cp['full_acceptance']=values; t['stage']='handoff'
                elif stage=='governance':
                    release_id=str(values.get('release_id') or '').strip()
                    if not release_id:
                        raise ValueError('A promoted knowledge release ID is required')
                    release=self.call('contribution','/v1/releases/'+release_id)
                    expected=set(cp['handoff']['candidate_ids'])
                    if release['status']!='promoted' or not expected.issubset(set(release['candidate_ids'])):
                        raise ValueError('Release must be promoted and contain all candidates from this task')
                    cp['release']=release
                    cp['base_knowledge']=values.get('base_knowledge','')
                    cp['index_mode']=values.get('index_mode','lexical')
                    if cp['index_mode'] not in {'lexical','hybrid'}:
                        raise ValueError('Index mode must be lexical or hybrid')
                    t['stage']='build_index'
                elif stage=='activation':
                    from .behavior_eval import verify_gate
                    if not values.get('confirmed'):
                        raise ValueError('Confirm activation after reviewing the evaluation')
                    verify_gate(values.get('report_id',''), {'index_id':cp['index']['index_id']})
                    cp['activation_report']=values['report_id']; t['stage']='activate'
                elif stage=='tool_approval':
                    if not values.get('approved'):
                        cp.setdefault('skill_history',[]).append({'user_response':{'tool_approval':'denied','comment':values.get('comment','')}})
                        cp.pop('pending_tool',None)
                    else:
                        cp['pending_tool']['approved']=True
                    t['stage']='skill'
                elif stage=='skill_question':
                    cp.setdefault('skill_history',[]).append({'user_response':values})
                    t['stage']='skill'
                else:
                    raise ValueError('Unknown interaction stage')
                t.update(status='queued',interaction=None,error=None)
            else:
                raise ValueError('Unknown task action')
            t=self.store.save(t)
            self.store.event(task_id,action,values)
        if t['status']=='queued':
            self.launch(task_id)
        return t

    def _checkpoint(self,t,key,result,stage=None):
        t['checkpoints'][key]=result
        if stage:t['stage']=stage
        t=self.store.save(t)
        self.store.event(t['id'],'checkpoint',{'name':key,'stage':t['stage']})
        return t

    def _wait(self,t,stage,kind,value=None):
        t.update(stage=stage,status='needs_approval' if kind in {'plan','activation','sample_review','tool_approval'} else 'needs_input',
                 interaction={'kind':kind,'value':value or {}})
        self.store.save(t); self.store.event(t['id'],'waiting',{'stage':stage,'kind':kind})

    def _child(self,t,name,limit):
        cp=t['checkpoints']; slot=name+'_job'
        if slot not in cp:
            job=self.call('extraction','/v1/extraction/jobs',{'plan':cp['plan'],'limit':limit,'request_id':t['id']+'_'+str(cp.get('plan_revision',0))+'_'+name})
            t=self._checkpoint(t,slot,job)
        job=cp.get(slot) or t['checkpoints'][slot]
        job=self.call('extraction','/v1/extraction/jobs/'+job['job_id'])
        if job['status'] in {'failed','partial','cancelled'}:
            if t['attempt']>t['checkpoints'][slot].get('_task_attempt',0) and job['status'] in {'failed','partial'}:
                job=self.call('extraction','/v1/extraction/jobs/'+job['job_id']+'/retry',{})
                job['_task_attempt']=t['attempt']; t=self._checkpoint(t,slot,job)
            else:
                raise RuntimeError(f"Extraction {name}: {job.get('error') or job['status']}. Retry this task to resume unfinished records.")
        prior=t['checkpoints'][slot]
        if prior.get('status')!=job['status'] or prior.get('progress')!=job.get('progress'):
            t=self._checkpoint(t,slot,{**job,'_task_attempt':t['attempt']})
        if job['status']=='completed':
            preview=self.call('extraction','/v1/extraction/jobs/'+job['job_id']+'/preview')
            t=self._checkpoint(t,name,{'job':job,'preview':preview})
            return t,True
        return t,False

    def _step(self,t):
        cp=t['checkpoints']
        if t['kind']=='skill_evolution':
            from .skills import SkillManager, _set_frontmatter_name
            from .skill_execution import model_json
            from .paths import data_root
            import shutil
            manager=SkillManager();record=manager.inspect(t['inputs']['skill'])
            original=manager.resolve(t['inputs']['skill']).read_text(encoding='utf-8')
            proposal,provenance=model_json('Propose an improved SKILL.md for the requested goal. Preserve the domain scope, evidence requirements and safety boundaries. Do not invent tools. Return JSON {"skill_md":"complete markdown including YAML frontmatter","changes":[],"evaluation_focus":[]}.',{'goal':t['goal'],'original_skill':original,'output_language':t['language']})
            markdown=proposal.get('skill_md')
            if not isinstance(markdown,str) or not markdown.strip():raise ValueError('Model did not return a complete skill proposal')
            folder=data_root()/'skill-drafts'/t['id'];folder.parent.mkdir(parents=True,exist_ok=True)
            if folder.exists():shutil.rmtree(folder)
            shutil.copytree(record.path,folder)
            (folder/'SKILL.md').write_text(_set_frontmatter_name(markdown,record.name),encoding='utf-8')
            t=self._checkpoint(t,'result',{'draft_path':str(folder),'original':record.to_dict(),'proposal':proposal,'provenance':provenance,'status':'draft_requires_behavior_comparison'})
            t['status']='completed';return self.store.save(t)
        if t['kind']=='skill':
            if cp.get('pending_tool'):
                pinned=cp.get('skill_snapshot')
                if pinned and pinned.get('bundle_checksum'):
                    from .skills import SkillManager
                    if SkillManager().inspect(pinned['path']).bundle_checksum!=pinned['bundle_checksum']:
                        raise ValueError('Skill or references changed before tool execution; create a new task')
                pending=cp['pending_tool']
                if pending.get('requires_approval') and not pending.get('approved'):
                    self._wait(t,'tool_approval','tool_approval',pending);return None
                decision=pending['decision']
                result=self.tool(decision['tool'],{**decision.get('arguments',{}),'_call_id':pending['call_id']},skill_path=pending['skill_path'])
                cp.setdefault('skill_history',[]).append({'tool':decision['tool'],'arguments':decision.get('arguments',{}),'result':result})
                if decision['tool']=='theraskill_resources' and isinstance(result,dict) and str(result.get('id','')).startswith('resource_'):
                    cp.setdefault('resource_children',[]).append(result['id'])
                cp.pop('pending_tool',None)
                t=self.store.save(t);self.store.event(t['id'],'tool_result',{'tool':decision['tool'],'result':result})
                return t
            if sum(1 for item in cp.get('skill_history',[]) if 'model' in item)>=int(t['inputs'].get('max_steps',24)):
                raise ValueError('Configured skill step budget reached; inspect the timeline before creating a follow-up task')
            step=self.skill_step(t)
            if 'selection' in step:
                t['inputs']['skill']=step['selection']['skill']
                return self._checkpoint(t,'selection',step,'skill')
            t['checkpoints']['skill_snapshot']=step['skill_snapshot']
            decision=step['decision']; history=cp.setdefault('skill_history',[])
            history.append({'model':decision,'provenance':step['provenance']})
            t=self.store.save(t)
            self.store.event(t['id'],'skill_decision',step)
            if decision['action']=='ask':
                self._wait(t,'skill_question','question',decision); return None
            if decision['action']=='finish':
                t=self._checkpoint(t,'result',decision['result'])
                t['status']='completed'; return self.store.save(t)
            import uuid
            t['checkpoints']['pending_tool']={'decision':decision,'skill_path':step['skill_snapshot']['path'],
                'call_id':t['id']+'_'+uuid.uuid4().hex,'requires_approval':tool_requires_approval(decision['tool'],decision.get('arguments',{}))}
            return self.store.save(t)
        if t['kind']=='evaluation':
            from .behavior_eval import run_suite
            result=run_suite(t['inputs'],run_id=t['id'],checkpoint=lambda key,val:self._evaluation_checkpoint(t['id'],key,val),
                             completed=cp.get('eval_cases',{}))
            t=self.store.get(t['id'])
            t=self._checkpoint(t,'result',result); t['status']='completed'; return self.store.save(t)
        stage=t['stage']
        if stage=='start':
            from .source_adapters import inspect_source,sample_source
            path=t['inputs']['source_path']
            info=inspect_source(path).to_dict()
            samples=[r.to_dict() for r in sample_source(path,5)]
            return self._checkpoint(t,'source',{'info':info,'samples':samples,'sample_limit':5},'propose_plan')
        if stage=='propose_plan':
            from plugins.theraskill.extraction_plan import plan_with_active_model
            plan=plan_with_active_model(t['goal'],cp['source']['samples'],source={'path':t['inputs']['source_path']},language=t['language'])
            t=self._checkpoint(t,'plan',plan)
            self._wait(t,'plan','plan',plan); return None
        if stage in {'trial','full'}:
            if stage=='full':
                from .source_adapters import inspect_source
                if inspect_source(t['inputs']['source_path']).to_dict()['fingerprint']!=cp['source']['info']['fingerprint']:
                    raise ValueError('Source changed since plan confirmation; create a new task')
            t,done=self._child(t,stage,int(t['inputs'].get('trial_limit',3)) if stage=='trial' else None)
            if done:
                self._wait(t,'sample_review' if stage=='trial' else 'extraction_review',
                           'sample_review' if stage=='trial' else 'extraction_review',t['checkpoints'][stage]); return None
            return t
        if stage=='handoff':
            result=self.call('extraction','/v1/extraction/jobs/'+cp['full']['job']['job_id']+'/handoff',{})
            if not result.get('candidate_ids'):
                raise ValueError('No eligible candidates were extracted; review the source and extraction plan')
            t=self._checkpoint(t,'handoff',result)
            self._wait(t,'governance','governance',{'candidate_ids':result['candidate_ids'],
                'href':'/theraskill/expert/?view=governance&candidate_id='+result['candidate_ids'][0]}); return None
        if stage=='build_index':
            from .paths import data_root
            from .resources import active_index
            from .indexing.common import digest
            from .indexing import build_index
            # A full managed index must retain the current base corpus.
            # The user supplies its JSONL because indexes do not contain raw source.
            base=Path(cp['base_knowledge']).expanduser()
            selected=active_index()
            if selected and (not base.is_file() or digest(base)!=selected[1]['input']['knowledge_sha256']):
                raise ValueError('Select the base knowledge JSONL matching the active index fingerprint')
            folder=data_root()/'tasks'/t['id'];folder.mkdir(parents=True,exist_ok=True)
            merged=folder/'knowledge.jsonl'
            import shutil
            with merged.open('wb') as out:
                if selected:
                    with base.open('rb') as src:shutil.copyfileobj(src,out)
                    out.write(b'\n')
                with Path(cp['release']['delta_units_path']).open('rb') as src:shutil.copyfileobj(src,out)
            index_id='workflow_'+t['id'].removeprefix('task_')
            index=build_index(merged,data_root()/'indexes'/index_id,mode=cp['index_mode'],resume=True,
                cancel=lambda:self.store.get(t['id'])['status']=='cancelled',
                progress=lambda value:self.store.event(t['id'],'index_progress',value))
            t=self._checkpoint(t,'index',{**index,'directory_id':index_id,'knowledge':str(merged)})
            self._wait(t,'activation','activation',{'index_id':index['index_id'],'directory_id':index_id}); return None
        if stage=='activate':
            from .behavior_eval import verify_gate
            from .resources import activate_index
            verify_gate(cp['activation_report'],{'index_id':cp['index']['index_id']})
            with self.lock:
                if self.store.get(t['id'])['status']=='cancelled':raise TaskConflict('Task cancelled')
                result=activate_index(cp['index']['directory_id'],knowledge=cp['index']['knowledge'],report_id=cp['activation_report'])
            t=self._checkpoint(t,'activation',result); t['status']='completed';return self.store.save(t)
        raise ValueError('Cannot run extraction stage '+stage)

    def _evaluation_checkpoint(self,task_id,key,value):
        with self.lock:
            t=self.store.get(task_id)
            if t['status']=='cancelled':raise TaskConflict('Task cancelled')
            t['checkpoints'].setdefault('eval_cases',{})[key]=value
            self.store.save(t);self.store.event(task_id,'evaluation_case',{'case_id':key,'status':value.get('status')})

    def _work(self,task_id):
        try:
            with self.lock:
                t=self.store.get(task_id)
                if t['status'] not in {'queued','pending'}:return
                t.update(status='running',worker_pid=os.getpid()); t=self.store.save(t)
            while not self.closed and t['status']=='running':
                current=self.store.get(task_id)
                if current['status']=='cancelled':return
                t=self._step(current)
                if t is None or t['status']!='running':return
                if t['kind']=='extraction' and t['stage'] in {'trial','full'}:time.sleep(2)
        except TaskConflict:
            pass
        except Exception as exc:
            with self.lock:
                t=self.store.get(task_id)
                if t['status']!='cancelled':
                    t.update(status='failed',error=redact_secrets(f'{type(exc).__name__}: {exc}'))
                    self.store.save(t);self.store.event(task_id,'failed',{'error':t['error'],'stage':t['stage']})
        finally:
            with self.lock:self.running.discard(task_id)
            if not self.closed and self.store.get(task_id)['status']=='queued':self.launch(task_id)
