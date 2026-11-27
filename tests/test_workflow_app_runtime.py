"""Actual TCP app -> unmodified OpenCode -> plugin/Core -> two profile relays.

Only the two external model services use deterministic fixture responses. This
does not qualify model decisions, a subscription, Windows or company gateways.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import httpx

from conftest import LiveServer
from test_agent_sessions import CLI, KEY, MODEL, ModelWire, tool
from packages.adapters.opencode_profile import CLI_SHA, VERSION


class LoggedServer(LiveServer):
    def start(self):
        self.log = (self.data_dir.parent/'actual-api.log').open('wb')
        self.process = subprocess.Popen([sys.executable,'-m','apps.api','serve'],
            cwd=Path(__file__).resolve().parents[1],env=self.env,stdout=self.log,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+45
        with httpx.Client(trust_env=False) as client:
            while time.monotonic()<deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('Actual app exited; inspect retained actual-api.log')
                try:
                    if client.get(self.origin+'/health',timeout=.3).status_code==200:
                        return self
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
        raise RuntimeError('Actual app readiness exceeded 45 seconds; log retained')

    def stop(self):
        try:
            super().stop()
        finally:
            if getattr(self,'log',None):
                self.log.close()


def test_real_tcp_plugin_model_replan_then_two_original_children_preserve_pins_and_evidence(tmp_path):
    approved = Path(os.environ.get('EEAS_TEST_APPROVED_CLI_CACHE', str(CLI)))
    assert hashlib.sha256(approved.read_bytes()).hexdigest() == CLI_SHA[os.name]
    data = tmp_path/'data'
    installed = data/'provider-settings/runtime'/VERSION/'opencode'
    installed.parent.mkdir(parents=True)
    if approved.stat().st_dev == installed.parent.stat().st_dev:
        os.link(approved, installed)
    else:
        shutil.copy2(approved, installed)
    fast, deep = ModelWire([]).start(), ModelWire(['ACTUAL_SECOND_PROFILE_CHILD']).start()
    server = LoggedServer(data)
    try:
        server.start()
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=60) as client:
            bootstrap = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap':'1'})
            assert bootstrap.status_code == 200
            client.headers.update({'Origin':server.origin, 'X-EEAS-CSRF':bootstrap.json()['csrf_token']})
            def call(method,path,body=None):
                result=client.request(method,path,json=body)
                assert result.is_success, (result.status_code,result.text)
                return result.json()
            pid=call('POST','/api/projects',{'name':'真实动态任务链','description':'original runtime qualification'})['id']
            profiles=[]
            for ident,wire in [('workflow-fast',fast),('workflow-deep',deep)]:
                saved=call('POST','/api/planning/providers',{'operation_id':'save-'+ident,'id':ident,
                    'provider_url':wire.url,'model_id':MODEL,'api_key':KEY,
                    'auth_mode':'API_KEY','reasoning_efforts':['low','high']})
                profiles.append(saved['profile_ref'])
            root=f'/api/projects/{pid}/dynamic-workflows'
            initial=call('POST',root,{'operation_id':'create-original-workflow','plan':{
                'title':'双路核对','goal':'保持人工约束与证据','nodes':[
                    {'id':'first','title':'核对第一来源','acceptance':[{'id':'read','text':'来源核对'}],
                     'agent_policy':{'model':{'mode':'MANUAL','value':'workflow-fast'},'depth':{'mode':'AUTO','value':None}}},
                    {'id':'second','title':'核对第二来源','acceptance':[{'id':'read','text':'第二来源核对'}],
                     'agent_policy':{'model':{'mode':'MANUAL','value':'workflow-deep'},'depth':{'mode':'MANUAL','value':'high'}}}]}})
            config=call('GET','/api/agent-sessions/configuration')
            opened=call('POST',f'/api/projects/{pid}/agent-sessions/open',{
                'operation_id':'open-dynamic-owner','provider_profile_ref':profiles[0],
                'capability_binding_sha256':config['capability_binding_sha256'],
                'workflow_ref':initial['workflow_ref'],'workflow_provider_refs':[profiles[1]],
                'ttl_seconds':180,'max_calls':16,'max_output_tokens':1024})
            sid=opened['session_ref']['id'];session=f'/api/projects/{pid}/agent-sessions/{sid}'
            projection_path=session+'/dynamic-workflow?generation='+str(opened['generation'])+'&session_sha256='+opened['session_ref']['sha256']
            projection=call('GET',projection_path)['projection']
            aliases=[alias for node in projection['plan']['nodes'] for alias in node['aliases']]
            requested=[next(row['alias'] for row in aliases if row['node_id']==nid and row['route_id']=='workflow-fast' and row['variant'] is None)
                       for nid in ('first','second')]
            revised=deepcopy(initial['plan']);revised['nodes'][0]['description']='依据新获得资料细化核对，保留人工选择'
            plan_args={'operation_id':'model-refine-plan','expected_ref':initial['workflow_ref'],
                       'plan':revised,'reason':'新证据细化工作，不能覆盖人工约束'}
            fast.replies[:]=[tool('eeas_workflow_replan',plan_args),
                tool('task',{'description':'First real review','prompt':'Read-only review first source','subagent_type':requested[0]}),
                'ACTUAL_FIRST_PROFILE_CHILD',
                tool('task',{'description':'Second real review','prompt':'Read-only review second source','subagent_type':requested[1]}),
                'ACTUAL_DYNAMIC_PARENT_RETURNED']
            call('POST',session+'/messages',{'operation_id':'run-dynamic-parent','session_ref':opened['session_ref'],
                'generation':opened['generation'],'raw_text':'先细化计划，再委派核对','timeout_seconds':120})
            allowed=set();deadline=time.monotonic()+100;final=None
            while time.monotonic()<deadline:
                final=call('GET',session)
                for pending in final['pending_permissions']:
                    ref=pending['request_ref']
                    if ref['id'] in allowed:
                        continue
                    assert pending['native_request']['permission']=='task'
                    assert set(pending['native_request']['patterns']) <= {row['alias'] for row in aliases}
                    call('POST',session+'/permissions/'+ref['id']+'/reply',{
                        'operation_id':'allow-'+str(len(allowed)),'session_ref':opened['session_ref'],
                        'generation':opened['generation'],'request_ref':ref,'reply':'once'})
                    allowed.add(ref['id'])
                if final['active_operation_id'] is None:
                    break
                time.sleep(.1)
            assert final and final['active_operation_id'] is None
            actual=call('GET',root+'/'+initial['id'])
            assert actual['revision']==2 and actual['change']['actor']=='MODEL'
            assert [node['agent_policy'] for node in actual['plan']['nodes']]==[node['agent_policy'] for node in initial['plan']['nodes']]
            assert len(final['children'])==2 and len(fast.requests)==5 and len(deep.requests)==1
            assert deep.requests[0]['body']['reasoning_effort']=='high'
            observations=[record for record in actual['actual_records'] if record['kind']=='EXECUTION_OBSERVATION']
            assert {record['node_id'] for record in observations}=={'first','second'}
            second=[record for record in observations if record['node_id']=='second'][-1]
            assert second['payload']['routing']['model']['effective']=='workflow-deep'
            assert second['payload']['routing']['depth']['effective']=='high'
            assert all(row['validation_status']!='PASSED' for row in actual['node_projection'])
            assert client.post('/api/dynamic-workflow/plugin-plan',json={
                'schema_version':1,'owner_ref':{'project_id':pid,'session_ref':opened['session_ref'],'generation':opened['generation']},
                **plan_args}).status_code==401, 'An ordinary cookie is not plugin model-plan authority'
            after=call('GET',projection_path)
            assert after['projection']['plan']['plan_ref']==actual['workflow_ref']
            assert len(fast.requests)==5 and len(deep.requests)==1, 'Observation must not dispatch model turns'
            tmp_path.joinpath('actual-workflow-app-evidence.json').write_text(json.dumps({
                'workflow':actual,'session':final,'plugin':after,
                'wire_requests':{'fast':fast.requests,'deep':deep.requests},
                'qualification':'ACTUAL_TCP_APP_ORIGINAL_RUNTIME_ONLY_EXTERNAL_MODELS_REPLACED'},ensure_ascii=False))
            closed=call('POST',session+'/disconnect',{'operation_id':'close-dynamic-owner',
                'session_ref':opened['session_ref'],'generation':opened['generation']})
            assert closed['owner_status']=='DISCONNECTED'
    finally:
        tmp_path.joinpath('external-model-requests.json').write_text(json.dumps({
            'fast':fast.requests,'deep':deep.requests,
            'qualification':'SYNTHETIC_EXTERNAL_MODEL_REQUESTS_ONLY'},ensure_ascii=False))
        fast.close();deep.close();server.stop()
