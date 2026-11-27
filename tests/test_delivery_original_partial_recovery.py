"""Real ASGI/report/harness recovery of one known partial, without a model replay."""
from copy import deepcopy
from pathlib import Path

from packages.domain.local_workspaces import digest, sha
from packages.domain.store import Conflict
from test_harness_manual_continuity import scenario, no_external, call, ref, tree


def test_exact_original_outer_request_recovers_own_pending_without_regenerating_report(scenario, monkeypatch):
    e = scenario; delivery = e['app'].state.planning_delivery
    authority = e['plans'].manual_changes
    original = authority.require_clear; gates = []
    body = {'operation_id':'known-partial-original', 'proposal_ref':ref(e['approved']),
            'workspace_ref':e['workspace'], 'directory':'harness/known-partial'}
    route = f'/api/projects/{e["pid"]}/planning-delivery/proposals/{e["approved"]["id"]}/export'

    def fail_once_after_harness_files(*args, **kwargs):
        gates.append(kwargs.get('_active_export'))
        if len(gates) == 6:
            raise Conflict('QA_SOURCE33_KNOWN_HARNESS_POSTWRITE_FAILURE')
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(authority, 'require_clear', fail_once_after_harness_files)
        failed = call(e, 'POST', route, body, 409)
    assert 'QA_SOURCE33_KNOWN_HARNESS_POSTWRITE_FAILURE' in str(failed) and len(gates) == 6
    folder = delivery._path(e['pid'], body['operation_id'])
    saved_request = (folder/'request.json').read_bytes()
    saved_report = (folder/'report.json').read_bytes()
    report = delivery._read(folder/'report.json')
    report_bytes = call(e, 'GET', f'/api/projects/{e["pid"]}/planning-delivery/operations/{body["operation_id"]}')
    assert report_bytes['status'] == 'FAILED_OR_PARTIAL'
    assert report_bytes['steps']['report']['artifact']['id'] == report['artifact']['id']
    partial_files = tree(e['work'])
    assert partial_files and not (folder/'result.json').exists()

    changed = deepcopy(body); changed['directory'] += '-changed'
    assert 'IDEMPOTENCY_CONFLICT' in str(call(e, 'POST', route, changed, 409))
    foreign = deepcopy(body); foreign['operation_id'] = 'new-operation-must-not-adopt-pending'
    call(e, 'POST', route, foreign, 409)
    assert not delivery._path(e['pid'], foreign['operation_id']).exists()
    assert tree(e['work']) == partial_files

    recovered = call(e, 'POST', route, body)
    assert recovered['status'] == 'EXPORTED_CONFIRMED_PLAN_DERIVATIVES'
    assert recovered['operation_id'] == body['operation_id']
    assert recovered['report']['artifact']['id'] == report['artifact']['id']
    assert recovered['execution_authorized'] is False
    assert (folder/'request.json').read_bytes() == saved_request
    assert (folder/'report.json').read_bytes() == saved_report
    assert all(tree(e['work'])[path] == value for path, value in partial_files.items())
    assert call(e, 'POST', route, body)['report']['artifact']['id'] == report['artifact']['id']
