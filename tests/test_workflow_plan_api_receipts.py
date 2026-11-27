"""Real HTTP application/Core receipt tests with a synthetic owner authority.

Qualification: TestClient + actual middleware/API + SQLite + Core and real
AgentSessions integrity-wrapped files. Only the live process/config-current
boundary is a UNIT fixture, not an OpenCode runtime, model or login proof.
"""
from contextlib import contextmanager
from copy import deepcopy
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api.main import create_app
from packages.adapters import opencode_workflow_plugin as policy_files
from packages.domain.tasks import digest
from packages.dynamic_workflow.store import _plan as normalize_plan


def check(condition, message='workflow plan API receipt acceptance failed'):
    if not condition:
        raise AssertionError(message)


def plan():
    return {'title': '原创回执边界', 'goal': '不同意图不能借用同一回执',
            'nodes': [{'id': 'review', 'title': '核对资料', 'description': '用户原始正文',
                       'acceptance': [{'id': 'trace', 'text': '实际结果可追溯'}]}]}


class ReceiptFixture:
    def __init__(self, app, client, monkeypatch):
        self.app, self.client = app, client
        self.sessions, self.core = app.state.agent_sessions, app.state.dynamic_workflows
        self.pid = app.state.store.create_project('回执归属测试', '原创 UNIT fixture')['id']
        self.first = self.core.create(self.pid, plan(), operation_id='create_fixture', actor='USER')
        self.ident = '2' * 32
        self.configuration_current = True
        self.native_calls = []
        monkeypatch.setattr(self.sessions, '_configuration_current', lambda manifest: self.configuration_current)

        async def forbidden_native(*args, **kwargs):
            self.native_calls.append((args, kwargs))
            raise AssertionError('This HTTP receipt fixture must not call a native runtime/model')

        monkeypatch.setattr(self.sessions, '_http', forbidden_native)
        manifest = {'project_id': self.pid, 'id': self.ident,
                    'request': {'workflow_ref': self.first['workflow_ref'], 'workflow_provider_refs': [],
                                'network_mode': 'STRICT_OFFLINE'}}
        self.sessions._write(self.sessions._path(self.pid, self.ident, 'manifest.json'), manifest)
        _, session_ref = self.sessions._manifest(self.pid, self.ident)
        self.owner_ref = {'project_id': self.pid, 'session_ref': session_ref, 'generation': 7}
        profile = SimpleNamespace(profile_id='fixture_route', provider_id='fixture_provider', model_id='fixture_model',
                                  provider_url='http://127.0.0.1:9/v1', reasoning_efforts=('low', 'high'))
        nodes = [{key: node[key] for key in ('id', 'title', 'agent_policy')} for node in self.first['plan']['nodes']]
        initial_policy = policy_files._policy(self.owner_ref, nodes, self.first['workflow_ref'], [], 1)
        filename = self.sessions._path(self.pid, self.ident, 'policy.json')
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(policy_files._canonical(initial_policy) + '\n')
        admission = {'owner_ref': self.owner_ref, 'policy_path': str(filename),
                     'policy_ref': {'revision': 1, 'sha256': initial_policy['sha256']}}
        self.context = {'process': SimpleNamespace(returncode=None), 'closing': False, 'generation': 7,
                        'expires_at': '2076-01-01T00:00:00+00:00', 'profile': profile,
                        'known_sessions': {'ses_fixture'}, 'workflow_id': self.first['id'],
                        'workflow_plan_ref': self.first['workflow_ref'], 'workflow_policy_lock': threading.RLock(),
                        'workflow': {'admission': admission, 'aliases': [{'node_id': 'review'}]},
                        'workflow_plan_token': 'c' * 96, 'workflow_event_token': 'd' * 96}
        self.sessions.active[self.ident] = self.context
        self.sessions._write(self.sessions._path(self.pid, self.ident, 'state.json'),
            {'generation': 7, 'owner_status': 'READY', 'active_operation_id': 'fixture_active_message',
             'observation_error': None, 'last_event_seq': 0})
        check(not client.cookies, 'Plugin fixture must not inherit a user cookie')

    def body(self, operation_id='model_proposal'):
        proposal = deepcopy(self.first['plan'])
        proposal['nodes'][0]['description'] = '模型建议的新描述，仍需真实验证'
        return {'schema_version': 1, 'owner_ref': deepcopy(self.owner_ref), 'operation_id': operation_id,
                'expected_ref': deepcopy(self.first['workflow_ref']), 'plan': proposal,
                'reason': '依据新资料修订'}

    def headers(self, event=False):
        token = self.context['workflow_event_token' if event else 'workflow_plan_token']
        return {'Authorization': 'Bearer ' + token}

    def core_id(self, operation_id):
        return 'wfplan_' + digest([self.ident, self.context['generation'], operation_id])[:48]

    def intent_path(self, operation_id):
        return self.sessions._path(self.pid, self.ident, 'workflow-plan-operations',
            digest([self.context['generation'], operation_id]) + '.json')

    def put_intent(self, body, **overrides):
        value = {'request': deepcopy(body), 'request_sha256': digest(body),
                 'core_operation_id': self.core_id(body['operation_id'])}
        value.update(overrides)
        path = self.intent_path(body['operation_id'])
        self.sessions._write(path, value, replace=path.exists())
        return value

    def post(self, body, event=False):
        return self.client.post('/api/dynamic-workflow/plugin-plan', json=body, headers=self.headers(event))

    def read(self, operation_id, event=False):
        return self.client.get('/api/dynamic-workflow/plugin-plan/operations/' + operation_id,
                               headers=self.headers(event))

    def counts(self):
        with self.app.state.store.connect() as connection:
            return tuple(connection.execute('SELECT count(*) FROM ' + table + ' WHERE project_id=?',
                                             (self.pid,)).fetchone()[0]
                         for table in ('dynamic_workflow_versions', 'dynamic_workflow_operations'))

    def state(self, **changes):
        self.sessions._update(self.pid, self.ident, **changes)


