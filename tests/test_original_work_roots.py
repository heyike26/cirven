"""AUTHORED_NOT_RUN: real original receipts, bounded readonly metadata catalog.

Original ACK/native/model and inherited observer fixtures are UNIT boundaries.
This is not native execution, whole-task or UI qualification.
"""
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from packages.agent_sessions.work_roots import inspect_work_roots
from packages.agent_sessions import work_roots
from packages.domain.store import Conflict
from test_original_work_requirements import work
from test_command_observations_routes import make_routed
from test_opencode_command_observation import command_scene
from test_session_diffs import app_for
from test_original_work_test_cases import mapped
from test_original_work_retests import retest,submit_packet,native_boundary,COMMAND
from packages.agent_sessions.work_retests import submit_work_retest
import asyncio


@pytest.fixture
def routed(command_scene):return make_routed(command_scene,commands=[COMMAND,'python other.py'])


@pytest.fixture
def roots(work):
    s=work;s['service']._finish(s['pid'],s['ident'],s['input']['root_operation_id'],
        'UNKNOWN_OUTCOME',native_dispatch_acknowledged=True)
    s['root_query']={key:value for key,value in s['input'].items() if key!='root_operation_id'}|{'offset':0}
    return s


def inspect(s,**changes):return inspect_work_roots(s['service'],s['pid'],s['ident'],{**s['root_query'],**changes})


def test_commandless_original_work_is_discovered_without_command_or_source_reads(roots,monkeypatch):
    s=roots
    def forbidden(*args,**kwargs):raise AssertionError('Metadata catalog must not execute/read commands or workspace files')
    monkeypatch.setattr(s['service'],'command_observations',forbidden)
    monkeypatch.setattr(s['service'].workspaces,'read_saved_snapshot',forbidden)
    v=inspect(s)
    assert [r['operation_id'] for r in v['entries']]==[s['input']['root_operation_id']]
    assert v['model_calls']==v['commands_started']==v['source_files_written']==0
    assert v['whole_task_acceptance']=='NOT_ASSESSED' and v['coverage']['complete']


@pytest.mark.parametrize('ack',[False,None,1,'true'])
def test_unacknowledged_or_truthy_metadata_never_becomes_an_acknowledged_work_entry(roots,ack):
    s=roots;s['service']._finish(s['pid'],s['ident'],s['input']['root_operation_id'],
        'UNKNOWN_OUTCOME',native_dispatch_acknowledged=ack)
    v=inspect(s)
    assert v['entries']==[] and v['coverage']['unacknowledged_messages']==1
    assert s['service'].operation(s['pid'],s['ident'],s['input']['root_operation_id'])['native_dispatch_acknowledged']==ack


def test_same_titles_are_distinct_original_operations(roots):
    s=roots;service=s['service'];old=service.operation(s['pid'],s['ident'],s['input']['root_operation_id'])
    request={**old['request'],'operation_id':'other_root'}
    service._claim(s['pid'],s['ident'],'other_root','MESSAGE',request)
    service._finish(s['pid'],s['ident'],'other_root','UNKNOWN_OUTCOME',native_dispatch_acknowledged=True,
        generation=1,original_session_id='ses_command',native_message_id='msg_other')
    v=inspect(s)
    assert len(v['entries'])==2 and v['entries'][0]['label']==v['entries'][1]['label']
    assert len({r['operation_id'] for r in v['entries']})==2


def test_foreign_binding_or_generation_cannot_browse_old_scope(roots):
    s=roots
    for change in [{'generation':2},{'workspace_ref':{**s['workspace_ref'],'binding_sha256':'f'*64}}]:
        with pytest.raises(Conflict):inspect(s,**change)


def test_corrupt_operation_reports_partial_coverage_and_keeps_verified_original(roots):
    s=roots;path=s['service']._path(s['pid'],s['ident'],'operations','f'*64+'.json');path.write_text('{bad json')
    v=inspect(s)
    assert len(v['entries'])==1 and not v['coverage']['complete']
    assert 'ORIGINAL_OPERATION_UNAVAILABLE' in v['coverage']['reason_codes']
    assert path.read_text()=='{bad json'


def test_catalog_does_not_rewrite_or_create_operation_receipts(roots):
    s=roots;directory=s['service']._path(s['pid'],s['ident'],'operations');before={p.name:p.read_bytes() for p in directory.iterdir()}
    inspect(s);inspect(s,offset=8)
    assert {p.name:p.read_bytes() for p in directory.iterdir()}==before


