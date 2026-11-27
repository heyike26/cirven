"""Use the real observer, original file refs and signed records after a private relocation."""
import asyncio
import json
import shutil

import pytest

from packages.agent_sessions.diff_reviews import DiffReviews, DiffPreparation
from packages.agent_sessions.child_observations import ChildObservations, child_reference
from packages.agent_sessions import AgentSessions
from packages.domain.local_workspaces import LocalWorkspaces
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Conflict, Store
from packages.domain.tasks import digest
from test_session_full_diffs import coding, Producer
from test_diff_reviews import capture, decision


@pytest.fixture
def relocated(coding, request):
    s = coding
    if getattr(request, 'param', 'root') == 'child':
        svc = s['value']
        producer = Producer(s, native_session='ses_child')
        try:
            producer.finish()
            message = producer.message()
            child = {'id':'ses_child', 'parentID':'ses_root', 'directory':str(s['folder']), 'title':'Child review'}
            svc._update(s['pid'], s['ident'], children=[child], original_session={'id':'ses_root', 'directory':str(s['folder'])}, last_event_seq=0)
            for event in ({'type':'message.updated','properties':{'info':message['info']}},
                          {'type':'message.part.updated','properties':{'part':message['parts'][0]}}):
                svc._event(s['pid'],s['ident'],'ORIGINAL_EVENT',{'native_payload':event,'native_payload_sha256':digest(event),'generation':1})
            ref=child_reference(svc._state(s['pid'],s['ident']),child)
            cat,_=asyncio.run(ChildObservations(svc).diffs(s['pid'],s['ident'],s['ref'],1,ref,s['workspace']))
            selection=DiffPreparation(session_ref=s['ref'],generation=1,workspace_ref=s['workspace'],snapshot_ref=cat['snapshot_ref'],diff_ref=cat['files'][0]['diff_ref'],child_ref=ref)
        finally:
            producer.finish(False)
    else:
        selection=capture(s)
    data = s['value'].data_dir
    service = s['value']
    review = DiffReviews(service)
    preparation = asyncio.run(review.prepare(s['pid'], s['ident'], selection))
    ack = asyncio.run(review.decide(s['pid'], s['ident'], decision(selection, preparation)))
    restored = s['tmp'] / 'after private restore'
    shutil.copytree(data, restored)
    data.rename(s['tmp'] / 'unavailable prior data')
    marker = {'schema_version':'eeas.restore-provenance.v1', 'target_data_dir':str(restored),
              'allowed_source_data_dirs':[str(data)], 'snapshot_sha256':'a'*64, 'manifest_sha256':'a'*64}
    (restored/'restore-provenance.json').write_text(json.dumps(marker))
    old_record=s['tmp']/'unavailable prior data'/service._path(s['pid'],s['ident'],'diff-reviews',digest('review_one')+'.json').relative_to(data)
    store=Store(restored/'eeas.sqlite3')
    owner=AgentSessions(store,restored,provider_registry=service.providers,workspaces=LocalWorkspaces(store,restored),capabilities=NativeCapabilities(store,restored))
    return s,selection,DiffReviews(owner),ack,marker,restored,old_record.read_bytes()


@pytest.mark.parametrize('relocated',['root','child'], indirect=True)
def test_immutable_prior_review_and_next_revision_after_relocation(relocated):
    s,sel,review,ack,_,restored,raw=relocated
    pid,sid=s['pid'],s['ident']
    assert review.operation(pid,sid,'review_one')==ack
    before=asyncio.run(review.prepare(pid,sid,sel))
    assert before['review_revision']==1 and before['matches_after']
    result=asyncio.run(review.decide(pid,sid,decision(sel,before,'review_two','REJECT')))
    assert result['review_ref']['revision']==2 and result['workspace_writes']==0
    assert review.operation(pid,sid,'review_one')==ack
    assert review.owner._path(pid,sid,'diff-reviews',digest('review_one')+'.json').read_bytes()==raw
    assert (s['folder']/'a.py').read_bytes()==b'keep\nnew\ntail\n'


@pytest.mark.parametrize('damage',['missing','foreign_target','foreign_source','wrong_digest','copied_config','copied_ledger_link'])
def test_relocated_review_rejects_mismatched_proof_and_never_reads_old_scope(relocated,damage):
    s,sel,review,ack,marker,restored,_=relocated
    pid,sid=s['pid'],s['ident']
    proof=restored/'restore-provenance.json'
    config=review.owner._path(pid,sid,'runtime','change-review','generation-1','scope.json')
    if damage=='missing':proof.unlink()
    elif damage=='foreign_target':marker['target_data_dir']=str(s['tmp']/'other')
    elif damage=='foreign_source':marker['allowed_source_data_dirs']=[str(s['tmp']/'other')]
    elif damage=='wrong_digest':marker['manifest_sha256']='b'*64
    elif damage=='copied_config':config.write_bytes(config.read_bytes()+b' ')
    else:
        ledger=config.parent/'ledger'
        archived=config.parent/'retained';ledger.rename(archived);ledger.symlink_to(archived,target_is_directory=True)
    if damage in ('foreign_target','foreign_source','wrong_digest'):proof.write_text(json.dumps(marker))
    with pytest.raises(Conflict):
        asyncio.run(review.prepare(pid,sid,sel))
    assert review.operation(pid,sid,'review_one')==ack
    assert (s['folder']/'a.py').read_bytes()==b'keep\nnew\ntail\n'


def test_relocated_session_cannot_adopt_replacement_workspace_with_same_saved_file(relocated):
    scene, selection, review, ack, _, _, record_bytes = relocated
    project, session = scene['pid'], scene['ident']
    original = scene['folder']
    prior_file = (original/'a.py').read_bytes()
    original.rename(scene['tmp']/'original workspace retained')
    original.mkdir()
    (original/'a.py').write_bytes(prior_file)
    with pytest.raises(Conflict):
        asyncio.run(review.prepare(project,session,selection))
    assert review.operation(project,session,'review_one') == ack
    record = review.owner._path(project,session,'diff-reviews',digest('review_one')+'.json')
    assert record.read_bytes() == record_bytes and (original/'a.py').read_bytes() == prior_file
