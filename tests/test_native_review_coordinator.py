"""Real controller/Store/queue and external SDK/OS facets, never CLI qualification."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.api.native_messages import NativeMessages
from packages.agent_sessions.models import OpenSession
from packages.agent_sessions.native_review import NativeReviews
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.provider_settings import ProviderSettings
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_agent_sessions_deadline_recovery import Scene, OwnedProcessFacet, until, clock
from test_native_collaboration import HostLifecycleCounter
from test_native_review import FrozenPixels, TOKEN, ROOT, check


class BridgeFacet:
    def close(self): pass


def setup(directory):
    fixture = Scene(directory)
    sessions = fixture.owner
    skill = fixture.caps.import_skill({'operation_id': 'review-skill', 'id': 'eeas-acceptance-review',
        'expected_revision': 0, 'content': (ROOT / 'packages/planning/skills/acceptance-review/SKILL.md').read_text(), 'enabled': True})['config_ref']
    binding = fixture.caps.native_binding_snapshot()
    authority = NativeCollaborationAuthority(fixture.data, token=TOKEN, owner_pid=123, native=HostLifecycleCounter(), platform='posix')
    app = SimpleNamespace(state=SimpleNamespace(store=fixture.store, settings=SimpleNamespace(data_dir=fixture.data),
        native_capabilities=fixture.caps, provider_settings=ProviderSettings(fixture.store, fixture.data, ROOT)))
    native = NativeMessages(app, authority, FrozenPixels())
    reviews = NativeReviews(native, sessions); native.reviews = reviews
    selection = {'skill_ref': skill, 'tool_selection': None, 'basis_ref': None, 'capability_binding_sha256': binding['sha256']}
    request = {'operation_id': 'parent-page', 'project_id': fixture.pid, 'conversation_id': 'unit-discussion',
        'provider_profile_ref': fixture.profile_ref, 'context_ref': native.context.context_ref,
        'raw_text': 'Check only this frozen page.', 'file_source_ref': None, 'file_link_ref': None,
        'native_review': {'version': 1, 'selection': selection, 'process_start_consent': False}}
    key = reviews._channel_key(request)
    admission = {'key': reviews._admission_key(request, selection), 'project_id': fixture.pid,
        'conversation_id': request['conversation_id'], 'provider_profile_ref': fixture.profile_ref,
        'selection': selection, 'source_host_generation': native.generation}
    aid = digest(admission); reviews.write('admissions', aid, admission)
    aref = {'id': aid, 'revision': 1, 'sha256': digest(admission)}
    directory = sessions._path(fixture.pid, fixture.sid, 'runtime-project'); directory.mkdir()
    _, identity = fixture.works._root(str(directory))
    manifest = fixture.manifest
    manifest.update(native_review_admission=aref, directory_identity=identity, workspace_kind='APP_INTERNAL_READONLY',
        capability_configuration_refs=binding['value']['configuration_refs'])
    manifest['request'] = OpenSession.model_validate({'operation_id': 'unit-review-owner', 'provider_profile_ref': fixture.profile_ref,
        'capability_binding_sha256': binding['sha256'], 'max_calls': 24, 'max_output_tokens': 1024,
        'ttl_seconds': 600}).model_dump(exclude={'operation_id'})
    sessions._write(sessions._path(fixture.pid, fixture.sid, 'manifest.json'), manifest, replace=True)
    fixture.ref = sessions._manifest(fixture.pid, fixture.sid)[1]
    fixture.context.update(directory=str(directory), native_review={'bridge': BridgeFacet()})
    fixture.sdk.directory = str(directory)
    owner = {'project_id': fixture.pid, 'session_ref': fixture.ref, 'generation': 3, 'admission_ref': aref, 'key': admission['key']}
    reviews.write('channels', key, {'key': key, 'owner': owner, 'active': None, 'queue': [], 'reviews_admitted': 0})
    return fixture, native, reviews, request


def test_get_and_inspect_do_not_dispatch_queue_unknown_blocks_and_close_retains_queued_inputs(tmp_path, clock):
    async def run():
        f, native, reviews, body = setup(tmp_path)
        try:
            first = await native.submit(body, TOKEN)
            check(first['status'] == 'PROCESSING' and f.sdk.post_count('/prompt_async') == 1)
            for index in range(4):
                queued = await native.submit({**body, 'operation_id': 'queued-' + str(index), 'raw_text': 'Frozen queued page ' + str(index)}, TOKEN)
                check(queued['review']['phase'] == 'QUEUED' and queued['review']['queue_position'] == index + 1)
            full = await native.submit({**body, 'operation_id': 'too-many'}, TOKEN)
            check(full['error']['code'] == 'REVIEW_QUEUE_FULL' and full['dispatch_state'] == 'NOT_DISPATCHED')
            parent = reviews._record(body['operation_id'])
            original = f.owner.operation(f.pid, f.sid, parent['native_operation_id'])
            f.sdk.terminal(original['native_message_id'], error=False)
            await f.inspect()
            for _ in range(3): reviews.get(body['operation_id']); reviews.get('queued-0')
            # Threaded catalog is the actual synchronous HTTP projection seam.
            hint = await asyncio.to_thread(reviews.catalog, project_id=f.pid, conversation_id=body['conversation_id'],
                provider_profile_ref=f.profile_ref, selection=body['native_review']['selection'])
            check(hint['permission_hint']['owner_ref'] == f.ref)
            await asyncio.sleep(.03)
            check(f.sdk.post_count('/prompt_async') == 1, 'GET/inspect indirectly dispatched the queue')
            f.sdk.fail_prompt = True
            # External original event boundary, not a substituted coordinator.
            f.context['native_review_terminal_event'] = True
            monitor = asyncio.create_task(f.owner._monitor(f.pid, f.sid, f.context)); f.tasks.append(monitor)
            await until(lambda: f.sdk.post_count('/prompt_async') == 2)
            await until(lambda: reviews.get('queued-0')['status'] == 'UNKNOWN_OUTCOME')
            f.sdk.terminal(f.owner.operation(f.pid, f.sid, 'review-message-queued-0')['native_message_id'], error=False, identifier='msg_final_later')
            await f.inspect(); reviews.get('queued-0'); await asyncio.sleep(.2)
            check(f.sdk.post_count('/prompt_async') == 2, 'Idle after unknown dispatch released another page')
            cancelled = await reviews.cancel('queued-1')
            check(cancelled['status'] == 'CANCELLED' and not cancelled['execution_started'])
            await native.close(); await f.owner.close()
            check(reviews.get('queued-2')['status'] == 'CANCELLED')
            check(f.context['process'].terminated == 1 and not reviews.tasks)
            check(f.sdk.post_count('/prompt_async') == 2)
        finally:
            await native.close(); await f.owner.close(); await f.close()
    asyncio.run(run())


def test_guidance_and_question_controls_bind_original_parent_are_idempotent_and_stop_blocks_new_guidance(tmp_path, clock):
    async def run():
        f, native, reviews, body = setup(tmp_path)
        try:
            result = await native.submit(body, TOKEN)
            control = {'operation_id': 'same-page-guide', 'expected_review_ref': result['review']['review_ref'], 'raw_text': 'Check the missing acceptance detail.'}
            guided = await reviews.control(body['operation_id'], control, kind='GUIDANCE')
            check(guided['operation_id'] == control['operation_id'] and guided['status'] == 'ACCEPTED')
            count = len(f.sdk.posts)
            check(await reviews.control(body['operation_id'], control, kind='GUIDANCE') == guided and len(f.sdk.posts) == count)
            with pytest.raises(Conflict):
                await reviews.control(body['operation_id'], {**control, 'raw_text': 'changed'}, kind='GUIDANCE')
            bad = {**control, 'operation_id': 'wrong-page', 'expected_review_ref': {**result['review']['review_ref'], 'sha256': '0' * 64}}
            with pytest.raises(Conflict): await reviews.control(body['operation_id'], bad, kind='GUIDANCE')
            with pytest.raises(Conflict):
                await f.owner.guidance.submit(f.pid, f.sid, f.body('bypass', raw_text='Unbound input', expected_active_operation_id='review-message-parent-page'))
            original = f.owner.operation(f.pid, f.sid, 'review-message-parent-page')
            f.sdk.tool_wait(original['native_message_id'], 'QUESTION')
            await f.inspect()
            pending = reviews.get(body['operation_id'])['review']['pending_questions']
            check(len(pending) == 1)
            answered = await reviews.control(body['operation_id'], {'operation_id': 'answer-once',
                'expected_review_ref': result['review']['review_ref'], 'request_ref': pending[0]['request_ref'], 'answers': [['Yes']]},
                kind='QUESTION', request_id=pending[0]['request_ref']['id'])
            check(answered['status'] == 'RETURNED')
            await reviews.cancel(body['operation_id'])
            check(not reviews.get(body['operation_id'])['review']['can_guide'])
            check(f.sdk.post_count('/prompt_async') == 2)
        finally:
            await native.close(); await f.owner.close(); await f.close()
    asyncio.run(run())


def test_start_failure_persists_exact_owner_before_process_attempt_and_refuses_unconfirmed_replacement(tmp_path, clock):
    async def run():
        f, native, reviews, body = setup(tmp_path)
        try:
            body = {**body, 'operation_id': 'known-start-failure', 'conversation_id': 'new-explicit-discussion'}
            result = await native.submit(body, TOKEN)
            check(result['status'] == 'FAILED' and result['dispatch_state'] == 'NOT_DISPATCHED')
            check(result['owner_start_attempted'] and result['owner_stop_confirmed'])
            channel = reviews.read('channels', reviews._channel_key(body)); owner = channel['owner']
            check(owner and owner['generation'] == 1 and owner['session_ref']['id'] != f.sid)
            check(not f.sdk.posts, 'A startup failure must not reach the original prompt/reply API')
            check(await native.submit(body, TOKEN) == result)
            state = f.owner._state(owner['project_id'], owner['session_ref']['id'])
            state.update(owner_status='BLOCKED_PROCESS_STOP_UNCONFIRMED', process_identity={'pid': 999999, 'created': 1})
            f.owner._write(f.owner._path(owner['project_id'], owner['session_ref']['id'], 'state.json'), state, replace=True)
            blocked = await native.submit({**body, 'operation_id': 'explicit-new-but-unconfirmed'}, TOKEN)
            check(blocked['error']['code'] == 'REVIEW_OWNER_EXPIRED' and not blocked['execution_started'])
            check(reviews.read('channels', reviews._channel_key(body))['owner'] == owner)
            check(not f.sdk.posts)
        finally:
            await native.close(); await f.owner.close(); await f.close()
    asyncio.run(run())


@pytest.mark.parametrize('confirmed', [True, False])
def test_unknown_review_stop_is_one_exact_control_preserves_unknown_and_never_replays_queue(tmp_path, clock, confirmed):
    async def run():
        f, native, reviews, body = setup(tmp_path)
        unconfirmed_process = None
        try:
            f.sdk.fail_prompt = True
            first = await native.submit(body, TOKEN)
            check(first['status'] == 'UNKNOWN_OUTCOME' and first['review']['can_cancel'])
            queued_body = {**body, 'operation_id': 'retained-queued-page', 'raw_text': 'Preserve this exact later-page input.'}
            await native.submit(queued_body, TOKEN)
            before = f.owner.operation(f.pid, f.sid, 'review-message-parent-page')
            before_request = deepcopy(reviews._record('retained-queued-page')['request'])
            if not confirmed:
                class UnconfirmedProcess(OwnedProcessFacet):
                    def terminate(self): self.terminated += 1
                    async def wait(self): await asyncio.sleep(30)
                unconfirmed_process = UnconfirmedProcess()
                f.context['process'] = unconfirmed_process
                f.owner._update(f.pid, f.sid, process_identity={'pid': 99999999, 'created': 1})
            stopped = await reviews.cancel(body['operation_id'])
            check(stopped['status'] == 'UNKNOWN_OUTCOME' and stopped['owner_stop_confirmed'] is confirmed)
            stop = stopped['review']['stop_control']
            check(stop['kind'] == 'OWNER_STOP' and stop['status'] == ('RETURNED' if confirmed else 'UNKNOWN_OUTCOME'))
            check(f.owner.operation(f.pid, f.sid, 'review-message-parent-page') == before,
                'Stopping must not rewrite the immutable unknown native prompt')
            count = len(f.sdk.posts)
            check((await reviews.cancel(body['operation_id']))['review']['stop_control'] == stop)
            check(reviews.control_get(body['operation_id'], stop['operation_id']) == stop)
            check(len(f.sdk.posts) == count and f.sdk.post_count('/prompt_async') == 1, 'Unknown stop or queued input was replayed')
            queued = reviews.get('retained-queued-page')
            check(reviews._record('retained-queued-page')['request'] == before_request)
            if confirmed:
                check(queued['dispatch_state'] == 'NOT_DISPATCHED' and queued['review']['phase'] == 'NEEDS_PERMISSION')
            else:
                check(queued['review']['phase'] == 'QUEUED')
                blocked = await native.submit({**body, 'operation_id': 'no-owner-replacement'}, TOKEN)
                check(blocked['error']['code'] == 'REVIEW_OWNER_EXPIRED')
            check(f.sdk.post_count('/prompt_async') == 1)
        finally:
            # The failure injection represents a temporarily unresponsive owned process.
            # Its OS facet confirms natural exit only after checking the unknown-stop receipt.
            if unconfirmed_process is not None:
                unconfirmed_process.returncode = 0
            await native.close(); await f.owner.close(); await f.close()
    asyncio.run(run())
