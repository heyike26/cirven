"""Authored NOT_RUN for concentrated debug.

Real SQLite/original records with a labelled SDK-boundary fixture below. The
separate pinned-CLI case replaces only external model output; neither qualifies
Windows, VS Code, semantic quality or child file-write review.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from packages.agent_sessions.child_controls import ChildControls
from packages.agent_sessions.child_observations import child_reference
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_session_guidance import UnitOwner
from test_agent_sessions import scene, owner, ModelWire, tool, control, until


class ChildUnit(UnitOwner):
    def __init__(self,directory):
        super().__init__(directory)
        self.child={'id':'ses_unit_child','parentID':self.context['session_id'],'directory':'/original','title':'Review','agent':'explore'}
        self.context.update(known_sessions={self.context['session_id'],self.child['id']},directory='/original',
            process=SimpleNamespace(returncode=None))
        self.child_controls=ChildControls(self)
        self._update(self.pid,self.sid,active_operation_id=None,original_session_id=self.context['session_id'],
            original_session={'id':self.context['session_id'],'directory':'/original'},children=[self.child],
            pending_questions=[],pending_permissions=[],native_status='idle')
        self.messages=[{'info':{'id':'msg_seed','sessionID':self.child['id'],'role':'user','agent':'explore'},'parts':[]}]
        self.pending=[];self.post_error=False;self.statuses={}

    def child_body(self,key='child_next',parent=None,text='Continue this exact original child'):
        return {'operation_id':key,'session_ref':self.ref,'generation':3,
            'child_ref':child_reference(self._state(self.pid,self.sid),self.child),'raw_text':text,
            'timeout_seconds':60,'expected_active_operation_id':parent}

    async def _http(self,context,method,path,body=None,**options):
        self.http_calls.append({'method':method,'path':path,'body':deepcopy(body),'options':options})
        if method=='GET':
            if path=='/session/'+self.child['id']:return deepcopy(self.child)
            if path=='/session/ses_other':return {'id':'ses_other','parentID':self.context['session_id'],'directory':self.context['directory']}
            if path=='/session/'+self.child['id']+'/message':return deepcopy(self.messages)
            if path=='/session/status':return deepcopy(self.statuses)
            if path=='/question':return deepcopy(self.pending)
        elif path.endswith('/prompt_async'):
            self.messages.append({'info':{'id':body['messageID'],'sessionID':self.child['id'],'role':'user','agent':body['agent']},
                'parts':[{'id':'prt_'+body['messageID'][4:],'type':'text','sessionID':self.child['id'],
                    'messageID':body['messageID'],'text':body['parts'][0]['text']}]})
            if self.post_error:raise Conflict('UNIT original POST acknowledgement lost')
            return None
        elif path.endswith('/abort') or '/reply' in path:return True
        raise AssertionError((method,path))

    def answer(self,record,text='Original child result',native=None):
        native=native or self.child['id']
        self.messages.append({'info':{'id':'msg_final_'+record['native_message_id'][4:],'sessionID':native,'role':'assistant',
            'parentID':record['native_message_id'],'finish':'stop','time':{'completed':1}},
            'parts':[{'id':'prt_final_'+record['native_message_id'][4:],'sessionID':native,
                'messageID':'msg_final_'+record['native_message_id'][4:],'type':'text','text':text}]})

    async def observe(self,barrier=None):
        return await self.child_controls.observe(self.pid,self.sid,self.context,deepcopy(self.messages),self.statuses,
            quiescent=True,barrier=barrier,can_release_interrupt=bool(barrier))


def test_child_dispatch_uses_original_target_and_result_requires_exact_child_user_and_final(tmp_path):
    async def run():
        s=ChildUnit(tmp_path);body=s.child_body()
        saved=await s.child_controls.submit(s.pid,s.sid,body)
        assert saved['status']=='ACCEPTED' and saved['original_session_id']==s.child['id']
        posts=[row for row in s.http_calls if row['method']=='POST']
        assert len(posts)==1 and posts[0]['path']=='/session/'+s.child['id']+'/prompt_async'
        assert 'model' not in posts[0]['body'] and posts[0]['body']['agent']=='explore'
        assert s._state(s.pid,s.sid)['active_operation_id']==body['operation_id']
        await s.observe();assert s.operation(s.pid,s.sid,body['operation_id'])['status']=='ACCEPTED'
        s.answer(saved);await s.observe()
        final=s.operation(s.pid,s.sid,body['operation_id'])
        assert final['status']=='RETURNED' and final['semantic_acceptance'] is False
        assert s._state(s.pid,s.sid)['active_operation_id'] is None
        assert await s.child_controls.submit(s.pid,s.sid,body)==final
        assert len([row for row in s.http_calls if row['method']=='POST'])==1
    asyncio.run(run())


def test_latest_child_guidance_drives_cohort_without_using_the_old_response(tmp_path):
    async def run():
        s=ChildUnit(tmp_path);first=await s.child_controls.submit(s.pid,s.sid,s.child_body())
        s.answer(first,'OLD_RESPONSE')
        guide=await s.child_controls.submit(s.pid,s.sid,s.child_body('child_guide','child_next','Use the corrected constraint'))
        await s.observe();assert s.operation(s.pid,s.sid,'child_next')['status']=='ACCEPTED'
        s.answer(guide,'NEW_RESPONSE');await s.observe()
        final=s.operation(s.pid,s.sid,'child_next')
        assert final['continuation_guidance_ids']==['child_guide']
        assert final['native_assistant_ids']==['msg_final_'+guide['native_message_id'][4:]]
        assert s.operation(s.pid,s.sid,'child_guide')['status']=='RETURNED'
    asyncio.run(run())


def test_lost_native_ack_preserves_unknown_and_does_not_replay_same_or_other_root_work(tmp_path):
    async def run():
        s=ChildUnit(tmp_path);s.post_error=True;body=s.child_body()
        with pytest.raises(Conflict):await s.child_controls.submit(s.pid,s.sid,body)
        original=s.operation(s.pid,s.sid,body['operation_id']);assert original['status']=='UNKNOWN_OUTCOME'
        s.post_error=False
        assert await s.child_controls.submit(s.pid,s.sid,body)==original
        with pytest.raises(Conflict):await s.child_controls.submit(s.pid,s.sid,s.child_body('different_child_request'))
        assert len([row for row in s.http_calls if row['method']=='POST'])==1
        assert s._state(s.pid,s.sid)['active_operation_id']==body['operation_id']
    asyncio.run(run())


def test_exact_stop_and_deadline_abort_address_child_while_original_root_identity_stays_fixed(tmp_path):
    async def run():
        s=ChildUnit(tmp_path);original_root=s.context['session_id']
        active=await s.child_controls.submit(s.pid,s.sid,s.child_body())
        stop=await s.interrupt(s.pid,s.sid,{'operation_id':'child_stop','session_ref':s.ref,'generation':3,
            'expected_active_operation_id':active['id']})
        assert stop['target_native_session_id']==s.child['id']
        assert s.http_calls[-1]['path']=='/session/'+s.child['id']+'/abort'
        assert s.context['session_id']==original_root
        await s.observe(s.context['interrupt_barrier'])
        assert s.operation(s.pid,s.sid,active['id'])['status']=='CANCELLED'
        # Root's existing post-observation barrier release is separate from ACK.
        s.context.pop('interrupt_barrier')
        second=await s.child_controls.submit(s.pid,s.sid,s.child_body('deadline_child'))
        await s._deadline_abort(s.pid,s.sid,s.context,second)
        assert s.operation(s.pid,s.sid,second['id'])['status']=='UNKNOWN_OUTCOME'
        assert s.http_calls[-1]['path']=='/session/'+s.child['id']+'/abort'
        assert s.context['session_id']==original_root
    asyncio.run(run())


def test_shared_model_budget_and_foreign_child_basis_refuse_before_native_post(tmp_path):
    async def run():
        s=ChildUnit(tmp_path);s._update(s.pid,s.sid,model_calls_reserved=40)
        with pytest.raises(Conflict):await s.child_controls.submit(s.pid,s.sid,s.child_body())
        assert not any(row['method']=='POST' for row in s.http_calls)
        s._update(s.pid,s.sid,model_calls_reserved=1)
        body=s.child_body('foreign');body['child_ref']['root_native_session_id']='ses_foreign'
        with pytest.raises(Conflict):await s.child_controls.submit(s.pid,s.sid,body)
        assert not any(row['method']=='POST' for row in s.http_calls)
    asyncio.run(run())


def test_child_question_reply_keeps_parent_and_rejects_a_sibling_request_before_post(tmp_path):
    async def run():
        s=ChildUnit(tmp_path);first=await s.child_controls.submit(s.pid,s.sid,s.child_body())
        native={'id':'que_child','sessionID':s.child['id'],'questions':[{'header':'Direction','question':'Which constraint?',
            'options':[{'label':'A','description':'Use A'}],'multiple':False,'custom':False}]}
        s.pending=[native]
        request_ref={'id':native['id'],'sha256':digest(native),'generation':3}
        s._update(s.pid,s.sid,pending_questions=[{'kind':'QUESTION','request_ref':request_ref,'native_request':native}])
        await s.observe()
        assert s.operation(s.pid,s.sid,first['id'])['human_wait']['status']=='WAITING_FOR_USER'
        body={'operation_id':'reply_child_question','session_ref':s.ref,'generation':3,'request_ref':request_ref,'answers':[['A']]}
        replied=await s.reply(s.pid,s.sid,native['id'],body,kind='QUESTION')
        assert replied['status']=='RETURNED' and s.http_calls[-1]['path']=='/question/que_child/reply'
        assert s._state(s.pid,s.sid)['active_operation_id']==first['id']
        sibling={**native,'id':'que_sibling','sessionID':'ses_other'};s.context['known_sessions'].add('ses_other');s.pending=[sibling]
        bad={**body,'operation_id':'sibling_reply','request_ref':{'id':sibling['id'],'sha256':digest(sibling),'generation':3}}
        posts=len([row for row in s.http_calls if row['method']=='POST'])
        with pytest.raises(Conflict):await s.reply(s.pid,s.sid,sibling['id'],bad,kind='QUESTION')
        assert len([row for row in s.http_calls if row['method']=='POST'])==posts
    asyncio.run(run())


def test_actual_pinned_cli_continues_existing_explore_child_on_same_process_and_shared_wire(scene):
    wire=ModelWire([tool('task',{'description':'Original child review','prompt':'Read only review','subagent_type':'explore'}),
        'ORIGINAL_CHILD_RESPONSE','ORIGINAL_ROOT_RESPONSE','CHILD_CONTINUATION_RESPONSE']).start()
    async def run():
        service,request=owner(scene,wire)
        try:
            opened=await service.open(scene['pid'],request);sid=opened['session_ref']['id']
            await service.send(scene['pid'],sid,{**control(opened,'root_for_child'),'raw_text':'Use the explore child'})
            waiting=await until(service,scene,sid,lambda row:bool(row['pending_permissions']))
            pending=waiting['pending_permissions'][0]
            await service.reply(scene['pid'],sid,pending['request_ref']['id'],{**control(opened,'allow_original_child'),
                'request_ref':pending['request_ref'],'reply':'once'},kind='PERMISSION')
            final=await until(service,scene,sid,lambda row:row['active_operation_id'] is None)
            original_process=service.active[sid]['process']
            child=final['children'][0]
            accepted=await service.child_controls.submit(scene['pid'],sid,{**control(final,'continue_actual_child'),
                'child_ref':child_reference(final,child),'raw_text':'Continue your exact original review without writing files'})
            complete=await until(service,scene,sid,lambda row:row['active_operation_id'] is None)
            receipt=service.operation(scene['pid'],sid,'continue_actual_child')
            assert receipt['status']=='RETURNED' and receipt['original_session_id']==child['id']
            assert service.active[sid]['process'] is original_process and len(wire.requests)==4
            assert complete['model_calls_reserved']==4 and scene['actual'].read_bytes()==scene['original']
            assert accepted['root_original_session_id']==final['original_session_id']
        finally:await service.close()
    try:asyncio.run(run())
    finally:wire.close()
