from types import SimpleNamespace
from copy import deepcopy
import pytest
from domains.theraskill.theraskill_simulation.training import SQLiteTrainingRepository,TrainingCourse,TrainingAssignment
from domains.theraskill.theraskill_simulation.learning import LearningService


class Views:
    def __init__(self):self.data={}
    def get_snapshot(self,sid):
        return SimpleNamespace(public_view=SimpleNamespace(model_dump=lambda **k:deepcopy(self.data[sid])))


def setup(tmp_path, template_id=None, with_behaviors=False):
    repo=SQLiteTrainingRepository(tmp_path/'training.sqlite');views=Views()
    objective={'id':'empathy','description':'Validation'}
    if with_behaviors:
        objective.update({'observable_behaviors':['Reflect emotion','Check understanding'],
                          'observation_statuses':['observed','not_observed','not_applicable']})
    course=TrainingCourse(course_id='c',title='Course',scenario_ids=['s1','s2'],learning_objectives=[objective],template_id=template_id)
    repo.add_course(course)
    repo.add_assignment(TrainingAssignment(assignment_id='a',title='Practice',scenario_ids=['s1','s2'],assignee='learner-a',course_id='c'))
    for sid,scenario in [('p1','s1'),('p2','s2')]:
        views.data[sid]={'session_id':sid,'mode':'human_therapist_vs_patient','scenario':{'scenario_id':scenario,'difficulty_band':'standard'},'status':'completed',
            'transcript':[{'role':'patient','text':'Synthetic concern'},{'role':'therapist','text':'Synthetic reflection'}],
            'review':{'status':'complete','gate_status':'pass'},'progress':{}}
    return LearningService(repo,views),views


def test_course_completion_requires_each_scenario_and_instructor_review(tmp_path):
    learning,_=setup(tmp_path)
    assert learning.progress('a')['status']=='assigned'
    a=learning.link('a','p1');learning.link('a','p1')
    assert len(learning.attempts('a'))==1
    assert learning.progress('a')['completion']['completed']==0
    learning.save_reflection(a['id'], 'I checked the client goal before clarifying.')
    learning.review(a['id'],'approved',{'empathy':3},'Fixture review','instructor')
    assert learning.progress('a')['completion']['completed']==1
    assert learning.progress('a')['attempts'][0]['self_reflection'].startswith('I checked')
    second=learning.link('a','p2');learning.review(second['id'],'approved',{'empathy':4},'Fixture review','instructor')
    result=learning.progress('a');assert result['status']=='completed'
    assert result['objectives'][0]['change']==1
    assert learning.report('different-learner')['assignments']==[]
    assert learning.progress('a')['next_step']['kind']=='review'


def test_learner_reflection_is_saved_on_attempt_and_visible_to_instructor(tmp_path):
    learning,_=setup(tmp_path)
    attempt=learning.link('a','p1')
    learning.save_reflection(attempt['id'], 'I noticed I moved to advice too quickly.')
    progress=learning.progress('a')
    assert progress['attempts'][0]['self_reflection'].startswith('I noticed')
    with pytest.raises(ValueError, match='too long'):
        learning.save_reflection(attempt['id'], 'x' * 2001)


def test_changed_session_invalidates_prior_approval(tmp_path):
    learning,views=setup(tmp_path);a=learning.link('a','p1')
    learning.review(a['id'],'approved',{'empathy':4},'Fixture','instructor')
    views.data['p1']['transcript'].append({'role':'therapist','text':'A later edit'})
    result=learning.progress('a')
    assert result['completion']['completed']==0 and not result['attempts'][0]['human_review_current']


def test_empty_and_research_sessions_do_not_count(tmp_path):
    learning,views=setup(tmp_path)
    views.data['p1']['transcript']=[{'role':'patient','text':'Only one side'}]
    a=learning.link('a','p1')
    with pytest.raises(ValueError,match='evaluable'):learning.review(a['id'],'approved',{'empathy':5},'Fixture','instructor')
    assert learning.progress('a')['completion']['completed']==0
    views.data['p2']['mode']='dual_agent_research'
    with pytest.raises(ValueError,match='learner practice'):learning.link('a','p2')


