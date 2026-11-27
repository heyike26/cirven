"""Actual Main history callbacks and Solid sidebar; isolated fixture transports.

No shared App/API, Windows, owner load, message send or model is exercised.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

R = Path(__file__).resolve().parents[1]

TEMPLATE = r'''
import {render} from 'solid-js/web';
import {batch,createEffect,createMemo,createSignal} from 'solid-js';
import ConversationSidebar from '__SRC__/ConversationSidebar.tsx';
import {parseHistoryPage} from '__SRC__/conversation-history.ts';
import {parseThreadScope,parseThreadHistory,threadDraftSources} from '__SRC__/conversation-thread.ts';
import {assertAgentSession,originalSessionReady,originalSessionCanResumeForSend} from '__SRC__/agent-sessions.ts';
import {sameNativeReference} from '__SRC__/native-collaboration.ts';
import {DiscussionDraftLease,migrateConversationDraft,discussionDraftKey,seedConversationDraft} from '__SRC__/discussion-drafts.ts';
import {originalConversationMode,readConversationAgentMode} from '__SRC__/conversation-agent-mode.ts';
import {sameReview} from '__SRC__/native-review.ts';
import {userProjects} from '__SRC__/startup-project.ts';

function Fixture(){
 const known={id:'known-project',name:'Existing project',description:''},external={id:'api-created-project',name:'API-created project',description:''},internal={id:'original-internal-project',name:'Internal owner',description:'eeas.original-agent-session.internal-project.v1'};
 const id='original:'+'a'.repeat(64),ownerRef={id:'owner',revision:1,sha256:'b'.repeat(64)},workspaceRef={id:'c'.repeat(64),revision:1,binding_sha256:'d'.repeat(64)};
 const source={kind:'original',project_id:external.id,session_ref:ownerRef,generation:2,provider_profile_ref:null,workspace_ref:workspaceRef};
 const rawDirectory={version:1,execution_authorized:false,coverage:{model_calls:0,partial:false,complete:true,has_more:false,errors:[]},next_cursor:null,items:[{id,kind:'original',project_id:external.id,project_name:external.name,title:'Externally created conversation',read_only:false,updated_at:null,source,conversation_ref:{id,revision:1},organization:{revision:1,archived:false},conversation_origin:{kind:'original',project_id:external.id,session_id:ownerRef.id}}]};
 const rawScope={version:1,conversation_ref:{id,revision:1},project_id:external.id,archived:false,execution_started:false,files_moved:false,automatic_input_authorized:false,file_write_authorized:false,sources:[{id,identity:{kind:'original',project_id:external.id,session_id:ownerRef.id},original_source:{...source,generation:1}}],next_message:{project_id:external.id,conversation_id:'frozen-conversation'}};
 const rawHistory={version:1,conversation_ref:{id,revision:1},project_id:external.id,execution_authorized:false,automatic_input_authorized:false,file_write_authorized:false,messages:[],next_cursor:null,coverage:{complete:true,source_partial:false,model_calls:0}};
 const owner={project_id:external.id,session_ref:ownerRef,generation:2,owner_status:'READY',connected_owner:true,configuration_current:true,observation_is_current:true,native_status:'idle',messages:[],todo:[],children:[],pending_questions:[],pending_permissions:[],last_event_seq:6,automatic_post_replay:false};
 const [allProjects,setProjects]=createSignal<any[]>([known,internal]),projects=createMemo(()=>userProjects(allProjects()));
 const [project,setProject]=createSignal<any>(known),[workspace,setWorkspace]=createSignal<any>(),[workspaces,setWorkspaces]=createSignal<any[]>([]);
 const [historyItems,setHistoryItems]=createSignal(parseHistoryPage(rawDirectory,'en').items),[historyActiveId,setHistoryActiveId]=createSignal<string>(),[historySelectedItem,setHistorySelectedItem]=createSignal<any>();
 const [threadScope,setThreadScope]=createSignal<any>(),[selectedDiscussion,setSelectedDiscussion]=createSignal<any>(),[originalSession,setOriginalSession]=createSignal<any>();
 const [prompt,setPrompt]=createSignal('Existing project draft'),[nativeDraftAttachments,setNativeDraftAttachments]=createSignal<any[]>([]),[error,setError]=createSignal('');
 const calls:any[]=[];let nav=0,activityRequest=0,studioDisposed=false,threadReadGeneration=0,threadResultGeneration=0,threadReadAbort:AbortController|undefined,nativeDraftOwner:any,discussionDraftLease:DiscussionDraftLease|undefined,composerInteracted=true;
 let backendProjects=[known,internal,external],deferKind='',releaseRead:(()=>void)|undefined;
 const wait=async(kind:string)=>{if(deferKind===kind){deferKind='';calls.push({kind:'pending',read:kind});await new Promise<void>(resolve=>releaseRead=resolve);releaseRead=undefined;}};
 const api={projects:async()=>{calls.push({kind:'projects'});await wait('projects');return structuredClone(backendProjects);},workspaces:async()=>{calls.push({kind:'workspaces'});await wait('workspaces');return [structuredClone(workspaceRef)];}};
 const agentSessionsApi={get:async(pid:string,ref:any)=>{calls.push({kind:'owner',pid,generation:owner.generation});return assertAgentSession(structuredClone(owner),ref,pid);}};
 const threadClient={select:async()=>{calls.push({kind:'selection'});return parseThreadScope(structuredClone(rawScope),id);},history:async(scope:any)=>parseThreadHistory(structuredClone(rawHistory),scope)};
 const navigation={begin:()=>{const stamp=++nav;return{current:()=>stamp===nav,signal:new AbortController().signal};},capture:()=>{const stamp=nav;return()=>stamp===nav;}};
 const creationClient={pending:undefined},nativeBridge=undefined,activeSession=()=>undefined,conversationId=()=>threadScope()?.next_message.conversation_id??'unbound',uiLocale=()=> 'zh';
 function saveComposerDraft(){if(discussionDraftLease)discussionDraftLease.save({text:prompt(),attachments:nativeDraftAttachments()});else localStorage.setItem('fixture-existing-draft',prompt());}
 createEffect(()=>{prompt();nativeDraftAttachments();selectedDiscussion();if(discussionDraftLease)saveComposerDraft();});
 const noop=()=>{},flushWorkbenchDrafts=async()=>{},flushDynamicWorkflowDrafts=async()=>{},flushOriginalControlDrafts=noop,stopObserver=noop;
 const selectOriginalSession=(value?:any)=>setOriginalSession(value),draftPersistence={pause:noop},projectObjects={activate:noop};
 const setThreadResultSelection=noop,setDraftReady=noop,setActiveOperation=noop,setActiveSession=noop,setFeedbackRecords=noop,setDraftAttachments=noop,setMaterials=noop,setSelection=noop,clearProjectFeed=noop,setDirectFile=noop,setDirectWrite=noop,setDirectFileRequired=noop,setDirectResults=noop,setNativeFileFeedbacks=noop,setDirectFeedbackRefs=noop,setInspector=noop,setNativeOpen=noop,setSkillDrawer=noop,setSessionManagementOpen=noop,setNativeMessages=noop;
 const setThreadAccess=noop,setThreadMessages=noop,setThreadCursor=noop,setThreadLoading=noop,setThreadError=noop,setToolMode=noop,setOriginalAgent=noop,setHistoryReloadToken=noop,setNotice=noop,showAgentChat=noop,presentWorkspace=noop,setHistoryPopoverOpen=noop,setSidebarOpen=noop,loadSourceContext=noop,refreshNative=noop,refreshObjects=noop,compact=()=>false;
 const busy=()=>false,setSupervisorReturn=noop,setActivityLoading=noop,setActivityDetail=noop,setActivityOpen=noop,setSupervisorOpen=noop;
 const recoverDraft=()=>{throw Error('Unexpected fixture draft conflict');},adoptCreatedConversation=async()=>{},openLegacyHistoryConversation=async()=>{},historyDiscoverySource=()=>false,refreshHistory=async()=>{};
 const providerRef={id:'existing-pro',revision:1,sha256:'e'.repeat(64)},providers=()=>[{profile_ref:providerRef}],providerId=()=>providerRef.id,originalAgent=()=> 'plan',quickScope=()=>({providerProfileRef:providerRef});
 const originalConnection={open:async()=>{calls.push({kind:'unexpected-owner-open'});return{bound:false};},acknowledge:async()=>{}},action=async(_label:string,run:()=>Promise<any>)=>run(),openProviders=async()=>{},setConnectionNotice=noop;
 __CALLBACKS__
 (window as any).qa={calls,external:external.id,known:known.id,id,
  state:()=>({project:project()?.id,projects:projects().map(p=>p.id),all_projects:allProjects().map(p=>p.id),draft:prompt(),error:error(),scope:threadScope()?.conversation_ref.id,source_generation:rawScope.sources[0].original_source.generation,owner_generation:originalSession()?.generation}),
  checkUnbound:async()=>{const previous=threadScope(),priorOwner=originalSession(),scope={...rawScope,project_id:null},value={...owner,project_id:internal.id,provider_profile_ref:providerRef,active_operation_id:null};setThreadScope(scope);setOriginalSession(value);const before=calls.filter(c=>c.kind==='unexpected-owner-open').length;try{return{internal_present:allProjects().some(p=>p.id===internal.id),internal_hidden:!projects().some(p=>p.id===internal.id),in_project:originalInCurrentProject(value,scope),in_target:actualSendTarget(value,scope),ready:await ensureOriginalForSend(scope,prompt(),[],'plan'),extra_owner_opens:calls.filter(c=>c.kind==='unexpected-owner-open').length-before};}finally{setThreadScope(previous);setOriginalSession(priorOwner);}},
  evict:()=>setProjects([known,internal]),defer:(kind:string)=>deferKind=kind,pending:()=>!!releaseRead,
  release:()=>releaseRead?.(),deleted:()=>backendProjects=[known,internal],
  away:()=>{saveComposerDraft();nav++;activityRequest++;discussionDraftLease=undefined;setSelectedDiscussion(undefined);setProject(known);setPrompt('Other navigation draft');},
  stored:()=>localStorage.getItem('eeas-conversation-draft-v1:'+id)};
 return <div style={{display:'flex',gap:'20px',height:'700px'}}><ConversationSidebar items={historyItems()} projectCatalog={projects()} currentProjectId={project()?.id} activeId={historyActiveId()} locale="en" loaded showFiles={false} onOpen={row=>openHistoryConversation(historyItems().find(item=>item.id===row.id)!)}/><main><strong data-current-project>{project()?.name}</strong><textarea aria-label="Current message" value={prompt()} onInput={e=>setPrompt(e.currentTarget.value)}/><p role="alert">{error()}</p></main></div>;
}
render(()=> <Fixture/>,document.getElementById('app')!);
'''


@contextmanager
def history_app(tmp_path):
    app = R / 'apps/agent-shell'
    root = tmp_path / 'history-app'
    root.mkdir()
    source_path = Path(os.environ.get('EEAS_HISTORY_PROJECT_SOURCE', app / 'src/App.tsx'))
    source = source_path.read_text()
    callbacks = source[source.index('  function finishHistorySelection('):source.index('  async function openThreadDiscoveryDocument(')]
    callbacks += source[source.index('  async function installThread('):source.index('  async function openLegacyHistoryConversation(')]
    callbacks += source[source.index('  function originalInCurrentProject('):source.index('  async function inspectOriginalConnection(')]
    sender = source[source.index('  async function submitOriginalPrompt('):source.index('  function hasComposerFile(')]
    target = next(line.strip() for line in sender.splitlines() if line.strip().startswith('const inTarget='))
    callbacks += '\nfunction actualSendTarget(value:any,scope:any){'+target+'return !!inTarget;}\n'
    fixture = TEMPLATE.replace('__SRC__', '/@fs/' + str(app / 'src')).replace('__CALLBACKS__', callbacks)
    (root / 'fixture.tsx').write_text(fixture)
    (root / 'index.html').write_text('<!doctype html><meta charset="utf-8"><div id="app"></div><script type="module" src="/fixture.tsx"></script>')
    config = {'root':str(root),'cacheDir':str(root/'cache'),'server':{'host':'127.0.0.1','hmr':False,'strictPort':True,'fs':{'allow':[str(R),str(tmp_path)]}},'resolve':{'dedupe':['solid-js'],'alias':{'solid-js':str(app/'node_modules/solid-js')}}}
    (root/'vite.config.mjs').write_text('import solid from '+json.dumps(str(app/'node_modules/vite-plugin-solid/dist/esm/index.mjs'))+';export default '+json.dumps(config).replace('"root":','"plugins":[solid()],"root":',1)+';')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    log=(root/'vite.log').open('w')
    proc=subprocess.Popen([str(R/'.node-tools/node_modules/node/bin/node'),str(app/'node_modules/vite/bin/vite.js'),'--config',str(root/'vite.config.mjs'),'--port',str(port)],cwd=root,env={**os.environ,'TMPDIR':str(tmp_path),'NODE_COMPILE_CACHE':str(tmp_path/'node-cache')},stdout=log,stderr=subprocess.STDOUT)
    try:
        end=time.monotonic()+30
        while True:
            assert proc.poll() is None, 'Own Vite exited; inspect log'
            try:
                urllib.request.urlopen('http://127.0.0.1:'+str(port),timeout=1).close()
                break
            except OSError:
                if time.monotonic()>end:
                    raise
                time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                yield browser,'http://127.0.0.1:'+str(port),{'App_sha256':hashlib.sha256(source.encode()).hexdigest(),'callbacks_sha256':hashlib.sha256(callbacks.encode()).hexdigest()}
            finally:
                browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()
        assert proc.poll() is not None


def test_actual_main_reopens_api_created_project_and_preserves_draft_and_stale_navigation(tmp_path):
    with history_app(tmp_path) as (browser,url,pins):
        p=browser.new_page(viewport={'width':1280,'height':820})
        p.set_default_timeout(10000)
        errors=[]
        p.on('pageerror',lambda error:errors.append(str(error)))
        failed=[]
        p.on('requestfailed',lambda request:failed.append({'url':request.url,'failure':request.failure}))
        try:
            p.goto(url,wait_until='domcontentloaded',timeout=60000)
            p.wait_for_function('!!window.qa',timeout=30000)
        except Exception:
            (tmp_path/'BROWSER_SETUP.raw.json').write_text(json.dumps({'page_errors':errors,'failed_requests':failed},indent=2))
            raise
        row=p.locator('.as-conversation-row')
        records=[]
        decisions=[]
        def unbound_owner():
            decision=p.evaluate('window.qa.checkUnbound()')
            decisions.append(decision)
            (tmp_path/'UNBOUND_OWNER_DECISIONS.raw.json').write_text(json.dumps({'pins':pins,'decisions':decisions,'scope':'Actual originalInCurrentProject, ensureOriginalForSend and submitOriginalPrompt inTarget expression; no actual owner or message opened'},indent=2))
            assert decision=={'internal_present':True,'internal_hidden':True,'in_project':True,'in_target':True,'ready':True,'extra_owner_opens':0}, 'Navigation must retain the complete internal-owner catalog without opening another owner'
        def settled():
            expect(row).not_to_have_attribute('aria-busy','true')
            state=p.evaluate('window.qa.state()')
            records.append(state)
            (tmp_path/'HISTORY_PROJECT_BROWSER.raw.json').write_text(json.dumps({'pins':pins,'states':records,'calls':p.evaluate('window.qa.calls'),'page_errors':errors,'scope':'Actual App callbacks/sidebar/draft leases with fixture transports; not Windows/shared App'},ensure_ascii=False,indent=2))
            return state
        try:
            p.locator('[data-project-toggle]').filter(has_text='API-created project').click()
            row.click()
            first=settled()
            assert first['project']=='api-created-project' and first['owner_generation']==2
            assert first['source_generation']==1
            unbound_owner()
            p.get_by_label('Current message').fill('保留目标会话草稿 😀')
            p.evaluate('window.qa.inputNode=document.querySelector("textarea")')
            row.click()
            reopened=settled()
            assert reopened['error']=='', 'Same-project reopen must not reject a project omitted from the old cache'
            assert reopened['draft']=='保留目标会话草稿 😀'
            assert 'api-created-project' in reopened['projects']
            unbound_owner()
            assert p.evaluate('window.qa.inputNode===document.querySelector("textarea")')
            assert p.evaluate('window.qa.calls.filter(c=>c.kind==="projects").length')==1, 'Committed first open must populate the cache'
            p.evaluate('window.qa.evict()')
            row.click()
            recovered=settled()
            assert recovered['error']=='' and recovered['draft']=='保留目标会话草稿 😀'
            assert 'api-created-project' in recovered['projects']
            unbound_owner()
            assert p.evaluate('window.qa.calls.filter(c=>c.kind==="projects").length')==2
            for kind in ('projects','workspaces'):
                p.evaluate('(kind)=>{window.qa.evict();window.qa.defer(kind)}',kind)
                row.click()
                p.wait_for_function('window.qa.pending()')
                p.evaluate('window.qa.away();window.qa.release()')
                stale=settled()
                assert stale['project']=='known-project' and stale['projects']==['known-project']
                assert stale['all_projects']==['known-project','original-internal-project']
                assert stale['draft']=='Other navigation draft' and stale['error']==''
            p.evaluate('window.qa.deleted()')
            row.click()
            deleted=settled()
            assert deleted['project']=='known-project' and deleted['projects']==['known-project']
            assert deleted['all_projects']==['known-project','original-internal-project']
            assert deleted['draft']=='Other navigation draft'
            assert deleted['error']=='会话暂时无法打开，当前草稿保留。'
            assert json.loads(p.evaluate('window.qa.stored()'))['text']=='保留目标会话草稿 😀'
            assert not errors, errors
        finally:
            p.screenshot(path=str(tmp_path/'history-project-final.png'))
            p.close()
