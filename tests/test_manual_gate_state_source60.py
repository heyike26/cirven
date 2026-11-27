"""Actual manual coverage -> HTTP draft persistence -> original capture/edit.

Fresh original fixtures only. No native App, listener, CLI or model is started.
"""
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.local_workspaces import register_local_workspace_routes
from apps.api.workbench_state import register_workbench_state_routes
from packages.domain.planning_schema import ManualCaptureInput, ProposalEdit, HarnessExportInput
from packages.domain.store import Store
from packages.domain.workbench_state import canonical, sha
from test_harness_manual_continuity import (
    MARKER, scenario, no_external, call, exported, export_body, edit_c,
    change_a, approve, ref, digest,
)


def must(value, message='Source60 manual state regression failed'):
    if not value:
        raise AssertionError(message)


def key(e, target):
    return {'project_id': e['pid'], 'kind': 'proposal', 'object_id': target['id'],
            'revision': target['revision'], 'sha256': target['sha256'],
            'workspace_id': e['workspace']['id'], 'binding_revision': e['workspace']['revision'],
            'binding_sha256': e['workspace']['binding_sha256']}


def state_save(e, target, fields, revision=0, op='persist-real-gate', status=200):
    base = f"/api/projects/{e['pid']}/workbench-state"
    return call(e, 'POST', base+'/save', {'key': key(e, target), 'expected_revision': revision,
        'operation_id': op, 'fields': fields, 'view': {'editing': True, 'selected': ['C']}, 'dirty': True}, status)


def read_restarted(e, target):
    store = Store(e['data']/'eeas.sqlite3'); app = FastAPI()
    register_local_workspace_routes(app, store, e['data'])
    register_workbench_state_routes(app, store, e['data'], workspaces=app.state.local_workspaces)
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{e['pid']}/workbench-state/load", json={'key': key(e, target)})
        must(response.status_code == 200, response.text)
        return response.json()


