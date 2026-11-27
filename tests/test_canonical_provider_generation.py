"""Provider revisions in one canonical chat; original CLI, fixture inference only."""
import asyncio
from copy import deepcopy
import os
from legacy_evidence import internal_output
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from apps.api.canonical_conversations import CanonicalConversations, ConversationMessage
from packages.agent_sessions.models import OpenConversation, operation_request
from packages.agent_sessions.service import AgentSessions
from apps.api.conversation_creation import ConversationCreation, CreateConversation
from apps.api.conversation_history import ConversationHistory
from apps.api.conversation_organization import OrganizationChange, register_conversation_organization
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_agent_sessions import scene
from test_canonical_conversations_cli import setup
from test_public_conversation_runtime_cli import returned
import native_capability_task_source64_fixture as fixture


async def failed(h, pid, sid, key):
    deadline = asyncio.get_running_loop().time() + 30
    while True:
        view = await h.sessions.inspect(pid, sid)
        op = h.sessions.operation(pid, sid, key)
        if view['active_operation_id'] is None and op['status'] == 'FAILED':
            return view
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(.05)


async def route_owner(h, directory, cid, *, expected_status=200):
    app = FastAPI()
    app.state = h.native.app.state
    register_conversation_organization(app, directory)
    @app.exception_handler(Conflict)
    async def conflict(request, error):
        return JSONResponse(status_code=409, content={'error': 'CONFLICT'})
    source = directory.threads.metadata_header(cid)['source']
    before = len(h.wire.requests)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated-test') as client:
        get = await client.get('/api/conversations/' + cid + '/scope')
        selection = await client.post('/api/conversations/' + cid + '/selection', json={'source': source})
    assert get.status_code == selection.status_code == expected_status
    assert len(h.wire.requests) == before
    evidence = os.environ.get('PROVIDER_GENERATION_EVIDENCE')
    if evidence:
        target = Path(evidence)
        target = internal_output(target)
        target.mkdir(parents=True, exist_ok=True)
        index = len(list(target.glob('*.scope.json')))
        for suffix, response in [('scope', get), ('selection', selection)]:
            with (target / (str(index).zfill(2) + '.' + suffix + '.json')).open('xb') as stream:
                stream.write(response.content)
    if expected_status == 200:
        assert get.json()['current_owner'] == selection.json()['current_owner']
        return get.json()['current_owner']


@pytest.fixture
def metadata_service(tmp_path):
    """Real canonical locks/journals; session I/O boundary has no SDK or model."""
    reference = {'id': 'native:' + 'c' * 64, 'revision': 1}
    scope = {'conversation_ref': reference, 'project_id': None, 'sources': []}
    profile = {'id': 'metadata-profile', 'revision': 1, 'sha256': 'a' * 64}
    views, manifests, operations = {}, {}, {}
    calls = {'open': 0, 'send': 0, 'stop': 0}

    def resolve(value):
        return SimpleNamespace(supports_images=False)

    def session_path(pid, sid, *parts):
        path = tmp_path / 'sessions' / pid / sid
        path.mkdir(parents=True, exist_ok=True)
        return path.joinpath(*parts)

    async def open_session(pid, request, **internal_admission):
        calls['open'] += 1
        sid = str(calls['open']).zfill(32)
        ref = {'id': sid, 'revision': 1, 'sha256': digest(request)}
        view = {'project_id': pid, 'session_ref': ref, 'generation': 1,
            'provider_profile_ref': request['provider_profile_ref'], 'configuration_current': True,
            'connected_owner': True, 'owner_status': 'READY', 'native_status': 'idle',
            'observation_is_current': True, 'active_operation_id': None,
            'pending_questions': [], 'pending_permissions': [], 'messages': []}
        views[sid], manifests[sid] = view, {'request': request}
        session_path(pid, sid, 'operations').mkdir()
        scope['sources'].append({'original_source': {'kind':'original',**CanonicalConversations._source(view)}})
        return deepcopy(view)

    async def inspect(pid, sid):
        return deepcopy(views[sid])

    async def send_session(pid, sid, body):
        calls['send'] += 1
        request = operation_request(body)
        operations[body.operation_id] = {'id': body.operation_id, 'kind': 'MESSAGE',
            'status': 'RETURNED', 'request': request, 'request_sha256': digest(request)}

    sessions = SimpleNamespace(data_dir=tmp_path, _write=AgentSessions._write,
        _read=AgentSessions._read, _path=session_path, closed=False,
        providers=SimpleNamespace(resolve=resolve), internal_project=lambda: 'execution-project',
        configuration=lambda: {'capability_binding_sha256': 'b' * 64}, open=open_session,
        get=lambda pid, sid: deepcopy(views[sid]), _manifest=lambda pid, sid: (manifests[sid], None),
        inspect=inspect, send=send_session, operation=lambda pid, sid, key: deepcopy(operations[key]))
    threads = SimpleNamespace(view=lambda cid: deepcopy(scope))
    app = SimpleNamespace(state=SimpleNamespace(agent_sessions=sessions, conversation_threads=threads,
        conversation_runtime=SimpleNamespace(native=SimpleNamespace(input_sources=SimpleNamespace(validate=lambda x: None)))))
    service = CanonicalConversations(app)
    # Ownership is supplied at the session-I/O boundary, not by a model substitute.
    service._bound = lambda source, cid: any(row['original_source'].get('kind')=='original' and all(row['original_source'][key]==source[key] for key in ('project_id','session_ref','generation')) for row in scope['sources'])
    return SimpleNamespace(service=service, scope=scope, profile=profile, views=views,
                           operations=operations, calls=calls)


