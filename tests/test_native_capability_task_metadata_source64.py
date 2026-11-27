"""Real metadata authorities and frozen original-part projections, no CLI/model.

The separate Source64 original-CLI suite owns runtime qualification. Native OS
pixels/host lifetime here are explicitly original test data, not Windows proof.
"""
import base64
from copy import deepcopy
import hashlib
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest
from pydantic import ValidationError

from packages.agent_sessions.capability_task import (
    FrozenCapabilityContext, current_selection, freeze_selection, prompt_parts, required_effects,
)
from packages.agent_sessions.models import NativeCapabilityTaskInput, NativeReviewInput, OpenSession
from packages.agent_sessions.native_review import NativeReviews
from packages.agent_sessions.service import AgentSessions
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.store import Conflict, InvalidInput, Store
from packages.domain.tasks import digest
from test_agent_sessions_selected_scope import configured
from test_native_collaboration import HostLifecycleCounter


def check(value, reason='Source64 metadata assertion failed'):
    if not value:
        raise AssertionError(reason)


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


@pytest.fixture
def manager(tmp_path):
    return NativeCapabilities(Store(tmp_path / 'records.sqlite'), tmp_path)


def skill(manager, name):
    return manager.import_skill({'operation_id': 'import-' + name, 'id': name, 'enabled': True,
        'content': '---\nname: ' + name + '\ndescription: Source64 original fixture\n---\nUse only supplied evidence.\n'})['config_ref']


def selection(manager, items=(), selected_skill=None):
    return {'capability_binding_sha256': manager.native_binding_snapshot(validate_mcp_ids=set())['sha256'],
            'skill_ref': selected_skill, 'mcp_tool_selection': list(items)}


def context(*, visual=False, file=False):
    reference = {'id': 'exact-page', 'revision': 1, 'sha256': 'a' * 64}
    text = 'Original frozen page：中文🙂'
    value = {'schema': 'eeas.capability-task-context.v1', 'context_ref': reference,
        'observation_sha256': 'b' * 64,
        'page': {'text': text, 'text_sha256': sha(text), 'truncated': False,
            'coverage': 'BOUNDED_FROZEN_OBSERVATION', 'whole_page_seen': False},
        'file': None, 'visual': None, 'warnings': []}
    raw = None
    if visual:
        image = Image.new('RGB', (20, 14), '#d4f1ed')
        output = BytesIO(); image.save(output, format='PNG'); raw = output.getvalue()
        value['visual'] = {'context_ref': reference,
            'frame_ref': {'id': 'exact-frame', 'revision': 1, 'sha256': 'c' * 64},
            'image_ref': {'id': 'exact-image', 'revision': 1, 'sha256': sha(raw), 'width': 20, 'height': 14},
            'pixel_source': 'UNKNOWN'}
    if file:
        text = 'Original saved head only. '
        value['file'] = {'kind': 'LOCAL_FILE',
            'file_source_ref': {'kind': 'local_file', 'project_id': 'fixture-A', 'workspace_id': '1' * 64,
                'binding_revision': 1, 'binding_sha256': '2' * 64, 'path': 'original.txt', 'revision': 1, 'sha256': '3' * 64},
            'file_link_ref': None, 'snapshot_ref': None, 'source_sha256': '3' * 64,
            'text': text, 'text_sha256': sha(text), 'offset': 0, 'returned_characters': len(text),
            'total_characters': 64000, 'content_truncated': True, 'coverage': 'EXACT_SAVED_FILE_TEXT',
            'unsaved_buffer_verified': False,
            'comments': {'items': [], 'total': 0, 'truncated': False, 'coverage': 'NOT_APPLICABLE'}}
    return value, raw


def test_v1_serialization_is_preserved_and_v2_selection_is_explicit(manager):
    ref = skill(manager, 'one-skill')
    original = {'version': 1, 'selection': {'skill_ref': ref, 'tool_selection': None,
        'capability_binding_sha256': manager.native_binding_snapshot()['sha256'], 'basis_ref': None},
        'process_start_consent': False}
    check(NativeReviewInput.model_validate(original).model_dump() == original)
    parsed = NativeCapabilityTaskInput.model_validate({'version': 2, 'kind': 'CAPABILITY_TASK',
        'selection': selection(manager, selected_skill=ref)}).model_dump()
    check(parsed['task_root_operation_id'] is None and parsed['remote_mcp_connect_consent'] is False)
    for change in ({'version': True}, {'version': 2.0}, {'process_start_consent': 1}, {'remote_mcp_connect_consent': 'true'},
                   {'parts': []}, {'selection': {**parsed['selection'], 'skill_ref': None}}):
        with pytest.raises(ValidationError):
            NativeCapabilityTaskInput.model_validate({**parsed, **change})