def test_actual_coverage_survives_http_state_restart_then_capture_manual_review_and_original_save(scenario):
    e = scenario; original = exported(e); item, saved_file, _ = edit_c(e, original)
    target = approve(e, change_a(e, e['approved'], 'only-A-state60'), 'confirm-A-state60')
    response = call(e, 'POST', e['base']+'/harness/export', export_body(e, target, 'gate-state60'), 409)
    gate = {'code': response['code'], **response['detail']['manual_changes']}
    must(gate['coverage']['complete'] and 'read_receipt' in gate['coverage'])
    must(gate['coverage']['raw_bytes_verified'] > 0 and gate['coverage']['checked_paths'])
    fields = {'candidate': deepcopy(target['payload']['candidate']), 'changeReason': '保留原C改文', 'manualGate': gate}
    receipt = state_save(e, target, fields)
    must(receipt['fields'] == fields and read_restarted(e, target)['fields'] == fields)
    must(not (e['data']/'harness-manual').exists(), 'Draft persistence must not capture or approve')
    # Invalid diagnostics cannot erase the saved real gate or convert unknown to clear.
    invalid = []
    def bad_cov(name, value):
        row = deepcopy(fields); row['manualGate']['coverage'][name] = value; invalid.append(row)
    bad_cov('unknown_authority', True); bad_cov('raw_bytes_verified', True)
    bad_cov('checked_paths', ['../escape.md']); bad_cov('checked_paths', ['output/a']*257)
    bad_cov('unchecked_path_count', 1)
    row = deepcopy(fields); row['manualGate']['coverage']['read_receipt']['charged_bytes'] = 8*1024*1024+1; invalid.append(row)
    row = deepcopy(fields); row['manualGate']['coverage']['read_receipt']['metadata']['limit_bytes'] = 32*1024*1024; invalid.append(row)
    row = deepcopy(fields); row['manualGate']['coverage']['read_receipt']['reads'][0]['exact'] = 1; invalid.append(row)
    row = deepcopy(fields); row['manualGate']['coverage']['read_receipt']['scope'] = 'ARBITRARY_FILE_AUTHORITY'; invalid.append(row)
    row = deepcopy(fields); row['manualGate']['coverage']['read_receipt']['metadata']['reads'][0]['extra'] = 'raw contents'; invalid.append(row)
    for index, bad in enumerate(invalid):
        state_save(e, target, bad, 1, f'invalid-real-gate-{index}', 422)
    must(read_restarted(e, target)['fields'] == fields)
    # Legacy five-key snapshots still round-trip on their original exact scope.
    legacy = deepcopy(fields)
    legacy['manualGate']['coverage'] = {k: gate['coverage'][k] for k in
        ('complete', 'total_registered_files', 'checked_files', 'scanned_export_count', 'unverified')}
    state_save(e, target, legacy, 1, 'legacy-gate-compatible')
    state_save(e, target, fields, 2, 'restore-real-gate')
    request = ManualCaptureInput.model_validate({'operation_id': 'capture-state60',
        'target_proposal_ref': ref(target), 'workspace_ref': e['workspace'],
        'files': [{k: f[k] for k in ('export_ref', 'path', 'basis_sha256', 'edited_sha256')} for f in gate['files']],
        'predecessor_export_ref': None}).model_dump()
    fields['manualCapture'] = {'request': request, 'request_sha256': digest({k:v for k,v in request.items() if k!='operation_id'}),
        'operation_hash': digest(request['operation_id']), 'state': 'PREPARED', 'capture_ref': None}
    state_save(e, target, fields, 3, 'persist-original-capture')
    must(read_restarted(e, target)['fields']['manualCapture'] == fields['manualCapture'])
    captured = call(e, 'POST', e['base']+'/harness/manual-changes', request, 201)
    operation = call(e, 'GET', e['base']+'/harness/manual-changes/operations/'+request['operation_id']+
        '?request_sha256='+fields['manualCapture']['request_sha256'])
    must(operation['status']=='COMPLETED' and operation['capture_ref']==captured['capture_ref'])
    hunk = captured['snapshot']['files'][0]['hunks'][0]; must(hunk['keep_text'] == MARKER)
    candidate = deepcopy(target['payload']['candidate']); node = next(n for n in candidate['nodes'] if n['id']=='C')
    before = deepcopy(node['acceptance']); node['acceptance'].append(hunk['keep_text'])
    decision = {'hunk_id': hunk['id'], 'hunk_sha256': hunk['sha256'], 'mode': 'KEEP',
        'target': {'node_id':'C','field':'acceptance','edit_action':'ADD','item_index':None,'before_value_sha256':digest(before)},
        'text': hunk['keep_text'], 'reason': '原创QA明确采用保存原文。'}
    edit = ProposalEdit.model_validate({'operation_id':'adopt-state60','expected_revision':target['revision'],
        'candidate':candidate,'change_reason':'从真实草稿恢复并采用C改文。',
        'manual_review':{'change_ref':captured['capture_ref'],'decisions':[decision]}}).model_dump(exclude={'requirement_coverage'})
    fields['candidate'] = candidate; fields['manualCapture'].update(state='COMPLETED',capture_ref=captured['capture_ref'])
    fields['manualReview'] = {'change_ref':captured['capture_ref'],'choices':[{'hunk_id':hunk['id'],'hunk_sha256':hunk['sha256'],
        'node_id':'C','field':'acceptance','edit_action':'ADD','item_index':'','mode':'KEEP','text':hunk['keep_text'],'reason':decision['reason']}],
        'applied':[decision]}
    fields['manualSave'] = {**edit,'request_sha256':digest({'proposal_id':target['id'],**{k:v for k,v in edit.items()if k!='operation_id'}}),
        'state':'UNKNOWN','result_ref':None}
    state_save(e, target, fields, 4, 'persist-original-adoption')
    must(read_restarted(e, target)['fields'] == fields)
    adopted = call(e, 'PUT', e['base'], edit)
    original_save = call(e, 'GET', f"/api/projects/{e['pid']}/planning/operations/{edit['operation_id']}")
    must(ref(original_save)==ref(adopted) and adopted['payload']['manual_inheritance'][0]['capture_ref']==captured['capture_ref'])
    fields['manualSave'].update(state='COMPLETED',result_ref=ref(adopted))
    state_save(e, target, fields, 5, 'persist-applied-adoption')
    must(read_restarted(e, target)['fields'] == fields)
    confirmed = approve(e, adopted, 'confirm-adopt-state60')
    new_export = call(e, 'POST', e['base']+'/harness/export', export_body(e, confirmed, 'new-export-state60'))
    new_c = next(f for f in new_export['files']if f['path'].endswith('/task-C.md'))
    must(MARKER in (e['work']/new_c['path']).read_text())
    must(sha((e['work']/item['path']).read_bytes()) == saved_file['source_ref']['sha256'])


