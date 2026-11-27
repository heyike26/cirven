"""AUTHORED_NOT_RUN: test-owned API/Store/saved records; no model/native GUI.

Original-input GET must read the immutable request and its saved metadata basis,
without checking or touching the current workspace, SDK, model or repair engine.
"""
import hashlib
import json
import pytest
from packages.domain.local_workspaces import canonical,digest
from packages.domain.native_rework import NativeApplyInput
from test_native_rework import scene,feedback,mapping,prepare,apply_body,call


def setup(s):
    f=feedback(s)
    m=mapping(s,f)
    return f,m


def original(s,key,codes=(200,)):
    return call(s,'GET','/native-rework/operations/'+key+'/input',codes=codes)


def verify(v,pid,key,kind):
    assert v['kind']=='NATIVE_REWORK_ORIGINAL_INPUT' and v['version']==1
    assert (v['project_id'],v['operation_id'],v['operation_kind'])==(pid,key,kind)
    assert v['read_only'] and not v['write_authorized'] and not v['execution_started']
    assert v['model_calls']==v['source_files_written']==0
    raw=v['input_json'];body=json.loads(raw)
    assert hashlib.sha256(raw.encode()).hexdigest()==v['input_sha256']
    assert v['request_sha256']==digest({'project_id':pid,'operation_id':key,'kind':kind,'input':body})
    assert body['operation_id']==key
    return body


def test_original_mapping_input_preserves_actual_normalized_choices(scene):
    s=scene;f,m=setup(s);v=original(s,'map');body=verify(v,s['pid'],'map','MAPPING')
    assert body['native_feedback_ref']==f['feedback_ref'] and body['task_ids']==['a']
    assert v['input_scope']=={'basis_ref':body['proposal_ref'],'feedback_ref':f['feedback_ref']}
    assert v['saved_metadata_records_read']==1 and v['protocol']=='MAPPING'


def test_original_plan_input_keeps_exact_large_integer_in_saved_canonical_json(scene):
    s=scene;f,m=setup(s);prepare(s,m,value=9007199254740993,independent=False)
    v=original(s,'prepare');body=verify(v,s['pid'],'prepare','PLAN')
    assert '9007199254740993' in v['input_json'] and body['proposed_value']==9007199254740993
    assert v['input_scope']=={'basis_ref':m['record_ref'],'feedback_ref':f['feedback_ref']}
    assert v['protocol']=='ORDINARY_PREVIEW' and v['saved_metadata_records_read']==2


def test_original_apply_intent_without_a_result_is_readable_without_resuming_it(scene):
    s=scene;f,m=setup(s);p=prepare(s,m,independent=False);service=s['client'].app.state.native_rework
    before=(s['root']/'任务与harness.json').read_bytes()
    request=NativeApplyInput(**apply_body(s,p,'pending_apply'))
    service._run(s['pid'],request,'APPLY',lambda fresh:{'status':'INCOMPLETE_REVIEW_REQUIRED'})
    v=original(s,'pending_apply');body=verify(v,s['pid'],'pending_apply','APPLY')
    assert body['plan_ref']==p['plan_ref'] and body['expected_current_sources']
    assert v['input_scope']=={'basis_ref':p['plan_ref'],'feedback_ref':f['feedback_ref']}
    assert (s['root']/'任务与harness.json').read_bytes()==before


def test_original_input_does_not_call_get_current_basis_or_the_repair_engine(scene,monkeypatch):
    s=scene;f,m=setup(s);prepare(s,m,independent=False);service=s['client'].app.state.native_rework
    def forbidden(*args,**kwargs):raise AssertionError('Original input touched current execution state')
    for name in ['get','_proposal','_requirements']:
        monkeypatch.setattr(service,name,forbidden)
    monkeypatch.setattr(service.rework,'operation',forbidden)
    verify(original(s,'prepare'),s['pid'],'prepare','PLAN')


def test_original_input_survives_unavailable_current_workspace_bytes(scene,monkeypatch):
    s=scene;f,m=setup(s);prepare(s,m,independent=False);service=s['client'].app.state.native_rework
    def forbidden(*args,**kwargs):raise AssertionError('Original input read a workspace source')
    monkeypatch.setattr(service.ws,'read',forbidden)
    monkeypatch.setattr(service.ws,'read_saved_snapshot',forbidden)
    monkeypatch.setattr(service.ws,'file_location',forbidden)
    verify(original(s,'prepare'),s['pid'],'prepare','PLAN')


def test_original_input_refuses_inner_request_hash_tampering(scene):
    s=scene;setup(s);service=s['client'].app.state.native_rework
    path=service._path(s['pid'],'operations',digest('map'),'request.json');wrapped=json.loads(path.read_text())
    wrapped['payload']['input']['task_ids']=['b'];wrapped['sha256']=digest(wrapped['payload']);path.write_text(canonical(wrapped))
    original(s,'map',codes=(409,))


def test_original_input_refuses_a_changed_saved_basis_even_if_outer_request_hash_is_recomputed(scene):
    s=scene;f,m=setup(s);prepare(s,m,independent=False);service=s['client'].app.state.native_rework
    path=service._path(s['pid'],'operations',digest('prepare'),'request.json');wrapped=json.loads(path.read_text());payload=wrapped['payload'];payload['input']['mapping_ref']['sha256']='0'*64
    payload['request_sha256']=digest({k:payload[k] for k in ['project_id','operation_id','kind','input']});wrapped['sha256']=digest(payload);path.write_text(canonical(wrapped))
    original(s,'prepare',codes=(409,))


def test_missing_original_input_is_unknown_not_a_new_operation(scene):
    s=scene;setup(s);service=s['client'].app.state.native_rework;path=service._path(s['pid'],'operations',digest('never_saved'),'request.json')
    assert not path.exists();original(s,'never_saved',codes=(404,));assert not path.exists()


def test_original_input_is_bound_to_its_project_and_does_not_cross_to_another_project(scene):
    s=scene;setup(s);other=s['client'].post('/api/projects',json={'name':'Separate original input owner'}).json()['id']
    result=s['client'].get('/api/projects/'+other+'/native-rework/operations/map/input')
    assert result.status_code==404


def test_original_conflict_preview_reads_saved_parent_metadata_without_resolving_again(scene):
    from packages.domain.native_rework_conflicts import NativeConflictResolve
    from test_native_rework import source
    s=scene;f,m=setup(s);p=prepare(s,m,independent=False);service=s['client'].app.state.native_rework
    body=NativeConflictResolve(operation_id='saved_conflict',plan_ref=p['plan_ref'],current_source=source(s),
        current_locator=None,review_sha256='0'*64,resolved_value_text='"saved manual text"',
        rationale='Original user decision',current_object_confirmed=True)
    service._run(s['pid'],body,'PLAN',lambda fresh:{'status':'INCOMPLETE_REVIEW_REQUIRED'})
    v=original(s,'saved_conflict');raw=verify(v,s['pid'],'saved_conflict','PLAN')
    assert v['protocol']=='CONFLICT_PREVIEW' and raw['resolved_value_text']=='"saved manual text"'
    assert v['input_scope']=={'basis_ref':m['record_ref'],'feedback_ref':f['feedback_ref']}
    assert v['saved_metadata_records_read']==2
