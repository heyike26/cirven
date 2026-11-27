"""Actual app/OOXML/storage test fixture with user-authored pre-answer context.

No model, original CLI, desktop or listening port is invoked here. Original CLI
intake-to-confirmed-export evidence belongs to test_planning_delivery.py.
"""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from docx import Document
from fastapi.testclient import TestClient
from apps.api.config import Settings
from apps.api.main import create_app

temporary = tempfile.TemporaryDirectory(prefix='eeas-frontend-delivery-')
folder = Path(temporary.name)
selected = folder / 'selected-project'
selected.mkdir()
settings = Settings(folder / 'controller')


def create_client():
    value = TestClient(create_app(settings), base_url=settings.origin)
    token = value.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'}).json()['csrf_token']
    value.headers.update({'Origin': settings.origin, 'X-EEAS-CSRF': token})
    return value


def require(response):
    if response.status_code >= 400:
        raise RuntimeError(str(response.status_code) + ': ' + response.text)
    return response.json()


client = create_client()
try:
    for raw in sys.stdin:
        try:
            command = json.loads(raw)
            if command.get('fixture') == 'setup':
                pid = require(client.post('/api/projects', json={'name': '前端真实阶段出口', 'description': ''}))['id']
                base = f'/api/projects/{pid}'
                workspace = require(client.post(base + '/local-workspaces', json={
                    'idempotency_key': 'selected', 'root': str(selected), 'allowed_write_paths': ['delivery/']}))
                document = Document()
                document.sections[0].header.paragraphs[0].text = '原创模板页眉 — 保留'
                document.add_paragraph('{{ report_sections }}')
                document.save(selected / '用户模板.docx')
                (selected / '普通说明.md').write_bytes('Firsyt\r\n保留原来的文字。\r\n'.encode())
                template = require(client.get(base + f'/local-workspaces/{workspace["id"]}/file', params={'path': '用户模板.docx'}))['source_ref']
                session = require(client.post(base + '/discovery/sessions', json={
                    'operation_id': 'user-session', 'title': '用户明确陈述的阶段工作', 'entry_mode': 'PROBLEM',
                    'statement': '我要核对当前来源；验收范围尚待我回答。',
                    'workspace_ref': {key: workspace[key] for key in ('id', 'revision', 'binding_sha256')}}))
                candidate = {'title': '用户编写的阶段工作', 'phase': 'DISCOVER', 'summary': '保留未知验收边界。',
                    'claims': [{'id': 'review', 'text': '我要求每项结论能回到原版本。',
                        'category': 'REQUIREMENT', 'origin': 'USER_STATEMENT', 'acceptance': ['固定来源版本']}],
                    'questions': [{'id': 'scope', 'text': '首轮实际验收范围是什么？', 'reason': '尚未给出范围。',
                        'category': 'MISSING_INPUT', 'blocking': True, 'affects_claim_ids': ['review']}],
                    'graphs': [{'id': 'user-flow', 'kind': 'FLOW', 'title': '用户绘制的核对流程',
                        'nodes': [{'id': 'source', 'label': '保留来源', 'description': '保留已读取的来源版本。', 'claim_ids': ['review'], 'origin': 'USER_STATEMENT'},
                            {'id': 'inspect', 'label': '用户核对', 'description': '由用户核对待确认范围。', 'claim_ids': ['review'], 'origin': 'USER_STATEMENT'}],
                        'edges': [{'id': 'read', 'source': 'source', 'target': 'inspect', 'relation': '核对',
                            'claim_ids': ['review'], 'origin': 'USER_STATEMENT'}]}]}
                session = require(client.put(base + '/discovery/sessions/' + session['id'], json={
                    'operation_id': 'user-edit', 'expected_revision': session['revision'],
                    'candidate': candidate, 'change_reason': '用户明示其要求、待答问题与图'}))
                answer = {'status': 200, 'body': {'pid': pid, 'workspace': workspace, 'session': session, 'template': template, 'selected_file_path': str(selected / '普通说明.md')}}
            elif command.get('fixture') == 'restart':
                client.close()
                client = create_client()
                answer = {'status': 200, 'body': {'reopened': True}}
            elif command.get('fixture') == 'tamper':
                relative = Path(command['path'])
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Test file must belong to the selected fixture folder')
                (selected / relative).write_bytes(command.get('text', '用户人工修改保留。').encode())
                answer = {'status': 200, 'body': {'modified': True}}
            else:
                response = client.request(command['method'], command['path'], json=command.get('body'), headers=command.get('headers', {}))
                answer = {'status': response.status_code, 'body': response.json()}
        except Exception as error:
            answer = {'status': 500, 'body': {'fixture_error': type(error).__name__, 'message': str(error)}}
        print(json.dumps(answer, ensure_ascii=False), flush=True)
finally:
    client.close()
    temporary.cleanup()
