"""Authored NOT_RUN: actual plugin/files/Store, injected SDK/tool boundaries.

No live model, native tool, Windows or VS Code qualification is claimed.
"""
import asyncio
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from packages.adapters.opencode_change_review import approve_guard,read_child_lineage,read_history
from packages.agent_sessions.child_observations import child_reference
from apps.api.agent_collaboration import register_agent_collaboration_routes
from fastapi.testclient import TestClient
from packages.domain.store import Conflict
from packages.domain.tasks import digest,canonical
from test_session_full_diffs import coding,Producer,native,observed
from test_session_diffs import app_for


def child_observed(scene,message):
    return {**observed(scene,[message]),'original_session_id':'ses_child','root_original_session_id':'ses_root',
        'active_native_session_id':'ses_root'}


def test_real_child_capture_approval_and_historical_text_keep_original_root_and_child_identities(coding):
    scene=coding;p=Producer(scene,native_session='ses_child')
    try:
        pending=native(scene,p);message=p.message('running');basis=child_observed(scene,message)
        approved=approve_guard(scene['admission'],basis,pending,message,workspaces=scene['value'].workspaces,binding=scene['binding'])
        assert approved['original_session_id']=='ses_child' and approved['root_native_session_id']=='ses_root'
        assert approved['atomic_write_guaranteed'] is False
        reference=read_child_lineage(scene['admission'],'ses_child',ancestor='ses_root')
        assert approved['child_lineage_ref']==reference
        p.finish();message=p.message();entries,reasons=read_history(scene['admission'],child_observed(scene,message),data_dir=scene['value'].data_dir)
        assert len(entries)==1 and entries[0]['source_native_session_id']=='ses_child'
        assert entries[0]['load_content']()['before']=='keep\nold\ntail\n'
        assert entries[0]['load_content']()['after']=='keep\nnew\ntail\n'
        root_entries,_=read_history(scene['admission'],observed(scene,[message]),data_dir=scene['value'].data_dir)
        assert root_entries==[]
    finally:p.finish(False)


@pytest.mark.parametrize('fault',['manual_save','lineage_hash','other_parent'])
def test_changed_child_proof_or_manual_file_save_refuses_positive_approval(coding,fault):
    scene=coding;p=Producer(scene,native_session='ses_child')
    try:
        pending=native(scene,p);message=p.message('running');basis=child_observed(scene,message)
        if fault=='manual_save':(scene['folder']/'a.py').write_text('keep\nhuman\ntail\n')
        elif fault=='other_parent':basis['active_native_session_id']='ses_sibling'
        else:
            reference=read_child_lineage(scene['admission'],'ses_child')
            path=Path(scene['admission']['scope']['ledger_root'])/'children'/(reference['id']+'.json')
            packet=json.loads(path.read_text());packet['value']['root_native_session_id']='ses_other';path.write_text(canonical(packet))
        with pytest.raises(Conflict):approve_guard(scene['admission'],basis,pending,message,workspaces=scene['value'].workspaces,binding=scene['binding'])
    finally:p.finish(False)


