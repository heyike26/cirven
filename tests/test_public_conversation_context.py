"""Real ASGI/SQLite/source wrappers; saved SDK observations here are UNIT only."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest
from test_conversation_directory import api, check, project, unit_session
from test_conversation_threads import organization, scope, item


def registered(api):
    owner, a, sid, _ = unit_session(api, guided=False)
    b = api.post('/api/projects', json={'name': 'Target project', 'description': 'TARGET_ORIGINAL_PROJECT_CONTEXT'}).json()['id']
    row = api.get('/api/conversations').json()['items'][0]
    check(organization(api, row, 'move', 'public_context_move', target_project_id=b).status_code == 200)
    row = item(api, row['id'])
    check(organization(api, row, 'archive', 'public_context_archive').status_code == 200)
    selected = scope(api, row['id']); sid_b = 'b' * 32
    manifest = {'id': sid_b, 'project_id': b, 'workspace_kind': 'APP_INTERNAL_READONLY',
        'request': {'workspace_ref': None, 'provider_profile_ref': None,
                    'conversation_ref': selected['conversation_ref'], 'max_calls': 24,
                    'workflow_ref': None, 'allowed_effects': []}}
    owner._write(owner._path(b, sid_b, 'manifest.json'), manifest)
    owner._write(owner._path(b, sid_b, 'state.json'), {'generation': 1, 'owner_status': 'DISCONNECTED',
        'native_status': 'idle', 'original_session_id': 'ses_public_context', 'messages': [],
        'pending_questions': [], 'pending_permissions': [], 'active_operation_id': None})
    runtime = api.app.state.conversation_runtime
    runtime.bind_open(manifest)
    reference = owner._manifest(b, sid_b)[1]
    return runtime, owner, a, b, sid_b, manifest, reference, selected


def test_original_owner_binding_preserves_old_source_and_freezes_target_context_on_its_original_operation(api):
    runtime, owner, a, b, sid, manifest, reference, selected = registered(api)
    original = deepcopy(manifest)
    op, _ = owner._claim(b, sid, 'public_user', 'MESSAGE', {'operation_id': 'public_user',
        'session_ref': reference, 'generation': 1, 'raw_text': 'Continue here; literal {"user":true}',
        'agent': 'build', 'conversation_ref': selected['conversation_ref']})
    context = {'profile': SimpleNamespace(supports_images=True), 'generation': 1, 'session_id': 'ses_public_context'}
    runtime.prepare(manifest, context, op)
    current = owner.operation(b, sid, 'public_user'); data = current['conversation_context']
    check(data['scope']['project_id'] == b and data['scope']['project_context']['snapshot']['description'] == 'TARGET_ORIGINAL_PROJECT_CONTEXT')
    wire = str(data['history'])
    check('OLD_FIRST_RESPONSE' in wire and '只读核对原任务' in wire and 'Continue here' not in wire)
    check(current['request']['raw_text'] == op['request']['raw_text'] and not data['execution_scope_changed'])
    check(owner._read(owner._path(b, sid, 'manifest.json')) == original and not owner.active)
    check(scope(api, selected['conversation_ref']['id'])['archived'] is False)
    check(scope(api, selected['conversation_ref']['id'])['conversation_ref']['revision'] == selected['conversation_ref']['revision'] + 1)
    # Own metadata unarchiving does not rewrite the admitted scope/ref or old A source.
    check(data['scope']['conversation_ref'] == selected['conversation_ref'])
    with api.app.state.store.connect() as connection:
        check(connection.execute('SELECT count(*) FROM conversation_message_admissions').fetchone()[0] == 1)
    runtime.prepare(manifest, context, current)
    with api.app.state.store.connect() as connection:
        check(connection.execute('SELECT count(*) FROM conversation_message_admissions').fetchone()[0] == 1)


def test_public_payload_keeps_user_text_and_uses_typed_display_instead_of_host_context_json(api):
    runtime, owner, _, b, sid, manifest, reference, selected = registered(api)
    raw = 'Exact user text {"keep my JSON":true}'
    op, _ = owner._claim(b, sid, 'display_user', 'MESSAGE', {'operation_id': 'display_user',
        'session_ref': reference, 'generation': 1, 'raw_text': raw, 'agent': 'build',
        'conversation_ref': selected['conversation_ref']})
    context = {'profile': SimpleNamespace(supports_images=True), 'generation': 1, 'session_id': 'ses_public_context'}
    runtime.prepare(manifest, context, op)
    owner._finish(b, sid, 'display_user', 'DISPATCHING', native_message_id='msg_context_user')
    payload = runtime.dispatch_payload(manifest, context, owner.operation(b, sid, 'display_user'),
        {'messageID': 'msg_context_user', 'parts': [{'type': 'text', 'text': raw}]})
    check(payload['parts'][-1]['text'] == raw and 'TARGET_ORIGINAL_PROJECT_CONTEXT' in payload['parts'][0]['text'])
    message = {'info': {'id': 'msg_context_user', 'role': 'user', 'sessionID': context['session_id']},
        'parts': [{**part, 'id': 'part_' + str(index), 'messageID': 'msg_context_user', 'sessionID': context['session_id']}
                  for index, part in enumerate(payload['parts'])]}
    public = runtime.public_messages(manifest, context, [message])[0]
    check([part['text'] for part in public['parts']] == [raw] and public['conversation_input_origin']['operation_id'] == 'display_user')
    wrong = deepcopy(message); wrong['parts'][0]['text'] = 'A different host payload'
    hidden = runtime.public_messages(manifest, context, [wrong])[0]
    check(hidden['conversation_input_unavailable'] and 'A different host payload' not in str(hidden))
    # An evicted/corrupt lookup is never treated as an unwrapped user message.
    index_path = owner._path(b, sid, 'conversation-users.json')
    owner._write(index_path, {}, replace=True)
    missing = runtime.public_messages(manifest, context, [message])[0]
    check(missing['conversation_input_unavailable'] and 'READONLY_CONVERSATION_SOURCE_DATA' not in str(missing))
    plain_op = {**op, 'native_message_id': 'msg_plain_guide'}
    plain_payload = {'messageID': 'msg_plain_guide', 'parts': [{'type': 'text', 'text': raw}]}
    check(runtime.dispatch_payload(manifest, context, plain_op, plain_payload) == plain_payload)
    plain = {'info': {'id': 'msg_plain_guide', 'role': 'user'}, 'parts': plain_payload['parts']}
    check(runtime.public_messages(manifest, context, [plain])[0] == plain)


def test_move_blocks_new_message_on_old_project_owner_without_retargeting_its_guidance(api):
    runtime, owner, a, b, sid, manifest, reference, selected = registered(api)
    current = item(api, selected['conversation_ref']['id'])
    check(organization(api, current, 'move', 'move_again', target_project_id=a).status_code == 200)
    changed = scope(api, current['id'])
    with pytest.raises(Conflict):
        runtime.threads.original_scope(b, sid, changed['conversation_ref'])
    # A guide remains a guide to the old task; no A metadata gets injected into B.
    parent, _ = owner._claim(b, sid, 'old_b_task', 'MESSAGE',
        {'operation_id': 'old_b_task', 'session_ref': reference, 'generation': 1, 'raw_text': 'Original work in B'})
    owner._finish(b, sid, parent['id'], 'ACCEPTED', generation=1, original_session_id='ses_public_context')
    owner._update(b, sid, active_operation_id=parent['id'])
    guide, _ = owner._claim(b, sid, 'guide_existing', 'GUIDANCE', {'operation_id': 'guide_existing',
        'session_ref': reference, 'generation': 1, 'raw_text': 'Continue the existing B task',
        'expected_active_operation_id': 'old_b_task', 'conversation_ref': changed['conversation_ref']})
    guide = owner._finish(b, sid, guide['id'], 'DISPATCHING', original_active_operation_id=parent['id'], generation=1)
    runtime.prepare(manifest, {'profile': SimpleNamespace(supports_images=True)}, guide, guidance=True)
    check('conversation_context' not in owner.operation(b, sid, 'guide_existing'))
    check(not owner.active and owner._read(owner._path(b, sid, 'manifest.json')) == manifest)
    deleted = organization(api, item(api, current['id']), 'delete', 'delete_guidance_source',
        delete_scope='CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES')
    check(deleted.status_code == 200, deleted.text)
    next_guide, _ = owner._claim(b, sid, 'guide_deleted', 'GUIDANCE', {'operation_id': 'guide_deleted',
        'session_ref': reference, 'generation': 1, 'raw_text': 'Do not revive deleted conversation'})
    next_guide = owner._finish(b, sid, next_guide['id'], 'DISPATCHING', original_active_operation_id=parent['id'], generation=1)
    with pytest.raises(NotFound):
        runtime.prepare(manifest, {}, next_guide, guidance=True)


def test_existing_organization_change_cannot_be_undone_by_late_message_metadata(api):
    runtime, _, a, b, sid, _, _, selected = registered(api)
    frozen = runtime.threads.original_scope(b, sid, selected['conversation_ref'])
    changed = organization(api, item(api, selected['conversation_ref']['id']), 'move', 'newer_move', target_project_id=a)
    check(changed.status_code == 200)
    result = runtime.threads.note_message(frozen, {'kind': 'original', 'project_id': b, 'session_ref': {'id': sid}},
        'late_metadata', digest('old body'), created_at='2026-10-19T00:00:00+00:00')
    check(result['archived'] and result['revision'] == changed.json()['organization']['revision'])
    check(scope(api, selected['conversation_ref']['id'])['project_id'] == a)


def test_public_input_contract_preserves_legacy_hashes_and_shares_twenty_attachment_bound():
    from pydantic import ValidationError
    from packages.agent_sessions.models import SendMessage, operation_request
    from packages.agent_sessions.guidance import GuidanceInput
    ref = {'id': 'session', 'revision': 1, 'sha256': 'a' * 64}
    body = {'operation_id': 'unchanged_legacy', 'session_ref': ref, 'generation': 1, 'raw_text': 'Keep {"user":true}'}
    attachment = {'kind': 'CURRENT_PAGE', 'context_ref': {'id': 'frame', 'revision': 1, 'sha256': 'b' * 64},
                  'label': 'User selected page', 'regions': []}
    for model in (SendMessage, GuidanceInput):
        parsed = model.model_validate(body)
        legacy_defaults = {'agent':'build','timeout_seconds':300} if model is SendMessage else {'intent':'AUTO','expected_active_operation_id':None}
        old = {**body, **legacy_defaults}
        check(operation_request(parsed) == old and digest(operation_request(parsed)) == digest(old))
        check(len(model.model_validate({**body, 'input_attachments': [attachment] * 20}).input_attachments) == 20)
        with pytest.raises(ValidationError):
            model.model_validate({**body, 'input_attachments': [attachment] * 21})
