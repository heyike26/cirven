"""Actual domain pipeline over the original mocked Win32 ABI counter.

Bytes/versions/Store parse/edit/export are real; injected handles do not qualify
the Windows kernel or installed application behavior.
"""
from contextlib import contextmanager
import copy
import json
import ntpath
import os
from pathlib import Path

import pytest

from packages.adapters.windows_workspace_handles import WindowsWorkspaceHandles, WindowsParentHandle, FILE_ATTRIBUTE_REPARSE_POINT
from packages.domain import local_workspaces as workspace_module
from packages.domain.local_workspaces import LocalWorkspaces, WorkspaceInput, FileEditInput, FileSource, sha
from packages.domain.local_materials import LocalMaterials, LocalMaterialsInput
from packages.domain.planning_exports import PlanningExports
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.store import Store, Conflict, InvalidInput, NotFound
from test_windows_workspace_handles import OriginalHandleCounter


class PlatformProxy:
    """Only this domain module is NT; global os/Path remains the actual Linux host."""
    name = "nt"
    def __getattr__(self, name): return getattr(os, name)


@pytest.fixture
def nt_selected(tmp_path, monkeypatch, request):
    api = OriginalHandleCounter(); handles = WindowsWorkspaceHandles(api)
    monkeypatch.setattr(workspace_module, "os", PlatformProxy())
    monkeypatch.setattr(workspace_module, "_windows_handles", lambda: handles, raising=False)
    store = Store(tmp_path/'controller'/'store.sqlite3'); store.migrate()
    pid = store.create_project('Windows dispatch actual domain counter', '')['id']
    service = LocalWorkspaces(store, tmp_path/'controller')
    root, identity = handles.root_identity('C:/selected')
    binding = {'root': root, 'root_identity': identity}
    yield service, api, handles, pid, binding
    if os.environ.get('EEAS_EVIDENCE_DIR'):
        target=Path(os.environ['EEAS_EVIDENCE_DIR'])/'mock-nt-counter'/request.node.name
        target.mkdir(parents=True,exist_ok=True)
        nodes={key:{**{k:v for k,v in value.items() if k!='raw'},'raw_hex':value['raw'].hex(),
                    'raw_sha256':sha(value['raw'])} for key,value in api.nodes.items()}
        (target/'counter.json').write_text(json.dumps({'qualification':'MOCK_WIN32_ABI_NOT_REAL_WINDOWS',
            'nodes':nodes,'calls':api.calls,'remaining_handles':list(api.handles)},ensure_ascii=False,indent=2)+'\n')


def test_nt_parent_seam_yields_authoritative_opaque_handle_not_posix_fd(nt_selected):
    service, api, _, _, binding = nt_selected
    with service._parent(binding, 'nested/source.txt') as (parent, leaf):
        assert isinstance(parent, WindowsParentHandle) and leaf == 'source.txt'
        assert parent.path == 'C:\\selected\\nested' and parent.native_handle in api.handles
    assert api.handles == {}


def bound(nt_selected, *, scopes=None, key='actual-nt-domain'):
    service, api, handles, pid, _ = nt_selected
    binding = service.bind(pid, WorkspaceInput(idempotency_key=key, root='C:/selected',
        allowed_write_paths=['*'] if scopes is None else scopes))
    return service, api, handles, pid, binding


def controller_lock_lab_mapping(handles, monkeypatch):
    """Linux registry bytes are real; only lock calls map to a fake NT namespace.

    This explicit lab seam checks controller dispatch and the original LockFileEx
    API counter, not Win32 safety of the Linux registry directory.
    """
    original = handles.lock; seen = []
    @contextmanager
    def mapped(path):
        seen.append(path)
        identity = sha(str(path).encode())
        with original('C:\\controller\\locks\\'+identity): yield
    monkeypatch.setattr(handles, 'lock', mapped)
    return seen


def test_nt_root_binding_actual_source_version_tree_and_history_keep_public_contract(nt_selected):
    service, api, _, pid, binding = bound(nt_selected)
    assert binding['root'] == 'C:\\selected' and binding['root_identity'] == {'device': 17, 'inode': 2}
    assert service.get(pid, binding['id']) == binding
    tree = service.tree(pid, binding['id'], limit=1)
    assert tree['entries'][0]['type'] == 'directory' and tree['next_offset'] == 1
    value = service.read(pid, binding['id'], 'nested/source.txt')
    assert value['content'].encode() == api.nodes['c:\\selected\\nested\\source.txt']['raw']
    assert value['source_ref']['sha256'] == sha(value['content'].encode()) and value['current']
    assert service.history(pid, binding['id'], 'nested/source.txt')[0]['revision'] == 1
    assert api.handles == {}


