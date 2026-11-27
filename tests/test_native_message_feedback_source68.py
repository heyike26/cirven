"""Actual native-message row, original GET handler and composer in isolated Chromium."""
import asyncio
import json
import os
from legacy_evidence import evidence_folder, chromium_executable
from pathlib import Path
import subprocess
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / '.node-tools/node_modules/node/bin/node'
CHROMIUM = chromium_executable()
STYLES = ['native-composer.css', 'styles.css', 'native-collaboration.css', 'agent-studio.css', 'brand-and-spacing.css', 'chat-workbench-layout.css']
COMPILER = r"""
import fs from'node:fs';import path from'node:path';import{createRequire}from'node:module';import{createHash}from'node:crypto';
const root=process.env.EEAS_REPO,app=path.join(root,'apps/agent-shell'),out=process.env.EEAS_EVIDENCE_DIR,variant=process.env.EEAS_UI_VARIANT??'after',baseline=process.env.EEAS_UI_BASELINE,req=createRequire(path.join(app,'package.json')),ts=req('typescript'),babel=req('@babel/core'),esbuild=req('esbuild'),preset=(await import(path.join(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js'))).default;
const sourcePath=process.env.EEAS_UI_APP_SOURCE??path.join(variant==='before'?baseline:root,'apps/agent-shell/src/App.tsx'),source=fs.readFileSync(sourcePath,'utf8'),tree=ts.createSourceFile(sourcePath,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),nodes=[];function walk(n){nodes.push(n);ts.forEachChild(n,walk);}walk(tree);
const fn=name=>{const found=nodes.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name);if(!found)throw Error('Missing actual function '+name);return found.getText(tree);};
const row=nodes.find(n=>ts.isJsxElement(n)&&n.openingElement.getText(tree)==='<For each={nativeMessageRows()}>').getText(tree),area=nodes.find(n=>ts.isJsxSelfClosingElement(n)&&n.tagName.getText(tree)==='textarea'&&n.getText(tree).includes('描述要完成的工作')).getText(tree);
const program=`import{render}from'solid-js/web';import{createSignal,createResource,Show,For}from'solid-js';import{Icon}from'${root}/staging-native-ui/upstream/packages/ui/src/components/icon.tsx';import TaskStatus from'${app}/src/TaskStatus.tsx';import AssistantReply from'${app}/src/AssistantReply.tsx';import NativeReviewActions from'${app}/src/NativeReviewActions.tsx';import NativeMessageArtifacts from'${app}/src/NativeMessageArtifacts.tsx';import NativePageConversation from'${app}/src/NativePageConversation.tsx';import{stableItems}from'${app}/src/stable-items.ts';import{authoredText,authoredErrorCopy}from'${app}/src/authored-ui-copy.ts';import{startUILanguage,t as uiText}from'${app}/src/ui-language.ts';import{NativeMessageClient,nativeInspectionFailure,nativeMessagePending}from'${app}/src/native-collaboration.ts';import{ordinaryControlScope,ordinaryControlSummaries,controlNotice}from'${app}/src/native-message-guidance.ts';${variant==='after'?`import NativeMessageFeedback from'${app}/src/NativeMessageFeedback.tsx';`:''}
import NativeContextRecovery from'${app}/src/NativeContextRecovery.tsx';import NativeActionProposals from'${app}/src/NativeActionProposals.tsx';import {NativeContextRecoveryClient} from'${app}/src/native-context-recovery.ts';import {verifiedFeedbackDisplay} from'${app}/src/native-feedback-api.ts';import {currentNativeReference} from'${app}/src/native-collaboration.ts';import {uiLocale} from'${app}/src/ui-language.ts';
function Fixture(){startUILanguage();const nativeRecoveryClient=new NativeContextRecoveryClient({},window.localStorage),nativeRecoveryVersion=()=>0,currentNativeContext=()=>undefined,captureNativeRecoveryCurrent=()=>()=>true,openNativeRecoveredChat=async()=>false;const feedbackDisplays=()=>[],messagePreviewTargets=()=>[];
${['NativeUserInput','NativeAssistantResult'].filter(name=>source.includes('function '+name+'(')).map(fn).join('\n')}
let historyGeneration=1,cid='qa-original-conversation',pid='qa-project',posts=0,heldResolve;const calls=[],opens=[],outcomes=new Map();
 const keyA='aaaaaaaa-1111-4111-8111-111111111111',keyB='bbbbbbbb-2222-4222-8222-222222222222',raw='RAW_BACKEND_68 '+ '长诊断 Long diagnostic '.repeat(1500)+' END_OF_RAW_DIAGNOSTIC_68',userSummary="用户原文 Error invoking remote method 'eeas:user-content': KEEP_SUMMARY_68";
 const originScope={project_id:pid,conversation_id:cid},pendingReview={review_ref:{id:'original-review-68',revision:1,sha256:'a'.repeat(64)},phase:'WAITING_QUESTION',pending_questions:[{kind:'QUESTION',request_ref:{id:'original-question-68',generation:1,sha256:'b'.repeat(64)},native_request:{questions:[{question:'Original unanswered question 原问题',options:[{label:'Keep chosen answer 保留选项'}],multiple:true,custom:true}]},allowed_choices:[],reply_is_execution_authorization:false}]};
 const initialRows=()=>[{input:'Original first question 原问题',message:{...originScope,operation_id:keyA,status:'UNKNOWN_OUTCOME',error:{message:raw},reason:'ORIGINAL_REASON_68',execution:{session_id:'INTERNAL_SESSION_68'},file_review_status:'CURRENT'}},{input:'Original second question 原问题',message:{...originScope,operation_id:keyB,status:'INSPECTION_UNAVAILABLE',error:{message:raw+' SECOND'}}}];
 const [nativeMessages,setNativeMessages]=createSignal(initialRows());
 const nativeMessageRows=stableItems(nativeMessages,row=>JSON.stringify([row.message.project_id??null,row.message.conversation_id??null,row.message.operation_id]));
 const [prompt,setPrompt]=createSignal("未发送草稿 Error invoking remote method 'eeas:user-input': 保留"),[busy,setBusy]=createSignal(''),[error,setError]=createSignal(''),[notice,setNotice]=createSignal('');
 const project=()=>({id:pid}),conversationId=()=>cid,message=e=>e instanceof Error?e.message:String(e),uiCopyText=x=>authoredText(x,uiText),nativeBridge={inspectNativeMessage:async id=>{calls.push({method:'GET',id});if(window.qaHeld)await new Promise(resolve=>heldResolve=resolve);const mode=outcomes.get(id)??(id===keyA?'404':'ipc');if(mode==='404')throw Object.assign(Error(raw),{status:404,code:'NOT_FOUND'});if(mode==='ipc')throw Error("Error invoking remote method 'eeas:inspect-native-message': "+raw);return {...originScope,operation_id:id,status:mode==='pending'?'RUNNING':mode==='success'?'RETURNED':'FAILED',...(mode==='pending'?{review:pendingReview}:mode==='success'?{text:'Actual reply 原回复 **KEEP_REPLY_68**'}:{summary:userSummary,error:{message:raw}})};},submitNativeMessage:async()=>{posts++;throw Error('No send allowed');}},nativeClient=new NativeMessageClient(nativeBridge);
 ${['action','nativeRecord','nativeReplyText','nativeLocalNotice','inspectNativeOperation'].map(fn).join('\n')}
 function persistNativeHistory(){}function forgetNativeMessage(){}function observeNativeMessage(){}function dispatchOrdinaryGuidance(){throw Error('No guidance control in this fixture');}
 const localStorage=window.localStorage,quickBridge=undefined,savedQuickReply=()=>null,viewSavedQuickReply=()=>{},openProviders=async()=>opens.push('providers'),canStopNativeReview=()=>false,cancelNativeMessage=async()=>{posts++;},beginPageGuidance=()=>{},continueCapabilityTask=()=>{},controlPageReview=async()=>{posts++;},openAssistantLink=async(_m,href)=>opens.push(href),nativeArtifactAvailable=()=>false,openNativeArtifact=async()=>{},reworkRecovery={restore(){}},nativeReworkError=()=>'',windowMode=()=>'workbench',composerView=()=>({focused:true,folded:false}),composerPresentation={focus(){}},submitPrompt=async()=>{posts++;};
 window.qa={keyA,keyB,raw,userSummary,state:()=>({calls:[...calls],posts,opens:[...opens],error:error(),statuses:nativeMessages().map(r=>({id:r.message.operation_id,status:r.message.status,project_id:r.message.project_id??null,conversation_id:r.message.conversation_id??null,hasReview:!!r.message.review})),draft:prompt()}),outcome:(key,mode)=>outcomes.set(key,mode),hold:()=>{window.qaHeld=true;},release:()=>{window.qaHeld=false;heldResolve?.();},scope:()=>{cid='new-conversation';pid='other-project';historyGeneration++;},publish:(id,value)=>nativeRecord('',{...originScope,operation_id:id,...value}),seedPending:()=>nativeRecord('',{...originScope,operation_id:keyA,status:'UNKNOWN_OUTCOME',review:pendingReview}),reset:()=>{setNativeMessages(initialRows());calls.length=0;outcomes.clear();}};
 return <div class="as-app-frame"><div class="as-shell" data-layout="workbench" data-chat-layout="adaptive" data-window-mode="workbench" data-fullscreen="false" data-agent-hidden="false" data-workbench-hidden="true" data-files-hidden="true" data-mobile-view="agent" data-compact={String(window.matchMedia('(max-width:920px)').matches)}><aside class="as-sidebar"/><div class="as-pane-separator" data-kind="files"/><main class="as-workbench"/><div class="as-pane-separator" data-kind="agent"/><aside class="as-agent"><div class="as-agent-content"><header class="as-agent-header"><strong>QA · Conversation</strong></header><main class="as-conversation"><div id="qa-history"><For each={[0,1,2,3,4,5]}>{n=><article id={'reading-'+n}><p>Original reading paragraph 原创阅读段落 {n} · Keep the nonzero reading position.</p></article>}</For></div>${row}</main><form class="as-composer" onSubmit={e=>{e.preventDefault();posts++;}}><NativePageConversation input={${area}} toolbar={<button type="submit">QA send</button>}/></form><div id="qa-error">{authoredText(error(),uiText)}</div></div></aside></div></div>;
}render(Fixture,document.getElementById('root'));`;
const used=new Set([sourcePath]),pin=p=>{const b=fs.readFileSync(p);return{path:p,bytes:b.length,sha256:createHash('sha256').update(b).digest('hex')};},plugin={name:'source68-actual-row',setup(b){b.onLoad({filter:/\.tsx$/},({path:p})=>{used.add(p);return{contents:babel.transformSync(ts.transpileModule(fs.readFileSync(p,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText,{filename:p+'.jsx',presets:[[preset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:path.dirname(p)};});}};
fs.mkdirSync(out,{recursive:true});const entry=path.join(out,'fixture.tsx');fs.writeFileSync(entry,program);try{const result=await esbuild.build({entryPoints:[entry],outfile:path.join(out,'fixture.js'),bundle:true,platform:'browser',format:'iife',conditions:['browser'],nodePaths:[path.join(app,'node_modules')],plugins:[plugin],loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"production"'},metafile:true,logLevel:'silent'});for(const n of Object.keys(result.metafile.inputs))used.add(path.resolve(n));fs.writeFileSync(path.join(out,'COMPILE_INPUTS.json'),JSON.stringify({variant,source:pin(sourcePath),inputs:[...used].map(pin),fixture:pin(entry),bundle:pin(path.join(out,'fixture.js')),scope:'Actual App native row/query handler/textarea and actual components; bridge responses only; no full App/server/Windows/model/Vite.'},null,2)+'\n');}finally{esbuild.stop();}
"""
CLIP = r'''n=>{const r=n.getBoundingClientRect();let l=Math.max(0,r.left),t=Math.max(0,r.top),rr=Math.min(innerWidth,r.right),bb=Math.min(innerHeight,r.bottom),hidden=false;for(let p=n;p;p=p.parentElement){const s=getComputedStyle(p);if(p.hidden||p.inert||s.display==='none'||s.visibility==='hidden')hidden=true;if(p!==n){const b=p.getBoundingClientRect();if(/auto|scroll|hidden|clip/.test(s.overflowX)){l=Math.max(l,b.left);rr=Math.min(rr,b.right);}if(/auto|scroll|hidden|clip/.test(s.overflowY)){t=Math.max(t,b.top);bb=Math.min(bb,b.bottom);}}}return{width:r.width,height:r.height,visibleWidth:hidden?0:Math.max(0,rr-l),visibleHeight:hidden?0:Math.max(0,bb-t)};}'''


