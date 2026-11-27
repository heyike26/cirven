"""Real ProposalRenderer/ObjectDraftSession/Kobalte controls; controlled HTTP/picker ports.

This is component/browser qualification, not App, model, Windows or native picker QA.
"""
import asyncio,json,subprocess
from pathlib import Path
import pytest
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
NODE=ROOT/'.node-tools/node_modules/node/bin/node'
BUILD=r'''
const fs=require('node:fs'),path=require('node:path'),{createRequire}=require('node:module');const root=process.argv[1],app=path.join(root,'apps/agent-shell'),src=path.join(app,'src'),req=createRequire(path.join(app,'package.json')),esbuild=req('esbuild'),babel=req('@babel/core'),solid=createRequire(req.resolve('vite-plugin-solid'))('babel-preset-solid'),ts=req('typescript');
const entry=`import {render} from 'solid-js/web';import {createComponent} from 'solid-js';import ProposalRenderer from ${JSON.stringify(path.join(src,'ProposalRenderer.tsx'))};import {ObjectDraftBoundary} from ${JSON.stringify(path.join(src,'ObjectDraft.tsx'))};import {objectDraft,artifactStateKey} from ${JSON.stringify(path.join(src,'workbench-state.ts'))};import {startUILanguage,setUILanguagePreference} from ${JSON.stringify(path.join(src,'ui-language.ts'))};
window.mountProposal=async(artifact,locale)=>{window.__calls=[];let lang={preference:locale,resolved_locale:locale};window.eeasDesktop={version:1,chooseFiles:async()=>['D:\\\\Templates\\\\Chosen.dotx'],getUILanguage:async()=>lang,setUILanguage:async p=>(lang={preference:p,resolved_locale:p}),onUILanguageChanged:()=>()=>{}};const stopLanguage=startUILanguage();await setUILanguagePreference(locale);const context={dispatch:async(id,target,input)=>{window.__calls.push({id,input:structuredClone(input),scope:structuredClone(target.scope),revision:target.revision});return{operation_id:'component-only',status:'PROCESSING',automatic_model_replay:false}}};const stop=render(()=>createComponent(ObjectDraftBoundary,{artifact,get children(){return createComponent(ProposalRenderer,{artifact,context})}}),document.querySelector('#fixture'));const owner=objectDraft(artifactStateKey(artifact));window.__driver={locale:setUILanguagePreference,finish:()=>owner.finish(),state:()=>structuredClone({fields:owner.fields,calls:window.__calls,status:owner.status}),dispose:async()=>{await owner.finish();stop();stopLanguage()}};};`;
const plugin={name:'component-io-boundary',setup(b){b.onResolve({filter:/^@opencode-ai\/ui\/button$/},()=>({path:path.join(root,'staging-native-ui/upstream/packages/ui/src/components/button.tsx')}));b.onResolve({filter:/^\.\/(TaskGraph|PlanningDeliveryResult)$/},()=>({path:'unused-child',namespace:'fixture'}));b.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:'export default function UnusedChild(){return null}',loader:'js'}));b.onLoad({filter:/\.css$/},()=>({contents:'',loader:'js'}));b.onLoad({filter:/\.tsx$/},a=>{const stripped=ts.transpileModule(fs.readFileSync(a.path,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;return{contents:babel.transformSync(stripped,{filename:a.path,presets:[[solid,{generate:'dom',hydratable:false}]],configFile:false,babelrc:false}).code,loader:'js',resolveDir:path.dirname(a.path)}});}};
esbuild.build({stdin:{contents:entry,loader:'js',resolveDir:app},plugins:[plugin],bundle:true,format:'iife',platform:'browser',write:false,define:{'process.env.NODE_ENV':'"production"'},logLevel:'silent'}).then(r=>process.stdout.write(r.outputFiles[0].text)).catch(e=>{console.error(e);process.exitCode=1});
'''

