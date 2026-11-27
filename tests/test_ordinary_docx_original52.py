"""Root-run real App/original CLI/private-plugin DOCX continuity integration.

Only the external model is a loopback fixture. The existing scene observes its
actual POSIX process lifetime; this is not Windows, native Word or paid-provider
qualification. There are exactly eight model RPC responses and six private tool
attempts. No coordinator/kernel/API method or original CLI result is replaced.

The model holds its old JSON text answer after the first real proposal. The
client then submits two typed guidance requests to the same parent/owner. After
release, the original loop must read/locate/propose again for cohort3 and publish
only its new tracked copy. An unknown receipt is only read at its original ID;
this test never resends a parent or guidance POST.
"""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import threading
import time
import zipfile

from lxml import etree

from packages.domain.tasks import canonical, digest
from packages.domain.local_workspaces import sha
from test_native_file_continuity_api import scene, opened_report, send, MESSAGES
from test_native_messages_docx import saved_docx, configure, QUOTE, paragraph_view, check, W


OLD_REPLACEMENT = '旧轮草稿：验收失败记录由原负责人事后补齐。'
LATEST_REPLACEMENT = '最新要求：验收失败须保留原始日志，并由负责人明确确认。'
OLD_ANSWER = '旧轮已提出局部修订；这不是最新指导的完成结果。'
LATEST_ANSWER = '已按两次补充更新修订副本；原稿和旧提案保留。'
PRIVATE_TOOL = 'eeas_native_message_call'
PRIVATE_STEPS = {
    0: 'read_docx_revision_context', 1: 'locate_docx_revision', 2: 'propose_docx_revision',
    4: 'read_docx_revision_context', 5: 'locate_docx_revision', 6: 'propose_docx_revision',
}


class OrdinaryDocxWire:
    """Eight deterministic model responses; actual tool outputs feed later calls."""
    def __init__(self):
        self.calls, self.results, self.responses, self.errors = [], {}, [], []
        self.lock = threading.Lock()
        self.entered, self.release = threading.Event(), threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    with owner.lock:
                        index = len(owner.calls)
                        owner.calls.append(body)
                    check(index < 8, 'Unexpected ninth original model RPC; no fixture fallback')
                    names = {row['function']['name'] for row in body['tools']}
                    check(names == {PRIVATE_TOOL}, canonical(sorted(names)))
                    owner.observe_results(body)
                    if index in PRIVATE_STEPS:
                        name, args = PRIVATE_TOOL, owner.arguments(index)
                    else:
                        check(index in {3, 7})
                        if index == 3:
                            check(2 in owner.results, 'Old proposal tool result must precede hold')
                            owner.entered.set()
                            check(owner.release.wait(90), 'Old JSON answer fixture was not released')
                        name = None
                        args = {'text': OLD_ANSWER if index == 3 else LATEST_ANSWER, 'questions': []}
                    with owner.lock:
                        owner.responses.append({'rpc_index': index, 'tool': name, 'arguments': deepcopy(args)})
                    delta = ({'role': 'assistant', 'tool_calls': [{'index': 0,
                        'id': 'ordinary_docx52_' + str(index), 'type': 'function',
                        'function': {'name': name, 'arguments': canonical(args)}}]}
                        if name else {'role': 'assistant', 'content': canonical(args)})
                    chunks = [
                        {'id': 'ordinary-docx52', 'object': 'chat.completion.chunk', 'created': 0,
                         'model': 'docx-wire', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                        {'id': 'ordinary-docx52', 'object': 'chat.completion.chunk', 'created': 0,
                         'model': 'docx-wire', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if name else 'stop'}],
                         'usage': {'prompt_tokens': 15, 'completion_tokens': 10, 'total_tokens': 25}},
                    ]
                    raw = (''.join('data: ' + canonical(row) + '\n\n' for row in chunks)
                        + 'data: [DONE]\n\n').encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except BaseException as error:
                    with owner.lock:
                        owner.errors.append({'type': type(error).__name__, 'message': str(error)})
                    try:
                        self.send_error(500)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

        # Both server and request threads are real, non-daemon and joined.
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = False
        self.thread = threading.Thread(target=self.server.serve_forever,
            name='ordinary-docx52-external-model', daemon=False)
        self.thread.start()

    def observe_results(self, body):
        for message in body['messages']:
            if message['role'] != 'tool':
                continue
            call_id = message.get('tool_call_id', '')
            if not call_id.startswith('ordinary_docx52_'):
                continue
            index = int(call_id.removeprefix('ordinary_docx52_'))
            if index not in PRIVATE_STEPS:
                continue
            content = message['content']
            if isinstance(content, list):
                content = '\n'.join(part.get('text', '') for part in content)
            value = json.loads(content)
            check(isinstance(value, dict) and 'result' in value, canonical(value))
            check(value.get('status') != 'ERROR', canonical(value))
            with self.lock:
                if index in self.results:
                    check(self.results[index] == value, 'Original tool result changed in model history')
                self.results[index] = value

    def arguments(self, index):
        method = PRIVATE_STEPS[index]
        if index in {0, 4}:
            arguments = {}
        elif index in {1, 5}:
            context = self.results[index - 1]['result']
            paragraph = next(row for row in context['paragraphs'] if QUOTE in row['text'])
            arguments = {'part': 'word/document.xml',
                'paragraph_path': paragraph['paragraph_path'], 'quote': QUOTE}
        else:
            located = self.results[index - 1]['result']
            anchor = located.get('anchor', located)
            arguments = {'patches': [{**anchor,
                'replacement': OLD_REPLACEMENT if index == 2 else LATEST_REPLACEMENT,
                'reason': '只修订本问题指定的验收说法；保留人工阈值和原始文件。'}]}
        return {'method_id': method, 'arguments_json': canonical(arguments)}

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()  # ThreadingMixIn joins all actual request threads.
        self.thread.join(timeout=3)
        check(not self.thread.is_alive(), 'External-model fixture server did not join')