def test_review_validates_objectives_and_rejects_incomplete_practice(tmp_path):
    learning,views=setup(tmp_path);a=learning.link('a','p1')
    with pytest.raises(ValueError,match='every course objective'):learning.review(a['id'],'approved',{},'Fixture','instructor')
    with pytest.raises(ValueError,match='objective IDs'):learning.review(a['id'],'approved',{'wrong':4},'Fixture','instructor')
    views.data['p1']['review']['status']='not_evaluable'
    with pytest.raises(ValueError,match='evaluable'):learning.review(a['id'],'approved',{'empathy':4},'Fixture','instructor')


def test_same_objective_id_in_different_courses_is_not_a_learning_trend(tmp_path):
    learning,views=setup(tmp_path)
    learning.repo.add_course(TrainingCourse(course_id='other',title='Different course',scenario_ids=['s2'],learning_objectives=[{'id':'empathy','description':'Different rubric'}]))
    learning.repo.add_assignment(TrainingAssignment(assignment_id='other-a',title='Other',scenario_ids=['s2'],assignee='learner-a',course_id='other'))
    a=learning.link('a','p1');learning.review(a['id'],'needs_practice',{'empathy':2},'Fixture','instructor')
    a=learning.link('other-a','p2');learning.review(a['id'],'needs_practice',{'empathy':4},'Fixture','instructor')
    trends=learning.report('learner-a')['trends']
    assert len(trends)==2 and all(t['change'] is None for t in trends)


def test_instructor_overview_does_not_merge_different_learners(tmp_path):
    learning,views=setup(tmp_path)
    learning.repo.add_assignment(TrainingAssignment(assignment_id='other-learner',title='Practice',scenario_ids=['s2'],assignee='learner-b',course_id='c'))
    a=learning.link('a','p1');learning.review(a['id'],'needs_practice',{'empathy':2},'Learner A observation','instructor')
    b=learning.link('other-learner','p2');learning.review(b['id'],'needs_practice',{'empathy':4},'Learner B observation','instructor')
    report=learning.report()
    assert len(report['trends'])==2
    assert {row['assignee'] for row in report['trends']}=={'learner-a','learner-b'}
    assert all(row['count']==1 and row['change'] is None for row in report['trends'])
    assert report['recommendations'][0]['assignee']=='learner-a'


def test_high_scores_do_not_imply_independent_practice(tmp_path):
    learning,_=setup(tmp_path)
    for sid in ['p1','p2']:
        attempt=learning.link('a',sid)
        learning.review(attempt['id'],'approved',{'empathy':5},'Reviewed with hints','instructor')
    progress=learning.progress('a')
    assert progress['status']=='completed'
    assert progress['pedagogical_progress']['completed_competencies']==0
    assert progress['pedagogical_progress']['competencies'][0]['independent_count']==0


def test_unrated_safety_issue_remains_visible_until_reassessment(tmp_path):
    learning,_=setup(tmp_path)
    attempt=learning.link('a','p1')
    learning.review(attempt['id'],'needs_practice',{},'Needs safety review','instructor',safety_issue=True)
    state=learning.progress('a')['pedagogical_progress']
    assert state['status']=='blocked_safety'
    assert state['pending_safety_attempt_ids']==[attempt['id']]
    learning.review(attempt['id'],'needs_practice',{'empathy':3},'Issue resolved after review','instructor',safety_issue=False)
    state=learning.progress('a')['pedagogical_progress']
    assert state['status']!='blocked_safety'
    assert state['pending_safety_attempt_ids']==[]
    assert len(learning.reviews(attempt['id']))==2


def test_progress_uses_course_template_observation_policy(tmp_path):
    learning,_=setup(tmp_path, template_id='foundation_first_session')
    for sid in ['p1','p2']:
        attempt=learning.link('a',sid)
        learning.review(attempt['id'],'approved',{'empathy':5},'Independent observation','instructor',
                        performance_level='independent',transfer_context=f'Context {sid}')
    state=learning.progress('a')['pedagogical_progress']
    assert state['policy']['minimum_attempts']==3
    assert state['completed_competencies']==0


def test_review_records_behavior_observations_with_optional_score_and_note(tmp_path):
    learning,_=setup(tmp_path, with_behaviors=True)
    attempt=learning.link('a','p1')
    result=learning.review(attempt['id'],'needs_practice',{'empathy':3},'Behavior-level review','instructor',
        behavior_observations={'empathy': [
            {'behavior':'Reflect emotion','status':'observed','score':4,'note':'Named the feeling before asking a question.'},
            {'behavior':'Check understanding','status':'not_observed','note':'No opportunity in this short exchange.'},
        ]})
    assert result['behavior_observations']['empathy'][0]['score']==4.0
    assert result['behavior_observations']['empathy'][1]['status']=='not_observed'
    assert learning.reviews(attempt['id'])[0]['behavior_observations']['empathy'][0]['note'].startswith('Named')


