"""AUTHORED_NOT_RUN: actual event writers, SQL/ledger gaps and original IDs.

Native clients/processes/tools and Windows are not exercised. Adapters below are
allocated without constructors so only event serialization/storage is invoked.
"""
import asyncio
import json
import pytest
from packages.adapters.claude_sdk import ClaudeBridge
from packages.adapters.opencode import OpenCodeBridge
from packages.adapters import task_resources as module
from packages.domain import task_resource_budget as budget_module
from packages.domain.task_resource_budget import TaskResourceBudget,task_resource_scope
from packages.domain.store import Conflict
from test_task_resource_budget import resource
from test_execution_resource_links import record


@pytest.fixture
def output_resource(resource):
    s=resource;authority={**s['authority'],'root_operation_id':'output-work'};limits={**s['limits'],'retained_output_bytes':2048}
    budget=TaskResourceBudget(s['ws'],authority,limits=limits);budget.allocate()
    return {**s,'budget':budget,'authority':authority,'limits':limits}


def owner(s,kind='opencode'):
    cls=ClaudeBridge if kind=='claude' else OpenCodeBridge
    value=object.__new__(cls);value.service=s['store'];value.data_dir=s['ws'].data_dir;value.workspace_authority=s['ws'];value.active={};value._route_secrets=set()
    return value


def created(s,original):
    with task_resource_scope(s['budget']),s['store'].connect() as db:
        value=record(s,db);module.bind_created_execution(original,db,value['row'])
    return value


def events(s,ident):
    with s['store'].connect() as db:return [r[0] for r in db.execute('SELECT payload FROM agent_events WHERE execution_id=? ORDER BY event_seq',(ident,))]


@pytest.mark.parametrize('kind',['opencode','claude'])
def test_late_output_counts_exact_saved_bytes_by_original_record_even_under_another_work(output_resource,kind):
    s=output_resource;o=owner(s,kind);r=created(s,o);other=TaskResourceBudget(s['ws'],{**s['authority'],'root_operation_id':'other-work'},limits=s['limits']);other.allocate()
    with s['store'].connect() as db:db.execute("UPDATE agent_executions SET execution_status='RETURNED' WHERE id=?",(r['row']['id'],))
    with task_resource_scope(other):o._event(r['row']['id'],{'type':'late_original','text':'保留原消息 😀'})
    raw=events(s,r['row']['id']);assert len(raw)==1 and s['budget'].inspect()['used']['retained_output_bytes']==len(raw[0].encode())
    assert other.inspect()['used']['retained_output_bytes']==0
    with s['store'].connect() as db:assert db.execute('SELECT count(*) FROM agent_execution_resource_gaps').fetchone()[0]==0


def test_claude_redaction_and_numeric_diagnostic_are_both_counted_as_actually_saved(output_resource):
    s=output_resource;o=owner(s,'claude');o._route_secrets={'UNIT_PRIVATE_SECRET'};r=created(s,o)
    o._event(r['row']['id'],{'type':'native_result','text':'UNIT_PRIVATE_SECRET','cost':float('nan')})
    raw=events(s,r['row']['id']);assert len(raw)==2 and 'UNIT_PRIVATE_SECRET' not in ''.join(raw)
    assert json.loads(raw[1])['type']=='sdk_numeric_invalid' and s['budget'].inspect()['used']['retained_output_bytes']==sum(len(v.encode()) for v in raw)


@pytest.mark.parametrize('kind',['opencode','claude'])
def test_counter_save_failure_preserves_event_and_sql_gap_seals_later_generation(output_resource,monkeypatch,kind):
    s=output_resource;o=owner(s,kind);r=created(s,o);live=module.resolve(o,s['authority']['project_id'],r['row'],{})
    o.active[r['row']['id']]={'task_resource_budget':live};original=TaskResourceBudget.consume
    def fail_output(self,**units):
        if units.get('retained_output_bytes'):raise Conflict('UNIT output accounting save failed')
        return original(self,**units)
    monkeypatch.setattr(TaskResourceBudget,'consume',fail_output)
    o._event(r['row']['id'],{'type':'received_before_failure','text':'不能丢失'})
    raw=events(s,r['row']['id']);assert len(raw)==1 and live.accounting_unconfirmed and s['budget'].inspect()['accounting_unconfirmed']
    with s['store'].connect() as db:
        gap=dict(db.execute('SELECT * FROM agent_execution_resource_gaps').fetchone());assert gap['resource_id']==s['budget'].id
    with pytest.raises(Conflict):
        with module.record_scope(o,s['authority']['project_id'],r['row']):pytest.fail('Generation admitted after unknown accounting')
    before=s['budget'].inspect()['used']['model_attempts']
    with pytest.raises(Conflict):s['budget'].consume(model_attempts=1)
    assert s['budget'].inspect()['used']['model_attempts']==before and events(s,r['row']['id'])==raw


