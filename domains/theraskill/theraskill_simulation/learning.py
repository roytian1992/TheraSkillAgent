"""Course objectives, practice attempts, instructor review and learning trends."""
from __future__ import annotations
import hashlib
import json
import math
import uuid
from datetime import datetime,timezone

from .pedagogy import DEFAULT_TEMPLATES, ProgressObservation, summarize_progress


def stamp():return datetime.now(timezone.utc).isoformat()

def session_fingerprint(view):
    return hashlib.sha256(json.dumps({k:view.get(k) for k in ['session_id','mode','status','scenario','transcript','review','progress']},sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def rubric_fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def comparable_change(values):
    """A score difference is meaningful only under the recorded same conditions."""
    if len(values) < 2:
        return None, 'insufficient_observations'
    first, latest = values[0], values[-1]
    conditions = ('performance_level', 'rubric_sha256', 'scenario_difficulty_band')
    if any(first.get(key) in (None, '') or latest.get(key) in (None, '') for key in conditions):
        return None, 'comparison_conditions_not_recorded'
    changed = [key for key in conditions if first[key] != latest[key]]
    if changed:
        return None, 'different_' + '_and_'.join(changed)
    return latest['value'] - first['value'], 'same_recorded_conditions'


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
                  'scenario_id':view['scenario']['scenario_id'],'created_at':stamp(),'self_reflection':''}
            db.execute('INSERT INTO learning_attempts VALUES (?,?,?,?,?)',(item['id'],assignment_id,session_id,item['created_at'],json.dumps(item)))
        return item

    def save_reflection(self, attempt_id, reflection):
        if not isinstance(reflection, str):
            raise ValueError('Self reflection must be text')
        text = str(reflection or '').strip()
        if len(text) > 2000:
            raise ValueError('Self reflection is too long')
        with self.repo._connect() as db:
            row = db.execute('SELECT payload FROM learning_attempts WHERE id=?', (attempt_id,)).fetchone()
            if not row:
                raise KeyError(attempt_id)
            item = json.loads(row[0])
            view = self.sessions.get_snapshot(item['session_id']).public_view.model_dump(mode='json')
            item['self_reflection'] = text
            item['self_reflection_updated_at'] = stamp()
            item['updated_at'] = item['self_reflection_updated_at']
            item['self_reflection_session_sha256'] = session_fingerprint(view)
            db.execute('UPDATE learning_attempts SET payload=? WHERE id=?', (json.dumps(item, ensure_ascii=False), attempt_id))
        return item

    def reviews(self,attempt_id):
        with self.repo._connect() as db:
            rows=db.execute('SELECT payload FROM learning_reviews WHERE attempt_id=? ORDER BY created_at',(attempt_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def review(self,attempt_id,decision,ratings,comment,reviewer,performance_level='guided',transfer_context='',evidence_unit_ids=None,safety_issue=False,behavior_observations=None):
        if decision not in {'approved','needs_practice','rejected'} or not comment.strip() or not reviewer.strip():
            raise ValueError('Provide a decision, explanation and reviewer')
        if performance_level not in {'guided','independent'}:
            raise ValueError('Performance level must be guided or independent')
        if len(str(transfer_context or '')) > 500:
            raise ValueError('Transfer context is too long')
        if not isinstance(safety_issue, bool):
            raise ValueError('Safety issue must be a boolean')
        if decision == 'approved' and safety_issue:
            raise ValueError('Resolve the safety issue before approving this attempt')
        evidence_unit_ids = list(dict.fromkeys(str(value).strip() for value in (evidence_unit_ids or []) if str(value).strip()))[:30]
        with self.repo._connect() as db:
            row=db.execute('SELECT payload FROM learning_attempts WHERE id=?',(attempt_id,)).fetchone()
        if not row:raise KeyError(attempt_id)
        attempt=json.loads(row[0]);assignment=self.assignment(attempt['assignment_id'])
        course=self.repo.get_course(assignment.course_id) if assignment.course_id else None
        objectives={o['id'] for o in course.learning_objectives} if course else set(assignment.skill_tags)
        if objectives and (set(ratings)-objectives):raise ValueError('Ratings must use course objective IDs')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not 1<=v<=5 for v in ratings.values()):
            raise ValueError('Objective ratings must be finite numbers from 1 to 5')
        behavior_observations = behavior_observations or {}
        if not isinstance(behavior_observations, dict):
            raise ValueError('Behavior observations must be an object')
        configured = {str(o['id']): [str(item).strip() for item in (o.get('observable_behaviors') or []) if str(item).strip()]
                      for o in (course.learning_objectives if course else [])}
        if behavior_observations and not any(configured.values()):
            raise ValueError('This course has no observable behaviours; use objective-level ratings')
        normalized_behaviors = {}
        for objective_id, rows in behavior_observations.items():
            if objective_id not in configured:
                raise ValueError(f'Behavior observations must use course objective IDs: {objective_id}')
            if not isinstance(rows, list) or not rows:
                raise ValueError(f'Behavior observations for {objective_id} cannot be empty')
            seen = set(); normalized_rows = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError('Each behavior observation must be an object')
                behavior = str(row.get('behavior') or '').strip()
                status = str(row.get('status') or '').strip()
                if behavior not in configured[objective_id]:
                    raise ValueError(f'Unknown observable behavior for {objective_id}: {behavior}')
                if behavior in seen:
                    raise ValueError(f'Duplicate behavior observation: {behavior}')
                seen.add(behavior)
                if status not in {'observed', 'not_observed', 'not_applicable'}:
                    raise ValueError('Behavior status must be observed, not_observed or not_applicable')
                score = row.get('score')
                if score is not None:
                    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)) or not 1 <= float(score) <= 5:
                        raise ValueError('Behavior score must be finite and between 1 and 5')
                    if status != 'observed':
                        raise ValueError('Only observed behaviors may have a score')
                    score = float(score)
                note = str(row.get('note') or '').strip()
                if len(note) > 2000:
                    raise ValueError('Behavior note is too long')
                normalized_rows.append({'behavior': behavior, 'status': status, 'score': score, 'note': note})
            normalized_behaviors[objective_id] = normalized_rows
        view=self.sessions.get_snapshot(attempt['session_id']).public_view.model_dump(mode='json')
        review=view.get('review') or {}
        roles={t.get('role') for t in view.get('transcript',[]) if str(t.get('text','')).strip()}
        required=assignment.required_review_gate or (course.completion_review_gate if course else 'pass')
        valid_dialogue=bool(roles & {'patient','human_patient','patient_agent'}) and bool(roles & {'therapist','human_therapist','therapist_agent'})
        if decision=='approved' and (view['status']!='completed' or review.get('status')!='complete' or review.get('gate_status')!=required or not valid_dialogue):
            raise ValueError('Only completed, evaluable practice can receive instructor approval')
        if decision=='approved' and objectives-set(ratings):raise ValueError('Rate every course objective before approval')
        item={'id':'learning_review_'+uuid.uuid4().hex,'attempt_id':attempt_id,'decision':decision,'ratings':ratings,
              'comment':comment,'reviewer':reviewer,'performance_level':performance_level,
              'transfer_context':str(transfer_context or '').strip(),'evidence_unit_ids':evidence_unit_ids,'safety_issue':safety_issue,
              'behavior_observations':normalized_behaviors,
              'course_rubric_sha256':rubric_fingerprint(course.learning_objectives if course else list(assignment.skill_tags)),
              'objective_rubric_sha256':{str(o['id']):rubric_fingerprint(o) for o in (course.learning_objectives if course else [{'id':tag,'description':tag} for tag in assignment.skill_tags])},
              'scenario_difficulty_band':(view.get('scenario') or {}).get('difficulty_band'),
              'created_at':stamp(),'session_sha256':session_fingerprint(view)}
        with self.repo._connect() as db:
            db.execute('INSERT INTO learning_reviews VALUES (?,?,?,?)',(item['id'],attempt_id,item['created_at'],json.dumps(item,ensure_ascii=False)))
        return item

    def progress(self,assignment_id):
        assignment=self.assignment(assignment_id)
        course=self.repo.get_course(assignment.course_id) if assignment.course_id else None
        required=assignment.required_review_gate or (course.completion_review_gate if course else 'pass')
        require_human=course.require_instructor_review if course else True
        current_rubric = rubric_fingerprint(course.learning_objectives if course else list(assignment.skill_tags))
        items=[];completed=set();trends={};pedagogy_observations=[]
        for attempt in self.attempts(assignment_id):
            result=dict(attempt)
            history=self.reviews(attempt['id']);human=history[-1] if history else None
            try:view=self.sessions.get_snapshot(attempt['session_id']).public_view.model_dump(mode='json')
            except Exception as exc:
                result.update(status='unavailable',error=str(exc),completed=False,
                              human_review=human,review_history=history,human_review_current=False)
                items.append(result);continue
            review=view.get('review') or {}
            human_session_current=bool(human and human['session_sha256']==session_fingerprint(view))
            human_current=bool(human_session_current
                               and human.get('course_rubric_sha256') == current_rubric)
            roles={str(turn['role']) for turn in view['transcript'] if str(turn.get('text','')).strip()}
            both=bool(roles & {'patient','human_patient','patient_agent'}) and bool(roles & {'therapist','human_therapist','therapist_agent'})
            machine_pass=view['status']=='completed' and review.get('status')=='complete' and review.get('gate_status')==required and both
            human_pass=bool(human_current and human['decision']=='approved')
            pending_safety = bool(human and human.get('safety_issue'))
            done=machine_pass and not pending_safety and (human_pass if require_human else not (human_current and human['decision'] != 'approved'))
            if done:completed.add(attempt['scenario_id'])
            if human_session_current:
                for key,value in human.get('ratings',{}).items():
                    trends.setdefault(key,[]).append({'at':human['created_at'],'value':value,'attempt_id':attempt['id'],
                        'scenario_id':attempt['scenario_id'],'performance_level':human.get('performance_level'),
                        'rubric_sha256':(human.get('objective_rubric_sha256') or {}).get(key),
                        'scenario_difficulty_band':human.get('scenario_difficulty_band')})
                    if not human_current:
                        continue
                    pedagogy_observations.append(ProgressObservation(
                        attempt_id=attempt['id'], competency_id=str(key), scenario_id=attempt['scenario_id'],
                        rating=float(value), level=human.get('performance_level') or 'guided', observed_at=human['created_at'],
                        transfer_context=human.get('transfer_context') or '',
                        evidence_unit_ids=human.get('evidence_unit_ids') or [], reviewer=human.get('reviewer') or '',
                        safety_issue=bool(human.get('safety_issue', False)),
                    ))
            result.update(status=view['status'],review=review,review_history=history,human_review=human,human_review_current=human_current,
                          machine_passed=machine_pass,human_passed=human_pass,completed=done,session_sha256=session_fingerprint(view),
                          self_reflection_current=bool(attempt.get('self_reflection') and attempt.get('self_reflection_session_sha256')==session_fingerprint(view)),
                          practice_href='/theraskill/simulation/?session_id='+attempt['session_id'])
            items.append(result)
        pending_safety = [item['id'] for item in items if (item.get('human_review') or {}).get('safety_issue')]
        blocked_scenarios = {item['scenario_id'] for item in items if item['id'] in pending_safety}
        completed.difference_update(blocked_scenarios)
        for item in items:
            if item['scenario_id'] in blocked_scenarios:
                item['completed'] = False
        missing=[s for s in assignment.scenario_ids if s not in completed]
        status='blocked_safety' if pending_safety else 'completed' if not missing and assignment.scenario_ids else 'in_progress' if items else 'assigned'
        objective_summary=[]
        for key,values in trends.items():
            values.sort(key=lambda r:r['at'])
            change, comparison_reason = comparable_change(values)
            objective_summary.append({'objective_id':key,'observations':values,'first':values[0]['value'],'latest':values[-1]['value'],
                                      'change':change,'comparison_reason':comparison_reason,'count':len(values)})
        template = next((item for item in DEFAULT_TEMPLATES if course and item.template_id == course.template_id), None)
        pedagogical = summarize_progress(
            pedagogy_observations,
            [str(item['id']) for item in (course.learning_objectives if course else [])] or list(assignment.skill_tags),
            completion_policy=template.completion_policy if template else {'minimum_attempts': 2, 'minimum_independent': 2, 'require_transfer': True},
        )
        pedagogical['pending_safety_attempt_ids'] = pending_safety
        if pending_safety:
            pedagogical['status'] = 'blocked_safety'
        if pending_safety:
            next_step = {'kind': 'supervision', 'reason': 'Resolve the flagged safety issue with a qualified supervisor before more practice.'}
        else:
            incomplete = [item for item in pedagogical.get('competencies', []) if item['status'] not in {'independent', 'transfer'}]
            candidate = next((scenario for scenario in assignment.scenario_ids if scenario not in completed), None)
            if incomplete and candidate:
                focus = incomplete[0]
                next_step = {'kind': 'practice', 'scenario_id': candidate, 'competency_id': focus['competency_id'],
                             'reason': 'Practice this competency in the next uncompleted case, then review the dialogue evidence.'}
            elif incomplete:
                focus = incomplete[0]
                next_step = {'kind': 'review', 'competency_id': focus['competency_id'],
                             'reason': 'Review the existing evidence and choose a new case or a focused micro-practice.'}
            else:
                next_step = {'kind': 'spaced_transfer', 'reason': 'Schedule a later practice in a changed context to check retention and transfer.'}
        return {'assignment':assignment.model_dump(mode='json'),'course':course.model_dump(mode='json') if course else None,
                'status':status,'completed_scenarios':sorted(completed),'remaining_scenarios':missing,'attempts':items,
                'completion':{'completed':len(completed),'required':len(assignment.scenario_ids),'required_review_gate':required,'requires_instructor_review':require_human},
                'objectives':objective_summary,'pedagogical_progress':pedagogical,'next_step':next_step,
                'limitations':['Ratings are instructor observations, not causal evidence of learning or clinical competence.']}

    def report(self,assignee=None):
        rows=[self.progress(a.assignment_id) for a in self.repo.list_assignments(assignee=assignee)]
        observations={}
        for row in rows:
            scope=row['assignment'].get('course_id') or row['assignment']['assignment_id']
            for obj in row['objectives']:observations.setdefault((row['assignment']['assignee'],scope,obj['objective_id']),[]).extend(obj['observations'])
        trends=[]
        for (learner,scope,key),values in observations.items():
            values.sort(key=lambda r:r['at'])
            change, comparison_reason = comparable_change(values)
            trends.append({'assignee':learner,'course_id':scope,'objective_id':key,'count':len(values),'observations':values,'latest':values[-1]['value'],
                           'change':change,'comparison_reason':comparison_reason})
        return {'assignee':assignee,'assignments':rows,'trends':trends,
                'recommendations':[{'assignee':r['assignee'],'course_id':r['course_id'],'objective_id':r['objective_id'],'reason':'low_latest_rating','latest':r['latest']} for r in trends if r['latest']<3.5]}