def test_native_message_feedback_original_reads_focus_and_draft():
    asyncio.run(run())


async def scoped_identity(page, a, b, key_a):
    await page.evaluate('()=>qa.seedPending()')
    custom = a.locator('.as-native-review-actions input:not([type])')
    await custom.type('Unsubmitted custom answer 未提交回答')
    await a.locator('.as-native-review-actions input[type=checkbox]').check()
    await a.evaluate('(n)=>window.originalScopedRow=n')
    await a.locator('.as-native-feedback').evaluate('(n)=>window.originalScopedFeedback=n')
    await a.locator('.as-native-feedback button').evaluate('(n)=>window.originalScopedTrigger=n')
    await a.locator('.as-native-feedback button').click()
    await page.wait_for_function('()=>!document.querySelector(".as-native-feedback button[aria-busy=true]")')
    await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
    first = await a.evaluate('(n)=>({sameRow:n===window.originalScopedRow,sameFeedback:n.querySelector(".as-native-feedback")===window.originalScopedFeedback,sameTrigger:n.querySelector(".as-native-feedback button")===window.originalScopedTrigger,focused:document.activeElement===window.originalScopedTrigger})')
    failed = next(x for x in (await page.evaluate('()=>qa.state()'))['statuses'] if x['id'] == key_a)
    assert failed['status'] == 'NO_RECORD' and not failed['hasReview'], failed
    assert await a.locator('.as-native-review-actions').count() == 0
    # Only a subsequent exact GET may restore the original pending request; no old permission is carried by the error projection.
    await page.evaluate('()=>qa.outcome(qa.keyA,"pending")')
    await a.locator('.as-native-feedback button').click()
    await custom.wait_for()
    recovered = {'custom': await custom.input_value(), 'checked': await a.locator('.as-native-review-actions input[type=checkbox]').is_checked()}
    await b.locator('.as-native-feedback button').evaluate('(n)=>window.originalScopedIPCTrigger=n')
    await b.locator('.as-native-feedback button').click()
    await page.wait_for_function('()=>!document.querySelector(".as-native-feedback button[aria-busy=true]")')
    await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
    ipc = await b.locator('.as-native-feedback button').evaluate('(n)=>({sameTrigger:n===window.originalScopedIPCTrigger,focused:document.activeElement===n})')
    state = await page.evaluate('()=>qa.state()')
    result = {'phase': 'scoped_row_identity', 'after_404': first, 'failure_scope': failed, 'recovered_pending_draft': recovered, 'after_IPC': ipc, 'reads': state['calls'], 'posts': state['posts'], 'review_not_retained_on_failed_read': True}
    await page.screenshot(path=str(evidence_folder('native_message_feedback_source68') / f'{await page.evaluate("()=>document.documentElement.lang||navigator.language")}-scoped-pending.png'))
    return result


