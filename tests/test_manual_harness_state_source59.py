"""Real ASGI presentation recovery only; manual adoption authority is tested separately."""
from copy import deepcopy
from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.api.local_workspaces import register_local_workspace_routes
from apps.api.workbench_state import register_workbench_state_routes
from packages.domain.tasks import TaskService
from packages.domain.workbench_state import canonical, sha
from packages.domain.planning_schema import PlanningCandidate, ManualCaptureInput, ManualReview
from test_template_profile_imports import scene, ROOT

H = 'a' * 64


def check(value, message='Source59 manual presentation acceptance failed'):
    if not value: raise AssertionError(message)


def digest(value): return sha(canonical(value).encode())


def fixture(e):
    scope = {'project_id': e['b'], 'kind': 'proposal', 'object_id': 'proposal-manual-ui', 'revision': 3, 'sha256': H}
    workspace = {k: e['bbind'][k] for k in ('id', 'revision', 'binding_sha256')}
    target = {'id': scope['object_id'], 'revision': scope['revision'], 'sha256': H}
    export = {'id': 'e' * 64, 'request_sha256': H, 'result_sha256': H}
    file = {'export_ref': export, 'path': 'output/task-C.md', 'basis_sha256': H, 'edited_sha256': 'b' * 64}
    request = ManualCaptureInput.model_validate({'operation_id': 'original-capture', 'target_proposal_ref': target,
        'workspace_ref': workspace, 'files': [file]}).model_dump()
    capref = {'id': 'c' * 64, 'revision': 1, 'sha256': H}
    decision = {'hunk_id': 'd' * 64, 'hunk_sha256': H, 'mode': 'KEEP',
        'target': {'node_id': 'C', 'field': 'acceptance', 'edit_action': 'ADD', 'item_index': None,
                   'before_value_sha256': digest(['原验收'])}, 'text': 'MANUAL_ONLY_C_314159', 'reason': '明确承接'}
    candidate = PlanningCandidate.model_validate({'title': '原创三任务', 'summary': '仅演示草稿保存，不授捕获或写文件权',
        'nodes': [{'id': 'C', 'title': '独立核对', 'description': '当前说明', 'acceptance': ['原验收', 'MANUAL_ONLY_C_314159']}]}).model_dump()
    review = ManualReview.model_validate({'change_ref': capref, 'decisions': [decision]}).model_dump()
    body = {'expected_revision': 3, 'candidate': candidate, 'change_reason': '承接人工改文', 'manual_review': review}
    fields = {'candidate': candidate, 'changeReason': body['change_reason'],
        'manualGate': {'code': 'MANUAL_REVIEW_REQUIRED', 'target_proposal_ref': target, 'workspace_ref': workspace,
            'status': 'REVIEW_REQUIRED', 'files': [{**file, 'node_id': 'C', 'kind': 'PROMPT', 'status': 'MODIFIED'}],
            'coverage': {'complete': True, 'total_registered_files': 1, 'checked_files': 1, 'scanned_export_count': 1, 'unverified': []},
            'predecessor_export_refs': [export]},
        'manualCapture': {'request': request, 'request_sha256': digest({k: v for k, v in request.items() if k != 'operation_id'}),
            'operation_hash': digest(request['operation_id']), 'state': 'COMPLETED', 'capture_ref': capref},
        'manualReview': {'change_ref': capref, 'choices': [{'hunk_id': decision['hunk_id'], 'hunk_sha256': H,
            'node_id': 'C', 'field': 'acceptance', 'edit_action': 'ADD', 'item_index': '', 'mode': 'KEEP',
            'text': decision['text'], 'reason': decision['reason']}], 'applied': [decision]},
        'manualSave': {'operation_id': 'original-save', **body, 'request_sha256': digest({'proposal_id': scope['object_id'], **body}),
            'state': 'UNKNOWN', 'result_ref': None}}
    return scope, fields


def register(e):
    register_workbench_state_routes(e['app'], e['store'], e['data'], workspaces=e['app'].state.local_workspaces)
    return f"/api/projects/{e['b']}/workbench-state"


