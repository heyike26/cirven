"""Real create_app/API/SQLite/OOXML and one original ordinary CLI/tool round trip.

The only model is a loopback fixture. A read-only POSIX adapter observes the
actual pytest process PID/birth/session for the host-lifetime boundary. It has
no window, capture or input implementation and is NOT Windows qualification.
"""
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import time

from fastapi.testclient import TestClient
import psutil
import pytest

from apps.api.config import Settings
from apps.api.main import create_app
from packages.domain.local_workspaces import sha
from packages.domain.store import Conflict, NotFound
from test_file_continuity import docx
from native_docx_wire_fixture import RevisionWire, configure
from test_native_open import report


HOST_TOKEN = 'd' * 64
MESSAGES = '/api/desktop-host/native-messages'


def check(condition, message='native file continuity API requirement failed'):
    if not condition:
        raise AssertionError(message)


class ActualPosixHostLifetime:
    qualification = 'REAL_POSIX_PROCESS_LIFETIME_ONLY_NOT_WINDOWS'

    def process_identity(self, pid):
        process = psutil.Process(pid)
        return {'pid': process.pid, 'process_creation': int(process.create_time() * 1_000_000_000),
            'os_session': os.getsid(process.pid)}


@contextmanager
def app_client(settings, *, actual_posix_host=True):
    app = create_app(settings)
    if actual_posix_host:
        authority = app.state.native_messages.ws
        check(authority.owner_pid == os.getpid())
        authority.native = ActualPosixHostLifetime()
        authority.owner_identity = authority.native.process_identity(os.getpid())
    with TestClient(app, base_url=settings.origin) as client:
        csrf = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'}).json()['csrf_token']
        client.headers.update({'Origin': settings.origin, 'X-EEAS-CSRF': csrf, 'X-EEAS-Desktop-Host': HOST_TOKEN})
        yield client


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv('EEAS_DESKTOP_HOST_TOKEN', HOST_TOKEN)
    monkeypatch.setenv('EEAS_DESKTOP_HOST_PID', str(os.getpid()))
    settings = Settings(tmp_path / 'controller')
    folder = tmp_path / '原创项目'
    folder.mkdir()
    (folder / '材料.md').write_text('来自真实文件的任务要求。', encoding='utf-8')
    with app_client(settings) as client:
        response = client.post('/api/projects', json={'name': '真实文件连续性'})
        check(response.status_code == 201, response.text)
        pid = response.json()['id']
        binding = client.post(f'/api/projects/{pid}/local-workspaces', json={
            'idempotency_key': 'original-folder', 'root': str(folder), 'allowed_write_paths': []})
        check(binding.status_code == 201, binding.text)
        base = f'/api/projects/{pid}/local-workspaces/{binding.json()["id"]}'
        source = client.get(base + '/file', params={'path': '材料.md'}).json()['source_ref']
        # Same tuple contract as test_native_open.scene, so its real report
        # generator is reused without replacing report/storage implementations.
        yield client, pid, folder, base, source, settings


def opened_report(scene):
    client, pid, *_ = scene
    artifact = report(scene)
    original = client.get(artifact['download_url']).content
    response = client.post('/api/desktop-host/native-open/report', json={
        'project_id': pid, 'artifact_id': artifact['id'], 'sha256': artifact['sha256'],
        'download_url': artifact['download_url']})
    check(response.status_code == 200, response.text)
    value = response.json()
    check(value['file_link_ref']['project_id'] == pid)
    check(value['active_document_identity_verified'] is False)
    return artifact, original, Path(value['native_path']), value['file_link_ref']


def send(client, pid, link, operation='question', **extra):
    return client.post(MESSAGES, json={'operation_id': operation, 'raw_text': '刚刚保存改了文稿，按批注继续。',
        'project_id': pid, 'file_link_ref': link, 'conversation_id': 'original_conversation', **extra})


