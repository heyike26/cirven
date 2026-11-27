"""Source43 real Store/report sources and original CLI; only provider/OS pixels are fixtures."""
import asyncio
import base64
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

from PIL import Image
import pytest
from pydantic import ValidationError

from apps.api.native_messages import NativeMessages, NativeQuestionInput
from packages.adapters.opencode_profile import COMMIT, VERSION, CLI_SHA
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.adapters import opencode_native_review as review_bridge
from packages.agent_sessions import AgentSessions
from packages.agent_sessions.native_review import NativeReviews
from packages.agent_sessions.models import NativeReviewInput
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.provider_settings import ProviderSettings
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.planning_reports import PlanningReports, PlanningReportInput
from packages.domain.planning_schema import ProposalInput
from packages.domain.file_continuity import FileContinuity
from packages.domain.linked_requirements import FrozenLinkedRequirements
from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest, now
from test_linked_requirements import original, scene
from test_native_collaboration import HostLifecycleCounter

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'b' * 64


def check(value, message='native review requirement failed'):
    if not value: raise AssertionError(message)


class FrozenPixels:
    """Owned PNG/context fixture replaces OS capture only, never original tool/loop."""
    def __init__(self):
        image = Image.new('RGB', (16, 12), (230, 77, 41))
        image.paste((20, 114, 212), (8, 0, 16, 12))
        stream = BytesIO(); image.save(stream, 'PNG', optimize=True); self.raw = stream.getvalue()
        self.context_ref = {'id': 'owned-frozen-page', 'revision': 1, 'sha256': digest('page-one')}
        self.image_ref = {'id': 'owned-image', 'revision': 1, 'sha256': __import__('hashlib').sha256(self.raw).hexdigest(), 'width': 16, 'height': 12}
        self.observation = {'context_ref': self.context_ref, 'automatic_input_authorized': False,
            'observations': {'accessibility': {'text_content': 'Task review: check original materials; output and acceptance must be traceable.'},
                'visual': {'frame_ref': {'id': 'owned-frame', 'revision': 1, 'sha256': digest('frame')},
                    'image_ref': self.image_ref}}, 'coverage': {'pixel_source': 'OWNED_PNG_FIXTURE_NOT_WINDOWS'}}
        self.pages = {digest(self.context_ref): deepcopy(self.observation)}
        self.images = {self.image_ref['sha256']: self.raw}
    def second_page(self):
        ref = {**self.context_ref, 'revision': 2, 'sha256': digest('page-two')}
        observation = deepcopy(self.observation); observation['context_ref'] = ref
        observation['observations']['accessibility']['text_content'] = 'Second frozen page. Do not read the first page as the current question.'
        image = Image.new('RGB', (16, 12), (38, 183, 89)); stream = BytesIO(); image.save(stream, 'PNG', optimize=True)
        raw = stream.getvalue(); sha = __import__('hashlib').sha256(raw).hexdigest()
        self.images[sha] = raw
        observation['observations']['visual']['frame_ref'] = {'id': 'owned-frame-two', 'revision': 1, 'sha256': digest('frame-two')}
        observation['observations']['visual']['image_ref'] = {**self.image_ref, 'id': 'owned-image-two', 'sha256': sha}
        self.pages[digest(ref)] = observation
        return ref
    def lookup(self, ref):
        if digest(ref) not in self.pages: raise Conflict('Unknown owned context')
        observation = deepcopy(self.pages[digest(ref)])
        return {'observation': observation, 'preview': {'status': 'AVAILABLE', 'image_ref': observation['observations']['visual']['image_ref'], 'pixel_source': 'OWNED_PNG_FIXTURE_NOT_WINDOWS'}}
    def image(self, ref):
        candidates = [v['observations']['visual']['image_ref'] for v in self.pages.values()]
        if ref not in [{k: value[k] for k in ('id', 'revision', 'sha256')} for value in candidates]: raise Conflict('Image differs')
        return self.images[ref['sha256']]