def save(client, base, scope, fields, revision=0, op='save-original-manual-presentation'):
    return client.post(base + '/save', json={'key': scope, 'expected_revision': revision, 'operation_id': op,
        'fields': fields, 'view': {'selected': ['C'], 'editing': True, 'scroll': {'x': 0, 'y': 416}}, 'dirty': True})


def test_typed_manual_request_choices_and_unknown_save_survive_actual_state_service_restart(scene):
    e = scene; base = register(e); scope, fields = fixture(e)
    response = save(e['client'], base, scope, fields); check(response.status_code == 200, response.text)
    restarted_store = TaskService(e['data'] / 'eeas.sqlite3', ROOT); app = FastAPI()
    register_local_workspace_routes(app, restarted_store, e['data'])
    register_workbench_state_routes(app, restarted_store, e['data'], workspaces=app.state.local_workspaces)
    with TestClient(app) as client:
        loaded = client.post(base + '/load', json={'key': scope}); check(loaded.status_code == 200, loaded.text)
        check(loaded.json()['fields'] == fields)
        check(loaded.json()['view'] == {'selected': ['C'], 'editing': True, 'scroll': {'x': 0, 'y': 416}})
        receipt = client.get(base + '/operations/save-original-manual-presentation')
        check(receipt.status_code == 200 and receipt.json()['fields']['manualSave']['operation_id'] == 'original-save')
    # These are typed presentation refs, intentionally not fabricated capture authority.
    check(not (e['data'] / 'harness-manual').exists())


def test_invalid_manual_foreign_basis_unknown_fields_and_bool_revisions_keep_prior_input(scene):
    e = scene; base = register(e); scope, fields = fixture(e)
    check(save(e['client'], base, scope, fields).status_code == 200)
    variants = []
    bad = deepcopy(fields); bad['manualCapture']['request']['target_proposal_ref']['id'] = 'foreign-proposal'; variants.append(bad)
    bad = deepcopy(fields); bad['manualCapture']['request']['target_proposal_ref']['revision'] = True; variants.append(bad)
    bad = deepcopy(fields); bad['manualCapture']['request_sha256'] = '0' * 64; variants.append(bad)
    bad = deepcopy(fields); bad['manualGate']['files'][0]['path'] = '../outside.md'; variants.append(bad)
    bad = deepcopy(fields); bad['manualReview']['choices'][0]['mode'] = 'AUTO_ACCEPT'; variants.append(bad)
    bad = deepcopy(fields); bad['manualReview']['applied'][0]['target']['field'] = 'capabilities'; variants.append(bad)
    bad = deepcopy(fields); bad['manualReview']['change_ref']['id'] = 'f' * 64; variants.append(bad)
    bad = deepcopy(fields); bad['manualSave']['result_ref'] = {'id': 'other', 'revision': 4, 'sha256': H}; bad['manualSave']['state'] = 'COMPLETED'; variants.append(bad)
    bad = deepcopy(fields); bad['manualSave']['extra_permission'] = True; variants.append(bad)
    for index, bad in enumerate(variants):
        response = save(e['client'], base, scope, bad, 1, f'reject-manual-{index}')
        check(response.status_code == 422, response.text)
        current = e['client'].post(base + '/load', json={'key': scope}).json(); check(current['state_revision'] == 1 and current['fields'] == fields)


def test_incomplete_editing_choices_and_legacy_proposal_fields_remain_presentation_only(scene):
    e = scene; base = register(e); scope, fields = fixture(e)
    fields.pop('manualSave'); fields['manualReview']['choices'][0].update(mode='', field='', edit_action='', reason='', text='')
    fields['manualReview']['applied'] = []
    check(save(e['client'], base, scope, fields).status_code == 200)
    legacy = {'candidate': fields['candidate'], 'answers': {}, 'resolutions': {}, 'changeReason': '保原人工草稿', 'templatePath': '', 'templateName': '', 'harnessDirectory': 'output/new', 'destinationId': e['bbind']['id']}
    check(save(e['client'], base, scope, legacy, 1, 'legacy-presentation').status_code == 200)
    loaded = e['client'].post(base + '/load', json={'key': scope}).json(); check(loaded['fields'] == legacy)
