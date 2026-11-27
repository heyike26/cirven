"""Isolated TestClient/real Word/harness only; never a live product API or model."""
from copy import deepcopy
from conftest import client
from test_harness_manual_continuity import scenario,no_external,call,ref,tree
from packages.domain.planning_delivery import DeliveryExport
from packages.domain.store import Conflict


def check(value,message='Real original recovery acceptance failed'):
    if not value:raise AssertionError(message)


def test_explicit_saved_request_endpoint_runs_real_original_export_once_and_keeps_report(scenario,monkeypatch):
    e=scenario;delivery=e['app'].state.planning_delivery;authority=e['plans'].manual_changes
    original=authority.require_clear;gates=[]
    body={'operation_id':'known-partial-original','proposal_ref':ref(e['approved']),
        'workspace_ref':e['workspace'],'directory':'harness/known-partial'}
    route=f'/api/projects/{e["pid"]}/planning-delivery/proposals/{e["approved"]["id"]}/export'
    operation=f'/api/projects/{e["pid"]}/planning-delivery/operations/{body["operation_id"]}'
    def fail_once(*args,**kwargs):
        gates.append(kwargs.get('_active_export'))
        if len(gates)==6:raise Conflict('QA_EXPLICIT_RECOVERY_KNOWN_HARNESS_POSTWRITE_FAILURE')
        return original(*args,**kwargs)
    with monkeypatch.context()as patch:
        patch.setattr(authority,'require_clear',fail_once)
        call(e,'POST',route,body,409)
    check(len(gates)==6)
    folder=delivery._path(e['pid'],body['operation_id']);saved_request=(folder/'request.json').read_bytes()
    saved_report=(folder/'report.json').read_bytes();report=delivery._read(folder/'report.json');before=tree(e['work'])
    sha=delivery._read(folder/'request.json')['request_sha256']
    inspected=call(e,'GET',operation)
    check(inspected['status']=='FAILED_OR_PARTIAL' and inspected['export_resume']['request_sha256']==sha)
    call(e,'POST',operation+'/resume-export',{'request_sha256':sha,'directory':'replace-original'},422)
    call(e,'POST',operation+'/resume-export',{'request_sha256':'f'*64},409)
    check(not(folder/'resume-attempts').exists() and tree(e['work'])==before)
    with delivery.materials._lock(folder/'export-lock'):
        busy=call(e,'POST',operation+'/resume-export',{'request_sha256':sha},409)
        check(busy['code']=='BUSY')
        check(call(e,'GET',operation)['status']=='PROCESSING')
    check(not(folder/'resume-attempts').exists())
    resumed=[]
    def observe(*args,**kwargs):
        check(delivery._owns_export_lock(e['pid'],DeliveryExport.model_validate(body)), 'Actual gate ran without original export lock')
        resumed.append(deepcopy(kwargs.get('_active_export')))
        return original(*args,**kwargs)
    with monkeypatch.context()as patch:
        patch.setattr(authority,'require_clear',observe)
        recovered=call(e,'POST',operation+'/resume-export',{'request_sha256':sha})
    check(len(resumed)==6 and all(resumed),f'Existing pending branch must run its six existing gates, got {resumed!r}')
    check(recovered['operation_id']==body['operation_id'] and recovered['status']=='EXPORTED_CONFIRMED_PLAN_DERIVATIVES')
    check(recovered['report']['artifact']['id']==report['artifact']['id'] and recovered['execution_authorized']is False)
    check((folder/'request.json').read_bytes()==saved_request and (folder/'report.json').read_bytes()==saved_report)
    check(all(tree(e['work'])[path]==sha for path,sha in before.items()))
    check(delivery._read(folder/'resume-attempts/0001/result.json')['status']=='COMPLETED')
    call(e,'POST',operation+'/resume-export',{'request_sha256':sha},409)
    check(call(e,'GET',operation)['operation_id']==body['operation_id'])
    check((folder/'failure-original.json').exists()is False and len(list(folder.glob('failure-*.json')))==1,'Original failure must remain')


import pytest
from test_harness_manual_continuity import change_a

@pytest.mark.parametrize('drift',['proposal','source','workspace'])
def test_descriptor_is_only_observation_current_basis_drift_rejects_before_any_generator(scenario,monkeypatch,drift):
    e=scenario;delivery=e['app'].state.planning_delivery;authority=e['plans'].manual_changes;original=authority.require_clear;gates=[]
    body={'operation_id':'stale-descriptor-original','proposal_ref':ref(e['approved']),'workspace_ref':e['workspace'],'directory':'harness/stale-descriptor'}
    route=f'/api/projects/{e["pid"]}/planning-delivery/proposals/{e["approved"]["id"]}/export'
    operation=f'/api/projects/{e["pid"]}/planning-delivery/operations/{body["operation_id"]}'
    def fail_once(*args,**kwargs):
        gates.append(None)
        if len(gates)==6:raise Conflict('QA_EXPLICIT_KNOWN_POSTWRITE_FAILURE')
        return original(*args,**kwargs)
    with monkeypatch.context()as patch:
        patch.setattr(authority,'require_clear',fail_once);call(e,'POST',route,body,409)
    folder=delivery._path(e['pid'],body['operation_id']);request=(folder/'request.json').read_bytes();report=(folder/'report.json').read_bytes()
    inspected=call(e,'GET',operation);check(inspected['export_resume']['proposal_ref']==body['proposal_ref'])
    preserved=e['work']
    if drift=='proposal':change_a(e,e['approved'],'descriptor-after-proposal-edit')
    elif drift=='source':
        path=e['work']/'资料.md';path.write_bytes(path.read_bytes()+b'\nQA_SOURCE_DRIFT_AFTER_DESCRIPTOR\n')
    else:
        preserved=e['work'].with_name(e['work'].name+' moved-only-owned-fixture');e['work'].rename(preserved);e['work'].mkdir()
    before=tree(preserved);generated=[]
    def deny(*args,**kwargs):generated.append('GENERATOR');raise AssertionError('Stale descriptor ran a generator')
    with monkeypatch.context()as patch:
        patch.setattr(e['app'].state.planning_reports,'generate',deny)
        patch.setattr(e['app'].state.planning_diagrams,'generate',deny)
        patch.setattr(e['plans'],'taskbook',deny)
        patch.setattr(e['app'].state.planning_exports,'export',deny)
        rejected=call(e,'POST',operation+'/resume-export',{'request_sha256':inspected['export_resume']['request_sha256']},409)
    check(not generated and tree(preserved)==before)
    check((folder/'request.json').read_bytes()==request and (folder/'report.json').read_bytes()==report)
    check(not(folder/'result.json').exists() and (folder/'resume-attempts/0001/result.json').exists())
