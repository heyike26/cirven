"""Real binary/text feedback persistence and real original-CLI/CAS provenance links."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sys

from docx import Document
from PIL import Image
import httpx
import pytest

from conftest import LiveServer
from packages.domain.local_workspaces import MAX_EDIT_BYTES, digest, sha
from packages.domain.native_feedback import NativeFeedback
from test_direct_actions import DirectModelWire, ORIGINAL, BUDGET
from test_opencode_planning import KEY, MODEL
from test_planning_delivery import seed_cli


def check(condition, message='acceptance check failed'):
    if not condition:
        raise AssertionError(message)


class Evidence:
    def __init__(self, client, directory):
        self.client, self.directory = client, directory
        self.calls, self.details = [], {}
        directory.mkdir(parents=True, exist_ok=True)
    def call(self, method, path, body=None, status=200, params=None):
        response = self.client.request(method, path, json=body, params=params)
        value = response.json()
        self.calls.append({'method': method, 'path': path, 'input': body, 'params': params,
            'status': response.status_code, 'response': value})
        self.flush()
        check(response.status_code == status, response.text)
        return value
    def flush(self):
        raw = json.dumps({'calls': self.calls, 'details': self.details,
            'qualification': 'LINUX_REAL_SERVICES_WITH_ORIGINAL_CLI_EXTERNAL_PROVIDER_FIXTURE_ONLY',
            'native_windows': 'NOT_RUN', 'native_word_mutation': 'NOT_ADAPTED', 'screen_identity': 'NOT_QUALIFIED'},
            ensure_ascii=False, indent=2).replace(KEY, '<fixture-credential-redacted>')
        (self.directory / 'receipt.json').write_text(raw)


def prepare(evidence, folder):
    folder.mkdir()
    (folder / '说明.md').write_bytes(ORIGINAL)
    document = Document(); document.add_paragraph('真实用户Word原件；注释不能变成修改授权。')
    document.save(folder / '实际原件.docx')
    image = Image.new('RGB', (80, 60), (13, 101, 187)); image.save(folder / '实际图片.png')
    (folder / '另一份.md').write_text('独立资料保留。\n')
    pid = evidence.call('POST', '/api/projects', {'name': '通用原生文件反馈', 'description': ''}, 201)['id']
    base = f'/api/projects/{pid}'
    bound = evidence.call('POST', base + '/local-workspaces', {'idempotency_key': 'feedback-folder',
        'root': str(folder), 'allowed_write_paths': ['说明.md']}, 201)
    refs = {}
    for path in ('说明.md', '实际原件.docx', '实际图片.png', '另一份.md'):
        refs[path] = evidence.call('GET', base + f'/local-workspaces/{bound["id"]}/file', params={'path': path})['source_ref']
    return {'pid': pid, 'base': base, 'folder': folder, 'refs': refs, 'bound': bound, 'evidence': evidence,
        'originals': {p: (folder / p).read_bytes() for p in refs}}


@pytest.fixture
def scene(client, tmp_path, request):
    if not getattr(client.app.state, 'native_feedback', None):
        from apps.api.native_feedback import register_native_feedback_routes
        register_native_feedback_routes(client.app, client.app.state.store)
    parent = Path(os.environ.get('EEAS_NATIVE_FEEDBACK_EVIDENCE', tmp_path / 'evidence'))
    evidence = Evidence(client, parent / request.node.name)
    value = prepare(evidence, tmp_path.parent / (tmp_path.name + '-real-selected-files'))
    value.update(store=client.app.state.store, service=client.app.state.native_feedback, client=client, wires=[])
    try:
        yield value
    finally:
        retain(value)


def retain(scene):
    evidence = scene['evidence']
    evidence.details['wire_requests'] = [w.requests for w in scene.get('wires', [])]
    if scene.get('store'):
        with scene['store'].connect() as db:
            rows = db.execute('SELECT * FROM agent_executions WHERE project_id=?', (scene['pid'],)).fetchall()
            evidence.details['executions'] = [dict(row) for row in rows]
            evidence.details['task_runs'] = db.execute('SELECT count(*) FROM task_runs').fetchone()[0]
        for i, row in enumerate(rows):
            target = evidence.directory / ('original-native-' + str(i + 1)); target.mkdir(exist_ok=True)
            for name in ('http-starts.jsonl', 'http-responses.jsonl', 'native-events.jsonl', 'planning-native-result.json'):
                path = Path(row['workspace']) / name
                if path.is_file():
                    (target / name).write_bytes(path.read_bytes().replace(KEY.encode(), b'<fixture-credential-redacted>'))
    shutil.copytree(scene['folder'], evidence.directory / 'selected-files-final', dirs_exist_ok=True)
    if scene.get('archived_folder'):
        shutil.copytree(scene['archived_folder'], evidence.directory / 'preserved-original-folder', dirs_exist_ok=True)
    evidence.flush()
    for wire in scene.get('wires', []):
        wire.close()


def body(scene, name='说明.md', op='feedback'):
    return {'operation_id': op, 'file_source_ref': scene['refs'][name], 'comment': '请核对当前来源，保留人工改动。',
        'mode': 'SUBMITTED', 'selection': {'kind': 'USER_DESCRIPTION', 'text': '用户描述原软件里的所选对象。'},
        'context_ref': {'id': 'user-attached-context', 'window_title': '另一个文件名称不能充当来源'},
        'frame_ref': {'id': 'user-attached-frame', 'sha256': 'a' * 64},
        'normalized_regions': [{'x': .1, 'y': .2, 'width': .3, 'height': .4}]}


def record(scene, request_body):
    return scene['evidence'].call('POST', scene['base'] + '/native-feedback', request_body, 201)


@pytest.mark.parametrize('name', ['实际原件.docx', '实际图片.png'])
def test_real_binary_file_feedback_is_durable_but_has_no_mutation_adapter(scene, name):
    request_body = body(scene, name)
    value = record(scene, request_body)
    check(value['mutation_status'] == 'NOT_ADAPTED' and value['current_file_check']['status'] == 'CURRENT')
    check(value['payload']['baseline_snapshot']['sha256'] == sha(scene['originals'][name]))
    screen = value['payload']['screen_annotations']
    check(not screen['grants_file_authority'] and not screen['object_identity_verified'])
    check(screen['verification'] == 'CALLER_ATTACHED_NOT_QUALIFIED')
    check(not value['payload']['write_authorized'] and not value['payload']['model_dispatched'])
    restored = NativeFeedback(scene['store'], scene['client'].app.state.local_workspaces,
        direct_actions=scene['client'].app.state.direct_actions)
    durable = restored.get(scene['pid'], value['feedback_ref']['id'])
    check(durable['payload'] == value['payload'] and durable['feedback_ref'] == value['feedback_ref'])
    changed = scene['originals'][name] + b'actual user edit'
    (scene['folder'] / name).write_bytes(changed)
    replay = record(scene, request_body)
    check(replay['current_file_check']['status'] == 'STALE')
    check(replay['payload'] == value['payload'] and replay['feedback_ref'] == value['feedback_ref'])
    check(replay['current_file_check']['current_source_ref']['sha256'] == sha(changed))
    check((scene['folder'] / name).read_bytes() == changed)
    check(scene['evidence'].call('GET', scene['base'] + '/native-feedback/operations/feedback')['feedback_ref'] == value['feedback_ref'])
    scene['evidence'].call('POST', scene['base'] + f'/native-feedback/{value["feedback_ref"]["id"]}/direct-action-links',
        {'operation_id': 'binary-link-blocked', 'feedback_ref': value['feedback_ref'], 'direct_operation_id': 'never-started'}, 409)
    with scene['store'].connect() as db:
        check(db.execute('SELECT count(*) FROM agent_executions').fetchone()[0] == 0)


def test_exact_text_quote_verification_and_human_versions_stay_separate(scene):
    request_body = body(scene)
    text = ORIGINAL.decode(); start = text.index('Firsyt')
    request_body['selection'] = {'kind': 'TEXT_RANGE', 'char_start': start, 'char_end': start + 6,
        'quote_sha256': sha('Firsyt'.encode())}
    value = record(scene, request_body)
    check(value['payload']['selection_verification'] == 'TEXT_RANGE_HASH_VERIFIED')
    check('requested_selection' not in value['payload'])
    check(value['mutation_status'] == 'EXISTING_DIRECT_ACTION_AVAILABLE')
    (scene['folder'] / '说明.md').write_bytes(ORIGINAL + '人工补充：保留。\n'.encode())
    old = scene['evidence'].call('GET', scene['base'] + f'/native-feedback/{value["feedback_ref"]["id"]}')
    check(old['current_file_check']['status'] == 'STALE' and old['payload'] == value['payload'])
    (scene['folder'] / '说明.md').write_bytes(ORIGINAL)
    back = scene['evidence'].call('GET', scene['base'] + f'/native-feedback/{value["feedback_ref"]["id"]}')
    check(back['current_file_check']['status'] == 'STALE', 'new observed version silently became the old baseline')
    check(back['current_file_check']['live_sha256'] == scene['refs']['说明.md']['sha256'])
    check(back['current_file_check']['current_source_ref']['revision'] > scene['refs']['说明.md']['revision'])


def quote_source(scene, raw, name='说明.md'):
    (scene['folder'] / name).write_bytes(raw)
    scene['originals'][name] = raw
    scene['refs'][name] = scene['evidence'].call('GET',
        scene['base'] + f'/local-workspaces/{scene["bound"]["id"]}/file', params={'path': name})['source_ref']


@pytest.mark.parametrize('text,quote', [
    ('前😀缀\r\n  原句😀 \r\n结尾', '  原句😀 \r\n'),
    ('\ufeff原句\r\n余文', '\ufeff原句'),
    ('前\n   \n后', '   '),
    ('前😀\n结尾', '😀'),
    ('前' + '句' * 4000 + '后', '句' * 4000),
], ids=['unicode-crlf-spaces', 'bom', 'only-spaces', 'emoji', 'max4000'])
def test_exact_quote_preserves_unicode_newlines_bom_and_whitespace(scene, text, quote):
    quote_source(scene, text.encode('utf-8'))
    request_body = body(scene)
    request_body['selection'] = {'kind': 'EXACT_QUOTE', 'quote': quote}
    value = record(scene, request_body)
    payload = value['payload']; start = text.index(quote)
    check(payload['requested_selection'] == request_body['selection'])
    check(payload['file_selection'] == {'kind': 'TEXT_RANGE', 'char_start': start,
        'char_end': start + len(quote), 'quote_sha256': sha(quote.encode('utf-8'))})
    check(payload['selection_verification'] == 'TEXT_RANGE_HASH_VERIFIED')
    check(payload['baseline_snapshot']['sha256'] == sha(text.encode('utf-8')))
    check(not payload['write_authorized'] and not payload['model_dispatched'])
    check((scene['folder'] / '说明.md').read_bytes() == text.encode('utf-8'))


@pytest.mark.parametrize('raw,quote,status,name', [
    (b'actual source', 'missing', 'QUOTE_NOT_FOUND', '说明.md'),
    ('原句与原句'.encode(), '原句', 'QUOTE_AMBIGUOUS', '说明.md'),
    (b'aaa', 'aa', 'QUOTE_AMBIGUOUS', '说明.md'),
    (b'first\r\nsecond', 'first\nsecond', 'QUOTE_NOT_FOUND', '说明.md'),
    (b'\xffactual', 'actual', 'QUOTE_NOT_ADAPTED', '说明.md'),
    (b'actual\x00source', 'actual', 'QUOTE_NOT_ADAPTED', '说明.md'),
    (b'x' * (MAX_EDIT_BYTES + 1), 'x', 'QUOTE_NOT_ADAPTED', '说明.md'),
    (b'actual text with binary suffix', 'actual', 'QUOTE_NOT_ADAPTED', '实际原件.docx'),
], ids=['missing', 'duplicate', 'overlapping', 'newline-not-normalized', 'invalid-utf8',
    'nul', 'over-edit-limit', 'binary-suffix'])
def test_unresolved_exact_quote_is_durable_without_range_or_authority(scene, raw, quote, status, name):
    quote_source(scene, raw, name)
    request_body = body(scene, name)
    request_body['selection'] = {'kind': 'EXACT_QUOTE', 'quote': quote}
    value = record(scene, request_body)
    check(value['payload']['requested_selection'] == request_body['selection'])
    check(value['payload']['selection_verification'] == status and value['payload']['file_selection'] is None)
    check(not value['payload']['write_authorized'] and not value['payload']['model_dispatched'])
    restored = NativeFeedback(scene['store'], scene['client'].app.state.local_workspaces)
    check(restored.operation(scene['pid'], 'feedback')['payload'] == value['payload'])
    check((scene['folder'] / name).read_bytes() == raw)
    with scene['store'].connect() as db:
        check(db.execute('SELECT count(*) FROM agent_executions').fetchone()[0] == 0)


@pytest.mark.parametrize('selection', [
    {'kind': 'EXACT_QUOTE'}, {'kind': 'EXACT_QUOTE', 'quote': ''},
    {'kind': 'EXACT_QUOTE', 'quote': 'x' * 4001}, {'kind': 'EXACT_QUOTE', 'quote': 123},
    {'kind': 'EXACT_QUOTE', 'quote': '原句', 'char_start': 0},
], ids=['missing', 'empty', 'over4000', 'not-string', 'extra-offset'])
def test_exact_quote_rejects_invalid_input_without_record_or_write(scene, selection):
    request_body = body(scene); request_body['selection'] = selection
    scene['evidence'].call('POST', scene['base'] + '/native-feedback', request_body, 422)
    scene['evidence'].call('GET', scene['base'] + '/native-feedback/operations/feedback', status=404)
    check((scene['folder'] / '说明.md').read_bytes() == scene['originals']['说明.md'])


def test_exact_quote_replay_get_and_project_identity_preserve_the_original_request(scene):
    request_body = body(scene); request_body['selection'] = {'kind': 'EXACT_QUOTE', 'quote': 'Firsyt'}
    value = record(scene, request_body)
    changed = {**request_body, 'selection': {'kind': 'EXACT_QUOTE', 'quote': 'First'}}
    scene['evidence'].call('POST', scene['base'] + '/native-feedback', changed, 409)
    human = ORIGINAL + '独立人工追加😀\r\n'.encode()
    (scene['folder'] / '说明.md').write_bytes(human)
    replay = record(scene, request_body)
    check(replay['payload'] == value['payload'] and replay['feedback_ref'] == value['feedback_ref'])
    check(replay['current_file_check']['status'] == 'STALE')
    original = scene['evidence'].call('GET', scene['base'] + '/native-feedback/operations/feedback')
    check(original['payload']['requested_selection'] == request_body['selection'])
    scene['evidence'].call('GET', scene['base'] + '/native-feedback/operations/another-operation', status=404)
    other = scene['evidence'].call('POST', '/api/projects', {'name': '另一个实际项目'}, 201)['id']
    scene['evidence'].call('POST', f'/api/projects/{other}/native-feedback', request_body, 422)
    scene['evidence'].call('GET', f'/api/projects/{other}/native-feedback/{value["feedback_ref"]["id"]}', status=404)
    check((scene['folder'] / '说明.md').read_bytes() == human)


@pytest.mark.parametrize('selection', [None, {'kind': 'USER_DESCRIPTION', 'text': '原描述'}])
def test_legacy_feedback_has_no_requested_selection_key(scene, selection):
    request_body = body(scene); request_body['selection'] = selection
    value = record(scene, request_body)
    check('requested_selection' not in value['payload'])
    check(value['payload']['file_selection'] == selection)


@pytest.mark.parametrize('fault', ['missing-file-ref', 'fake-sha', 'cross-project', 'bad-quote', 'binary-text-range',
    'region-outside', 'region-without-frame', 'region-boolean', 'injected-write-authority', 'title-as-path'])
def test_unqualified_targets_selection_and_screen_data_never_grant_mutation(scene, fault):
    request_body = body(scene)
    expected = 422
    if fault == 'missing-file-ref':
        del request_body['file_source_ref']
    elif fault == 'fake-sha':
        request_body['file_source_ref'] = {**request_body['file_source_ref'], 'sha256': '0' * 64}; expected = 409
    elif fault == 'cross-project':
        request_body['file_source_ref'] = {**request_body['file_source_ref'], 'project_id': 'unrelated-project'}
    elif fault == 'bad-quote':
        request_body['selection'] = {'kind': 'TEXT_RANGE', 'char_start': 0, 'char_end': 3, 'quote_sha256': '0' * 64}; expected = 409
    elif fault == 'binary-text-range':
        request_body['file_source_ref'] = scene['refs']['实际原件.docx']
        request_body['selection'] = {'kind': 'TEXT_RANGE', 'char_start': 0, 'char_end': 3, 'quote_sha256': sha(b'PK\x03')}
    elif fault == 'region-outside':
        request_body['normalized_regions'][0]['width'] = .95
    elif fault == 'region-without-frame':
        request_body['frame_ref'] = None
    elif fault == 'region-boolean':
        request_body['normalized_regions'][0]['x'] = True
    elif fault == 'injected-write-authority':
        request_body['user_requested_write'] = True
    elif fault == 'title-as-path':
        request_body['file_source_ref'] = {**request_body['file_source_ref'], 'path': '另一个文件名称不能充当来源'}; expected = 409
    scene['evidence'].call('POST', scene['base'] + '/native-feedback', request_body, expected)
    check(all((scene['folder'] / p).read_bytes() == raw for p, raw in scene['originals'].items()))
    with scene['store'].connect() as db:
        check(db.execute('SELECT count(*) FROM agent_executions').fetchone()[0] == 0)


def test_comment_idempotency_and_missing_file_state_survive_restart(scene):
    request_body = body(scene)
    value = record(scene, request_body)
    changed = {**request_body, 'comment': '另一个意见不是同一次操作。'}
    scene['evidence'].call('POST', scene['base'] + '/native-feedback', changed, 409)
    (scene['folder'] / '说明.md').unlink()
    durable = NativeFeedback(scene['store'], scene['client'].app.state.local_workspaces)
    old = durable.operation(scene['pid'], request_body['operation_id'])
    check(old['feedback_ref'] == value['feedback_ref'] and old['current_file_check']['status'] == 'MISSING')
    check(old['payload']['comment'] == request_body['comment'] and not old['execution_authorized'])


@pytest.mark.parametrize('concurrent', [False, True])
def test_real_original_cli_before_after_link_or_cas_conflict_preserves_human_edit(scene, concurrent):
    seed_cli(scene['client'].app.state.settings.data_dir)
    human = ORIGINAL + '模型进行期间的人工作品。\n'.encode()
    callback = (lambda: (scene['folder'] / '说明.md').write_bytes(human)) if concurrent else None
    wire = DirectModelWire(callback=callback).start(); scene['wires'].append(wire)
    profile = scene['evidence'].call('POST', '/api/planning/providers', {'operation_id': 'actual-original-provider',
        'id': 'native-feedback-provider', 'provider_url': wire.base_url, 'model_id': MODEL, 'api_key': KEY}, 201)['profile_ref']
    value = record(scene, body(scene))
    result = scene['evidence'].call('POST', scene['base'] + '/direct-actions', {'operation_id': 'native-direct-edit',
        'file_ref': scene['refs']['说明.md'], 'instruction': '纠正Firsyt为First，保留其他全部内容。',
        'user_requested_write': True, 'provider_profile_ref': profile, 'budget': BUDGET}, 422 if concurrent else 200)
    check(result['status'] == ('FAILED' if concurrent else 'APPLIED'), json.dumps(result, ensure_ascii=False))
    check(result['before_ref'] == value['payload']['baseline_file_source_ref'])
    link_body = {'operation_id': 'link-actual-direct', 'feedback_ref': value['feedback_ref'], 'direct_operation_id': 'native-direct-edit'}
    link = scene['evidence'].call('POST', scene['base'] + f'/native-feedback/{value["feedback_ref"]["id"]}/direct-action-links', link_body, 201)
    check(link['payload']['action_receipt']['status'] == result['status'])
    check(not link['payload']['feedback_granted_write_authority'] and not link['payload']['model_dispatched'])
    if concurrent:
        check((scene['folder'] / '说明.md').read_bytes() == human and result['after_ref'] is None)
        check(link['current_file_check']['status'] == 'STALE')
    else:
        check(result['after_ref'] == link['payload']['action_receipt']['after_ref'])
        check((scene['folder'] / '说明.md').read_bytes() == ORIGINAL.replace(b'Firsyt', b'First'))
        check(link['current_file_check']['status'] == 'CURRENT')
        check(link['payload']['action_receipt']['execution_ref']['session_id'].startswith('ses_'))
        independent = record(scene, body(scene, '另一份.md', 'independent-file-feedback'))
        scene['evidence'].call('POST', scene['base'] + f'/native-feedback/{independent["feedback_ref"]["id"]}/direct-action-links',
            {'operation_id': 'wrong-file-link', 'feedback_ref': independent['feedback_ref'],
                'direct_operation_id': 'native-direct-edit'}, 409)
    replay = scene['evidence'].call('POST', scene['base'] + f'/native-feedback/{value["feedback_ref"]["id"]}/direct-action-links', link_body, 201)
    check(replay['link_ref'] == link['link_ref'] and len(wire.requests) == 1)
    restored = NativeFeedback(scene['store'], scene['client'].app.state.local_workspaces,
        direct_actions=scene['client'].app.state.direct_actions)
    saved = restored.get(scene['pid'], value['feedback_ref']['id'])
    check(saved['direct_action_links'][0]['link_ref'] == link['link_ref'])
    check(saved['current_file_check']['status'] == 'STALE', 'feedback baseline changed after action')
    check((scene['folder'] / '另一份.md').read_bytes() == scene['originals']['另一份.md'])
    with scene['store'].connect() as db:
        check(db.execute('SELECT count(*) FROM task_runs').fetchone()[0] == 0)
        check(db.execute('SELECT count(*) FROM planning_proposals').fetchone()[0] == 0)


def test_cannot_link_action_without_matching_real_before_ref(scene):
    value = record(scene, body(scene))
    missing = scene['evidence'].call('POST', scene['base'] + '/direct-actions', {'operation_id': 'no-provider',
        'instruction': '解释此文件', 'file_ref': scene['refs']['说明.md']})
    check(missing['status'] == 'NEEDS_INPUT' and missing['before_ref'] is None)
    scene['evidence'].call('POST', scene['base'] + f'/native-feedback/{value["feedback_ref"]["id"]}/direct-action-links',
        {'operation_id': 'cannot-forge-link', 'feedback_ref': value['feedback_ref'], 'direct_operation_id': 'no-provider'}, 409)
    current = scene['evidence'].call('GET', scene['base'] + '/native-feedback/operations/cannot-forge-link')
    check(current['status'] == 'INCOMPLETE_REVIEW_REQUIRED' and not current['automatic_retry'])


def test_replaced_selected_folder_is_binding_changed_not_a_new_file_grant(scene):
    value = record(scene, body(scene, '实际原件.docx'))
    archived = scene['folder'].with_name(scene['folder'].name + '-preserved-original')
    scene['folder'].rename(archived)
    scene['folder'].mkdir()
    scene['archived_folder'] = archived
    (scene['folder'] / '实际原件.docx').write_bytes(b'different folder is not the old source')
    old = scene['evidence'].call('GET', scene['base'] + f'/native-feedback/{value["feedback_ref"]["id"]}')
    check(old['payload'] == value['payload'] and old['current_file_check']['status'] == 'BINDING_CHANGED')
    check(old['current_file_check']['current_source_ref'] is None and not old['execution_authorized'])
    check((archived / '实际原件.docx').read_bytes() == scene['originals']['实际原件.docx'])


def test_actual_http_restart_replays_binary_feedback_with_current_user_bytes(tmp_path, request):
    data = tmp_path / 'actual-controller'; data.mkdir()
    server = LiveServer(data)
    if sys.flags.optimize:
        server.env['PYTHONOPTIMIZE'] = str(sys.flags.optimize)
    parent = Path(os.environ.get('EEAS_NATIVE_FEEDBACK_EVIDENCE', tmp_path / 'evidence'))
    scene_value = None
    @contextmanager
    def connect():
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=40) as client:
            token = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'}).json()['csrf_token']
            client.headers.update({'Origin': server.origin, 'X-EEAS-CSRF': token})
            yield client
    try:
        server.start()
        with connect() as client:
            evidence = Evidence(client, parent / request.node.name)
            scene_value = prepare(evidence, tmp_path / 'real-selected-files')
            request_body = body(scene_value, '实际原件.docx')
            value = record(scene_value, request_body)
        (scene_value['folder'] / '实际原件.docx').write_bytes(scene_value['originals']['实际原件.docx'] + b'USER-EDIT')
        server.stop(); server.start()
        with connect() as client:
            scene_value['evidence'].client = client
            replay = record(scene_value, request_body)
            check(replay['feedback_ref'] == value['feedback_ref'] and replay['payload'] == value['payload'])
            check(replay['current_file_check']['status'] == 'STALE' and replay['mutation_status'] == 'NOT_ADAPTED')
            check((scene_value['folder'] / '实际原件.docx').read_bytes().endswith(b'USER-EDIT'))
    finally:
        if scene_value:
            retain(scene_value)
        server.stop()