class ReviewWire:
    def __init__(self):
        self.requests, self.outputs = [], []
        self.review_ref = None
        self.release = threading.Event(); self.release.set()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(body)
                owner.release.wait(30)
                calls = len(owner.requests)
                for message in body['messages']:
                    if message.get('role') != 'tool': continue
                    content = message.get('content')
                    if isinstance(content, list): content = '\n'.join(v.get('text', '') for v in content)
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, dict) and parsed.get('review_ref'):
                            owner.review_ref = parsed['review_ref']; owner.outputs.append(parsed)
                    except (TypeError, ValueError): pass
                sequence = [('eeas_review_context', {}), ('skill', {'name': 'eeas-acceptance-review'}),
                    ('cirven-review-acceptance_acceptance_coverage', {'review_ref': owner.review_ref}), ('eeas_review_visual', {})]
                step = (calls - 1) % 5
                if step < len(sequence):
                    name, arguments = sequence[step]
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_review_' + str(calls),
                        'type': 'function', 'function': {'name': name, 'arguments': canonical(arguments)}}]}
                    finish = 'tool_calls'
                else:
                    delta, finish = {'role': 'assistant', 'content': 'Original frozen source review fixture completed; structure is not business verification.'}, 'stop'
                chunks = [{'id': 'review-wire', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'review-wire',
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                    {'id': 'review-wire', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'review-wire',
                     'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}]
                raw = (''.join('data: ' + canonical(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
    def close(self):
        self.release.set()
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)


@pytest.fixture
def review_scene(scene):
    store, data, folder, pid, works, _binding = scene
    material = store.save_document(pid, 'review-source.txt', b'Preserve original materials and explain missing deliverables.', 'owned-review-fixture')
    proposals = PlanningProposals(store, ROOT, workspaces=works)
    created = asyncio.run(proposals.create(pid, ProposalInput.model_validate({'operation_id': 'owned-review-plan',
        'mode': 'manual', 'prompt': 'Original owned fixture with deliberately missing outputs and acceptance.',
        'materials': [{'document_id': material['id'], 'revision': 1, 'sha256': material['sha256']}],
        'candidate': {'title': 'Original review plan', 'summary': 'Deliberately incomplete owned task.',
            'nodes': [{'id': 'review', 'title': 'Check original materials', 'description': 'Read the provided materials.',
                'outputs': [], 'acceptance': []}]}})))
    reports = PlanningReports(store, data, proposals, works)
    generated = reports.generate(pid, created['id'], PlanningReportInput(operation_id='owned-review-report', expected_revision=1))
    artifact = generated['artifact']; raw, item = reports.download(pid, created['id'], artifact['id'])
    path = data / 'native-working-copies' / pid / artifact['id'] / 'docx' / item['filename']
    path.parent.mkdir(parents=True); path.write_bytes(raw)
    continuity = FileContinuity(store, data, works, report_reader=reports.download)
    link = continuity.register_report(pid, artifact['id'], 'docx', item['filename'], item['sha256'], artifact['download_url'], 'PROPOSAL', created['id'])['link_ref']
    reader = FrozenLinkedRequirements(continuity, reports)
    original = (reader, link, created, generated, path, proposals)
    caps = NativeCapabilities(store, data)
    skill = caps.import_skill({'operation_id': 'import-review', 'id': 'eeas-acceptance-review', 'expected_revision': 0,
        'content': (ROOT / 'packages/planning/skills/acceptance-review/SKILL.md').read_text(), 'enabled': True})['config_ref']
    authority = NativeCollaborationAuthority(data, token=TOKEN, owner_pid=123, native=HostLifecycleCounter(), platform='posix')
    app = SimpleNamespace(state=SimpleNamespace(store=store, settings=SimpleNamespace(data_dir=data),
        provider_settings=ProviderSettings(store, data, ROOT), native_capabilities=caps, local_workspaces=works,
        file_continuity=reader.continuity, planning_reports=reader.reports))
    pixels = FrozenPixels()
    native = NativeMessages(app, authority, pixels)
    return {'store': store, 'data': data, 'pid': pid, 'works': works, 'caps': caps,
        'skill': skill, 'native': native, 'link': link, 'pixels': pixels, 'original': original}


def attach(value, wire=None):
    config = {'schema': 'eeas.opencode-provider.v1', 'id': 'native-review-provider', 'revision': 1,
        'version': VERSION, 'source_commit': COMMIT, 'executable': str(ROOT / 'var/opencode-ui-offline-guard/opencode'),
        'executable_sha256': CLI_SHA['posix'], 'provider_id': 'eeas-review-provider', 'protocol': 'OPENAI_COMPATIBLE_CHAT_COMPLETIONS',
        'provider_url': 'http://127.0.0.1:' + str(wire.server.server_port if wire else 9) + '/v1', 'model_id': 'review-wire',
        'secret_ref': 'secret:review-fixture', 'transport_kind': 'MOCK_WIRE', 'supports_images': True}
    registry = OpenCodeProviderRegistry(lambda *_: deepcopy(config), lambda _: 'review-fixture-only')
    sessions = AgentSessions(value['store'], value['data'], provider_registry=registry, workspaces=value['works'], capabilities=value['caps'])
    value['native'].transport.provider_registry = registry
    reviews = NativeReviews(value['native'], sessions); value['native'].reviews = reviews
    profile = {'id': config['id'], 'revision': 1, 'sha256': digest(config)}
    basis, _ = reviews._basis(value['link'])
    selection = {'skill_ref': value['skill'], 'tool_selection': {'kind': 'BUILTIN_READONLY', 'tool_ref': reviews._tool_ref()},
        'capability_binding_sha256': value['caps'].native_binding_snapshot()['sha256'], 'basis_ref': basis}
    body = {'operation_id': 'page-review-one', 'raw_text': '用选中规范检查当前页，指出验收缺项。', 'project_id': value['pid'],
        'provider_profile_ref': profile, 'file_source_ref': None, 'file_link_ref': None, 'conversation_id': 'owned-review-discussion',
        'context_ref': value['pixels'].context_ref, 'native_review': {'version': 1, 'selection': selection, 'process_start_consent': False}}
    return reviews, sessions, body


def test_old_question_serialization_and_strict_review_union(review_scene):
    reviews, sessions, body = attach(review_scene)
    old = {'operation_id': 'ordinary', 'raw_text': ' preserve original whitespace  '}
    check('native_review' not in NativeQuestionInput.model_validate(old).request_dict())
    check(NativeQuestionInput.model_validate(body).request_dict()['native_review']['selection'] == body['native_review']['selection'])
    for patch in ({'version': True}, {'process_start_consent': 1}, {'version': '1'}):
        with pytest.raises(ValidationError): NativeReviewInput.model_validate({**body['native_review'], **patch})
    invalid = deepcopy(body['native_review']); invalid['selection']['tool_selection']['probe_ref'] = {'id': 'fake'}
    with pytest.raises(ValidationError): NativeReviewInput.model_validate(invalid)
    check(sessions.active == {})


def test_catalog_is_read_only_exact_basis_not_latest_and_no_fake_probe(review_scene):
    reviews, sessions, body = attach(review_scene)
    before = review_scene['original'][4].read_bytes()
    result = reviews.catalog(project_id=review_scene['pid'], conversation_id=body['conversation_id'],
        provider_profile_ref=body['provider_profile_ref'], file_link_ref=review_scene['link'], selection=body['native_review']['selection'])
    check(result['tools'][0]['selectable'] and result['tools'][0]['selection']['kind'] == 'BUILTIN_READONLY')
    check('probe_ref' not in canonical(result['tools'][0]))
    check(result['basis_ref'] == body['native_review']['selection']['basis_ref'])
    check(result['permission_hint']['required_effects'] == ['PROCESS_START'])
    check(sessions.active == {} and review_scene['original'][4].read_bytes() == before)


def test_missing_process_consent_is_durable_known_not_dispatched_no_replay(review_scene):
    async def run():
        reviews, sessions, body = attach(review_scene)
        first = await review_scene['native'].submit(body, TOKEN)
        check(first['status'] == 'FAILED' and first['dispatch_state'] == 'NOT_DISPATCHED' and not first['execution_started'])
        check(first['error']['code'] == 'REVIEW_PROCESS_PERMISSION_REQUIRED' and sessions.active == {})
        check(await review_scene['native'].submit(body, TOKEN) == first)
        changed = deepcopy(body); changed['native_review']['process_start_consent'] = True
        with pytest.raises(Conflict): await review_scene['native'].submit(changed, TOKEN)
        await review_scene['native'].close()
    asyncio.run(run())


def _owned_startup_admission(reviews, body):
    """Real fixture admission for startup only; never submit a session message."""
    selected, _candidate = reviews._selection(body['native_review']['selection'], body['project_id'])
    key = reviews._admission_key(body, selected)
    admission = {'key': key, 'project_id': body['project_id'], 'conversation_id': body['conversation_id'],
        'provider_profile_ref': body['provider_profile_ref'], 'selection': selected,
        'created_at': now(),
        'source_host_generation': reviews.native.generation}
    ident = digest(['owned-startup-only', key]); reviews.write('admissions', ident, admission)
    ref = {'id': ident, 'revision': 1, 'sha256': digest(admission)}
    channel_key = reviews._channel_key(body)
    reviews.write('channels', channel_key, {'key': channel_key, 'owner': None, 'active': None,
        'queue': [], 'reviews_admitted': 0})
    reviews.channels.add(channel_key)
    return selected, ref


@pytest.mark.parametrize('mode', ['failed', 'missing-status', 'missing', 'forged'])
def test_private_mcp_load_error_precedes_missing_list_and_keeps_integrity_guard(review_scene, tmp_path, mode):
    from packages.agent_sessions.native_review import ReviewRejected
    reviews, sessions, body = attach(review_scene)
    _selected, ref = _owned_startup_admission(reviews, body)
    prepared = review_bridge.prepare(ROOT, tmp_path / 'runtime', owner_ref=ref, generation=1,
        directory=str(tmp_path), bridge=SimpleNamespace(base_url='http://127.0.0.1:9', key='d' * 64))
    receipt = Path(prepared['config_path']).with_name('mcp-listed.json')
    if mode == 'forged': review_bridge.write_private(receipt, {'schema': 'forged-list-not-evidence'})
    mcp = {review_bridge.SERVER_ID: {'status': 'failed', 'error': 'Operation timed out after 15000ms; fixture-secret'}} if mode == 'failed' else {
        review_bridge.SERVER_ID: {'status': 'connected'}}
    if mode == 'missing-status': mcp[review_bridge.SERVER_ID] = None
    context = {'generation': 1, 'native_review': {'prepared': prepared}, 'secrets': ['fixture-secret']}
    with pytest.raises(Conflict) as caught:
        reviews.validate_loaded({'id': 'a' * 32, 'native_review_admission': ref}, context,
            [{'id': 'eeas_review_context'}, {'id': 'eeas_review_visual'}], mcp)
    if mode in {'failed', 'missing-status'}:
        check(isinstance(caught.value, ReviewRejected) and caught.value.code == 'REVIEW_MCP_LOAD_FAILED')
        if mode == 'failed':
            check('Operation timed out after 15000ms' in str(caught.value) and 'fixture-secret' not in str(caught.value))
    elif mode == 'missing':
        check(isinstance(caught.value, ReviewRejected) and caught.value.code == 'REVIEW_MCP_LIST_NOT_OBSERVED')
    else:
        check(not isinstance(caught.value, ReviewRejected) and 'receipt differs' in str(caught.value))
    check(receipt.exists() == (mode == 'forged'), 'Validation must not synthesize a list receipt')
    check(sessions.active == {}, 'Readonly validation must not create a process owner')


@pytest.mark.parametrize('timeout_ms', [15000, 120000])
def test_actual_original_cli_delayed_private_mcp_startup_without_prompt(review_scene, tmp_path, monkeypatch, timeout_ms):
    """Only interpreter start is delayed; original CLI, SDK and list receipt are real."""
    async def run():
        wire = ReviewWire()
        reviews, sessions, body = attach(review_scene, wire)
        selected, ref = _owned_startup_admission(reviews, body)
        wrapper = tmp_path / 'delayed-private-mcp.py'
        adapter = ROOT / 'packages/adapters/opencode_native_review.py'
        wrapper.write_text('import runpy,sys,time\ntime.sleep(16)\nsys.argv=[' + repr(str(adapter)) +
            ',*sys.argv[1:]]\nrunpy.run_path(' + repr(str(adapter)) + ',run_name="__main__")\n')
        original_binding = reviews.binding
        def delayed_binding(*args):
            value = original_binding(*args)
            config = value['value']['native_config_fragment']['mcp'][review_bridge.SERVER_ID]
            check(config['timeout'] == 120000, 'Packaged startup must have an explicit bounded 120s budget')
            config['command'] = [sys.executable, '-B', '-X', 'utf8', str(wrapper), '--scope', config['command'][-1]]
            config['timeout'] = timeout_ms
            return value
        monkeypatch.setattr(reviews, 'binding', delayed_binding)
        original_http = sessions._http
        calls = []
        async def observe_http(context, method, path, *args, **kwargs):
            calls.append((method, path))
            check(method == 'GET' or '/message' not in path and '/prompt' not in path,
                'Startup must never submit a model prompt')
            if path in {'/experimental/tool', '/mcp'}:
                check(method == 'GET' and kwargs['timeout'] == min(130, timeout_ms / 1000 + 10),
                    'Only bounded readonly startup GET receives the MCP initialization allowance')
            return await original_http(context, method, path, *args, **kwargs)
        monkeypatch.setattr(sessions, '_http', observe_http)
        started = time.monotonic(); result = None; error = None
        try:
            result = await sessions.open(body['project_id'], {'operation_id': 'owned-startup-only',
                'provider_profile_ref': body['provider_profile_ref'],
                'capability_binding_sha256': selected['capability_binding_sha256'], 'title': 'Owned startup only',
                'ttl_seconds': 600, 'max_calls': 24, 'max_output_tokens': 4096,
                'allowed_effects': ['PROCESS_START'], 'mcp_tool_allowlist': [review_bridge.TOOL_NAME]},
                native_review_admission=ref)
        except Exception as caught:
            error = {'type': type(caught).__name__, 'code': getattr(caught, 'code', None), 'message': str(caught)}
        finally:
            observations = list(reviews.path('load-observations', 'x').parent.glob('*.json'))
            observed = reviews.ws._read(observations[0], authority=False) if observations else None
            summary = {'timeout_ms': timeout_ms, 'elapsed_seconds': round(time.monotonic()-started, 3),
                'owner_started': result is not None, 'error': error,
                'mcp': observed.get('mcp') if observed else None, 'calls': calls,
                'model_prompts': sum(method != 'GET' and ('/message' in path or '/prompt' in path) for method, path in calls),
                'provider_requests': len(wire.requests),
                'qualification': 'LINUX_PINNED_CLI_AND_REAL_PRIVATE_MCP_STARTUP_ONLY_WITH_CONTROLLED_INTERPRETER_DELAY'}
            (tmp_path / 'startup-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
            await sessions.close(); await reviews.native.close(); wire.close()
        check(summary['model_prompts'] == 0 and summary['provider_requests'] == 0)
        if timeout_ms == 15000:
            check(result is None and error['code'] == 'REVIEW_MCP_LOAD_FAILED', str(summary))
            check('Operation timed out after 15000ms' in error['message'])
        else:
            check(result is not None and error is None, str(summary))
        check(observed['mcp'][review_bridge.SERVER_ID]['status'] == ('failed' if timeout_ms == 15000 else 'connected'))
        check(sessions.active == {}, 'The owned startup test must end every original process')
    asyncio.run(run())


def test_actual_original_cli_skill_page_private_mcp_and_structured_image(review_scene):
    async def run():
        wire = ReviewWire()
        reviews, sessions, body = attach(review_scene, wire)
        body['native_review']['process_start_consent'] = True
        try:
            wire.release.clear()
            result = await review_scene['native'].submit(body, TOKEN)
            check(result['status'] == 'PROCESSING', canonical(result))
            second = {**deepcopy(body), 'operation_id': 'page-review-two', 'context_ref': review_scene['pixels'].second_page(),
                      'raw_text': 'Check this second frozen page against the same explicit source plan.'}
            queued = await review_scene['native'].submit(second, TOKEN)
            check(queued['review']['phase'] == 'QUEUED' and queued['review']['queue_position'] == 1)
            wire.release.set()
            deadline = asyncio.get_running_loop().time() + 70
            approved = set(); results = {}
            while asyncio.get_running_loop().time() < deadline:
                for current in (body, second):
                    operation = current['operation_id']; record = reviews._record(operation)
                    if record.get('owner'):
                        owner = record['owner']; await sessions.inspect(owner['project_id'], owner['session_ref']['id'])
                    result = reviews.get(operation); results[operation] = result
                    if result['review']['pending_permissions'] and operation not in approved:
                        item = result['review']['pending_permissions'][0]
                        reply = await reviews.control(operation, {'operation_id': 'approve-' + operation,
                            'expected_review_ref': result['review']['review_ref'], 'request_ref': item['request_ref'], 'reply': 'once'},
                            kind='PERMISSION', request_id=item['request_ref']['id'])
                        check(reply['status'] == 'RETURNED', canonical(reply)); approved.add(operation)
                if all(v['status'] in {'RETURNED', 'FAILED', 'UNKNOWN_OUTCOME'} for v in results.values()): break
                await asyncio.sleep(.1)
            check(all(v['status'] == 'RETURNED' for v in results.values()) and len(approved) == 2, canonical(results))
            check(reviews._record(body['operation_id'])['owner'] == reviews._record(second['operation_id'])['owner'])
            for result in results.values():
                check(any(s['status'] == 'COMPLETED' for s in result['review']['capability_usage']['skills']))
                check(any(s['status'] == 'COMPLETED' for s in result['review']['capability_usage']['tools']))
            coverage = [item for item in wire.outputs if 'candidate_sha256' in item]
            check(coverage and all(v['result']['nodes'] == [{'node_id': 'review', 'missing': ['outputs', 'acceptance']}] for v in coverage),
                'The actual original MCP must identify the exact missing outputs/acceptance, not return an echo')
            image_shas = []
            original_calls = []
            for request in wire.requests:
                for message in request['messages']:
                    for call in message.get('tool_calls', []):
                        if call.get('function', {}).get('name') == 'cirven-review-acceptance_acceptance_coverage':
                            original_calls.append(json.loads(call['function']['arguments']))
                    if isinstance(message.get('content'), list):
                        for block in message['content']:
                            url = block.get('image_url', {}).get('url')
                            if url and url.startswith('data:image/png;base64,'):
                                image_shas.append(__import__('hashlib').sha256(base64.b64decode(url.split(',', 1)[1], validate=True)).hexdigest())
            refs = [v['review']['review_ref'] for v in results.values()]
            check(original_calls and all(call in [{'review_ref': ref} for ref in refs] for call in original_calls),
                'The model must not generate/copy a candidate into MCP arguments')
            check(set(review_scene['pixels'].images) <= set(image_shas), 'Both distinct frozen images must reach the original provider wire')
            for output in wire.outputs:
                if 'observation' in output:
                    expected = results[output['review_ref']['id']]['context_ref']
                    check(output['context_ref'] == expected and output['observation']['context_ref'] == expected,
                        'A queued request read a different frozen page')
            evidence = os.environ.get('EEAS_NATIVE_REVIEW_EVIDENCE')
            if evidence:
                path = Path(evidence); path.mkdir(parents=True, exist_ok=True)
                (path / 'ACTUAL_CLI_RESULT.json').write_text(canonical({'results': results, 'provider_requests': wire.requests,
                    'mcp_outputs': wire.outputs, 'qualification': 'FIXED_LINUX_CLI_ONLY_EXTERNAL_PROVIDER_AND_OS_PIXELS_FIXTURE'}))
        finally:
            await review_scene['native'].close(); await sessions.close(); wire.close()
    asyncio.run(run())


def test_current_host_channel_reuse_and_concurrent_scope_isolation_without_prompt(review_scene, tmp_path, monkeypatch):
    """Real original CLI owner and signed channels; catalog never sends a prompt."""
    async def run():
        wire = ReviewWire()
        reviews, sessions, body = attach(review_scene, wire)
        selected, ref = _owned_startup_admission(reviews, body)
        key = reviews._channel_key(body)
        original_http = sessions._http
        calls = []
        async def observe_http(context, method, path, *args, **kwargs):
            calls.append((method, path))
            check(method == 'GET' or '/message' not in path and '/prompt' not in path,
                'Channel tests must never submit a model prompt')
            return await original_http(context, method, path, *args, **kwargs)
        monkeypatch.setattr(sessions, '_http', observe_http)
        started = time.monotonic()
        try:
            owner = await sessions.open(body['project_id'], {'operation_id': 'owned-channel-startup-only',
                'provider_profile_ref': body['provider_profile_ref'],
                'capability_binding_sha256': selected['capability_binding_sha256'], 'title': 'Owned channel startup only',
                'ttl_seconds': 600, 'max_calls': 24, 'max_output_tokens': 4096,
                'allowed_effects': ['PROCESS_START'], 'mcp_tool_allowlist': [review_bridge.TOOL_NAME]},
                native_review_admission=ref)
            startup_seconds = time.monotonic() - started
            before = reviews.path('channels', key).read_bytes()
            def catalog(conversation):
                return reviews.catalog(project_id=body['project_id'], conversation_id=conversation,
                    provider_profile_ref=body['provider_profile_ref'], file_link_ref=review_scene['link'],
                    selection=body['native_review']['selection'])
            same, concurrent, separate = await asyncio.gather(
                asyncio.to_thread(catalog, body['conversation_id']),
                asyncio.to_thread(catalog, body['conversation_id']),
                asyncio.to_thread(catalog, 'separate-owned-discussion'))
            for item in (same, concurrent):
                hint = item['permission_hint']
                check(hint['owner_ref'] == owner['session_ref'] and hint['generation'] == owner['generation'])
                check(hint['required_effects'] == [] and hint['reason'] is None)
            check(separate['permission_hint']['owner_ref'] is None)
            check(separate['permission_hint']['required_effects'] == ['PROCESS_START'])
            check(reviews.path('channels', key).read_bytes() == before, 'Concurrent catalog changed the runtime channel')
            check(key != reviews._channel_key({**body, 'conversation_id': 'separate-owned-discussion'}))
            check(key != reviews._channel_key({**body, 'project_id': 'separate-owned-project'}))
            channel = reviews.read('channels', key)
            channel['reviews_admitted'] = 128
            reviews.write('channels', key, channel)
            results = await asyncio.gather(*(reviews.native.submit({**deepcopy(body),
                'operation_id': 'owned-full-channel-' + str(index)}, TOKEN) for index in range(2)))
            check(all(item['error']['code'] == 'REVIEW_QUEUE_FULL' and not item['execution_started'] for item in results))
            check(reviews.read('channels', key)['reviews_admitted'] == 128 and reviews.read('channels', key)['queue'] == [])
            check(wire.requests == [], 'No provider request is authorized by channel observation or full-queue refusal')
            summary = {'startup_seconds': round(startup_seconds, 3), 'same_host_owner_reused_by_catalog': True,
                'concurrent_readers': 2, 'other_discussion_isolated': True, 'other_project_key_isolated': True,
                'concurrent_full_channel_refusals': len(results), 'per_host_admission_limit': 128,
                'model_prompts': 0, 'provider_requests': len(wire.requests)}
        finally:
            await reviews.native.close(); await sessions.close(); wire.close()
        check(sessions.active == {}, 'Owned original CLI must end after the startup-only test')
        summary['owned_processes_ended'] = True
        (tmp_path / 'same-host-summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    asyncio.run(run())


@pytest.mark.parametrize('token', [TOKEN, 'c' * 64], ids=['same-token-new-generation', 'rotated-token-new-generation'])
def test_restart_review_channel_preserves_foreign_host_queue_and_readonly_history(review_scene, tmp_path, monkeypatch, token):
    async def run():
        old, old_sessions, old_body = attach(review_scene)
        first = await old.native.submit(old_body, TOKEN)
        check(first['error']['code'] == 'REVIEW_PROCESS_PERMISSION_REQUIRED')
        # A signed stale PROCESSING queue is an explicit storage fixture, not
        # evidence that a model or native process was ever dispatched.
        record = old._record(old_body['operation_id'])
        record.update(status='PROCESSING', review_phase='QUEUED', channel_key=old._channel_key(old_body))
        old.ws._write(old.native.path(old_body['operation_id']), record)
        old_channel = {'key': old._channel_key(old_body), 'owner': None, 'active': None,
            'queue': [old_body['operation_id']], 'reviews_admitted': 1}
        old.write('channels', old_channel['key'], old_channel)
        legacy_key = digest([old_body['project_id'], old_body['conversation_id']])
        old.write('channels', legacy_key, {**old_channel, 'key': legacy_key})
        protected = {old.native.path(old_body['operation_id']), old.path('channels', old_channel['key']),
            old.path('channels', legacy_key), old.path('reviews', old_body['operation_id'])}
        await old.native.close(); await old_sessions.close()
        before = {path: path.read_bytes() for path in protected}
        authority = NativeCollaborationAuthority(review_scene['data'], token=token, owner_pid=123,
            native=HostLifecycleCounter(), platform='posix')
        pixels = FrozenPixels()
        reopened = NativeMessages(old.native.app, authority, pixels)
        value = {**review_scene, 'native': reopened, 'pixels': pixels}
        current, sessions, body = attach(value)
        body.update(operation_id='page-review-new-host', context_ref=pixels.second_page())
        reads = []
        original_read = authority._read
        def observed_read(path, *, authority=False):
            reads.append((Path(path), authority))
            return original_read(path, authority=authority)
        monkeypatch.setattr(authority, '_read', observed_read)
        try:
            catalog = current.catalog(project_id=body['project_id'], conversation_id=body['conversation_id'],
                provider_profile_ref=body['provider_profile_ref'], file_link_ref=value['link'],
                selection=body['native_review']['selection'])
            check(catalog['permission_hint']['can_submit'] and catalog['permission_hint']['reason'] is None,
                'New host must not try to authorize the old channel')
            result = await reopened.submit(body, token)
            check(result['error']['code'] == 'REVIEW_PROCESS_PERMISSION_REQUIRED', canonical(result))
            check(current._channel_key(body) != old_channel['key'] and current._channel_key(body) != legacy_key)
            check(current._record(body['operation_id'])['channel_key'] == current._channel_key(body))
            historic = reopened.get(old_body['operation_id'])
            duplicate = await reopened.submit(old_body, token)
            check(historic['status'] == duplicate['status'] == 'UNKNOWN_OUTCOME')
            check(historic['review']['phase'] == 'BLOCKED' and historic['automatic_retry'] is False)
            check(not historic['execution_started'] and not duplicate['execution_started'])
            check(not any(path in protected and privileged for path, privileged in reads),
                'New host may inspect old records only without live authority')
            check(all(path.read_bytes() == raw for path, raw in before.items()), 'Old queue/history bytes changed')
            check(sessions.active == {} and current.channels == set() and current.tasks == {} and reopened.tasks == {})
            if token != TOKEN:
                with pytest.raises(Conflict): original_read(old.path('channels', legacy_key), authority=True)
            summary = {'different_channel': current._channel_key(body) != old_channel['key'],
                'token_rotated': token != TOKEN, 'legacy_channel_ignored': current._channel_key(body) != legacy_key,
                'old_files_unchanged': True, 'old_authority_reads': 0, 'old_status': historic['status'],
                'duplicate_status': duplicate['status'], 'new_refusal': result['error']['code'],
                'owners_created': 0, 'model_calls': 0}
            (tmp_path / 'restart-summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        finally:
            await reopened.close(); await sessions.close()
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['payload', 'mac', 'token'])
def test_current_channel_tamper_remains_blocked_before_owner_start(review_scene, tmp_path, fault):
    async def run():
        reviews, sessions, body = attach(review_scene)
        key = reviews._channel_key(body)
        path = reviews.path('channels', key)
        reviews.write('channels', key, {'key': key, 'owner': None, 'active': None, 'queue': [], 'reviews_admitted': 0})
        wrapper = json.loads(path.read_text())
        if fault == 'payload': wrapper['payload']['reviews_admitted'] = 128
        elif fault == 'mac': wrapper['host_mac'] = '0' * 64
        else: wrapper['host_token_sha256'] = '0' * 64
        path.write_text(canonical(wrapper))
        before = path.read_bytes()
        try:
            with pytest.raises(Conflict): reviews.read('channels', key)
            if fault == 'payload':
                with pytest.raises(Conflict): reviews.read('channels', key, authority=False)
            result = await reviews.native.submit(body, TOKEN)
            check(result['status'] == 'FAILED' and result['dispatch_state'] == 'NOT_DISPATCHED')
            check(result['error']['message'] == 'Collaboration receipt integrity failed')
            check(not result['execution_started'] and not result['owner_start_attempted'])
            check(path.read_bytes() == before and sessions.active == {} and reviews.channels == set())
            (tmp_path / 'tamper-summary.json').write_text(json.dumps({'fault': fault,
                'current_authority_rejected': True, 'owner_started': False, 'record_unchanged': True,
                'model_calls': 0}, indent=2)+'\n')
        finally:
            await reviews.native.close(); await sessions.close()
    asyncio.run(run())