def test_v2_installs_only_selected_skill_and_mcp_and_ignores_unrelated_drift(manager):
    chosen_skill = skill(manager, 'selected-skill')
    unused_skill = skill(manager, 'unselected-skill')
    chosen, _ = configured(manager, 'selected')
    unused, command = configured(manager, 'selected_other')
    selected = selection(manager, [chosen], chosen_skill)
    command.unlink()
    with pytest.raises((Conflict, InvalidInput)):
        manager.native_binding_snapshot()
    selected, scope, binding = freeze_selection(manager, selected)
    check(set(binding['value']['configuration_refs']['skills']) == {'selected-skill'})
    check(set(binding['value']['configuration_refs']['mcp']) == {'selected'})
    check(required_effects(binding) == ['PROCESS_START'])
    manager.set_enabled('skills', unused_skill['id'], {'operation_id': 'unused-skill-revision', 'expected_revision': 1, 'enabled': False})
    manager.set_enabled('mcp', unused['server_id'], {'operation_id': 'unused-mcp-revision', 'expected_revision': 1, 'enabled': False})
    restarted = NativeCapabilities(manager.store, manager.data_dir)
    check(current_selection(restarted, selected, scope)['sha256'] == binding['sha256'])


def test_skill_only_starts_no_mcp_and_preserves_original_probe_identity(manager):
    chosen_skill = skill(manager, 'selected-skill')
    _, command = configured(manager, 'unused')
    selected = selection(manager, selected_skill=chosen_skill)
    command.unlink()
    selected, scope, binding = freeze_selection(manager, selected)
    check(binding['value']['native_config_fragment']['mcp'] == {} and required_effects(binding) == [])
    chosen, _ = configured(manager, 'selected')
    selected, scope, binding = freeze_selection(manager, selection(manager, [chosen]))
    newer = manager._last_probe('selected', chosen['config_ref']); newer.pop('current_configuration')
    newer['id'] = 'e' * 32; manager._record_probe(newer)
    check(current_selection(manager, selected, scope)['sha256'] == binding['sha256'])
    with pytest.raises(Conflict):
        freeze_selection(manager, selection(manager, [chosen]))
    manager._path('mcp-probes', chosen['probe_ref']['id'] + '.json').unlink()
    with pytest.raises((Conflict, InvalidInput)):
        current_selection(manager, selected, scope)


@pytest.mark.parametrize('fault', ['selected_revision', 'bool_reference', 'foreign_probe', 'duplicate', 'extra_projection', 'command'])
def test_selected_scope_tampering_still_fails_closed(manager, fault):
    chosen, command = configured(manager, 'selected')
    other, _ = configured(manager, 'other')
    selected, scope, _ = freeze_selection(manager, selection(manager, [chosen]))
    if fault == 'selected_revision':
        manager.set_enabled('mcp', 'selected', {'operation_id': 'selected-new', 'expected_revision': 1, 'enabled': False})
    elif fault == 'bool_reference':
        selected['mcp_tool_selection'][0]['config_ref']['revision'] = True
    elif fault == 'foreign_probe':
        selected['mcp_tool_selection'][0]['probe_ref'] = other['probe_ref']
        scope['selection_sha256'] = digest(selected)
    elif fault == 'duplicate':
        selected['mcp_tool_selection'].append(deepcopy(selected['mcp_tool_selection'][0]))
        scope['selection_sha256'] = digest(selected)
    elif fault == 'extra_projection':
        scope['configuration_refs']['mcp']['other'] = other['config_ref']
    else:
        command.write_text('Changed exact selected command bytes')
    with pytest.raises((Conflict, InvalidInput, ValidationError)):
        current_selection(manager, selected, scope)


@pytest.mark.parametrize('fault', ['text_hash', 'false_as_int', 'visual_scope', 'wrong_file', 'complete_unknown', 'extra', 'comments_omitted'])
def test_frozen_context_metadata_is_closed_and_honest(fault):
    value, _ = context(visual=True, file=True)
    if fault == 'text_hash': value['page']['text'] += 'not frozen'
    elif fault == 'false_as_int': value['page']['whole_page_seen'] = 0
    elif fault == 'visual_scope': value['visual']['context_ref']['revision'] = True
    elif fault == 'wrong_file': value['file']['source_sha256'] = '4' * 64
    elif fault == 'complete_unknown': value['file'].update(total_characters=None, content_truncated=False)
    elif fault == 'extra': value['parts'] = [{'type': 'file', 'url': 'file:///never-allowed'}]
    else: value['file']['comments'].update(total=1)
    with pytest.raises(ValidationError):
        FrozenCapabilityContext.model_validate(value)