def test_broken_resource_link_still_retains_original_event_without_adopting_current_work(output_resource):
    s=output_resource;o=owner(s);r=created(s,o)
    with s['store'].connect() as db:db.execute('UPDATE agent_execution_resources SET resource_json=? WHERE execution_id=?',('{broken original link',r['row']['id']))
    before=s['budget'].path.read_bytes();o._event(r['row']['id'],{'type':'unattributed_original','text':'保留原件'})
    assert len(events(s,r['row']['id']))==1 and s['budget'].path.read_bytes()==before
    with s['store'].connect() as db:
        gap=dict(db.execute('SELECT * FROM agent_execution_resource_gaps').fetchone());assert gap['resource_id'] is None and gap['execution_id']==r['row']['id']
        assert db.execute('SELECT resource_json FROM agent_execution_resources').fetchone()[0]=='{broken original link'
    with pytest.raises(Conflict):module.resolve(o,s['authority']['project_id'],r['row'],{})


def test_gap_from_one_execution_blocks_another_root_linked_to_same_work_without_touching_usage(output_resource):
    s=output_resource;o=owner(s);one=created(s,o);two=created(s,o)
    with s['store'].connect() as db:db.execute('INSERT INTO agent_execution_resource_gaps VALUES (?,?,?,?,?)',(one['row']['id'],s['authority']['project_id'],s['budget'].id,'UNIT_UNKNOWN','UNIT'))
    before=s['budget'].path.read_bytes()
    with pytest.raises(Conflict):
        with module.record_scope(o,s['authority']['project_id'],two['row']):pytest.fail('Shared work gap ignored')
    with pytest.raises(Conflict):s['budget'].consume(model_attempts=1)
    assert s['budget'].path.read_bytes()==before


def test_full_gap_index_keeps_new_original_events_and_globally_refuses_generation(output_resource,monkeypatch):
    s=output_resource;o=owner(s);one=created(s,o);two=created(s,o)
    monkeypatch.setattr(module,'MAX_OUTPUT_GAPS',1);monkeypatch.setattr(budget_module,'MAX_OUTPUT_GAPS',1)
    with s['store'].connect() as db:
        db.execute('INSERT INTO agent_execution_resource_gaps VALUES (?,?,?,?,?)',(one['row']['id'],s['authority']['project_id'],None,'UNIT_INDEX_FULL','UNIT'))
        db.execute('UPDATE agent_execution_resources SET resource_json=? WHERE execution_id=?',('{broken',two['row']['id']))
    o._event(two['row']['id'],{'type':'retained_at_capacity','text':'保留旧记录与新事件'})
    assert len(events(s,two['row']['id']))==1
    with s['store'].connect() as db:assert db.execute('SELECT count(*) FROM agent_execution_resource_gaps').fetchone()[0]==1
    with pytest.raises(Conflict):s['budget'].consume(model_attempts=1)


def test_sdk_existing_terminal_and_cancelled_records_remain_observable_without_client_or_reallocation(output_resource):
    s=output_resource;o=owner(s);r=created(s,o);calls=[]
    async def observe(self,pid,ident,client,request):calls.append(ident);return self.inspect(pid,ident)
    def inspect(pid,ident):
        with s['store'].connect() as db:return dict(db.execute('SELECT * FROM agent_executions WHERE id=? AND project_id=?',(ident,pid)).fetchone())
    o.inspect=inspect;wrapped=module.sdk_scope(observe)
    for status,cancelled in [('UNKNOWN_OUTCOME',0),('QUEUED',1)]:
        with s['store'].connect() as db:db.execute('UPDATE agent_executions SET execution_status=?,cancel_requested=? WHERE id=?',(status,cancelled,r['row']['id']))
        before=s['budget'].path.read_bytes();result=asyncio.run(wrapped(o,s['authority']['project_id'],r['row']['id'],None,{}))
        assert result['execution_status']==status and s['budget'].path.read_bytes()==before
    assert len(calls)==2


def test_sdk_gap_refuses_before_the_client_owner_method_can_start(output_resource):
    s=output_resource;o=owner(s);r=created(s,o);calls=[]
    def inspect(pid,ident):
        with s['store'].connect() as db:return dict(db.execute('SELECT * FROM agent_executions WHERE id=?',(ident,)).fetchone())
    o.inspect=inspect
    async def generate(self,pid,ident,client,request):calls.append('unexpected');return None
    with s['store'].connect() as db:db.execute('INSERT INTO agent_execution_resource_gaps VALUES (?,?,?,?,?)',(r['row']['id'],s['authority']['project_id'],s['budget'].id,'UNIT_UNKNOWN','UNIT'))
    with pytest.raises(Conflict):asyncio.run(module.sdk_scope(generate)(o,s['authority']['project_id'],r['row']['id'],None,{}))
    assert not calls
