"""Question APIs use the existing authenticated local single-user boundary."""
from fastapi import Query
from fastapi.responses import JSONResponse

from packages.domain.questions import AnswerInput, KnowledgeBlocked, QuestionCancel, QuestionInput, QuestionService


def register_question_routes(app, store, caller_id):
    service = QuestionService(store)
    app.state.questions = service
    # caller_id comes from the server's already-authenticated local session.
    # It is never accepted from JSON, a model field, or a client header.
    service.caller(caller_id)

    @app.exception_handler(KnowledgeBlocked)
    async def blocked_question(request, exception):
        return JSONResponse({'error': {'code': exception.gate['status'],
            'message': str(exception), 'knowledge': exception.gate}}, status_code=409)

    @app.post('/api/projects/{project_id}/questions', status_code=201)
    def create_question(project_id: str, body: QuestionInput):
        return service.save(project_id, body, caller_id)

    @app.put('/api/projects/{project_id}/questions/{question_id}')
    def revise_question(project_id: str, question_id: str, body: QuestionInput):
        return service.save(project_id, body, caller_id, question_id)

    @app.get('/api/projects/{project_id}/questions')
    def questions(project_id: str, after_seq: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
        return service.list(project_id, after_seq, limit)

    @app.get('/api/projects/{project_id}/questions/{question_id}')
    def question(project_id: str, question_id: str, revision: int | None = Query(default=None, ge=1)):
        return service.get(project_id, question_id, revision)

    @app.post('/api/projects/{project_id}/questions/{question_id}/answers', status_code=201)
    def answer(project_id: str, question_id: str, body: AnswerInput):
        return service.save_answer(project_id, question_id, body, caller_id)

    @app.put('/api/projects/{project_id}/questions/{question_id}/answers/{answer_id}')
    def correct_answer(project_id: str, question_id: str, answer_id: str, body: AnswerInput):
        return service.save_answer(project_id, question_id, body, caller_id, answer_id)

    @app.get('/api/projects/{project_id}/questions/{question_id}/answers')
    def answers(project_id: str, question_id: str, after_seq: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
        return service.answers(project_id, question_id, after_seq, limit)

    @app.get('/api/projects/{project_id}/questions/{question_id}/answers/{answer_id}')
    def stored_answer(project_id: str, question_id: str, answer_id: str, revision: int | None = Query(default=None, ge=1)):
        return service.get_answer(project_id, question_id, answer_id, revision)

    @app.post('/api/projects/{project_id}/questions/{question_id}/cancel')
    def cancel(project_id: str, question_id: str, body: QuestionCancel):
        return service.cancel(project_id, question_id, body, caller_id)

    @app.get('/api/projects/{project_id}/question-events')
    def question_events(project_id: str, after_seq: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
        return {'events': service.events(project_id, after_seq, limit)}

    @app.get('/api/projects/{project_id}/tasks/{task_id}/knowledge')
    def knowledge(project_id: str, task_id: str, revision: int | None = Query(default=None, ge=1)):
        return service.knowledge(project_id, task_id, revision)