def test_behavior_observation_rejects_unknown_or_scored_unobserved_behaviors(tmp_path):
    learning,_=setup(tmp_path, with_behaviors=True)
    attempt=learning.link('a','p1')
    with pytest.raises(ValueError, match='Unknown observable behavior'):
        learning.review(attempt['id'],'needs_practice',{},'Invalid behavior','instructor',
            behavior_observations={'empathy':[{'behavior':'Give advice','status':'observed','score':4}]})
    with pytest.raises(ValueError, match='Only observed behaviors'):
        learning.review(attempt['id'],'needs_practice',{},'Invalid score','instructor',
            behavior_observations={'empathy':[{'behavior':'Reflect emotion','status':'not_applicable','score':2}]})


def test_old_course_without_behaviors_keeps_objective_level_ratings(tmp_path):
    learning,_=setup(tmp_path)
    attempt=learning.link('a','p1')
    learning.review(attempt['id'],'needs_practice',{'empathy':3},'Legacy review','instructor')
    with pytest.raises(ValueError, match='no observable'):
        learning.review(attempt['id'],'needs_practice',{},'Behavior detail not supported','instructor',
            behavior_observations={'empathy':[{'behavior':'Reflect emotion','status':'observed'}]})


def test_reflection_is_timestamped_and_bound_to_the_session_it_describes(tmp_path):
    from domains.theraskill.theraskill_simulation.learning import session_fingerprint
    learning, views = setup(tmp_path)
    attempt = learning.link('a', 'p1')
    saved = learning.save_reflection(attempt['id'], 'I should check my understanding.')
    assert saved['self_reflection_updated_at'] == saved['updated_at']
    assert saved['self_reflection_session_sha256'] == session_fingerprint(views.data['p1'])
    assert learning.progress('a')['attempts'][0]['self_reflection_current']
    views.data['p1']['transcript'][1]['text'] = 'Edited after reflection'
    assert not learning.progress('a')['attempts'][0]['self_reflection_current']
    assert learning.attempts('a')[0]['self_reflection'] == 'I should check my understanding.'


@pytest.mark.parametrize('condition', ['performance_level', 'difficulty', 'rubric', 'missing_difficulty'])
def test_trends_do_not_subtract_scores_under_different_or_unknown_conditions(tmp_path, condition):
    import json
    learning, views = setup(tmp_path)
    if condition == 'missing_difficulty':
        for view in views.data.values():
            view['scenario'].pop('difficulty_band')
    first = learning.link('a', 'p1')
    learning.review(first['id'], 'needs_practice', {'empathy': 2}, 'First observation', 'instructor')
    second = learning.link('a', 'p2')
    if condition == 'difficulty':
        views.data['p2']['scenario']['difficulty_band'] = 'advanced'
    if condition == 'rubric':
        course = learning.repo.get_course('c').model_dump(mode='json')
        course['learning_objectives'][0]['description'] = 'Changed rubric'
        with learning.repo._connect() as db:
            db.execute('UPDATE training_courses SET payload_json=? WHERE course_id=?', (json.dumps(course), 'c'))
    learning.review(second['id'], 'needs_practice', {'empathy': 4}, 'Later observation', 'instructor',
                    performance_level='independent' if condition == 'performance_level' else 'guided')
    progress = learning.progress('a')
    objective = progress['objectives'][0]
    assert objective['count'] == 2
    assert objective['change'] is None
    assert objective['comparison_reason'] != 'same_recorded_conditions'
    assert 'performance_level' in objective['observations'][0]
    assert 'rubric_sha256' in objective['observations'][0]
    assert 'scenario_difficulty_band' in objective['observations'][0]
    trend = learning.report()['trends'][0]
    assert trend['change'] is None and trend['comparison_reason'] == objective['comparison_reason']