def test_default_wsl_without_native_lifetime_remains_unavailable_not_fake_windows(tmp_path, monkeypatch):
    monkeypatch.setenv('EEAS_DESKTOP_HOST_TOKEN', HOST_TOKEN)
    monkeypatch.setenv('EEAS_DESKTOP_HOST_PID', str(os.getpid()))
    with app_client(Settings(tmp_path / 'controller'), actual_posix_host=False) as client:
        check(client.app.state.native_messages.ws.native is None)
        response = client.post(MESSAGES, json={'operation_id': 'no-host', 'raw_text': '解释当前文件'})
        check(response.status_code == 409, response.text)
        check(client.app.state.native_messages.transport.active == {})


def test_full_report_saved_edit_freezes_without_provider_and_replay_never_reobserves(scene):
    client, pid, folder, base, source, settings = scene
    artifact, immutable, path, link = opened_report(scene)
    first_saved = docx('原软件保存的正文版本甲', '文稿已关闭，按这条保存的批注补依据')
    path.write_bytes(first_saved)  # Real file handle is closed; no window exists.
    response = send(client, pid, link)
    check(response.status_code == 200, response.text)
    result = response.json()
    check(result['status'] == 'NOT_CONFIGURED' and result['project_id'] == pid)
    check(result['execution_effects'] == 'READONLY' and result['changes_applied'] is False)
    observation = result['file_observation']
    check(observation['current_sha256'] == sha(first_saved) and observation['status'] == 'CHANGED')
    check(observation['comments'][0]['text'] == '文稿已关闭，按这条保存的批注补依据')
    check(observation['unsaved_changes'] == 'NOT_OBSERVED' and result['context_ref'] is None)
    second_saved = docx('后来另外保存的正文乙', '不属于前一次提问的后续批注')
    path.write_bytes(second_saved)
    replay = send(client, pid, link).json()
    check(replay['file_observation'] == observation, 'same operation cannot silently replace its file basis')
    text = client.app.state.file_continuity.read_text(observation['snapshot_ref'], limit=16000)
    check('正文版本甲' in text['text_content'] and '正文乙' not in text['text_content'])
    check(client.get(MESSAGES + '/question').json()['file_observation'] == observation)
    check(client.get(artifact['download_url']).content == immutable)
    check(path.read_bytes() == second_saved)
    check(client.app.state.native_messages.transport.active == {})
    check(not (settings.data_dir / 'native-collaboration' / 'context-rpc' / 'contexts').exists(),
        'file continuation must not request a fresh screenshot/current-window context')
    other = send(client, pid, link, operation='question-next').json()
    check(other['file_observation']['current_sha256'] == sha(second_saved))
    check(other['file_observation']['snapshot_ref'] != observation['snapshot_ref'])


def test_workspace_native_open_returns_persistent_link_and_history_is_project_and_conversation_scoped(scene):
    client, pid, folder, base, source, settings = scene
    opened = client.post('/api/desktop-host/native-open/file', json={'project_id': pid, 'file_source_ref': source})
    check(opened.status_code == 200, opened.text)
    link = opened.json()['file_link_ref']
    check(link['project_id'] == pid)
    first = send(client, pid, link, operation='project-a-old', raw_text='甲工程会话应保留').json()
    check(first['status'] == 'NOT_CONFIGURED')
    second_pid = client.post('/api/projects', json={'name': '真实文件连续性'}).json()['id']
    for body in [
        {'operation_id': 'project-b-old', 'raw_text': '乙工程不可串入', 'project_id': second_pid, 'conversation_id': 'original_conversation'},
        {'operation_id': 'unbound-old', 'raw_text': '无工程不可串入', 'conversation_id': 'original_conversation'},
        {'operation_id': 'other-conversation', 'raw_text': '另会话不可串入', 'project_id': pid, 'conversation_id': 'different_conversation'},
    ]:
        response = client.post(MESSAGES, json=body)
        check(response.status_code == 200, response.text)
    current = send(client, pid, link, operation='project-a-current')
    check(current.status_code == 200, current.text)
    messages = client.app.state.native_messages
    # Callback contract reads the actual durable HTTP question and real result
    # rows. Original MCP dispatch is separately exercised in the runtime test.
    history = messages.read_history({}, {'request': {'operation_id': 'project-a-current'}})
    check([item['user_text'] for item in history['messages']] == ['甲工程会话应保留'])
    check(all(item['scope'] == 'HISTORICAL_DATA_NO_NATIVE_AUTHORITY' for item in history['messages']))
    check(messages.transport.active == {})


