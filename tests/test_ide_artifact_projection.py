"""Actual paired HTTP -> mapper -> TCP App registry/native resolver -> SQLite/PPTX.

The original-session message feed and pairing approval are explicit UNIT
boundaries. The artifact is made by the real optional authoring adapter; its
registry and managed working copy are real. No CLI/model/VSCode/Office runs.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
from docx import Document

from packages.domain.store import Store
from test_deliverable_authoring_plugin import MODULE, source, request as deck_request
from test_workflow_app_runtime import LoggedServer

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / '.node-tools/node_modules/node/bin/node'


def check(value, message='actual IDE artifact boundary failed'):
    if not value:
        raise AssertionError(message)


def test_actual_registered_artifact_pair_open_and_unknown_get_no_reopen(tmp_path):
    data = tmp_path / 'data'; data.mkdir()
    store = Store(data / 'eeas.sqlite3')
    pid = store.create_project('原创 IDE 文件路由', 'synthetic scope only')['id']
    source_ref, original = source(store, pid)
    made = MODULE.DeliverableAuthoring(store, data, pid).make_report_deck(deck_request(source_ref, 'actual_ide_artifact'))
    check(made['status'] == 'SAVED_NOT_VISUALLY_VERIFIED')
    manifest = made['artifact_manifest']
    folder=tmp_path/'original-approved-workspace';folder.mkdir()
    doc=Document();doc.add_paragraph('原创 QA Word：阈值 42，保留人工检查。');doc.save(folder/'report.docx')
    docx_before=hashlib.sha256((folder/'report.docx').read_bytes()).hexdigest()
    (folder/'notes.md').write_text('# 原创 QA\n保留编辑器手改。',encoding='utf-8')
    server = LoggedServer(data)
    server.env.update(EEAS_DESKTOP_HOST_TOKEN='a'*64, EEAS_DESKTOP_HOST_PID='123')
    if sys.flags.optimize:
        server.env['PYTHONOPTIMIZE'] = str(sys.flags.optimize)
    script = tmp_path / 'actual-registered-artifact.cjs'
    script.write_text(r"""
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=input.root;
const {createIdeApiAdapter}=require(path.join(root,'apps/desktop-host/ide-api-adapter.cjs'));
const {IdeCompanionBridge}=require(path.join(root,'apps/desktop-host/ide-companion-bridge.cjs'));
const {BridgeClient,newTicket}=require(path.join(root,'plugins/vscode-companion/client.cjs'));
const {CompanionController,PrivateState}=require(path.join(root,'plugins/vscode-companion/controller.cjs'));
const scope={project_id:input.pid,session_ref:{id:'synthetic_observed_owner',revision:1,sha256:'b'.repeat(64)},generation:1};
const original={...scope,workspace_ref:input.workspace_ref,original_session_id:'ses_fixture',owner_status:'DISCONNECTED',native_status:'idle',messages:[{info:{id:'msg_report',sessionID:'ses_fixture',role:'assistant'},parts:[{id:'prt_report',messageID:'msg_report',sessionID:'ses_fixture',type:'tool',callID:'call_report',tool:'deliverable-authoring_make_report_deck',state:{status:'completed',output:JSON.stringify(input.made)}}]}]};
original.messages.push({info:{id:'msg_files',role:'assistant',sessionID:'ses_fixture'},parts:[{id:'prt_links',messageID:'msg_files',sessionID:'ses_fixture',type:'text',text:'[Word 制作稿](report.docx) 与 [Markdown 说明](notes.md)'}]});
(async()=>{
 let bridge,client,release,opened;const trace=[];let nativeOpenCalls=0;
 const bootstrap=await fetch(input.origin+'/api/bootstrap',{headers:{'X-EEAS-Bootstrap':'1'}});assert.equal(bootstrap.status,200);const csrf=(await bootstrap.json()).csrf_token,cookie=bootstrap.headers.getSetCookie().map(v=>v.split(';')[0]).join(';');
 const request=async(url,body,signal)=>{
  if(url.includes('/agent-sessions/')){assert.equal(body,undefined);return structuredClone(original);}
  const reply=await fetch(input.origin+url,{method:body===undefined?'GET':'POST',headers:{Cookie:cookie,Origin:input.origin,'X-EEAS-CSRF':csrf,'X-EEAS-Desktop-Host':'a'.repeat(64),'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal});
  trace.push({method:body===undefined?'GET':'POST',url,status:reply.status});const result=await reply.json();if(!reply.ok)throw Object.assign(Error('fixture API failure'),{status:reply.status});return result;
 };
 try{
  const dispatch=createIdeApiAdapter({request,openArtifact:async({kind,target,...body},{signal})=>{nativeOpenCalls++;const resolved=await request('/api/desktop-host/native-open/'+(kind==='workspace-file'?'file':'report'),body,signal);assert.equal(resolved.status,'RESOLVED');if(kind==='workspace-file')return target==='editor'?resolved:{status:'OPEN_REQUESTED'};opened=resolved;assert.equal(crypto.createHash('sha256').update(fs.readFileSync(opened.native_path)).digest('hex'),input.made.artifact_ref.sha256);await new Promise(r=>{release=r;});return opened;}});
  bridge=new IdeCompanionBridge({dataDir:input.bridge_data,dispatch,requestPairing:async()=>({approved:true,methods:['session.get','artifact.open'],project_ids:[input.pid]})});const discovery=await bridge.start();client=new BridgeClient({discovery,ticket:newTicket(),timeoutMs:5000});await client.pair();
  for(let n=0;n<50;n++){if((await client.pairStatus()).result.pairing_status==='PAIRED')break;await new Promise(r=>setTimeout(r,5));}
  const controller=new CompanionController({state:new PrivateState(input.private_data)});controller.attach(client);controller.selected=await controller.read('session.get',scope);assert.equal(controller.selected.artifacts.length,3);assert.equal(controller.selected.artifacts[1].verification,'EXACT_ORIGINAL_MARKDOWN_HREF_AND_WORKSPACE_BINDING');assert.equal(controller.selected.artifacts[0].filename,input.made.artifact_manifest.filename);controller.setDraft(controller.snapshot().scopeToken,0,'保留下一条未发送的修改意见');
  const token=controller.snapshot().artifactTokens[0];client.timeoutMs=30;const reply=await controller.openArtifact(0,token);assert.equal(reply.status,'UNKNOWN');const deadline=Date.now()+10000;while(!release){if(Date.now()>deadline)throw Error('actual resolver did not complete');await new Promise(r=>setTimeout(r,10));}
  await assert.rejects(controller.openArtifact(0,token),{code:'ORIGINAL_REQUEST_UNRESOLVED'});client.timeoutMs=5000;const id=controller.state.value.requests[0].request_id;assert.equal((await controller.inspect(id)).status,'PENDING');release();
  let result;for(let n=0;n<50;n++){result=await controller.inspect(id);if(result.status==='OK')break;await new Promise(r=>setTimeout(r,10));}assert.equal(result.status,'OK');assert.equal(nativeOpenCalls,1);assert.equal(controller.snapshot().draft.text,'保留下一条未发送的修改意见');assert.equal(trace.filter(x=>x.method==='POST').length,1);assert.doesNotMatch(JSON.stringify(controller.selected),/native_path|DesktopToken|csrf_token/);
  const files=[];for(const name of ['report.docx','notes.md']){const index=controller.selected.artifacts.findIndex(item=>item.filename===name);assert(index>=0);const openedFile=await controller.openArtifact(index,controller.snapshot().artifactTokens[index]);assert.equal(openedFile.status,'OK');assert.equal(openedFile.result.file_source_ref.project_id,input.pid);assert.equal(openedFile.result.file_source_ref.path,name);assert.equal(openedFile.result.status,name.endsWith('.md')?'EDITOR_READY':'OPEN_REQUESTED');files.push(openedFile.result);}assert.equal(nativeOpenCalls,3);assert.equal(controller.snapshot().draft.text,'保留下一条未发送的修改意见');
  const foreign=await client.rpc('session.get',{...scope,project_id:'unapproved-project'},{});assert.equal(foreign.status,'ERROR');assert.equal(foreign.error.code,'SCOPE_DENIED');
  fs.writeFileSync(input.output,JSON.stringify({qualification:'ACTUAL_PAIRED_HTTP_TCP_APP_REGISTRY_STORE_NATIVE_RESOLUTION_WITH_UNIT_ORIGINAL_MESSAGE_AND_APPROVAL',trace,native_open_calls:nativeOpenCalls,workspace_files:files,artifact:controller.selected.artifacts[0],file_link_ref:opened.file_link_ref,reply:result.status,unsent_preserved:true,api_model_requests:0,office_open:'NOT_RUN',native_windows:'NOT_RUN',vscode:'NOT_RUN'},null,2),{flag:'wx'});
 }finally{release?.();client?.close();await bridge?.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
""", encoding='utf-8')
    try:
        server.start()
        with httpx.Client(base_url=server.origin,trust_env=False,timeout=20) as http:
            boot=http.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'});check(boot.status_code==200)
            http.headers.update({'Origin':server.origin,'X-EEAS-CSRF':boot.json()['csrf_token']})
            reply=http.post(f'/api/projects/{pid}/local-workspaces',json={'idempotency_key':'ide-owned-files','root':str(folder),'allowed_write_paths':[]});check(reply.status_code==201,reply.text)
            binding=reply.json()
        output = tmp_path / 'actual-artifact-result.json'
        process = subprocess.run([str(NODE), str(script)], input=json.dumps({'root':str(ROOT),'pid':pid,'origin':server.origin,'made':made,
            'workspace_ref':{key:binding['binding_sha256'] if key=='binding_sha256' else binding[key] for key in ('id','revision','binding_sha256')},'files':[{'name':name,'uri':(folder/name).as_uri()} for name in ('report.docx','notes.md')],'bridge_data':str(tmp_path/'bridge-data'),'private_data':str(tmp_path/'private-data'),'output':str(output)}, ensure_ascii=False),
            cwd=ROOT, env=os.environ, text=True, capture_output=True, timeout=45)
        (tmp_path/'node.log').write_text(process.stdout+process.stderr, encoding='utf-8')
        check(process.returncode == 0, 'actual Node/HTTP boundary failed; see node.log')
        result = json.loads(output.read_text())
        check(result['native_open_calls'] == 3)
        check(hashlib.sha256((folder/'report.docx').read_bytes()).hexdigest()==docx_before,'original Word bytes changed')
        check(len(result['workspace_files'])==2)
        check(result['reply'] == 'OK' and result['unsent_preserved'])
        check(result['artifact']['sha256'] == manifest['sha256'])
        check(store.document(pid, source_ref['document_id'], source_ref['revision'])['raw_bytes'] == original, 'original source bytes changed')
    finally:
        server.stop()