def _saved_parent(native, operation):
    # Read only this isolated QA parent's actual signed record, never a fake one.
    with native.ws.lock:
        return native.ws._read(native.path(operation), authority=True)


def _get_parent(client, parent, trace):
    response = client.get(parent)
    check(response.status_code == 200, response.text)
    value = response.json()
    trace.append({key: value.get(key) for key in ('status', 'native_message_phase', 'error', 'artifact_status')})
    return value


def test_original_cli_docx_two_guidance_publish_only_latest_tracked_copy(scene):
    client, pid, _folder, _base, _source, settings = scene
    original_artifact, immutable, path, link = opened_report(scene)
    frozen = saved_docx()
    path.write_bytes(frozen)  # Only the existing isolated fixture working copy.
    wire = OrdinaryDocxWire()
    operation = 'ordinary52-docx-original-parent'
    parent = MESSAGES + '/' + operation
    trace, controls, posted = [], [], []
    identity = result = old_entry = latest_entry = latest = shutdown = None
    bridge = process = context = None
    started = time.monotonic()
    try:
        profile = configure(client, settings, wire)
        posted.append({'kind': 'PARENT', 'operation_id': operation})
        response = send(client, pid, link, operation=operation, provider_profile_ref=profile)
        check(response.status_code == 200, response.text)
        first = response.json()
        check(first['status'] == 'PROCESSING', canonical(first))
        while not wire.entered.is_set() and time.monotonic() - started < 90:
            observed = _get_parent(client, parent, trace)
            check(observed['status'] == 'PROCESSING', canonical(observed))
            check(not wire.errors, canonical(wire.errors))
            time.sleep(.1)
        check(wire.entered.is_set(), 'Original loop did not reach old JSON answer hold')
        check(len(wire.calls) == 4 and set(wire.results) == {0, 1, 2})
        held = _get_parent(client, parent, trace)
        native = client.app.state.native_messages
        sessions = client.app.state.agent_sessions
        old_record = _saved_parent(native, operation)
        initial_cohort = deepcopy(held['native_cohort'])
        check(initial_cohort['sequence'] == 1)
        old_entry = deepcopy(old_record['docx_revision'])
        check(old_entry['proposal_ref'] == wire.results[2]['result']['proposal_ref'])
        check(old_entry['native_cohort_sha256'] == initial_cohort['cohort_sha256'])
        old_proposal = native.docx_revisions.proposal(pid, old_entry['proposal_ref'])
        check(old_proposal['status'] == 'PROPOSED')
        check(old_proposal['patches'][0]['replacement'] == OLD_REPLACEMENT)
        unpublished = native.docx_revisions.operation(pid, old_entry['operation_id'])
        check(unpublished['status'] == 'NOT_ADMITTED' and unpublished['automatic_retry'] is False)
        owner = held['owner']
        context = sessions.active[owner['session_ref']['id']]
        process = context['process']
        bridge = context['native_message']['bridge']
        identity = {'pid': process.pid, 'session_id': context['session_id'],
            'generation': context['generation'], 'owner': deepcopy(owner)}
        check(process.returncode is None and bridge.thread.is_alive())
        check(native.transport.active == {}, 'Legacy transport must not execute this ordinary parent')

        for index, text in enumerate([
            '保留原稿和阈值 42；旧提案先不要出版。',
            '按最新要求将验收失败改为保留原始日志并等待负责人明确确认，再生成修订副本。',
        ]):
            body = {'operation_id': 'ordinary52-docx-guide-' + str(index),
                'expected_native_message_ref': first['native_message_ref'],
                'raw_text': text, 'intent': 'STEER'}
            posted.append({'kind': 'GUIDANCE', 'operation_id': body['operation_id']})
            response = client.post(parent + '/guidance', json=body)  # Exactly once.
            check(response.status_code == 200, response.text)
            control = response.json()
            controls.append(control)
            recovered = client.get(parent + '/controls/' + body['operation_id'])
            check(recovered.status_code == 200, recovered.text)
            if control['status'] == 'UNKNOWN_OUTCOME':
                # Preserve/read the original uncertain POST. Never submit a replacement.
                check(recovered.json()['operation_id'] == body['operation_id'])
                raise AssertionError('Original guidance outcome is unknown; retained without POST retry')
            check(control['status'] == 'ACCEPTED', canonical(control))
            check(control['original_operation']['request']['raw_text'] == text)
            check(recovered.json()['original_operation']['native_message_id']
                == control['original_operation']['native_message_id'])
            current = _saved_parent(native, operation)
            check(current['ordinary_cohort']['sequence'] == index + 2)
            check('docx_revision' not in current and current['docx_revision_history'] == [old_entry])
            check(native.docx_revisions.operation(pid, old_entry['operation_id'])['status'] == 'NOT_ADMITTED')
            check(process.returncode is None and len(wire.calls) == 4)
        latest = deepcopy(current['ordinary_cohort'])
        check(latest['sequence'] == 3 and latest['native_session_id'] == identity['session_id'])
        check(latest['owner_ref'] == owner['session_ref'] and latest['generation'] == owner['generation'])
        check(latest['native_user_message_id'] == controls[-1]['original_operation']['native_message_id'])
        check(latest['cohort_sha256'] == digest({k: v for k, v in latest.items() if k != 'cohort_sha256'}))
        check(path.read_bytes() == frozen)
        wire.release.set()
        while time.monotonic() - started < 170:
            result = _get_parent(client, parent, trace)
            if result['status'] != 'PROCESSING':
                break  # UNKNOWN/FAILED is retained and fails below, never reposted.
            time.sleep(.1)
        check(result is not None and result['status'] == 'RETURNED', canonical(result))
        check(result['text'] == LATEST_ANSWER and result['native_cohort'] == latest)
        check(result['artifact_status'] == 'NEW_ARTIFACT', canonical(result))
        check(result['execution_effects'] == 'CREATE_NEW_DERIVED_DOCX')
        check(result['changes_applied'] is False and result['source_files_modified'] is False)
        check(not wire.errors, canonical(wire.errors))
        check(len(wire.calls) == 8 and set(wire.results) == set(PRIVATE_STEPS))
        current = _saved_parent(native, operation)
        check(current['user_turns'] == 3 and current['tool_attempts'] == 6)
        check(current['docx_revision_history'] == [old_entry])
        latest_entry = deepcopy(current['docx_revision'])
        check(latest_entry['proposal_ref'] == wire.results[6]['result']['proposal_ref'])
        check(latest_entry['proposal_ref'] != old_entry['proposal_ref'])
        check(latest_entry['native_cohort_sha256'] == latest['cohort_sha256'])
        check(native.docx_revisions.proposal(pid, old_entry['proposal_ref']) == old_proposal)
        check(native.docx_revisions.operation(pid, old_entry['operation_id'])['status'] == 'NOT_ADMITTED')
        published = native.docx_revisions.operation(pid, latest_entry['operation_id'])
        check(published['status'] == 'NEW_ARTIFACT')
        check(len(result['artifacts']) == 1 and published['artifact'] == result['artifacts'][0])
        artifact = result['artifacts'][0]
        check(artifact['source_sha256'] == sha(frozen) and not artifact['later_source_edits_included'])
        downloaded = client.get(artifact['download_url'])
        check(downloaded.status_code == 200 and sha(downloaded.content) == artifact['sha256'])
        with zipfile.ZipFile(BytesIO(frozen)) as before, zipfile.ZipFile(BytesIO(downloaded.content)) as after:
            check(before.namelist() == after.namelist())
            for name in before.namelist():
                if name != 'word/document.xml':
                    check(before.read(name) == after.read(name), 'Untouched OOXML member differs: ' + name)
            tree = etree.fromstring(after.read('word/document.xml'))
            check(bool(tree.xpath('//w:ins', namespaces={'w': W})))
            check(bool(tree.xpath('//w:del', namespaces={'w': W})))
            accepted, rejected = paragraph_view(tree, accept=True), paragraph_view(tree, accept=False)
            check(LATEST_REPLACEMENT in accepted and QUOTE not in accepted)
            check(OLD_REPLACEMENT not in accepted and OLD_REPLACEMENT not in rejected)
            check(QUOTE in rejected and LATEST_REPLACEMENT not in rejected)
            check('阈值为 42' in accepted and '阈值为 42' in rejected)
        check(path.read_bytes() == frozen)
        check(client.get(original_artifact['download_url']).content == immutable)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and (process.returncode is None or owner['session_ref']['id'] in sessions.active):
            time.sleep(.1)
        check(process.returncode is not None and owner['session_ref']['id'] not in sessions.active)
        check(context['closing'] and bridge.close_confirmed)
        check(all(not thread.is_alive() for thread in bridge.owned_threads))
        check(all(task.done() for task in bridge.owned_tasks))
        check(not any(bridge.resources().values()))
        state = sessions.get(pid, owner['session_ref']['id'])
        check(state['owner_status'] == 'DISCONNECTED')
        shutdown = {'returncode': process.returncode, 'owner_status': state['owner_status'],
            'same_process_handle': context['process'] is process, 'bridge_close_confirmed': bridge.close_confirmed,
            'bridge_resources': bridge.resources(), 'owner_registered': owner['session_ref']['id'] in sessions.active}
        check(shutdown['same_process_handle'])
        # Original GETs after completion cannot dispatch or publish again.
        check(_get_parent(client, parent, trace)['artifacts'] == result['artifacts'])
        check(len(wire.calls) == 8 and len(posted) == 3)
    finally:
        wire.release.set()
        wire.close()
        (settings.data_dir.parent / 'ordinary52-docx-original-cli.json').write_text(canonical({
            'scope': 'REAL_APP_ASGI_ORIGINAL_CLI_PRIVATE_PLUGIN_ONLY_EXTERNAL_MODEL_FIXTURE',
            'windows': 'NOT_RUN', 'native_word': 'NOT_RUN', 'paid_model_calls': 0,
            'identity': identity, 'result': result, 'controls': controls, 'trace': trace,
            'old_entry': old_entry, 'latest_entry': latest_entry, 'latest_cohort': latest,
            'model_rpc_count': len(wire.calls), 'model_fixture_errors': wire.errors,
            'model_response_steps': wire.responses, 'private_result_steps': sorted(wire.results),
            'client_posts': posted, 'automatic_post_retry': False, 'shutdown': shutdown,
            'source_unchanged': path.read_bytes() == frozen,
            'external_model_server_joined': not wire.thread.is_alive(),
        }) + '\n', encoding='utf-8')
