"""Original user inputs through the actual app API; no model, GUI or listener.

Reuses the backend owner's two independent task fixture without modifying it.
The frontend sends every mapping/plan/apply itself through HTTP TestClient.
"""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from fastapi.testclient import TestClient
from apps.api.config import Settings
from apps.api.main import create_app
from test_native_rework import scene, source, ref

temporary = tempfile.TemporaryDirectory(prefix='eeas-frontend-native-rework-')
folder = Path(temporary.name)
settings = Settings(folder / 'controller')


def create_client():
    value = TestClient(create_app(settings), base_url=settings.origin)
    token = value.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'}).json()['csrf_token']
    value.headers.update({'Origin': settings.origin, 'X-EEAS-CSRF': token})
    return value


client = create_client()
generator = None
s = None
try:
    for raw in sys.stdin:
        try:
            command = json.loads(raw)
            if command.get('fixture') == 'setup':
                generator = scene.__wrapped__(client, settings.data_dir)
                s = next(generator)
                answer = {'status': 200, 'body': {'pid': s['pid'], 'source': source(s),
                    'binary_source': source(s, 'binary.png'), 'proposal_ref': ref(s['proposal']),
                    'selected_directory': str(s['root']), 'original': s['original'], 'independent': s['independent'].decode()}}
            elif command.get('fixture') == 'restart':
                client.close()
                client = create_client()
                s['client'] = client
                answer = {'status': 200, 'body': {'reopened': True}}
            elif command.get('fixture') == 'tamper':
                path = Path(command.get('path', '任务与harness.json'))
                if path.is_absolute() or '..' in path.parts:
                    raise ValueError('Test edit outside the original fixture is forbidden')
                (s['root'] / path).write_text(command['text'])
                answer = {'status': 200, 'body': {'modified': True}}
            elif command.get('fixture') == 'bytes':
                answer = {'status': 200, 'body': {'actual': (s['root'] / '任务与harness.json').read_text(),
                    'independent': (s['root'] / '独立任务B.md').read_text()}}
            else:
                response = client.request(command['method'], command['path'],
                    json=command.get('body'), headers=command.get('headers', {}))
                answer = {'status': response.status_code, 'body': response.json()}
        except Exception as error:
            answer = {'status': 500, 'body': {'fixture_error': type(error).__name__, 'message': str(error)}}
        print(json.dumps(answer, ensure_ascii=False), flush=True)
finally:
    if generator is not None:
        generator.close()
    client.close()
    temporary.cleanup()
