"""AUTHORED_NOT_RUN. Real metadata Store/043/Threads with UNIT owner I/O only."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from apps.api.conversation_owner_selection import ConversationOwnerSelection,OwnerPreparation,SelectOriginalOwner
from packages.domain.conversation_threads import ConversationThreads,source_identity
from packages.domain.store import Store,Conflict,NotFound
from packages.domain.tasks import canonical,digest
from test_canonical_provider_generation import metadata_service


@pytest.fixture
def owners(metadata_service,tmp_path):
    assert not str(tmp_path.resolve()).startswith('/mnt/d/')
    h=metadata_service;c=h.service;s=c.sessions;store=Store(tmp_path/'owners.sqlite3')
    pid=store.create_project('Owner project','UNIT metadata selection')['id'];cid=h.scope['conversation_ref']['id']
    async def initial():
        for key in ['existing_a','existing_b']:
            await s.open(pid,{'operation_id':key,'conversation_ref':h.scope['conversation_ref'],'provider_profile_ref':h.profile,'workspace_ref':None})
    asyncio.run(initial());h.calls.update(open=0,send=0,stop=0)
    source_rows=deepcopy(h.scope['sources']);threads=ConversationThreads(store)
    with store.connect() as db:
        db.execute('INSERT INTO conversation_threads (conversation_id,initial_kind,project_id,created_at,deleted_at) VALUES (?,?,?,?,?)',(cid,'original',None,'UNIT',None))
        db.execute('INSERT INTO conversation_organization VALUES (?,?,?,?)',(cid,1,0,'UNIT'))
        alias=None
        for item in source_rows:
            source={**item['original_source'],'generation':0,'provider_profile_ref':h.profile,'workspace_ref':None};alias,identity=source_identity(source)
            db.execute('INSERT INTO conversation_source_aliases VALUES (?,?,?,?,?,?,?)',(alias,cid,'original',canonical(identity),canonical(source),digest(source),'UNIT'))
        db.execute('INSERT INTO conversation_native_routes VALUES (?,?,?,?,?)',(cid,'',None,'metadata_route',alias))
    original=s._manifest
    s._manifest=lambda pid,sid:(original(pid,sid)[0],deepcopy(h.views[sid]['session_ref']))
    c.threads=threads;c.owner_selection=ConversationOwnerSelection(c)
    h.store=store;h.cid=cid;h.choices=[c._source(v) for v in h.views.values()];h.selection=c.owner_selection
    return h


def request(h,index=0,key='select_owner',expected=None):
    scope=h.service.threads.view(h.cid)
    return {'operation_id':key,'conversation_ref':scope['conversation_ref'],'project_id':scope['project_id'],
        'original_source':deepcopy(h.choices[index]),'expected_selection_operation_id':expected}


def test_preparation_does_not_choose_start_load_stop_or_send(owners):
    h=owners;p=h.selection.prepare({'conversation_ref':h.scope['conversation_ref'],'project_id':None})
    assert p['selection_allowed'] and len(p['candidates'])==2 and p['current_owner']=={'status':'LEGACY'}
    assert p['read_only'] and not p['owner_started'] and not p['permission_granted'] and p['model_calls']==0
    assert h.calls=={'open':0,'send':0,'stop':0}


def test_exact_shared_selection_changes_member_without_new_owner(owners):
    h=owners;r=request(h,1);result=asyncio.run(h.selection.select(r));scope=h.service.threads.view(h.cid)
    assert result['selection_saved'] and result['selection_applied'] and result['request_sha256']==digest(r)
    assert h.service.current_owner(scope)['status']=='SELECTED'
    assert h.service._member(scope)['session_ref']==h.choices[1]['session_ref']
    assert h.calls=={'open':0,'send':0,'stop':0}


def test_repeated_selection_and_receipt_are_read_only_after_move(owners):
    h=owners;r=request(h);asyncio.run(h.selection.select(r));pid=h.store.create_project('Moved','UNIT')['id']
    with h.store.connect() as db:h.service.threads.move(db,h.cid,pid,updated_at='UNIT_MOVED')
    again=asyncio.run(h.selection.select(r));assert again==h.selection.operation(h.cid,r['operation_id'])
    scope=h.service.threads.view(h.cid);assert scope['project_id']==pid
    assert h.service.current_owner(scope)['original_source']==h.choices[0]
    assert h.calls['open']==h.calls['send']==h.calls['stop']==0


def test_competing_first_choices_are_cas_bound(owners):
    h=owners;a=request(h,0,'first');b=request(h,1,'competing')
    async def run():return await asyncio.gather(h.selection.select(a),h.selection.select(b),return_exceptions=True)
    rows=asyncio.run(run());assert sum(isinstance(r,Conflict) for r in rows)==1
    with h.store.connect() as db:assert db.execute('SELECT count(*) FROM conversation_owner_selections').fetchone()[0]==1


def test_new_explicit_choice_retains_historical_original_receipt(owners):
    h=owners;a=request(h,0,'first');asyncio.run(h.selection.select(a));b=request(h,1,'second','first');asyncio.run(h.selection.select(b))
    historical=h.selection.operation(h.cid,'first');assert historical['selection_saved'] and not historical['selection_applied']
    assert h.selection.operation(h.cid,'second')['selection_applied']
    with h.store.connect() as db:assert db.execute('SELECT count(*) FROM conversation_owner_selections').fetchone()[0]==2


@pytest.mark.parametrize('field,value',[('project_id','foreign'),('expected_selection_operation_id','other'),('generation',9)])
def test_changed_organization_expected_choice_or_generation_cannot_commit(owners,field,value):
    h=owners;r=request(h)
    if field=='generation':r['original_source']['generation']=value
    else:r[field]=value
    with pytest.raises(Conflict):asyncio.run(h.selection.select(r))
    with h.store.connect() as db:assert db.execute('SELECT count(*) FROM conversation_owner_selections').fetchone()[0]==0


def test_foreign_or_private_original_cannot_be_selected(owners):
    h=owners;r=request(h);r['original_source']['session_ref']['id']='unknown_owner'
    with pytest.raises(Conflict):asyncio.run(h.selection.select(r))
    r=request(h);original=h.service.sessions._manifest
    h.service.sessions._manifest=lambda *a:({**original(*a)[0],'native_message_admission':{'private':True}},original(*a)[1])
    with pytest.raises(Conflict):asyncio.run(h.selection.select(r))


def test_configured_or_pending_head_cannot_be_replaced_with_legacy_choice(owners,monkeypatch):
    h=owners;monkeypatch.setattr(h.service,'_owner_head',lambda cid:{'phase':'PREPARING'})
    with pytest.raises(Conflict,match='configured or pending'):asyncio.run(h.selection.select(request(h)))
    assert h.calls['open']==h.calls['stop']==0


def test_selected_source_unavailable_requires_another_explicit_choice(owners):
    h=owners;asyncio.run(h.selection.select(request(h)));sid=h.choices[0]['session_ref']['id'];h.views[sid]['generation']=0
    scope=h.service.threads.view(h.cid);assert h.service.current_owner(scope)=={'status':'SELECTION_REQUIRED','selection_operation_id':'select_owner'}
    with pytest.raises(Conflict):h.service._member(scope)
    asyncio.run(h.selection.select(request(h,1,'replacement','select_owner')))
    assert h.service.current_owner(scope)['original_source']==h.choices[1]


def test_selection_commit_is_atomic_when_pointer_update_is_rejected(owners):
    h=owners
    with h.store.connect() as db:db.execute("CREATE TRIGGER reject_owner_pointer BEFORE INSERT ON conversation_selected_owners BEGIN SELECT RAISE(ABORT,'UNIT pointer failed'); END")
    with pytest.raises(Exception,match='UNIT pointer failed'):asyncio.run(h.selection.select(request(h)))
    with h.store.connect() as db:
        assert db.execute('SELECT count(*) FROM conversation_owner_selections').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM conversation_selected_owners').fetchone()[0]==0


def test_same_key_changed_body_is_rejected_and_original_receipt_survives(owners):
    h=owners;r=request(h);asyncio.run(h.selection.select(r));changed=request(h,1)
    with pytest.raises(Conflict,match='IDEMPOTENCY_CONFLICT'):asyncio.run(h.selection.select(changed))
    assert h.selection.operation(h.cid,r['operation_id'])['original_source']==r['original_source']


def test_selection_request_has_no_permission_model_message_or_workspace_fields():
    assert len(OwnerPreparation.model_json_schema()['properties'])==2
    assert len(SelectOriginalOwner.model_json_schema()['properties'])==5
    for field in ['raw_text','allowed_effects','workspace_ref','provider_profile_ref','permission_granted']:
        with pytest.raises(ValidationError):SelectOriginalOwner.model_validate({field:True})