def test_marker_without_verified_retest_ledger_cannot_hide_a_real_original(roots):
    s=roots;op=s['service'].operation(s['pid'],s['ident'],s['input']['root_operation_id'])
    op['work_retest']={'intent_ref':{'operation_id':'missing'}}
    s['service']._write(s['service']._path(s['pid'],s['ident'],'operations',work_roots.digest(op['id'])+'.json'),op,replace=True)
    assert len(inspect(s)['entries'])==1


def test_directory_scan_has_an_explicit_bound_and_declares_incomplete_coverage(roots,monkeypatch):
    class Scan:
        def __enter__(self):return (SimpleNamespace(name='unit-unrelated') for _ in range(1025))
        def __exit__(self,*args):return False
    monkeypatch.setattr(work_roots.os,'scandir',lambda path:Scan())
    v=inspect(roots)
    assert v['coverage']['scanned_entries']==1024 and not v['coverage']['complete']
    assert 'OPERATION_SCAN_CAPACITY_REACHED' in v['coverage']['reason_codes']


def test_guidance_receipts_are_not_invented_as_independent_work_roots(roots):
    s=roots;service=s['service'];old=service.operation(s['pid'],s['ident'],s['input']['root_operation_id'])
    service._claim(s['pid'],s['ident'],'guidance_only','GUIDANCE',{**old['request'],'operation_id':'guidance_only'})
    assert [r['operation_id'] for r in inspect(s)['entries']]==[old['id']]


def test_selected_root_ack_drift_is_visible_as_incomplete_coverage(roots,monkeypatch):
    s=roots;original=work_roots._require_dispatch_ack
    def drift(*args,**kwargs):raise Conflict('UNIT selected root acknowledgement changed')
    monkeypatch.setattr(work_roots,'_require_dispatch_ack',drift)
    v=inspect(s)
    assert v['entries']==[] and 'SELECTED_ROOT_UNAVAILABLE' in v['coverage']['reason_codes']
    monkeypatch.setattr(work_roots,'_require_dispatch_ack',original)


def test_http_catalog_route_uses_existing_original_service_without_dispatch(roots):
    s=roots;client=TestClient(app_for(s['service']))
    response=client.post('/api/projects/'+s['pid']+'/agent-sessions/'+s['ident']+'/work-roots/inspect',
        json=s['root_query'])
    assert response.status_code==200 and response.json()['commands_started']==0


def test_ledger_verified_retest_is_grouped_with_its_exact_original_root(retest,monkeypatch):
    s=retest;service=s['service'];service._finish(s['pid'],s['ident'],s['input']['root_operation_id'],
        'UNKNOWN_OUTCOME',native_dispatch_acknowledged=True)
    native_boundary(s,monkeypatch)
    asyncio.run(submit_work_retest(service,s['pid'],s['ident'],submit_packet(s)))
    query={k:v for k,v in s['input'].items() if k!='root_operation_id'}|{'offset':0}
    v=inspect_work_roots(service,s['pid'],s['ident'],query)
    assert [r['operation_id'] for r in v['entries']]==[s['input']['root_operation_id']]
    assert v['entries'][0]['label']=='Original selected command'


def test_native_object_manifest_keeps_its_existing_object_context_boundary(roots):
    s=roots;service=s['service'];manifest,_=service._manifest(s['pid'],s['ident'])
    manifest['native_message_admission']={'unit_metadata_marker':True}
    service._write(service._path(s['pid'],s['ident'],'manifest.json'),manifest,replace=True)
    _,reference=service._manifest(s['pid'],s['ident'])
    v=inspect(s,session_ref=reference)
    assert v['entries']==[] and 'NATIVE_WORK_USES_EXISTING_OBJECT_CONTEXT_ENTRY' in v['coverage']['reason_codes']


def test_scope_changed_during_catalog_read_cannot_return_an_old_current_projection(roots,monkeypatch):
    s=roots;service=s['service'];original=work_roots._require_dispatch_ack
    def change(*args,**kwargs):
        original(*args,**kwargs);state=service._state(s['pid'],s['ident']);state['generation']=2
        service._write(service._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    monkeypatch.setattr(work_roots,'_require_dispatch_ack',change)
    with pytest.raises(Conflict):inspect(s)