@contextmanager
def receipt_fixture(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path / 'data', port=8787, worker_enabled=False))
    # Keep unrelated read-only collection out of a deliberately synthetic owner.
    supervisor = app.state.global_supervisor
    settings = supervisor.settings()
    supervisor.update_settings(enabled=False, interval_seconds=settings['interval_seconds'],
                               expected_revision=settings['revision'], notifications_enabled=False)
    with TestClient(app, base_url='http://127.0.0.1:8787') as client:
        fixture = None
        try:
            fixture = ReceiptFixture(app, client, monkeypatch)
            yield fixture
        finally:
            # Never let application shutdown treat the synthetic process as a
            # created/adopted original owner. Real services still close normally.
            app.state.agent_sessions.active.pop('2' * 32, None)
            if fixture is not None:
                check(not fixture.native_calls, 'A receipt lookup attempted native/model execution')


@pytest.mark.parametrize('actor', ['USER', 'MODEL'])
def test_preclaimed_deterministic_core_operation_never_becomes_a_plugin_commit(tmp_path, monkeypatch, actor):
    with receipt_fixture(tmp_path, monkeypatch) as f:
        body = f.body('preclaimed')
        other = deepcopy(body['plan'])
        if actor == 'MODEL':
            other['nodes'][0]['description'] = '另一条 MODEL 请求，不能借用其结果'
        # USER uses otherwise identical semantics: the actor alone must remain
        # part of the request fingerprint. MODEL additionally changes content.
        existing = f.core.replan(f.pid, f.first['id'], other,
            expected_ref=body['expected_ref'], operation_id=f.core_id(body['operation_id']),
            actor=actor, reason=body['reason'])
        before = f.counts()
        original = f.core.operation(f.pid, f.first['id'], f.core_id(body['operation_id']))
        rejected = f.post(body)
        check(rejected.status_code == 409, rejected.text)
        check(rejected.json().get('write_status') != 'COMMITTED')
        for _ in range(2):
            recovered = f.read(body['operation_id'])
            check(recovered.status_code == 409, recovered.text)
            check(recovered.json().get('write_status') != 'COMMITTED')
        duplicate = f.post(body)
        check(duplicate.status_code == 409 and duplicate.json().get('write_status') != 'COMMITTED')
        check(f.counts() == before, 'Rejected/recovered plugin intent wrote an extra Core version/operation')
        check(f.core.get(f.pid, f.first['id'])['workflow_ref'] == existing['workflow_ref'])
        check(f.core.operation(f.pid, f.first['id'], f.core_id(body['operation_id'])) == original)
        intent = f.sessions._read(f.intent_path(body['operation_id']))
        check(intent['request_sha256'] == digest(body), 'Original rejected plugin intent was lost')
        check(original['request_sha256'] != digest({'method': 'replan', 'workflow_id': f.first['id'],
            'plan': normalize_plan(body['plan'], f.pid), 'expected_ref': body['expected_ref'],
            'actor': 'MODEL', 'reason': body['reason']}))


