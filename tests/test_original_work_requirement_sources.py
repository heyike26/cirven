"""AUTHORED_NOT_RUN: real SQL/files/HTTP; native execution is a UNIT boundary.

Explicit foreign data grants never qualify workspace access, native work,
measured test coverage or whole-task acceptance. No scenario was executed here.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import digest
from packages.agent_sessions.work_requirements import WorkRequirementLink
from packages.agent_sessions import work_requirement_sources as sources
from test_command_observations_routes import routed
from test_opencode_command_observation import command_scene
from test_original_work_requirements import work,bundle,ref,revise,inspect,packet,link
from test_session_diffs import app_for


def foreign(s,**options):
    pid=s['service'].store.create_project('Explicit source project','')['id']
    original={**s,'pid':pid}
    return original,bundle(original,**options)


def review_packet(s,original,b,**changes):
    return {**s['input'],'root_sha256':inspect(s)['root_sha256'],'source_project_id':original['pid'],
        'requirement_ref':ref(b),'requirement_ids':['R1'],'acceptance_ids':['A1'],**changes}


def review(s,p):return sources.review_work_requirement_source(s['service'],s['pid'],s['ident'],p)
def change(s,p):return sources.change_work_requirement_source(s['service'],s['pid'],s['ident'],p)
def allow(s,p,operation='allow_source'):
    observed=review(s,p)
    command={**p,'operation_id':operation,'expected_revision':observed['basis']['expected_revision'],
        'action':'ALLOW','review_sha256':observed['review_sha256']}
    return command,change(s,command)
def authority(r):return {'source_project_id':r['source_project_id'],'grant_id':r['grant_id'],
    'revision':r['revision'],'receipt_sha256':digest(r)}
def associate(s,b,r,operation='foreign_link'):
    return link(s,packet(s,b,operation,source_authority=authority(r)))
def revoke(s,p,r,operation='revoke_source'):
    return change(s,{**p,'operation_id':operation,'expected_revision':r['revision'],
        'action':'REVOKE','review_sha256':None})
def lookup(s,r):return sources.inspect_work_requirement_source_operation(s['service'],s['pid'],s['ident'],{
    **s['input'],'operation_id':r['operation_id'],'request_sha256':r['request_sha256']})


def test_foreign_bundle_still_cannot_be_linked_without_an_explicit_source_decision(work):
    s=work;original,b=foreign(s)
    with pytest.raises(NotFound):link(s,packet(s,b))
    assert inspect(s)['revision']==0


def test_explicit_catalog_and_preview_do_not_write_grants_or_touch_a_native_owner(work,monkeypatch):
    s=work;original,b=foreign(s);v=s['service']
    async def forbidden(*_args,**_kwargs):pytest.fail('Foreign requirement reads cannot dispatch a native owner')
    monkeypatch.setattr(v,'send',forbidden);monkeypatch.setattr(v,'inspect',forbidden);monkeypatch.setattr(v,'_http',forbidden)
    monkeypatch.setattr(v.workspaces,'get',lambda *_args:pytest.fail('No current workspace body reads'))
    value=sources.browse_work_requirement_sources(v,s['pid'],s['ident'],{**s['input'],
        'source_project_id':original['pid'],'selected_requirement_ref':ref(b)})
    assert value['selected']['payload']==b['payload'] and value['catalog']['entries'][0]['ref']==ref(b)
    assert value['grant_recorded'] is False and value['source_workspace_authorized'] is False
    with v.store.connect() as c:assert c.execute('SELECT count(*) FROM original_work_requirement_source_operations').fetchone()[0]==0


def test_review_binds_exact_selected_items_and_is_read_only(work):
    s=work;original,b=foreign(s);p=review_packet(s,original,b);r=review(s,p)
    assert r['review_sha256']==digest(r['basis']) and r['query_sha256']==digest(p)
    assert [row['id'] for row in r['selected']['requirements']]==['R1']
    assert [row['id'] for row in r['selected']['acceptance']]==['A1']
    assert r['basis']['expected_revision']==0 and r['permission_granted'] is False
    with s['service'].store.connect() as c:assert c.execute('SELECT count(*) FROM original_work_requirement_source_heads').fetchone()[0]==0


def test_allow_is_metadata_only_and_keeps_original_source_and_execution_project(work):
    s=work;original,b=foreign(s);p=review_packet(s,original,b);command,r=allow(s,p)
    assert r['request_sha256']==digest(sources.WorkRequirementSourceChange.model_validate(command).model_dump())
    assert r['source_project_id']==original['pid'] and r['project_id']==s['pid']
    assert r['state']=='ACTIVE' and r['source_workspace_authorized'] is False
    assert 'payload' not in r and 'selected' not in r and r['whole_task_acceptance']=='NOT_ASSESSED'
    assert lookup(s,r)==r and s['service'].store.requirements(original['pid'],b['id'])==b
    assert inspect(s)['revision']==0  # Allow is distinct from adopting the link.


def test_foreign_association_exposes_only_the_allowed_selection_in_the_original_root(work):
    s=work;original,b=foreign(s);_,grant=allow(s,review_packet(s,original,b));saved=associate(s,b,grant)
    current=inspect(s);assert current['links']==[saved] and current['impacts'][0]['review_status']=='CURRENT'
    assert [r['id'] for r in current['selected']['requirements']]==['R1']
    assert [r['id'] for r in current['selected']['acceptance']]==['A1']
    with pytest.raises(NotFound):inspect(s,selected_requirement_ref=ref(b),detail_kind='requirement',detail_id='R2')
    assert inspect(s,include_catalog=True)['catalog']['entries']==[]


def test_foreign_link_cannot_expand_granted_requirement_or_acceptance_ids(work):
    s=work;original,b=foreign(s);_,grant=allow(s,review_packet(s,original,b))
    with pytest.raises(Conflict):link(s,packet(s,b,source_authority=authority(grant),requirement_ids=['R1','R2']))
    with pytest.raises(Conflict):link(s,packet(s,b,source_authority=authority(grant),acceptance_ids=['A2']))
    with pytest.raises(Conflict):link(s,packet(s,b,source_authority={**authority(grant),'receipt_sha256':'0'*64}))


def test_grant_is_bound_to_exact_root_not_just_source_project_and_requirement_id(work):
    s=work;original,b=foreign(s);_,grant=allow(s,review_packet(s,original,b))
    from packages.agent_sessions import work_requirements as requirements
    with s['service'].store.connect() as c:
        candidate={**packet(s,b,source_authority=authority(grant)),'session_ref':s['ref']}
        with pytest.raises(Conflict):requirements._requirement_project(c,s['pid'],{**candidate,'root_operation_id':'other_root'})
        with pytest.raises(Conflict):requirements._requirement_project(c,s['pid'],{**candidate,'root_sha256':'0'*64})


def test_revoke_invalidates_active_link_without_rewriting_original_receipts(work):
    s=work;original,b=foreign(s);p=review_packet(s,original,b);_,grant=allow(s,p);saved=associate(s,b,grant)
    withdrawn=revoke(s,p,grant);current=inspect(s)
    assert withdrawn['state']=='REVOKED' and current['links']==[saved]
    assert current['impacts'][0]['review_status']=='UNAVAILABLE'
    assert current['selected'] is None and current['selected_issue']=='IMMUTABLE_REQUIREMENT_VIEW_UNAVAILABLE'
    assert lookup(s,grant)==grant and lookup(s,withdrawn)==withdrawn
    removed=link(s,packet(s,b,'remove_foreign',action='REMOVE',requirement_ids=[],acceptance_ids=[]))
    assert removed['action']=='REMOVE' and inspect(s)['links']==[]


def test_reallow_never_revives_old_grant_references_or_old_links(work):
    s=work;original,b=foreign(s);p=review_packet(s,original,b);_,old=allow(s,p);saved=associate(s,b,old)
    revoke(s,p,old);_,fresh=allow(s,p,'allow_again')
    assert fresh['revision']==3 and fresh['grant_id']==old['grant_id']
    assert inspect(s)['links']==[saved] and inspect(s)['impacts'][0]['review_status']=='UNAVAILABLE'
    newer=associate(s,b,fresh,'adopt_again')
    assert inspect(s)['links']==[newer] and inspect(s)['impacts'][0]['review_status']=='CURRENT'


def test_original_key_recovery_after_revocation_does_not_reactivate_a_grant(work):
    s=work;original,b=foreign(s);p=review_packet(s,original,b);command,grant=allow(s,p);withdrawn=revoke(s,p,grant)
    assert change(s,command)==grant and lookup(s,grant)==grant
    with s['service'].store.connect() as c:
        assert sources._head(c,s['pid'],s['ident'],s['input']['root_operation_id'],grant['grant_id'])==withdrawn
    with pytest.raises(Conflict):change(s,{**command,'acceptance_ids':[]})


def test_selected_change_needs_review_while_old_foreign_reference_is_preserved(work):
    s=work;original,b=foreign(s);_,grant=allow(s,review_packet(s,original,b));saved=associate(s,b,grant)
    fresh=revise(original,b,lambda p:p['requirements'][0].update(text='Changed selected requirement'))
    current=inspect(s);impact=current['impacts'][0]
    assert impact['review_status']=='NEEDS_REVIEW' and impact['changed_requirement_ids']==['R1']
    assert impact['current_ref']==ref(fresh) and current['links']==[saved]
    assert impact['automatic_adoption'] is False


def test_unselected_source_change_does_not_invalidate_unchanged_selected_items(work):
    s=work;original,b=foreign(s);_,grant=allow(s,review_packet(s,original,b));associate(s,b,grant)
    fresh=revise(original,b,lambda p:p['requirements'][1].update(text='Changed unselected requirement'))
    impact=inspect(s)['impacts'][0]
    assert impact['review_status']=='SELECTED_UNCHANGED_NEWER_BUNDLE' and impact['changed_requirement_ids']==[]
    assert impact['current_ref']==ref(fresh) and impact['requires_explicit_adoption'] is True
    with pytest.raises(Conflict):link(s,packet(s,fresh,'silent_upgrade',source_authority=authority(grant)))


def test_stale_original_document_blocks_review_and_propagates_selected_impact(work):
    s=work;original,b=foreign(s);store=s['service'].store
    doc=store.save_document(original['pid'],'source.txt',b'Original source\n','UNIT')
    payload=deepcopy(b['payload']);payload['requirements'][0]['source_refs']=[{
        'document_id':doc['id'],'revision':1,'sha256':doc['sha256'],'line_start':1,'line_end':1}]
    b=store.save_requirements(original['pid'],payload,b['id'],1);p=review_packet(s,original,b)
    _,grant=allow(s,p);associate(s,b,grant)
    store.save_document(original['pid'],'source.txt',b'Changed original source\n','UNIT',doc['id'],1)
    assert inspect(s)['impacts'][0]['stale_source_requirement_ids']==['R1']
    with pytest.raises(Conflict):review(s,p)


def test_source_unavailable_still_allows_exact_revocation_and_old_receipt_lookup(work,monkeypatch):
    s=work;original,b=foreign(s);p=review_packet(s,original,b);_,grant=allow(s,p)
    from packages.agent_sessions import work_requirements as requirements
    monkeypatch.setattr(requirements,'_bundle',lambda *_a,**_k:pytest.fail('Revocation/recovery must not reread source bodies'))
    withdrawn=revoke(s,p,grant);assert withdrawn['state']=='REVOKED' and lookup(s,grant)==grant


def test_two_reviewers_cannot_overwrite_same_grant_revision(work):
    s=work;original,b=foreign(s);p=review_packet(s,original,b);observed=review(s,p)
    commands=[{**p,'operation_id':name,'expected_revision':0,'action':'ALLOW',
        'review_sha256':observed['review_sha256']} for name in ['one','two']]
    def commit(value):
        try:return change(s,value)['operation_id']
        except Conflict:return 'CONFLICT'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(commit,commands))
    assert results.count('CONFLICT')==1


def test_changed_review_or_unconfirmed_source_cannot_be_allowed(work):
    s=work;original,b=foreign(s);p=review_packet(s,original,b);observed=review(s,p)
    with pytest.raises(Conflict):change(s,{**p,'operation_id':'bad_review','expected_revision':0,
        'action':'ALLOW','review_sha256':'0'*64})
    revise(original,b,lambda p:p.update(confirmed=False))
    with pytest.raises(Conflict):change(s,{**p,'operation_id':'stale_review','expected_revision':0,
        'action':'ALLOW','review_sha256':observed['review_sha256']})
    with pytest.raises(Conflict):review(s,{**p,'source_project_id':s['pid']})


def test_append_only_decisions_and_bounded_capacity_preserve_old_evidence(work,monkeypatch):
    s=work;original,b=foreign(s);p=review_packet(s,original,b);_,grant=allow(s,p)
    with s['service'].store.connect() as c:
        for statement in ['UPDATE original_work_requirement_source_operations SET revision=99',
                          'DELETE FROM original_work_requirement_source_operations']:
            with pytest.raises(sqlite3.IntegrityError):c.execute(statement)
    monkeypatch.setattr(sources,'MAX_OPERATIONS',1)  # Explicit tiny-capacity UNIT configuration.
    with pytest.raises(Conflict):revoke(s,p,grant)
    assert lookup(s,grant)==grant


def test_old_same_project_request_bytes_and_checksum_do_not_acquire_a_null_field(work):
    s=work;b=bundle(s);p=packet(s,b)
    assert WorkRequirementLink.model_validate(p).model_dump()==p
    assert WorkRequirementLink.model_validate({**p,'source_authority':None}).model_dump()==p
    saved=link(s,p);assert saved['request_sha256']==digest(p) and 'source_authority' not in saved


def test_strict_source_schemas_reject_execution_fields_and_duplicate_items(work):
    s=work;original,b=foreign(s);p=review_packet(s,original,b)
    for extra in [{'permission_granted':True},{'command':'rm file'},{'requirement_ids':['R1','R1']},
                  {'acceptance_ids':['A1','A1']},{'generation':True}]:
        with pytest.raises(ValidationError):sources.WorkRequirementSourceReview.model_validate({**p,**extra})
    with pytest.raises(ValidationError):sources.WorkRequirementSourceChange.model_validate({**p,
        'operation_id':'missing_review','action':'ALLOW','expected_revision':0})


def test_http_source_routes_are_metadata_only_and_recover_exact_original_key(work,monkeypatch):
    s=work;original,b=foreign(s);p=review_packet(s,original,b)
    async def forbidden(*_args,**_kwargs):pytest.fail('No native execution from source decisions')
    monkeypatch.setattr(s['service'],'send',forbidden);monkeypatch.setattr(s['service'],'open',forbidden)
    path='/api/projects/'+s['pid']+'/agent-sessions/'+s['ident']+'/work-requirement-sources'
    with TestClient(app_for(s['service'])) as client:
        assert client.post(path+'/catalog',json={**s['input'],'source_project_id':original['pid']}).status_code==200
        observed=client.post(path+'/review',json=p);assert observed.status_code==200,observed.text
        command={**p,'operation_id':'http_allow','expected_revision':0,'action':'ALLOW',
            'review_sha256':observed.json()['review_sha256']}
        assert client.post(path+'/decisions',json={**command,'start_task':True}).status_code==422
        saved=client.post(path+'/decisions',json=command);assert saved.status_code==200,saved.text
        q={**s['input'],'operation_id':command['operation_id'],'request_sha256':saved.json()['request_sha256']}
        assert client.post(path+'/operations/inspect',json=q).json()==saved.json()


def test_authorized_selection_can_be_reviewed_before_any_link_without_reading_unselected_items(work):
    s=work;original,b=foreign(s);_,grant=allow(s,review_packet(s,original,b))
    current=inspect(s,selected_requirement_ref=ref(b),selected_source_authority=authority(grant))
    assert current['links']==[] and current['revision']==0
    assert current['selected']['source_authority']==authority(grant)
    assert [r['id'] for r in current['selected']['requirements']]==['R1']
    assert [r['id'] for r in current['selected']['acceptance']]==['A1']
    with pytest.raises(NotFound):inspect(s,selected_requirement_ref=ref(b),
        selected_source_authority=authority(grant),detail_kind='requirement',detail_id='R2')


def test_explicit_prelink_view_fails_closed_after_revocation_or_wrong_authority(work):
    s=work;original,b=foreign(s);p=review_packet(s,original,b);_,grant=allow(s,p)
    with pytest.raises(Conflict):inspect(s,selected_requirement_ref=ref(b),
        selected_source_authority={**authority(grant),'receipt_sha256':'0'*64})
    revoke(s,p,grant)
    with pytest.raises(Conflict):inspect(s,selected_requirement_ref=ref(b),selected_source_authority=authority(grant))


def test_legacy_inspection_query_hash_omits_the_optional_source_authority(work):
    from packages.agent_sessions.work_requirements import WorkRequirementInspect
    s=work;b=bundle(s)
    request={**s['input'],'include_catalog':False,'catalog_offset':0,'selected_requirement_ref':ref(b),
        'detail_kind':None,'detail_id':None}
    parsed=WorkRequirementInspect.model_validate(request)
    assert parsed.model_dump()==request and inspect(s,**{k:v for k,v in request.items() if k not in s['input']})['query_sha256']==digest(request)
    assert WorkRequirementInspect.model_validate({**request,'selected_source_authority':None}).model_dump()==request
    with pytest.raises(ValidationError):WorkRequirementInspect.model_validate({**request,
        'selected_requirement_ref':None,'selected_source_authority':{'source_project_id':'other',
        'grant_id':'1'*64,'revision':1,'receipt_sha256':'2'*64}})


def test_review_reports_exact_current_grant_without_silently_renewing_it(work):
    s=work;original,b=foreign(s);p=review_packet(s,original,b);_,grant=allow(s,p)
    current=review(s,p)
    assert current['current_grant']==grant and current['basis']['expected_revision']==grant['revision']
    with s['service'].store.connect() as c:assert c.execute('SELECT count(*) FROM original_work_requirement_source_operations').fetchone()[0]==1