def test_pending_child_write_uses_actual_child_message_and_root_grant_before_one_permission_post(coding):
    scene=coding;p=Producer(scene,native_session='ses_child');service=scene['value'];pending=native(scene,p);message=p.message('running')
    async def run():
        calls=[]
        async def transport(request):
            calls.append((request.method,request.url.path))
            if request.method=='GET' and request.url.path=='/permission':return httpx.Response(200,json=[pending])
            if request.method=='GET' and request.url.path=='/session/ses_child':return httpx.Response(200,json={'id':'ses_child','parentID':'ses_root','directory':str(scene['folder'])})
            if request.method=='GET' and request.url.path=='/session/ses_child/message/msg_one':return httpx.Response(200,json=message)
            if request.method=='POST' and request.url.path=='/permission/per_one/reply':return httpx.Response(200,json=True)
            return httpx.Response(404,json={})
        context={'generation':1,'closing':False,'process':SimpleNamespace(returncode=None),'loop':asyncio.get_running_loop(),
            'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat(),'directory':str(scene['folder']),
            'session_id':'ses_root','known_sessions':{'ses_root','ses_child'},'lock':asyncio.Lock(),'change_review':scene['admission'],'profile':object()}
        context['client']=httpx.AsyncClient(base_url='http://sdk-boundary.invalid',transport=httpx.MockTransport(transport),trust_env=False)
        service.active[scene['ident']]=context
        service._claim(scene['pid'],scene['ident'],'root_work','MESSAGE',{'raw_text':'Original task','session_ref':scene['ref'],'generation':1})
        service._finish(scene['pid'],scene['ident'],'root_work','ACCEPTED',generation=1,original_session_id='ses_root')
        service._update(scene['pid'],scene['ident'],owner_status='READY',native_status='busy',active_operation_id='root_work')
        try:
            await service._path_permission_messages(scene['pid'],scene['ident'],context,[pending],[])
            assert service._permission_choices(pending,scene['manifest'],message=message)==['once','reject']
            result=await service.reply(scene['pid'],scene['ident'],'per_one',{'operation_id':'approve_child_once','session_ref':scene['ref'],
                'generation':1,'request_ref':{'id':'per_one','sha256':digest(pending),'generation':1},'reply':'once'},kind='PERMISSION')
            assert result['status']=='RETURNED' and result['workspace_write_review']['original_session_id']=='ses_child'
            assert sum(method=='POST' for method,_ in calls)==1
            assert ('GET','/session/ses_root/message/msg_one') not in calls
            assert context['session_id']=='ses_root'
        finally:
            await context['client'].aclose();service.active.pop(scene['ident'],None)
    try:asyncio.run(run())
    finally:p.finish(False)


def test_child_diff_api_reads_original_completed_child_and_preserves_later_manual_save(coding):
    scene=coding;p=Producer(scene,native_session='ses_child');service=scene['value']
    try:
        p.finish();message=p.message();child={'id':'ses_child','parentID':'ses_root','directory':str(scene['folder']),'title':'Child review'}
        service._update(scene['pid'],scene['ident'],children=[child],original_session={'id':'ses_root','directory':str(scene['folder'])},last_event_seq=0)
        for event in [{'type':'message.updated','properties':{'info':message['info']}},
                {'type':'message.part.updated','properties':{'part':message['parts'][0]}}]:
            service._event(scene['pid'],scene['ident'],'ORIGINAL_EVENT',{'native_payload':event,'native_payload_sha256':digest(event),'generation':1})
        reference=child_reference(service._state(scene['pid'],scene['ident']),child)
        (scene['folder']/'a.py').write_text('KEEP_LATER_MANUAL_SAVE\n')
        app=app_for(service);register_agent_collaboration_routes(app,service=service)
        route=f"/api/projects/{scene['pid']}/agent-sessions/{scene['ident']}/children/ses_child/diffs"
        query={'generation':1,'session_sha256':scene['ref']['sha256'],'child_sha256':reference['sha256'],
            'root_native_session_id':'ses_root','workspace_id':scene['workspace']['id'],
            'binding_revision':scene['workspace']['revision'],'binding_sha256':scene['workspace']['binding_sha256']}
        with TestClient(app) as client:
            response=client.get(route,params=query);assert response.status_code==200,response.text
            directory=response.json();assert directory['child_ref']==reference
            assert directory['snapshot_ref']['id']==digest({'scope':directory['scope'],'child_ref':reference})
            row=directory['files'][0]
            assert row['content_scope']=='FULL_FILE' and row['source_native_session_id']=='ses_child'
            detail=client.get(route,params={**query,'diff_id':row['diff_ref']['id'],'diff_sha256':row['diff_ref']['sha256'],
                'snapshot_sha256':directory['snapshot_ref']['sha256']})
            assert detail.status_code==200,detail.text
            value=detail.json();assert value['before']=='keep\nold\ntail\n' and value['after']=='keep\nnew\ntail\n'
            assert value['current_disk_compared'] is False and value['editor_dirty_state']=='NOT_OBSERVED'
            assert value['child_ref']==reference and value['source_native_session_id']=='ses_child'
            assert client.get(route,params={**query,'child_sha256':'f'*64}).status_code==409
        assert (scene['folder']/'a.py').read_text()=='KEEP_LATER_MANUAL_SAVE\n'
    finally:p.finish(False)