def test_exact_commit_receipt_reads_original_after_new_head_and_expiry_without_user_actor_authority(tmp_path, monkeypatch):
    with receipt_fixture(tmp_path, monkeypatch) as f:
        body = f.body()
        # Dedicated plugin token is not a general same-origin user/API token.
        user_path = f'/api/projects/{f.pid}/dynamic-workflows'
        check(f.client.post(user_path, json={'operation_id': 'unauthorized_user', 'plan': plan()},
                            headers=f.headers()).status_code == 401)
        for actor in ('USER', 'VERIFIER'):
            forged = {**body, 'operation_id': 'forged_' + actor, 'actor': actor}
            check(f.post(forged).status_code == 422)
        check(f.post(body, event=True).status_code == 401)
        before = f.counts()
        for owner_change in ({'project_id': 'foreign_project'}, {'generation': 8}):
            foreign = deepcopy(body)
            foreign['owner_ref'].update(owner_change)
            check(f.post(foreign).status_code == 409)
        check(f.counts() == before)
        result = f.post(body)
        check(result.status_code == 200, result.text)
        exact = result.json()
        check(exact['write_status'] == 'COMMITTED' and exact['change']['actor'] == 'MODEL')
        check(exact['plugin_request_sha256'] == digest(body))
        check(exact['core_operation_id'] == f.core_id(body['operation_id']))
        committed_counts = f.counts()
        check(f.post(body).json()['workflow_ref'] == exact['workflow_ref'])
        check(f.counts() == committed_counts, 'Duplicate plugin POST executed the mutation twice')
        another = deepcopy(exact['plan'])
        another['nodes'][0]['description'] = '之后用户的有效第三版'
        latest = f.core.replan(f.pid, f.first['id'], another, expected_ref=exact['workflow_ref'],
                              operation_id='later_user_version', actor='USER', reason='用户继续编辑')
        counts = f.counts()

        def original_read():
            recovered = f.read(body['operation_id'])
            check(recovered.status_code == 200, recovered.text)
            value = recovered.json()
            check(value['workflow_ref'] == exact['workflow_ref'] != latest['workflow_ref'])
            check(value['plugin_request_sha256'] == digest(body) and value['write_status'] == 'COMMITTED')
            check(f.counts() == counts, 'Original GET mutated the plan or replayed execution')

        original_read()
        f.state(active_operation_id=None)
        check(f.post(f.body('new_idle_write')).status_code == 401)
        original_read()
        f.state(active_operation_id='fixture_active_message')
        f.context['expires_at'] = '2000-01-01T00:00:00+00:00'
        check(f.post(f.body('new_expired_write')).status_code == 401)
        original_read()
        f.context['expires_at'] = '2076-01-01T00:00:00+00:00'
        f.configuration_current = False
        check(f.post(f.body('new_changed_config_write')).status_code == 401)
        original_read()
        f.configuration_current = True
        f.state(owner_status='BLOCKED')
        check(f.post(f.body('new_blocked_write')).status_code == 401)
        original_read()
        f.state(owner_status='READY', generation=8)
        check(f.read(body['operation_id']).status_code == 401)
        check(f.post(f.body('other_generation')).status_code == 401)
        f.state(generation=7)
        check(f.read(body['operation_id'], event=True).status_code == 401)
        original_read()
        check(f.read('missing_original').status_code == 404)
        check(f.counts() == counts)
        check(not f.client.cookies, 'Plugin request unexpectedly gained a user session cookie')


@pytest.mark.parametrize('corruption', ['self_checksum', 'derived_operation', 'different_model_input'])
def test_integrity_wrapped_but_semantically_wrong_intent_cannot_borrow_a_core_receipt(tmp_path, monkeypatch, corruption):
    with receipt_fixture(tmp_path, monkeypatch) as f:
        body = f.body('semantic_integrity')
        result = f.post(body)
        check(result.status_code == 200, result.text)
        before = f.counts()
        correct = f.sessions._read(f.intent_path(body['operation_id']))
        altered = deepcopy(correct)
        if corruption == 'self_checksum':
            altered['request_sha256'] = '0' * 64
        elif corruption == 'derived_operation':
            altered['core_operation_id'] = 'another_owned_core_operation'
        else:
            altered['request']['reason'] = '不同的 MODEL 意图，外层文件仍自洽'
            altered['request_sha256'] = digest(altered['request'])
        # Recompute the actual AgentSessions file envelope: this must exercise
        # semantic matching beyond merely catching broken file integrity bytes.
        f.sessions._write(f.intent_path(body['operation_id']), altered, replace=True)
        wrong = f.read(body['operation_id'])
        check(wrong.status_code == 409, wrong.text)
        check(wrong.json().get('write_status') != 'COMMITTED')
        check(f.counts() == before)
        check(f.sessions._read(f.intent_path(body['operation_id'])) == altered, 'GET silently rewrote the original intent')
        f.sessions._write(f.intent_path(body['operation_id']), correct, replace=True)
        restored = f.read(body['operation_id'])
        check(restored.status_code == 200 and restored.json()['workflow_ref'] == result.json()['workflow_ref'])
        check(f.counts() == before)


def test_durable_intent_without_core_receipt_is_unknown_and_duplicate_post_never_dispatches(tmp_path, monkeypatch):
    with receipt_fixture(tmp_path, monkeypatch) as f:
        body = f.body('intent_only')
        original = f.put_intent(body)
        before = f.counts()
        for _ in range(2):
            missing = f.read(body['operation_id'])
            check(missing.status_code == 404, missing.text)
            check(missing.json().get('write_status') != 'COMMITTED')
            duplicate = f.post(body)
            check(duplicate.status_code == 404, duplicate.text)
            check(duplicate.json().get('write_status') != 'COMMITTED')
        check(f.counts() == before, 'An existing uncertain intent retried the plan mutation')
        check(f.core.get(f.pid, f.first['id'])['workflow_ref'] == f.first['workflow_ref'])
        check(f.sessions._read(f.intent_path(body['operation_id'])) == original)
