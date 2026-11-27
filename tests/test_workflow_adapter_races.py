"""Adapter qualification, not a native runtime/model test.

Policy races use the real SQLite store and filesystem policy CAS helper. The
native source in late-assistant cases is an explicit function-level fixture;
no fixture is represented as an observed OpenCode process or existing API.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace


from packages.adapters import opencode_workflow_plugin as policy_files
from packages.agent_sessions import workflow
from packages.agent_sessions.service import AgentSessions
from packages.domain.tasks import digest
from packages.dynamic_workflow.store import DynamicWorkflowStore


def check(condition, message='workflow adapter acceptance failed'):
    if not condition:
        raise AssertionError(message)


def plan():
    return {'title': '原创通用工程', 'goal': '计划与真实运行依据分开',
            'nodes': [{'id': 'review', 'title': '核对资料', 'description': '不改原件',
                       'acceptance': [{'id': 'source', 'text': '实际核验原件未改动'}]}]}


class UnitSourceOwner:
    """Only the adapter owner boundary is substituted, with real record files."""
    _write = staticmethod(AgentSessions._write)
    _read = staticmethod(AgentSessions._read)

    def __init__(self, directory, *, resources=False):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.pid, self.ident = 'project', '1' * 32
        self.dynamic_workflows = DynamicWorkflowStore(self.directory / 'core.sqlite')
        self.first = self.dynamic_workflows.create(self.pid, plan(), operation_id='create', actor='USER')
        self.session_ref = {'id': self.ident, 'revision': 1, 'sha256': 'a' * 64}
        self.owner_ref = {'project_id': self.pid, 'session_ref': self.session_ref, 'generation': 4}
        self.profile = SimpleNamespace(profile_id='route', provider_id='provider', model_id='model',
                                       provider_url='http://127.0.0.1:9/v1', reasoning_efforts=('low', 'high'))
        self.manifest = {'request': {'workflow_ref': self.first['workflow_ref'],
                                    'workflow_provider_refs': [], 'network_mode': 'STRICT_OFFLINE'}}
        if resources:
            from packages.domain.store import Store
            from packages.domain.local_workspaces import LocalWorkspaces
            self.store=Store(self.directory/'rpc-budget.sqlite')
            self.workspaces=LocalWorkspaces(self.store,self.directory/'private')
            self.manifest.update(id=self.ident,project_id=self.pid)
            self.manifest['request'].update(workspace_ref=None,max_calls=10)
            self.session_ref={**self.session_ref,'sha256':digest(self.manifest)}
            self.owner_ref={**self.owner_ref,'session_ref':self.session_ref}
            self._editor_assert=lambda manifest,**options:None  # UNIT has no registered editor.
        nodes = self._nodes(self.first)
        policy = policy_files._policy(self.owner_ref, nodes, self.first['workflow_ref'], [], 1)
        filename = self.directory / 'policy.json'
        filename.write_text(policy_files._canonical(policy) + '\n')
        admission = {'owner_ref': self.owner_ref, 'policy_path': str(filename),
                     'policy_ref': {'revision': 1, 'sha256': policy['sha256']}, 'config_sha256': 'b' * 64}
        self.context = {'workflow': {'admission': admission, 'aliases': [
                            {'alias': 'eeas_fixture_review', 'node_id': 'review', 'route_id': 'route'}]},
                        'workflow_policy_lock': threading.RLock(), 'workflow_id': self.first['id'],
                        'workflow_plan_ref': self.first['workflow_ref'],
                        'workflow_policy_plans': {(1, policy['sha256']): self.first['workflow_ref']},
                        'closing': False, 'generation': 4, 'profile': self.profile,
                        'known_sessions': {'ses_root', 'ses_child'}, 'expires_at': '2076-01-01T00:00:00+00:00',
                        'process': SimpleNamespace(returncode=None), 'workflow_plan_token': 'c' * 96,
                        'workflow_event_token': 'd' * 96}
        self.active, self.closed, self.configuration_current = {self.ident: self.context}, False, True
        self._write(self._path(self.pid, self.ident, 'state.json'),
                    {'generation': 4, 'owner_status': 'READY', 'active_operation_id': 'original_message',
                     'observation_error': None})
        if not resources:
            self._write(self._path(self.pid, self.ident, 'original-operation.json'),
                        {'operation_ref': {'id': 'original_message', 'revision': 1, 'sha256': 'e' * 64}})
        if resources:
            request={'session_ref':self.session_ref,'generation':4,'raw_text':'UNIT admitted original work'}
            self._write(self._path(self.pid,self.ident,'original-operation.json'),
                {'id':'original_message','session_ref':self.session_ref,'generation':4,'kind':'MESSAGE',
                 'request':request,'request_sha256':digest(request),'status':'DISPATCHING'})
        selected = {'id': 'dispatch_one', 'session_id': 'ses_root', 'call_id': 'call_one',
                    'node_id': 'review', 'selected_alias': 'eeas_fixture_review',
                    'selected': {'provider_id': 'provider', 'model_id': 'model', 'variant': 'high'},
                    'policy_ref': admission['policy_ref']}
        projection = {'schema': 'eeas.dynamic-workflow.projection.v1', 'owner_ref': self.owner_ref,
                      'config_sha256': admission['config_sha256'], 'actual': {'dispatches': [selected]}}
        filename = self._path(self.pid, self.ident, 'runtime', 'workflow', 'generation-4', 'projection.json')
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(json.dumps(projection))
        self.child_source, self.http_calls = [], []
        self.root_source = [{'info': {'sessionID': 'ses_root'}, 'parts': [
            {'type': 'tool', 'tool': 'task', 'sessionID': 'ses_root', 'callID': 'call_one',
             'state': {'status': 'completed', 'metadata': {'sessionId': 'ses_child', 'parentSessionId': 'ses_root'}}}]}]
        self.thread_manifest_events = {}

    @staticmethod
    def _nodes(value):
        return [{key: item[key] for key in ('id', 'title', 'agent_policy')} for item in value['plan']['nodes']]

    def _path(self, pid, ident, *parts):
        check(pid == self.pid and ident == self.ident, 'fixture exact owner mismatch')
        result = self.directory.joinpath('records', *parts)
        result.parent.mkdir(parents=True, exist_ok=True)
        return result

    def _manifest(self, pid, ident):
        check(pid == self.pid and ident == self.ident, 'fixture foreign manifest')
        event = self.thread_manifest_events.get(threading.current_thread().name)
        if event:
            event.set()
        return self.manifest, self.session_ref

    def _state(self, pid, ident):
        return self._read(self._path(pid, ident, 'state.json'))

    def _update(self, pid, ident, **fields):
        current = self._state(pid, ident)
        current.update(fields)
        self._write(self._path(pid, ident, 'state.json'), current, replace=True)

    def _configuration_current(self, manifest):
        return self.configuration_current

    def operation(self, pid, ident, operation_id):
        check(operation_id == 'original_message', 'fixture wrong original operation')
        return self._read(self._path(pid, ident, 'original-operation.json'))

    @staticmethod
    def _clean(context, value):
        return deepcopy(value)  # This original source fixture contains no secrets.

    async def _http(self, context, method, path, **options):
        check(method == 'GET' and path == '/session/ses_child/message', 'fixture may only observe existing child')
        self.http_calls.append((method, path, deepcopy(options)))
        return deepcopy(self.child_source)


def change(owner, title, operation):
    current = owner.dynamic_workflows.get(owner.pid, owner.first['id'])
    value = deepcopy(current['plan'])
    value['nodes'][0]['description'] = title
    return owner.dynamic_workflows.replan(owner.pid, current['id'], value,
        expected_ref=current['workflow_ref'], operation_id=operation, actor='USER', reason=title)


def current_policy(owner):
    return json.loads(Path(owner.context['workflow']['admission']['policy_path']).read_bytes())


def node_projection(owner):
    value = owner.dynamic_workflows.get(owner.pid, owner.first['id'])
    return next(item for item in value['node_projection'] if item['node_id'] == 'review')


def test_parallel_policy_sync_serializes_head_read_and_real_filesystem_write_without_regression(tmp_path, monkeypatch):
    owner = UnitSourceOwner(tmp_path)
    second = change(owner, '用户第二版', 'second')
    paused, release, b_manifest, b_finished = (threading.Event() for _ in range(4))
    owner.thread_manifest_events['policy-B'] = b_manifest
    original_write = workflow.write_workflow_policy
    results, errors = {}, []

    def gated_write(*args, **kwargs):
        if threading.current_thread().name == 'policy-A':
            paused.set()
            if not release.wait(10):
                raise AssertionError('test failed to release exact write gate')
        return original_write(*args, **kwargs)

    monkeypatch.setattr(workflow, 'write_workflow_policy', gated_write)

    def sync(label):
        try:
            results[label] = workflow.sync_workflow_policies(owner, owner.pid, owner.first['id'])
        except BaseException as error:
            errors.append(error)
        finally:
            if label == 'B':
                b_finished.set()

    first_thread = threading.Thread(target=sync, args=('A',), name='policy-A')
    second_thread = threading.Thread(target=sync, args=('B',), name='policy-B')
    first_thread.start()
    try:
        check(paused.wait(10), 'A did not reach actual file helper')
        third = change(owner, '用户第三版', 'third')
        second_thread.start()
        # This proves the owner lock encloses the head read, rather than merely
        # hoping the independent filesystem CAS rejects one stale writer.
        check(not b_manifest.wait(.15), 'B read a head while A owned the policy transaction')
        check(not b_finished.is_set(), 'B bypassed the owner policy lock')
    finally:
        release.set()
        first_thread.join(10)
        if second_thread.ident is not None:
            second_thread.join(10)
    check(not first_thread.is_alive() and not second_thread.is_alive(), 'owned test threads survived')
    check(not errors, repr(errors))
    check(results['A'][0]['status'] == results['B'][0]['status'] == 'NEXT_DELEGATION')
    saved = current_policy(owner)
    check(saved['plan_ref'] == third['workflow_ref'] == owner.context['workflow_plan_ref'])
    check(saved['revision'] == 3 and saved['nodes'][0]['id'] == 'review')
    check(owner.dynamic_workflows.get(owner.pid, owner.first['id'])['workflow_ref'] == third['workflow_ref'])
    check(second['revision'] == 2 and third['revision'] == 3)


def test_sync_rejects_a_stale_context_head_and_keeps_exact_current_policy_bytes(tmp_path):
    owner = UnitSourceOwner(tmp_path)
    second = change(owner, '实际第二版', 'second')
    check(workflow.sync_workflow_policies(owner, owner.pid, owner.first['id'])[0]['status'] == 'NEXT_DELEGATION')
    filename = Path(owner.context['workflow']['admission']['policy_path'])
    before = filename.read_bytes()
    # A source-corrupted projected context must not downgrade actual disk/head.
    # No Core method or plan is mocked: the authoritative SQLite head stays v2.
    owner.context['workflow_plan_ref'] = {**second['workflow_ref'], 'revision': 3}
    result = workflow.sync_workflow_policies(owner, owner.pid, owner.first['id'])
    check(result[0]['status'] == 'POLICY_SYNC_UNCONFIRMED')
    check(filename.read_bytes() == before)
    check(owner.dynamic_workflows.get(owner.pid, owner.first['id'])['workflow_ref'] == second['workflow_ref'])


def final_assistant():
    return [{'info': {'id': 'msg_child_final', 'sessionID': 'ses_child', 'role': 'assistant',
                      'providerID': 'provider', 'modelID': 'model', 'variant': 'high',
                      'time': {'created': 10, 'completed': 20}}, 'parts': []}]


def clock(monkeypatch):
    value = [100.0]
    monkeypatch.setattr(workflow, 'time', SimpleNamespace(monotonic=lambda: value[0]))
    return value


def test_unit_native_source_late_assistant_enriches_real_core_after_root_return_without_reexecution(tmp_path, monkeypatch):
    owner, moment = UnitSourceOwner(tmp_path), clock(monkeypatch)

    async def run():
        await workflow.reconcile_workflow(owner, owner.pid, owner.ident, owner.context, owner.root_source)
        initial = node_projection(owner)
        check(initial['execution_status'] == 'RETURNED' and initial['effective']['model']['status'] == 'UNOBSERVED')
        check(owner.context['workflow_evidence_pending'] and not owner.context.get('workflow_final_calls'))
        await workflow.reconcile_workflow(owner, owner.pid, owner.ident, owner.context, owner.root_source)
        check(len(owner.http_calls) == 1, '1s evidence observation limit was ignored')
        owner._update(owner.pid, owner.ident, active_operation_id=None)
        owner.child_source = final_assistant()
        moment[0] += 1.1
        await workflow.reconcile_workflow(owner, owner.pid, owner.ident, owner.context, owner.root_source)
        enriched = node_projection(owner)
        check(enriched['execution_status'] == 'RETURNED')
        check(enriched['effective']['model']['effective'] == 'route')
        check(enriched['effective']['depth']['effective'] == 'high')
        check(enriched['validation_status'] == 'NOT_OBSERVED', 'returned model output was falsely accepted')
        check(not owner.context['workflow_evidence_pending'] and owner.context['workflow_final_calls'])
        moment[0] += 2
        await workflow.reconcile_workflow(owner, owner.pid, owner.ident, owner.context, owner.root_source)
        check(len(owner.http_calls) == 2, 'final immutable evidence was repeatedly fetched')
        check(all(call[0] == 'GET' for call in owner.http_calls))
        value = owner.dynamic_workflows.get(owner.pid, owner.first['id'])
        check(len(value['actual_records']) == 2, 'late evidence did not persist as separate exact records')
        reopened = DynamicWorkflowStore(owner.directory / 'core.sqlite').get(owner.pid, owner.first['id'])
        check(reopened['node_projection'] == value['node_projection'])

    asyncio.run(run())


def test_unit_native_source_failed_core_commit_never_caches_final_evidence_as_saved(tmp_path, monkeypatch):
    owner, moment = UnitSourceOwner(tmp_path), clock(monkeypatch)
    owner.child_source = final_assistant()
    with sqlite3.connect(owner.dynamic_workflows.path) as connection:
        original = connection.execute('SELECT payload FROM dynamic_workflow_versions WHERE revision=1').fetchone()[0]
        connection.execute("UPDATE dynamic_workflow_versions SET payload='{}' WHERE revision=1")

    async def run():
        await workflow.reconcile_workflow(owner, owner.pid, owner.ident, owner.context, owner.root_source)
        check(not owner.context.get('workflow_final_calls') and not owner.context.get('workflow_observations_seen'))
        check(owner._state(owner.pid, owner.ident).get('workflow_observation_error') == 'WorkflowIntegrityError')
        with sqlite3.connect(owner.dynamic_workflows.path) as connection:
            connection.execute('UPDATE dynamic_workflow_versions SET payload=? WHERE revision=1', (original,))
        moment[0] += 1.1
        await workflow.reconcile_workflow(owner, owner.pid, owner.ident, owner.context, owner.root_source)
        check(owner.context['workflow_final_calls'], 'successful evidence was not acknowledged after recovery')
        check(node_projection(owner)['effective']['model']['effective'] == 'route')
        check(owner._state(owner.pid, owner.ident).get('workflow_observation_error') is None)

    asyncio.run(run())


def test_unit_native_source_missing_final_evidence_ends_bounded_observation_without_failure_claim(tmp_path, monkeypatch):
    owner, moment = UnitSourceOwner(tmp_path), clock(monkeypatch)

    async def run():
        await workflow.reconcile_workflow(owner, owner.pid, owner.ident, owner.context, owner.root_source)
        moment[0] += 11
        owner._update(owner.pid, owner.ident, active_operation_id=None)
        await workflow.reconcile_workflow(owner, owner.pid, owner.ident, owner.context, owner.root_source)
        check(not owner.context['workflow_evidence_pending'])
        projection = node_projection(owner)
        check(projection['execution_status'] == 'RETURNED' and projection['validation_status'] == 'NOT_OBSERVED')
        check(projection['effective']['model']['status'] == 'UNOBSERVED')
        check(not owner.context.get('workflow_final_calls'), 'missing evidence was cached as complete')
        check(all(call[0] == 'GET' for call in owner.http_calls))

    asyncio.run(run())


def test_exact_plan_token_admission_revokes_write_but_keeps_original_read_and_never_crosses_actor(tmp_path):
    owner = UnitSourceOwner(tmp_path)
    context, token = owner.context, 'Bearer ' + owner.context['workflow_plan_token']
    admitted = lambda **kw: workflow.admitted_workflow_owner(owner, token, plan=True, **kw) is not None
    check(admitted(), 'healthy active exact owner was rejected')
    context['expires_at'] = '2000-01-01T00:00:00+00:00'
    check(not admitted() and admitted(read_only=True))
    context['expires_at'] = '2076-01-01T00:00:00+00:00'
    owner.configuration_current = False
    check(not admitted() and admitted(read_only=True))
    owner.configuration_current = True
    owner._update(owner.pid, owner.ident, active_operation_id=None)
    check(not admitted() and admitted(read_only=True))
    owner._update(owner.pid, owner.ident, active_operation_id='original_message', generation=5)
    check(not admitted() and not admitted(read_only=True))
    owner._update(owner.pid, owner.ident, generation=4)
    event_token = 'Bearer ' + context['workflow_event_token']
    check(workflow.admitted_workflow_owner(owner, event_token, plan=True) is None)
    check(workflow.admitted_workflow_owner(owner, event_token, plan=False) is not None)