def test_nt_raw_missing_parent_and_drift_never_falls_back_to_posix(nt_selected, monkeypatch):
    service, api, _, _, binding = nt_selected
    monkeypatch.setattr(workspace_module.os, 'open', lambda *_args, **_kw: (_ for _ in ()).throw(AssertionError('No fd fallback')), raising=False)
    assert service._raw(binding, 'missing.txt', missing=True) is None
    with pytest.raises(NotFound): service._raw(binding, 'missing/source.txt', missing=True)
    api.read_drift = True
    with pytest.raises(Conflict, match='SOURCE_DRIFT'): service._raw(binding, 'original.txt')
    assert api.handles == {}


@pytest.mark.parametrize('path', ['NUL.txt', 'nested/name.', 'nested/name ', 'original.txt:stream', '.git/config', '.EEAS-WRITE-staging'])
def test_nt_domain_passes_path_guards_into_original_adapter(nt_selected, path):
    service, _, _, _, binding = nt_selected
    with pytest.raises(InvalidInput): service._raw(binding, path)


def test_nt_search_uses_handle_discovery_skips_junction_and_records_factual_bytes(nt_selected):
    service, api, _, pid, binding = bound(nt_selected)
    api.add('C:\\selected\\junction', directory=True, attributes=FILE_ATTRIBUTE_REPARSE_POINT)
    api.add('C:\\selected\\junction\\secret.txt', raw=b'outside needle')
    api.add('C:\\selected\\.eeas-runtime', directory=True)
    api.add('C:\\selected\\.eeas-runtime\\secret.txt', raw=b'private needle')
    api.add('C:\\selected\\nested\\中文.md', raw='原件\r\n实际 needle\r\n'.encode())
    hits = service.search(pid, binding['id'], 'needle')
    assert len(hits['hits']) == 1 and hits['hits'][0]['path'] == 'nested/中文.md'
    assert hits['hits'][0]['line_number'] == 2
    assert hits['hits'][0]['source_ref']['sha256'] == sha(api.nodes['c:\\selected\\nested\\中文.md']['raw'])
    assert api.handles == {}


def test_nt_actual_domain_cas_edit_backup_diff_guarded_security_and_idempotent_replay(nt_selected):
    service, api, _, pid, binding = bound(nt_selected)
    before = service.read(pid, binding['id'], 'original.txt'); original = copy.deepcopy(api.nodes['c:\\selected\\original.txt'])
    body = FileEditInput(operation_id='edit-original', path='original.txt', expected_revision=1,
        expected_sha256=before['source_ref']['sha256'], content='修改真实字节\r\n')
    applied = service.edit(pid, binding['id'], body)
    assert applied['source_ref']['revision'] == 2 and '修改真实字节' in applied['diff']
    assert api.nodes['c:\\selected\\original.txt']['raw'] == body.content.encode()
    assert api.nodes['c:\\selected\\original.txt']['security'] == original['security']
    assert service.read(pid, binding['id'], 'original.txt', revision=1)['content'].encode() == original['raw']
    renames = len([call for call in api.calls if call[0] == 'rename'])
    assert service.edit(pid, binding['id'], body)['replayed']
    assert len([call for call in api.calls if call[0] == 'rename']) == renames == 0
    assert applied['commit_qualification']['kind'] == 'WINDOWS_EXCLUSIVE_HANDLE_COMPARE_COMMIT'
    assert applied['commit_qualification']['crash_atomic'] is False
    assert service.operation(pid, binding['id'], 'edit-original')['status'] == 'APPLIED'
    assert api.handles == {}


def test_nt_write_grants_and_external_edit_reject_before_atomic_replace(nt_selected):
    service, api, _, pid, binding = bound(nt_selected, scopes=['nested/'])
    source = service.read(pid, binding['id'], 'original.txt')['source_ref']
    body = FileEditInput(operation_id='outside', path='original.txt', expected_revision=1,
        expected_sha256=source['sha256'], content='denied')
    with pytest.raises(InvalidInput, match='grant'): service.edit(pid, binding['id'], body)
    assert not any(call[0] == 'rename' for call in api.calls)
    current = service.read(pid, binding['id'], 'nested/source.txt')['source_ref']
    api.nodes['c:\\selected\\nested\\source.txt']['raw'] = b'changed by external writer'
    stale = FileEditInput(operation_id='stale', path='nested/source.txt', expected_revision=1,
        expected_sha256=current['sha256'], content='cannot overwrite')
    with pytest.raises(Conflict, match='SOURCE_DRIFT'): service.edit(pid, binding['id'], stale)
    assert not any(call[0] == 'rename' for call in api.calls)


