from types import SimpleNamespace
from copy import deepcopy
import pytest
from domains.theraskill.theraskill_simulation.training import SQLiteTrainingRepository,TrainingCourse,TrainingAssignment
from domains.theraskill.theraskill_simulation.learning import LearningService


class Views:
    def __init__(self):self.data={}
    def get_snapshot(self,sid):
        return SimpleNamespace(public_view=SimpleNamespace(model_dump=lambda **k:deepcopy(self.data[sid])))


def setup(tmp_path):
    repo=SQLiteTrainingRepository(tmp_path/'training.sqlite');views=Views()
    course=TrainingCourse(course_id='c',title='Course',scenario_ids=['s1','s2'],learning_objectives=[{'id':'empathy','description':'Validation'}])
    repo.add_course(course)
    repo.add_assignment(TrainingAssignment(assignment_id='a',title='Practice',scenario_ids=['s1','s2'],assignee='learner-a',course_id='c'))
    for sid,scenario in [('p1','s1'),('p2','s2')]:
        views.data[sid]={'session_id':sid,'mode':'human_therapist_vs_patient','scenario':{'scenario_id':scenario},'status':'completed',
            'transcript':[{'role':'patient','text':'Synthetic concern'},{'role':'therapist','text':'Synthetic reflection'}],
            'review':{'status':'complete','gate_status':'pass'},'progress':{}}
    return LearningService(repo,views),views


def test_course_completion_requires_each_scenario_and_instructor_review(tmp_path):
    learning,_=setup(tmp_path)
    assert learning.progress('a')['status']=='assigned'
    a=learning.link('a','p1');learning.link('a','p1')
    assert len(learning.attempts('a'))==1
    assert learning.progress('a')['completion']['completed']==0
    learning.review(a['id'],'approved',{'empathy':3},'Fixture review','instructor')
    assert learning.progress('a')['completion']['completed']==1
    second=learning.link('a','p2');learning.review(second['id'],'approved',{'empathy':4},'Fixture review','instructor')
    result=learning.progress('a');assert result['status']=='completed'
    assert result['objectives'][0]['change']==1
    assert learning.report('different-learner')['assignments']==[]


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