def test_api_rejects_cross_project_ambiguous_sources_arbitrary_paths_and_unknown_references(scene):
    client, pid, folder, base, source, settings = scene
    opened = client.post('/api/desktop-host/native-open/file', json={'project_id': pid, 'file_source_ref': source}).json()
    link = opened['file_link_ref']
    other_pid = client.post('/api/projects', json={'name': '另一个工程'}).json()['id']
    invalid = [
        ({'project_id': other_pid}, 422),
        ({'file_source_ref': source}, 422),
        ({'native_path': str(folder / '材料.md')}, 422),
        ({'snapshot_ref': {'project_id': pid, 'id': 'f' * 64, 'revision': 1, 'sha256': 'e' * 64}}, 422),
        ({'file_link_ref': {**link, 'id': 'f' * 64}}, 404),
        ({'file_link_ref': {**link, 'sha256': 'e' * 64}}, 409),
        ({'file_link_ref': {**link, 'project_id': other_pid}, 'project_id': other_pid}, 409),
    ]
    for index, (extra, expected) in enumerate(invalid):
        response = send(client, pid, link, operation='invalid_' + str(index), **extra)
        check(response.status_code == expected, response.text)
    with pytest.raises(NotFound):
        client.app.state.file_continuity.read_text({'project_id': pid, 'id': 'f' * 64, 'revision': 1, 'sha256': 'e' * 64})
    client.headers.pop('X-EEAS-Desktop-Host')
    denied = send(client, pid, link, operation='wrong-host')
    check(denied.status_code == 409, denied.text)
    check((folder / '材料.md').read_text(encoding='utf-8') == '来自真实文件的任务要求。')


def test_malformed_saved_docx_and_budget_refusal_are_explicit_and_preserve_user_file(scene):
    client, pid, folder, base, source, settings = scene
    artifact, immutable, path, link = opened_report(scene)
    first = send(client, pid, link, operation='before-corruption').json()['file_observation']
    broken = b'PK\x03\x04incomplete user save'
    path.write_bytes(broken)
    malformed = send(client, pid, link, operation='partial-save')
    check(malformed.status_code == 200, malformed.text)
    observation = malformed.json()['file_observation']
    check(observation['parsed']['status'] == 'UNAVAILABLE')
    check(observation['warnings'] == ['PARSER_UNAVAILABLE_OR_MALFORMED_DOCUMENT'])
    check(observation['comments'] == [] and observation['current_sha256'] == sha(broken))
    check(path.read_bytes() == broken)
    client.app.state.file_continuity.max_total_snapshot_bytes = 1
    latest = docx('预算不足也不能覆写这份人工稿', '等待容量问题处理')
    path.write_bytes(latest)
    rejected = send(client, pid, link, operation='over-budget')
    check(rejected.status_code == 409, rejected.text)
    check(client.get(MESSAGES + '/over-budget').status_code == 404)
    check(path.read_bytes() == latest and client.get(artifact['download_url']).content == immutable)
    check(bool(client.app.state.file_continuity.read_text(first['snapshot_ref'])['text_content']))
    check(client.app.state.native_messages.transport.active == {})


