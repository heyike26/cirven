"""Real application/SQLite planning round trips, without a model fixture."""
from copy import deepcopy

from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api.main import create_app


def connect(client):
    response = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
    assert response.status_code == 200
    client.headers.update({'Origin': 'http://127.0.0.1:8787', 'X-EEAS-CSRF': response.json()['csrf_token']})


def plan():
    return {'title': '工程核对', 'goal': '明确来源再撰写，不把返回当验收', 'nodes': [
        {'id': 'read', 'title': '核对资料', 'question_ids': ['scope'],
         'acceptance': [{'id': 'trace', 'text': '结论可追溯来源'}]},
        {'id': 'write', 'title': '撰写任务书', 'depends_on': ['read']}],
        'questions': [{'id': 'scope', 'text': '确认目标范围', 'blocking': True}]}


def test_real_api_graph_answer_policy_cas_restart_and_unknown_operation_read(tmp_path):
    settings = Settings(data_dir=tmp_path / 'data', port=8787, worker_enabled=False)
    app = create_app(settings)
    pid = app.state.store.create_project('工程', '')['id']
    root = f'/api/projects/{pid}/dynamic-workflows'
    with TestClient(app, base_url='http://127.0.0.1:8787') as client:
        connect(client)
        created = client.post(root, json={'operation_id': 'create-plan', 'plan': plan()})
        assert created.status_code == 200, created.text
        old = created.json();ref = old['workflow_ref'];path = root + '/' + ref['id']
        assert old['automatic_execution'] is False
        duplicate = client.post(root, json={'operation_id': 'create-plan', 'plan': plan()})
        assert duplicate.status_code == 200 and duplicate.json()['workflow_ref'] == ref
        current = client.get(path).json()
        assert current['node_projection'][0]['status'] == 'NEEDS_CONFIRMATION'
        assert client.get(root + '/operations/create-plan').json()['workflow_ref'] == ref
        assert client.get(root + '/operations/unknown-operation').status_code == 404
        body = {'operation_id': 'answer-scope', 'expected_ref': ref, 'answer': '仅首轮任务书与提示词'}
        answered = client.post(path + '/questions/scope/answer', json=body)
        assert answered.status_code == 200, answered.text
        answered_ref = answered.json()['workflow_ref']
        assert client.post(path + '/questions/scope/answer', json=body).json()['workflow_ref'] == answered_ref
        policy = {'operation_id': 'policy-auto', 'expected_ref': answered_ref,
                  'agent_policy': {'model': {'mode': 'AUTO', 'value': None}, 'depth': {'mode': 'AUTO', 'value': None}}}
        changed = client.post(path + '/nodes/read/policy', json=policy)
        assert changed.status_code == 200, changed.text
        latest = changed.json()['workflow_ref']
        assert client.post(path + '/nodes/read/policy', json=policy).json()['workflow_ref'] == latest
        invalid_depth = deepcopy(policy);invalid_depth.update(operation_id='false-depth', expected_ref=latest)
        invalid_depth['agent_policy']['depth'] = {'mode': 'MANUAL', 'value': 'invented-max'}
        assert client.post(path + '/nodes/read/policy', json=invalid_depth).status_code == 422
        new_plan = plan();new_plan['nodes'][0]['title'] = '重新核对资料'
        conflict = client.post(path + '/replan', json={'operation_id': 'stale-replan', 'expected_ref': ref,
            'plan': new_plan, 'reason': '版本已变化'})
        assert conflict.status_code == 409
        assert client.get(path).json()['workflow_ref'] == latest
        assert client.get(path + '?revision=1').json()['workflow_ref'] == ref
        foreign = app.state.store.create_project('其他工程', '')['id']
        assert client.get(f'/api/projects/{foreign}/dynamic-workflows/{ref["id"]}').status_code == 404
        assert client.get('/api/dynamic-workflow/catalog').json()['model_calls'] == 0
    reopened = create_app(settings)
    with TestClient(reopened, base_url='http://127.0.0.1:8787') as client:
        connect(client)
        actual = client.get(path).json()
        assert actual['workflow_ref'] == latest
        assert actual['answers']['scope']['answer'] == body['answer']
        assert actual['dispatch_performed'] is False


def test_invalid_graph_and_actor_escalation_do_not_create_a_workflow(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / 'data', port=8787, worker_enabled=False))
    pid = app.state.store.create_project('测试', '')['id'];root = f'/api/projects/{pid}/dynamic-workflows'
    with TestClient(app, base_url='http://127.0.0.1:8787') as client:
        connect(client)
        cyclic = plan();cyclic['nodes'][0]['depends_on'] = ['write']
        assert client.post(root, json={'operation_id': 'cycle', 'plan': cyclic}).status_code == 422
        assert client.post(root, json={'operation_id': 'escalation', 'plan': plan(), 'actor': 'VERIFIER'}).status_code == 422
        assert client.get(root).json()['workflows'] == []
