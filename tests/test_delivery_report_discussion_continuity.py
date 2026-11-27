"""Real delivery/API/SQLite/DOCX continuity, with explicit user-authored plans.

No model, fake Store, fake file/execution service or Windows GUI is used. The
only OS adapter is the existing actual POSIX host PID/birth/session reader.
Source provenance, saved edits and immutable report requirements stay distinct.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil

from docx import Document
import pytest

from apps.api.config import Settings
from packages.domain.linked_requirements import FrozenLinkedRequirements
from packages.domain.local_workspaces import canonical, sha
from packages.domain.store import Conflict, InvalidInput
from test_native_file_continuity_api import app_client, HOST_TOKEN, MESSAGES


STATEMENT = 'ORIGINAL_REQUIREMENT_V1：保留原材料和人工修改；批注不自动变成已确认要求。'


def check(value, message='delivery/report discussion continuity failed'):
    if not value:
        raise AssertionError(message)


def ref(value):
    return {key: value[key] for key in ('id', 'revision', 'sha256')}


class Evidence:
    def __init__(self, client, directory):
        self.client, self.directory, self.calls, self.facts = client, Path(directory), [], {}
        self.directory.mkdir(parents=True, exist_ok=True)

    def call(self, method, url, body=None, *, status=200, params=None):
        response = self.client.request(method, url, json=body, params=params)
        value = response.json()
        self.calls.append({'method':method, 'url':url, 'request':body, 'params':params,
            'http_status':response.status_code, 'result':value})
        self.save()
        check(response.status_code == status, f'{url}: {response.status_code}: {response.text}')
        return value

    def save(self):
        (self.directory / 'receipt.json').write_text(canonical({
            'qualification':'REAL_API_SQLITE_DOCX_NO_MODEL_NO_NATIVE_GUI', 'model_calls':0,
            'windows':'NOT_RUN', 'native_word':'NOT_RUN', 'visual_rendering':'NOT_RUN',
            'calls':self.calls, 'facts':self.facts}) + '\n')


def candidate():
    return {'title':'原始来源核对', 'phase':'SPECIFY', 'summary':STATEMENT,
        'claims':[{'id':'retain', 'category':'REQUIREMENT', 'origin':'USER_STATEMENT',
            'text':STATEMENT, 'acceptance':['原文件、报告生成依据和后续保存稿分别可核对。']}],
        'questions':[{'id':'language', 'text':'是否需要双语？', 'reason':'交付形式待用户回答。',
            'category':'DECISION', 'blocking':False, 'affects_claim_ids':['retain']}], 'graphs':[]}


def prepare(client, settings, folder, evidence_dir, kind='STAGE'):
    evidence = Evidence(client, evidence_dir)
    folder.mkdir(parents=True)
    original = (STATEMENT + '\n原材料仍然保持这些字节。\n').encode()
    (folder / '要求.txt').write_bytes(original)
    pid = evidence.call('POST', '/api/projects', {'name':'交付后继续原讨论'}, status=201)['id']
    base = f'/api/projects/{pid}'
    binding = evidence.call('POST', base + '/local-workspaces', {'idempotency_key':'selected-folder',
        'root':str(folder), 'allowed_write_paths':['delivery/']}, status=201)
    workspace = {key:binding[key] for key in ('id', 'revision', 'binding_sha256')}
    local = base + '/local-workspaces/' + binding['id']
    material = evidence.call('POST', local + '/materials', {'operation_id':'original-material',
        'paths':['要求.txt']}, status=201)['materials'][0]['document_ref']
    session = evidence.call('POST', base + '/discovery/sessions', {'operation_id':'original-discovery',
        'title':'原始讨论', 'entry_mode':'REQUIREMENTS', 'statement':STATEMENT,
        'materials':[material], 'workspace_ref':workspace}, status=201)
    session = evidence.call('PUT', base + '/discovery/sessions/' + session['id'], {
        'operation_id':'user-authored-requirements', 'expected_revision':session['revision'],
        'candidate':candidate(), 'change_reason':'用户明确输入候选要求，不替代模型或自动确认。'})
    operation = 'generated-delivery-' + kind.lower()
    if kind == 'STAGE':
        delivered = evidence.call('POST', base + f'/planning-delivery/sessions/{session["id"]}/stage', {
            'operation_id':operation, 'session_ref':ref(session), 'workspace_ref':workspace, 'directory':'delivery'})
        check(delivered['status'] == 'STAGE_REPORT')
    else:
        session = evidence.call('POST', base + f'/discovery/sessions/{session["id"]}/confirm', {
            'operation_id':'explicit-user-confirm-requirements', 'expected_revision':session['revision'],
            'session_sha256':session['sha256'], 'user_confirmed':True,
            'accepted_claim_ids':['retain'], 'accepted_graph_ids':[]})
        check(session['readiness']['can_plan'])
        proposal = evidence.call('POST', base + '/planning/proposals', {'operation_id':'user-authored-plan',
            'mode':'manual', 'prompt':'用户明确编写原任务计划，保持冻结来源。', 'materials':[material],
            'workspace_ref':workspace, 'discovery_ref':ref(session), 'candidate':{
                'title':'原始详细任务计划', 'summary':STATEMENT, 'nodes':[{
                    'id':'review', 'title':'按原要求核对来源', 'description':STATEMENT,
                    'inputs':['原要求'], 'outputs':['来源清单'], 'acceptance':['保留原件和人工改动']}]}}, status=201)
        proposal = evidence.call('POST', base + f'/planning/proposals/{proposal["id"]}/confirm', {
            'operation_id':'explicit-user-confirm-plan', 'expected_revision':proposal['revision'],
            'proposal_sha256':proposal['sha256'], 'user_confirmed':True})
        delivered = evidence.call('POST', base + f'/planning-delivery/proposals/{proposal["id"]}/export', {
            'operation_id':operation, 'proposal_ref':ref(proposal), 'workspace_ref':workspace, 'directory':'delivery'})
        check(delivered['status'] == 'EXPORTED_CONFIRMED_PLAN_DERIVATIVES')
    artifact = delivered['report']['artifact']
    reports = [item for item in delivered['project_files'] if item.get('sha256') == artifact['sha256']
        and item['path'].endswith('.docx')]
    check(len(reports) == 1)
    generated = reports[0]['file_source_ref']
    path = folder / generated['path']
    raw = client.get(artifact['download_url']).content
    source_url = delivered['report']['source_snapshot']['download_url']
    source_raw = client.get(source_url).content
    check(raw == path.read_bytes() and sha(raw) == artifact['sha256'])
    (evidence.directory / 'immutable-report.docx').write_bytes(raw)
    (evidence.directory / 'immutable-source-snapshot.json').write_bytes(source_raw)
    result = dict(client=client, evidence=evidence, pid=pid, base=base, folder=folder, local=local,
        settings=settings, workspace=workspace, session=session, delivered=delivered, artifact=artifact,
        operation=operation, generated=generated, path=path, original=raw, original_material=original,
        source_url=source_url, source_raw=source_raw, kind=kind)
    evidence.facts.update(generated_source=generated, delivery_operation_id=operation,
        immutable_sha256=sha(raw), source_snapshot_sha256=sha(source_raw))
    evidence.save()
    return result


@pytest.fixture
def scene(tmp_path, monkeypatch, request):
    monkeypatch.setenv('EEAS_DESKTOP_HOST_TOKEN', HOST_TOKEN)
    monkeypatch.setenv('EEAS_DESKTOP_HOST_PID', str(os.getpid()))
    settings = Settings(tmp_path / 'controller')
    directory = Path(os.environ.get('EEAS_DELIVERY_DISCUSSION_EVIDENCE_DIR', tmp_path / 'evidence')) / request.node.name
    with app_client(settings) as client:
        scene = prepare(client, settings, tmp_path / 'selected-project', directory,
            kind=getattr(request, 'param', 'STAGE'))
        try:
            yield scene
        finally:
            shutil.copytree(scene['folder'], directory / 'selected-folder-final', dirs_exist_ok=True)
            scene['evidence'].save()


def current_source(scene):
    return scene['evidence'].call('GET', scene['local'] + '/file',
        params={'path':scene['generated']['path']})['source_ref']


def open_file(scene, source=None, **extra):
    return scene['evidence'].call('POST', '/api/desktop-host/native-open/file', {
        'project_id':scene['pid'], 'file_source_ref':source or scene['generated'],
        'delivery_operation_id':scene['operation'], **extra})


def human_save(path, marker):
    document = Document(path)
    run = document.add_paragraph().add_run('HUMAN_BODY_' + marker + '：只属于后续人工保存稿。')
    document.add_comment(run, text='HUMAN_COMMENT_' + marker + '：按这里继续讨论，不自动确认要求。',
        author='真实保存夹具', initials='HS')
    temporary = path.with_name(path.stem + '-atomic-save.docx')
    document.save(temporary)
    os.replace(temporary, path)
    return path.read_bytes()


def read_all(read_page):
    """Exercise real bounded codepoint pagination, including longer exports."""
    parts, offset = [], 0
    for _ in range(32):
        page = read_page(offset, 8000)
        check(page['offset'] == offset)
        parts.append(page['text_content'])
        following = offset + len(page['text_content']) if page['has_more'] else None
        if following is None:
            check(sum(map(len, parts)) == page['total_characters'])
            return ''.join(parts)
        check(type(following) is int and following > offset)
        offset = following
    raise AssertionError('bounded document pagination did not terminate')


def reader(scene):
    app = scene['client'].app
    return FrozenLinkedRequirements(app.state.file_continuity, app.state.planning_reports)


def ask(scene, link, operation='discussion-1'):
    return scene['evidence'].call('POST', MESSAGES, {'operation_id':operation,
        'raw_text':'按刚保存的正文和批注继续讨论；请保留原要求来源。', 'project_id':scene['pid'],
        'file_link_ref':link, 'conversation_id':'same-original-discussion'})


def unchanged_sources(scene):
    check(scene['client'].get(scene['artifact']['download_url']).content == scene['original'])
    check(scene['client'].get(scene['source_url']).content == scene['source_raw'])
    check((scene['folder'] / '要求.txt').read_bytes() == scene['original_material'])
    with scene['client'].app.state.store.connect() as database:
        check(database.execute('SELECT count(*) FROM agent_executions').fetchone()[0] == 0)
    check(scene['client'].app.state.native_messages.transport.active == {})


@pytest.mark.parametrize('scene', ['STAGE', 'PROPOSAL'], indirect=True)
@pytest.mark.parametrize('saved_before_open', [False, True])
def test_delivery_open_saved_edits_and_question_freeze_separate_current_body_from_generation_basis(scene, saved_before_open):
    if not saved_before_open:
        opened = open_file(scene)
    first = human_save(scene['path'], 'FIRST')
    current = current_source(scene)
    if saved_before_open:
        opened = open_file(scene, current)
    link = opened['file_link_ref']
    check(opened['file_source_ref'] == (current if saved_before_open else scene['generated']))
    check(opened['native_path'] == str(scene['path']) and not opened['active_document_identity_verified'])
    registered = scene['client'].app.state.file_continuity.get(link)
    check(registered['origin']['kind'] == 'GENERATED_DELIVERY_REPORT')
    check(registered['origin']['generation_file_source_ref'] == scene['generated'])
    check(registered['baseline_sha256'] == sha(first if saved_before_open else scene['original']))
    old_requirements = reader(scene).read(link, limit=8000)
    check(old_requirements['status'] == 'AVAILABLE' and STATEMENT in old_requirements['text_content'])
    check('HUMAN_BODY_' not in old_requirements['text_content'] and 'HUMAN_COMMENT_' not in old_requirements['text_content'])
    check(old_requirements['source_snapshot_sha256'] == sha(scene['source_raw']))
    check(old_requirements['generated_file_source_ref'] == scene['generated'])
    check(old_requirements['opened_file_baseline_ref'] == opened['file_source_ref'])
    question = ask(scene, link)
    check(question['status'] == 'NOT_CONFIGURED' and question['execution_effects'] == 'READONLY')
    observed = question['file_observation']
    check(observed['current_sha256'] == sha(first) and observed['unsaved_changes'] == 'NOT_OBSERVED')
    check(any('HUMAN_COMMENT_FIRST' in comment['text'] for comment in observed['comments']))
    service = scene['client'].app.state.native_messages
    binding = {'request':{'operation_id':question['operation_id'], 'project_id':scene['pid'],
        'prompt':'按刚保存的正文和批注继续讨论；请保留原要求来源。'}}
    check(service.read_linked_requirements({'limit':8000}, binding) == old_requirements)
    read_frozen = lambda offset, limit: service.read_linked_file({'offset':offset, 'limit':limit}, binding)
    frozen = read_all(read_frozen)
    check('HUMAN_BODY_FIRST' in frozen)
    second = human_save(scene['path'], 'SECOND')
    check(sha(second) != sha(first))
    check(ask(scene, link)['file_observation'] == observed)
    check(read_all(read_frozen) == frozen and 'HUMAN_BODY_SECOND' not in frozen)
    check(reader(scene).read(link, limit=8000) == old_requirements)
    latest = scene['evidence'].call('POST', '/api/desktop-host/file-contexts/observe', {'link_ref':link})
    check(latest['current_sha256'] == sha(second) and latest['snapshot_ref'] != observed['snapshot_ref'])
    reopened = scene['evidence'].call('POST', '/api/desktop-host/native-open/link', {'link_ref':link})
    check(reopened['file_link_ref'] == link and reopened['native_path'] == str(scene['path']))
    check(reopened['current_sha256'] == sha(second) and reopened['working_copy_modified'])
    check(reopened['file_source_ref']['sha256'] == sha(second) and not reopened['active_document_identity_verified'])
    check(scene['path'].read_bytes() == second)
    unchanged_sources(scene)
    scene['evidence'].facts.update(link_ref=link, frozen_observation=observed, requirements=old_requirements,
        latest_snapshot_ref=latest['snapshot_ref'], saved_before_open=saved_before_open)


def test_legacy_or_forged_origin_never_grants_original_requirements_and_wrong_op_cannot_register(scene):
    client, audit = scene['client'], scene['evidence']
    plain = audit.call('POST', '/api/desktop-host/native-open/file', {'project_id':scene['pid'],
        'file_source_ref':scene['generated']})
    check(reader(scene).read(plain['file_link_ref'])['status'] == 'UNAVAILABLE')
    before = client.app.state.file_continuity.list_candidates(scene['pid'], limit=30)
    for extra, status in [({'delivery_operation_id':'does-not-exist'},404),
            ({'origin':{'kind':'GENERATED_DELIVERY_REPORT'}},422),
            ({'generation_file_source_ref':scene['generated']},422)]:
        audit.call('POST', '/api/desktop-host/native-open/file', {'project_id':scene['pid'],
            'file_source_ref':scene['generated'], **extra}, status=status)
    with pytest.raises(InvalidInput):
        client.app.state.file_continuity.register_workspace(scene['pid'], scene['generated'],
            {'kind':'GENERATED_DELIVERY_REPORT', 'operation_id':scene['operation']})
    check(client.app.state.file_continuity.list_candidates(scene['pid'], limit=30) == before)
    unchanged_sources(scene)


def test_other_project_or_identical_named_physical_file_cannot_borrow_the_real_delivery(scene, tmp_path):
    client, audit, pid = scene['client'], scene['evidence'], scene['pid']
    other = audit.call('POST', '/api/projects', {'name':'另一工程'}, status=201)['id']
    binding = audit.call('POST', f'/api/projects/{other}/local-workspaces', {'idempotency_key':'same-folder-other-project',
        'root':str(scene['folder'])}, status=201)
    source = audit.call('GET', f'/api/projects/{other}/local-workspaces/{binding["id"]}/file',
        params={'path':scene['generated']['path']})['source_ref']
    audit.call('POST', '/api/desktop-host/native-open/file', {'project_id':other,
        'file_source_ref':source, 'delivery_operation_id':scene['operation']}, status=404)
    separate = tmp_path / 'another-physical-folder'; separate.mkdir()
    twin_path = separate / scene['generated']['path']
    twin_path.parent.mkdir(parents=True)
    twin_path.write_bytes(scene['original'])
    binding = audit.call('POST', scene['base'] + '/local-workspaces', {'idempotency_key':'other-physical-folder',
        'root':str(separate)}, status=201)
    twin = audit.call('GET', scene['base'] + f'/local-workspaces/{binding["id"]}/file',
        params={'path':scene['generated']['path']})['source_ref']
    check(twin['sha256'] == scene['generated']['sha256'] and twin['path'] == scene['generated']['path']
        and twin['workspace_id'] != scene['generated']['workspace_id'])
    audit.call('POST', '/api/desktop-host/native-open/file', {'project_id':pid,
        'file_source_ref':twin, 'delivery_operation_id':scene['operation']}, status=409)
    check(twin_path.read_bytes() == scene['original'])
    unchanged_sources(scene)


def test_old_file_card_is_rejected_after_save_but_current_exact_file_keeps_original_report_basis(scene):
    saved = human_save(scene['path'], 'BEFORE_REOPEN')
    scene['evidence'].call('POST', '/api/desktop-host/native-open/file', {'project_id':scene['pid'],
        'file_source_ref':scene['generated'], 'delivery_operation_id':scene['operation']}, status=409)
    opened = open_file(scene, current_source(scene))
    check(opened['file_source_ref']['sha256'] == sha(saved))
    check(reader(scene).read(opened['file_link_ref'], limit=8000)['generated_file_source_ref'] == scene['generated'])
    check(scene['path'].read_bytes() == saved)
    unchanged_sources(scene)


@pytest.mark.parametrize('kind', ['STAGE', 'PROPOSAL'])
def test_late_requirements_and_app_restart_keep_both_frozen_bases_without_replaying_authority(tmp_path, monkeypatch, request, kind):
    monkeypatch.setenv('EEAS_DESKTOP_HOST_TOKEN', HOST_TOKEN)
    monkeypatch.setenv('EEAS_DESKTOP_HOST_PID', str(os.getpid()))
    settings = Settings(tmp_path / 'controller')
    directory = Path(os.environ.get('EEAS_DELIVERY_DISCUSSION_EVIDENCE_DIR', tmp_path / 'evidence')) / request.node.name
    with app_client(settings) as client:
        scene = prepare(client, settings, tmp_path / 'selected-project', directory, kind)
        link = open_file(scene)['file_link_ref']
        first = human_save(scene['path'], 'BEFORE_RESTART')
        question = ask(scene, link, 'persisted-discussion')
        old = reader(scene).read(link, limit=8000)
        current = scene['evidence'].call('GET', scene['base'] + '/discovery/sessions/' + scene['session']['id'])
        changed = deepcopy(current['payload']['candidate']); changed['summary'] = 'LATER_REQUIREMENT_V2：不能倒灌报告。'
        scene['evidence'].call('PUT', scene['base'] + '/discovery/sessions/' + current['id'], {
            'operation_id':'late-user-requirements', 'expected_revision':current['revision'],
            'candidate':changed, 'change_reason':'用户之后换了要求，保留旧报告依据。'})
        check(reader(scene).read(link, limit=8000) == old)
        unchanged_sources(scene)
    second = human_save(scene['path'], 'AFTER_RESTART')
    with app_client(settings) as restarted:
        loaded = restarted.get(MESSAGES + '/persisted-discussion')
        check(loaded.status_code == 200, loaded.text)
        check(loaded.json()['record_origin'] == 'HISTORICAL_READONLY')
        check(loaded.json()['file_observation'] == question['file_observation'])
        continuity = restarted.app.state.file_continuity
        restored = FrozenLinkedRequirements(continuity, restarted.app.state.planning_reports)
        check(restored.read(link, limit=8000) == old)
        check('LATER_REQUIREMENT_V2' not in old['text_content'] and 'HUMAN_BODY_' not in old['text_content'])
        frozen = read_all(lambda offset, limit: continuity.read_text(
            question['file_observation']['snapshot_ref'], offset=offset, limit=limit))
        check('HUMAN_BODY_BEFORE_RESTART' in frozen and 'HUMAN_BODY_AFTER_RESTART' not in frozen)
        check(question['file_observation']['current_sha256'] == sha(first))
        check(continuity.observe(link)['current_sha256'] == sha(second))
        binding = {'request':{'operation_id':'persisted-discussion', 'project_id':scene['pid'],
            'prompt':'按刚保存的正文和批注继续讨论；请保留原要求来源。'}}
        with pytest.raises(Conflict):
            restarted.app.state.native_messages.read_linked_requirements({}, binding)
        check(restarted.app.state.native_messages.transport.active == {})
        check(scene['path'].read_bytes() == second)
        check(restarted.get(scene['artifact']['download_url']).content == scene['original'])
        check(restarted.get(scene['source_url']).content == scene['source_raw'])
        with restarted.app.state.store.connect() as database:
            check(database.execute('SELECT count(*) FROM agent_executions').fetchone()[0] == 0)
        scene['evidence'].facts.update(link_ref=link, old_requirements=old, restored_question=loaded.json(),
            current_user_sha256=sha(second))
    shutil.copytree(scene['folder'], directory / 'selected-folder-final', dirs_exist_ok=True)
    scene['evidence'].save()
