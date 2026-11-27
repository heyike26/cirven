"""Actual original-export action and delivery components, with a deferred HTTP port.

This is local renderer evidence, not execution in the Windows App.
"""
import asyncio
import hashlib
import json
import os
from legacy_evidence import evidence_folder
from pathlib import Path
import subprocess
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

from test_partial_delivery_report_browser import ROOT, NODE, CHROMIUM, STYLES, CLIP


COMPILER = r"""
import fs from 'node:fs';import path from 'node:path';import{createRequire}from'node:module';import{createHash}from'node:crypto';
const root=process.env.EEAS_REPO,app=path.join(root,'apps/agent-shell'),out=process.env.EEAS_EVIDENCE_DIR,overlay=process.env.EEAS_RECOVERY_SOURCE;
const req=createRequire(path.join(app,'package.json')),ts=req('typescript'),babel=req('@babel/core'),esbuild=req('esbuild'),preset=(await import(path.join(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js'))).default;
const actual=p=>overlay&&p.startsWith(root+'/')&&fs.existsSync(path.join(overlay,path.relative(root,p)))?path.join(overlay,path.relative(root,p)):p;
const used=new Set(),pin=p=>{const raw=fs.readFileSync(p);return{path:p,bytes:raw.length,sha256:createHash('sha256').update(raw).digest('hex')};};
const appPath=actual(path.join(app,'src/App.tsx')),raw=fs.readFileSync(appPath,'utf8'),ast=ts.createSourceFile(appPath,raw,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);used.add(appPath);
const actions=[],declarations={},functions={};
function walk(n){
 if(ts.isCallExpression(n)&&n.expression.getText(ast)==='registry.registerAction'){
  const item=n.arguments[0];if(ts.isObjectLiteralExpression(item)&&item.properties.some(p=>ts.isPropertyAssignment(p)&&p.name.getText(ast)==='actionId'&&p.initializer.text==='delivery.export.resume'))actions.push(n.getText(ast));
 }
 if(ts.isVariableDeclaration(n)&&ts.isIdentifier(n.name)&&n.initializer){const name=n.name.text,text=n.getText(ast);if(['quickScopeValue','quickScope'].includes(name)||name==='scope'&&text.includes('projectId: project()?.id')){if(declarations[name])throw Error('Ambiguous App declaration '+name);declarations[name]=text;}}
 if(ts.isFunctionDeclaration(n)&&n.name?.text==='recordDeliveryFeed'){if(functions.recordDeliveryFeed)throw Error('Ambiguous feed function');functions.recordDeliveryFeed=n.getText(ast);}
 ts.forEachChild(n,walk);
}walk(ast);
if(actions.length!==1||Object.keys(declarations).length!==3||!functions.recordDeliveryFeed)throw Error('Actual App action/scope/feed not found uniquely');
fs.writeFileSync(path.join(out,'EXTRACTED_APP.json'),JSON.stringify({source:pin(appPath),actions,declarations,functions},null,2)+'\n');
const program=`import{render}from'solid-js/web';import{createSignal,createMemo,onCleanup}from'solid-js';
import PlanningDeliveryResult from'${app}/src/PlanningDeliveryResult.tsx';
import{PlanningDeliveryApi,resumableExport,deliveryComplete}from'${app}/src/planning-delivery.ts';
import{ApiError}from'${app}/src/api.ts';
import{deliveryFeedItem,upsertDeliveryFeed}from'${app}/src/delivery-recovery.ts';
import{associationScope,sameDiscussionScope}from'${app}/src/discussion-file-context.ts';
import{sameNativeReference}from'${app}/src/native-collaboration.ts';import{startUILanguage}from'${app}/src/ui-language.ts';
function Fixture(){onCleanup(startUILanguage());const originalWorkInputChoice=()=>undefined,contextChoice=()=>undefined;const hash='b'.repeat(64),reportId='a'.repeat(64),requestHash='d'.repeat(64),basis={id:'original-plan',revision:5,sha256:'c'.repeat(64)};
 const partial=(pid='report-project',operation='original-operation')=>({operation_id:operation,project_id:pid,proposal_ref:basis,request_sha256:requestHash,status:'FAILED_OR_PARTIAL',kind:'EXPORT',automatic_model_replay:false,export_resume:{action:'RESUME_EXPORT',operation_id:operation,project_id:pid,request_sha256:requestHash,proposal_ref:basis},steps:{report:{source_kind:'PROPOSAL',status:'CONFIRMED',proposal_ref:basis,artifact:{id:reportId,sha256:hash,filename:'用户已保存任务书_'+ 'Task-任务-'.repeat(8)+'.docx',byte_length:277597,media_type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',download_url:'/api/projects/'+pid+'/planning/proposals/'+basis.id+'/reports/'+reportId+'/download'}}},failures:[{reason:'ManualChangesConflict'}]});
 const initial=()=>({project:'report-project',workspace:'workspace-a',session:'retained-planning-session',conversation:'conversation-a',mode:'original',nav:0,value:partial()});
 const [state,setState]=createSignal(initial()),[feed,setFeed]=createSignal([deliveryFeedItem({projectId:'report-project',kind:'EXPORT',basis},partial(),true)]),[reports,setReports]=createSignal([]);
 const project=()=>({id:state().project}),workspace=()=>({id:state().workspace,project_id:state().project}),activeSession=()=>({id:state().session});
 const toolMode=()=>state().mode,conversationId=()=>state().conversation,threadScope=()=>undefined,originalSession=()=>undefined,providers=()=>[],providerId=()=>'',originalAgent=()=> 'build';let quickScopeGeneration=0;
 const ${declarations.scope};const ${declarations.quickScopeValue};const ${declarations.quickScope};
 const navigation={capture:()=>{const nav=state().nav;return()=>state().nav===nav;}};
 const calls=[],deferred=[],notices=[],dispatches=[];let recoveryStatus='PROCESSING',badObservation=false;const setNotice=value=>notices.push(value);
 const planningDeliveryApi=new PlanningDeliveryApi({projectPath:pid=>'/api/projects/'+pid,request:(url,method,body)=>{calls.push({url,method,body});if(method==='POST')return new Promise((resolve,reject)=>deferred.push({resolve,reject}));if(method==='GET')return Promise.resolve(recoveryStatus==='EXPORTED_CONFIRMED_PLAN_DERIVATIVES'?complete(badObservation):{...partial(),status:recoveryStatus,export_resume:recoveryStatus==='FAILED_OR_PARTIAL'?partial().export_resume:undefined,...(badObservation?{request_sha256:'e'.repeat(64)}:{})});throw Error('Unexpected method');}});
 ${functions.recordDeliveryFeed}
 let handler;const registry={registerAction:(_,run)=>handler=run};${actions[0]};
 const artifact=()=>({artifactId:state().value.operation_id,type:'planning-delivery',schemaVersion:1,revision:5,scope:scope(),availability:'IMPLEMENTED',data:state().value});
 const context={dispatch:(action,target,input)=>{dispatches.push({action,target:structuredClone(target),input});if(action==='delivery.export.resume')return handler(target,input);if(action==='delivery.report.open')return Promise.resolve({status:'OPEN_REQUESTED'});throw Error('Unexpected action '+action);}};
 const complete=(foreignMasked=false)=>{const value=partial(),file={path:'delivery/original/harness/README.md',sha256:hash,byte_length:10,file_source_ref:{kind:'local_file',project_id:'report-project',workspace_id:'workspace-a',binding_revision:1,binding_sha256:hash,path:'delivery/original/harness/README.md',revision:1,sha256:hash}},foreign={...file,file_source_ref:{...file.file_source_ref,project_id:'foreign-project'}};return {operation_id:value.operation_id,status:'EXPORTED_CONFIRMED_PLAN_DERIVATIVES',project_id:'report-project',proposal_ref:basis,report:value.steps.report,diagrams:[],artifacts:[],project_files:foreignMasked?[file]:[],harness:{files:[foreignMasked?foreign:file],version_directory:'delivery/original/harness',workspace_ref:{id:'workspace-a',revision:1,binding_sha256:hash}},version_directory:'delivery/original',manifest:{path:'delivery/original/manifest.json',sha256:hash},taskbook_ref:{taskbook_id:'original-taskbook',revision:1},file_checks:[{path:file.path,status:'CURRENT'}],basis_current:true,current_review_status:'CURRENT',historical_receipt_preserved:true,execution_authorized:false};};
 window.qa={snapshot:()=>({calls:structuredClone(calls),dispatches:structuredClone(dispatches),feed:structuredClone(feed()),reports:structuredClone(reports()),notices:[...notices],artifactScope:scope(),discussionScope:quickScope()}),settle:(index,kind='success')=>kind==='success'?deferred[index].resolve(complete()):deferred[index].reject(new ApiError(409,'BUSY','busy fixture')),reset:()=>{setState(initial());setFeed([deliveryFeedItem({projectId:'report-project',kind:'EXPORT',basis},partial(),true)]);setReports([]);notices.length=0;recoveryStatus='PROCESSING';badObservation=false;},switch:(kind)=>setState(s=>kind==='project'?{...s,project:'other-project',value:partial('other-project','other-operation')}:kind==='workspace'?{...s,workspace:'workspace-b'}:kind==='session'?{...s,session:'other-session'}:kind==='navigation'?{...s,nav:s.nav+1}:{...s,conversation:'conversation-b'}),recovery:(status,bad=false)=>{recoveryStatus=status;badObservation=bad;},mismatch:()=>handler({...artifact(),scope:{...scope(),sessionId:'wrong-session'}}).then(()=>false,()=>true)};
 return <main class="as-app-frame"><div class="as-object" style={{width:'100%','max-width':'760px',padding:'16px','margin-inline':'auto'}}><PlanningDeliveryResult value={state().value} artifact={artifact()} context={context}/><label>Unsent draft<textarea id="draft" style={{width:'100%'}} value="用户尚未发送的反馈"/></label></div></main>;
}render(Fixture,document.getElementById('root'));`;
const entry=path.join(out,'fixture.tsx');fs.writeFileSync(entry,program);
const plugin={name:'actual-original-export-recovery',setup(b){b.onLoad({filter:/\.(tsx?|json)$/},({path:p})=>{const source=actual(p);used.add(source);const raw=fs.readFileSync(source,'utf8');if(p.endsWith('.tsx'))return{contents:babel.transformSync(ts.transpileModule(raw,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText,{filename:p+'.jsx',presets:[[preset,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:path.dirname(p)};return{contents:raw,loader:p.endsWith('.json')?'json':'ts',resolveDir:path.dirname(p)};});}};
try{const result=await esbuild.build({entryPoints:[entry],outfile:path.join(out,'fixture.js'),bundle:true,platform:'browser',format:'iife',conditions:['browser'],nodePaths:[path.join(app,'node_modules')],plugins:[plugin],loader:{'.css':'empty'},alias:{'@opencode-ai/ui/icon':path.join(root,'staging-native-ui/upstream/packages/ui/src/components/icon.tsx')},define:{'process.env.NODE_ENV':'"production"'},metafile:true,logLevel:'silent'});for(const n of Object.keys(result.metafile.inputs))used.add(actual(path.resolve(n)));fs.writeFileSync(path.join(out,'COMPILE_INPUTS.json'),JSON.stringify({inputs:[...used].map(pin),bundle:pin(path.join(out,'fixture.js')),boundary:'Actual extracted App action/scopes/feed; actual HTTP client and delivery components; deferred HTTP port. Not Windows/App native/real API/model.'},null,2)+'\n');}finally{esbuild.stop();}
"""


