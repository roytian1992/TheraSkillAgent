"""Learning UI regression with synthetic API responses; no live model calls.

Build the workbench before running. Screenshots and API payloads are retained in
pytest's temporary directory for failures and review.
"""
from copy import deepcopy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest


def test_course_draft_micro_feedback_and_isolated_reflection(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    assets = Path(__file__).parents[1] / "views/theraskill/workbench/dist"
    if not (assets / "index.html").is_file():
        pytest.skip("Build the workbench before running its browser regression")
    ART = tmp_path
    calls=[]; submissions=[]; jobs={}
    goal={'id':'empathy','description':'验证情绪并检查理解','observable_behaviors':['反映具体情绪','邀请修正'],'rubric':[{'level':'guided','label':'需要提示','description':'提示后反映感受'}]}
    card={'card_id':'card1','competency_id':'empathy','title':'理解情绪','why_it_matters':'先听见感受','when_to_use':'来访者表达担忧时','common_misuse':'立即给建议','knowledge_unit_ids':['unit1']}
    exercise={'practice_id':'micro1','competency_id':'empathy','title':'先回应感受','scenario_text':'来访者：同事最近总是打断我，我开始怀疑自己。','prompt':'请写一句反映感受的回应。','target_behavior':'反映具体情绪','hint':'从具体事件中的感受开始。','reflection_prompt':'你为什么选择这句回应？','example_response':'被频繁打断让你感到委屈，也开始怀疑自己。','example_discussion':'这句回应连接事件与感受。','knowledge_unit_ids':['unit1']}
    blueprint={'template_id':'foundation_first_session','title':'首次会谈基础','description':'练习倾听与验证情绪','objectives':[goal],'competency_ids':['empathy'],'scaffolding':['示范','练习'],'knowledge_cards':[card],'micro_practices':[exercise],'knowledge_references':[{'unit_id':'unit1','quote':'检查来访者是否被理解。','label':'情绪验证'}],'retrieval':{'status':'grounded'},'scenario_constraints':{'count':3},'generation_payload':{'course_title':'stale title','brief':'stale old goal','count':3,'scenario_family':'supportive_exploration','difficulty_band':'low','risk_band':'low','language':'zh'}}
    course={'course_id':'course1','title':'首次会谈基础','description':'练习倾听与验证情绪','learning_objectives':[goal],'scenario_ids':['s1'],'skill_tags':['empathy'],'completion_review_gate':'pass','learning_units':{'knowledge_cards':[card],'micro_practices':[exercise]},'knowledge_references':blueprint['knowledge_references']}
    scenarios=[{'scenario_id':'s1','scenario_brief':'与同事相处的困扰','difficulty_band':'low','generated':False}]
    assignment={'assignment_id':'assignment1','title':'第一周倾听练习','assignee':'学习小组 A','scenario_ids':['s1'],'skill_tags':['empathy']}
    attempts=[{'id':'attempt1','scenario_id':'s1','session_id':'session1','status':'completed','practice_href':'#session1','review':{'status':'complete','summary':{'finding':'FIRST_PRIVATE_FEEDBACK'}},'self_reflection':'','human_review':{'reviewer':'教师 A','decision':'needs_practice','comment':'邀请来访者修正你的理解','behavior_observations':{'empathy':[{'behavior':'邀请修正','status':'not_observed','note':'尝试增加“我理解得对吗？”'}]}},'human_review_current':True,'machine_passed':True},{'id':'attempt2','scenario_id':'s1','session_id':'session2','status':'completed','practice_href':'#session2','review':{'status':'complete','summary':{'finding':'SECOND_PRIVATE_FEEDBACK'}},'self_reflection':'','machine_passed':True}]
    result={'schema_version':'theraskill_learning_judge_v1','status':'complete','assessment_kind':'formative','evidence_mode':'knowledge_grounded','objectives':[{'objective_id':'empathy','observation':'observed','rating':4,'rationale':'回应具体反映了担忧','transcript_evidence':[{'turn_id':'response','quote':'听起来你很委屈。'}],'knowledge_evidence':[{'unit_id':'unit1','quote':'检查来访者是否被理解。'}],'behaviors':[]}],'next_practice':['下一次邀请来访者修正理解']}
    def handle(route):
        path=route.request.url.split('/api/simulation')[-1].split('?')[0]; method=route.request.method; body=route.request.post_data_json if method=='POST' else None
        if method=='POST':calls.append({'path':path,'body':body})
        data={}
        if path=='/learning/report':data={'assignments':[{'assignment':assignment,'course':course,'status':'in_progress','completion':{'completed':0,'required':1},'completed_scenarios':[],'attempts':attempts,'pedagogical_progress':{'competencies':[]}}],'trends':[]}
        elif path=='/scenarios':data={'scenarios':scenarios}
        elif path=='/courses' and method=='GET':data={'courses':[course]}
        elif path=='/courses' and method=='POST':data={'course_id':'saved-course',**body}
        elif path=='/courses/blueprint':
            data=deepcopy(blueprint)
            if body.get('language')=='en':
                data['title']='First-session foundations';data['description']='Practise reflective listening';data['objectives'][0]['description']='Reflect emotion and check understanding';data['knowledge_cards'][0]['title']='Understand emotions'
        elif path=='/scenarios/generate-batch/jobs':data={'job_id':'batch_fixture'}
        elif path=='/assignments/assignment1/micro-practices':
            if method=='POST':
                data={'submission_id':'sub1','assignment_id':'assignment1','created_at':'2026-10-20T01:00:00Z','exercise_snapshot':exercise,'human_reviews':[],'latest_judge_job':None,**body};submissions.append(data)
            else:data={'exercises':[exercise],'submissions':submissions,'attempt_judge_jobs':{}}
        elif path=='/learning/submissions/sub1/judge':
            data={'job_id':'job1','status':'running','progress':{'stage':'review'},'source_current':True};jobs['job1']=data
        elif path=='/learning/judge-jobs/job1/retry':
            data={'job_id':'job1','status':'running','source_current':True};jobs['job1']=data
        elif path=='/learning/judge-jobs/job1':
            data={'job_id':'job1','status':'completed','result':result,'source_current':True};jobs['job1']=data
            if submissions:submissions[0]['latest_judge_job']=data
        elif path.endswith('/reflection'):
            key=path.split('/')[-2];next(item for item in attempts if item['id']==key)['self_reflection']=body['reflection'];data={'saved':True}
        route.fulfill(status=200,content_type='application/json',body=json.dumps(data,ensure_ascii=False))
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(assets)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with playwright.sync_playwright() as p:
            try:
                browser=p.chromium.launch(headless=True)
            except playwright.Error as exc:
                pytest.skip(f"Playwright Chromium is unavailable: {exc}")
            page=browser.new_page(viewport={'width':1360,'height':1000}); errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.route('**/api/**',handle)
            page.goto(base + '/?workspace=learning')
            page.get_by_role('button',name='创建课程起点',exact=True).click()
            page.get_by_label('希望学员做到什么',exact=True).fill('UPDATED GOAL: 先反映情绪再邀请来访者修正')
            page.get_by_label('为什么练习这个技能',exact=True).fill('UPDATED KNOWLEDGE CARD')
            page.get_by_label('与同事相处的困扰',exact=True).check()
            page.get_by_role('button',name='保存课程',exact=True).click()
            page.get_by_text('课程已保存，可分配给学员。',exact=True).wait_for()
            saved=next(item['body'] for item in calls if item['path']=='/courses')
            assert saved['learning_objectives'][0]['description'].startswith('UPDATED GOAL')
            assert saved['learning_units']['knowledge_cards'][0]['why_it_matters']=='UPDATED KNOWLEDGE CARD'
            page.reload();page.get_by_label('希望学员做到什么',exact=True).wait_for()
            assert page.get_by_label('希望学员做到什么',exact=True).input_value().startswith('UPDATED GOAL')
            page.screenshot(path=str(ART/'teacher-design.png'),full_page=True)
            page.get_by_role('button',name='按当前目标生成病例草案',exact=True).click()
            page.wait_for_url('**/theraskill/simulation/?batch_job=batch_fixture')
            generated=next(item['body'] for item in calls if item['path']=='/scenarios/generate-batch/jobs')
            assert generated['brief'].startswith('UPDATED GOAL') and generated['learning_units']['knowledge_cards'][0]['why_it_matters']=='UPDATED KNOWLEDGE CARD'
            page.goto(base + '/?workspace=learning')
            page.get_by_label('学员标签（留空查看全部）',exact=True).fill('学习小组 A')
            page.get_by_label('当前视角',exact=True).select_option('human_therapist')
            page.get_by_label('你的回应',exact=True).fill('听起来你很委屈。')
            page.get_by_label('你为什么选择这句回应？',exact=True).fill('我先回应对方的情绪。')
            page.get_by_text('需要一点提示',exact=True).click()
            page.get_by_role('button',name='提交微练习',exact=True).click()
            page.get_by_role('button',name='获取练习反馈',exact=True).click()
            page.get_by_text('回应具体反映了担忧',exact=True).wait_for(timeout=10000)
            assert submissions[0]['hint_used'] is True
            page.reload();page.get_by_text('回应具体反映了担忧',exact=True).wait_for()
            first,second=page.locator('.learning-attempt').all()
            first.get_by_label('看反馈之前，先回想一下',exact=True).fill('ONLY FIRST ATTEMPT REFLECTION')
            assert second.get_by_label('看反馈之前，先回想一下',exact=True).input_value()==''
            assert first.get_by_text('邀请来访者修正你的理解',exact=True).count()==0
            first.get_by_role('button',name='保存反思并查看反馈',exact=True).click()
            first.get_by_text('邀请来访者修正你的理解',exact=True).wait_for()
            assert second.get_by_text('SECOND_PRIVATE_FEEDBACK',exact=False).count()==0
            page.screenshot(path=str(ART/'learner-feedback.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(ART/'learner-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1360,'height':1000})
            page.get_by_label('当前视角',exact=True).select_option('instructor')
            page.get_by_role('button',name='学习与练习',exact=True).click()
            first,second=page.locator('.learning-attempt').all()
            first.get_by_role('button',name='教师评价',exact=True).click()
            first.get_by_label('复核人',exact=True).fill('SHOULD RESET')
            first.get_by_label('完成方式',exact=True).select_option('independent')
            first.get_by_label('需要督导处理的安全问题',exact=True).check()
            second.get_by_role('button',name='教师评价',exact=True).click()
            assert second.get_by_label('复核人',exact=True).input_value()==''
            assert second.get_by_label('完成方式',exact=True).input_value()=='guided'
            assert not second.get_by_label('需要督导处理的安全问题',exact=True).is_checked()
            submissions[0]['latest_judge_job']={'job_id':'job1','status':'failed','error':'Fixture interruption','retry_allowed':True,'source_current':True}
            page.reload();page.get_by_role('button',name='学习与练习',exact=True).click()
            page.get_by_role('button',name='重试未完成的反馈',exact=True).click()
            page.get_by_text('回应具体反映了担忧',exact=True).wait_for()
            page.get_by_role('button',name='EN',exact=True).click()
            page.get_by_role('button',name='Create course starter',exact=True).click()
            page.get_by_label('What should the learner be able to do?',exact=True).wait_for()
            assert page.get_by_label('Course title',exact=True).input_value()=='First-session foundations'
            page.screenshot(path=str(ART/'teacher-english.png'),full_page=True)
            assert not errors,errors
            (ART/'results.json').write_text(json.dumps({'status':'passed','checks':['edited course saved exactly','draft survives reload','generation uses latest goals and cards','learner micro-practice saved with hint/reflection','judge polls and restores on refresh','reflection isolated per attempt','feedback hidden until reflection/skip','teacher fields reset per attempt','390px has no page overflow','failed judge retries from persisted job','English UI and starter'],'page_errors':errors,'calls':calls},ensure_ascii=False,indent=2))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