def test_actual_pending_export_gate_persists_without_fake_clear_or_automatic_recovery(scenario, monkeypatch):
    e = scenario; exports = e['app'].state.planning_exports; target = e['approved']
    body = HarnessExportInput.model_validate(export_body(e, target, 'pending-export-state60'))
    def fail_directory(*args, **kwargs):
        raise OSError('Original QA interruption after durable request, before output directory')
    with monkeypatch.context() as patch:
        patch.setattr(exports, '_directories', fail_directory)
        with pytest.raises(OSError): exports.export(e['pid'], target['id'], body)
    response = call(e, 'POST', e['base']+'/harness/export', export_body(e, target, 'observe-pending-state60'),409)
    gate = {'code':response['code'],**response['detail']['manual_changes']}
    must(gate['code']=='UNVERIFIED' and not gate['coverage']['complete'])
    must(gate['coverage']['pending_exports'][0]['recovery']=='ORIGINAL_OPERATION_ONLY')
    fields = {'manualGate':gate};state_save(e,target,fields)
    must(read_restarted(e,target)['fields']==fields)
    wrong = deepcopy(fields);wrong['manualGate']['coverage']['complete']=True
    state_save(e,target,wrong,1,'cannot-clear-pending',422)
    # Only the caller's explicit original operation resumes business work.
    completed = exports.export(e['pid'],target['id'],body)
    must(completed['status']=='EXPORTED_CONFIRMED_PLAN')
    must(read_restarted(e,target)['fields']==fields, 'Historical draft must not silently rewrite its coverage')


def test_actual_33_changed_files_limit_and_failed_read_receipt_remain_bounded_presentation(scenario):
    e=scenario; candidate=deepcopy(e['approved']['payload']['candidate'])
    for index in range(30):
        node=deepcopy(candidate['nodes'][2]);node['id']=f'EXTRA{index}';node['title']=f'原创额外节点{index}';candidate['nodes'].append(node)
    draft=call(e,'PUT',e['base'],{'operation_id':'33-nodes-state60','expected_revision':e['approved']['revision'],
        'candidate':candidate,'change_reason':'真实33处变动显示预算边界，不授自动捕获。'})
    target=approve(e,draft,'confirm-33-state60');original=exported(e,target,'export-33-state60')
    for item in original['files']:
        if item['path'].endswith('.md') and '/task-'in item['path']:
            f=e['work']/item['path'];f.write_bytes(f.read_bytes()+b'\nORIGINAL_CHANGED_33\n')
    response=call(e,'POST',e['base']+'/harness/export',export_body(e,target,'gate-33-state60'),409)
    gate={'code':response['code'],**response['detail']['manual_changes']}
    must(gate['code']=='UNVERIFIED' and len(gate['files'])==32 and gate['coverage']['limit']['observed_at_least']==33)
    fields={'manualGate':gate};state_save(e,target,fields)
    must(read_restarted(e,target)['fields']==fields)
    wrong=deepcopy(fields);wrong['manualGate']['coverage']['limit']['max']=True
    state_save(e,target,wrong,1,'invalid-limit-type',422)