async def run():
    out = evidence_folder('native_message_feedback_source68');out.mkdir(parents=True,exist_ok=True)
    compiler=out/'compile-fixture.mjs';compiler.write_text(COMPILER)
    result=subprocess.run([str(NODE),str(compiler)],cwd=ROOT,env={**os.environ, 'EEAS_EVIDENCE_DIR':str(out),'EEAS_REPO':str(ROOT),'TMPDIR':str(out),'NODE_COMPILE_CACHE':str(out/'node-cache')},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    (out/'compile.stdout').write_bytes(result.stdout);(out/'compile.stderr').write_bytes(result.stderr);assert result.returncode==0,result.stderr.decode()
    variant=os.environ.get('EEAS_UI_VARIANT','after');observations=[]
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True,executable_path=str(CHROMIUM))
            try:
                for locale,width,height in [('zh-CN',360,600),('en',1060,680)]:
                    context=await browser.new_context(viewport={'width':width,'height':height},locale=locale);page=await context.new_page();page.set_default_timeout(6000);errors=[];outside=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    async def route(r):
                        path=urlparse(r.request.url).path
                        if r.request.url=='http://127.0.0.1:17668/':await r.fulfill(body='<!doctype html><html><body><div id="root"></div></body></html>',content_type='text/html')
                        elif path in ['/web/assets/fonts/manrope-variable.woff2','/web/assets/fonts/noto-sans-sc-variable.woff2']:await r.fulfill(body=(ROOT/'apps'/path.removeprefix('/')).read_bytes(),content_type='font/woff2')
                        else:outside.append(r.request.url);await r.abort()
                    await page.route('**/*',route);await page.goto('http://127.0.0.1:17668/');await page.evaluate('(l)=>localStorage.setItem("eeas.ui-language.v1",l)',locale)
                    for name in STYLES:await page.add_style_tag(content=(ROOT/'apps/agent-shell/src'/name).read_text())
                    await page.add_script_tag(path=str(out/'fixture.js'));await asyncio.wait_for(page.evaluate('()=>document.fonts.ready'),timeout=10);await page.evaluate('()=>Promise.all(document.getAnimations().filter(a=>a.effect?.getComputedTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})))')
                    assert not errors,errors
                    key_a=await page.evaluate('()=>qa.keyA');key_b=await page.evaluate('()=>qa.keyB');a=page.locator(f'[data-operation-id="{key_a}"]');b=page.locator(f'[data-operation-id="{key_b}"]');assert await a.count()==await b.count()==1
                    collapsed=await page.locator('.as-conversation').inner_text()
                    if variant=='before':
                        counter={'original_uuid_visible':key_a in collapsed,'technical_session_visible':'INTERNAL_SESSION_68' in collapsed,'raw_error_visible':'RAW_BACKEND_68' in collapsed}
                        assert all(counter.values());await a.scroll_into_view_if_needed();await page.screenshot(path=str(out/f'{locale}-source67-counter.png'));observations.append({'locale':locale,'counterexample':counter});await context.close();continue
                    scoped = await scoped_identity(page, a, b, key_a);observations.append({'locale':locale, **scoped})
                    assert all(scoped['after_404'].values()) and all(scoped['after_IPC'].values()) and scoped['recovered_pending_draft']=={'custom':'Unsubmitted custom answer 未提交回答','checked':True}, scoped
                    assert scoped['failure_scope']['project_id']=='qa-project' and scoped['failure_scope']['conversation_id']=='qa-original-conversation' and scoped['posts']==0
                    if os.environ.get('EEAS_UI_SCOPE_ONLY')=='1':await context.close();continue
                    await page.evaluate('()=>qa.reset()')
                    collapsed=await page.locator('.as-conversation').inner_text()
                    assert key_a not in collapsed and key_b not in collapsed and 'RAW_BACKEND_68'not in collapsed and 'INTERNAL_SESSION_68'not in collapsed
                    field=page.locator('.as-composer textarea');await field.evaluate('(n)=>{window.originalField=n;n.focus();n.setSelectionRange(3,12);}');draft=await field.input_value()
                    await a.scroll_into_view_if_needed();await page.screenshot(path=str(out/f'{locale}-collapsed.png'))
                    await a.locator('summary').click();pre=a.locator('pre').first;assert await pre.inner_text()==await page.evaluate('()=>qa.raw');assert key_a in await a.inner_text()and 'INTERNAL_SESSION_68'in await a.inner_text();assert await a.locator('pre').nth(1).inner_text()=='ORIGINAL_REASON_68'
                    await pre.evaluate('(n)=>n.scrollTop=n.scrollHeight');await pre.scroll_into_view_if_needed();box=await pre.evaluate(CLIP);assert box['visibleHeight']>=box['height']-1 and box['visibleWidth']>=box['width']-1,box
                    terminal=await pre.evaluate('(n)=>{const t=n.firstChild,s=t.textContent.lastIndexOf("END_OF_RAW_DIAGNOSTIC_68"),r=document.createRange();r.setStart(t,s);r.setEnd(t,s+24);const b=r.getBoundingClientRect(),p=n.getBoundingClientRect();return{top:b.top,bottom:b.bottom,preTop:p.top,preBottom:p.bottom};}');assert terminal['top']>=terminal['preTop']-1 and terminal['bottom']<=terminal['preBottom']+1,terminal
                    await page.screenshot(path=str(out/f'{locale}-details-end.png'));await a.locator('summary').click()
                    for card,key in [(b,key_b),(a,key_a)]:
                        button=card.locator('.as-native-feedback button');await button.evaluate('(n)=>window.originalInspectButton=n');await button.click();await page.wait_for_function('()=>!document.querySelector(".as-native-feedback button[aria-busy=true]")');await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');focus=await button.evaluate('(n)=>({same:n===window.originalInspectButton,focused:document.activeElement===n,active:document.activeElement.tagName})');observations.append({'phase':'original_query_focus','key':key,**focus});assert focus['same']and focus['focused'],focus
                    state=await page.evaluate('()=>qa.state()');assert [x['id']for x in state['calls']]==[key_b,key_a]and state['posts']==0 and state['error']==''
                    assert {x['id']:x['status']for x in state['statuses']}=={key_a:'NO_RECORD',key_b:'INSPECTION_UNAVAILABLE'}
                    assert await field.evaluate('(n)=>n===window.originalField&&n.selectionStart===3&&n.selectionEnd===12')and await field.input_value()==draft
                    await page.evaluate('()=>qa.outcome(qa.keyA,"success")');button=a.locator('.as-native-feedback button');await button.click();await page.wait_for_function('()=>document.activeElement?.hasAttribute("data-native-feedback-body")');assert 'KEEP_REPLY_68'in await a.inner_text()and await a.locator('.as-native-feedback button').count()==0
                    # A delayed success while typing cannot steal focus or move the nonzero reading anchor.
                    await page.evaluate('()=>{qa.outcome(qa.keyB,"success");qa.hold();}');await b.locator('.as-native-feedback button').click();await field.focus();await field.evaluate('(n)=>n.setSelectionRange(n.value.length,n.value.length)');await field.type(' keep 中文')
                    await field.dispatch_event('compositionstart',{'data':'中'});await field.dispatch_event('keydown',{'key':'Enter','ctrlKey':True,'isComposing':True});await field.dispatch_event('compositionend',{'data':'中文'})
                    before=await page.evaluate('()=>{const n=document.querySelector(".as-conversation");n.scrollTop=180.5;const a=[...n.querySelectorAll("#qa-history article")].find(a=>a.getBoundingClientRect().bottom>n.getBoundingClientRect().top+4);window.qaAnchor=a;return{scroll:n.scrollTop,id:a?.id,offset:a?.getBoundingClientRect().top-n.getBoundingClientRect().top};}')
                    await page.evaluate('()=>qa.release()');await page.wait_for_function('()=>!document.querySelector(".as-native-feedback button[aria-busy=true]")');await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');after=await page.evaluate('()=>{const n=document.querySelector(".as-conversation"),a=window.qaAnchor;return{scroll:n.scrollTop,id:a?.id,offset:a?.getBoundingClientRect().top-n.getBoundingClientRect().top};}')
                    assert before['scroll']>0 and before==after,(before,after);assert await field.evaluate('(n)=>n===window.originalField&&document.activeElement===n')and await field.input_value()==draft+' keep 中文'
                    await page.evaluate('()=>qa.publish(qa.keyB,{status:"FAILED",summary:qa.userSummary,error:{message:qa.raw},file_review_status:"STALE_OR_UNAVAILABLE"})');assert await b.locator('[data-native-feedback-body]').inner_text()==await page.evaluate('()=>qa.userSummary');assert await b.locator('.as-inline-warning').is_visible()
                    await page.evaluate('()=>qa.publish(qa.keyB,{status:"NOT_CONFIGURED"})');model=b.locator('.as-connection-recovery');await model.scroll_into_view_if_needed();assert await model.is_visible()
                    # Original row remains untouched when a held GET fails after scope/generation changes.
                    await page.evaluate('()=>{qa.publish(qa.keyA,{status:"UNKNOWN_OUTCOME"});qa.outcome(qa.keyA,"ipc");qa.hold();}');await a.locator('.as-native-feedback button').click();await field.focus();await page.evaluate('()=>qa.scope()');await page.evaluate('()=>qa.release()');await page.wait_for_function('()=>!document.querySelector(".as-native-feedback button[aria-busy=true]")')
                    final=await page.evaluate('()=>qa.state()');assert {x['id']:x['status']for x in final['statuses']}[key_a]=='UNKNOWN_OUTCOME'and final['error']==''and final['posts']==0
                    assert await field.evaluate('(n)=>n===window.originalField&&document.activeElement===n')and await field.input_value()==draft+' keep 中文'
                    assert not errors and not outside,(errors,outside);await page.screenshot(path=str(out/f'{locale}-final.png'))
                    observations.append({'locale':locale,'viewport':[width,height],'collapsed_diagnostics_hidden':True,'long_detail_clip':box,'terminal':terminal,'original_reads':final['calls'],'posts':final['posts'],'explicit_success_focus':True,'background_success_retained_input_focus':True,'same_textarea_draft_selection_IME':True,'reading_before':before,'reading_after':after,'summary_verbatim':True,'stale_source_warning_visible':True,'missing_model_action_visible':True,'late_scope_error_did_not_mutate_row':True})
                    await context.close()
            finally:await browser.close()
    finally:(out/'OBSERVATIONS.json').write_text(json.dumps({'variant':variant,'observations':observations,'scope':'Isolated actual production fragments, controlled bridge; not fullApp/native/real backend or IME OS qualification'},ensure_ascii=False,indent=2)+'\n')