def test_nt_replaced_root_and_private_controller_case_alias_are_rejected(nt_selected):
    service, api, _, pid, binding = bound(nt_selected)
    api.nodes['c:\\selected']['inode'] += 1
    with pytest.raises(Conflict, match='ROOT_DRIFT'): service.get(pid, binding['id'])
    guard = LocalWorkspaces.__new__(LocalWorkspaces); guard.data_dir = Path('C:\\Selected\\PRIVATE-Controller')
    with pytest.raises(InvalidInput, match='Controller-owned'):
        guard._private({'root': 'c:\\selected'}, 'private-controller/deep/file.txt')
    guard._private({'root': 'D:\\selected'}, 'private-controller/deep/file.txt')


def test_nt_materials_dispatch_native_lock_and_store_real_bytes_without_reparse(nt_selected, monkeypatch):
    service, api, handles, pid, binding = bound(nt_selected)
    seen = controller_lock_lab_mapping(handles, monkeypatch)
    materials = LocalMaterials(service)
    body = LocalMaterialsInput(operation_id='material-real-nt-source', paths=['original.txt'])
    saved = materials.materialize(pid, binding['id'], body)
    assert saved['status'] == 'SAVED' and len(seen) == 2
    document_ref = saved['materials'][0]['document_ref']
    doc = service.store.document(pid, document_ref['document_id'], document_ref['revision'])
    assert doc['raw_bytes'] == api.nodes['c:\\selected\\original.txt']['raw']
    assert document_ref['sha256'] == sha(doc['raw_bytes'])
    assert materials.materialize(pid, binding['id'], body)['replayed']
    assert api.handles == {} and any(call[0] == 'lock' for call in api.calls) and any(call[0] == 'unlock' for call in api.calls)


def test_nt_export_directories_use_parent_relative_creation_and_reject_junction(nt_selected):
    service, api, _, _, binding = nt_selected
    proposals = PlanningProposals(service.store, Path(__file__).resolve().parents[1], workspaces=service)
    exports = PlanningExports(proposals, service)
    exports._directories(binding, 'eeas-harness/proposal/v1-sha')
    assert 'c:\\selected\\eeas-harness\\proposal\\v1-sha' in api.nodes
    assert not any(call[0] == 'mkdir' for call in api.calls)
    api.add('C:\\selected\\blocked', directory=True, attributes=FILE_ATTRIBUTE_REPARSE_POINT)
    with pytest.raises(InvalidInput, match='Reparse'):
        exports._directories(binding, 'blocked/never-created')
    assert 'c:\\selected\\blocked\\never-created' not in api.nodes and api.handles == {}


