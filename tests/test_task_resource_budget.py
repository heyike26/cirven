"""AUTHORED_NOT_RUN: durable work resource ledger, real scoped source readers.

Uses a private Store/LocalWorkspaces and real original records only when run.
No SDK, model, shell/tool, user database, browser, native or OS quota qualification.
"""
from concurrent.futures import ThreadPoolExecutor,TimeoutError as FutureTimeout
from copy import deepcopy
import os
import subprocess
import sys
import threading
import pytest
from packages.domain.store import Store,Conflict
from packages.domain.local_workspaces import LocalWorkspaces
from packages.domain.task_resource_budget import TaskResourceBudget,TaskResourceLimit,task_resource_scope,current_task_resource_budget
from packages.domain.context_read_budget import ContextReadBudget,context_read_scope,source_bytes,current_context_read_budget


@pytest.fixture
def resource(tmp_path):
    store=Store(tmp_path/'db.sqlite3');pid=store.create_project('Resources','UNIT original work')['id'];ws=LocalWorkspaces(store,tmp_path/'private')
    limits={'source_bytes':64,'source_reads':16,'model_attempts':3,'retained_output_bytes':16}
    authority={'project_id':pid,'kind':'ORIGINAL_WORK','root_operation_id':'work','request_sha256':'a'*64}
    b=TaskResourceBudget(ws,authority,limits=limits);b.allocate()
    return {'budget':b,'store':store,'ws':ws,'authority':authority,'limits':limits,'tmp':tmp_path}


def another(s):return TaskResourceBudget(s['ws'],s['authority'],limits=s['limits'])