REF={'kind':'local_file','project_id':'project_fixture','workspace_id':'workspace_fixture','binding_revision':2,'binding_sha256':'b'*64,'path':'templates/用户指定任务书模板.docx','revision':3,'sha256':'c'*64}
WORKSPACE={'id':'workspace_fixture','project_id':'project_fixture','revision':2,'binding_sha256':'b'*64,'root':'D:\\Project','allowed_write_paths':['delivery/']}

def artifact():
 proposal={'id':'proposal_fixture','project_id':'project_fixture','revision':1,'sha256':'a'*64,'review_status':'CURRENT','answers':[],
 'payload':{'status':'CONFIRMED','workspace_ref':{'id':WORKSPACE['id'],'revision':2,'binding_sha256':'b'*64},'discovery_ref':{'id':'discussion_fixture','revision':4,'sha256':'d'*64},'candidate':{'title':'原创任务计划','summary':'生成指定模板任务书，不执行开发。','nodes':[{'id':'N01','title':'准备任务书','description':'保留用户模板和人工内容','kind':'TASK','origin':'USER_STATEMENT','parent_id':None,'depends_on':[],'inputs':[],'outputs':['任务书'],'acceptance':['来源可核'],'capabilities':[],'risks':[],'source_anchors':[]}],'questions':[]}}}
 return {'type':'proposal','artifactId':proposal['id'],'revision':1,'contentHash':proposal['sha256'],'scope':{'projectId':proposal['project_id'],'sessionId':'discussion_fixture','workspaceId':WORKSPACE['id']},'data':proposal}

@pytest.fixture(scope='module')
def bundle():
 result=subprocess.run([str(NODE),'-e',BUILD,str(ROOT)],capture_output=True,text=True,timeout=35)
 assert result.returncode==0,result.stderr
 return result.stdout

class Ports:
 def __init__(self,*,target=None,source=None,workspaces=None,stale=False,hold_file=False,hold_target=False):
  self.target={} if target is None else target;self.source={'templateRef':REF,'templateName':'用户指定任务书模板.docx'} if source is None else source;self.workspaces=[WORKSPACE] if workspaces is None else workspaces;self.stale=stale;self.records={};self.calls=[];self.file_entered=asyncio.Event();self.file_gate=asyncio.Event();self.target_entered=asyncio.Event();self.target_gate=asyncio.Event()
  if not hold_file:self.file_gate.set()
  if not hold_target:self.target_gate.set()
 async def route(self,route):
  req=route.request;path=req.url.split('127.0.0.1:8897')[-1];body=req.post_data_json if req.method=='POST' else None;self.calls.append({'method':req.method,'path':path,'body':body})
  if path=='/':await route.fulfill(body='<html></html>',content_type='text/html');return
  if path in ['/web/assets/fonts/manrope-variable.woff2','/web/assets/fonts/noto-sans-sc-variable.woff2']:
   await route.fulfill(body=(ROOT/'apps/web/assets/fonts'/path.split('/')[-1]).read_bytes(),content_type='font/woff2');return
  if path=='/api/bootstrap':value={'csrf_token':'controlled-component'}
  elif path.endswith('/workbench-state/load'):
   key=body['key'];identity=json.dumps(key,sort_keys=True)
   if key['kind']=='proposal':self.target_entered.set();await self.target_gate.wait()
   value=self.records.get(identity,{'status':'SAVED','schema_version':1,'key':key,'state_revision':0,'fields':self.source if key['kind']=='discovery' else self.target,'view':{},'dirty':False})
  elif path.endswith('/workbench-state/save'):
   key=body['key'];identity=json.dumps(key,sort_keys=True);value={'status':'SAVED','schema_version':1,'key':key,'state_revision':body['expected_revision']+1,'fields':body['fields'],'view':body['view'],'dirty':body['dirty'],'operation_id':body['operation_id']};self.records[identity]=value
  elif path.endswith('/local-workspaces') and req.method=='GET':value={'workspaces':self.workspaces}
  elif path.endswith('/local-workspaces') and req.method=='POST':
   assert body['allowed_write_paths']==[],'explicit picker must only request readonly parent';value={**WORKSPACE,'id':'picked_workspace','allowed_write_paths':[]}
  elif '/file?' in path:
   chosen='picked_workspace' in path
   if not chosen:self.file_entered.set();await self.file_gate.wait()
   source={**REF,'workspace_id':'picked_workspace','path':'Chosen.dotx'} if chosen else {**REF,'sha256':('e'*64 if self.stale else REF['sha256'])};value={'current':True,'source_ref':source,'byte_length':1,'encoding':'binary'}
  else:raise AssertionError(f'Unexpected external/business request: {req.method} {path}')
  await route.fulfill(json=value)

