"""Actual optional plugin/FS/Store/HTTP, with explicit UNIT original-tool boundary.

No model is called. Node runs the real plugin hooks; only the original tool
execution and original message observation are deterministic boundary fixtures.
This does not qualify native Windows writes, a model task, or atomic CAS.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from packages.adapters.opencode_change_review import (approve_guard, bind, historical_admissions,
    prepare, read_history, record_admission, ready, verify_bundle, write_patterns)
from packages.agent_sessions import AgentSessions
from packages.agent_sessions.models import OpenSession
from packages.domain.local_workspaces import LocalWorkspaces, WorkspaceInput
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Store, Conflict, InvalidInput
from packages.domain.tasks import canonical, digest
from test_session_diffs import ROOT, PATCH, app_for, check, query


@pytest.fixture
def coding(tmp_path):
    data = tmp_path/'data'; data.mkdir()
    store = Store(data/'eeas.sqlite3'); pid = store.create_project('原创完整差异', 'UNIT original tool boundary')['id']
    folder = tmp_path/'code workspace'; folder.mkdir(); (folder/'a.py').write_bytes(b'keep\nold\ntail\n')
    ws = LocalWorkspaces(store, data)
    binding = ws.bind(pid, WorkspaceInput(idempotency_key='review-write',root=str(folder),allowed_write_paths=['a.py','new.py','nested/']))
    caps=NativeCapabilities(store,data)
    profile={'synthetic_external_configuration':True}
    providers=SimpleNamespace(configuration_resolver=lambda *_:profile,current_fingerprint=lambda *_:'fixture-profile')
    value = AgentSessions(store, data, provider_registry=providers, workspaces=ws, capabilities=caps)
    ident = 'a'*32; workspace = {k:binding[k] for k in ['id','revision','binding_sha256']}
    request = {'workspace_ref':workspace,'allowed_effects':['WORKSPACE_WRITE'],'mcp_tool_allowlist':[], 'command_allowlist':[],
               'provider_profile_ref':{'id':'synthetic-profile','revision':1,'sha256':digest(profile)},
               'capability_binding_sha256':caps.native_binding_snapshot()['sha256']}
    manifest = {'id':ident,'project_id':pid,'request':request,'directory_identity':binding['root_identity'],
                'workspace_kind':'LOCAL_WORKSPACE','provider_fingerprint':'fixture-profile',
                'capability_configuration_refs':caps._index()}
    ref = {'id':ident,'revision':1,'sha256':digest(manifest)}
    value._write(value._path(pid,ident,'manifest.json'),manifest)
    value._write(value._path(pid,ident,'state.json'),{'generation':1,'original_session_id':'ses_root','messages':[],
        'observation_at':'2026-10-16T00:00:00Z','owner_status':'DISCONNECTED','native_status':'idle','active_operation_id':None})
    runtime = value._path(pid,ident,'runtime');runtime.mkdir()
    admission = prepare(runtime,project_id=pid,session_ref=ref,generation=1,workspace=binding)
    admission = bind(admission,'ses_root');record_admission(runtime,admission)
    return {'value':value,'pid':pid,'ident':ident,'folder':folder,'binding':binding,'workspace':workspace,
            'ref':ref,'runtime':runtime,'admission':admission,'manifest':manifest,'tmp':tmp_path}


class Producer:
    """Keep real plugin state alive between its two official hook callbacks."""
    def __init__(self, scene, *, tool='edit', target='a.py', before=None, relative_input=False, args=None, native_session='ses_root'):
        self.scene = scene; self.tool = tool; self.target = target; self.native_session = native_session
        self.existed = (scene['folder']/target).exists()
        self.args = args if args is not None else {'filePath':target if relative_input else str(scene['folder']/target), **({'oldString':'old','newString':'new','replaceAll':False} if tool=='edit' else {'content':'keep\nnew\ntail\n'})}
        script = scene['tmp']/('producer-'+target.replace('/','_')+'.mjs')
        script.write_text(r"""
