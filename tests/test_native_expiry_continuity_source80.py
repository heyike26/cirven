"""Real expiry/records/rework; controlled SDK and explicit inputs, no live model/CLI."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest

from apps.api.desktop_context import DesktopContextRPC
from apps.api.native_messages import NativeMessages
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.agent_sessions.native_message import NativeMessageSessions
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.tasks import digest
from test_native_collaboration import HostLifecycleCounter
from test_native_message_sessions52 import scene, initial, finish
from test_agent_sessions_deadline_recovery import clock, until
from test_native_messages import ROOT, TOKEN
from test_native_rework import call, check, feedback, ref, source
from test_native_rework_producer import export, producer_scene, task_path


def test_expiry_records_safe_stop_and_reads_do_not_renew_or_rewrite(tmp_path, clock):
    async def run():
        f = await scene(tmp_path)
        try:
            await initial(f)
            c, parent = f.coordinator, f.body_original['operation_id']
            before = c._record(parent)
            request_hash = before['request_sha256']
            admission_bytes = c.path('admissions', parent).read_bytes()
            deadline = c.deadline(f.manifest52)
            clock.instant = datetime.fromisoformat(deadline) + timedelta(seconds=1)
            task = f.monitor()
            await until(lambda: f.sid not in f.owner.active)
            await task
            await c._owner_changed(f.pid, f.sid)
            result = f.native.get(parent)
            original = f.owner.operation(f.pid, f.sid, before['native_operation_id'])
            check(result['status'] == original['status'] == 'UNKNOWN_OUTCOME')
            check(original['owner_stop_status'] == 'EXPIRED' and f.state()['owner_status'] == 'EXPIRED')
            check(result['native_execution']['stop']['owner_stop_status'] == 'EXPIRED')
            check(result['native_execution']['stop']['reason'] == 'OWNER_DISCONNECTED_NO_POST_REPLAY')
            # This original fixture has no created_at: absence must not be reconstructed.
            budget = result['native_execution']['budget']
            check('admitted_at' not in budget and 'startup_elapsed_seconds' not in budget)
            check(budget['absolute_deadline'] == deadline)
            snapshot = f.native.path(parent).read_bytes()
            for _ in range(3):
                check(f.native.get(parent)['native_execution'] == result['native_execution'])
            check(f.native.path(parent).read_bytes() == snapshot)
            check(c._record(parent)['request_sha256'] == request_hash)
            check(c.path('admissions', parent).read_bytes() == admission_bytes)
            check(f.sdk.post_count('/prompt_async') == 1 and f.context['expires_at'] == deadline)
            check(not any(result['control_capabilities'].values()))
            f.native.generation = 'new-host-readonly-observation'
            historical = f.native.get(parent)
            check(historical['record_origin'] == 'HISTORICAL_READONLY')
            check(historical['native_execution'] == result['native_execution'])
            check(not any(historical['control_capabilities'].values()))
            check(f.native.path(parent).read_bytes() == snapshot)
        finally:
            await finish(f)
    asyncio.run(run())


def test_projection_uses_exact_recorded_budget_and_does_not_infer_legacy_expiry():
    started = datetime(2026, 10, 19, tzinfo=timezone.utc)
    body = {'operation_id': 'parent'}
    admission = {'parent_operation_id': 'parent', 'body': body, 'body_sha256': digest(body),
        'request': {'budget': {'timeout_seconds': 57}}, 'created_at': started.isoformat(),
        'absolute_deadline': (started + timedelta(seconds=57)).isoformat()}
    admission['request_sha256'] = digest(admission['request'])
    owner = {'session_ref': {'id': 'owner', 'revision': 1, 'sha256': 'a' * 64}, 'generation': 3}
    record = {'request': body, 'request_sha256': digest(body), 'owner': owner,
        'native_operation_id': 'ordinary-original',
        'native_message_ref': {'id': 'parent', 'revision': 1, 'sha256': digest(admission)}}
    op = {'id': 'ordinary-original', **owner, 'reason': 'OWNER_DISCONNECTED_NO_POST_REPLAY',
        'dispatch_at': (started + timedelta(seconds=12)).isoformat(), 'deadline': admission['absolute_deadline'],
        'updated_at': (started + timedelta(seconds=58)).isoformat(),
        'error_type': 'PRIVATE_DIAGNOSTIC', 'observation_error': {'secret': 'PRIVATE_DIAGNOSTIC'}}
    value = NativeMessageSessions._execution_observation(record, op, admission)
    check('owner_stop_status' not in value['stop'])
    check(value['budget']['timeout_seconds'] == 57 and value['budget']['startup_elapsed_seconds'] == 12)
    check(value['budget']['remaining_at_dispatch_seconds'] == 45)
    check('PRIVATE_DIAGNOSTIC' not in json.dumps(value))
    for field in ('session_ref', 'generation', 'id'):
        bad = deepcopy(op); bad[field] = None
        check(NativeMessageSessions._execution_observation(record, bad, admission) is None)
    changed = deepcopy(admission); changed['request']['budget']['timeout_seconds'] = 9999
    check('budget' not in NativeMessageSessions._execution_observation(record, op, changed))
    check('stop' not in NativeMessageSessions._execution_observation(record, {**op, 'reason': 'PRIVATE_DIAGNOSTIC'}, admission))


@pytest.mark.parametrize('target_changed', [False, True])
def test_expired_parent_keeps_actual_preview_and_apply_source_guards(producer_scene, target_changed):
    s = producer_scene
    exported = export(s); path = task_path(s, exported); bpath = task_path(s, exported, 'b')
    original = (s['root'] / path).read_text(); independent = (s['root'] / bpath).read_bytes()
    feedback(s, path=path, selection={'kind': 'EXACT_QUOTE', 'quote': 'A 原已确认要求'})
    authority = NativeCollaborationAuthority(s['data_dir'] / 'expiry-own-host', token=TOKEN,
        owner_pid=123, native=HostLifecycleCounter(), platform='posix')
    native = NativeMessages(s['client'].app, authority, DesktopContextRPC(authority))
    profile = {'schema': 'eeas.opencode-provider.v1', 'id': 'explicit-unit', 'revision': 1,
        'version': VERSION, 'source_commit': COMMIT,
        'executable': str(ROOT / 'var/opencode-ui-offline-guard/opencode'), 'executable_sha256': CLI_SHA['posix'],
        'provider_id': 'eeas-expiry-unit', 'protocol': 'OPENAI_COMPATIBLE_CHAT_COMPLETIONS',
        'provider_url': 'http://127.0.0.1:9/v1', 'model_id': 'unit-model',
        'secret_ref': 'secret:unit-only', 'transport_kind': 'MOCK_WIRE'}
    native.transport.provider_registry = OpenCodeProviderRegistry(lambda *_: deepcopy(profile), lambda _: 'unit-only')
    body = {'operation_id': 'expiry-original', 'project_id': s['pid'], 'raw_text': '只准备选句的局部修订提案。',
        'file_source_ref': source(s, path), 'provider_profile_ref': {'id': profile['id'], 'revision': 1, 'sha256': digest(profile)}}
    record, request = native._prepare_ordinary(body, TOKEN)
    binding = {'request': {'operation_id': body['operation_id'], 'prompt': body['raw_text']}}
    context = native.read_rework_context({}, binding)
    mapped = native.resolve_native_feedback({'native_feedback_ref': context['feedback'][-1]['feedback_ref'],
        'proposal_ref': ref(s['proposal'])}, binding)
    plan = native.preview_native_rework({'mapping_ref': mapped['mapping_ref'], 'classification_kind': 'CONSTRAINT',
        'rationale': '仅修改明确选句，保持人工内容。', 'proposed_value': 'A 到期后仍可审阅的修订'}, binding)
    sessions = s['client'].app.state.agent_sessions
    coordinator = NativeMessageSessions(native, sessions); native.ordinary = coordinator
    # Durable closed-owner facet; no process/model was dispatched by this fixture.
    admission = {'parent_operation_id': body['operation_id'], 'body': record['request'],
        'body_sha256': record['request_sha256'], 'request': request, 'request_sha256': digest(request),
        'absolute_deadline': datetime.now(timezone.utc).isoformat()}
    admission_ref = {'id': body['operation_id'], 'revision': 1, 'sha256': digest(admission)}
    coordinator.write('admissions', body['operation_id'], admission)
    sid = uuid.uuid4().hex
    manifest = {'id': sid, 'project_id': s['pid'], 'native_message_admission': admission_ref}
    sessions._write(sessions._path(s['pid'], sid, 'manifest.json'), manifest)
    session_ref = sessions._manifest(s['pid'], sid)[1]
    sessions._write(sessions._path(s['pid'], sid, 'state.json'), {'generation': 1, 'owner_status': 'EXPIRED'})
    operation_id = 'ordinary-send-' + digest(body['operation_id'])
    sessions._write(sessions._path(s['pid'], sid, 'operations', digest(operation_id) + '.json'), {
        'id': operation_id, 'session_ref': session_ref, 'generation': 1, 'status': 'UNKNOWN_OUTCOME',
        'reason': 'OWNER_DISCONNECTED_NO_POST_REPLAY', 'owner_stop_status': 'EXPIRED'})
    saved = coordinator._record(body['operation_id'])
    saved.update(native_message_ref=admission_ref, native_operation_id=operation_id,
        owner={'project_id': s['pid'], 'session_ref': session_ref, 'generation': 1})
    native.ws._write(native.path(body['operation_id']), saved)
    before = native.path(body['operation_id']).read_bytes()
    observed = native.get(body['operation_id'])
    check(observed['status'] == 'UNKNOWN_OUTCOME' and observed['rework_previews'] == [mapped, plan])
    check(observed['native_execution']['stop']['owner_stop_status'] == 'EXPIRED')
    human = '人工前言😀\r\n' + original + '\n人工后记保留。\n'
    if target_changed:
        human = human.replace('A 原已确认要求', '人工已重写目标')
    (s['root'] / path).write_bytes(human.encode())
    applied = call(s, 'POST', '/native-rework/plans/' + plan['plan_ref']['id'] + '/apply', {
        'operation_id': 'human-expired-plan-apply', 'plan_ref': plan['plan_ref'],
        'expected_current_sources': [source(s, path)], 'allow_rebase': True}, codes=(409,) if target_changed else (200,))
    check(applied['status'] == ('CONFLICT' if target_changed else 'APPLIED'))
    expected = human if target_changed else human.replace('A 原已确认要求', 'A 到期后仍可审阅的修订')
    check((s['root'] / path).read_bytes() == expected.encode())
    check((s['root'] / bpath).read_bytes() == independent)
    check(native.path(body['operation_id']).read_bytes() == before)
    check(native.tasks == {} and native.transport.active == {})
    asyncio.run(native.close())
