"""Current Solid confirmation review, with controlled HTTP/draft and graph ports.

Original QA input is optional. No App, server persistence, Windows or model claim.
"""
import asyncio,copy,hashlib,json,os,subprocess
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from test_proposal_delivery_preferences_browser import BUILD as BASE_BUILD,ROOT,NODE,Ports,artifact as basic_artifact

ENTRY=r'''const entry=`import {render} from 'solid-js/web';import {createComponent,createSignal} from 'solid-js';import ProposalRenderer from ${JSON.stringify(path.join(src,'ProposalRenderer.tsx'))};import {ObjectDraftBoundary} from ${JSON.stringify(path.join(src,'ObjectDraft.tsx'))};import {objectDraft,artifactStateKey} from ${JSON.stringify(path.join(src,'workbench-state.ts'))};import {startUILanguage,setUILanguagePreference} from ${JSON.stringify(path.join(src,'ui-language.ts'))};
window.mountReview=async(initial,locale)=>{const stopLanguage=startUILanguage();await setUILanguagePreference(locale);const [artifact,setArtifact]=createSignal(initial);window.__calls=[];const context={dispatch:async(id,target,input)=>{window.__calls.push({id,input:structuredClone(input),artifact:structuredClone(target)});if(window.__failNext){window.__failNext=false;throw Error('Original QA connection unavailable')}return{status:'RECORDED'}}};const owner=objectDraft(artifactStateKey(initial));const stop=render(()=>createComponent(ObjectDraftBoundary,{get artifact(){return artifact()},get children(){return createComponent(ProposalRenderer,{get artifact(){return artifact()},context})}}),document.querySelector('#fixture'));window.__driver={locale:setUILanguagePreference,refresh:setArtifact,finish:()=>owner.finish(),state:()=>({fields:structuredClone(owner.fields),calls:structuredClone(window.__calls),status:owner.status}),dispose:async()=>{await owner.finish();stop();stopLanguage()}};};`;
'''
BUILD=BASE_BUILD[:BASE_BUILD.index('const entry=`')]+ENTRY+BASE_BUILD[BASE_BUILD.index('const plugin='):]


def proposal():
 source=os.environ.get('CIRVEN_CONFIRMATION_QA_INPUT')
 if source:
  value=json.loads(Path(source).read_text())['value']['proposal']
  assert value['revision']==3 and value['payload']['status']=='CONFIRMED'
  assert len(value['payload']['confirmation']['resolutions'])==5
  artifact={'type':'proposal','artifactId':value['id'],'revision':value['revision'],'contentHash':value['sha256'],'scope':{'projectId':value['project_id'],'sessionId':value['payload']['discovery_ref']['id'],'workspaceId':value['payload']['workspace_ref']['id']},'data':value}
  return artifact,{'scope':'ORIGINAL_ROOT_QA_CONFIRMED_R3_NOT_ENTERPRISE','path':source,'sha256':hashlib.sha256(Path(source).read_bytes()).hexdigest()}
 artifact=basic_artifact();value=artifact['data'];value.update(revision=3,sha256='f'*64);artifact.update(revision=3,contentHash='f'*64)
 questions=[{'id':f'Q{i+7:02}','text':f'原创问题 {i+7} / Current question {i+7}','reason':'仅使用原创虚构资料，保留已保存的原始决定。','category':'DECISION','blocking':i<5,'affects_node_ids':['N01'],'requested_materials':[],'source_anchors':[]} for i in range(7)]
 answers=[{'answer_id':f'answer_{i}','proposal_id':value['id'],'proposal_revision':2,'question_id':q['id'],'question_sha256':'b'*64,'text':f'原创已保存答复 {i+7} / Saved response','accepted':False} for i,q in enumerate(questions[:5])]
 value['payload']['candidate']['questions']=questions;value['answers']=answers
 value['payload']['confirmation']={'accepted_revision':2,'accepted_sha256':'a'*64,'user_confirmed':True,'execution_authorized':False,'resolutions':[{'question_id':a['question_id'],'mode':'ANSWER_ACCEPTED','answer_id':a['answer_id'],'answer_text':a['text'],'answer_sha256':'c'*64,'assumption':None,'reason':'用户明确采用原创答复。'} for a in answers]}
 return artifact,{'scope':'SYNTHETIC_ORIGINAL_COMPONENT_FIXTURE_NOT_API'}