def test_comment_locator_omission_does_not_claim_complete_or_change_body_total():
    value, _ = context(file=True)
    comment = {'id': 'note-1', 'text': 'Original note', 'text_sha256': sha('Original note'), 'text_truncated': False,
        'source_locator_json': '{"coverage":"LOCATOR_EXCEEDS_BUDGET"}', 'exact_text_range_verified': False}
    value['file']['comments'] = {'items': [comment], 'total': 1, 'truncated': True,
        'coverage': 'CLASSIC_NOTE_REFERENCES_NOT_THREADED_OR_EXACT_RANGES'}
    frozen = FrozenCapabilityContext.model_validate(value).model_dump()
    check(frozen['file']['total_characters'] == 64000 and frozen['file']['comments']['truncated'])
    frozen['file']['comments']['items'][0]['exact_text_range_verified'] = 0
    with pytest.raises(ValidationError): FrozenCapabilityContext.model_validate(frozen)


def test_host_parts_are_exact_data_only_and_durable_descriptors_have_no_image_bytes():
    value, raw = context(visual=True, file=True)
    calls = []
    def reader(visual):
        calls.append(visual)
        check(visual == value['visual'])
        return raw
    parts, expected = prompt_parts(value, 'Raw user instruction\n 原文 ', image_reader=reader)
    check(parts[0]['text'] == 'Raw user instruction\n 原文 ' and len(calls) == 1)
    check(parts[-1]['type'] == 'file' and parts[-1]['url'].startswith('data:image/png;base64,'))
    check('data:image/' not in str(expected) and 'base64' not in str(expected) and 'file:' not in str(expected))
    check(expected['derived_visual']['whole_page_seen'] is False)
    native = {'info': {'id': 'msg_exact', 'role': 'user', 'sessionID': 'ses_exact'}, 'parts': deepcopy(parts)}
    record = {'native_message_id': 'msg_exact', 'original_session_id': 'ses_exact', 'request': {'raw_text': parts[0]['text']}, 'host_payload': expected}
    check(NativeReviews.observed_payload(record, native) is True)
    check(AgentSessions._cohort_users_observed({'session_id': 'ses_exact'}, [record], [native]))
    persisted = NativeReviews.persistent_messages([native])
    check('data:image/' not in str(persisted) and persisted[0]['parts'][-1]['host_omitted_data_url']['byte_length'] > 0)
    check(native['parts'][-1]['url'].startswith('data:image/png;'))
    only_raw = {**native, 'parts': [parts[0]]}
    check(NativeReviews.observed_payload(record, only_raw) is False)
    native['parts'][1]['text'] += 'changed context'
    check(NativeReviews.observed_payload(record, native) is False)
    with pytest.raises(Conflict): prompt_parts(value, 'Do not recapture', image_reader=lambda _: b'other image')


def test_original_png_normalization_may_change_bytes_but_not_frozen_pixels():
    value, raw = context(visual=True)
    parts, expected = prompt_parts(value, 'Read only the actual frozen image', image_reader=lambda _: raw)
    with Image.open(BytesIO(base64.b64decode(parts[-1]['url'].split(',', 1)[1]))) as opened:
        output = BytesIO(); opened.save(output, format='PNG', compress_level=0)
        normalized = output.getvalue()
    check(sha(normalized) != expected['parts'][-1]['sha256'])
    parts[-1]['url'] = 'data:image/png;base64,' + base64.b64encode(normalized).decode()
    record = {'native_message_id': 'msg_exact', 'original_session_id': 'ses_exact', 'host_payload': expected}
    message = {'info': {'id': 'msg_exact', 'role': 'user', 'sessionID': 'ses_exact'}, 'parts': parts}
    check(NativeReviews.observed_payload(record, message))


