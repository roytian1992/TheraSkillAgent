"""Learning routes attached to the existing simulation API."""
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from .contracts import ActorRole,SessionMode,SessionLanguage


class AttemptRequest(BaseModel):
    session_id:str


class PracticeRequest(BaseModel):
    scenario_id:str
    language:SessionLanguage=SessionLanguage.ZH
    request_id:str=Field(min_length=1,max_length=160)


class ReviewRequest(BaseModel):
    decision:str
    ratings:dict[str,float]=Field(default_factory=dict)
    comment:str=Field(min_length=1)
    reviewer:str=Field(min_length=1)


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
        return execute(lambda:learning.review(attempt_id,body.decision,body.ratings,body.comment,body.reviewer))

    return router