@pytest.fixture(scope='module')
def bundle():
 result=subprocess.run([str(NODE),'-e',BUILD,str(ROOT)],capture_output=True,text=True,timeout=35)
 assert result.returncode==0,result.stderr
 return result.stdout


async def mount(browser,bundle,ports,artifact,width,locale):
 page=await browser.new_page(viewport={'width':1000,'height':1000});errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
 await page.route('**/*',ports.route);await page.goto('http://127.0.0.1:8897/');await page.set_content(f'<div class="as-app-frame"><div id="fixture" style="width:{width}px;max-width:100%;height:100%;min-height:0;overflow:auto"></div></div>')
 for name in ['styles.css','native-collaboration.css','agent-studio.css','brand-and-spacing.css','proposal-review.css']:await page.add_style_tag(content=(ROOT/'apps/agent-shell/src'/name).read_text())
 await page.add_script_tag(content=bundle);await page.evaluate('async args=>mountReview(...args)',[artifact,locale]);await page.locator('.as-adopted-toggle').wait_for();return page,errors


async def finish(page,ports):
 await page.evaluate('__driver.dispose()')
 assert not [c for c in ports.calls if c['method']=='POST' and not c['path'].endswith(('/workbench-state/load','/workbench-state/save'))],ports.calls
 await page.close()


async def run_layout(bundle,tmp):
 artifact,provenance=proposal();before=copy.deepcopy(artifact);questions=artifact['data']['payload']['candidate']['questions'];adopted=[r['question_id'] for r in artifact['data']['payload']['confirmation']['resolutions']];pending=[q['id'] for q in questions if q['id'] not in adopted];records=[]
 p=await async_playwright().start();browser=await p.chromium.launch(headless=True)
 try:
  for width,locale in [(320,'zh-CN'),(320,'en'),(760,'zh-CN'),(760,'en')]:
   ports=Ports(source={});page,errors=await mount(browser,bundle,ports,artifact,width,locale)
   section=page.locator('.as-plan-questions');assert ' '.join((await section.locator('h3').inner_text()).split())==('待确认问题 2' if locale=='zh-CN' else 'Open question 2')
   assert await section.locator('.as-question-card:visible').count()==2
   actual_ids=await section.locator('.as-question-card').evaluate_all('(rows)=>rows.map(row=>row.dataset.questionId)');assert actual_ids==pending+adopted
   assert await section.locator('.as-saved-answer:visible').count()==0
   await section.evaluate('e=>e.scrollIntoView({block:"start",inline:"nearest"})')
   await page.screenshot(path=str(tmp/f'collapsed-{width}-{locale}.png'),clip={'x':0,'y':0,'width':width,'height':1000})
   toggle=page.locator('.as-adopted-toggle');await toggle.scroll_into_view_if_needed()
   bounds=await toggle.bounding_box();frame=await page.locator('#fixture').bounding_box();assert bounds and frame and bounds['y']>=frame['y']-1 and bounds['y']+bounds['height']<=frame['y']+frame['height']+1,'last disclosure is reachable within the actual scrolling ancestor'
   await page.screenshot(path=str(tmp/f'collapsed-end-{width}-{locale}.png'),clip={'x':0,'y':0,'width':width,'height':1000})
   geometry=await section.evaluate('e=>{const b=e.getBoundingClientRect();return{width:b.width,height:b.height,client:e.clientWidth,scroll:e.scrollWidth,fields:[...e.querySelectorAll(".as-question-card:not([hidden])>div:first-child>strong,.as-question-card:not([hidden])>div:first-child>span,.as-question-card:not([hidden])>p")].map(n=>({font:getComputedStyle(n).fontSize,line:getComputedStyle(n).lineHeight,rect:n.getBoundingClientRect().toJSON()}))}}')
   assert geometry['scroll']<=geometry['client']+1,geometry
   assert all(float(f['font'][:-2])>=13 for f in geometry['fields']),geometry
   toggle=page.locator('.as-adopted-toggle');await toggle.focus();await toggle.press('Enter');assert await toggle.get_attribute('aria-expanded')=='true'
   assert await section.locator('.as-question-adopted:visible').count()==5
   assert await section.locator('.as-question-adopted textarea:visible').count()==0,'adopted decisions add no empty response forms'
   assert await section.locator('.as-question-adopted .as-adopted-decision').count()==5
   await section.locator('.as-question-adopted').first.screenshot(path=str(tmp/f'adopted-detail-{width}-{locale}.png'))
   await toggle.press('Enter');assert await toggle.get_attribute('aria-expanded')=='false';assert await toggle.evaluate('e=>document.activeElement===e')
   assert await page.evaluate('__driver.state().calls')==[] and not errors,(errors,await page.evaluate('__driver.state()'))
   records.append({'width':width,'locale':locale,'pending':pending,'adopted':adopted,'geometry':geometry,'page_errors':errors});await finish(page,ports)
  assert artifact==before
  (tmp/'LAYOUT_RESULT.json').write_text(json.dumps({'scope':'ACTUAL_SOLID_COMPONENT_NOT_APP_NOT_WINDOWS','provenance':provenance,'records':records,'model_calls':0},ensure_ascii=False,indent=2)+'\n')
 finally:await browser.close();await p.stop()