async def mount(browser,bundle,ports,width,locale):
 page=await browser.new_page(viewport={'width':1000,'height':1000});errors=[];page.on('pageerror',lambda error:errors.append(str(error)));await page.route('**/*',ports.route);await page.goto('http://127.0.0.1:8897/');await page.set_content(f'<div class="as-app-frame"><div id="fixture" style="width:{width}px;max-width:100%"></div></div>')
 for name in ['styles.css','native-collaboration.css','agent-studio.css','brand-and-spacing.css','proposal-review.css']:await page.add_style_tag(content=(ROOT/'apps/agent-shell/src'/name).read_text())
 await page.add_script_tag(content=bundle);await page.evaluate('async args=>mountProposal(...args)',[artifact(),locale]);await page.locator('.as-export-options>summary').click();return page,errors

def directory_input(page):return page.locator('.as-export-options>label input')
def export_button(page):return page.locator('.as-export-options>.as-delivery-actions button').first
def clear_button(page):return page.locator('.as-template-choice button.as-text-action')

async def finish(page,ports):
 await page.evaluate('__driver.dispose()');assert not [item for item in ports.calls if item['method']=='POST' and not (item['path'].endswith('/workbench-state/load') or item['path'].endswith('/workbench-state/save') or item['path'].endswith('/local-workspaces'))],ports.calls;await page.close()

