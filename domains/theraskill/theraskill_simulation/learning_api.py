"""Learning routes attached to the existing simulation API."""
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field,ConfigDict,StrictFloat,StrictInt,StrictBool
from typing import Literal
from .contracts import ActorRole,SessionMode,SessionLanguage


class AttemptRequest(BaseModel):
    session_id:str


class PracticeRequest(BaseModel):
    scenario_id:str
    language:SessionLanguage=SessionLanguage.ZH
    request_id:str=Field(min_length=1,max_length=160)


class BehaviorObservation(BaseModel):
    """One teacher observation of one configured course behaviour."""
    model_config = ConfigDict(extra='forbid')
    behavior: str = Field(min_length=1, max_length=500)
    status: Literal['observed', 'not_observed', 'not_applicable']
    score: StrictFloat | StrictInt | None = Field(default=None, ge=1, le=5)
    note: str = Field(default='', max_length=2000)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision:str
    ratings:dict[str,StrictFloat|StrictInt]=Field(default_factory=dict)
    comment:str=Field(min_length=1)
    reviewer:str=Field(min_length=1)
    safety_issue: StrictBool = False
    performance_level:str=Field(default='guided', pattern='^(guided|independent)$')
    transfer_context:str=Field(default='', max_length=500)
    evidence_unit_ids:list[str]=Field(default_factory=list, max_length=30)
    behavior_observations:dict[str,list[BehaviorObservation]]=Field(default_factory=dict, max_length=32)


class ReflectionRequest(BaseModel):
    reflection:str=Field(default='', max_length=2000)


def create_learning_router(learning,service,caller_role):
    router=APIRouter()
    def execute(fn):
        try:return fn()
        except KeyError:raise HTTPException(404,'Learning record not found') from None
        except ValueError as exc:raise HTTPException(422,str(exc)) from None

    @router.get('/learning/report')
    def report(assignee:str|None=None,role:ActorRole=Depends(caller_role)):
        if role not in {ActorRole.INSTRUCTOR,ActorRole.RESEARCHER,ActorRole.SYSTEM} and not assignee:
            raise HTTPException(400,'Choose the assignment learner label')
        return execute(lambda:learning.report(assignee))

    @router.get('/assignments/{assignment_id}/progress')
    def progress(assignment_id:str,_role:ActorRole=Depends(caller_role)):
        return execute(lambda:learning.progress(assignment_id))

    @router.post('/assignments/{assignment_id}/attempts',status_code=201)
    def link(assignment_id:str,body:AttemptRequest,_role:ActorRole=Depends(caller_role)):
        return execute(lambda:learning.link(assignment_id,body.session_id))

    @router.post('/assignments/{assignment_id}/practice',status_code=201)
    def practice(assignment_id:str,body:PracticeRequest,_role:ActorRole=Depends(caller_role)):
        def start():
            import hashlib
            assignment=learning.assignment(assignment_id)
            if body.scenario_id not in assignment.scenario_ids:raise ValueError('Scenario is not in assignment')
            sid='session_'+hashlib.sha256((assignment_id+':'+body.request_id).encode()).hexdigest()[:32]
            from .repository import SessionNotFoundError
            try:snapshot=service.get_snapshot(sid)
            except SessionNotFoundError:snapshot=service.create_session(scenario_id=body.scenario_id,mode=SessionMode.HUMAN_THERAPIST_VS_PATIENT,language=body.language,session_id=sid)
            if snapshot.public_view.scenario.scenario_id!=body.scenario_id:raise ValueError('Request ID reused for a different scenario')
            return learning.link(assignment_id,sid)
        return execute(start)

    @router.post('/learning/attempts/{attempt_id}/review',status_code=201)
    def review(attempt_id:str,body:ReviewRequest,role:ActorRole=Depends(caller_role)):
        if role!=ActorRole.INSTRUCTOR:raise HTTPException(403,'Instructor role is required')
        return execute(lambda:learning.review(attempt_id,body.decision,body.ratings,body.comment,body.reviewer,
                                               performance_level=body.performance_level,transfer_context=body.transfer_context,
                                               evidence_unit_ids=body.evidence_unit_ids,safety_issue=body.safety_issue,
                                               behavior_observations={key:[item.model_dump(mode='json') for item in value]
                                                                     for key,value in body.behavior_observations.items()}))

    @router.post('/learning/attempts/{attempt_id}/reflection')
    def reflection(attempt_id:str,body:ReflectionRequest,role:ActorRole=Depends(caller_role)):
        if role not in {ActorRole.HUMAN_THERAPIST, ActorRole.INSTRUCTOR}:
            raise HTTPException(403,'Learner or instructor role is required')
        return execute(lambda:learning.save_reflection(attempt_id,body.reflection))

    return router