def test_frozen_document_question_persists_across_real_app_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('EEAS_DESKTOP_HOST_TOKEN', HOST_TOKEN)
    monkeypatch.setenv('EEAS_DESKTOP_HOST_PID', str(os.getpid()))
    settings = Settings(tmp_path / 'controller')
    folder = tmp_path / '工程'
    folder.mkdir()
    path = folder / '要求.txt'
    path.write_text('重启前保存的真实正文', encoding='utf-8')
    with app_client(settings) as client:
        pid = client.post('/api/projects', json={'name': '重启验证'}).json()['id']
        binding = client.post(f'/api/projects/{pid}/local-workspaces', json={
            'idempotency_key': 'folder', 'root': str(folder)}).json()
        source = client.get(f'/api/projects/{pid}/local-workspaces/{binding["id"]}/file', params={'path': path.name}).json()['source_ref']
        link = client.post('/api/desktop-host/native-open/file', json={'project_id': pid, 'file_source_ref': source}).json()['file_link_ref']
        result = send(client, pid, link, operation='restart-question').json()
        frozen = result['file_observation']
    path.write_text('重启后另改的内容', encoding='utf-8')
    with app_client(settings) as restarted:
        loaded = restarted.get(MESSAGES + '/restart-question')
        check(loaded.status_code == 200, loaded.text)
        check(loaded.json()['record_origin'] == 'HISTORICAL_READONLY')
        check(loaded.json()['file_observation'] == frozen)
        check(restarted.app.state.file_continuity.read_text(frozen['snapshot_ref'])['text_content'] == '重启前保存的真实正文')
        check(restarted.app.state.native_messages.transport.active == {})


def test_original_cli_ordinary_tool_reads_frozen_saved_docx_after_later_human_save(scene):
    client, pid, folder, base, source, settings = scene
    artifact, immutable, path, link = opened_report(scene)
    saved = docx('发问时已保存的模型依据', '请补上可复核的依据')
    path.write_bytes(saved)
    wire = RevisionWire(read_only=True, gate_model=True)
    messages = client.app.state.native_messages
    try:
        profile = configure(client, settings, wire)
        response = send(client, pid, link, operation='original-mcp-question', provider_profile_ref=profile)
        check(response.status_code == 200 and response.json()['status'] == 'PROCESSING', response.text)
        check(wire.entered.wait(75), 'original CLI did not reach the actual loopback model gateway')
        later = docx('回答期间用户继续保存的另一稿', '此后改稿不应混入旧问答')
        path.write_bytes(later)
        wire.release.set()
        deadline = time.monotonic() + 90
        result = {}
        while time.monotonic() < deadline:
            value = client.get(MESSAGES + '/original-mcp-question')
            check(value.status_code == 200, value.text)
            result = value.json()
            if result['status'] != 'PROCESSING':
                break
            time.sleep(.1)
        check(result.get('status') == 'RETURNED', repr(result))
        check(result['native_cohort']['native_session_id'].startswith('ses_'))
        check(result['answer_source'] == 'ORIGINAL_NATIVE_TEXT_JSON_VALIDATED')
        check(len(wire.calls) == 2 and len(wire.results) == 1)
        read = wire.results[0]['result']
        check(read['snapshot_ref'] == result['file_observation']['snapshot_ref'])
        check(read['scope'] == 'EXACT_FROZEN_REGISTERED_FILE_SNAPSHOT' and read['current_sha256'] == sha(saved))
        check('发问时已保存的模型依据' in read['text_content'] and '另一稿' not in read['text_content'])
        check(read['comments'][0]['text'] == '请补上可复核的依据')
        check(len(read['text_content']) <= 4000 and not read['automatic_input_authorized'])
        check(result['changes_applied'] is False and path.read_bytes() == later)
        check(client.get(artifact['download_url']).content == immutable)
        admission = messages.ordinary.validate_admission(result['native_message_ref'])
        binding = {'request': deepcopy(admission['request']), 'native_cohort': result['native_cohort']}
        with pytest.raises(Conflict):
            messages.read_linked_file({'snapshot_ref': result['file_observation']['snapshot_ref']}, binding)
        other_pid = client.post('/api/projects', json={'name': '不能偷换的项目'}).json()['id']
        cross = deepcopy(binding)
        cross['request']['project_id'] = other_pid
        with pytest.raises(Conflict):
            messages.read_linked_file({}, cross)
        replay = send(client, pid, link, operation='original-mcp-question',
            provider_profile_ref=profile)
        check(replay.status_code == 200 and replay.json()['native_cohort'] == result['native_cohort'])
        check(replay.json()['file_observation'] == result['file_observation'] and len(wire.calls) == 2)
    finally:
        wire.release.set()
        wire.close()
        check(not wire.thread.is_alive(), 'the owned external-model fixture thread must actually stop')
