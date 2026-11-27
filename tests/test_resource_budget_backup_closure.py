"""AUTHORED_NOT_RUN: real data inventory/copy and budget/SQL preservation.

Exercises maintenance subcomponents in private fixtures only when run. Does not
qualify native ACL, full backup/restore publication, startup or an old program.
"""
from pathlib import Path
import pytest
from scripts import native_user_state as recovery
from packages.domain.store import Store
from packages.domain.local_workspaces import LocalWorkspaces
from packages.domain.task_resource_budget import TaskResourceBudget
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.tasks import digest
from packages.agent_sessions.task_resources import for_operation
from test_experience_candidates import experience


@pytest.fixture
def budget_data(experience):
    s=experience;o=s['sessions'];manifest,_=o._manifest(s['pid'],s['sid']);b=for_operation(o,manifest,o.operation(s['pid'],s['sid'],s['scope']['root_operation_id']));b.allocate();b.consume(model_attempts=1);b.reserve_read(32);b.retain_received(17)
    NativeCapabilities._write(b.gap,{'resource_id':b.id,'created_at':'UNIT','error_type':'UNIT_UNKNOWN_ACCOUNTING','original_evidence_retained':True})
    o._update(s['pid'],s['sid'],last_event_seq=0,resource_retention_unconfirmed=True)
    o._event(s['pid'],s['sid'],'ORIGINAL_EVENT',{'generation':1,'native_payload':{'type':'message.updated','properties':{'info':{'id':'msg_unattributed','role':'assistant','sessionID':'ses_original','parentID':'msg_unknown'}}}})
    manual=s['data']/'manual-draft.txt';manual.write_text('保留人工稿\n😀\n')
    return {**s,'budget':b,'manual':manual}


def copied(s,name):
    target=s['data'].parent/name;target.mkdir();rows,total=recovery._inventory(s['data']);recovery._copy(s['data'],target,rows)
    return target,rows,total


def test_native_inventory_copy_preserves_ledger_gap_unknown_reads_original_events_and_sql_attribution(budget_data):
    s=budget_data;b=s['budget'];target,rows,total=copied(s,'restored-private-state')
    for path in [b.path,b.marker,b.lock_path,b.gap,s['manual']]:
        name=path.relative_to(s['data']).as_posix();assert name in rows and (target/name).read_bytes()==path.read_bytes()
    store=Store(target/'store.sqlite3');fresh=TaskResourceBudget(LocalWorkspaces(store,target),b.authority,limits=b.limits)
    assert fresh.inspect()['resource_ref']==b.inspect()['resource_ref']
    assert fresh.inspect()['pending_reads']==1 and fresh.inspect()['pending_read_bytes']==32 and fresh.inspect()['used']['model_attempts']==1 and fresh.inspect()['accounting_unconfirmed']
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM original_resource_messages WHERE pending_bytes>0').fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM schema_migrations WHERE version='052_original_resource_retention.sql'").fetchone()[0]==1
    assert (target/'manual-draft.txt').read_text()=='保留人工稿\n😀\n' and total>0


def test_applied_and_rejected_allowance_receipts_remain_exact_after_independent_copy(budget_data):
    s=budget_data;b=s['budget'];ref=b.inspect()['resource_ref'];larger={**b.limits,'source_bytes':b.limits['source_bytes']+64};applied=b.adjust('explicit_addition','a'*64,ref,larger)
    rejected=b.adjust('stale_snapshot','b'*64,ref,{**larger,'model_attempts':256})
    assert applied['status']=='APPLIED' and rejected['status']=='REJECTED'
    target,_,_=copied(s,'restored-allowance-history');store=Store(target/'store.sqlite3');fresh=TaskResourceBudget(LocalWorkspaces(store,target),b.authority,limits=b.limits)
    assert fresh.adjustment('explicit_addition')['adjustment_ref']==applied['adjustment_ref']
    assert fresh.adjustment('stale_snapshot')['adjustment_ref']==rejected['adjustment_ref']
    assert fresh.inspect()['limits']==larger and fresh.inspect()['accounting_unconfirmed']


@pytest.mark.parametrize('policy',recovery.CACHE_EXCLUSION_POLICIES)
def test_resource_ledgers_and_unknown_receipts_are_never_classified_as_regenerable_cache(policy):
    prefix='task-resource-budgets/'+'a'*64+'/'
    for name in ['state.json','allocation.json','writer.lock','accounting-unconfirmed.json','future-proof-recovery.original.json']:
        assert not recovery._excluded(prefix+name,cache_exclusion_policy=policy)
    assert not recovery._excluded('agent-sessions/'+'b'*36+'/'+'c'*32+'/subscription-http-budget.json',cache_exclusion_policy=policy)
