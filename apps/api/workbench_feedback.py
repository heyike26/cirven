from fastapi.responses import JSONResponse
from packages.domain.workbench_feedback import WorkbenchFeedback,FeedbackInput,FeedbackRevise


def register_workbench_feedback_routes(app,store):
    service=WorkbenchFeedback(store,app.state.discovery);app.state.workbench_feedback=service

    @app.post('/api/projects/{pid}/workbench/feedback')
    def record(pid:str,body:FeedbackInput):return service.record(pid,body)

    @app.get('/api/projects/{pid}/workbench/feedback')
    def listing(pid:str,session_id:str):return {'feedback':service.list(pid,session_id)}

    @app.get('/api/projects/{pid}/workbench/feedback/{ident}')
    def get(pid:str,ident:str):return service.get(pid,ident)

    @app.post('/api/projects/{pid}/workbench/feedback/{ident}/revise')
    async def revise(pid:str,ident:str,body:FeedbackRevise):
        value=await service.revise(pid,ident,body)
        return JSONResponse(value,status_code=422 if value.get('status') in {'FAILED','UNKNOWN_OUTCOME','CANCELLED'} else 202 if value.get('status')=='PROCESSING' else 200)