async def run_state(bundle,tmp):
 artifact,provenance=proposal();before=copy.deepcopy(artifact);adopted_id=artifact['data']['payload']['confirmation']['resolutions'][0]['question_id'];pending_id=artifact['data']['payload']['candidate']['questions'][-1]['id'];ports=Ports(source={},target={'answers':{adopted_id:'此前未发答复😀，保留原稿'},'resolutions':{pending_id:{'reason':'未提交采用理由'}}});records=[]
 p=await async_playwright().start();browser=await p.chromium.launch(headless=True)
 try:
  page,errors=await mount(browser,bundle,ports,artifact,320,'zh-CN');pending=page.locator(f'[data-question-id="{pending_id}"]>textarea');await pending.fill('当前未发问题草稿 😀\n保留换行')
  await pending.evaluate('e=>window.__samePending=e');await pending.focus();latest=copy.deepcopy(artifact);latest['data']['answers'].append({'answer_id':'later-observed','proposal_id':artifact['artifactId'],'proposal_revision':3,'question_id':pending_id,'question_sha256':'a'*64,'text':'同版新观察答复，尚未采用'})
  await page.evaluate('value=>__driver.refresh(value)',latest)
  assert await pending.evaluate('e=>e===window.__samePending&&document.activeElement===e')
  assert await pending.input_value()=='当前未发问题草稿 😀\n保留换行';assert ' '.join((await page.locator('.as-plan-questions>h3').inner_text()).split())=='待确认问题 2'
  await page.evaluate('__driver.locale("en")');assert await pending.evaluate('e=>e===window.__samePending&&document.activeElement===e');assert await pending.input_value()=='当前未发问题草稿 😀\n保留换行'
  assert 'Unsubmitted draft' in await page.locator('.as-adopted-toggle').inner_text()
  await page.locator('.as-adopted-toggle').click();draft=page.locator(f'[data-question-id="{adopted_id}"]>textarea');assert await draft.input_value()=='此前未发答复😀，保留原稿'
  await draft.focus();await draft.evaluate('e=>window.__sameAdopted=e');await page.evaluate('value=>__driver.refresh(value)',copy.deepcopy(latest));assert await draft.evaluate('e=>e===window.__sameAdopted&&document.activeElement===e')
  await page.locator('.as-adopted-toggle').click();assert await page.locator('.as-question-adopted:visible').count()==0
  await pending.fill('保存失败也保留的草稿');await page.evaluate('window.__failNext=true');await page.locator(f'[data-question-id="{pending_id}"]>button').click();await page.locator('.as-proposal>p[role=alert]').wait_for();assert await pending.input_value()=='保存失败也保留的草稿'
  calls=await page.evaluate('__driver.state().calls');assert len(calls)==1 and calls[0]['id']=='proposal.answer';assert calls[0]['artifact']['revision']==3 and calls[0]['artifact']['contentHash']==artifact['contentHash'];assert calls[0]['input']=={'questionId':pending_id,'text':'保存失败也保留的草稿'}
  await page.evaluate('__driver.finish()');state=await page.evaluate('__driver.state()');assert state['fields']['answers'][adopted_id]=='此前未发答复😀，保留原稿';records.append({'case':'same-version-observation-locale-failure','state':state});assert not errors,errors;await finish(page,ports)
  page,errors=await mount(browser,bundle,ports,artifact,760,'en');assert await page.locator(f'[data-question-id="{pending_id}"]>textarea').input_value()=='保存失败也保留的草稿';await page.locator('.as-adopted-toggle').click();assert await page.locator(f'[data-question-id="{adopted_id}"]>textarea').input_value()=='此前未发答复😀，保留原稿';assert await page.evaluate('__driver.state().calls')==[];assert not errors
  # Editing a confirmed question invalidates only that local projection without
  # remounting its focused field or borrowing the old accepted wording.
  await page.locator('.as-proposal-toolbar').first.locator('button').first.click()
  editor=page.locator(f'[data-question-id="{adopted_id}"] label textarea').first
  await editor.focus();await editor.evaluate('e=>window.__sameQuestionEditor=e')
  original_question=next(q for q in artifact['data']['payload']['candidate']['questions'] if q['id']==adopted_id)
  await editor.fill(original_question['text']+' / local draft correction')
  assert await editor.evaluate('e=>e===window.__sameQuestionEditor&&document.activeElement===e')
  assert await page.locator(f'[data-question-id="{adopted_id}"]').get_attribute('data-adopted')=='false'
  assert ' '.join((await page.locator('.as-plan-questions>h3').inner_text()).split())=='Open question 3'
  await page.evaluate('__driver.finish()');records.append({'case':'local-question-edit-keeps-focus-and-does-not-inherit-old-decision','state':await page.evaluate('__driver.state()')});await finish(page,ports)
  # A copied old confirmation on a new basis cannot transfer adoption or drafts.
  newer=copy.deepcopy(artifact);newer.update(revision=4,contentHash='e'*64);newer['data'].update(revision=4,sha256='e'*64);newer['data']['payload'].update(revision=4,status='DRAFT')
  page=await browser.new_page(viewport={'width':1000,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));await page.route('**/*',ports.route);await page.goto('http://127.0.0.1:8897/');await page.set_content('<div class="as-app-frame"><div id="fixture" style="width:320px;height:100%;min-height:0;overflow:auto"></div></div>')
  for name in ['styles.css','native-collaboration.css','agent-studio.css','brand-and-spacing.css','proposal-review.css']:await page.add_style_tag(content=(ROOT/'apps/agent-shell/src'/name).read_text())
  await page.add_script_tag(content=bundle);await page.evaluate('args=>mountReview(...args)',[newer,'en']);await page.locator('.as-plan-questions').wait_for();assert await page.locator('.as-adopted-toggle').count()==0;assert await page.locator('.as-question-card:visible').count()==7;assert await page.locator(f'[data-question-id="{pending_id}"]>textarea').input_value()==''
  assert any(r['fields'].get('answers',{}).get(adopted_id)=='此前未发答复😀，保留原稿' for r in ports.records.values());assert not errors;records.append({'case':'restart-old-draft-preserved-new-basis-isolated','state':await page.evaluate('__driver.state()')});await finish(page,ports)
  assert artifact==before
  (tmp/'STATE_RESULT.json').write_text(json.dumps({'scope':'REAL_OBJECT_DRAFT_SESSION_WITH_CONTROLLED_HTTP_NOT_SQLITE','provenance':provenance,'records':records,'model_calls':0},ensure_ascii=False,indent=2)+'\n')
 finally:await browser.close();await p.stop()


def test_actual_confirmation_review_pending_first_readable_narrow_wide_and_bilingual(bundle,tmp_path):asyncio.run(asyncio.wait_for(run_layout(bundle,tmp_path),70))
def test_exact_basis_drafts_same_dom_late_observation_failure_restart_and_new_basis_isolation(bundle,tmp_path):asyncio.run(asyncio.wait_for(run_state(bundle,tmp_path),70))