async def run(bundle,tmp):
 p=await async_playwright().start();browser=await p.chromium.launch(headless=True);results=[]
 try:
  for width in [320,760]:
   for locale in ['zh-CN','en']:
    ports=Ports();page,errors=await mount(browser,bundle,ports,width,locale);await page.locator('.as-proposal-template-name').wait_for();await page.wait_for_function("document.querySelector('.as-export-options>label input').value==='delivery'");assert await export_button(page).is_enabled()
    assert await page.locator('.as-proposal-template-name').inner_text()=='用户指定任务书模板.docx'
    geometry=await page.locator('.as-export-options').evaluate('e=>{const b=e.getBoundingClientRect();return{container:b.toJSON(),scrollWidth:e.scrollWidth,clientWidth:e.clientWidth,controls:[...e.querySelectorAll("button,input,select")].filter(n=>n.getBoundingClientRect().width).map(n=>({label:n.getAttribute("aria-label")||n.textContent,rect:n.getBoundingClientRect().toJSON()}))}}');assert geometry['scrollWidth']<=geometry['clientWidth']+1,geometry
    for control in geometry['controls']:assert control['rect']['left']>=geometry['container']['left'] and control['rect']['right']<=geometry['container']['right']+1,geometry
    await page.screenshot(path=str(tmp/f'{width}-{locale}-inherited.png'));await export_button(page).click();await page.wait_for_function('__driver.state().calls.length===1');sent=await page.evaluate('__driver.state()');assert sent['calls'][0]['id']=='proposal.delivery.export' and sent['calls'][0]['input']['directory']=='delivery' and sent['calls'][0]['input']['template']==REF,sent
    await directory_input(page).fill('manual/review');assert await export_button(page).is_disabled();await clear_button(page).click();await page.evaluate('__driver.finish()');await page.screenshot(path=str(tmp/f'{width}-{locale}-explicit-clear.png'));assert not errors,errors
    await finish(page,ports)
    # A new renderer and real ObjectDraftSession read the saved fields, rather than reusing memory.
    page,errors=await mount(browser,bundle,ports,width,locale);await page.wait_for_function("document.querySelector('.as-export-options>label input').value==='manual/review'");assert await page.locator('.as-proposal-template-name').count()==0;assert await clear_button(page).count()==0;assert await export_button(page).is_disabled();state=await page.evaluate('__driver.state()');assert state['fields']['templatePath']=='' and 'templateRef' not in state['fields'];assert not errors,errors;results.append({'case':'inherit-export-clear-restart','width':width,'locale':locale,'geometry':geometry,'state':state,'calls':ports.calls});await finish(page,ports)
  for action in ['clear','path','picker']:
   ports=Ports(hold_file=True);page,errors=await mount(browser,bundle,ports,320,'en');await asyncio.wait_for(ports.file_entered.wait(),5);assert await export_button(page).is_disabled()
   if action=='clear':await clear_button(page).click()
   elif action=='path':
    details=page.locator('.as-export-options>details');await details.locator('summary').click();await details.locator('input').fill('templates/manual.dotx')
   else:
    # Native picker is an explicit controlled IO boundary; no real OS dialog is claimed.
    await page.locator('.as-template-choice button').first.click();await page.wait_for_function("document.querySelector('.as-proposal-template-name')?.textContent==='Chosen.dotx'")
   ports.file_gate.set();await page.wait_for_timeout(100);state=await page.evaluate('__driver.state()')
   if action=='clear':assert state['fields'].get('templateRef') is None and state['fields']['templatePath']==''
   elif action=='path':assert state['fields'].get('templateRef') is None and state['fields']['templatePath']=='templates/manual.dotx'
   else:assert state['fields']['templateRef']['path']=='Chosen.dotx' and state['fields']['templateRef']['workspace_id']=='picked_workspace'
   assert state['calls']==[] and not errors,(state,errors);results.append({'case':'pending-'+action,'state':state,'calls':ports.calls});await finish(page,ports)
  for kind in ['stale','cross-project','ambiguous-grant','no-grant','old-directory','delayed-target']:
   kwargs={'stale':True} if kind=='stale' else {'source':{'templateRef':{**REF,'project_id':'foreign'}}} if kind=='cross-project' else {'workspaces':[{**WORKSPACE,'allowed_write_paths':['a/','b/']}]} if kind=='ambiguous-grant' else {'workspaces':[{**WORKSPACE,'allowed_write_paths':[]}]} if kind=='no-grant' else {'target':{'harnessDirectory':'eeas-harness'}} if kind=='old-directory' else {'hold_target':True}
   ports=Ports(**kwargs);page,errors=await mount(browser,bundle,ports,320,'zh-CN')
   if kind=='delayed-target':
    await asyncio.wait_for(ports.target_entered.wait(),5);assert not ports.file_entered.is_set();assert await directory_input(page).is_disabled() and await export_button(page).is_disabled();ports.target_gate.set();await page.locator('.as-proposal-template-name').wait_for();assert await export_button(page).is_enabled()
   elif kind in ['stale','cross-project']:
    await page.locator('.as-export-options>p[role=alert]').wait_for();assert await export_button(page).is_disabled();assert await page.locator('.as-proposal-template-name').count()==0;await clear_button(page).click();assert await export_button(page).is_enabled()
   else:
    await page.locator('.as-proposal-template-name').wait_for();assert await directory_input(page).input_value()==('eeas-harness' if kind=='old-directory' else '');assert await export_button(page).is_disabled()
   assert not errors,errors;results.append({'case':kind,'state':await page.evaluate('__driver.state()'),'calls':ports.calls});await finish(page,ports)
  (tmp/'RESULT.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
 except BaseException:
  (tmp/'PARTIAL.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
  if browser.contexts and browser.contexts[-1].pages:
   page=browser.contexts[-1].pages[-1]
   try:
    await page.screenshot(path=str(tmp/'failure.png'),timeout=5000)
    (tmp/'failure-body.txt').write_text(await page.locator('body').inner_text(timeout=3000))
   except Exception:pass
  raise
 finally:
  try:await asyncio.wait_for(browser.close(),timeout=8)
  finally:await p.stop()

def test_exact_template_inheritance_and_grant_defaults_preserve_user_choice_and_restart(bundle,tmp_path):asyncio.run(asyncio.wait_for(run(bundle,tmp_path),100))

def test_actual_draft_api_sqlite_restart_keeps_clear_and_directory_without_changing_predecessor(tmp_path):
 from test_workbench_state import client_at,counts
 from docx import Document
 folder=tmp_path/'original';folder.mkdir();(folder/'delivery').mkdir();template=folder/'用户模板.docx';doc=Document();doc.add_paragraph('用户原创模板，保留原件。');doc.save(template);original=template.read_bytes();data=tmp_path/'data';receipt=[]
 with client_at(data) as client:
  response=client.post('/api/projects',json={'name':'提案偏好持久性原创夹具','description':''});assert response.status_code==201,response.text;pid=response.json()['id']
  response=client.post(f'/api/projects/{pid}/local-workspaces',json={'idempotency_key':'original-grant','root':str(folder),'allowed_write_paths':['delivery/']});assert response.status_code==201,response.text;workspace=response.json()
  response=client.get(f'/api/projects/{pid}/local-workspaces/{workspace["id"]}/file',params={'path':template.name});assert response.status_code==200,response.text;source=response.json()['source_ref']
  predecessor={'project_id':pid,'session_id':'discussion','kind':'discovery','object_id':'discussion','revision':4,'sha256':'d'*64};target={**predecessor,'kind':'proposal','object_id':'proposal','revision':1,'sha256':'a'*64};base=f'/api/projects/{pid}/workbench-state'
  for key,op,rev,fields in [(predecessor,'original-template',0,{'templateRef':source,'templateName':template.name}),(target,'inherited-template',0,{'templateRef':source,'templateName':template.name,'templatePath':''}),(target,'explicit-clear',1,{'templateName':'','templatePath':'','harnessDirectory':'delivery/manual'})]:
   response=client.post(base+'/save',json={'key':key,'operation_id':op,'expected_revision':rev,'fields':fields,'view':{},'dirty':True});assert response.status_code==200,response.text;receipt.append(response.json())
  assert counts(client.app.state.store)['agent_executions']==0 and counts(client.app.state.store)['task_runs']==0
 with client_at(data) as client:
  restored=client.post(base+'/load',json={'key':target});assert restored.status_code==200,restored.text;assert restored.json()['fields']=={'templateName':'','templatePath':'','harnessDirectory':'delivery/manual'}
  previous=client.post(base+'/load',json={'key':predecessor});assert previous.json()['fields']=={'templateRef':source,'templateName':template.name}
  history=client.get(base+'/operations/inherited-template');assert history.json()['fields']['templateRef']==source
  assert client.app.state.local_workspaces.get(pid,workspace['id'])['allowed_write_paths']==['delivery/']
  assert counts(client.app.state.store)['agent_executions']==0 and counts(client.app.state.store)['task_runs']==0
  receipt.extend([restored.json(),previous.json(),history.json()])
 assert template.read_bytes()==original
 (tmp_path/'API_SQLITE_RESTART.json').write_text(json.dumps({'qualification':'REAL_ASGI_API_SQLITE_SAVED_REFS_AND_CLEAR','windows':'NOT_RUN','model_calls':0,'original_template_unchanged':True,'receipts':receipt},ensure_ascii=False,indent=2)+'\n')
