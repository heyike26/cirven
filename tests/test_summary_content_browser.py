"""Real Solid summary + Discovery/Proposal components, controlled HTTP/draft port.

No App/native Windows/model qualification; browser and business data stay local.
"""
import asyncio
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from test_proposal_delivery_preferences_browser import BUILD as EXISTING_BUILD, ROOT, NODE, Ports, artifact as proposal_artifact

ENTRY=r'''const entry=`import {render} from 'solid-js/web';import {createComponent,createSignal} from 'solid-js';import SummaryContent from ${JSON.stringify(path.join(src,'SummaryContent.tsx'))};import DiscoveryRenderer from ${JSON.stringify(path.join(src,'DiscoveryRenderer.tsx'))};import ProposalRenderer from ${JSON.stringify(path.join(src,'ProposalRenderer.tsx'))};import {ObjectDraftBoundary} from ${JSON.stringify(path.join(src,'ObjectDraft.tsx'))};import {objectDraft,artifactStateKey} from ${JSON.stringify(path.join(src,'workbench-state.ts'))};import {startUILanguage,setUILanguagePreference} from ${JSON.stringify(path.join(src,'ui-language.ts'))};
window.mountSummary=async(kind,artifact,locale)=>{const stopLanguage=startUILanguage();await setUILanguagePreference(locale);window.__calls=[];const [text,setText]=createSignal(typeof artifact==='string'?artifact:artifact.data.payload.candidate.summary);const context={dispatch:async(id,target,input)=>{window.__calls.push({id,input:structuredClone(input),artifact:structuredClone(target)});return {status:'DRAFT'}}};const owner=kind==='summary'?undefined:objectDraft(artifactStateKey(artifact));const stop=render(()=>kind==='summary'?createComponent(SummaryContent,{get text(){return text()}}):createComponent(ObjectDraftBoundary,{artifact,get children(){return createComponent(kind==='discovery'?DiscoveryRenderer:ProposalRenderer,{artifact,context})}}),document.querySelector('#fixture'));window.__driver={locale:setUILanguagePreference,text:setText,state:()=>({calls:structuredClone(window.__calls),fields:structuredClone(owner?.fields??{}),view:structuredClone(owner?.view??{})}),dispose:async()=>{await owner?.finish();stop();stopLanguage()}};};`;
'''
BUILD=EXISTING_BUILD[:EXISTING_BUILD.index('const entry=`')]+ENTRY+EXISTING_BUILD[EXISTING_BUILD.index('const plugin='):]
BUILD=BUILD.replace('(TaskGraph|PlanningDeliveryResult)', '(TaskGraph|PlanningDeliveryResult|SemanticGraph|GraphEditor|WordPreview)')

@pytest.fixture(scope='module')
def bundle():
 result=subprocess.run([str(NODE),'-e',BUILD,str(ROOT)],capture_output=True,text=True,timeout=35)
 assert result.returncode==0,result.stderr
 return result.stdout

class SummaryPorts(Ports):
 def __init__(self):super().__init__(source={});self.external=[]
 async def route(self,route):
  req=route.request
  if not req.url.startswith('http://127.0.0.1:8897/'):
   self.external.append(req.url);await route.abort();return
  if req.url.endswith('/template-profiles'):
   self.calls.append({'method':req.method,'path':'/template-profiles','body':None});await route.fulfill(json={'profiles':[]});return
  await super().route(route)