@pytest.mark.parametrize('discovery_context',[False,True])
def test_nt_api_actual_local_materials_confirmed_plan_exports_factual_prompt_bytes(client, nt_selected, monkeypatch, discovery_context):
    from test_planning_proposals import candidate, confirm_body
    _service, api, handles, _pid, _ = nt_selected
    controller_lock_lab_mapping(handles, monkeypatch)
    pid = client.post('/api/projects', json={'name': '原生Windows薄接真实业务链'}).json()['id']
    response = client.post(f'/api/projects/{pid}/local-workspaces', json={'idempotency_key': 'nt-folder',
        'root': 'C:/selected', 'allowed_write_paths': ['eeas-harness/']})
    assert response.status_code == 201, response.text
    binding = response.json(); base = f'/api/projects/{pid}/local-workspaces/{binding["id"]}'
    source = client.post(base+'/materials', json={'operation_id': 'nt-material', 'paths': ['original.txt']})
    assert source.status_code == 201, source.text
    materials = [value['document_ref'] for value in source.json()['materials']]
    body = {'operation_id': 'nt-manual-plan', 'prompt': '按真实字节形成待确认计划，不执行。', 'mode': 'manual',
        'workspace_ref': {k: binding[k] for k in ['id','revision','binding_sha256']}, 'materials': materials, 'candidate': candidate()}
    if discovery_context:
        from test_discovery_workbench import candidate as discovery_candidate, confirm as confirm_discovery
        discovery_base = f'/api/projects/{pid}/discovery/sessions'
        created_discovery = client.post(discovery_base,json={'operation_id':'nt-requirements',
            'title':'真实本地来源的共创上下文','entry_mode':'PROBLEM','statement':'核对来源再明确任务，减少返工。',
            'materials':materials,'workspace_ref':body['workspace_ref']})
        assert created_discovery.status_code==201,created_discovery.text
        discovery = created_discovery.json(); current_base=discovery_base+'/'+discovery['id']
        edited = client.put(current_base,json={'operation_id':'nt-requirements-edit','expected_revision':1,
            'candidate':discovery_candidate(),'change_reason':'用户提出要求与未知竞品观察，仍待明确接受。'})
        assert edited.status_code==200,edited.text
        discovery=edited.json()
        answered=client.post(current_base+'/answers',json={'operation_id':'nt-scope-answer',
            'expected_revision':discovery['revision'],'question_id':'scope','text':'首批只核对所选 original.txt。'})
        assert answered.status_code==200,answered.text
        confirmed=confirm_discovery(client,pid,discovery,answered.json()['id'])
        assert confirmed.status_code==200,confirmed.text
        discovery=confirmed.json()
        body['discovery_ref']={k:discovery[k] for k in ['id','revision','sha256']}
    created = client.post(f'/api/projects/{pid}/planning/proposals', json=body)
    assert created.status_code == 201, created.text
    proposal = created.json(); planbase = f'/api/projects/{pid}/planning/proposals/{proposal["id"]}'
    confirm = confirm_body(proposal,[{'question_id':'interface','mode':'ASSUMPTION_ACCEPTED',
        'assumption':'UART 为用户显式接受的计划假设，未验证真实设备。','reason':'用户明确接受当前计划假设'}])
    accepted = client.post(planbase+'/confirm', json=confirm)
    assert accepted.status_code == 200, accepted.text
    value = accepted.json()
    exportbody = {'operation_id': 'nt-export', 'expected_revision': value['revision'], 'proposal_sha256': value['sha256'],
        'workspace_ref': body['workspace_ref'], 'directory': 'eeas-harness'}
    exported = client.post(planbase+'/harness/export', json=exportbody)
    assert exported.status_code == 200, exported.text
    result = exported.json(); assert result['status'] == 'EXPORTED_CONFIRMED_PLAN' and result['execution_started'] is False
    for item in result['files']:
        raw = api.nodes[ntpath.join('C:\\selected',item['path'].replace('/','\\')).casefold()]['raw']
        assert sha(raw) == item['sha256'] and len(raw) == item['byte_length']
    manifest_path = ntpath.join('C:\\selected',result['version_directory'].replace('/','\\'),'manifest.json').casefold()
    assert manifest_path in api.nodes and api.nodes['c:\\selected\\original.txt']['raw'] == b'original actual byte counter'
    directory=ntpath.join('C:\\selected',result['version_directory'].replace('/','\\'))
    network=json.loads(api.nodes[ntpath.join(directory,'task-network.json').casefold()]['raw'])
    assert 'prompts' not in network and 'context_files' not in network
    context_key=ntpath.join(directory,'discovery-context.json').casefold()
    if discovery_context:
        context=json.loads(api.nodes[context_key]['raw'])
        assert context['discovery_ref']==body['discovery_ref']
        assert context['payload']['source_receipt']['sha256']==discovery['payload']['source_receipt']['sha256']
        assert context['payload']['candidate']['competitors'][0]['observations'][0]['status']=='UNKNOWN'
        assert any(answer['text']=='首批只核对所选 original.txt。' for answer in context['answers'])
        assert 'discovery-context.md' in api.nodes[ntpath.join(directory,'task-design.md').casefold()]['raw'].decode()
        manifest=json.loads(api.nodes[manifest_path]['raw'])
        assert {'discovery-context.md','discovery-context.json'} <= {item['filename'] for item in manifest['files']}
    else: assert context_key not in api.nodes
    before = len([call for call in api.calls if call[0] == 'rename'])
    replay = client.post(planbase+'/harness/export', json=exportbody).json()
    assert replay['replayed'] and all(item['current'] for item in replay['file_checks'])
    assert before == len([call for call in api.calls if call[0] == 'rename'])
    with client.app.state.store.connect() as connection:
        assert connection.execute('SELECT count(*) FROM task_runs').fetchone()[0] == 0
    assert api.handles == {}
