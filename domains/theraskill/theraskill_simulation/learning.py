"""Course objectives, practice attempts, instructor review and learning trends."""
from __future__ import annotations
import hashlib
import json
import uuid
from datetime import datetime,timezone


def stamp():return datetime.now(timezone.utc).isoformat()

def session_fingerprint(view):
    return hashlib.sha256(json.dumps({k:view.get(k) for k in ['session_id','mode','status','transcript','review','progress']},sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


class LearningService:
    def __init__(self,repository,session_service):
        self.repo=repository;self.sessions=session_service
        with self.repo._connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS learning_attempts (
              id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL, session_id TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS learning_reviews (
              id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);''')

    def assignment(self,assignment_id):
        row=next((a for a in self.repo.list_assignments() if a.assignment_id==assignment_id),None)
        if not row:raise KeyError(assignment_id)
        return row

    def attempts(self,assignment_id):
        with self.repo._connect() as db:
            rows=db.execute('SELECT payload FROM learning_attempts WHERE assignment_id=? ORDER BY created_at',(assignment_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def link(self,assignment_id,session_id):
        assignment=self.assignment(assignment_id)
        snapshot=self.sessions.get_snapshot(session_id)
        view=snapshot.public_view.model_dump(mode='json')
        if view['scenario']['scenario_id'] not in assignment.scenario_ids:
            raise ValueError('Session scenario is not part of this assignment')
        if view['mode'] in {'review_only','dual_agent_research'}:
            raise ValueError('Only learner practice sessions count toward assignments')
        with self.repo._connect() as db:
            old=db.execute('SELECT payload FROM learning_attempts WHERE session_id=?',(session_id,)).fetchone()
            if old:
                item=json.loads(old[0])
                if item['assignment_id']!=assignment_id:raise ValueError('Session is already linked to another assignment')
                return item
            item={'id':'attempt_'+uuid.uuid4().hex,'assignment_id':assignment_id,'session_id':session_id,
                  'scenario_id':view['scenario']['scenario_id'],'created_at':stamp()}
            db.execute('INSERT INTO learning_attempts VALUES (?,?,?,?,?)',(item['id'],assignment_id,session_id,item['created_at'],json.dumps(item)))
        return item

    def reviews(self,attempt_id):
        with self.repo._connect() as db:
            rows=db.execute('SELECT payload FROM learning_reviews WHERE attempt_id=? ORDER BY created_at',(attempt_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def review(self,attempt_id,decision,ratings,comment,reviewer):
        if decision not in {'approved','needs_practice','rejected'} or not comment.strip() or not reviewer.strip():
            raise ValueError('Provide a decision, explanation and reviewer')
        with self.repo._connect() as db:
            row=db.execute('SELECT payload FROM learning_attempts WHERE id=?',(attempt_id,)).fetchone()
        if not row:raise KeyError(attempt_id)
        attempt=json.loads(row[0]);assignment=self.assignment(attempt['assignment_id'])
        course=self.repo.get_course(assignment.course_id) if assignment.course_id else None
        objectives={o['id'] for o in course.learning_objectives} if course else set(assignment.skill_tags)
        if objectives and (set(ratings)-objectives):raise ValueError('Ratings must use course objective IDs')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not 1<=v<=5 for v in ratings.values()):
            raise ValueError('Objective ratings must be finite numbers from 1 to 5')
        view=self.sessions.get_snapshot(attempt['session_id']).public_view.model_dump(mode='json')
        review=view.get('review') or {}
        roles={t.get('role') for t in view.get('transcript',[]) if str(t.get('text','')).strip()}
        required=assignment.required_review_gate or (course.completion_review_gate if course else 'pass')
        valid_dialogue=bool(roles & {'patient','human_patient','patient_agent'}) and bool(roles & {'therapist','human_therapist','therapist_agent'})
        if decision=='approved' and (view['status']!='completed' or review.get('status')!='complete' or review.get('gate_status')!=required or not valid_dialogue):
            raise ValueError('Only completed, evaluable practice can receive instructor approval')
        if decision=='approved' and objectives-set(ratings):raise ValueError('Rate every course objective before approval')
        item={'id':'learning_review_'+uuid.uuid4().hex,'attempt_id':attempt_id,'decision':decision,'ratings':ratings,
              'comment':comment,'reviewer':reviewer,'created_at':stamp(),'session_sha256':session_fingerprint(view)}
        with self.repo._connect() as db:
            db.execute('INSERT INTO learning_reviews VALUES (?,?,?,?)',(item['id'],attempt_id,item['created_at'],json.dumps(item,ensure_ascii=False)))
        return item

    def progress(self,assignment_id):
        assignment=self.assignment(assignment_id)
        course=self.repo.get_course(assignment.course_id) if assignment.course_id else None
        required=assignment.required_review_gate or (course.completion_review_gate if course else 'pass')
        require_human=course.require_instructor_review if course else True
        items=[];completed=set();trends={}
        for attempt in self.attempts(assignment_id):
            result=dict(attempt)
            try:view=self.sessions.get_snapshot(attempt['session_id']).public_view.model_dump(mode='json')
            except Exception as exc:
                result.update(status='unavailable',error=str(exc),completed=False);items.append(result);continue
            review=view.get('review') or {};history=self.reviews(attempt['id']);human=history[-1] if history else None
            human_current=bool(human and human['session_sha256']==session_fingerprint(view))
            roles={str(turn['role']) for turn in view['transcript'] if str(turn.get('text','')).strip()}
            both=bool(roles & {'patient','human_patient','patient_agent'}) and bool(roles & {'therapist','human_therapist','therapist_agent'})
            machine_pass=view['status']=='completed' and review.get('status')=='complete' and review.get('gate_status')==required and both
            human_pass=bool(human_current and human['decision']=='approved')
            done=machine_pass and (human_pass if require_human else True)
            if done:completed.add(attempt['scenario_id'])
            if human_current:
                for key,value in human.get('ratings',{}).items():
                    trends.setdefault(key,[]).append({'at':human['created_at'],'value':value,'attempt_id':attempt['id'],'scenario_id':attempt['scenario_id']})
            result.update(status=view['status'],review=review,review_history=history,human_review=human,human_review_current=human_current,
                          machine_passed=machine_pass,human_passed=human_pass,completed=done,session_sha256=session_fingerprint(view),
                          practice_href='/theraskill/simulation/?session_id='+attempt['session_id'])
            items.append(result)
        missing=[s for s in assignment.scenario_ids if s not in completed]
        status='completed' if not missing and assignment.scenario_ids else 'in_progress' if items else 'assigned'
        objective_summary=[]
        for key,values in trends.items():
            values.sort(key=lambda r:r['at'])
            objective_summary.append({'objective_id':key,'observations':values,'first':values[0]['value'],'latest':values[-1]['value'],
                                      'change':values[-1]['value']-values[0]['value'] if len(values)>1 else None,'count':len(values)})
        return {'assignment':assignment.model_dump(mode='json'),'course':course.model_dump(mode='json') if course else None,
                'status':status,'completed_scenarios':sorted(completed),'remaining_scenarios':missing,'attempts':items,
                'completion':{'completed':len(completed),'required':len(assignment.scenario_ids),'required_review_gate':required,'requires_instructor_review':require_human},
                'objectives':objective_summary,'limitations':['Ratings are instructor observations, not causal evidence of learning or clinical competence.']}

    def report(self,assignee=None):
        rows=[self.progress(a.assignment_id) for a in self.repo.list_assignments(assignee=assignee)]
        observations={}
        for row in rows:
            scope=row['assignment'].get('course_id') or row['assignment']['assignment_id']
            for obj in row['objectives']:observations.setdefault((scope,obj['objective_id']),[]).extend(obj['observations'])
        trends=[]
        for (scope,key),values in observations.items():
            values.sort(key=lambda r:r['at'])
            trends.append({'course_id':scope,'objective_id':key,'count':len(values),'observations':values,'latest':values[-1]['value'],
                           'change':values[-1]['value']-values[0]['value'] if len(values)>1 else None})
        return {'assignee':assignee,'assignments':rows,'trends':trends,
                'recommendations':[{'course_id':r['course_id'],'objective_id':r['objective_id'],'reason':'low_latest_rating','latest':r['latest']} for r in trends if r['latest']<3.5]}