def test_task_budget_is_persistent_shared_and_never_reset_by_a_new_owner(tmp_path):
    authority = NativeCollaborationAuthority(tmp_path, token='a' * 64, owner_pid=123,
        native=HostLifecycleCounter(), platform='posix')
    native = SimpleNamespace(ws=authority, generation='host-source64',
        path=lambda op: authority._path('native-messages', op + '.json'))
    owner = SimpleNamespace(native_reviews=None)
    reviews = NativeReviews(native, owner)
    body = {'operation_id': 'first-task', 'project_id': 'fixture-A', 'conversation_id': 'one-conversation',
        'native_review': {'version': 2, 'kind': 'CAPABILITY_TASK', 'task_root_operation_id': None}}
    root = reviews._task_root(body)
    for index in range(8): reviews._reserve_task(root, 'turns', 'turn-' + str(index))
    reviews._reserve_task(root, 'turns', 'turn-0')
    with pytest.raises(Conflict): reviews._reserve_task(root, 'turns', 'ninth-turn')
    restarted = NativeReviews(native, SimpleNamespace(native_reviews=None))
    check(len(restarted._task(root)['turns']) == 8)
    for index in range(24):
        restarted._reserve_task(root, 'tool_attempts', 'call-' + str(index), review_ref={'id': 'parent-' + str(index // 8)})
    with pytest.raises(Conflict): restarted._reserve_task(root, 'tool_attempts', 'new-owner-bypass', review_ref={'id': 'new-parent'})
    with pytest.raises(Conflict):
        NativeReviews(SimpleNamespace(**{**vars(native), 'generation': 'new-host'}), owner)._task(root)


@pytest.fixture
def permission_scene(manager):
    """Only metadata records, no original process/HTTP/model is created."""
    item, _ = configured(manager, 'selected')
    selected, scope, _ = freeze_selection(manager, selection(manager, [item]))
    pid = manager.store.create_project('Source64 original permission metadata', 'No business approval')['id']
    authority = NativeCollaborationAuthority(manager.data_dir, token='a' * 64, owner_pid=123,
        native=HostLifecycleCounter(), platform='posix')
    native = SimpleNamespace(ws=authority, generation='source64-metadata-host',
        path=lambda op: authority._path('native-messages', op + '.json'))
    sessions = AgentSessions(manager.store, manager.data_dir, provider_registry=None, workspaces=None, capabilities=manager)
    reviews = NativeReviews(native, sessions)
    body = {'operation_id': 'first-permission-parent', 'project_id': pid, 'conversation_id': 'original-conversation',
        'native_review': {'version': 2, 'kind': 'CAPABILITY_TASK', 'task_root_operation_id': None, 'selection': selected}}
    root = reviews._task_root(body)
    admission = {'version': 2, 'kind': 'CAPABILITY_TASK', 'selection': selected, 'capability_scope': scope,
        'task_root_operation_id': root, 'project_id': pid, 'conversation_id': body['conversation_id'],
        'provider_profile_ref': {'id': 'unused-metadata-provider', 'revision': 1, 'sha256': 'e' * 64},
        'source_host_generation': native.generation}
    aref = {'id': 'original-admission', 'revision': 1, 'sha256': digest(admission)}
    reviews.write('admissions', aref['id'], admission)
    request = OpenSession.model_validate({'operation_id': 'original-open', 'provider_profile_ref': admission['provider_profile_ref'],
        'capability_binding_sha256': selected['capability_binding_sha256'], 'mcp_tool_allowlist': [item['tool_id']],
        'mcp_tool_selection': [item], 'allowed_effects': ['PROCESS_START']}).model_dump(exclude={'operation_id'})
    sid = '1' * 32
    manifest = {'schema': 'eeas.agent-session.v1', 'project_id': pid, 'id': sid, 'request': request, 'native_review_admission': aref}
    sessions._write(sessions._path(pid, sid, 'manifest.json'), manifest)
    owner_ref = sessions._manifest(pid, sid)[1]
    sessions._write(sessions._path(pid, sid, 'state.json'), {'active_operation_id': 'native-original-parent'})
    frozen = {'request': body, 'selection_sha256': digest(selected), 'task_root_operation_id': root}
    ref = {'id': body['operation_id'], 'revision': 1, 'sha256': digest(frozen)}
    reviews.write('reviews', ref['id'], frozen)
    record = {'request': body, 'request_sha256': digest(body), 'review_ref': ref,
        'generation': native.generation, 'native_operation_id': 'native-original-parent'}
    authority._write(native.path(body['operation_id']), record)
    context = {'session_id': 'ses_original', 'generation': 1, 'secrets': ['SECRET_SOURCE64_FIXTURE']}
    mapping = {'review_ref': ref, 'generation': 1, 'native_session_id': context['session_id'],
        'owner_ref': owner_ref, 'operation_id': record['native_operation_id'], 'selection_sha256': digest(selected)}
    reviews.write('native-users', digest([sid, 1, 'msg_original_user']), mapping)
    message = {'info': {'id': 'msg_original_assistant', 'role': 'assistant', 'sessionID': context['session_id'], 'parentID': 'msg_original_user'},
        'parts': [{'type': 'tool', 'tool': item['tool_id'], 'callID': 'call_original', 'messageID': 'msg_original_assistant',
            'sessionID': context['session_id'], 'state': {'status': 'running', 'input': {'query': 'Exact original argument 中文🙂'}}}]}
    pending = {'id': 'per_original', 'permission': item['tool_id'], 'metadata': {}, 'patterns': ['*'], 'always': ['*'],
        'tool': {'messageID': 'msg_original_assistant', 'callID': 'call_original'}}
    return SimpleNamespace(sessions=sessions, reviews=reviews, manifest=manifest, context=context,
        message=message, pending=pending, item=item, root=root)


@pytest.mark.parametrize('variant', ['exact', 'unicode_truncated', 'redacted'])
def test_permission_projection_shows_original_arguments_without_mutating_native_request(permission_scene, variant):
    f = permission_scene
    actual = f.message['parts'][0]['state']['input']
    if variant == 'unicode_truncated': actual['query'] = '界🙂' * 5000
    if variant == 'redacted': actual['credential'] = 'SECRET_SOURCE64_FIXTURE'
    original = deepcopy(f.pending)
    result = f.sessions._pending(f.context, f.pending, 'PERMISSION', f.manifest, messages=[f.message])
    call = result['capability_call']
    check(result['native_request'] == original and f.pending == original)
    check(result['request_ref'] == {'id': original['id'], 'sha256': digest(original), 'generation': 1})
    check(call['selection'] == f.item and call['tool_id'] == f.item['tool_id'])
    check(call['original_message_id'] == 'msg_original_assistant' and call['original_call_id'] == 'call_original')
    check(call['input_sha256'] == digest(actual) and len(call['input_json'].encode('utf-8')) <= 8000)
    check(call['input_truncated'] is (variant == 'unicode_truncated'))
    check(call['input_redacted'] is (variant == 'redacted'))
    check(call['coverage'] == ('EXACT_ORIGINAL_TOOL_INPUT' if variant == 'exact' else 'BOUNDED_ORIGINAL_TOOL_INPUT'))
    check('SECRET_SOURCE64_FIXTURE' not in call['input_json'])


@pytest.mark.parametrize('fault', ['missing_message', 'duplicate_call', 'foreign_parent', 'wrong_tool', 'unknown_task'])
def test_unassociated_permission_is_reject_only_and_never_uses_generic_star_as_arguments(permission_scene, fault):
    f = permission_scene
    messages = [f.message]
    if fault == 'missing_message': messages = []
    elif fault == 'duplicate_call': f.message['parts'].append(deepcopy(f.message['parts'][0]))
    elif fault == 'foreign_parent': f.message['info']['parentID'] = 'msg_unrelated_user'
    elif fault == 'wrong_tool': f.message['parts'][0]['tool'] = 'not_selected'
    else:
        task = f.reviews._task(f.root); task['blocked'] = True; f.reviews.write('capability-tasks', f.root, task)
    result = f.sessions._pending(f.context, f.pending, 'PERMISSION', f.manifest, messages=messages)
    check(result['allowed_choices'] == ['reject'])
    check(result['capability_call']['coverage'] == 'UNAVAILABLE' and 'input_json' not in result['capability_call'])
    check(result['native_request'] == f.pending and result['request_ref']['sha256'] == digest(f.pending))


def test_same_selected_owner_key_and_permission_survive_unrelated_global_metadata_change(permission_scene, manager):
    f = permission_scene
    admission = f.reviews.validate_admission(f.manifest['native_review_admission'], f.manifest['request'])
    first = f.reviews._record(f.root)['request']
    first['provider_profile_ref'] = admission['provider_profile_ref']
    old_key = f.reviews._admission_key(first, first['native_review']['selection'])
    configured(manager, 'unrelated')
    selected, _scope, _binding = freeze_selection(manager, selection(manager, [f.item]))
    check(selected['capability_binding_sha256'] != admission['selection']['capability_binding_sha256'])
    next_body = deepcopy(first); next_body['operation_id'] = 'second-permission-parent'
    next_body['native_review'].update(selection=selected, task_root_operation_id=f.root)
    check(f.reviews._admission_key(next_body, selected) == old_key)
    frozen = {'request': next_body, 'selection_sha256': digest(selected), 'task_root_operation_id': f.root}
    ref = {'id': next_body['operation_id'], 'revision': 1, 'sha256': digest(frozen)}
    f.reviews.write('reviews', ref['id'], frozen)
    record = {'request': next_body, 'request_sha256': digest(next_body), 'review_ref': ref,
        'generation': f.reviews.native.generation, 'native_operation_id': 'native-next-parent'}
    f.reviews.ws._write(f.reviews.native.path(ref['id']), record)
    owner_ref = f.sessions._manifest(f.manifest['project_id'], f.manifest['id'])[1]
    f.reviews.write('native-users', digest([f.manifest['id'], 1, 'msg_next_user']), {'review_ref': ref,
        'generation': 1, 'native_session_id': f.context['session_id'], 'owner_ref': owner_ref,
        'operation_id': record['native_operation_id'], 'selection_sha256': digest(selected)})
    f.sessions._write(f.sessions._path(f.manifest['project_id'], f.manifest['id'], 'state.json'),
        {'active_operation_id': record['native_operation_id']}, replace=True)
    f.message['info']['parentID'] = 'msg_next_user'
    call = f.sessions._pending(f.context, f.pending, 'PERMISSION', f.manifest, messages=[f.message])['capability_call']
    check(call['coverage'] == 'EXACT_ORIGINAL_TOOL_INPUT' and call['selection'] == f.item)


def test_public_user_projection_uses_durable_identity_not_prompt_markers(permission_scene):
    f = permission_scene
    source, raw_image = context(visual=True, file=True)
    raw_text = 'HOST_FROZEN_CONTEXT_DATA 是用户亲自写的原词；请保留，不按前缀删。'
    frozen = {'request': {'raw_text': raw_text}, 'capability_context': source}
    review_ref = {'id': 'public-user-parent', 'revision': 1, 'sha256': digest(frozen)}
    f.reviews.write('reviews', review_ref['id'], frozen)
    parts, evidence = prompt_parts(source, raw_text, image_reader=lambda _: raw_image)
    parts[0].update(id='prt_original_user_text', sessionID=f.context['session_id'], messageID='msg_original_user')
    evidence.update(review_ref=review_ref, context_sha256=digest(source), payload_sha256=digest({'parts': parts}))
    f.sessions._claim(f.manifest['project_id'], f.manifest['id'], 'native-original-parent', 'MESSAGE', {'raw_text': raw_text})
    f.sessions._finish(f.manifest['project_id'], f.manifest['id'], 'native-original-parent', 'DISPATCHING',
        generation=1, native_message_id='msg_original_user', original_session_id=f.context['session_id'], host_payload=evidence)
    owner_ref = f.sessions._manifest(f.manifest['project_id'], f.manifest['id'])[1]
    f.reviews.write('native-users', digest([f.manifest['id'], 1, 'msg_original_user']), {'review_ref': review_ref,
        'generation': 1, 'native_session_id': f.context['session_id'], 'native_user_message_id': 'msg_original_user',
        'native_operation_id': 'native-original-parent', 'context_ref': source['context_ref'], 'owner_ref': owner_ref})
    native = {'info': {'id': 'msg_original_user', 'role': 'user', 'sessionID': f.context['session_id']}, 'parts': parts}
    before = deepcopy(native)
    projected = f.reviews.public_messages(f.manifest, f.context, [native])[0]
    check(native == before, 'Public projection changed original SDK input parts')
    # Both AgentSessionPanel and VS Code's existing default reader render every
    # text part; neither needs a heuristic prefix filter or another UI route.
    displayed = ''.join(part.get('text', '') for part in projected['parts'] if part.get('type') == 'text')
    check(displayed == raw_text and len(projected['parts']) == 1)
    check(projected['parts'][0]['id'] == 'prt_original_user_text')
    check(projected['host_context_provenance']['review_ref'] == review_ref)
    check(projected['host_context_provenance']['image_ref'] == source['visual']['image_ref'])
    check('data:image/' not in str(projected))
    unmapped = {**native, 'info': {**native['info'], 'id': 'msg_unmapped_user'}}
    unknown = f.reviews.public_messages(f.manifest, f.context, [unmapped])[0]
    check(unknown['parts'] == [] and unknown['host_context_provenance']['coverage'] == 'DURABLE_USER_INPUT_ASSOCIATION_UNAVAILABLE')
