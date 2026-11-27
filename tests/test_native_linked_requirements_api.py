"""Real App/SQLite/OOXML and original CLI/ordinary report context tool.

The model is a loopback fixture; the host seam checks actual POSIX process
lifetime only. This is not Windows, Office, subscription or paid-model QA.
"""
from copy import deepcopy
import os
from pathlib import Path
import time

import pytest

from packages.adapters.opencode_profile import CLI_SHA
from packages.domain.linked_requirements import FrozenLinkedRequirements
from packages.domain.local_workspaces import canonical, sha
from packages.domain.store import Conflict
from test_file_continuity import docx
from test_native_file_continuity_api import scene, opened_report, send, app_client, MESSAGES
from native_docx_wire_fixture import RevisionWire, configure


def check(value, message='native linked original requirements contract failed'):
    if not value:
        raise AssertionError(message)


def binding(operation, pid, text='刚刚保存改了文稿，按批注继续。'):
    return {'request': {'operation_id': operation, 'project_id': pid, 'prompt': text}}


def test_scope_arguments_old_generation_and_host_authority_cannot_select_other_report(scene):
    client, pid, *_ = scene
    artifact, immutable, path, link = opened_report(scene)
    value = send(client, pid, link, operation='bound-question')
    check(value.status_code == 200, value.text)
    messages = client.app.state.native_messages
    exact = binding('bound-question', pid)
    reader = messages.read_linked_requirements
    check(reader({}, exact)['report_artifact_ref']['id'] == artifact['id'])
    for arguments in ({'project_id': pid}, {'path': str(path)}, {'artifact_id': artifact['id']},
                      {'source_kind': 'DISCOVERY'}, {'snapshot_ref': value.json()['file_observation']['snapshot_ref']}):
        with pytest.raises(Conflict):
            reader(arguments, exact)
    other = client.post('/api/projects', json={'name': '不可读的另一个工程'}).json()['id']
    for request in (binding('bound-question', other), binding('bound-question', pid, '不是原问题')):
        with pytest.raises(Conflict):
            reader({}, request)
    generation = messages.generation
    messages.generation = 'different-host-generation'
    try:
        with pytest.raises(Conflict):
            reader({}, exact)
    finally:
        messages.generation = generation
    identity = messages.ws.owner_identity
    messages.ws.owner_identity = {**identity, 'process_creation': identity['process_creation'] + 1}
    try:
        with pytest.raises(Conflict):
            reader({}, exact)
    finally:
        messages.ws.owner_identity = identity
    check(client.get(artifact['download_url']).content == immutable)


def test_discovery_report_original_statement_and_each_section_survive_restart(scene):
    client, pid, folder, base, source, settings = scene
    created = client.post(f'/api/projects/{pid}/discovery/sessions', json={
        'operation_id': 'original-discovery', 'title': '原始需求发现', 'entry_mode': 'PROBLEM',
        'statement': '原始任务：阅读当前页时能够直接提出问题。'})
    check(created.status_code == 201, created.text)
    old = created.json()
    reports = f'/api/projects/{pid}/discovery/sessions/{old["id"]}/reports'
    generated = client.post(reports, json={'operation_id': 'discovery-report', 'expected_revision': 1})
    check(generated.status_code in {200, 201}, generated.text)
    artifact = generated.json()['artifact']
    opened = client.post('/api/desktop-host/native-open/report', json={
        'project_id': pid, 'artifact_id': artifact['id'], 'sha256': artifact['sha256'],
        'download_url': artifact['download_url']})
    check(opened.status_code == 200, opened.text)
    link = opened.json()['file_link_ref']
    sent = send(client, pid, link, operation='discovery-question')
    check(sent.status_code == 200, sent.text)
    expected = {section: client.app.state.native_messages.read_linked_requirements(
        {'section': section}, binding('discovery-question', pid)) for section in ('requirements', 'questions', 'sources')}
    check('原始任务' in expected['requirements']['text_content'])
    check(expected['requirements']['discovery_ref'] == generated.json()['session_ref'])
    # A second real controller reads the same immutable report storage. An old
    # native question is history, never authority for a newly started host.
    with app_client(settings) as restarted:
        read = FrozenLinkedRequirements(restarted.app.state.file_continuity, restarted.app.state.planning_reports)
        for section in expected:
            check(read.read(link, section=section) == expected[section])
        restored = restarted.get(MESSAGES + '/discovery-question')
        check(restored.status_code == 200 and restored.json()['record_origin'] == 'HISTORICAL_READONLY')
        with pytest.raises(Conflict):
            restarted.app.state.native_messages.read_linked_requirements({}, binding('discovery-question', pid))


