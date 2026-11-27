"""Actual original CLI + official MCP for explicit current-page capability v2.

Only external model responses/OS frozen pixels are fixtures. The one lost-ACK
case uses a transparent real-network fault; it never fabricates SDK responses.
"""
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import httpx
import pytest

from packages.domain.store import Conflict, InvalidInput
from packages.domain.tasks import canonical, digest
from test_agent_sessions import scene, tool, ROOT
from native_capability_task_source64_fixture import (
    CapabilityScene, CapabilityWire, LostAckProxy, TOKEN, TOOL, TERMINAL,
    evidence, find_objects, image_urls, require, text_of,
)


@pytest.fixture(scope='module', autouse=True)
def stable_actual_python_and_fixed_plugin_inputs(request):
    # Freeze the production Python superset before any owner is started. This
    # includes newly added lazy imports, not only modules imported at collection.
    paths = {p for tree in ('apps', 'packages') for p in (ROOT / tree).rglob('*.py')}
    paths.update((ROOT / 'packages/domain/migrations').glob('*.sql'))
    paths.update([Path(__file__), ROOT / 'tests/native_capability_task_source64_fixture.py',
                  ROOT / 'tests/native_capability_running_mcp_source64_fixture.py',
                  ROOT / 'tests/test_agent_sessions.py', ROOT / 'tests/test_native_collaboration.py'])
    paths.update((ROOT / 'tests').rglob('*.py'))  # Also bind imported API/ledger fixture producers.
    paths.update((ROOT / 'toolchain/opencode-plugins/native-review-context').glob('*.*'))
    paths = sorted(p for p in paths if p.is_file())
    dependency_root = Path(sys.prefix) / 'lib' / ('python' + str(sys.version_info.major) + '.' + str(sys.version_info.minor)) / 'site-packages'
    dependency_paths = sorted({p for package in ('mcp', 'httpx', 'httpcore', 'anyio', 'pydantic', 'pydantic_core', 'PIL', 'starlette')
        for p in (dependency_root / package).rglob('*') if p.is_file() and p.suffix in {'.py', '.so'}})
    dependency_before = {str(p): {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                         for p in dependency_paths}
    require(all(not p.is_symlink() for p in paths), 'Selected test inputs must be ordinary')
    before = {str(p.relative_to(ROOT)): {'bytes': len(p.read_bytes()),
              'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths}
    report_key = 'source-inputs-' + request.module.__name__
    before_report = {'qualification': 'FROZEN_INPUTS_NOT_ALL_FEATURES_TESTED',
        'files': before, 'selected_dependency_files': dependency_before}
    evidence('source-inputs-before', before_report)  # Preserve existing consumers.
    evidence(report_key + '-before', before_report)
    yield
    after = {str(p.relative_to(ROOT)): {'bytes': len(p.read_bytes()),
             'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths}
    dependency_after = {str(p): {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                        for p in dependency_paths}
    after_report = {'unchanged': before == after, 'files': after,
        'selected_dependencies_unchanged': dependency_before == dependency_after,
        'selected_dependency_files': dependency_after}
    evidence('source-inputs-after', after_report)
    evidence(report_key + '-after', after_report)
    require(before == after, 'Production/test/plugin Python input changed during actual CLI qualification')
    require(dependency_before == dependency_after, 'Selected real SDK/parser/network dependency changed')


async def wait_rpc(h, count, operation, *, timeout=40):
    return await h.poll(operation, lambda _: len(h.wire.requests) >= count, timeout=timeout)


async def permission(h, operation, control_id, reply='once'):
    view = await h.poll(operation, lambda v: bool(v['review']['pending_permissions']))
    pending = view['review']['pending_permissions'][0]
    require(pending['native_request']['permission'] == TOOL, 'Original permission belongs to another MCP tool')
    require(set(pending['allowed_choices']) == {'once', 'reject'}, 'Only exact safe once/reject choices are allowed')
    original = pending['native_request']
    require(original.get('metadata') == {} and original['patterns'] == ['*'],
            'The fixture must exercise original MCP permission metadata without model arguments')
    call = pending.get('capability_call')
    require(isinstance(call, dict) and call['tool_id'] == TOOL and call['selection']['tool_id'] == TOOL
            and call['original_message_id'] == original['tool']['messageID']
            and call['original_call_id'] == original['tool']['callID'],
            'Visible approval arguments are not tied to the exact original selected call')
    require(not call['input_truncated'] and not call['input_redacted']
            and call['coverage'] == 'EXACT_ORIGINAL_TOOL_INPUT'
            and len(call['input_json'].encode()) <= 8000
            and digest(json.loads(call['input_json'])) == call['input_sha256'],
            'Visible original argument summary/hash differs for bounded nonsensitive fixture input')
    h.permission_projections.append(deepcopy(pending))
    result = await h.control(operation, control_id, 'PERMISSION', request_ref=pending['request_ref'], reply=reply)
    require(result['status'] == 'RETURNED', 'Original once/reject control was not acknowledged: ' + canonical(result))
    return pending, result


def retain(h, name, views, **extra):
    metrics = []
    for request in h.wire.requests:
        urls = image_urls(request)
        raw_images = [base64.b64decode(url.split(',', 1)[1], validate=True)
                      for url in urls if url.startswith('data:image/') and ';base64,' in url]
        metrics.append({'rpc_index': request['index'], 'all_message_text_characters': len(text_of(request)),
            'all_message_structured_image_count': len(urls),
            'all_message_decoded_image_bytes': sum(len(raw) for raw in raw_images),
            'all_message_image_sha256': [hashlib.sha256(raw).hexdigest() for raw in raw_images],
            'complete_model_request_bytes': len(canonical(request['body']).encode())})
    evidence(name, {'qualification': 'ACTUAL_FIXED_LINUX_CLI_OFFICIAL_MCP_EXTERNAL_MODEL_AND_OS_FIXTURES',
        'native_windows': 'NOT_RUN', 'company_model': 'NOT_RUN', 'paid_model_calls': 0,
        'views': views, 'provider_requests': h.wire.requests,
        'wire_input_metrics': metrics,
        'wire_input_metrics_scope': 'COMPLETE_MODEL_HISTORY_BYTES_AND_CHARACTERS_NOT_BILLING_TOKENS',
        'token_optimization_qualified': False,
        'actual_permission_projections': h.permission_projections,
        'mcp_calls': {ident: h.calls(ident) for ident in h.receipts},
        'mcp_starts': {ident: h.starts(ident) for ident in h.receipts}, **extra})


def test_remote_declaration_has_no_real_probe_or_selectable_tool_and_never_connects(scene):
    """Known unsupported remote branch; no CLI/model/server is started here."""
    manager = scene['manager']
    saved = manager.configure_mcp({'operation_id': 's64-remote-declaration', 'id': 's64-remote',
        'expected_revision': 0, 'configuration': {'type': 'remote',
            'url': 'https://127.0.0.1:9/mcp', 'enabled': True, 'timeout': 1000}})
    with pytest.raises(Conflict, match='Remote MCP authentication/connection is not adapted'):
        asyncio.run(manager.probe_mcp('s64-remote', {'config_ref': saved['config_ref'],
            'allowed_effects': ['REMOTE_MCP_CONNECT'], 'network_mode': 'AS_CONFIGURED'}))
    row = next(v for v in manager.tool_catalog()['mcp_servers'] if v['id'] == 's64-remote')
    require(row['availability'] == 'NOT_PROBED' and row['probe_ref'] is None and row['tools'] == [],
            'Unauthenticated remote declaration became an executable verified tool selection')
    with pytest.raises(InvalidInput, match='HTTPS'):
        manager.configure_mcp({'operation_id': 's64-remote-plain-http', 'id': 's64-http',
            'expected_revision': 0, 'configuration': {'type': 'remote',
                'url': 'http://127.0.0.1:9/mcp', 'enabled': True, 'timeout': 1000}})
    evidence('remote-unavailable-metadata', {'scope': 'REAL_CONFIGURATION_AND_PROBE_REJECTION_NO_NETWORK_OR_CLI',
        'config_ref': saved['config_ref'], 'catalog_row': row,
        'remote_connection_qualified': False, 'company_gateway_qualified': False, 'model_calls': 0})


def test_actual_page_skill_mcp_question_and_busy_guidance_stay_on_original_parent(scene):
    question = tool('question', {'questions': [{'question': 'Which exact evidence should be explained?',
        'header': 'Evidence', 'custom': False, 'options': [
            {'label': 'Frozen', 'description': 'Use the admitted frozen page'},
            {'label': 'Other', 'description': 'Do not infer another source'}]}]})
    wire = CapabilityWire([tool('skill', {'name': 's64-evidence'}),
        tool(TOOL, {'label': 'before-guide'}), question,
        {'hold': 'old-cohort', 'reply': 'OLD_COHORT_MUST_NOT_FINISH_LATEST_GUIDANCE'},
        tool(TOOL, {'label': 'after-guide'}), 'S64_LATEST_GUIDANCE_REAL_NATIVE_RETURN']).start()

    async def run():
        h = CapabilityScene(scene, wire)
        try:
            selected = await h.configure()
            await h.configure('s64-unselected-broken', fail_start=True, selected=False)
            body = h.body('s64-full-parent', tools=[selected])
            first = await h.native.submit(body, TOKEN)
            require(first['status'] == 'PROCESSING', 'v2 never entered original loop: ' + canonical(first))
            owner = deepcopy(h.record(body['operation_id'])['owner'])
            await permission(h, body['operation_id'], 's64-first-once')
            pending = await h.poll(body['operation_id'], lambda v: bool(v['review']['pending_questions']))
            item = pending['review']['pending_questions'][0]
            answered = await h.control(body['operation_id'], 's64-answer-original-question', 'QUESTION',
                                       request_ref=item['request_ref'], answers=[['Frozen']])
            require(answered['status'] == 'RETURNED', 'Real original question reply was not acknowledged')
            await wait_rpc(h, 4, body['operation_id'])
            guided = await h.control(body['operation_id'], 's64-guide-original', 'GUIDANCE',
                raw_text='S64_GUIDE_EXACT_314159 Explain the same frozen page after the question.')
            require(guided['status'] == 'ACCEPTED', 'Busy guidance did not enter original session')
            wire.release('old-cohort')
            await permission(h, body['operation_id'], 's64-second-once')
            final = await h.poll(body['operation_id'], lambda v: v['status'] in TERMINAL)
            require(final['status'] == 'RETURNED' and 'S64_LATEST_GUIDANCE_REAL_NATIVE_RETURN' in final['text'],
                    'Old stop or unrelated parent was accepted as latest result: ' + canonical(final))
            require(h.record(body['operation_id'])['owner'] == owner, 'Guidance changed exact original owner')
            require(len(wire.requests) == 6 and len(h.calls()) == 2, 'Unexpected original model/tool iterations')
            require(not h.starts('s64-unselected-broken'), 'v2 started an unselected broken enabled MCP')
            require([v['label'] for v in h.calls()] == ['before-guide', 'after-guide'], 'Real MCP sequence differs')
            for call, rpc_index in zip(h.calls(), (2, 5)):
                returned = text_of(wire.requests[rpc_index])
                require(call['source_sha256'] in returned and call['label'] in returned,
                        'The real original loop did not forward its actual official MCP result')
            advertised = {entry['function']['name'] for entry in wire.requests[0]['body'].get('tools', [])}
            require(TOOL in advertised and not advertised.intersection({
                'bash', 'edit', 'read', 'eeas_review_context', 'eeas_review_visual', 'eeas_native_message_call'}),
                'v2 exposed unselected builtin/private-controller capability to the real model')
            require('S64_PAGE_ONE_FROZEN_271828' in text_of(wire.requests[0]) and
                    'S64_GUIDE_EXACT_314159' in text_of(wire.requests[-1]), 'Original wire did not receive frozen page/guidance')
            urls = image_urls(wire.requests[0])
            require(bool(urls) and all(u.startswith('data:image/png;base64,') for u in urls),
                    'Frozen structured image was flattened or absent at actual provider wire')
            for url in urls: require(bool(base64.b64decode(url.split(',', 1)[1], validate=True)), 'Invalid real image part')
            messages = await h.original_messages(body['operation_id'])
            users = [m for m in messages if m['info']['role'] == 'user']
            require(len(users) == 2 and len({m['info']['id'] for m in users}) == 2,
                    'Initial/guidance must be two durable users in one real session')
            require(all(any(p.get('type') == 'file' and p.get('mime') == 'image/png' for p in u['parts']) for u in users),
                    'The original SDK did not persist both typed frozen image messages')
            usage = final['review']['capability_usage']
            require(any(s['status'] == 'COMPLETED' for s in usage['skills']) and
                    sum(t['status'] == 'COMPLETED' for t in usage['tools']) == 2,
                    'Invocation evidence does not come from actual same-cohort native tools')
            require(final['execution_effects'] == 'CAPABILITY_TASK' and final['changes_applied'] is None
                    and final['external_effects'] == 'UNASSESSED' and final['artifacts'] == [],
                    'v2 invented readonly/effect/artifact authority')
            frozen = h.reviews._frozen(final['review']['review_ref'])
            require('data:image' not in canonical(frozen), 'Host frozen record persisted base64 instead of image authority refs')
            replay = await h.native.submit(body, TOKEN)
            require(replay['review']['review_ref'] == final['review']['review_ref'] and
                    len(wire.requests) == 6 and len(h.calls()) == 2, 'Known exact same operation was sent again')
            with pytest.raises(Conflict):
                await h.native.submit({**body, 'raw_text': 'Same operation with changed body must fail'}, TOKEN)
            retain(h, 'full-parent', [first, final, replay], original_messages=messages, frozen=frozen)
        finally:
            await h.close('full-parent')
    try: asyncio.run(run())
    finally: wire.close()


def test_queued_explicit_file_uses_exact_frozen_excerpt_after_original_file_changes(scene):
    wire = CapabilityWire([{'hold': 'first-page', 'reply': 'S64_FIRST_PAGE_COMPLETE'},
                           'S64_SECOND_FROZEN_FILE_COMPLETE']).start()

    async def run():
        h = CapabilityScene(scene, wire)
        try:
            first_body = h.body('s64-file-first', consent=False)
            first = await h.native.submit(first_body, TOKEN)
            await wait_rpc(h, 1, first_body['operation_id'])
            raw = ('S64_FILE_EXCERPT_161803\n' + 'bounded-text-' * 1700 + '\nS64_UNSEEN_TAIL_999999').encode()
            scene['actual'].write_bytes(raw)
            file_ref = scene['workspaces'].read(scene['pid'], scene['bound']['id'], 'real.md')['source_ref']
            second_page = h.pages.add('second', 'S64_SECOND_PAGE_173205')
            second_body = h.body('s64-file-queued', consent=False, context=second_page,
                                 file_ref=file_ref, root=first_body['operation_id'])
            queued = await h.native.submit(second_body, TOKEN)
            require(queued['review']['phase'] == 'QUEUED', 'Second original task did not use admitted queue')
            for _ in range(3): h.reviews.get(second_body['operation_id'])
            require(len(wire.requests) == 1, 'GET projection dispatched queued work')
            scene['actual'].write_text('S64_LIVE_FILE_CHANGED_AFTER_FREEZE', encoding='utf-8')
            wire.release('first-page')
            final = await h.poll(second_body['operation_id'], lambda v: v['status'] in TERMINAL)
            require(final['status'] == 'RETURNED' and len(wire.requests) == 2, 'Frozen queued file did not complete')
            second_text = text_of(wire.requests[1])
            require('S64_FILE_EXCERPT_161803' in second_text and 'S64_SECOND_PAGE_173205' in second_text,
                    'Explicit frozen page/file content did not reach original wire')
            require('S64_LIVE_FILE_CHANGED_AFTER_FREEZE' not in second_text and 'S64_UNSEEN_TAIL_999999' not in second_text,
                    'Live/tail bytes leaked into bounded frozen excerpt')
            frozen = h.reviews._frozen(final['review']['review_ref'])
            contexts = find_objects(frozen, lambda v: v.get('schema') == 'eeas.capability-task-context.v1')
            require(len(contexts) == 1, 'One server-authoritative frozen context must be retained')
            context = contexts[0]; saved = context['file']
            require(saved['file_source_ref'] == file_ref and saved['source_sha256'] == hashlib.sha256(raw).hexdigest(),
                    'Frozen source revision/raw hash differs')
            require(saved['text_sha256'] == hashlib.sha256(saved['text'].encode()).hexdigest()
                    and saved['returned_characters'] == len(saved['text']) <= 16000 and saved['content_truncated'],
                    'File excerpt coverage/hash is dishonest')
            require(saved['unsaved_buffer_verified'] is False and context['page']['whole_page_seen'] is False,
                    'Saved file falsely identified the current unsaved page')
            require(h.record(first_body['operation_id'])['owner'] == h.record(second_body['operation_id'])['owner'],
                    'Explicit same-scope continuation acquired a shadow controller')
            retain(h, 'queued-frozen-file', [first, queued, final], frozen=frozen,
                   changed_live_sha256=hashlib.sha256(scene['actual'].read_bytes()).hexdigest())
        finally: await h.close('queued-frozen-file')
    try: asyncio.run(run())
    finally: wire.close()


def test_text_only_provider_keeps_explicit_coverage_without_image_or_mcp_process(scene):
    wire = CapabilityWire(['S64_TEXT_ONLY_LIMITS_EXPLAINED']).start()

    async def run():
        h = CapabilityScene(scene, wire, images=False)
        try:
            await h.configure('s64-never-start', fail_start=True, selected=False)
            body = h.body('s64-text-provider', consent=False)
            first = await h.native.submit(body, TOKEN)
            final = await h.poll(body['operation_id'], lambda v: v['status'] in TERMINAL)
            require(final['status'] == 'RETURNED' and len(wire.requests) == 1, 'Text-only original loop failed')
            require(not image_urls(wire.requests[0]) and not h.starts('s64-never-start'),
                    'Image/PROCESS_START was used despite zero selected MCP or text-only provider')
            frozen = h.reviews._frozen(final['review']['review_ref'])
            contexts = find_objects(frozen, lambda v: v.get('schema') == 'eeas.capability-task-context.v1')
            require(len(contexts) == 1 and contexts[0]['visual'] is None and bool(contexts[0]['warnings']),
                    'Unsupported image was silently treated as observed')
            require(contexts[0]['page']['whole_page_seen'] is False, 'Text-only metadata became whole-page proof')
            retain(h, 'text-provider', [first, final], frozen=frozen)
        finally: await h.close('text-provider')
    try: asyncio.run(run())
    finally: wire.close()


def test_once_reject_scope_change_and_terminal_cancel_do_not_restart_original_task(scene):
    """Reject ends this native run; terminal cancel is not active-tool stop."""
    wire = CapabilityWire([tool(TOOL, {'label': 'must-be-rejected'})]).start()

    async def run():
        h = CapabilityScene(scene, wire)
        try:
            selected = await h.configure()
            denied_body = h.body('s64-no-start-consent', tools=[selected], consent=False)
            denied = await h.native.submit(denied_body, TOKEN)
            require(denied['status'] == 'FAILED' and not denied['execution_started'] and not wire.requests,
                    'Missing local process consent reached native inference')
            require(len(h.starts()) == 1, 'Only the explicitly requested discovery probe may have started')
            body = h.body('s64-reject-stop', tools=[selected])
            first = await h.native.submit(body, TOKEN)
            await h.poll(body['operation_id'], lambda value: bool(value['review']['pending_permissions']))
            owner = deepcopy(h.record(body['operation_id'])['owner'])
            # Scope changes are rejected while this original call is still
            # waiting, not after reject has legitimately ended its run.
            changed = h.body('s64-changed-selection', skill=True, tools=[], consent=False,
                             root=body['operation_id'])
            changed_result = await h.native.submit(changed, TOKEN)
            require(changed_result['status'] == 'FAILED' and not changed_result['execution_started'],
                    'Running scope change acquired/reused broader owner')
            bad_ref = deepcopy(first['review']['review_ref']); bad_ref['sha256'] = '0' * 64
            with pytest.raises(Conflict):
                await h.reviews.control(body['operation_id'], {'operation_id': 's64-foreign-control',
                    'expected_review_ref': bad_ref, 'raw_text': 'never dispatch'}, kind='GUIDANCE')
            pending, rejected = await permission(h, body['operation_id'], 's64-reject-exact', reply='reject')
            ended = await h.poll(body['operation_id'], lambda v: v['status'] in TERMINAL)
            original = h.sessions.operation(h.record(body['operation_id'])['owner']['project_id'],
                h.record(body['operation_id'])['owner']['session_ref']['id'],
                h.record(body['operation_id'])['native_operation_id'])
            require(ended['status'] == 'CANCELLED' and original.get('user_reject_acknowledged') is True
                    and original.get('reason') == 'USER_REJECTED_ORIGINAL_TOOL_REQUEST',
                    'Original reject did not end the exact native run')
            require(not h.calls() and len(wire.requests) == 1, 'Rejected original MCP call actually executed or continued')
            before = len(wire.requests)
            try:
                cancelled = await h.native.cancel(body['operation_id'])
                require(cancelled['status'] == 'CANCELLED', 'Terminal cancel changed the already rejected result')
            except Conflict as error:
                # The projected native run is already cancelled; its exact
                # active-operation guard may reject a stale coordinator stop.
                # This is known refusal before POST, not running-tool abort.
                refused = h.sessions.operation(owner['project_id'], owner['session_ref']['id'],
                    'review-cancel-' + body['operation_id'])
                require(str(error) == 'Original active operation differs from the exact stop target'
                        and refused['status'] == 'FAILED' and refused.get('error_type') == 'Conflict'
                        and not refused.get('native_post_attempted')
                        and refused.get('control_scope') is None
                        and refused['request']['expected_active_operation_id'] == original['id'],
                        'Terminal stop was not a known exact-target refusal before native dispatch')
                cancelled = {'status': 'KNOWN_TERMINAL_STOP_REJECTED', 'original_operation': refused}
            for _ in range(3): h.reviews.get(body['operation_id'])
            require(not h.calls() and len(wire.requests) == before, 'Stop/GET replayed rejected tool/model work')
            require(h.record(body['operation_id'])['owner'] == owner, 'Exact stop adopted another owner')
            actual = h.sessions._state(owner['project_id'], owner['session_ref']['id'])
            require(actual.get('active_operation_id') is None, 'Stopped original operation is still active')
            retained = h.sessions.operation(owner['project_id'], owner['session_ref']['id'], original['id'])
            require(retained['status'] == 'CANCELLED' and retained.get('user_reject_acknowledged') is True,
                    'Terminal refusal changed the actual original rejected run')
            retain(h, 'reject-scope-stop', [denied, first, rejected, ended, changed_result, cancelled],
                   pending=pending, original_rejected_operation=original,
                   active_tool_stop_qualified=False, terminal_cancel_only=True)
        finally: await h.close('reject-scope-stop')
    try: asyncio.run(run())
    finally: wire.close()


def test_twentyfifth_actual_mcp_permission_cannot_reset_shared_task_budget(scene):
    replies = []
    for parent in range(3):
        replies.append([tool(TOOL, {'label': 'budget-' + str(parent) + '-' + str(i)}) for i in range(8)])
        replies.append('S64_EIGHT_CALL_PARENT_' + str(parent) + '_RETURNED')
    replies.extend([tool(TOOL, {'label': 'budget-task-twentyfive'}), 'S64_BUDGET_END_NOT_REQUIRED'])
    wire = CapabilityWire(replies).start()

    async def run():
        h = CapabilityScene(scene, wire)
        try:
            selected = await h.configure()
            root, finals, controls, owners = 's64-budget-parent-0', [], [], []
            for parent in range(3):
                operation = 's64-budget-parent-' + str(parent)
                body = h.body(operation, tools=[selected], root=root if parent else None)
                first = await h.native.submit(body, TOKEN)
                require(first['status'] == 'PROCESSING', 'Real same-task MCP parent failed admission')
                owners.append(deepcopy(h.record(operation)['owner']))
                for number in range(8):
                    _, receipt = await permission(h, operation, 's64-budget-once-' + str(parent) + '-' + str(number))
                    controls.append(receipt)
                final = await h.poll(operation, lambda v: v['status'] in TERMINAL)
                require(final['status'] == 'RETURNED' and len(h.calls()) == (parent + 1) * 8,
                        'Original eight-call parent did not execute its real official MCP calls')
                finals.append(final)
            operation = 's64-budget-parent-3'
            fourth = await h.native.submit(h.body(operation, tools=[selected], root=root), TOKEN)
            require(h.record(operation)['owner'] == owners[0], 'Fourth parent moved the task to a fresh owner')
            blocked = await h.poll(operation, lambda v: bool(v['review']['pending_permissions']))
            pending = blocked['review']['pending_permissions'][0]
            result = await h.control(operation, 's64-budget-twentyfive', 'PERMISSION',
                                     request_ref=pending['request_ref'], reply='once')
            require(result['status'] in {'FAILED', 'FAILED_NOT_DISPATCHED', 'REJECTED'},
                    'A new parent reset the original shared twenty-four-call task budget')
            require(len(h.calls()) == 24 and len(wire.requests) == 7,
                    'Task permission budget did not stop actual twenty-fifth execution')
            require(all(owner == owners[0] for owner in owners), 'Task budget test acquired a shadow owner')
            for _ in range(3):
                recovered = h.reviews.control_get(operation, 's64-budget-twentyfive')
                require(recovered['status'] == result['status'], 'GET changed original rejected approval outcome')
            require(len(h.calls()) == 24 and len(wire.requests) == 7, 'Read-only recovery replayed over-budget call')
            retain(h, 'permission-budget', finals + [fourth, blocked, result], controls=controls)
            await h.native.cancel(operation)
        finally: await h.close('permission-budget')
    try: asyncio.run(run())
    finally: wire.close()


def test_same_task_successors_share_eight_native_turns_and_original_deadline(scene):
    wire = CapabilityWire(['S64_TURN_' + str(i) for i in range(9)]).start()

    async def run():
        h = CapabilityScene(scene, wire)
        try:
            root, finals, owners, deadlines = 's64-task-turn-0', [], [], []
            for index in range(8):
                body = h.body('s64-task-turn-' + str(index), consent=False, root=root if index else None)
                first = await h.native.submit(body, TOKEN)
                require(first['status'] == 'PROCESSING', 'Admitted original turn failed early: ' + canonical(first))
                final = await h.poll(body['operation_id'], lambda v: v['status'] in TERMINAL)
                require(final['status'] == 'RETURNED', 'Original task turn did not finish')
                record = h.record(body['operation_id']); owners.append(record['owner'])
                context = h.sessions.active[record['owner']['session_ref']['id']]
                deadlines.append(context['expires_at'])
                finals.append(final)
            ninth = await h.native.submit(h.body('s64-task-turn-8', consent=False, root=root), TOKEN)
            require(ninth['status'] == 'FAILED' and not ninth['execution_started'] and len(wire.requests) == 8,
                    'Explicit successor bypassed the shared eight-turn task allowance')
            require(all(owner == owners[0] for owner in owners), 'Same task silently opened fresh budget/owner')
            require(all(deadlines) and all(row == deadlines[0] for row in deadlines),
                    'Task root deadline was absent or extended by successor admission')
            retain(h, 'task-turn-budget', finals + [ninth], deadline_observations=deadlines)
        finally: await h.close('task-turn-budget')
    try: asyncio.run(run())
    finally: wire.close()


def test_real_forwarded_guidance_lost_ack_recovers_by_get_without_second_post(scene):
    wire = CapabilityWire([{'hold': 'unknown-original', 'reply': 'OLD_STOP_AFTER_LOST_ACK'},
                           'S64_ORIGINAL_NATIVE_RECEIVED_GUIDANCE']).start()

    async def run():
        h = CapabilityScene(scene, wire)
        proxy = None
        context = original_base = None
        try:
            body = h.body('s64-lost-ack-parent', consent=False)
            first = await h.native.submit(body, TOKEN)
            await wait_rpc(h, 1, body['operation_id'])
            record = h.record(body['operation_id']); owner = record['owner']
            context = h.sessions.active[owner['session_ref']['id']]
            original_base = context['client'].base_url
            control_id = 's64-lost-ack-guidance'

            def durable_native_id():
                saved = h.sessions.operation(owner['project_id'], owner['session_ref']['id'], control_id)
                require(saved['kind'] == 'GUIDANCE' and saved['original_active_operation_id'] == record['native_operation_id'],
                        'External fault must match the exact durable original guidance operation')
                return saved['native_message_id']

            proxy = LostAckProxy(original_base, '/session/' + context['session_id'] + '/prompt_async', durable_native_id).start()
            context['client'].base_url = httpx.URL(proxy.url)
            unknown = await h.control(body['operation_id'], control_id, 'GUIDANCE',
                raw_text='S64_REAL_LOST_ACK_GUIDANCE_424242')
            require(unknown['status'] == 'UNKNOWN_OUTCOME' and proxy.dropped,
                    'The actual forwarded POST did not leave the expected unknown receipt')
            matching = [row for row in proxy.logs if row['response_discarded']]
            require(len(matching) == 1 and matching[0]['forwarded_response_status'] == 204,
                    'Fault happened before forwarding or affected more than one original POST')
            original = h.sessions.operation(owner['project_id'], owner['session_ref']['id'], control_id)
            message_path = '/session/' + context['session_id'] + '/message/' + original['native_message_id']
            actual_user = await h.sessions._http(context, 'GET', message_path)
            require(actual_user['info']['id'] == original['native_message_id'] and
                    actual_user['info']['sessionID'] == context['session_id'] and actual_user['info']['role'] == 'user',
                    'Lost ACK has no actual durable original user receipt')
            require('S64_REAL_LOST_ACK_GUIDANCE_424242' in canonical(actual_user['parts']),
                    'Native user parts do not match the lost-ACK guidance')
            before = [row for row in proxy.logs if row['method'] == 'POST']
            for _ in range(4):
                viewed = h.reviews.control_get(body['operation_id'], control_id)
                require(viewed['status'] in {'UNKNOWN_OUTCOME', 'OBSERVED'},
                        'Without a latest response, GET invented more than exact native user observation')
                h.reviews.get(body['operation_id'])
            require([row for row in proxy.logs if row['method'] == 'POST'] == before,
                    'Recovery issued another mutation through the original endpoint')
            after_user = await h.sessions._http(context, 'GET', message_path)
            require(after_user == actual_user, 'Recovery rewrote the exact original native user')
            require(len([row for row in proxy.logs if row['method'] == 'POST']) == 1,
                    'The exact external proxy observed a replayed POST')
            retain(h, 'real-lost-ack', [first, unknown, viewed], proxy_logs=proxy.logs,
                   original_guidance_operation=original, actual_original_user=actual_user)
            context['client'].base_url = original_base
            await h.native.cancel(body['operation_id'])
        finally:
            if context is not None and original_base is not None:
                context['client'].base_url = original_base
                try:
                    await h.close('real-lost-ack')
                finally:
                    if proxy is not None: proxy.close()
    try: asyncio.run(run())
    finally: wire.close()


def test_running_official_mcp_stop_preserves_effect_and_closes_exact_owner(scene):
    """An acknowledged abort does not roll back an external tool's audit effect."""
    import site
    import time
    import psutil
    wire = CapabilityWire([tool(TOOL, {'label': 'running-stop', 'hold': True}),
                           'UNEXPECTED_MODEL_RPC_AFTER_RUNNING_STOP']).start()

    async def run():
        h = CapabilityScene(scene, wire)
        try:
            ident = 's64-reader'
            script = h.data / 's64-running-official.py'
            script.write_bytes(Path(__file__).with_name('native_capability_running_mcp_source64_fixture.py').read_bytes())
            directory = h.data / 's64-running-receipts'; directory.mkdir()
            h.receipts[ident] = directory
            executable = Path(sys.executable).resolve()
            command = [str(executable), '-B', str(script), '--site-packages', site.getsitepackages()[0],
                '--receipts', str(directory), '--source', str(scene['actual']), '--server-id', ident]
            saved = h.caps.configure_mcp({'operation_id': 's64-running-config', 'id': ident,
                'expected_revision': 0, 'configuration': {'type': 'local', 'enabled': True,
                    'timeout': 120000, 'command': command,
                    'files_sha256': {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in (executable, script, scene['actual'])}}})
            probe = await h.caps.probe_mcp(ident, {'config_ref': saved['config_ref'],
                'allowed_effects': ['PROCESS_START'], 'network_mode': 'AS_CONFIGURED'})
            require(probe['status'] == 'INITIALIZED_AND_LISTED', 'Official running-tool probe failed')
            server = next(row for row in h.caps.tool_catalog()['mcp_servers'] if row['id'] == ident)
            selected_tool = next(row for row in server['tools'] if row['name'] == 'read_receipt')
            selected = {'server_id': ident, 'config_ref': server['config_ref'], 'probe_ref': server['probe_ref'],
                'tool_id': selected_tool['id'], 'name': selected_tool['name'],
                'input_schema_sha256': selected_tool['input_schema_sha256']}
            body = h.body('s64-running-tool-stop', tools=[selected])
            first = await h.native.submit(body, TOKEN)
            pending, once = await permission(h, body['operation_id'], 's64-running-once')
            await h.poll(body['operation_id'], lambda _: len(h.calls()) == 1)
            record = h.record(body['operation_id']); owner = deepcopy(record['owner'])
            audit = h.calls()
            require(audit[0]['audit'] == 1 and not (directory / 'completed.json').exists(),
                    'Tool must have committed its effect and still be held before stop')
            before = await h.original_messages(body['operation_id'])
            call = pending['capability_call']
            parts = [part for message in before if message['info']['id'] == call['original_message_id']
                for part in message['parts'] if part.get('callID') == call['original_call_id']]
            require(len(parts) == 1 and parts[0]['state']['status'] == 'running'
                    and parts[0]['state']['input'] == {'label': 'running-stop', 'hold': True},
                    'The exact original selected tool is not actually running')
            before_state = h.sessions._state(owner['project_id'], owner['session_ref']['id'])
            require(before_state['active_operation_id'] == record['native_operation_id']
                    and len(wire.requests) == 1, 'Stop target changed before the actual abort')
            started = time.monotonic()
            cancelled = await h.native.cancel(body['operation_id'])  # Exactly one user stop.
            stop_elapsed = time.monotonic() - started
            stop = h.sessions.operation(owner['project_id'], owner['session_ref']['id'],
                'review-cancel-' + body['operation_id'])
            require(stop['status'] == 'RETURNED' and stop.get('original_abort_acknowledged') is True
                    and stop.get('target_operation_id') == record['native_operation_id']
                    and stop.get('control_scope') == 'EXACT_OPERATION'
                    and stop['request']['session_ref'] == owner['session_ref']
                    and stop['request']['generation'] == owner['generation']
                    and stop.get('external_actions_rolled_back') is False,
                    'The actual abort did not acknowledge this exact original owner/run')
            final = await h.poll(body['operation_id'], lambda value: value['status'] in TERMINAL)
            after = await h.original_messages(body['operation_id'])
            for _ in range(3): h.reviews.get(body['operation_id'])
            require(final['status'] == 'CANCELLED' and h.record(body['operation_id'])['owner'] == owner
                    and h.calls() == audit and len(wire.requests) == 1,
                    'Stop/observation changed the original owner or replayed tool/model execution')
            try:
                live = psutil.Process(audit[0]['pid']).create_time() == audit[0]['birth']
            except psutil.NoSuchProcess:
                live = False
            completed = directory / 'completed.json'
            retain(h, 'running-tool-stop', [first, once, cancelled, final],
                original_stop_operation=stop, stop_elapsed_seconds=stop_elapsed,
                original_tool_messages_before=before, original_tool_messages_after=after,
                audit_before_and_after=audit, external_tool_process_alive_after_abort=live,
                external_tool_completed_after_abort=completed.exists(),
                external_effect_rollback_qualified=False,
                external_worker_termination_by_abort_qualified=not live,
                cleanup_scope='Subsequent exact owner disconnect is separately verified by cleanup receipt')
        finally:
            await h.close('running-tool-stop')
            if 's64-reader' in h.receipts:
                directory = h.receipts['s64-reader']
                evidence('running-tool-stop-effect-after-cleanup', {'audit': h.calls(),
                    'completion': json.loads((directory / 'completed.json').read_bytes())
                        if (directory / 'completed.json').exists() else None,
                    'external_effect_rolled_back': False})
    try: asyncio.run(run())
    finally: wire.close()


def test_two_typed_screenshots_and_one_user_instruction_reach_actual_original_provider_wire(scene):
    from io import BytesIO
    from PIL import Image
    wire = CapabilityWire(['S83_TWO_SAVED_INPUTS_RECEIVED']).start()

    async def run():
        h = CapabilityScene(scene, wire)
        try:
            second = h.pages.add('typed-second', 'S83_SECOND_INPUT_CONTEXT')
            body = h.body('s83-typed-screenshots', consent=False)
            body['raw_text'] = 'Compare my two saved screenshots.\n{"my JSON":"preserve literally"}'
            body['input_attachments'] = []
            for index, ref in enumerate((h.pages.first, second)):
                image = h.pages.lookup(ref)['preview']['image_ref']
                body['input_attachments'].append({'kind': 'SCREENSHOT', 'context_ref': ref,
                    'label': 'Saved user frame ' + str(index), 'image_sha256': image['sha256'],
                    'regions': [{'x': .25, 'y': .25, 'width': .5, 'height': .5}] if index == 0 else []})
            first = await h.native.submit(body, TOKEN)
            final = await h.poll(body['operation_id'], lambda value: value['status'] in TERMINAL)
            require(final['status'] == 'RETURNED' and final['text'] == 'S83_TWO_SAVED_INPUTS_RECEIVED', canonical(final))
            require(len(final['input_attachments']) == 2 and final['review']['coverage']['visual_input_prepared'],
                'Typed attachment history or selected crop image evidence is missing from the actual projection')
            require(len(wire.requests) == 1, 'Inputs must share exactly one original model request')
            urls = image_urls(wire.requests[0]); require(len(urls) == 2, 'A selected screenshot was omitted or duplicated')
            pictures = [Image.open(BytesIO(base64.b64decode(url.split(',', 1)[1]))) for url in urls]
            require([picture.size for picture in pictures] == [(8, 6), (16, 12)], 'Exact selected crop or second original frame differs')
            require(body['raw_text'] in text_of(wire.requests[0]), 'The user instruction was flattened into host metadata')
            original = await h.original_messages(body['operation_id'])
            users = [message for message in original if message['info']['role'] == 'user']
            require(len(users) == 1 and sum(part['type'] == 'file' for part in users[0]['parts']) == 2,
                'The one original SDK user message must retain both real image parts')
            replay = await h.native.submit(body, TOKEN)
            require(replay['status'] == 'RETURNED' and len(wire.requests) == 1, 'Exact replay dispatched another model call')
            retain(h, 'typed-two-screenshots-s83', [first, final, replay], original_messages=original)
        finally:
            await h.close('typed-two-screenshots-s83')
    try: asyncio.run(run())
    finally: wire.close()


def test_ordinary_two_saved_images_use_same_original_private_plugin_and_one_user_text(scene):
    from packages.agent_sessions.native_message import NativeMessageSessions
    replies = [tool('eeas_native_message_call', {'method_id': 'list_input_attachments', 'arguments_json': '{}'}),
        tool('eeas_native_message_call', {'method_id': 'read_input_attachments',
            'arguments_json': canonical({'view': 'IMAGE', 'items': [{'index': 0}, {'index': 1, 'region_index': 0}]})}),
        canonical({'text': 'S83_ORDINARY_TWO_IMAGES_READ', 'questions': []})]
    wire = CapabilityWire(replies).start()

    async def run():
        h = CapabilityScene(scene, wire)
        ordinary = NativeMessageSessions(h.native, h.sessions);h.native.ordinary = ordinary
        try:
            second = h.pages.add('ordinary-second', 'S83_ORDINARY_SECOND')
            body = h.body('s83-ordinary-images', consent=False);body.pop('native_review');body['context_ref'] = None
            body['raw_text'] = 'Review my two attachments.\n{"user JSON":"literal"}'
            body['input_attachments'] = []
            for index, ref in enumerate((h.pages.first, second)):
                image = h.pages.lookup(ref)['preview']['image_ref']
                body['input_attachments'].append({'kind': 'SCREENSHOT', 'context_ref': ref,
                    'label': 'Saved source ' + str(index), 'image_sha256': image['sha256'],
                    'regions': [] if index == 0 else [{'x': .25, 'y': .25, 'width': .5, 'height': .5}]})
            first = await h.native.submit(body, TOKEN)
            require(first['status'] == 'PROCESSING', canonical(first))
            deadline = asyncio.get_running_loop().time() + 60
            final = None
            while asyncio.get_running_loop().time() < deadline:
                record = ordinary._record(body['operation_id']);owner = record.get('owner')
                if owner and owner['session_ref']['id'] in h.sessions.active:
                    context = h.sessions.active[owner['session_ref']['id']]
                    h.contexts[owner['session_ref']['id']] = context
                    await h.sessions.inspect(owner['project_id'], owner['session_ref']['id'])
                    await ordinary._owner_changed(owner['project_id'], owner['session_ref']['id'])
                final = h.native.get(body['operation_id'])
                if final['status'] in TERMINAL:break
                await asyncio.sleep(.08)
            require(final['status'] == 'RETURNED' and final['text'] == 'S83_ORDINARY_TWO_IMAGES_READ', canonical(final))
            require(len(wire.requests) == 3 and bool(image_urls(wire.requests[-1])),
                'The actual private plugin failed to forward the selected image outputs to the original model')
            require(len(image_urls(wire.requests[-1])) == 2, 'Both bound image views must reach the same real loop')
            require(ordinary._record(body['operation_id'])['request']['raw_text'] == body['raw_text'] and len(final['input_attachments']) == 2,
                'User text or typed input history changed')
            require(ordinary._record(body['operation_id'])['tool_attempts'] == 2,
                'The selected inputs bypassed the original shared tool budget')
            replay = await h.native.submit(body, TOKEN)
            require(replay['status'] == 'RETURNED' and len(wire.requests) == 3, 'Same operation dispatched again')
            retain(h, 'ordinary-two-inputs-s83', [first, final, replay])
        finally:
            await h.close('ordinary-two-inputs-s83')
    try: asyncio.run(run())
    finally: wire.close()


def test_moved_conversation_reads_target_metadata_and_old_history_in_one_original_owner(scene):
    from apps.api.conversation_directory import ConversationDirectory
    from apps.api.conversation_organization import ConversationOrganization, OrganizationChange
    from packages.agent_sessions.native_message import NativeMessageSessions
    replies = [tool('eeas_native_message_call', {'method_id': 'read_project_context', 'arguments_json': '{}'}),
        tool('eeas_native_message_call', {'method_id': 'read_history', 'arguments_json': '{}'}),
        canonical({'text': 'S83_TARGET_B_AND_PRIOR_A_READ', 'questions': []})]
    wire = CapabilityWire(replies).start()

    async def run():
        h = CapabilityScene(scene, wire)
        ordinary = NativeMessageSessions(h.native, h.sessions); h.native.ordinary = ordinary
        h.native.app.state.native_messages = h.native
        directory = ConversationDirectory(h.native.app)
        h.native.app.state.conversation_threads = directory.threads
        organization = ConversationOrganization(scene['store'], directory.resolve, directory.threads)
        try:
            old = h.body('s83-before-project-move'); old.pop('native_review')
            old.update(context_ref=None, provider_profile_ref=None, raw_text='S83_PRIOR_A_USER_REQUIREMENT')
            require((await h.native.submit(old, TOKEN))['status'] == 'NOT_CONFIGURED', 'Old source must not start a model')
            old_path = h.native.path(old['operation_id']); old_bytes = old_path.read_bytes()
            source = {'kind': 'native', 'project_id': h.pid, 'last_operation_id': old['operation_id']}
            row = directory.resolve(source)
            target = scene['store'].create_project('S83 Target B', 'S83_B_PROJECT_DESCRIPTION_ONLY')['id']
            moved = organization.change(row['id'], OrganizationChange.model_validate({
                'operation_id': 's83-real-move-to-b', 'action': 'move', 'expected_revision': 0,
                'source': row['source'], 'target_project_id': target}))
            selected = directory.threads.view(row['id'])
            require(moved['project_id'] == target and not wire.requests, 'Organizing a chat must never start inference')
            body = h.body('s83-after-project-move'); body.pop('native_review')
            body.update(context_ref=None, raw_text='Continue the prior requirement in the selected project.',
                conversation_ref=selected['conversation_ref'], **selected['next_message'])
            first = await h.native.submit(body, TOKEN)
            require(first['status'] == 'PROCESSING', canonical(first))
            deadline = asyncio.get_running_loop().time() + 60
            final = None
            while asyncio.get_running_loop().time() < deadline:
                record = ordinary._record(body['operation_id']); owner = record.get('owner')
                if owner and owner['session_ref']['id'] in h.sessions.active:
                    h.contexts[owner['session_ref']['id']] = h.sessions.active[owner['session_ref']['id']]
                    await h.sessions.inspect(owner['project_id'], owner['session_ref']['id'])
                    await ordinary._owner_changed(owner['project_id'], owner['session_ref']['id'])
                final = h.native.get(body['operation_id'])
                if final['status'] in TERMINAL: break
                await asyncio.sleep(.08)
            require(final['status'] == 'RETURNED' and final['text'] == 'S83_TARGET_B_AND_PRIOR_A_READ', canonical(final))
            require(len(wire.requests) == 3, 'Both readonly tools must use the same bounded original loop')
            observed = text_of(wire.requests[-1])
            require('S83_B_PROJECT_DESCRIPTION_ONLY' in observed and 'S83_PRIOR_A_USER_REQUIREMENT' in observed,
                'Target metadata and exact old discussion did not reach the real original provider request')
            record = ordinary._record(body['operation_id'])
            require(record['owner']['project_id'] == target and record['tool_attempts'] == 2,
                'New task owner or shared tool accounting lost the selected target project')
            require(record['conversation_scope']['project_context']['file_write_authorized'] is False,
                'Project metadata created an unrelated execution grant')
            replay = await h.native.submit(body, TOKEN)
            require(replay['status'] == 'RETURNED' and len(wire.requests) == 3, 'Move continuation replay dispatched twice')
            require(old_path.read_bytes() == old_bytes, 'Moving a discussion rewrote its original task record')
            retain(h, 'moved-conversation-project-context-s83', [moved, first, final, replay])
        finally:
            await h.close('moved-conversation-project-context-s83')
    try: asyncio.run(run())
    finally: wire.close()


def test_v2_payload_carries_frozen_target_metadata_with_existing_page_in_original_wire(scene):
    from apps.api.conversation_directory import ConversationDirectory
    from apps.api.conversation_organization import ConversationOrganization, OrganizationChange
    wire = CapabilityWire(['S83_V2_SELECTED_PROJECT_METADATA_READ']).start()

    async def run():
        h = CapabilityScene(scene, wire)
        h.native.app.state.native_messages = h.native
        directory = ConversationDirectory(h.native.app)
        h.native.app.state.conversation_threads = directory.threads
        organization = ConversationOrganization(scene['store'], directory.resolve, directory.threads)
        try:
            old = h.body('s83-v2-source-before-move'); old.pop('native_review')
            old.update(context_ref=None, provider_profile_ref=None)
            require((await h.native.submit(old, TOKEN))['status'] == 'NOT_CONFIGURED', 'Seed must not infer')
            row = directory.resolve({'kind': 'native', 'project_id': h.pid, 'last_operation_id': old['operation_id']})
            target = scene['store'].create_project('S83 v2 Project', 'S83_V2_TARGET_CONTEXT_NO_FILE_GRANT')['id']
            moved = organization.change(row['id'], OrganizationChange.model_validate({
                'operation_id': 's83-v2-context-move', 'action': 'move', 'expected_revision': 0,
                'source': row['source'], 'target_project_id': target}))
            selected = directory.threads.view(row['id'])
            body = h.body('s83-v2-after-project-move', consent=False)
            body.update(conversation_ref=selected['conversation_ref'], **selected['next_message'])
            first = await h.native.submit(body, TOKEN)
            final = await h.poll(body['operation_id'], lambda value: value['status'] in TERMINAL)
            require(final['status'] == 'RETURNED' and final['text'] == 'S83_V2_SELECTED_PROJECT_METADATA_READ', canonical(final))
            require(len(wire.requests) == 1 and 'S83_V2_TARGET_CONTEXT_NO_FILE_GRANT' in text_of(wire.requests[0]),
                'V2 host parts omitted the admitted target project metadata')
            record = h.reviews._record(body['operation_id'])
            require(record['conversation_scope']['project_id'] == target and record['request']['context_ref'] == body['context_ref'],
                'The selected project metadata displaced the explicit original page source')
            require(not record['conversation_scope']['project_context']['file_write_authorized'], 'Metadata grants file writes')
            retain(h, 'v2-project-context-s83', [moved, first, final])
        finally:
            await h.close('v2-project-context-s83')
    try: asyncio.run(run())
    finally: wire.close()


@pytest.mark.parametrize('kind', ['original', 'discovery'])
@pytest.mark.parametrize('mode', ['ordinary', 'capability'])
def test_moved_public_history_reaches_same_original_loop_without_reopening_old_owner(scene, kind, mode):
    from apps.api.conversation_directory import ConversationDirectory
    from apps.api.conversation_organization import ConversationOrganization, OrganizationChange
    from packages.agent_sessions.native_message import NativeMessageSessions
    from packages.domain.discovery import DiscoveryWorkbench
    from packages.domain.discovery_schema import DiscoveryCreate
    from packages.domain.planning_proposals import PlanningProposals
    marker = 'S83_MOVED_' + kind.upper() + '_' + mode.upper()
    old_user = 'S83_PRIOR_PUBLIC_USER_REQUIREMENT_' + kind.upper()
    replies = ['S83_PRIOR_PUBLIC_ORIGINAL_RESPONSE'] if kind == 'original' else []
    if mode == 'ordinary':
        replies += [tool('eeas_native_message_call', {'method_id': 'read_project_context', 'arguments_json': '{}'}),
            tool('eeas_native_message_call', {'method_id': 'read_history', 'arguments_json': '{}'}),
            canonical({'text': marker, 'questions': []})]
    else:
        replies += [marker]
    wire = CapabilityWire(replies).start()

    async def run():
        h = CapabilityScene(scene, wire)
        ordinary = NativeMessageSessions(h.native, h.sessions); h.native.ordinary = ordinary
        state = h.native.app.state
        state.native_messages, state.agent_sessions = h.native, h.sessions
        directory = ConversationDirectory(h.native.app); state.conversation_threads = directory.threads
        organization = ConversationOrganization(scene['store'], directory.resolve, directory.threads)
        old_sid, old_paths = None, []
        name = 'moved-public-history-' + kind + '-' + mode
        try:
            if kind == 'original':
                opened = await h.sessions.open(h.pid, {'operation_id': 'public-history-open',
                    'provider_profile_ref': h.profile, 'capability_binding_sha256': h.caps.native_binding_snapshot()['sha256'],
                    'title': 'Public source conversation', 'ttl_seconds': 180, 'max_calls': 4, 'max_output_tokens': 1024})
                old_sid = opened['session_ref']['id']; context = h.sessions.active[old_sid]
                h.contexts[old_sid] = context
                h.identities[old_sid] = h.sessions._state(h.pid, old_sid)['process_identity']
                await h.sessions.send(h.pid, old_sid, {'operation_id': 'public-history-user',
                    'session_ref': opened['session_ref'], 'generation': opened['generation'], 'raw_text': old_user, 'agent': 'plan'})
                deadline = asyncio.get_running_loop().time() + 45
                while True:
                    saved = await h.sessions.inspect(h.pid, old_sid)
                    if saved['active_operation_id'] is None:
                        break
                    require(asyncio.get_running_loop().time() < deadline, 'Original source task did not return')
                    await asyncio.sleep(.08)
                require(h.sessions.operation(h.pid, old_sid, 'public-history-user')['status'] == 'RETURNED', 'Old source has no actual return')
                await h.sessions.disconnect(h.pid, old_sid)
                old_paths = [h.sessions._path(h.pid, old_sid, 'manifest.json'), h.sessions._path(h.pid, old_sid, 'state.json'),
                             h.sessions._path(h.pid, old_sid, 'operations', digest('public-history-user') + '.json')]
                source = {'kind': 'original', 'project_id': h.pid, 'session_ref': opened['session_ref']}
            else:
                proposals = PlanningProposals(scene['store'], ROOT, transport=h.native.transport, workspaces=scene['workspaces'])
                state.discovery = DiscoveryWorkbench(scene['store'], proposals)
                created = state.discovery.create(h.pid, DiscoveryCreate.model_validate({'operation_id': 'public-discovery-create',
                    'title': 'Stored user discovery', 'entry_mode': 'REQUIREMENTS', 'statement': old_user}))
                require(created.get('revision') == 1 and not wire.requests, 'Discovery draft must come from real user input without a model')
                source = {'kind': 'discovery', 'project_id': h.pid,
                    'object_ref': {'id': created['id'], 'revision': 1, 'sha256': created['sha256']}}
            old_bytes = {path: path.read_bytes() for path in old_paths}
            row = directory.resolve(source)
            target = scene['store'].create_project('History target B', 'S83_HISTORY_TARGET_PROJECT_B')['id']
            moved = organization.change(row['id'], OrganizationChange.model_validate({'operation_id': 'public-history-move',
                'action': 'move', 'expected_revision': 0, 'source': row['source'], 'target_project_id': target}))
            selected = directory.threads.view(row['id'])
            body = h.body('public-history-next', consent=False)
            body.update(raw_text='Continue my existing discussion in the selected project.',
                conversation_ref=selected['conversation_ref'], **selected['next_message'])
            if mode == 'ordinary':
                body.pop('native_review'); body['context_ref'] = None
            first = await h.native.submit(body, TOKEN)
            require(first['status'] == 'PROCESSING', canonical(first))
            if mode == 'capability':
                final = await h.poll(body['operation_id'], lambda value: value['status'] in TERMINAL)
            else:
                receipt = ordinary._record(body['operation_id'])
                admission = ordinary.validate_admission(receipt['native_message_ref'])
                deadline = datetime.fromisoformat(admission['absolute_deadline'])
                while True:
                    record = ordinary._record(body['operation_id']); owner = record.get('owner')
                    if owner and owner['session_ref']['id'] in h.sessions.active:
                        h.contexts[owner['session_ref']['id']] = h.sessions.active[owner['session_ref']['id']]
                        await h.sessions.inspect(owner['project_id'], owner['session_ref']['id'])
                        await ordinary._owner_changed(owner['project_id'], owner['session_ref']['id'])
                    final = h.native.get(body['operation_id'])
                    if final['status'] in TERMINAL:
                        break
                    require(datetime.now(timezone.utc) < deadline, 'New original conversation exceeded its admitted deadline')
                    await asyncio.sleep(.08)
            require(final['status'] == 'RETURNED' and final['text'] == marker, canonical(final))
            seen = text_of(wire.requests[-1])
            require(old_user in seen and 'S83_HISTORY_TARGET_PROJECT_B' in seen, 'Stored source or selected project did not reach the actual provider')
            if kind == 'original':
                require('S83_PRIOR_PUBLIC_ORIGINAL_RESPONSE' in seen and old_sid not in h.sessions.active,
                    'History lost the actual original response or reopened the closed source owner')
            require(len(wire.requests) == (1 if kind == 'original' else 0) + (3 if mode == 'ordinary' else 1),
                'History lookup or project movement added an inference call')
            listing = directory.read()
            require([item['id'] for item in listing['items']] == [row['id']],
                'Internal execution owners appeared as duplicate chats: ' + canonical(listing))
            require(all(path.read_bytes() == raw for path, raw in old_bytes.items()), 'History navigation rewrote the old task')
            replay = await h.native.submit(body, TOKEN)
            require(replay['status'] == 'RETURNED', canonical(replay))
            retain(h, name, [moved, first, final, replay], source_kind=kind, source_created_by='ACTUAL_ORIGINAL_TASK' if old_sid else 'ACTUAL_ZERO_MODEL_DISCOVERY_CREATE')
        finally:
            await h.close(name)
    try: asyncio.run(run())
    finally: wire.close()