import fs from 'node:fs';import {pathToFileURL} from 'node:url';import readline from 'node:readline';
const rl=readline.createInterface({input:process.stdin,crlfDelay:Infinity});let hooks,input;
for await(const line of rl){const command=JSON.parse(line);
 if(!hooks){input=command;process.env.EEAS_CHANGE_REVIEW_CONFIG=input.config;
  const module=await import(pathToFileURL(input.entry).href);const client=input.native_session==='ses_root'?undefined:{session:{get:async options=>({data:{id:options.path.id,parentID:'ses_root',directory:input.root}})}};hooks=await module.server({directory:input.root,worktree:input.root,client});
  await hooks['tool.execute.before']({tool:input.tool,sessionID:input.native_session,callID:'call_one'},{args:input.args});
  process.stdout.write('BEFORE\n');continue;}
 if(command.action==='complete'){
  fs.writeFileSync(input.args.filePath,'keep\nnew\ntail\n');
  const metadata=input.tool==='edit'?{filediff:{file:input.args.filePath,patch:input.patch,additions:1,deletions:1},diff:input.patch,diagnostics:{}}:{filepath:input.args.filePath,exists:input.existed,diagnostics:{}};
  await hooks['tool.execute.after']({tool:input.tool,sessionID:input.native_session,callID:'call_one',args:input.args},{title:input.target,output:'Tool completed',metadata});
 }else{await hooks.event({event:{type:'session.idle',properties:{sessionID:input.native_session}}});}
 process.stdout.write('DONE\n');rl.close();break;
}
""",encoding='utf-8')
        self.process = subprocess.Popen([str(ROOT/'.node-tools/node_modules/node/bin/node'),str(script)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            env={**os.environ,'TMPDIR':str(scene['tmp'])})
        packet = {'entry':scene['admission']['entry'],'config':scene['admission']['config_path'],'root':str(scene['folder']),
                  'tool':tool,'args':self.args,'patch':PATCH,'target':target,'existed':self.existed,'native_session':native_session}
        self.process.stdin.write(canonical(packet)+'\n');self.process.stdin.flush()
        check(self.line()=='BEFORE','Real plugin did not capture the tool boundary')

    def line(self):
        selector=selectors.DefaultSelector();selector.register(self.process.stdout,selectors.EVENT_READ)
        try:
            if not selector.select(15):
                raise AssertionError('Real plugin boundary timeout; no tool replay')
            line=self.process.stdout.readline().strip()
            if not line and self.process.poll() is not None:
                raise AssertionError('Real plugin exited: '+self.process.stderr.read())
            return line
        finally:selector.close()

    def finish(self, complete=True):
        if self.process.poll() is not None:return
        self.process.stdin.write(canonical({'action':'complete' if complete else 'skip'})+'\n');self.process.stdin.flush()
        check(self.line()=='DONE');self.process.stdin.close();self.process.wait(timeout=10)
        error=self.process.stderr.read();check(self.process.returncode==0,error)
        self.process.stdout.close();self.process.stderr.close()

    def message(self, status='completed'):
        return {'info':{'id':'msg_one','role':'assistant','sessionID':self.native_session,'path':{'cwd':str(self.scene['folder']),'root':str(self.scene['folder'])}},'parts':[
            {'id':'prt_one','messageID':'msg_one','sessionID':self.native_session,'type':'tool','tool':self.tool,'callID':'call_one',
             'state':{'status':status,'input':self.args,'metadata':({'filediff':{'file':str(self.scene['folder']/self.target),'patch':PATCH,'additions':1,'deletions':1}} if self.tool=='edit' else {'filepath':str(self.scene['folder']/self.target),'exists':self.existed})}}]}


def observed(scene, messages):
    return {'project_id':scene['pid'],'session_ref':scene['ref'],'workspace_ref':scene['workspace'],
            'generation':1,'original_session_id':'ses_root','messages':messages}


def saved(scene, messages, generation=1):
    service=scene['value'];state=service._state(scene['pid'],scene['ident'])
    state.update(messages=messages,generation=generation,change_review_admission=scene['admission'])
    service._write(service._path(scene['pid'],scene['ident'],'state.json'),state,replace=True)
    return {'session_sha256':scene['ref']['sha256'],'generation':generation,'workspace_ref':scene['workspace']}


def native(scene, producer):
    return {'id':'per_one','sessionID':producer.native_session,'permission':'edit','patterns':[producer.target],
            'always':['*'],'tool':{'messageID':'msg_one','callID':'call_one'},
            'metadata':{'filepath':producer.args['filePath'],'diff':PATCH}}


def test_explicit_write_grants_are_not_defaults_or_shell_permissions():
    old={'provider_profile_ref':{'id':'p','revision':1,'sha256':'a'*64},'capability_binding_sha256':'b'*64,'operation_id':'open'}
    check(OpenSession.model_validate(old).allowed_effects==[])
    with pytest.raises(Conflict):write_patterns({'allowed_write_paths':[]})
    with pytest.raises(InvalidInput):write_patterns({'allowed_write_paths':['src/a?.py']})
    check(write_patterns({'allowed_write_paths':['src/','README.md']})['src/*']=='ask')
    check(write_patterns({'allowed_write_paths':['src/','README.md']})['*']=='deny')
    check(write_patterns({'allowed_write_paths':['*']})['.git/*']=='deny')
    request={'mcp_tool_allowlist':[],'command_allowlist':[],'allowed_effects':[]}
    plain=AgentSessions._permissions(None,request)
    check('edit' not in plain and plain['*']=='deny' and plain['bash']=={'*':'deny'})


def test_real_plugin_complete_full_file_routes_restart_and_no_directory_blob_read(coding, monkeypatch):
    scene=coding;p=Producer(scene)
    try:p.finish()
    finally:p.finish(False)
    expected=saved(scene,[p.message()]);service=scene['value']
    real=Path.read_bytes
    def no_workspace_leaf(path):
        if path == scene['folder']/'a.py':raise AssertionError('Historical diff cannot read the current code')
        return real(path)
    monkeypatch.setattr(Path,'read_bytes',no_workspace_leaf)
    d=service.diffs(scene['pid'],scene['ident'],**expected)
    check(len(d['files'])==1 and d['files'][0]['content_scope']=='FULL_FILE',canonical(d))
    check(d['files'][0]['source_generation']==1 and d['files'][0]['source_scope']['generation']==1)
    ref=d['files'][0]['diff_ref']
    f=service.diff_file(scene['pid'],scene['ident'],**expected,snapshot_ref=d['snapshot_ref'],diff_ref=ref)
    check(f['before']=='keep\nold\ntail\n' and f['after']=='keep\nnew\ntail\n')
    check(f['before_sha256']==hashlib.sha256(f['before'].encode()).hexdigest())
    (scene['folder']/'a.py').write_text('USER_EDIT_AFTER_TASK_MUST_SURVIVE\n')
    with TestClient(app_for(service)) as client:
        base=f"/api/projects/{scene['pid']}/agent-sessions/{scene['ident']}/diffs"
        result=client.get(base,params=query(expected));check(result.status_code==200,result.text)
        q={**query(expected),'snapshot_sha256':d['snapshot_ref']['sha256'],'diff_sha256':ref['sha256']}
        result=client.get(base+'/'+ref['id'],params=q);check(result.status_code==200,result.text)
        check(result.json()['after']=='keep\nnew\ntail\n')
    restored=AgentSessions(service.store,service.data_dir,provider_registry=None,workspaces=service.workspaces,capabilities=None)
    check(restored.diff_file(scene['pid'],scene['ident'],**expected,snapshot_ref=d['snapshot_ref'],diff_ref=ref)['before']==f['before'])
    # Directory cannot open blob contents: remove one byte source only after
    # binding refs, and prove directory still reads metadata while file refuses.
    blob=Path(scene['admission']['scope']['ledger_root'])/'blobs'/(f['after_sha256']+'.utf8')
    blob.write_bytes(b'corrupt')
    check(service.diffs(scene['pid'],scene['ident'],**expected)['snapshot_ref']==d['snapshot_ref'])
    with pytest.raises(Conflict):service.diff_file(scene['pid'],scene['ident'],**expected,snapshot_ref=d['snapshot_ref'],diff_ref=ref)


@pytest.mark.parametrize('target,full', [('new.py',True), ('a.py',False)])
def test_full_producer_new_file_and_existing_write_missing_attestation(coding, target, full):
    scene=coding;p=Producer(scene,tool='write',target=target)
    try:p.finish()
    finally:p.finish(False)
    expected=saved(scene,[p.message()]);d=scene['value'].diffs(scene['pid'],scene['ident'],**expected)
    if not full:
        check(all(row['content_scope']!='FULL_FILE' for row in d['files']))
        check('FULL_FILE_RECORD_UNAVAILABLE' in d['coverage']['reason_codes'])
        return
    row=d['files'][0];check(row['content_scope']=='FULL_FILE' and row['status']=='added',canonical(d))
    f=scene['value'].diff_file(scene['pid'],scene['ident'],**expected,snapshot_ref=d['snapshot_ref'],diff_ref=row['diff_ref'])
    check(f['before']=='' and f['before_sha256']==hashlib.sha256(b'').hexdigest())


def test_exact_before_guard_rejects_manual_change_missing_claim_and_keeps_original_bytes(coding):
    scene=coding;p=Producer(scene)
    try:
        o=observed(scene,[p.message('running')]);n=native(scene,p)
        review=approve_guard(scene['admission'],o,n,p.message('running'),workspaces=scene['value'].workspaces,binding=scene['binding'])
        check(review['atomic_write_guaranteed'] is False and len(review['files'])==1)
        claim=Path(scene['admission']['scope']['budget_root'])/'claims'/(hashlib.sha256(b'a.py').hexdigest()+'.json')
        retained=claim.with_suffix('.retained');claim.rename(retained)
        try:
            with pytest.raises(Conflict):approve_guard(scene['admission'],o,n,p.message('running'),workspaces=scene['value'].workspaces,binding=scene['binding'])
        finally:retained.rename(claim)
        (scene['folder']/'a.py').write_text('USER_CHANGED_WHILE_PERMISSION_WAITED\n')
        with pytest.raises(Conflict,match='WORKSPACE_SOURCE_CHANGED'):
            approve_guard(scene['admission'],o,n,p.message('running'),workspaces=scene['value'].workspaces,binding=scene['binding'])
        check((scene['folder']/'a.py').read_text()=='USER_CHANGED_WHILE_PERMISSION_WAITED\n')
        (scene['folder']/'a.py').write_text('keep\nold\ntail\n')
        # Same text is insufficient: the saved version's stat changed.
        with pytest.raises(Conflict):approve_guard(scene['admission'],o,n,p.message('running'),workspaces=scene['value'].workspaces,binding=scene['binding'])
    finally:p.finish(False)


def test_cross_generation_index_retains_old_source_without_borrowing_new_authority(coding):
    scene=coding;p=Producer(scene)
    try:p.finish()
    finally:p.finish(False)
    expected=saved(scene,[p.message()]);service=scene['value']
    first=service.diffs(scene['pid'],scene['ident'],**expected)
    newer=prepare(scene['runtime'],project_id=scene['pid'],session_ref=scene['ref'],generation=2,workspace=scene['binding'])
    newer=bind(newer,'ses_root');record_admission(scene['runtime'],newer)
    check(newer['scope']['budget_root']==scene['admission']['scope']['budget_root'])
    check(newer['scope']['ledger_root']!=scene['admission']['scope']['ledger_root'])
    expected=saved(scene,[p.message()],generation=2)
    d=service.diffs(scene['pid'],scene['ident'],**expected);row=d['files'][0]
    check(d['scope']['generation']==2 and row['source_generation']==1 and row['source_scope']['generation']==1)
    f=service.diff_file(scene['pid'],scene['ident'],**expected,snapshot_ref=d['snapshot_ref'],diff_ref=row['diff_ref'])
    check(f['after']=='keep\nnew\ntail\n' and f['source_scope']['session_ref']==scene['ref'])
    with pytest.raises(Conflict):service.diff_file(scene['pid'],scene['ident'],**expected,snapshot_ref=first['snapshot_ref'],diff_ref=first['files'][0]['diff_ref'])
    forged=deepcopy(p.message());forged['info']['id']='msg_other';forged['parts'][0]['id']='prt_other';forged['parts'][0]['messageID']='msg_other'
    expected=saved(scene,[p.message(),forged],generation=2)
    d=service.diffs(scene['pid'],scene['ident'],**expected)
    check(all(r['content_scope']!='FULL_FILE' for r in d['files']) and 'AMBIGUOUS_ORIGINAL_CALL_ID' in d['coverage']['reason_codes'])


def test_index_budget_and_old_scope_corruption_are_bounded(coding):
    scene=coding
    admissions=list(historical_admissions(scene['runtime'],observed(scene,[])))
    check(len(admissions)==1 and admissions[0]==scene['admission'])
    for generation in range(2,17):
        value=prepare(scene['runtime'],project_id=scene['pid'],session_ref=scene['ref'],generation=generation,workspace=scene['binding'])
        value=bind(value,'ses_root');record_admission(scene['runtime'],value)
    with pytest.raises(Conflict,match='history is full'):
        prepare(scene['runtime'],project_id=scene['pid'],session_ref=scene['ref'],generation=17,workspace=scene['binding'])
    path=scene['runtime']/'change-review/generation-1/admission.json'
    record=json.loads(path.read_text());record['value']['scope']['project_id']='foreign';path.write_text(canonical(record))
    with pytest.raises(Conflict):list(historical_admissions(scene['runtime'],{**observed(scene,[]),'generation':16}))
    index=scene['runtime']/'change-review/admissions.json'
    body=json.loads(index.read_text())['value'];body['admissions']=[None]
    index.write_text(canonical({'value':body,'sha256':digest(body)}))
    with pytest.raises(Conflict):list(historical_admissions(scene['runtime'],observed(scene,[])))


@pytest.mark.parametrize('mode', ['drift','unknown'])
def test_actual_reply_service_drift_guard_or_unknown_has_no_automatic_post_replay(coding, mode):
    # UNIT original HTTP boundary; all operation/Store/controller code is real.
    scene=coding;p=Producer(scene);service=scene['value'];n=native(scene,p);m=p.message('running')
    expected=saved(scene,[m])
    class Process:returncode=None
    async def run():
        calls=[]
        async def transport(request):
            calls.append((request.method,request.url.path))
            if request.method=='GET' and request.url.path=='/permission':return httpx.Response(200,json=[n])
            if request.method=='GET' and '/message/' in request.url.path:return httpx.Response(200,json=m)
            if request.method=='POST' and request.url.path=='/permission/per_one/reply':
                if mode=='unknown':raise httpx.ReadTimeout('UNIT original reply response lost',request=request)
                return httpx.Response(200,json=True)
            return httpx.Response(404,json={})
        context={'project_id':scene['pid'],'generation':1,'closing':False,'process':Process(),'loop':asyncio.get_running_loop(),
                 'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat(),
                 'directory':str(scene['folder']),'session_id':'ses_root','known_sessions':{'ses_root'},
                 'lock':asyncio.Lock(),'change_review':scene['admission'],'profile':object()}
        context['client']=httpx.AsyncClient(base_url='http://original-fixture',transport=httpx.MockTransport(transport))
        service.active[scene['ident']]=context
        # Only the original process/HTTP and nonsecret provider configuration
        # are UNIT boundaries. Real _context, Store, namespace and CAS checks run.
        parent_id='original-edit'
        service._claim(scene['pid'],scene['ident'],parent_id,'MESSAGE',{'operation_id':parent_id,
            'session_ref':scene['ref'],'generation':1,'raw_text':'Apply the selected edit'})
        service._finish(scene['pid'],scene['ident'],parent_id,'ACCEPTED',generation=1, original_session_id='ses_root')
        ref={'id':'per_one','sha256':digest(n),'generation':1}
        service._update(scene['pid'],scene['ident'],owner_status='READY',native_status='busy',active_operation_id=parent_id,
            pending_permissions=[{'kind':'PERMISSION','request_ref':ref,'native_request':n}])
        body={'operation_id':'approve-drift','session_ref':scene['ref'],'generation':1,'request_ref':ref,'reply':'once'}
        try:
            check(service._permission_choices(n,scene['manifest'])==['once','reject'])
            if mode=='unknown':
                with pytest.raises(httpx.ReadTimeout):
                    await service.reply(scene['pid'],scene['ident'],'per_one',body,kind='PERMISSION')
                receipt=service.operation(scene['pid'],scene['ident'],'approve-drift')
                check(receipt['status']=='UNKNOWN_OUTCOME' and receipt['workspace_write_review']['atomic_write_guaranteed'] is False)
                before=list(calls)
                recovered=await service.reply(scene['pid'],scene['ident'],'per_one',body,kind='PERMISSION')
                check(recovered==receipt and calls==before and sum(method=='POST' for method,_ in calls)==1)
                check(service.operation(scene['pid'],scene['ident'],'approve-drift')==receipt and calls==before)
                return
            (scene['folder']/'a.py').write_text('MANUAL_EDIT_MUST_NOT_BE_APPROVED\n')
            with pytest.raises(Conflict,match='WORKSPACE_SOURCE_CHANGED'):
                await service.reply(scene['pid'],scene['ident'],'per_one',body,kind='PERMISSION')
            check(not any(method=='POST' for method,_ in calls))
            receipt=service.operation(scene['pid'],scene['ident'],'approve-drift')
            check(receipt['status']=='FAILED' and receipt.get('workspace_write_review') is None)
            rejected=await service.reply(scene['pid'],scene['ident'],'per_one',{**body,'operation_id':'reject-only','reply':'reject'},kind='PERMISSION')
            check(rejected['status']=='RETURNED' and sum(method=='POST' for method,_ in calls)==1)
        finally:
            service.active.clear();await context['client'].aclose()
    try:asyncio.run(run())
    finally:p.finish(False)


def test_actual_plugin_store_tcp_mapper_full_history_is_exact_and_readonly(coding):
    # Real producer hooks, Store, API auth and mapper; original tool execution
    # and original message observation remain explicitly UNIT boundaries.
    from test_workflow_app_runtime import LoggedServer
    scene=coding;p=Producer(scene)
    try:p.finish()
    finally:p.finish(False)
    saved(scene,[p.message()]);(scene['folder']/'a.py').write_text('NEW_USER_DISK_EDIT\n')
    server=LoggedServer(scene['value'].data_dir)
    script=scene['tmp']/'actual-full-mapper.cjs'
    script.write_text(r"""