def test_plain_workspace_file_without_report_origin_still_answers_and_requirements_are_unavailable(scene):
    client, pid, folder, base, source, settings = scene
    opened = client.post('/api/desktop-host/native-open/file', json={'project_id': pid, 'file_source_ref': source})
    check(opened.status_code == 200, opened.text)
    link = opened.json()['file_link_ref']
    response = send(client, pid, link, operation='ordinary-file')
    check(response.status_code == 200 and response.json()['status'] == 'NOT_CONFIGURED')
    messages = client.app.state.native_messages
    result = messages.read_linked_requirements({}, binding('ordinary-file', pid))
    check(result['status'] == 'UNAVAILABLE' and result['reason'] == 'ORIGINAL_REQUIREMENTS_NOT_LINKED_TO_REPORT')
    check('任务要求' in messages.read_linked_file({}, binding('ordinary-file', pid))['text_content'])


def test_original_cli_mcp_reads_old_report_requirements_not_later_plan_or_human_save(scene):
    client, pid, folder, base, source, settings = scene
    artifact, immutable, path, link = opened_report(scene)
    origin = client.app.state.file_continuity.get(link)['origin']
    source_url = artifact['download_url'].removesuffix('/download') + '/source/download'
    frozen_source = client.get(source_url)
    check(frozen_source.status_code == 200)
    snapshot = frozen_source.json()
    first_saved = docx('发问时用户保存正文', '请依据生成本报告时的任务要求补充，不要覆盖人工修改')
    path.write_bytes(first_saved)
    wire = RevisionWire(read_only=True, gate_model=True, read_method='read_linked_requirements')
    evidence = {'qualification': 'REAL_APP_ORIGINAL_CLI_ORDINARY_PRIVATE_TOOL_EXTERNAL_MODEL_FIXTURE_ONLY',
                'rpc_calls': None, 'result': None, 'tool_read': None}
    try:
        profile_ref = configure(client, settings, wire)
        response = send(client, pid, link, operation='actual-report-context', provider_profile_ref=profile_ref)
        check(response.status_code == 200 and response.json()['status'] == 'PROCESSING', response.text)
        check(wire.entered.wait(180), 'actual CLI did not enter external model gateway')
        candidate = deepcopy(snapshot['proposal']['payload']['candidate'])
        candidate['summary'] = 'LATER_REVISION_TWO_ONLY：不是原报告的任务要求'
        changed = client.put(f'/api/projects/{pid}/planning/proposals/{origin["source_id"]}', json={
            'operation_id': 'later-plan-two', 'expected_revision': 1, 'candidate': candidate,
            'change_reason': '用户后续改变任务'})
        check(changed.status_code == 200 and changed.json()['revision'] == 2, changed.text)
        later = docx('问答中另存的人工正文', '此后修改也不能被覆盖')
        path.write_bytes(later)
        wire.release.set()
        deadline, result = time.monotonic() + 180, {}
        while time.monotonic() < deadline:
            observed = client.get(MESSAGES + '/actual-report-context')
            check(observed.status_code == 200, observed.text)
            result = observed.json()
            if result['status'] != 'PROCESSING':
                break
            time.sleep(.1)
        check(result.get('status') == 'RETURNED', canonical(result))
        check(len(wire.calls) == 2 and len(wire.results) == 1)
        read = wire.results[0]['result']
        check(read['proposal_ref'] == {key: snapshot['proposal'][key] for key in ('id', 'revision', 'sha256')})
        check(read['source_snapshot_sha256'] == sha(frozen_source.content))
        check(read['report_artifact_ref']['id'] == artifact['id'])
        check('按已有资料拆解' in read['text_content'] and 'LATER_REVISION_TWO_ONLY' not in read['text_content'])
        check(read['scope'] == 'EXACT_REPORT_GENERATION_SNAPSHOT_NOT_LATEST_REQUIREMENTS')
        check(not read['execution_authorized'] and not read['automatic_input_authorized'])
        check(result['native_cohort']['native_session_id'].startswith('ses_') and not result['changes_applied'])
        messages = client.app.state.native_messages
        admission = messages.ordinary.validate_admission(result['native_message_ref'])
        check('read_linked_requirements' in admission['request']['method_ids'])
        check(result['answer_source'] == 'ORIGINAL_NATIVE_TEXT_JSON_VALIDATED')
        replay = send(client, pid, link, operation='actual-report-context', provider_profile_ref=profile_ref)
        check(replay.status_code == 200 and replay.json()['native_cohort'] == result['native_cohort'])
        for _ in range(3):
            check(client.get(MESSAGES + '/actual-report-context').json()['status'] == 'RETURNED')
        check(len(wire.calls) == 2, 'same-op replay or GET must never add a model request')
        check(path.read_bytes() == later and client.get(artifact['download_url']).content == immutable)
        evidence.update(rpc_calls=len(wire.calls), result=result, tool_read=read,
                        original_source_snapshot_sha256=sha(frozen_source.content), original_cli_sha256=CLI_SHA[os.name])
    finally:
        wire.release.set()
        wire.close()
        check(not wire.thread.is_alive(), 'owned external fixture must actually join')
        evidence_dir = os.environ.get('EEAS_EVIDENCE_DIR')
        if evidence_dir:
            target = Path(evidence_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / ('runtime-' + ('optimized' if __debug__ is False else 'normal') + '.json')).write_text(
                canonical(evidence) + '\n', encoding='utf-8')