def test_first_send_racing_another_profile_connection_cannot_reenter_dispatch(metadata_service, monkeypatch):
    h = metadata_service

    async def run():
        original_open = h.service.open
        reached, resume = asyncio.Event(), asyncio.Event()

        async def delayed_open(body):
            reached.set()
            await resume.wait()
            return await original_open(body)

        monkeypatch.setattr(h.service, 'open', delayed_open)
        task = asyncio.create_task(h.service.send(h.scope['conversation_ref']['id'],
            ConversationMessage(operation_id='racing_first_send', conversation_ref=h.scope['conversation_ref'],
                provider_profile_ref={**h.profile, 'revision': 2, 'sha256': 'e' * 64}, raw_text='Keep this input')))
        await asyncio.wait_for(reached.wait(), 2)
        await original_open(OpenConversation(operation_id='other_first_connect', project_id=None,
            conversation_ref=h.scope['conversation_ref'], provider_profile_ref=h.profile,
            capability_binding_sha256='b' * 64))
        resume.set()
        with pytest.raises(Conflict, match='latest proven original 401'):
            await asyncio.wait_for(task, 2)
        assert h.calls == {'open': 1, 'send': 0, 'stop': 0}
        assert not h.service._lock(('dispatch', h.scope['conversation_ref']['id'])).locked()
        assert not h.service._lock(('conversation', h.scope['conversation_ref']['id'])).locked()

    asyncio.run(run())


@pytest.mark.parametrize('status', ['RETURNED', 'UNKNOWN_OUTCOME'])
@pytest.mark.parametrize('head_state', ['PENDING', 'CORRUPT'])
def test_original_message_read_survives_unavailable_current_owner(metadata_service, status, head_state):
    h = metadata_service

    async def run():
        reference = h.scope['conversation_ref']
        await h.service.send(reference['id'], ConversationMessage(operation_id='saved_message',
            conversation_ref=reference, provider_profile_ref=h.profile, raw_text='Do not replay this input'))
        h.operations['saved_message']['status'] = status
        saved_operation = deepcopy(h.operations['saved_message'])
        path = h.service._path('current-owners', reference['id'])
        if head_state == 'PENDING':
            request = {'operation_id': 'pending_revision', 'conversation_ref': reference}
            h.service._save(path, {'kind': 'PROVIDER_GENERATION', 'request': request,
                'body_sha256': digest(request), 'provider_generation': 1, 'phase': 'PREPARING'})
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"invalid":"private-head-data-must-not-leak"}')
        for service in (h.service, CanonicalConversations(h.service.app)):
            result = service.operation(reference['id'], 'saved_message')
            assert result['status'] == status and result['operation'] == saved_operation
            assert result['can_continue'] is False
            assert result['current_owner_projection']['status'] == ('PENDING' if head_state == 'PENDING' else 'UNAVAILABLE')
            assert 'private-head-data' not in str(result)
            assert result['model_calls_by_query'] == 0 and result['owner_started_by_query'] is False
        assert h.calls == {'open': 1, 'send': 1, 'stop': 0}

    asyncio.run(run())


