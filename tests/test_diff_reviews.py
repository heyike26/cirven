"""AUTHORED_NOT_RUN: actual Store/files/plugin, injected original tool boundary.

No native model/CLI/IDE qualification. Review records never change file bytes.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

from fastapi.testclient import TestClient
import pytest

from apps.api.agent_collaboration import register_agent_collaboration_routes
from packages.agent_sessions.diff_reviews import DiffReviews, DiffPreparation, DiffDecision
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from packages.agent_sessions.child_observations import ChildObservations, child_reference
from test_session_full_diffs import coding, Producer, saved
from test_session_diffs import app_for


def capture(scene):
    producer=Producer(scene)
    try:
        producer.finish()
        query=saved(scene,[producer.message()])
        directory=scene['value'].diffs(scene['pid'],scene['ident'],**query)
        row=next(row for row in directory['files'] if row['content_scope']=='FULL_FILE')
        return DiffPreparation(session_ref=scene['ref'],generation=1,workspace_ref=scene['workspace'],
            snapshot_ref=directory['snapshot_ref'],diff_ref=row['diff_ref'])
    finally:producer.finish(False)


def decision(selection,prepared,operation='review_one',choice='ACCEPT'):
    return DiffDecision(**selection.model_dump(),operation_id=operation,decision=choice,
        preparation_sha256=prepared['preparation_sha256'],expected_review_revision=prepared['review_revision'])


def test_exact_review_is_metadata_only_and_has_revision_cas(coding):
    s=coding;selection=capture(s);reviews=DiffReviews(s['value']);path=s['folder']/'a.py';actual=path.read_bytes()
    p=asyncio.run(reviews.prepare(s['pid'],s['ident'],selection))
    assert p['matches_after'] and not p['matches_before'] and p['editor_barrier']=='NOT_ACQUIRED'
    body=decision(selection,p);r=asyncio.run(reviews.decide(s['pid'],s['ident'],body))
    assert r['status']=='RETURNED' and r['review_ref']['revision']==1 and r['workspace_writes']==0
    assert r['review_decision']=='ACCEPT' and path.read_bytes()==actual
    assert asyncio.run(reviews.decide(s['pid'],s['ident'],body))==r
    with pytest.raises(Conflict):asyncio.run(reviews.decide(s['pid'],s['ident'],decision(selection,p,'stale_review','REJECT')))
    p2=asyncio.run(reviews.prepare(s['pid'],s['ident'],selection));assert p2['review']['decision']=='ACCEPT'
    r2=asyncio.run(reviews.decide(s['pid'],s['ident'],decision(selection,p2,'second_review','REJECT')))
    assert r2['review_ref']['revision']==2 and r2['review_decision']=='REJECT' and path.read_bytes()==actual
    records=list(s['value']._path(s['pid'],s['ident'],'diff-reviews').iterdir());assert len(records)==2


def test_saved_manual_content_invalidates_preparation_and_is_never_restored_by_a_review(coding):
    s=coding;selection=capture(s);reviews=DiffReviews(s['value']);p=asyncio.run(reviews.prepare(s['pid'],s['ident'],selection))
    path=s['folder']/'a.py';path.write_bytes(b'USER_MANUAL_SAVE\r\n')
    with pytest.raises(Conflict):asyncio.run(reviews.decide(s['pid'],s['ident'],decision(selection,p)))
    current=asyncio.run(reviews.prepare(s['pid'],s['ident'],selection));assert not current['matches_after'] and not current['matches_before']
    result=asyncio.run(reviews.decide(s['pid'],s['ident'],decision(selection,current,'review_manual_basis','REJECT')))
    assert result['workspace_writes']==0 and path.read_bytes()==b'USER_MANUAL_SAVE\r\n'


def test_lost_review_receipt_recovers_only_its_immutable_metadata_even_after_a_newer_decision(coding,monkeypatch):
    s=coding;selection=capture(s);service=s['value'];reviews=DiffReviews(service);p=asyncio.run(reviews.prepare(s['pid'],s['ident'],selection))
    original=service._write;failed=False
    def write(path,value,**kwargs):
        nonlocal failed
        if path.parent.name=='diff-reviews' and not failed:
            # The installed legacy writer persisted a second envelope.
            value={'value':value,'sha256':digest(value)}
        if path.parent.name=='operations' and value.get('status')=='RETURNED' and not failed:
            failed=True;raise RuntimeError('UNIT interrupted receipt publication after exact review record')
        return original(path,value,**kwargs)
    monkeypatch.setattr(service,'_write',write)
    with pytest.raises(RuntimeError):asyncio.run(reviews.decide(s['pid'],s['ident'],decision(selection,p)))
    assert service.operation(s['pid'],s['ident'],'review_one')['status']=='UNKNOWN_OUTCOME'
    historical=service._path(s['pid'],s['ident'],'diff-reviews',digest('review_one')+'.json')
    saved=historical.read_bytes()
    p2=asyncio.run(reviews.prepare(s['pid'],s['ident'],selection))
    asyncio.run(reviews.decide(s['pid'],s['ident'],decision(selection,p2,'newer_review','REJECT')))
    old=reviews.operation(s['pid'],s['ident'],'review_one')
    assert old['status']=='RETURNED' and old['review_decision']=='ACCEPT' and old['review_ref']['revision']==1
    assert old['recovered_review_metadata'] is True and historical.read_bytes()==saved
    assert reviews.operation(s['pid'],s['ident'],'review_one')==old
    assert (s['folder']/'a.py').read_bytes()==b'keep\nnew\ntail\n'
    assert len(list(service._path(s['pid'],s['ident'],'diff-reviews').iterdir()))==2


def test_review_asgi_preserves_exact_snapshot_and_original_operation_lookup(coding):
    s=coding;selection=capture(s);app=app_for(s['value']);register_agent_collaboration_routes(app,service=s['value'])
    route=f"/api/projects/{s['pid']}/agent-sessions/{s['ident']}/diff-reviews"
    with TestClient(app) as client:
        p=client.post(route+'/prepare',json=selection.model_dump());assert p.status_code==200,p.text
        body=decision(selection,p.json()).model_dump();r=client.post(route,json=body);assert r.status_code==200,r.text
        original=client.get(route+'/review_one');assert original.status_code==200 and original.json()==r.json()
        next_preparation=client.post(route+'/prepare',json=selection.model_dump())
        assert next_preparation.status_code==200 and next_preparation.json()['review_revision']==1
        next_body=decision(selection,next_preparation.json(),'second_api_decision','REJECT').model_dump()
        second=client.post(route,json=next_body)
        assert second.status_code==200 and second.json()['review_ref']['revision']==2
        assert client.get(route+'/review_one').json()==r.json()
        wrong={**selection.model_dump(),'snapshot_ref':{**selection.snapshot_ref.model_dump(),'sha256':'f'*64}}
        assert client.post(route+'/prepare',json=wrong).status_code==409
        assert client.post(route,json={**body,'decision':'REJECT'}).status_code==409
    assert (s['folder']/'a.py').read_bytes()==b'keep\nnew\ntail\n'


def test_child_review_keeps_exact_original_child_provenance_and_cannot_borrow_root_snapshot(coding):
    s=coding;service=s['value'];producer=Producer(s,native_session='ses_child')
    try:
        producer.finish();message=producer.message();child={'id':'ses_child','parentID':'ses_root','directory':str(s['folder']),'title':'Review child'}
        service._update(s['pid'],s['ident'],children=[child],original_session={'id':'ses_root','directory':str(s['folder'])},last_event_seq=0)
        for event in [{'type':'message.updated','properties':{'info':message['info']}},
                {'type':'message.part.updated','properties':{'part':message['parts'][0]}}]:
            service._event(s['pid'],s['ident'],'ORIGINAL_EVENT',{'native_payload':event,'native_payload_sha256':digest(event),'generation':1})
        child_ref=child_reference(service._state(s['pid'],s['ident']),child)
        directory,_=asyncio.run(ChildObservations(service).diffs(s['pid'],s['ident'],s['ref'],1,child_ref,s['workspace']))
        selection=DiffPreparation(session_ref=s['ref'],generation=1,workspace_ref=s['workspace'],snapshot_ref=directory['snapshot_ref'],diff_ref=directory['files'][0]['diff_ref'],child_ref=child_ref)
        reviews=DiffReviews(service);p=asyncio.run(reviews.prepare(s['pid'],s['ident'],selection))
        assert p['diff']['source_native_session_id']=='ses_child' and p['child_ref']==child_ref
        result=asyncio.run(reviews.decide(s['pid'],s['ident'],decision(selection,p)))
        assert result['review_selection']['child_ref']==child_ref and result['workspace_writes']==0
        with pytest.raises(Conflict):asyncio.run(reviews.prepare(s['pid'],s['ident'],DiffPreparation.model_validate({**selection.model_dump(),'child_ref':None})))
    finally:producer.finish(False)


@pytest.mark.parametrize('damage',['outer_hash','legacy_inner_hash','third_envelope','foreign_project','foreign_session'])
def test_invalid_review_cannot_be_adopted_by_current_session(coding,damage):
    s=coding;reviews=DiffReviews(s['value']);pid=s['pid'];sid=s['ident'];selection=capture(s)
    p=asyncio.run(reviews.prepare(pid,sid,selection));body=decision(selection,p)
    asyncio.run(reviews.decide(pid,sid,body))
    path=s['value']._path(pid,sid,'diff-reviews',digest(body.operation_id)+'.json')
    initial=(s['folder']/'a.py').read_bytes()
    envelope=json.loads(path.read_bytes())
    assert envelope['value']['schema']=='eeas.diff-review.v1'
    def wrap(value):return {'value':value,'sha256':digest(value)}
    if damage=='outer_hash':
        envelope['sha256']='0'*64
    elif damage=='legacy_inner_hash':
        envelope['sha256']='0'*64
        envelope=wrap(envelope)
    elif damage=='third_envelope':
        envelope=wrap(wrap(envelope))
    else:
        record=envelope['value']
        if damage=='foreign_project':
            record['key']=reviews._key('other_project',selection)
        else:
            record['selection']['session_ref']['id']='f'*32
        envelope=wrap(record)
    path.write_text(json.dumps(envelope));persisted=path.read_bytes()
    with pytest.raises(Conflict):
        asyncio.run(reviews.prepare(pid,sid,selection))
    assert path.read_bytes()==persisted and (s['folder']/'a.py').read_bytes()==initial


def test_concurrent_review_decisions_commit_one_revision_and_retry_original_result(coding):
    s=coding;reviews=DiffReviews(s['value']);pid=s['pid'];sid=s['ident'];selection=capture(s)
    prepared=asyncio.run(reviews.prepare(pid,sid,selection))
    bodies=[decision(selection,prepared,'left','ACCEPT'),decision(selection,prepared,'right','REJECT')]
    gate=Barrier(2)
    def submit(body):
        gate.wait(timeout=10)
        try:return asyncio.run(reviews.decide(pid,sid,body))
        except Conflict:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(submit,bodies))
    winner,=[(body,result) for body,result in zip(bodies,results) if result is not None]
    loser,=[body for body,result in zip(bodies,results) if result is None]
    body,result=winner
    assert result['review_ref']['revision']==1 and result['workspace_writes']==0
    assert asyncio.run(reviews.decide(pid,sid,body))==result
    assert s['value'].operation(pid,sid,loser.operation_id)['status']=='FAILED'
    assert len(list(s['value']._path(pid,sid,'diff-reviews').iterdir()))==1
    assert (s['folder']/'a.py').read_bytes()==b'keep\nnew\ntail\n'
