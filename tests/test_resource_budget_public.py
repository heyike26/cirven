"""AUTHORED_NOT_RUN: read-only original-work usage projection, real records."""
import sqlite3
import pytest
from packages.domain.store import Conflict
from packages.domain.task_resource_budget import TaskResourceBudget
from packages.agent_sessions.task_resources import for_operation
from packages.agent_sessions import resource_budget_public as resources
from test_experience_candidates import experience
from test_resource_retention import retained,append,info


def view(s,operation=True,sha=None):
    owner=s['sessions'];_,reference=owner._manifest(s['pid'],s['sid'])
    return resources.public(owner,s['pid'],s['sid'],sha or reference['sha256'],s['scope']['root_operation_id'] if operation else None)


def test_usage_read_does_not_allocate_new_budget_or_fabricate_historical_zero(experience,monkeypatch):
    s=experience;o=s['sessions'];path=o._path(s['pid'],s['sid'],'operations');before={p.name:p.read_bytes() for p in path.iterdir()}
    def forbidden(*args,**kwargs):pytest.fail('GET allocated or charged work')
    monkeypatch.setattr(TaskResourceBudget,'allocate',forbidden);monkeypatch.setattr(TaskResourceBudget,'consume',forbidden)
    result=view(s)
    assert result['status']=='NOT_ALLOCATED' and result['usage'] is None and result['metadata_writes']==0 and result['model_calls']==0
    assert not (s['data']/'task-resource-budgets').exists() and before=={p.name:p.read_bytes() for p in path.iterdir()}


def test_usage_read_preserves_exact_allocated_ledger_and_reports_actual_dimensions(experience,monkeypatch):
    s=experience;o=s['sessions'];manifest,_=o._manifest(s['pid'],s['sid']);operation=o.operation(s['pid'],s['sid'],s['scope']['root_operation_id'])
    budget=for_operation(o,manifest,operation);budget.allocate();budget.consume(source_bytes=7,source_reads=1,model_attempts=1,retained_output_bytes=9);before=budget.path.read_bytes()
    monkeypatch.setattr(TaskResourceBudget,'allocate',lambda *a,**k:pytest.fail('GET allocated work'))
    result=view(s)
    assert result['status']=='AVAILABLE' and result['usage']['used']=={'source_bytes':7,'source_reads':1,'model_attempts':1,'retained_output_bytes':9}
    assert budget.path.read_bytes()==before and result['usage']['usage_coverage']=='SINCE_ALLOCATION_NOT_RETROACTIVE'


def test_no_work_selection_never_silently_uses_current_active_head(experience):
    s=experience;s['sessions']._update(s['pid'],s['sid'],active_operation_id=s['scope']['root_operation_id'])
    result=view(s,operation=False)
    assert result['status']=='NO_WORK_SELECTED' and result['operation_id'] is None and result['usage'] is None
    assert not (s['data']/'task-resource-budgets').exists()


def test_foreign_owner_sha_is_rejected_without_allocating_or_returning_another_work(experience):
    with pytest.raises(Conflict):view(experience,sha='f'*64)
    assert not (experience['data']/'task-resource-budgets').exists()


def test_missing_allocated_state_is_unavailable_and_never_recreated(experience):
    s=experience;o=s['sessions'];manifest,_=o._manifest(s['pid'],s['sid']);b=for_operation(o,manifest,o.operation(s['pid'],s['sid'],s['scope']['root_operation_id']));b.allocate();marker=b.marker.read_bytes();b.path.unlink()
    result=view(s)
    assert result['status']=='UNAVAILABLE' and result['usage'] is None and not b.path.exists() and b.marker.read_bytes()==marker


def test_original_pending_output_attribution_stays_visible_and_read_only(retained):
    s=retained;append(s,info('msg_unattributed',parent='msg_unknown'));before=s['budget'].path.read_bytes();result=view(s)
    assert result['status']=='AVAILABLE' and result['retained_event_attribution']['pending_bytes']>0
    assert s['budget'].path.read_bytes()==before and result['metadata_writes']==0


def test_damaged_sql_attribution_is_unavailable_without_zero_fallback(experience,monkeypatch):
    def failed(*args):raise sqlite3.DatabaseError('UNIT original attribution damaged')
    monkeypatch.setattr(resources,'summary',failed);result=view(experience)
    assert result['status']=='UNAVAILABLE' and result['retained_event_attribution'] is None and result['usage'] is None