const fs=require('node:fs'),assert=require('node:assert/strict');const input=JSON.parse(fs.readFileSync(0,'utf8'));
const {createIdeApiAdapter}=require(input.adapter);
(async()=>{const b=await fetch(input.origin+'/api/bootstrap',{headers:{'X-EEAS-Bootstrap':'1'}});assert.equal(b.status,200);
const cookie=b.headers.getSetCookie().map(x=>x.split(';')[0]).join(';'),trace=[];
const dispatch=createIdeApiAdapter({request:async(url,body)=>{assert.equal(body,undefined);const r=await fetch(input.origin+url,{headers:{Cookie:cookie}});trace.push({method:'GET',url,status:r.status});const value=await r.json();if(!r.ok)throw Object.assign(Error('UNIT readonly API unavailable'),{status:r.status});return value;}});
const d=await dispatch('session.diff',{scope:input.scope,payload:{workspace_ref:input.workspace_ref}});
assert.equal(d.files.length,1);assert.equal(d.files[0].content_scope,'FULL_FILE');
const f=await dispatch('session.diff.file',{scope:input.scope,payload:{workspace_ref:input.workspace_ref,snapshot_ref:d.snapshot_ref,diff_ref:d.files[0].diff_ref}});
assert.equal(f.before,'keep\nold\ntail\n');assert.equal(f.after,'keep\nnew\ntail\n');
assert.deepEqual(f.source_scope,d.files[0].source_scope);assert.equal(f.source_generation,1);assert.equal(trace.length,2);
process.stdout.write(JSON.stringify({trace,directory:d,file:f,model_calls:0,boundary:'REAL_PLUGIN_FS_STORE_TCP_MAPPER_UNIT_ORIGINAL_TOOL'}));
})().catch(e=>{console.error(e);process.exitCode=1;});
""",encoding='utf-8')
    try:
        server.start()
        result=subprocess.run([str(ROOT/'.node-tools/node_modules/node/bin/node'),str(script)],
            input=canonical({'origin':server.origin,'adapter':str(ROOT/'apps/desktop-host/ide-api-adapter.cjs'),
                'scope':{'project_id':scene['pid'],'session_ref':scene['ref'],'generation':1},'workspace_ref':scene['workspace']}),
            capture_output=True,text=True,timeout=25)
        (scene['tmp']/'actual-full-tcp-mapper-result.txt').write_text(result.stdout+'\n'+result.stderr)
        check(result.returncode==0,result.stdout+result.stderr)
        check((scene['folder']/'a.py').read_text()=='NEW_USER_DISK_EDIT\n')
    finally:server.stop()


def test_actual_original_cli_loads_opt_in_plugin_but_never_calls_a_model(tmp_path):
    from test_agent_sessions import scene as scene_fixture, owner, ModelWire
    scene=scene_fixture.__wrapped__(tmp_path)
    folder=tmp_path/'explicit coding workspace';folder.mkdir();(folder/'a.py').write_text('keep\nold\ntail\n')
    bound=scene['workspaces'].bind(scene['pid'],WorkspaceInput(idempotency_key='explicit-write-root',root=str(folder),allowed_write_paths=['a.py']))
    scene.update(folder=folder,bound=bound,actual=folder/'a.py',original=(folder/'a.py').read_bytes())
    wire=ModelWire([]).start()
    async def run():
        value,request=owner(scene,wire);request['allowed_effects']=['WORKSPACE_WRITE']
        try:
            opened=await value.open(scene['pid'],request)
            ident=opened['session_ref']['id'];context=value.active[ident]
            check(opened['file_writes']=='EXPLICIT_WORKSPACE_WRITE_ASK_WITH_CHANGE_REVIEW')
            check(ready(context['change_review']) and context['change_review']['native_session_id']==opened['original_session_id'])
            config=await value._http(context,'GET','/config')
            check(config['snapshot'] is False and config['permission']['edit']['a.py']=='ask')
            check(config['permission']['bash']=={'*':'deny'} and config['agent']['plan']['permission']['edit']=='deny')
            check(len(wire.requests)==0 and request['max_calls']==16 and request['max_output_tokens']==1024)
            check((folder/'a.py').read_bytes()==scene['original'])
        finally:await value.close()
    try:asyncio.run(run())
    finally:wire.close()