def test_original_export_resume_action_and_result_scope():
    asyncio.run(run())


async def run():
    out = evidence_folder('original_delivery_resume_browser')
    out.mkdir(parents=True, exist_ok=True)
    compiler = out / 'compile-fixture.mjs'
    compiler.write_text(COMPILER)
    proc = subprocess.run([str(NODE), str(compiler)], cwd=ROOT,
                          env={**os.environ, 'EEAS_EVIDENCE_DIR':str(out), 'EEAS_REPO': str(ROOT), 'TMPDIR': str(out), 'NODE_COMPILE_CACHE': str(out/'node-cache')},
                          text=True, capture_output=True)
    (out/'compile.stdout').write_text(proc.stdout)
    (out/'compile.stderr').write_text(proc.stderr)
    assert proc.returncode == 0, proc.stderr
    observations = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
            try:
                for locale, width, height in [('zh-CN', 360, 760), ('en', 780, 650)]:
                    context = await browser.new_context(viewport={'width': width, 'height': height}, locale=locale)
                    page = await context.new_page()
                    page.set_default_timeout(5000)
                    errors, outside = [], []
                    page.on('pageerror', lambda e: errors.append(str(e)))

                    async def route(r):
                        path = urlparse(r.request.url).path
                        if r.request.url == 'http://127.0.0.1:17635/':
                            await r.fulfill(body='<!doctype html><html><body><div id="root"></div></body></html>', content_type='text/html')
                        elif path in ['/web/assets/fonts/manrope-variable.woff2', '/web/assets/fonts/noto-sans-sc-variable.woff2']:
                            await r.fulfill(body=(ROOT/'apps'/path.removeprefix('/')).read_bytes(), content_type='font/woff2')
                        else:
                            outside.append(r.request.url)
                            await r.abort()

                    await page.route('**/*', route)
                    await page.goto('http://127.0.0.1:17635/')
                    await page.evaluate('(l)=>localStorage.setItem("eeas.ui-language.v1",l)', locale)
                    for name in STYLES:
                        await page.add_style_tag(content=(ROOT/'apps/agent-shell/src'/name).read_text())
                    await page.add_script_tag(path=str(out/'fixture.js'))
                    await page.evaluate('()=>document.fonts.ready')
                    assert not errors, errors
                    resume = page.locator('.as-chat-delivery > button.as-text-action')
                    draft = page.locator('#draft')
                    await expect(resume).to_be_visible()
                    initial = await page.evaluate('()=>qa.snapshot()')
                    assert initial['artifactScope']['sessionId'] == 'retained-planning-session'
                    assert 'sessionId' not in initial['discussionScope'], 'Fixture must exercise actual original-mode scope distinction'
                    assert await page.evaluate('()=>qa.mismatch()') is True
                    assert not (await page.evaluate('()=>qa.snapshot()'))['calls']
                    assert await page.locator('pre,details[open]').count() == 0
                    await resume.scroll_into_view_if_needed()
                    clip = await resume.evaluate(CLIP)
                    assert clip['hit'] and clip['visibleWidth'] >= clip['width']-1 and clip['visibleHeight'] >= clip['height']-1, clip
                    assert await page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
                    await page.screenshot(path=str(out/f'{locale}-original-resume.png'))
                    draft_node = await draft.element_handle()
                    await resume.click()
                    await resume.evaluate('(n)=>n.click()')
                    snapshot = await page.evaluate('()=>qa.snapshot()')
                    assert len(snapshot['calls']) == 1
                    call = snapshot['calls'][0]
                    assert call == {'url': '/api/projects/report-project/planning-delivery/operations/original-operation/resume-export', 'method': 'POST', 'body': {'request_sha256': 'd'*64}}
                    await draft.fill('用户原稿与焦点保持')
                    await draft.focus()
                    await page.evaluate('()=>qa.settle(0)')
                    await expect(page.locator('.as-delivery-heading strong')).to_contain_text('完整交付文件已保存' if locale == 'zh-CN' else 'Complete delivery files saved')
                    assert await resume.count() == 0
                    assert await page.locator('.as-delivery-result > .as-inline-warning').count() == 0
                    assert await draft.input_value() == '用户原稿与焦点保持'
                    assert await draft_node.evaluate('(n)=>n===document.getElementById("draft")&&document.activeElement===n')
                    result = await page.evaluate('()=>qa.snapshot()')
                    assert len(result['feed']) == 1 and result['feed'][0]['id'] == initial['feed'][0]['id']
                    assert result['feed'][0]['status'] == 'EXPORTED_CONFIRMED_PLAN_DERIVATIVES'
                    assert result['reports'][0]['id'] == 'a'*64 and result['reports'][0]['sha256'] == 'b'*64
                    observations.append({'locale': locale, 'case': 'original_mode_actual_App_action_single_POST_original_SHA_parent_and_feed_complete_same_report_draft_focus', 'clip': clip})
                    for kind in ['conversation', 'session', 'workspace', 'project', 'navigation']:
                        await page.evaluate('()=>qa.reset()')
                        await expect(resume).to_be_enabled()
                        index = len([c for c in (await page.evaluate('()=>qa.snapshot()'))['calls'] if c['method'] == 'POST'])
                        await resume.click()
                        await page.evaluate('(kind)=>qa.switch(kind)', kind)
                        await page.evaluate('(index)=>qa.settle(index)', index)
                        await expect(resume).to_be_enabled()
                        state = await page.evaluate('()=>qa.snapshot()')
                        assert len(state['feed']) == 1 and state['feed'][0]['status'] == 'FAILED_OR_PARTIAL'
                        assert not state['reports'] and not state['notices']
                        assert await page.locator('.as-delivery-heading').count() == 0
                        observations.append({'locale': locale, 'case': 'late_result_ignored', 'changed': kind})
                    await page.evaluate('()=>qa.reset()')
                    await expect(resume).to_be_enabled()
                    index = len([c for c in (await page.evaluate('()=>qa.snapshot()'))['calls'] if c['method'] == 'POST'])
                    await resume.click()
                    await page.evaluate('(index)=>qa.settle(index,"busy")', index)
                    await expect(page.locator('[role=alert]')).to_be_visible()
                    assert await resume.count() == 0, 'PROCESSING observation must not allow another resume'
                    # The parent must adopt the observed state as well as hiding the button.
                    warning = await page.locator('.as-delivery-result > .as-inline-warning').inner_text()
                    observations.append({'locale': locale, 'case': 'BUSY_observation_parent_projection', 'warning': warning})
                    assert ('操作尚在处理中' if locale == 'zh-CN' else 'processing') in warning.lower(), warning
                    assert await page.locator('.as-chat-artifact').count() == 1, 'Saved Word must remain available while processing'
                    for status in ['INTERRUPTED_REVIEW_REQUIRED', 'FAILED_OR_PARTIAL']:
                        await page.evaluate('()=>qa.reset()')
                        await expect(resume).to_be_enabled()
                        await page.evaluate('(status)=>qa.recovery(status)', status)
                        index = len([c for c in (await page.evaluate('()=>qa.snapshot()'))['calls'] if c['method'] == 'POST'])
                        await resume.click()
                        await page.evaluate('(index)=>qa.settle(index,"busy")', index)
                        await expect(page.locator('[role=alert]')).to_be_visible()
                        if status == 'INTERRUPTED_REVIEW_REQUIRED':
                            warning = await page.locator('.as-delivery-result > .as-inline-warning').inner_text()
                            assert ('操作已中断' if locale == 'zh-CN' else 'interrupted') in warning.lower(), warning
                            assert await resume.count() == 0
                        else:
                            await expect(resume).to_be_enabled()
                            assert len([c for c in (await page.evaluate('()=>qa.snapshot()'))['calls'] if c['method'] == 'POST']) == index+1, 'No automatic retry after definite failure'
                            await resume.click()
                            await page.evaluate('(index)=>qa.settle(index)', index+1)
                            await expect(page.locator('.as-delivery-heading strong')).to_contain_text('完整交付文件已保存' if locale == 'zh-CN' else 'Complete delivery files saved')
                        observations.append({'locale': locale, 'case': 'qualified_failure_observation_and_explicit_retry', 'status': status})
                    await page.evaluate('()=>qa.reset()')
                    await expect(resume).to_be_enabled()
                    await page.evaluate('()=>qa.recovery("PROCESSING",true)')
                    index = len([c for c in (await page.evaluate('()=>qa.snapshot()'))['calls'] if c['method'] == 'POST'])
                    await resume.click()
                    await page.evaluate('(index)=>qa.settle(index,"busy")', index)
                    await expect(page.locator('[role=alert]')).to_be_visible()
                    assert await resume.count() == 0, 'An unqualified observation must require a fresh original-receipt inspection'
                    warning = await page.locator('.as-delivery-result > .as-inline-warning').inner_text()
                    assert ('操作失败或仅完成部分步骤' if locale == 'zh-CN' else 'failed') in warning.lower(), warning
                    observations.append({'locale': locale, 'case': 'wrong_request_SHA_observation_not_adopted'})
                    await page.evaluate('()=>qa.reset()')
                    await expect(resume).to_be_enabled()
                    await page.evaluate('()=>qa.recovery("EXPORTED_CONFIRMED_PLAN_DERIVATIVES")')
                    index = len([c for c in (await page.evaluate('()=>qa.snapshot()'))['calls'] if c['method'] == 'POST'])
                    await resume.click()
                    await page.evaluate('(index)=>qa.settle(index,"busy")', index)
                    await expect(page.locator('.as-delivery-heading strong')).to_contain_text('完整交付文件已保存' if locale == 'zh-CN' else 'Complete delivery files saved')
                    state = await page.evaluate('()=>qa.snapshot()')
                    assert state['feed'][0]['status'] == 'EXPORTED_CONFIRMED_PLAN_DERIVATIVES'
                    assert len([c for c in state['calls'] if c['method'] == 'POST']) == index+1
                    assert [c['method'] for c in state['calls'][-2:]] == ['POST', 'GET']
                    assert await page.locator('[role=alert]').count() == 0
                    observations.append({'locale': locale, 'case': 'failed_POST_observation_then_same_original_GET_complete_no_replay'})
                    await page.evaluate('()=>qa.reset()')
                    await expect(resume).to_be_enabled()
                    await page.evaluate('()=>qa.recovery("EXPORTED_CONFIRMED_PLAN_DERIVATIVES",true)')
                    index = len([c for c in (await page.evaluate('()=>qa.snapshot()'))['calls'] if c['method'] == 'POST'])
                    await resume.click()
                    await page.evaluate('(index)=>qa.settle(index,"busy")', index)
                    await expect(page.locator('[role=alert]')).to_be_visible()
                    assert await page.locator('.as-delivery-heading').count() == 0
                    assert await resume.count() == 0
                    state = await page.evaluate('()=>qa.snapshot()')
                    assert state['feed'][0]['status'] == 'FAILED_OR_PARTIAL' and not state['reports']
                    assert [c['method'] for c in state['calls'][-2:]] == ['POST', 'GET']
                    assert await page.locator('.as-chat-artifact').count() == 1
                    observations.append({'locale': locale, 'case': 'raw_foreign_harness_source_not_hidden_by_same_path_project_file'})
                    assert not errors and not outside, (errors, outside)
                    await context.close()
            finally:
                await browser.close()
    finally:
        (out/'OBSERVATIONS.json').write_text(json.dumps({'qualification': 'ACTUAL_APP_ACTION_AND_COMPONENT_BROWSER_DEFERRED_HTTP_NOT_NATIVE', 'test_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'observations': observations}, ensure_ascii=False, indent=2)+'\n')