def test_matching_conditions_have_explicit_comparison_reason(tmp_path):
    learning, _ = setup(tmp_path)
    for sid, score in [('p1', 2), ('p2', 4)]:
        attempt = learning.link('a', sid)
        learning.review(attempt['id'], 'needs_practice', {'empathy': score}, 'Observed', 'instructor')
    trend = learning.report()['trends'][0]
    assert trend['change'] == 2
    assert trend['comparison_reason'] == 'same_recorded_conditions'


def test_course_rubric_edit_requires_new_approval_but_preserves_prior_rating_history(tmp_path):
    import json
    learning, _ = setup(tmp_path)
    attempt = learning.link('a', 'p1')
    learning.review(attempt['id'], 'approved', {'empathy': 4}, 'Observed', 'instructor')
    assert learning.progress('a')['completion']['completed'] == 1
    course = learning.repo.get_course('c').model_dump(mode='json')
    course['learning_objectives'][0]['description'] = 'Changed criterion'
    with learning.repo._connect() as db:
        db.execute('UPDATE training_courses SET payload_json=? WHERE course_id=?', (json.dumps(course), 'c'))
    progress = learning.progress('a')
    assert progress['completion']['completed'] == 0
    assert not progress['attempts'][0]['human_review_current']
    assert progress['objectives'][0]['latest'] == 4


@pytest.mark.parametrize('change', ['edit', 'unavailable'])
def test_pending_safety_survives_session_edits_or_missing_session(tmp_path, change):
    learning, views = setup(tmp_path)
    attempt = learning.link('a', 'p1')
    learning.review(attempt['id'], 'needs_practice', {}, 'Needs supervision', 'instructor', safety_issue=True)
    if change == 'edit':
        views.data['p1']['transcript'][1]['text'] = 'Edited later'
    else:
        del views.data['p1']
    progress = learning.progress('a')
    assert progress['status'] == 'blocked_safety'
    assert progress['pedagogical_progress']['pending_safety_attempt_ids'] == [attempt['id']]
    assert progress['next_step']['kind'] == 'supervision'


def test_another_passed_attempt_cannot_hide_unresolved_safety_on_same_scenario(tmp_path):
    learning, views = setup(tmp_path)
    for sid in ('p1', 'p2'):
        attempt = learning.link('a', sid)
        learning.review(attempt['id'], 'approved', {'empathy': 4}, 'Approved', 'instructor')
    assert learning.progress('a')['status'] == 'completed'
    views.data['p3'] = deepcopy(views.data['p1'])
    views.data['p3']['session_id'] = 'p3'
    flagged = learning.link('a', 'p3')
    learning.review(flagged['id'], 'needs_practice', {}, 'Unresolved issue', 'instructor', safety_issue=True)
    progress = learning.progress('a')
    assert progress['status'] == 'blocked_safety'
    assert progress['completed_scenarios'] == ['s2']
    assert progress['completion']['completed'] == 1
    learning.review(flagged['id'], 'needs_practice', {}, 'Reviewed and resolved', 'instructor', safety_issue=False)
    assert learning.progress('a')['status'] == 'completed'


def test_teacher_review_api_cannot_write_reflection_or_coerce_boolean_scores(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from domains.theraskill.theraskill_simulation.contracts import ActorRole
    from domains.theraskill.theraskill_simulation.learning_api import create_learning_router
    learning, views = setup(tmp_path, with_behaviors=True)
    attempt = learning.link('a', 'p1')
    learning.save_reflection(attempt['id'], 'Original learner reflection')
    before = learning.attempts('a')[0]
    app = FastAPI()
    app.include_router(create_learning_router(learning, views, lambda: ActorRole.INSTRUCTOR))
    body = {'decision': 'approved', 'ratings': {'empathy': 4}, 'comment': 'Observed', 'reviewer': 'Teacher'}
    with TestClient(app) as client:
        path = f"/learning/attempts/{attempt['id']}/review"
        assert client.post(path, json={**body, 'self_reflection': 'Teacher rewrite'}).status_code == 422
        assert client.post(path, json={**body, 'ratings': {'empathy': True}}).status_code == 422
        assert client.post(path, json={**body, 'ratings': {'empathy': '4'}}).status_code == 422
        assert client.post(path, json={**body, 'behavior_observations': {'empathy': [{'behavior': 'Reflect emotion', 'status': 'observed', 'score': True}]}}).status_code == 422
        assert learning.attempts('a')[0] == before
        assert learning.reviews(attempt['id']) == []
        assert client.post(path, json=body).status_code == 201
        assert learning.attempts('a')[0] == before
