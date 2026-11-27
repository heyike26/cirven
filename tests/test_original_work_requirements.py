"""AUTHORED_NOT_RUN: real SQL/files/HTTP; original execution is a UNIT boundary.

The saved root receipt is explicitly authored metadata, not a real OpenCode or
model run. No case here qualifies native Windows, report coverage or a task.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from packages.agent_sessions import AgentSessions
from packages.agent_sessions.work_requirements import (WorkRequirementInspect,WorkRequirementLink,
    inspect_work_requirements,link_work_requirements,inspect_work_requirement_operation)
from packages.domain.requirements import RequirementBundle
from packages.domain.store import Conflict,InvalidInput,NotFound
from packages.domain.tasks import digest
from test_command_observations_routes import routed
from test_opencode_command_observation import command_scene


@pytest.fixture
def work(routed):
    s=routed;v=s['service'];pid=s['pid'];sid=s['ident'];root='root_work'
    v._write(v._path(pid,sid,'state.json'),{'generation':1,'original_session_id':'ses_command',
        'messages':[],'owner_status':'DISCONNECTED','native_status':'UNKNOWN'},replace=True)
    request={'operation_id':root,'session_ref':s['ref'],'generation':1,'raw_text':'Keep manual edits and fix the shipping fee','agent':'build'}
    v._claim(pid,sid,root,'MESSAGE',request)
    v._finish(pid,sid,root,'UNKNOWN_OUTCOME',generation=1,original_session_id='ses_command',native_message_id='msg_root')
    s['input']={'session_ref':s['ref'],'generation':1,'workspace_ref':s['workspace_ref'],'root_operation_id':root}
    return s


def bundle(s,key='main',*,confirmed=True,origin='manual',source=None):
    payload=RequirementBundle(schema_version='eeas.requirements.v1',title='Shipping '+key,
        requirements=[{'id':'R1','text':'Charge each started block','source_refs':[source] if source else []},
                      {'id':'R2','text':'Preserve the existing interface','source_refs':[]}],
        acceptance=[{'id':'A1','text':'1001g is 700 cents','requirement_ids':['R1']},
                    {'id':'A2','text':'Existing callers retain their interface','requirement_ids':['R2']}],
        confirmed=confirmed,origin=origin).model_dump()
    return s['service'].store.save_requirements(s['pid'],payload)


def ref(value):return {k:value[k] for k in ('id','revision','sha256')}
def inspect(s,**changes):return inspect_work_requirements(s['service'],s['pid'],s['ident'],{**s['input'],**changes})
def packet(s,b,operation='link_one',**changes):
    current=inspect(s)
    return {**s['input'],'operation_id':operation,'root_sha256':current['root_sha256'],
        'expected_revision':current['revision'],'action':'ASSOCIATE','requirement_ref':ref(b),
        'requirement_ids':['R1'],'acceptance_ids':['A1'],**changes}
def link(s,p):return link_work_requirements(s['service'],s['pid'],s['ident'],p)
def revise(s,b,edit):
    payload=deepcopy(b['payload']);edit(payload)
    return s['service'].store.save_requirements(s['pid'],payload,b['id'],b['revision'])


def test_inspection_is_saved_metadata_without_owner_models_current_workspace_or_task_changes(work,monkeypatch):
    s=work;v=s['service'];b=bundle(s);path=v._path(s['pid'],s['ident'],'operations',digest('root_work')+'.json');before=path.read_bytes()
    async def no_dispatch(*_args,**_kwargs):pytest.fail('Requirement metadata must not contact a native owner')
    monkeypatch.setattr(v,'inspect',no_dispatch);monkeypatch.setattr(v,'send',no_dispatch);monkeypatch.setattr(v,'_http',no_dispatch)
    monkeypatch.setattr(v.workspaces,'get',lambda *_args:pytest.fail('No current workspace is needed'))
    r=inspect(s,selected_requirement_ref=ref(b));assert r['root_status']=='UNKNOWN_OUTCOME'
    assert r['selected']['title']==b['payload']['title'] and r['whole_task_acceptance']=='NOT_ASSESSED'
    assert r['current_workspace_verified'] is False and path.read_bytes()==before


def test_human_requirement_association_is_metadata_on_the_original_root_not_a_mock_run(work):
    s=work;b=bundle(s);p=packet(s,b);r=link(s,p);current=inspect(s)
    assert r['request_sha256']==digest(WorkRequirementLink.model_validate(p).model_dump())
    assert r['commands_started']==r['model_calls']==r['source_files_written']==0
    assert r['permission_granted'] is False and r['original_task_status_changed'] is False
    assert current['links']==[r] and current['impacts'][0]['review_status']=='CURRENT'
    assert s['service'].operation(s['pid'],s['ident'],'root_work')['status']=='UNKNOWN_OUTCOME'


def test_original_link_key_replays_only_identical_input_and_does_not_append_again(work):
    s=work;b=bundle(s);p=packet(s,b);first=link(s,p)
    assert link(s,p)==first
    with pytest.raises(Conflict):link(s,{**p,'acceptance_ids':[]})
    assert inspect(s)['revision']==1


def test_two_writers_cannot_overwrite_each_others_reviewed_root_revision(work):
    s=work;b=bundle(s);one=packet(s,b,'one');two={**one,'operation_id':'two','requirement_ids':['R2'],'acceptance_ids':['A2']}
    def save(value):
        try:return link(s,value)['operation_id']
        except Conflict:return 'CONFLICT'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,[one,two]))
    assert results.count('CONFLICT')==1 and inspect(s)['revision']==1


def test_changed_selected_requirement_keeps_original_evidence_ref_and_requires_review(work):
    s=work;b=bundle(s);saved=link(s,packet(s,b))
    newer=revise(s,b,lambda p:p['requirements'][0].update(text='Charge whole kilograms instead'))
    r=inspect(s);impact=r['impacts'][0]
    assert impact['review_status']=='NEEDS_REVIEW' and impact['changed_requirement_ids']==['R1']
    assert impact['current_ref']==ref(newer) and r['links'][0]==saved
    assert impact['automatic_adoption'] is False and impact['requires_explicit_adoption'] is True


def test_only_unselected_requirement_change_is_identified_without_promoting_old_binding(work):
    s=work;b=bundle(s);old=link(s,packet(s,b));newer=revise(s,b,lambda p:p['requirements'][1].update(text='Add an optional caller'))
    impact=inspect(s)['impacts'][0]
    assert impact['review_status']=='SELECTED_UNCHANGED_NEWER_BUNDLE'
    assert impact['changed_requirement_ids']==[] and impact['current_ref']==ref(newer)
    assert impact['requirement_ref']==ref(b) and inspect(s)['links'][0]==old


def test_selected_acceptance_edit_and_deleted_requirement_are_not_silently_revalidated(work):
    s=work;b=bundle(s);link(s,packet(s,b))
    current=revise(s,b,lambda p:p['acceptance'][0].update(text='1001g is 900 cents'))
    assert inspect(s)['impacts'][0]['changed_acceptance_ids']==['A1']
    def remove(p):
        p['requirements']=[p['requirements'][1]];p['acceptance']=[p['acceptance'][1]]
    revise(s,current,remove)
    assert inspect(s)['impacts'][0]['changed_requirement_ids']==['R1']


def test_changed_selected_source_marks_review_even_if_requirement_version_is_unchanged(work):
    s=work;store=s['service'].store;doc=store.save_document(s['pid'],'source.txt',b'Original requirement\n','UNIT')
    source={'document_id':doc['id'],'revision':doc['revision'],'sha256':doc['sha256'],'line_start':1,'line_end':1}
    b=bundle(s,source=source);link(s,packet(s,b));store.save_document(s['pid'],'source.txt',b'Changed requirement\n','UNIT',doc['id'],1)
    impact=inspect(s)['impacts'][0]
    assert impact['review_status']=='NEEDS_REVIEW' and impact['stale_source_requirement_ids']==['R1']
    assert impact['requirement_ref']==ref(b)


def test_stale_source_of_an_unselected_requirement_does_not_invalidate_selected_subset(work):
    s=work;store=s['service'].store;doc=store.save_document(s['pid'],'other.txt',b'Other requirement\n','UNIT')
    b=bundle(s);payload=deepcopy(b['payload']);payload['requirements'][1]['source_refs']=[{'document_id':doc['id'],'revision':1,'sha256':doc['sha256'],'line_start':1,'line_end':1}]
    b=store.save_requirements(s['pid'],payload,b['id'],1);link(s,packet(s,b));store.save_document(s['pid'],'other.txt',b'Changed other requirement\n','UNIT',doc['id'],1)
    assert store.requirements(s['pid'],b['id'])['source_status']=='STALE'
    assert inspect(s)['impacts'][0]['review_status']=='CURRENT'


def test_unconfirmed_foreign_and_noncurrent_bundles_rejected_while_confirmed_old_proposals_remain_usable(work):
    s=work
    for confirmed,origin in [(False,'manual'),(False,'mock')]:
        b=bundle(s,confirmed=confirmed,origin=origin)
        with pytest.raises(Conflict):link(s,packet(s,b,'invalid_'+origin+str(confirmed)))
    old_proposal=bundle(s,confirmed=True,origin='mock');saved=link(s,packet(s,old_proposal,'reviewed_old_proposal'))
    assert saved['whole_task_acceptance']=='NOT_ASSESSED' and inspect(s)['selected']['origin']=='mock'
    old=bundle(s);revise(s,old,lambda p:p.update(title='New title'))
    with pytest.raises(Conflict):link(s,packet(s,old,'old_version'))
    other=s['service'].store.create_project('Foreign','')['id'];other_s={**s,'pid':other};foreign=bundle(other_s)
    with pytest.raises(NotFound):link(s,packet(s,foreign,'foreign'))


def test_acceptance_link_requires_all_of_its_named_requirements(work):
    s=work;b=bundle(s)
    with pytest.raises(Conflict):link(s,packet(s,b,acceptance_ids=['A2']))
    with pytest.raises(Conflict):link(s,packet(s,b,requirement_ids=['MISSING']))
    assert inspect(s)['revision']==0


def test_remove_is_cas_metadata_and_original_association_receipt_remains_immutable(work):
    s=work;b=bundle(s);p=packet(s,b);old=link(s,p)
    removal=packet(s,b,'remove',action='REMOVE',requirement_ids=[],acceptance_ids=[]);r=link(s,removal)
    assert r['revision']==2 and inspect(s)['links']==[]
    query={**s['input'],'operation_id':p['operation_id'],'request_sha256':old['request_sha256']}
    assert inspect_work_requirement_operation(s['service'],s['pid'],s['ident'],query)==old
    assert link(s,p)==old and inspect(s)['links']==[]


def test_saved_root_basis_survives_newer_observation_generation_without_relabeling(work):
    s=work;b=bundle(s);old_view=inspect(s);old=link(s,packet(s,b));state=s['service']._state(s['pid'],s['ident']);state['generation']=2
    s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    s['input']['generation']=2;new_view=inspect(s)
    assert new_view['root_sha256']==old_view['root_sha256'] and new_view['root_basis']['root_generation']==1
    assert new_view['scope']['generation']==2 and new_view['links']==[old]


def test_wrong_root_hash_and_wrong_generation_do_not_modify_any_link(work):
    s=work;b=bundle(s);p=packet(s,b)
    for change in [{'root_sha256':'f'*64},{'generation':2},{'session_ref':{**s['ref'],'sha256':'e'*64}}]:
        with pytest.raises(Conflict):link(s,{**p,**change})
    assert inspect(s)['revision']==0


def test_mutation_rechecks_original_scope_after_waiting_for_sql_metadata(work,monkeypatch):
    s=work;b=bundle(s);p=packet(s,b);real=s['service']._state;calls=0
    def changing(*args):
        nonlocal calls
        state=real(*args);calls+=1
        return {**state,'generation':2} if calls>1 else state
    monkeypatch.setattr(s['service'],'_state',changing)
    with pytest.raises(Conflict):link(s,p)
    with s['service'].store.connect() as c:assert c.execute('SELECT count(*) FROM original_work_requirement_operations').fetchone()[0]==0


def test_catalog_is_bounded_project_scoped_and_reports_original_noneligible_origins(work):
    s=work
    for i in range(10):bundle(s,str(i),confirmed=i!=0,origin='mock' if i==1 else 'manual')
    first=inspect(s)['catalog'];second=inspect(s,catalog_offset=8)['catalog']
    assert len(first['entries'])==8 and first['next_offset']==8 and len(second['entries'])==2
    all_rows=first['entries']+second['entries']
    assert {r['reason'] for r in all_rows}=={'READY','NOT_CONFIRMED'}
    assert any(r['origin']=='mock' and r['eligible'] for r in all_rows)


def test_original_operation_must_be_an_acknowledged_integrity_checked_message(work):
    s=work;v=s['service'];path=v._path(s['pid'],s['ident'],'operations',digest('root_work')+'.json');original=v.operation(s['pid'],s['ident'],'root_work')
    for change in [{'kind':'GUIDANCE'},{'native_message_id':None},{'request_sha256':'f'*64}]:
        v._write(path,{**original,**change},replace=True)
        with pytest.raises(Conflict):inspect(s)
    v._write(path,original,replace=True)


def test_storage_failure_rolls_back_operation_and_head_together(work):
    s=work;b=bundle(s);p=packet(s,b)
    with s['service'].store.connect() as c:c.execute("CREATE TRIGGER fail_work_head BEFORE INSERT ON original_work_requirement_heads BEGIN SELECT RAISE(ABORT,'UNIT full'); END")
    with pytest.raises(sqlite3.IntegrityError):link(s,p)
    with s['service'].store.connect() as c:assert c.execute('SELECT count(*) FROM original_work_requirement_operations').fetchone()[0]==0


def test_sql_receipts_are_append_only_and_capacity_never_discards_old_evidence(work,monkeypatch):
    import packages.agent_sessions.work_requirements as module
    s=work;b=bundle(s);p=packet(s,b);old=link(s,p)
    with s['service'].store.connect() as c:
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):c.execute('DELETE FROM original_work_requirement_operations')
    monkeypatch.setattr(module,'MAX_OPERATIONS',1)
    with pytest.raises(Conflict,match='capacity'):link(s,packet(s,b,'next'))
    assert inspect(s)['links']==[old] and link(s,p)==old


def test_one_root_can_reference_multiple_requirement_sets_without_a_second_task(work):
    s=work;a=bundle(s,'one');b=bundle(s,'two');one=link(s,packet(s,a,'one'));two=link(s,packet(s,b,'two'))
    r=inspect(s);assert len(r['links'])==2 and {link['operation_id'] for link in r['links']}=={'one','two'}
    assert one['root_sha256']==two['root_sha256'] and r['root_status']=='UNKNOWN_OUTCOME'


def test_full_long_item_is_read_on_demand_from_exact_immutable_requirement_version(work):
    s=work;b=bundle(s);b=revise(s,b,lambda p:p['requirements'][0].update(text='详细要求'*300))
    r=inspect(s,selected_requirement_ref=ref(b));assert r['selected']['requirements'][0]['text_truncated'] is True
    r=inspect(s,selected_requirement_ref=ref(b),detail_kind='requirement',detail_id='R1')
    assert r['selected']['detail']['text']==b['payload']['requirements'][0]['text']


def test_http_metadata_routes_reject_extra_execution_fields_and_support_original_receipt_read(work,monkeypatch):
    from fastapi.testclient import TestClient
    from test_session_diffs import app_for
    s=work;b=bundle(s);p=packet(s,b)
    async def no_execution(*_args,**_kwargs):pytest.fail('No original execution from requirements metadata')
    monkeypatch.setattr(s['service'],'open',no_execution);monkeypatch.setattr(s['service'],'send',no_execution)
    root='/api/projects/'+s['pid']+'/agent-sessions/'+s['ident']+'/work-requirements'
    with TestClient(app_for(s['service'])) as client:
        assert client.post(root+'/inspect',json=s['input']).status_code==200
        assert client.post(root+'/links',json={**p,'start_task':True}).status_code==422
        saved=client.post(root+'/links',json=p);assert saved.status_code==200,saved.text
        query={**s['input'],'operation_id':p['operation_id'],'request_sha256':saved.json()['request_sha256']}
        assert client.post(root+'/operations/inspect',json=query).json()==saved.json()


def test_unreadable_frozen_requirements_do_not_hide_original_link_receipts(work):
    s=work;b=bundle(s);saved=link(s,packet(s,b))
    # Explicit DB-corruption UNIT boundary: normal immutable writes are protected.
    with s['service'].store.connect() as connection:
        connection.execute('DROP TRIGGER requirement_versions_no_update')
        connection.execute('UPDATE requirement_versions SET payload=? WHERE requirement_id=?',('{bad',b['id']))
    r=inspect(s);assert r['links']==[saved] and r['impacts'][0]['review_status']=='UNAVAILABLE'
    assert r['selected'] is None and r['selected_issue']=='IMMUTABLE_REQUIREMENT_VIEW_UNAVAILABLE'
