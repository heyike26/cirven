"""Direct-domain and lock tests only; no App/API/model/native Windows execution."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import threading
import time

import pytest
from pydantic import ValidationError
from packages.domain.local_materials import LocalMaterials,LocalMaterialBusy
from packages.domain.local_workspaces import LocalWorkspaces,canonical,digest
from packages.domain.planning_delivery import PlanningDelivery,DeliveryExport,DeliveryExportResume
from packages.domain.store import Conflict,NotFound
from packages.adapters.windows_workspace_handles import Win32Kernel

def check(value,message='Explicit continuation acceptance failed'):
    if not value:raise AssertionError(message)
def tree(path):return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest()for p in path.rglob('*')if p.is_file()}

class Store:
    @staticmethod
    def project(pid):
        if pid!='p':raise NotFound('Unknown fixture project')
class Workspace:
    _json=staticmethod(LocalWorkspaces._json)
    store=Store()
    def __init__(self,path):self.data_dir=path
    @staticmethod
    def _native_handles():return None
class Publisher:
    @staticmethod
    def publish_export(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8')as output:output.write(canonical(value)+'\n')

def fixture(tmp_path,kind='EXPORT',failed=True,lock=True):
    ws=Workspace(tmp_path);materials=LocalMaterials(ws)
    service=PlanningDelivery(ws.store,ws,materials,None,None,None,None,SimpleNamespace(manual=Publisher()))
    body=DeliveryExport(operation_id='original-op',proposal_ref={'id':'original-plan','revision':5,'sha256':'a'*64},
        workspace_ref={'id':'c'*64,'revision':1,'binding_sha256':'b'*64},directory='original-delivery')
    folder=service._path('p',body.operation_id);folder.mkdir(parents=True)
    request={'kind':kind,'input':body.model_dump()};hashed=digest(request)
    claim={**request,'request_sha256':hashed,'owner_epoch':service.epoch,'created_at':'fixture-original'}
    service._put(folder/'request.json',claim,atomic=True)
    service._put(folder/'report.json',{'saved_report':'ORIGINAL_REPORT_STEP'},atomic=True)
    if failed:service._put(folder/'failure-original.json',{'status':'FAILED_OR_PARTIAL','reason':'ManualChangesConflict',
        'automatic_model_replay':False,'old_files_and_steps_preserved':True},atomic=True)
    if lock:
        with(folder/'export-lock').open('xb'):pass
    service._present=lambda pid,value:value
    return service,body,hashed,folder

def test_get_is_read_only_and_advertises_only_original_failed_export(tmp_path):
    service,body,hashed,folder=fixture(tmp_path);before=tree(tmp_path)
    value=service.operation('p',body.operation_id)
    check(value['status']=='FAILED_OR_PARTIAL' and value['request_sha256']==hashed)
    check(value['export_resume']=={'action':'RESUME_EXPORT','operation_id':body.operation_id,'project_id':'p',
        'request_sha256':hashed,'proposal_ref':body.proposal_ref.model_dump()})
    check('input'not in value and tree(tmp_path)==before,'GET exposed/wrote the original body or changed files')
    with service.materials._lock(folder/'export-lock'):
        busy=service.operation('p',body.operation_id)
        check(busy['status']=='PROCESSING' and 'export_resume'not in busy)
    check(tree(tmp_path)==before)

@pytest.mark.parametrize('kind,failed,lock',[('INTAKE',True,True),('STAGE',True,True),('EXPORT',False,True),('EXPORT',True,False)])
def test_unsupported_or_unverified_states_never_resume_or_create_locks(tmp_path,kind,failed,lock,request):
    service,body,hashed,folder=fixture(tmp_path,kind,failed,lock)
    scope=tmp_path
    before=tree(scope)
    if kind=='STAGE':
        # A legacy export body relabelled STAGE cannot authenticate its source.
        with pytest.raises(Conflict,match='Original stage request identity or source is invalid'):
            service.operation('p',body.operation_id)
        with pytest.raises(Conflict):
            service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
        check(tree(scope)==before)
        # An authentic pending stage is readable and equally ineligible for export resume.
        from test_planning_delivery_operation_history import create_session,saved_stage
        client=request.getfixturevalue('client')
        pid,session=create_session(client)
        service=client.app.state.planning_delivery
        folder=saved_stage(service,pid,session,key=body.operation_id,status=None)
        claim=service._read(folder/'request.json')
        hashed=claim['request_sha256']
        scope=service.ws.data_dir / 'planning-delivery'
        before=tree(scope)
    else:
        pid='p'
    check('export_resume'not in service.operation(pid,body.operation_id))
    with pytest.raises(Conflict):service.resume_export(pid,body.operation_id,DeliveryExportResume(request_sha256=hashed))
    check(tree(scope)==before)


def test_resume_reloads_whole_saved_body_same_key_and_preserves_original_steps(tmp_path):
    service,body,hashed,folder=fixture(tmp_path);before=tree(tmp_path);calls=[]
    def original_export(pid,ident,received):
        check(pid=='p' and ident==body.proposal_ref.id and received.model_dump()==body.model_dump())
        check(service._owns_export_lock(pid,received),'Original lock was not explicitly owned')
        with pytest.raises(LocalMaterialBusy):
            with service.materials._lock(folder/'export-lock',blocking=False,create=False):pass
        value={'status':'EXPORTED_CONFIRMED_PLAN_DERIVATIVES','operation_id':body.operation_id}
        service._put(folder/'result.json',value,atomic=True);calls.append(received.model_dump());return value
    service.export=original_export
    value=service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
    check(value['operation_id']==body.operation_id and len(calls)==1)
    for name,sha in before.items():check(tree(tmp_path)[name]==sha,'Original evidence changed: '+name)
    audit=service._read(folder/'resume-attempts/0001/result.json');check(audit['status']=='COMPLETED')
    with pytest.raises(Conflict):service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
    check(len(calls)==1,'Completed operations were replayed')

def test_bad_hash_extra_body_foreign_scope_and_tampered_saved_request_fail_before_write(tmp_path):
    service,body,hashed,folder=fixture(tmp_path);before=tree(tmp_path)
    with pytest.raises(ValidationError):DeliveryExportResume(request_sha256=hashed,directory='replacement')
    with pytest.raises(Conflict):service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256='f'*64))
    with pytest.raises(NotFound):service.resume_export('foreign',body.operation_id,DeliveryExportResume(request_sha256=hashed))
    with pytest.raises(NotFound):service.resume_export('p','missing',DeliveryExportResume(request_sha256=hashed))
    check(tree(tmp_path)==before)
    claim=service._read(folder/'request.json');claim['input']['directory']='changed-body'
    # An intentionally self-owned corrupt record; original request SHA stays old.
    (folder/'request.json').write_text(canonical({'payload':claim,'sha256':digest(claim)}))
    before=tree(tmp_path)
    check('export_resume'not in service.operation('p',body.operation_id))
    with pytest.raises(Conflict):service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
    check(tree(tmp_path)==before)

def test_concurrent_resume_and_get_see_busy_without_waiting_or_second_export(tmp_path):
    service,body,hashed,folder=fixture(tmp_path);entered=threading.Event();release=threading.Event();errors=[];calls=[]
    def slow_export(pid,ident,original):
        calls.append(original.operation_id);entered.set();check(release.wait(3),'Test release timed out')
        raise Conflict('Known controlled failure')
    service.export=slow_export
    def run():
        try:service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
        except Exception as error:errors.append(error)
    worker=threading.Thread(target=run,daemon=True);worker.start()
    try:
        check(entered.wait(3),'First resume did not acquire the original lock')
        value=service.operation('p',body.operation_id);check(value['status']=='PROCESSING'and'export_resume'not in value)
        start=time.monotonic()
        with pytest.raises(LocalMaterialBusy):service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
        check(time.monotonic()-start<.5,'Second resume waited behind the operation')
    finally:release.set();worker.join(3)
    check(not worker.is_alive() and len(calls)==len(errors)==1)
    check(service.operation('p',body.operation_id)['status']=='FAILED_OR_PARTIAL')
    check(service._read(folder/'resume-attempts/0001/result.json')['status']=='FAILED_OR_PARTIAL')

def test_interrupted_latest_attempt_is_unknown_even_with_historical_failure(tmp_path):
    service,body,hashed,folder=fixture(tmp_path)
    service.export=lambda *args:(_ for _ in()).throw(KeyboardInterrupt('controlled interrupted continuation'))
    with pytest.raises(KeyboardInterrupt):service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
    check(not(folder/'resume-attempts/0001/result.json').exists())
    before=tree(tmp_path);value=service.operation('p',body.operation_id)
    check(value['status']=='INTERRUPTED_REVIEW_REQUIRED'and'export_resume'not in value)
    with pytest.raises(Conflict):service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
    check(tree(tmp_path)==before,'Unknown continuation was replayed or rewritten')

def test_internal_lock_capability_cannot_be_reused_for_another_body(tmp_path):
    service,body,hashed,folder=fixture(tmp_path)
    def export(pid,ident,original):
        check(service._owns_export_lock(pid,original))
        with pytest.raises(Conflict):service._owns_export_lock(pid,original.model_copy(update={'directory':'another'}))
        with pytest.raises(Conflict):service._owns_export_lock('foreign',original)
        raise Conflict('controlled exact-lock check')
    service.export=export
    with pytest.raises(Conflict):service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
    check(service._owns_export_lock('p',body)is False,'Internal capability leaked after failure')

def test_win32_nonblocking_lock_flag_abi_without_claiming_native_kernel():
    api=Win32Kernel.__new__(Win32Kernel);flags=[]
    api.lock_file=lambda handle,mode,*args:(flags.append(mode)or 1)
    api.lock(1);api.lock(1,blocking=False);check(flags==[2,3])
    with pytest.raises(Exception):api.lock(1,blocking=1)


@pytest.mark.parametrize('error',[OSError('write outcome unknown'),Conflict('UNKNOWN_OUTCOME: interrupted original write')])
def test_unexpected_or_explicit_unknown_continuation_never_becomes_resumable(tmp_path,error):
    service,body,hashed,folder=fixture(tmp_path)
    service.export=lambda *args:(_ for _ in()).throw(error)
    with pytest.raises(type(error)):service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
    before=tree(tmp_path);value=service.operation('p',body.operation_id)
    check(value['status']=='INTERRUPTED_REVIEW_REQUIRED' and 'export_resume'not in value)
    with pytest.raises(Conflict):service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
    check(tree(tmp_path)==before)

def test_legacy_unknown_failure_and_audit_capacity_are_readonly_closed(tmp_path):
    service,body,hashed,folder=fixture(tmp_path)
    original=service._read(folder/'failure-original.json')
    for failure in [dict(original,reason='OSError'),dict(original,reason='Conflict',message='UNKNOWN_OUTCOME: interrupted original write')]:
        (folder/'failure-original.json').write_text(canonical({'payload':failure,'sha256':digest(failure)}))
        before=tree(tmp_path)
        check(service.operation('p',body.operation_id)['status']=='INTERRUPTED_REVIEW_REQUIRED')
        with pytest.raises(Conflict):service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
        check(tree(tmp_path)==before)
    (folder/'failure-original.json').write_text(canonical({'payload':original,'sha256':digest(original)}))
    for number in range(1,21):
        target=folder/'resume-attempts'/f'{number:04d}';target.mkdir(parents=True)
        service._put(target/'request.json',{'schema':'cirven.delivery-export-resume.v1','attempt':number,'operation_id':body.operation_id,'request_sha256':hashed},atomic=True)
        service._put(target/'result.json',{'attempt':number,'operation_id':body.operation_id,'request_sha256':hashed,'status':'FAILED_OR_PARTIAL'},atomic=True)
    before=tree(tmp_path)
    check('export_resume'not in service.operation('p',body.operation_id))
    with pytest.raises(Conflict):service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
    check(tree(tmp_path)==before,'Capacity exhaustion created another attempt')

def test_failed_diagnostic_write_does_not_mask_original_error_or_leak_lock(tmp_path):
    service,body,hashed,folder=fixture(tmp_path);original_put=service._put
    service.export=lambda *args:(_ for _ in()).throw(Conflict('ORIGINAL_CONTROLLED_FAILURE'))
    def put(path,payload,**kwargs):
        if path.name=='result.json' and path.parent.parent.name=='resume-attempts':raise OSError('diagnostic persistence unavailable')
        return original_put(path,payload,**kwargs)
    service._put=put
    with pytest.raises(Conflict,match='ORIGINAL_CONTROLLED_FAILURE'):service.resume_export('p',body.operation_id,DeliveryExportResume(request_sha256=hashed))
    check(service._owns_export_lock('p',body)is False)
    with service.materials._lock(folder/'export-lock',blocking=False,create=False):pass
    check(service.operation('p',body.operation_id)['status']=='INTERRUPTED_REVIEW_REQUIRED')


def test_legacy_original_export_cannot_bypass_any_explicit_attempt_even_when_unknown(tmp_path):
    service,body,hashed,folder=fixture(tmp_path)
    target=folder/'resume-attempts/0001';target.mkdir(parents=True)
    service._put(target/'request.json',{'schema':'cirven.delivery-export-resume.v1','attempt':1,
        'operation_id':body.operation_id,'request_sha256':hashed},atomic=True)
    before=tree(tmp_path)
    # Public old endpoint must reject before budget/proof/generator work.
    with pytest.raises(Conflict,match='explicit continuation audit'):service.export('p',body.proposal_ref.id,body)
    check(tree(tmp_path)==before)


def test_legacy_waiter_rechecks_attempt_after_it_actually_acquires_original_lock(tmp_path):
    from contextlib import contextmanager,nullcontext
    service,body,hashed,folder=fixture(tmp_path);queued=threading.Event();errors=[];gets=[]
    service.exports.manual.read_budget=lambda:{}
    service.exports.manual.proof_scope=lambda *args:nullcontext()
    service.exports.manual.require_clear=lambda *args,**kwargs:None
    proposal={**body.proposal_ref.model_dump(),'current_revision':5,'review_status':'CURRENT','payload':{'status':'CONFIRMED'}}
    service.proposals=SimpleNamespace(get=lambda *args:(gets.append(args)or deepcopy(proposal)))
    service._destination=lambda *args:None
    actual_lock=service.materials._lock
    @contextmanager
    def observed(path,**kwargs):
        if path.name=='export-lock':queued.set()
        with actual_lock(path,**kwargs):yield
    service.materials._lock=observed
    def legacy():
        try:service.export('p',body.proposal_ref.id,body)
        except Exception as error:errors.append(error)
    with actual_lock(folder/'export-lock'):
        worker=threading.Thread(target=legacy,daemon=True);worker.start()
        check(queued.wait(3),'Legacy export did not reach the real original lock')
        target=folder/'resume-attempts/0001';target.mkdir(parents=True)
        service._put(target/'request.json',{'schema':'cirven.delivery-export-resume.v1','attempt':1,
            'operation_id':body.operation_id,'request_sha256':hashed},atomic=True)
        before=tree(tmp_path)
    worker.join(3)
    check(not worker.is_alive() and len(errors)==1 and isinstance(errors[0],Conflict))
    check('explicit continuation audit'in str(errors[0]))
    check(len(gets)==1,'Legacy waiter proceeded beyond its preflight after a new unknown attempt')
    check(tree(tmp_path)==before,'Legacy waiter generated or rewrote evidence after the unknown attempt')