def test_actual_401_then_new_revision_keeps_chat_history_and_restart_authority(scene, monkeypatch):
    wire = fixture.CapabilityWire([{'_http_error': 401}, 'NEW_PROVIDER_REVISION_RETURNED']).start()

    async def run():
        h, directory, organization, service = setup(scene, wire)
        try:
            created = ConversationCreation(scene['store'], directory.threads).create(
                CreateConversation(operation_id='provider_chat', project_id=None))
            cid = created['conversation_ref']['id']
            scope = directory.threads.view(cid)
            body = dict(operation_id='connect_revision_one', project_id=None,
                conversation_ref=scope['conversation_ref'], provider_profile_ref=h.profile,
                capability_binding_sha256=h.sessions.configuration()['capability_binding_sha256'])
            old = await service.open(body)
            pid, sid = old['project_id'], old['session_ref']['id']
            h.contexts[sid] = h.sessions.active[sid]
            h.identities[sid] = h.sessions._state(pid, sid)['process_identity']
            old_process = h.sessions.active[sid]['process']
            await service.send(cid, ConversationMessage(operation_id='known_401',
                conversation_ref=scope['conversation_ref'], provider_profile_ref=h.profile,
                raw_text='Original failed input must remain', original_source=service._source(old)))
            await failed(h, pid, sid, 'known_401')
            before_failure = deepcopy(service.operation(cid, 'known_401'))
            assert before_failure['failure']['code'] == 'MODEL_AUTH_REQUIRED'
            manifest_path = h.sessions._path(pid, sid, 'manifest.json')
            old_manifest = manifest_path.read_bytes()
            old_config = deepcopy(h.config)
            new_config = {**old_config, 'revision': 2, 'secret_ref': 'secret:revision-two'}
            new_profile = {'id': new_config['id'], 'revision': 2, 'sha256': digest(new_config)}
            old_key, new_key = fixture.KEY, 'new-synthetic-private-fixture-key'
            h.sessions.providers.configuration_resolver = lambda ident, revision: deepcopy(
                {1: old_config, 2: new_config}[revision])
            h.sessions.providers.secret_resolver = lambda ref: new_key if ref == 'secret:revision-two' else old_key
            monkeypatch.setattr(fixture, 'KEY', new_key)
            request = {**body, 'operation_id': 'explicit_revision_two', 'provider_profile_ref': new_profile}
            assert (await route_owner(h, directory, cid))['status'] == 'LEGACY'
            observed = await h.sessions.inspect(pid, sid)
            for name, fields in {
                'active': {'active_operation_id': 'known_401'},
                'question': {'pending_questions': [{'request_ref': {'id': 'pending'}}]},
                'permission': {'pending_permissions': [{'request_ref': {'id': 'pending'}}]},
                'busy': {'native_status': 'busy'},
                'stale': {'observation_is_current': False},
                'observation_error': {'observation_error': {'error_type': 'Unknown'}},
                'not_closed': {'connected_owner': False, 'owner_status': 'BLOCKED_PROCESS_STOP_UNCONFIRMED'},
            }.items():
                async def view_fixture(*args, _fields=fields, **kwargs):
                    return {**deepcopy(observed), **_fields}
                with monkeypatch.context() as patch:
                    patch.setattr(h.sessions, 'inspect', view_fixture)
                    with pytest.raises(Conflict):
                        await service.open({**request, 'operation_id': 'reject_' + name})
                assert old_process.returncode is None and len(h.sessions.active) == 1
                assert not service._path('current-owners', cid).exists()
            operation_path = h.sessions._path(pid, sid, 'operations', digest('known_401') + '.json')
            original_op = h.sessions.operation(pid, sid, 'known_401')
            h.sessions._write(operation_path, {**original_op, 'status': 'UNKNOWN_OUTCOME'}, replace=True)
            try:
                with pytest.raises(Conflict):
                    await service.open({**request, 'operation_id': 'reject_unknown_even_idle'})
            finally:
                h.sessions._write(operation_path, original_op, replace=True)
            for name, fields in {
                'grant': {'allowed_effects': ['PROCESS_START']},
                'budget': {'max_calls': 25},
                'other_id': {'provider_profile_ref': {**new_profile, 'id': 'another-provider'}},
            }.items():
                with pytest.raises(Conflict):
                    await service.open({**request, 'operation_id': 'reject_' + name, **fields})
            # Lose only final publication, after the real new CLI owner exists.
            save, lost = service._save, False
            head_path = service._path('current-owners', cid)
            def lose_publication(path, value, **options):
                nonlocal lost
                if path == head_path and value['phase'] == 'BOUND' and not lost:
                    lost = True
                    raise OSError('Injected current-owner publication acknowledgement loss')
                return save(path, value, **options)
            service._save = lose_publication
            try:
                with pytest.raises(OSError):
                    await service.open(request)
            finally:
                service._save = save
            assert (await route_owner(h, directory, cid))['status'] == 'PENDING'
            pending_original = service.operation(cid, 'known_401')
            assert pending_original['status'] == 'FAILED' and pending_original['failure']['http_status'] == 401
            assert pending_original['can_continue'] is False
            assert pending_original['current_owner_projection'] == {'status': 'PENDING'}
            assert service.open_operation(request['operation_id'])['conversation_bound'] is False
            with pytest.raises(Conflict):
                await CanonicalConversations(h.native.app).open({**request, 'operation_id': 'cannot_bypass_pending'})
            active_before_recovery = dict(h.sessions.active)
            new = await service.open(request)  # Same frozen connection, not a new owner or message.
            assert h.sessions.active == active_before_recovery
            new_pid, new_sid = new['project_id'], new['session_ref']['id']
            h.contexts[new_sid] = h.sessions.active[new_sid]
            h.identities[new_sid] = h.sessions._state(new_pid, new_sid)['process_identity']
            assert new_sid != sid and new['provider_profile_ref'] == new_profile
            assert old_process.returncode is not None and sid not in h.sessions.active
            assert manifest_path.read_bytes() == old_manifest
            assert service.operation(cid, 'known_401')['failure'] == before_failure['failure']
            assert service.operation(cid, 'known_401')['operation'] == before_failure['operation']
            assert len(wire.requests) == 1
            current = CanonicalConversations(h.native.app).current_owner(directory.threads.view(cid))
            assert current['status'] == 'CURRENT' and current['original_source']['session_ref'] == new['session_ref']
            assert current['provider_profile_ref'] == new_profile
            assert (await route_owner(h, directory, cid)) == current
            assert (await service.open(request))['session_ref'] == new['session_ref']
            assert service.open_operation(request['operation_id'])['conversation_bound'] is True
            assert service.operation(cid, 'known_401')['can_continue'] is False
            with pytest.raises(Conflict):
                await service.send(cid, ConversationMessage(operation_id='stale_old_owner',
                    conversation_ref=scope['conversation_ref'], provider_profile_ref=h.profile,
                    raw_text='Must not run', original_source=service._source(old)))
            await service.send(cid, ConversationMessage(operation_id='explicit_corrected_input',
                conversation_ref=scope['conversation_ref'], provider_profile_ref=new_profile,
                raw_text='This is the new explicit input', original_source=service._source(new)))
            await returned(h, new_pid, new_sid, 'explicit_corrected_input')
            result = service.operation(cid, 'explicit_corrected_input')
            assert result['status'] == 'RETURNED' and result['response']['text'] == 'NEW_PROVIDER_REVISION_RETURNED'
            rows = ConversationHistory(h.native.app, threads=directory.threads).collect(directory.threads.view(cid))[0]
            assert any(row.get('operation_id') == 'known_401' and row.get('failure_code') == 'MODEL_AUTH_REQUIRED' for row in rows)
            assert any(row.get('operation_id') == 'explicit_corrected_input' for row in rows)
            assert len(wire.requests) == 2 and scene['actual'].read_bytes() == scene['original']
            assert h.sessions.get(pid, sid)['model_calls_reserved'] == 1
            assert h.sessions.get(new_pid, new_sid)['model_calls_reserved'] == 1
            # Organization changes neither select an alias by order nor redirect execution.
            before_move = directory.threads.view(cid)
            target = scene['store'].create_project('New organization', 'No execution authority by moving')['id']
            organization.change(cid, OrganizationChange(operation_id='move_provider_chat', action='move',
                expected_revision=before_move['conversation_ref']['revision'], source=before_move['metadata_source'],
                target_project_id=target))
            moved = directory.threads.view(cid)
            assert moved['project_id'] == target
            moved_owner = await route_owner(h, directory, cid)
            assert moved_owner['original_source']['session_ref'] == new['session_ref']
            assert moved_owner['original_source']['project_id'] == new_pid
            with pytest.raises(Conflict):
                service.current_owner(before_move)
            frozen_head = head_path.read_bytes()
            head_path.write_bytes(b'{"corrupt":true}')
            try:
                with pytest.raises(Conflict):
                    CanonicalConversations(h.native.app).current_owner(directory.threads.view(cid))
                await route_owner(h, directory, cid, expected_status=409)
                for key in ('known_401', 'explicit_corrected_input'):
                    saved = service.operation(cid, key)
                    assert saved['status'] == ('FAILED' if key == 'known_401' else 'RETURNED')
                    assert saved['can_continue'] is False
                    assert saved['current_owner_projection'] == {'status': 'UNAVAILABLE', 'code': 'CURRENT_OWNER_UNAVAILABLE'}
                    if key == 'known_401':
                        assert saved['failure']['http_status'] == 401
                        assert saved['response']['source'] == 'SAVED_ORIGINAL_AUTH_REJECTION'
                assert len(wire.requests) == 2
            finally:
                head_path.write_bytes(frozen_head)
            other = ConversationCreation(scene['store'], directory.threads).create(
                CreateConversation(operation_id='different_provider_chat', project_id=None))
            foreign_path = service._path('current-owners', other['conversation_ref']['id'])
            foreign_path.parent.mkdir(parents=True, exist_ok=True)
            foreign_path.write_bytes(frozen_head)
            with pytest.raises(Conflict):
                service.current_owner(directory.threads.view(other['conversation_ref']['id']))
        finally:
            await h.close('provider-generation')

    try:
        asyncio.run(run())
    finally:
        wire.close()