async def mount(browser,bundle,ports,kind,value,width,locale):
 page=await browser.new_page(viewport={'width':1000,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 await page.route('**/*',ports.route);await page.goto('http://127.0.0.1:8897/');await page.set_content(f'<div class="as-app-frame"><div id="fixture" class="as-object" style="width:{width}px;max-width:100%"></div></div>')
 for name in ['styles.css','native-collaboration.css','agent-studio.css','brand-and-spacing.css','proposal-review.css']:await page.add_style_tag(content=(ROOT/'apps/agent-shell/src'/name).read_text())
 await page.add_script_tag(content=bundle);await page.evaluate('async args=>mountSummary(...args)',[kind,value,locale]);return page,errors

async def dispose(page,ports):
 await page.evaluate('__driver.dispose()');assert not ports.external,ports.external
 assert not [v for v in ports.calls if v['method']=='POST' and not any(v['path'].endswith(suffix) for suffix in ['/workbench-state/load','/workbench-state/save'])],ports.calls
 await page.close()

def actual_summary():
 source=os.environ.get('CIRVEN_SUMMARY_QA_INPUT')
 if source:return json.loads(Path(source).read_text())['summary']
 return '\n\n'.join(f'## {i}、工作说明\n原始资料与人工决定分开记录。'+('任务不代表执行授权。'*16) for i in range(1,8))

def discovery(summary):
 value={'id':'discovery_fixture','project_id':'project_fixture','revision':4,'current_revision':4,'sha256':'d'*64,'source_current':True,'answers':[],
 'payload':{'entry_mode':'TASK_FILE','materials':[],'source_receipt':None,'history':[],'status':'DRAFT','accepted_claim_ids':[],'accepted_graph_ids':[],'resolutions':[],'proposal_ref':None,
 'candidate':{'title':'原始工作说明','phase':'DEFINE','summary':summary,'claims':[],'graphs':[],'competitors':[],'next_actions':[],
 'questions':[{'id':'Q01','text':'还需要确认什么？','reason':'保留当前输入','category':'DECISION','blocking':True,'affects_claim_ids':[],'requested_materials':[],'source_anchors':[]}]}},
 'readiness':{'pending_count':1,'blocking_count':1,'requirements_confirmed':False,'can_plan':False,'review_status':'CURRENT','semantic_verification':'NOT_RUN','execution_authorized':False}}
 return {'type':'discovery','artifactId':value['id'],'revision':4,'contentHash':value['sha256'],'scope':{'projectId':value['project_id'],'sessionId':value['id']},'data':value}

SELECT=r'''({quote,occurrence=0})=>{const root=document.querySelector('[data-candidate-field="/summary"]'),walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node;while(node=walker.nextNode()){let at=-1,from=0;for(let i=0;i<=occurrence;i++){at=node.textContent.indexOf(quote,from);if(at<0)break;from=at+1}if(at>=0){const range=document.createRange();range.setStart(node,at);range.setEnd(node,at+quote.length);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);root.dispatchEvent(new PointerEvent('pointerup',{bubbles:true}));return true}}return false}'''

async def run_render(bundle,tmp):
 p=await async_playwright().start();browser=await p.chromium.launch(headless=True);results=[];raw=actual_summary();original=hashlib.sha256(raw.encode()).hexdigest()
 try:
  for width,locale in [(320,'zh-CN'),(320,'en'),(760,'zh-CN'),(760,'en')]:
   ports=SummaryPorts();proposal=proposal_artifact();proposal['data']['payload']['candidate']['summary']=raw;before=copy.deepcopy(proposal)
   page,errors=await mount(browser,bundle,ports,'proposal',proposal,width,locale);details=page.locator('.as-summary-disclosure');toggle=details.locator('summary');await toggle.wait_for()
   assert not await details.evaluate('e=>e.open');assert (await details.bounding_box())['height']<=56
   assert not await details.locator('h3').first.is_visible();assert await page.locator('.as-proposal-toolbar').first.is_visible()
   await toggle.focus();await page.keyboard.press('Enter');await page.wait_for_function("document.querySelector('.as-summary-disclosure').open")
   assert await details.locator('h3').count()==7;assert not (await details.inner_text()).lstrip().startswith('##')
   geometry=await details.locator('.as-summary-content').evaluate('e=>({width:e.clientWidth,scroll:e.scrollWidth,font:getComputedStyle(e).fontSize,line:getComputedStyle(e).lineHeight,headings:[...e.querySelectorAll("h3")].map(n=>n.textContent)})')
   assert geometry['scroll']<=geometry['width']+1,geometry;assert geometry['font']=='16px',geometry
   await page.screenshot(path=str(tmp/f'actual-{width}-{locale}-expanded.png'));assert proposal==before
   await toggle.focus();await page.keyboard.press('Enter');await page.wait_for_function("!document.querySelector('.as-summary-disclosure').open")
   assert await toggle.evaluate('e=>document.activeElement===e');await page.screenshot(path=str(tmp/f'actual-{width}-{locale}-collapsed.png'))
   assert not errors,errors;results.append({'case':'actual-seven-sections','width':width,'locale':locale,'geometry':geometry,'raw_sha256':original,'commands':await page.evaluate('__driver.state().calls')});await dispose(page,ports)
  ports=SummaryPorts();raw_security='## Short title\nA **clear** explanation.\n\n- First\n- Second\n\n[unsafe](javascript:alert(1)) ![alt](https://untrusted.invalid/image.png)\n<script>globalThis.__compromised=true</script> <img src=x onerror="globalThis.__compromised=true">\n[Safe](https://example.com/)'
  page,errors=await mount(browser,bundle,ports,'summary',raw_security,320,'en');content=page.locator('.as-summary-content');assert await page.locator('.as-summary-disclosure').count()==0;assert await content.locator('h3').inner_text()=='Short title';assert await content.locator('li').count()==2;assert await content.locator('strong').inner_text()=='clear'
  assert await content.locator('script,img,iframe,object,input,style').count()==0;assert await content.locator('[href^="javascript:"]').count()==0;assert await page.evaluate('window.__compromised') is None
  assert await content.locator('a').get_attribute('rel')=='noopener noreferrer';assert await content.locator('a').get_attribute('target')=='_blank';assert not errors,errors
  results.append({'case':'semantic-safe-no-media-fetch','html':await content.inner_html(),'external_requests':ports.external});await dispose(page,ports)
  (tmp/'RENDER_RESULT.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
 except BaseException:
  (tmp/'RENDER_PARTIAL.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
  if browser.contexts and browser.contexts[-1].pages:
   try:await browser.contexts[-1].pages[-1].screenshot(path=str(tmp/'render-failure.png'),timeout=3000)
   except Exception:pass
  raise
 finally:await browser.close();await p.stop()

async def run_selection(bundle,tmp):
 p=await async_playwright().start();browser=await p.chromium.launch(headless=True);results=[]
 try:
  for locale in ['zh-CN','en']:
   raw='## 原始标题\n中文🙂 **保留人工修改**，不能覆盖。\n\n'+('完整说明。'*150);value=discovery(raw);before=copy.deepcopy(value);ports=SummaryPorts();page,errors=await mount(browser,bundle,ports,'discovery',value,320,locale)
   question=page.locator('.as-question-card textarea').first;await question.fill('未发送的答复必须保留');await page.evaluate('window.__originalQuestion=document.querySelector(".as-question-card textarea")')
   toggle=page.locator('.as-summary-disclosure>summary');await toggle.click();assert await page.evaluate(SELECT,{'quote':'保留人工修改'})
   feedback=page.locator('.as-text-feedback');await feedback.wait_for();assert await feedback.locator('blockquote').inner_text()=='保留人工修改';await feedback.locator('textarea').fill('这里保留旧人工编辑')
   await feedback.locator('textarea').focus();await page.evaluate('window.__originalComment=document.querySelector(".as-text-feedback textarea")');await page.evaluate('__driver.locale("en")' if locale=='zh-CN' else '__driver.locale("zh-CN")')
   assert await page.evaluate('document.querySelector(".as-text-feedback textarea")===window.__originalComment&&document.activeElement===window.__originalComment');assert await feedback.locator('textarea').input_value()=='这里保留旧人工编辑'
   await feedback.locator('button').nth(1).click();await page.wait_for_function('__driver.state().calls.length===1');state=await page.evaluate('__driver.state()');sent=state['calls'][0]
   selection=sent['input']['selection']['text_selection'];assert sent['id']=='workbench.feedback' and sent['input']['delivery']=='DRAFT';assert sent['artifact']==before
   assert selection['text']=='保留人工修改' and selection['quote_sha256']==hashlib.sha256('保留人工修改'.encode()).hexdigest();loc=selection['locator'];assert raw[loc['char_start']:loc['char_end']]=='保留人工修改' and loc['field_pointer']=='/summary'
   assert await question.input_value()=='未发送的答复必须保留';assert await page.evaluate('document.querySelector(".as-question-card textarea")===window.__originalQuestion')
   assert value==before and not errors,(value,errors);results.append({'case':'precise-selection-draft-and-live-locale','locale':locale,'selection':selection,'state':state});await dispose(page,ports)
  ports=SummaryPorts();value=discovery('## 重复引用\n相同原文。相同原文。\n\n'+('保留说明。'*150));page,errors=await mount(browser,bundle,ports,'discovery',value,320,'zh-CN');question=page.locator('.as-question-card textarea').first;await question.fill('歧义时也不能丢稿');await page.locator('.as-summary-disclosure>summary').click();assert await page.evaluate(SELECT,{'quote':'相同原文'})
  alert=page.locator('.as-discovery>p[role=alert]');await alert.wait_for();assert await alert.inner_text()=='请缩小选文范围，或引用原文说明要修改的位置。';assert await page.locator('.as-text-feedback').count()==0;assert await page.evaluate('__driver.state().calls')==[]
  await page.evaluate('__driver.locale("en")');assert await alert.inner_text()=='Select a smaller passage, or quote the original text to describe the change.';assert await question.input_value()=='歧义时也不能丢稿';assert not errors,errors
  results.append({'case':'ambiguous-selection-visible-actionable-error-no-dispatch','state':await page.evaluate('__driver.state()')});await page.screenshot(path=str(tmp/'ambiguous-en.png'));await dispose(page,ports)
  (tmp/'SELECTION_RESULT.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
 finally:await browser.close();await p.stop()

def test_actual_long_summary_semantics_keyboard_geometry_and_untrusted_markup(bundle,tmp_path):asyncio.run(asyncio.wait_for(run_render(bundle,tmp_path),70))
def test_discovery_selection_preserves_raw_basis_and_live_drafts_with_actionable_ambiguity(bundle,tmp_path):asyncio.run(asyncio.wait_for(run_selection(bundle,tmp_path),70))
