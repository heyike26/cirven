"""AUTHORED_NOT_RUN: real execution SQL/ledger identity and transactional links.

No product App, SDK, model, CLI process, tool, browser/native or full backup
qualification. Fixture executions represent server records only.
"""
from datetime import datetime,timedelta,timezone
from contextlib import contextmanager
from types import SimpleNamespace
import sqlite3
import uuid
import pytest
from packages.adapters.task_resources import bind_created_execution,resolve
from packages.domain.task_resource_budget import TaskResourceBudget,task_resource_scope
from packages.domain.tasks import canonical,digest,now
from packages.domain.store import Conflict
from test_task_resource_budget import resource


def adapter(s):return SimpleNamespace(service=s['store'],data_dir=s['ws'].data_dir,workspace_authority=s['ws'])


def record(s,db,*,parent=None,key=None,request=None,scope=None,runtime='UNIT_NATIVE',workspace=None,status='QUEUED'):
    ident=key or str(uuid.uuid4());pid=s['authority']['project_id']
    request={'prompt':'UNIT frozen execution '+ident} if request is None else request
    scope={'UNIT_SOURCE':'frozen','input_hash':digest(request)} if scope is None else scope
    root=parent['root'] if parent else ident
    db.execute("INSERT INTO agent_executions(id,project_id,operation_id,request_hash,runtime,workspace,execution_status,verification_status,model_profile,sdk_version,request,created_at,cancel_requested) VALUES (?,?,?,?,?,?,?,'NOT_RUN','UNIT','UNIT',?,?,0)",
        (ident,pid,'operation_'+ident,digest(request),runtime,str(workspace if workspace is not None else s['tmp']/'workspace'/ident),status,canonical(request),now()))
    db.execute('INSERT INTO agent_scopes VALUES (?,?,?,?,?,?,?,?,?,?)',(ident,canonical(scope),digest(scope),(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat(),root,None,'0',None,None,ident))
    row=dict(db.execute('SELECT * FROM agent_executions WHERE id=?',(ident,)).fetchone())
    return {'row':row,'scope':scope,'root':root}


def test_new_execution_keeps_exact_creator_work_when_later_called_without_context(resource):
    s=resource;a=adapter(s);b=s['budget'];before=b.path.read_bytes()
    with task_resource_scope(b),s['store'].connect() as db:
        db.execute('BEGIN IMMEDIATE');created=record(s,db);bind_created_execution(a,db,created['row'])
    restored=resolve(a,s['authority']['project_id'],created['row'],{})
    assert restored.id==b.id and restored.authority==b.authority and restored.limits==b.limits
    assert restored.inspect()['used']==b.inspect()['used'] and b.path.read_bytes()==before
    with s['store'].connect() as db:assert db.execute('SELECT count(*) FROM agent_execution_resources').fetchone()[0]==1


def test_same_project_and_data_directory_do_not_allow_an_unrelated_context_to_borrow_execution(resource):
    s=resource;a=adapter(s)
    with s['store'].connect() as db:
        db.execute('BEGIN IMMEDIATE');created=record(s,db);own=bind_created_execution(a,db,created['row'])
    assert own.id!=s['budget'].id
    with task_resource_scope(s['budget']):
        with pytest.raises(Conflict):resolve(a,s['authority']['project_id'],created['row'],{})
    assert not own.path.exists(), 'Identity lookup does not allocate or reset a ledger'


def test_sql_creation_rollback_leaves_no_resource_link_or_new_allowance(resource):
    s=resource;a=adapter(s);before=s['budget'].path.read_bytes();ident=str(uuid.uuid4())
    with pytest.raises(RuntimeError):
        with task_resource_scope(s['budget']),s['store'].connect() as db:
            db.execute('BEGIN IMMEDIATE');created=record(s,db,key=ident);bind_created_execution(a,db,created['row']);raise RuntimeError('UNIT original creation rollback')
    with s['store'].connect() as db:
        assert db.execute('SELECT count(*) FROM agent_execution_resources').fetchone()[0]==0
        assert db.execute('SELECT 1 FROM agent_executions WHERE id=?',(ident,)).fetchone() is None
    assert s['budget'].path.read_bytes()==before


def test_child_created_in_another_callback_keeps_the_original_linked_root_without_context(resource):
    s=resource;a=adapter(s);b=s['budget']
    with task_resource_scope(b),s['store'].connect() as db:
        db.execute('BEGIN IMMEDIATE');root=record(s,db);bind_created_execution(a,db,root['row'])
    with s['store'].connect() as db:
        db.execute('BEGIN IMMEDIATE');child=record(s,db,parent=root);bind_created_execution(a,db,child['row'])
    assert resolve(a,s['authority']['project_id'],child['row'],{}).id==b.id
    with task_resource_scope(b):assert resolve(a,s['authority']['project_id'],child['row'],{}) is b


def test_legacy_unlinked_root_is_readonly_and_cannot_adopt_the_callers_current_work(resource):
    s=resource;a=adapter(s)
    with s['store'].connect() as db:root=record(s,db);child=record(s,db,parent=root)
    one=resolve(a,s['authority']['project_id'],root['row'],{});two=resolve(a,s['authority']['project_id'],child['row'],{})
    assert one.id==two.id and one.authority['root_execution_id']==root['row']['id'] and not one.path.exists()
    with task_resource_scope(s['budget']):
        with pytest.raises(Conflict):resolve(a,s['authority']['project_id'],child['row'],{})
    with s['store'].connect() as db:assert db.execute('SELECT count(*) FROM agent_execution_resources').fetchone()[0]==0


@pytest.mark.parametrize('fault',['request_hash','scope_hash','root_execution_id','resource_id','foreign_authority','child_resource'])
def test_corrupt_or_divergent_link_or_scope_is_refused_without_rebinding_or_erasing_originals(resource,fault):
    s=resource;a=adapter(s);pid=s['authority']['project_id']
    with task_resource_scope(s['budget']),s['store'].connect() as db:
        root=record(s,db);bind_created_execution(a,db,root['row']);child=record(s,db,parent=root);bind_created_execution(a,db,child['row'])
    with s['store'].connect() as db:
        ident=child['row']['id'];link=dict(db.execute('SELECT * FROM agent_execution_resources WHERE execution_id=?',(ident,)).fetchone())
        if fault in {'request_hash','scope_hash','root_execution_id'}:
            column={'request_hash':'request_sha256','scope_hash':'scope_sha256','root_execution_id':'root_execution_id'}[fault];value=ident if fault=='root_execution_id' else 'f'*64
            db.execute('UPDATE agent_execution_resources SET '+column+'=? WHERE execution_id=?',(value,ident))
        else:
            import json
            value=json.loads(link['resource_json'])
            if fault=='resource_id':value['resource_id']='f'*64
            elif fault=='foreign_authority':value['authority']['project_id']='foreign'
            else:
                other=TaskResourceBudget(s['ws'],{**s['authority'],'root_operation_id':'other'},limits=s['limits']);value={'resource_id':other.id,'authority':other.authority,'limits':other.limits}
            db.execute('UPDATE agent_execution_resources SET resource_json=? WHERE execution_id=?',(canonical(value),ident))
        damaged=dict(db.execute('SELECT * FROM agent_execution_resources WHERE execution_id=?',(ident,)).fetchone())
    with pytest.raises(Conflict):resolve(a,pid,child['row'],{})
    with s['store'].connect() as db:assert dict(db.execute('SELECT * FROM agent_execution_resources WHERE execution_id=?',(ident,)).fetchone())==damaged


def test_exhausted_link_capacity_rolls_back_only_new_execution_and_keeps_existing_link(resource,monkeypatch):
    from packages.adapters import task_resources as module
    s=resource;a=adapter(s)
    with s['store'].connect() as db:old=record(s,db);bind_created_execution(a,db,old['row'])
    monkeypatch.setattr(module,'MAX_BINDINGS',1)
    with pytest.raises(Conflict):
        with s['store'].connect() as db:
            db.execute('BEGIN IMMEDIATE');new=record(s,db);bind_created_execution(a,db,new['row'])
    with s['store'].connect() as db:
        assert db.execute('SELECT count(*) FROM agent_execution_resources').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM agent_executions').fetchone()[0]==1
    assert resolve(a,s['authority']['project_id'],old['row'],{}).authority['root_execution_id']==old['row']['id']


def test_child_creation_in_unrelated_work_is_refused_with_original_lineage_preserved(resource):
    s=resource;a=adapter(s)
    with task_resource_scope(s['budget']),s['store'].connect() as db:
        root=record(s,db);bind_created_execution(a,db,root['row'])
    other=TaskResourceBudget(s['ws'],{**s['authority'],'root_operation_id':'unrelated'},limits=s['limits'])
    with pytest.raises(Conflict):
        with task_resource_scope(other),s['store'].connect() as db:
            child=record(s,db,parent=root);bind_created_execution(a,db,child['row'])
    with s['store'].connect() as db:
        assert db.execute('SELECT count(*) FROM agent_execution_resources').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM agent_executions').fetchone()[0]==1
    assert resolve(a,s['authority']['project_id'],root['row'],{}).id==s['budget'].id and not other.path.exists()


@pytest.mark.parametrize('fault',['project','data_directory'])
def test_new_root_cannot_capture_another_project_or_application_allowance(resource,fault):
    from packages.domain.local_workspaces import LocalWorkspaces
    s=resource;a=adapter(s);ws=s['ws'] if fault=='project' else LocalWorkspaces(s['store'],s['tmp']/'other-private')
    authority={**s['authority'],'project_id':'foreign'} if fault=='project' else s['authority']
    other=TaskResourceBudget(ws,authority,limits=s['limits'])
    with pytest.raises(Conflict):
        with task_resource_scope(other),s['store'].connect() as db:
            root=record(s,db);bind_created_execution(a,db,root['row'])
    with s['store'].connect() as db:
        assert db.execute('SELECT count(*) FROM agent_execution_resources').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM agent_executions').fetchone()[0]==0
    assert not other.path.exists()


@pytest.mark.parametrize('fault',['actual_scope','request','malformed_resource','malformed_limits'])
def test_unreadable_or_changed_original_metadata_never_gets_a_new_resource_binding(resource,fault,monkeypatch):
    s=resource;a=adapter(s);pid=s['authority']['project_id']
    with s['store'].connect() as db:root=record(s,db);bind_created_execution(a,db,root['row'])
    with s['store'].connect() as db:
        ident=root['row']['id']
        if fault in {'actual_scope','request'}:
            # Current SQL protects these immutable columns. Verify that barrier,
            # then inject only a corrupted read below to exercise the separate
            # hash validator without removing any production/test DB trigger.
            query=('UPDATE agent_scopes SET scope=? WHERE execution_id=?' if fault=='actual_scope'
                   else 'UPDATE agent_executions SET request=? WHERE id=?')
            with pytest.raises(sqlite3.IntegrityError):db.execute(query,('{"changed":true}',ident))
        elif fault=='malformed_resource':db.execute('UPDATE agent_execution_resources SET resource_json=? WHERE execution_id=?',('{corrupt original',ident))
        else:db.execute('UPDATE agent_execution_resources SET resource_json=? WHERE execution_id=?',(canonical({'resource_id':s['budget'].id,'authority':s['authority'],'limits':False}),ident))
        before=dict(db.execute('SELECT * FROM agent_execution_resources WHERE execution_id=?',(ident,)).fetchone())
    original_connect=s['store'].connect
    @contextmanager
    def corrupt_read():
        with original_connect() as db:
            def execute(query,args=()):
                cursor=db.execute(query,args)
                table='agent_scopes' if fault=='actual_scope' else 'agent_executions'
                if query.startswith('SELECT * FROM '+table+' WHERE '):
                    row=dict(cursor.fetchone());row['scope' if fault=='actual_scope' else 'request']='{"changed":true}'
                    return SimpleNamespace(fetchone=lambda:row)
                return cursor
            yield SimpleNamespace(execute=execute)
    with monkeypatch.context() as patch:
        if fault in {'actual_scope','request'}:patch.setattr(s['store'],'connect',corrupt_read)
        with pytest.raises(Conflict):resolve(a,pid,root['row'],{})
    with s['store'].connect() as db:assert dict(db.execute('SELECT * FROM agent_execution_resources WHERE execution_id=?',(ident,)).fetchone())==before