def test_same_process_output_and_source_writers_share_the_actual_ledger_without_false_unknown(resource,monkeypatch):
    s=resource;entered=threading.Event();release=threading.Event();second_started=threading.Event()
    original=TaskResourceBudget._read
    def hold_first(self):
        if not entered.is_set():
            entered.set();assert release.wait(5)
        # The actual WAL format read now runs while the second thread holds
        # BEGIN IMMEDIATE and waits for this ledger. It must not need a writer.
        return original(self)
    monkeypatch.setattr(TaskResourceBudget,'_read',hold_first)
    def output():
        with s['store'].connect() as db:
            db.execute('BEGIN IMMEDIATE');second_started.set()
            return another(s).retain_received(5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(another(s).consume,source_bytes=1)
        try:
            assert entered.wait(3)
            second=pool.submit(output);assert second_started.wait(3)
            with pytest.raises(FutureTimeout):second.result(timeout=.1)
        finally:release.set()
        first.result(timeout=5);assert second.result(timeout=5) is not None
    value=another(s).inspect()
    assert value['used']['source_bytes']==1 and value['used']['retained_output_bytes']==5
    assert not value['accounting_unconfirmed'] and not s['budget'].gap.exists()


@pytest.mark.skipif(os.name!='posix',reason='Actual POSIX child lock; native Windows lock is a separate qualification')
def test_another_process_still_refuses_without_waiting_or_resetting_original_budget(resource):
    from packages.domain.local_materials import LocalMaterialBusy
    b=resource['budget'];before=b.path.read_bytes()
    code="import fcntl,os,sys;f=os.open(sys.argv[1],os.O_RDWR);fcntl.flock(f,fcntl.LOCK_EX);print('LOCKED',flush=True);sys.stdin.readline();os.close(f)"
    child=subprocess.Popen([sys.executable,'-c',code,str(b.lock_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='LOCKED'
        with pytest.raises(LocalMaterialBusy):another(resource).consume(source_bytes=1)
        assert b.path.read_bytes()==before
    finally:
        try:child.communicate('\n',timeout=5)
        except subprocess.TimeoutExpired:
            child.kill();child.communicate(timeout=5);raise
    assert child.returncode==0 and another(resource).inspect()['used']['source_bytes']==0


def test_three_resource_dimensions_share_one_original_work_and_negative_inspection_stays_readonly(resource):
    b=resource['budget'];b.consume(source_bytes=5,source_reads=1,model_attempts=1);b.retain_received(18)
    before=b.path.read_bytes();view=b.inspect();assert view['used']['source_bytes']==5 and view['used']['model_attempts']==1 and view['used']['retained_output_bytes']==18
    assert view['retention_threshold_exceeded'] and view['read_only'] and b.path.read_bytes()==before
    with pytest.raises(TaskResourceLimit):b.consume(model_attempts=1)
    assert b.path.read_bytes()==before


def test_reopened_ledger_preserves_unknown_read_and_model_reservations(resource):
    b=resource['budget'];b.consume(model_attempts=1);ticket=b.reserve_read(32);fresh=another(resource);fresh.allocate()
    assert fresh.inspect()['pending_read_bytes']==32 and fresh.inspect()['used']['model_attempts']==1
    fresh.finish_read(ticket,None);assert fresh.inspect()['used']['source_bytes']==32
    with pytest.raises(Conflict):fresh.finish_read(ticket,0)


def test_only_known_success_releases_the_unused_portion_of_a_read_bound(resource):
    b=resource['budget'];ticket=b.reserve_read(40);assert b.remaining_source_bytes()==24
    b.finish_read(ticket,3);assert b.inspect()['used']['source_bytes']==3 and b.remaining_source_bytes()==61
    assert b.inspect()['used']['source_reads']==1


def test_missing_state_with_existing_allocation_marker_never_resets_allowance(resource):
    b=resource['budget'];b.consume(model_attempts=1);b.path.unlink()
    with pytest.raises(Conflict,match='no allowance reset'):another(resource).allocate()
    assert b.marker.is_file() and not b.path.exists()


def test_business_sql_rollback_does_not_refund_an_already_charged_attempt(resource):
    s=resource;b=s['budget']
    with pytest.raises(RuntimeError):
        with s['store'].connect() as c:
            c.execute('BEGIN IMMEDIATE');b.consume(model_attempts=1);raise RuntimeError('UNIT later business rollback')
    assert another(s).inspect()['used']['model_attempts']==1


def test_parallel_actors_cannot_overdraw_shared_model_allowance(resource):
    s=resource
    def reserve(_):
        try:another(s).consume(model_attempts=1);return True
        except Conflict:return False
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(reserve,range(20)))
    assert sum(results)<=3 and s['budget'].inspect()['used']['model_attempts']==sum(results)
    assert s['budget'].inspect()['used']['model_attempts']>=1


def test_nested_scope_cannot_allocate_an_unrelated_work_allowance(resource):
    s=resource;other=TaskResourceBudget(s['ws'],{**s['authority'],'root_operation_id':'other'},limits=s['limits']);other.allocate()
    with task_resource_scope(s['budget']):
        with pytest.raises(Conflict):
            with task_resource_scope(other):pytest.fail('Original work replaced')
        assert current_task_resource_budget() is s['budget']
    assert current_task_resource_budget() is None


def test_real_file_reads_share_task_allowance_across_local_actions_and_scopes_reset(resource):
    s=resource;b=s['budget'];p=s['tmp']/'source';p.write_bytes(b'1234567890')
    with task_resource_scope(b):
        for _ in range(3):
            with context_read_scope():assert source_bytes(p)==b'1234567890'
    assert b.inspect()['used']['source_bytes']==30 and b.inspect()['used']['source_reads']==3
    assert current_task_resource_budget() is None and current_context_read_budget() is None


def test_guarded_reader_uses_known_bytes_but_failed_reader_charges_original_bound(resource):
    b=resource['budget']
    with task_resource_scope(b),context_read_scope() as local:
        assert local.guarded(lambda n:b'abc',20)==b'abc'
        def failed(n):raise OSError('UNIT read outcome unknown')
        with pytest.raises(OSError):local.guarded(failed,10)
    assert b.inspect()['used']['source_bytes']==14 and b.inspect()['used']['source_reads']==2 and b.inspect()['pending_reads']==0


def test_received_output_is_saved_by_caller_even_when_accounting_acknowledgement_is_lost(resource,monkeypatch):
    b=resource['budget'];change=b._change
    def lost(update):change(update);raise OSError('UNIT saved charge but ack lost')
    monkeypatch.setattr(b,'_change',lost);assert b.retain_received(8) is None
    artifact=resource['tmp']/'original-output';artifact.write_bytes(b'KEEP_OUTPUT')
    assert artifact.read_bytes()==b'KEEP_OUTPUT' and b.gap.exists()
    with pytest.raises(TaskResourceLimit):another(resource).consume(model_attempts=1)


def test_budget_query_never_allocates_missing_resource_or_changes_original_content(resource):
    s=resource;other=TaskResourceBudget(s['ws'],{**s['authority'],'root_operation_id':'never_allocated'},limits=s['limits'])
    with pytest.raises((Conflict,FileNotFoundError)):other.inspect()
    assert not other.path.exists() and not other.marker.exists()


@pytest.mark.parametrize('reader_kind',['guarded','fixed'])
def test_failed_read_preserves_original_error_and_pending_bound_if_settlement_fails(resource,monkeypatch,reader_kind):
    b=resource['budget']
    def settlement_failed(*args):raise OSError('UNIT quota acknowledgement unavailable')
    def source_failed(*args):raise ValueError('UNIT original source error')
    monkeypatch.setattr(b,'finish_read',settlement_failed)
    with task_resource_scope(b),context_read_scope() as local:
        with pytest.raises(ValueError,match='original source error'):getattr(local,reader_kind)(source_failed,10)
    usage=another(resource).inspect()
    assert usage['pending_reads']==1 and usage['pending_read_bytes']==(11 if reader_kind=='guarded' else 10)


def test_corrupt_allocation_marker_is_a_conflict_without_counter_reset(resource):
    from packages.domain.native_capabilities import NativeCapabilities
    b=resource['budget'];before=b.path.read_bytes()
    NativeCapabilities._write(b.marker,[],replace=True)
    with pytest.raises(Conflict):another(resource).allocate()
    assert b.path.read_bytes()==before


def test_revision_capacity_stops_without_overwriting_original_accounting(resource):
    b=resource['budget'];value=b._read();value['revision']=2**48-1;b._save(value);before=b.path.read_bytes()
    with pytest.raises(TaskResourceLimit):b.consume(model_attempts=1)
    assert b.path.read_bytes()==before
