"""Actual Kobalte review dialog and Quick HTML/preload; controlled DTO/IPC fixture, no App/API/model."""
import asyncio
from copy import deepcopy
from pathlib import Path
import json
import os
from urllib.parse import urlparse
import pytest
from playwright.async_api import async_playwright
from test_native_review_pending_source64 import ROOT, CHROMIUM, FONTS, CLIP, actual_main_styles

HASH='a'*64

def ref(name): return {'id': name, 'revision': 1, 'sha256': HASH}

def catalogs():
    servers=[]
    for name in ['local','remote']:
        servers.append({'id':name,'config_ref':ref(name),'enabled':True,'availability':'AVAILABLE','probe_ref':{'id':('1' if name=='local' else '2')*32,'sha256':HASH},'tools':[{'id':'qa_'+name,'name':'Original '+name+' tool 原创工具','input_schema_sha256':HASH,'selectable':True}],'source':'PRIVATE_MCP_PROBE_NOT_ORIGINAL_OWNER'})
    tools={'schema':'eeas.native-tools.v1','capability_binding_sha256':HASH,'configuration_refs':{'skills':{'writing':ref('writing')},'mcp':{name:ref(name) for name in ['local','remote']}},'owner':{'scope':None,'status':'NOT_SELECTED'},'builtin_tools':[],'mcp_servers':servers,'read_only':True,'model_calls':0,'effects_applied':False}
    basis={'kind':'REPORT_PLAN','file_link_ref':{**ref('b'*64),'project_id':'original_project'},'proposal_ref':ref('original_proposal'),'source_snapshot_sha256':HASH,'candidate_sha256':HASH}
    legacy={'schema':'eeas.native-review-capabilities.v1','capability_binding_sha256':'c'*64,'skills':[{'id':'legacy-writing','title':'Original legacy writing','config_ref':ref('legacy-writing'),'selectable':True}],'tools':[{'id':'planning_acceptance_coverage','title':'任务交付与验收覆盖','selection':{'kind':'BUILTIN_READONLY','tool_ref':ref('planning_acceptance_coverage')},'selectable':True}],'basis_ref':basis,'permission_hint':{'required_effects':['PROCESS_START'],'can_submit':True}}
    return {'schema':'eeas.native-capability-task-capabilities.v2','kind':'CAPABILITY_TASK','capability_binding_sha256':HASH,'skills':[{'id':'writing','title':'Original writing 原创写法','config_ref':ref('writing'),'selectable':True}],'mcp_catalog':tools,'permission_hint':{'required_effects':[],'can_submit':True},'read_only':False,'effects_applied':False,'legacy_review_catalog':legacy}

def task(catalog, ids):
    items=[]
    for name in ids:
        server=next(s for s in catalog['mcp_catalog']['mcp_servers'] if s['id']==name)
        tool=server['tools'][0]
        items.append({'server_id':name,'config_ref':server['config_ref'],'probe_ref':server['probe_ref'],'tool_id':tool['id'],'name':tool['name'],'input_schema_sha256':HASH})
    return {'version':2,'kind':'CAPABILITY_TASK','selection':{'capability_binding_sha256':HASH,'skill_ref':ref('writing'),'mcp_tool_selection':items},'process_start_consent':False,'remote_mcp_connect_consent':False,'task_root_operation_id':None}

async def page_for(browser,out,surface,locale,width,height):
    ctx=await browser.new_context(viewport={'width':width,'height':height},locale=locale,bypass_csp=True)
    page=await ctx.new_page();page.set_default_timeout(2500)
    calls=[];errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    async def route(r):
        if r.request.url=='http://127.0.0.1:17864/':
            await r.fulfill(body=(out/('quick-'+locale+'.html')).read_text() if surface=='quick' else '<!doctype html><body><main id="root"></main></body>',content_type='text/html')
        elif urlparse(r.request.url).path in FONTS:
            file=FONTS[urlparse(r.request.url).path];await r.fulfill(body=file.read_bytes(),content_type='font/woff2' if file.suffix=='.woff2' else 'font/ttf')
        else:calls.append(r.request.url);await r.abort()
    await page.route('**/*',route);await page.goto('http://127.0.0.1:17864/')
    await page.evaluate('(locale)=>localStorage.setItem("eeas.ui-language.v1",locale)',locale)
    return ctx,page,calls,errors

@pytest.mark.parametrize('surface',['main','quick'])
def test_capability_picker_scope_consent_legacy_and_original_draft(surface): asyncio.run(run(surface))

async def run(surface):
    raw=Path(os.environ['EEAS_EVIDENCE_DIR']);out=raw.resolve()
    assert raw.is_absolute() and raw==out and out.is_relative_to((ROOT.parent/'.development').resolve())
    observations=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,executable_path=str(CHROMIUM))
        try:
            for locale,width,height in (([('zh-CN',768,474)] if os.environ.get('EEAS_PICKER_FIRST_ONLY')=='1' else [('zh-CN',768,474),('en',1180,760)]) if surface=='main' else [('zh-CN',420,620),('en',520,620)]):
                ctx,page,calls,errors=await page_for(browser,out,surface,locale,width,height)
                catalog=catalogs()
                if surface=='main':
                    await actual_main_styles(page,out,'picker')
                    await page.evaluate('v=>window.qaInitial=v',{'catalog':catalog,'provider':ref('provider')})
                    await page.add_script_tag(path=str(out/'picker.js'));await page.evaluate('()=>document.fonts.ready')
                    composer=page.locator('#original-composer');await composer.fill('Original unsent draft 原创私稿 😀')
                    await composer.evaluate('n=>{window.originalComposer=n;n.setSelectionRange(4,11);}')
                    await page.locator('#open-capabilities').click();dialog=page.locator('.as-nc-dialog')
                    await dialog.locator('select').select_option('writing')
                    await dialog.get_by_label('Original local tool 原创工具',exact=True).check();await dialog.get_by_label('Original remote tool 原创工具',exact=True).check()
                    selected=await page.evaluate('()=>qa.state().input');assert selected['version']==2 and len(selected['selection']['mcp_tool_selection'])==2 and not selected['process_start_consent'] and not selected['remote_mcp_connect_consent'],selected
                    search=dialog.locator('input[type=search]');await search.fill('remote');await search.evaluate('n=>{window.originalSearch=n;n.setSelectionRange(1,4);}')
                    await page.evaluate('v=>qa.publish(v)',deepcopy(catalog));await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                    assert await search.evaluate('n=>n===originalSearch&&n.isConnected&&n.value==="remote"&&n.selectionStart===1&&n.selectionEnd===4&&document.activeElement===n')
                    await search.fill('');footer=dialog.locator('.as-nc-footer button');await footer.scroll_into_view_if_needed();box=await footer.evaluate(CLIP);layout=await footer.evaluate('node=>{const rows=[];for(let n=node;n;n=n.parentElement){const r=n.getBoundingClientRect(),s=getComputedStyle(n);rows.push({tag:n.tagName,cls:n.className,x:r.x,y:r.y,w:r.width,h:r.height,maxHeight:s.maxHeight,display:s.display,visibility:s.visibility,overflow:s.overflow,flex:s.flex,position:s.position});}return rows;}');observations.append({'phase':'dialog_footer_before_click','box':box,'ancestors':layout});await page.screenshot(path=str(out/f'picker-main-{locale}-footer-check.png'));assert box['visibleWidth']>=box['width']-1 and box['visibleHeight']>=box['height']-1,{'box':box,'layout':layout}
                    await page.screenshot(path=str(out/f'picker-main-{locale}-mixed.png'));await footer.click()
                    await page.wait_for_function('()=>!qa.state().open')
                    await page.locator('#allow-local').check();await page.locator('#allow-remote').check();await page.locator('#prepare-request').click()
                    request=await page.evaluate('()=>qa.requests[0]');assert request['nativeReview']['process_start_consent'] and request['nativeReview']['remote_mcp_connect_consent'];assert 'connection_hints' not in request['nativeReview']['selection']
                    await page.locator('#open-capabilities').click();await page.locator('.as-nc-dialog').get_by_label('任务交付与验收覆盖' if locale=='zh-CN' else 'Delivery and acceptance coverage',exact=True).check()
                    legacy=await page.evaluate('()=>qa.state().input');assert legacy['version']==1 and legacy['selection']['basis_ref']==catalog['legacy_review_catalog']['basis_ref'] and legacy['selection']['skill_ref'] is None,legacy
                    await page.locator('.as-nc-footer button').click();assert await page.locator('#allow-remote').count()==0
                    await page.locator('#open-capabilities').click();await page.locator('.as-nc-dialog').get_by_label('Original local tool 原创工具',exact=True).check()
                    assert (await page.evaluate('()=>qa.state().input'))['selection']['skill_ref'] is None
                    await page.locator('.as-nc-dialog select').select_option('writing');await page.locator('.as-nc-dialog').get_by_label('Original local tool 原创工具',exact=True).uncheck();await page.locator('.as-nc-footer button').click()
                    assert await page.locator('#allow-local').count()==0 and await page.locator('#allow-remote').count()==0
                    retained=await composer.evaluate('n=>({same:n===originalComposer,value:n.value,start:n.selectionStart,end:n.selectionEnd})');assert retained=={'same':True,'value':'Original unsent draft 原创私稿 😀','start':4,'end':11},retained
                    observations.append({'locale':locale,'surface':surface,'viewport':[width,height],'footer':box,'prepared_request':request,'legacy_input':legacy,'draft':retained,'scope':'Actual Panel/Picker/helpers; controlled host DTO and external submit boundary, not full App.'})
                else:
                    state={'epoch':1,'locale':locale,'visible':True,'kind':'ask','source':{'image':None,'available':False,'label':'Original frozen page'},'draft':{'id':'original_draft','phase':'EDITING','operationId':None,'comment':'Original unsent draft 原创私稿 😀','marks':[],'readonly':False,'nativeReview':None},'reviewCatalog':catalog,'reviewOpen':True,'reviewBusy':False,'reviewProcessRequired':False,'reviewRemoteRequired':False,'canSend':True,'canDraft':True,'canConfirm':True,'guidanceEditable':False,'cancelSupported':True}
                    await page.evaluate('value=>{const listeners=new Map();window.qa={current:value,controls:[],publish(next){this.current=structuredClone(next);listeners.get("eeas:quick-surface:state")?.(null,this.current);}};window.qaIPC={on:(key,fn)=>listeners.set(key,fn),invoke:async(key,payload)=>{if(payload.kind==="state")return qa.current;qa.controls.push(structuredClone(payload));return qa.current;}};}',state)
                    await page.add_script_tag(path=str(out/'quick.js'));await page.evaluate("window.dispatchEvent(new Event('DOMContentLoaded'))")
                    composer=page.locator('#comment');await composer.evaluate('n=>{window.originalComposer=n;n.focus();n.setSelectionRange(4,11);}')
                    await page.locator('#review-skill').select_option('writing');await page.wait_for_function('()=>qa.controls.length>0');assert (await page.evaluate('()=>qa.controls.at(-1)'))['kind']=='capability-selection'
                    state['draft']['nativeReview']=task(catalog,[]);await page.evaluate('v=>qa.publish(v)',state)
                    await page.locator('[data-capability-tool="qa_local"]').click();await page.wait_for_function('()=>qa.controls.at(-1)?.toolIds?.includes("qa_local")')
                    state['draft']['nativeReview']=task(catalog,['local']);state['reviewProcessRequired']=True;await page.evaluate('v=>qa.publish(v)',state);assert not await page.locator('#review-process').is_checked();assert not await page.locator('#review-remote-consent').is_visible()
                    await page.locator('#review-process').check();assert (await page.evaluate('()=>qa.controls.at(-1)'))['kind']=='review-consent'
                    await page.locator('[data-capability-tool="qa_remote"]').click();await page.wait_for_function('()=>qa.controls.at(-1)?.toolIds?.length===2')
                    state['draft']['nativeReview']=task(catalog,['local','remote']);state['reviewRemoteRequired']=True;await page.evaluate('v=>qa.publish(v)',state);assert not await page.locator('#review-process').is_checked() and not await page.locator('#review-remote').is_checked()
                    await page.locator('#review-remote').check();assert (await page.evaluate('()=>qa.controls.at(-1)'))['kind']=='review-remote-consent'
                    await page.locator('[data-builtin-id]').click();await page.wait_for_function('()=>qa.controls.at(-1)?.kind==="review-selection"');legacy_event=await page.evaluate('()=>qa.controls.at(-1)');assert legacy_event['skillId']=='' and legacy_event['toolId']=='planning_acceptance_coverage'
                    state['draft']['nativeReview']={'version':1,'selection':{'capability_binding_sha256':'c'*64,'skill_ref':None,'tool_selection':catalog['legacy_review_catalog']['tools'][0]['selection'],'basis_ref':catalog['legacy_review_catalog']['basis_ref']},'process_start_consent':False};state['reviewRemoteRequired']=False;await page.evaluate('v=>qa.publish(v)',state)
                    assert not await page.locator('#review-remote-consent').is_visible()
                    retained=await composer.evaluate('n=>({same:n===originalComposer,value:n.value,start:n.selectionStart,end:n.selectionEnd})');assert retained=={'same':True,'value':'Original unsent draft 原创私稿 😀','start':4,'end':11},retained
                    await page.screenshot(path=str(out/f'picker-quick-{locale}-legacy.png'))
                    state['draft'].update({'phase':'COMPLETED','operationId':'original_task','nativeReview':task(catalog,['local'])});state['reviewOpen']=False;state['result']={'operation_id':'original_task','status':'RETURNED','text':'Original task result','review':{'version':2,'task_root_operation_id':'original_task','review_ref':ref('review')}}
                    await page.evaluate('v=>qa.publish(v)',state);button=page.locator('[data-command="capability-continue"]');await button.scroll_into_view_if_needed();box=await button.evaluate(CLIP);assert box['visibleWidth']>=box['width']-1 and box['visibleHeight']>=box['height']-1,box;await button.click();assert (await page.evaluate('()=>qa.controls.at(-1)'))['command']=='capability-continue'
                    state['result']['status']='UNKNOWN_OUTCOME';state['draft']['phase']='UNKNOWN';await page.evaluate('v=>qa.publish(v)',state);assert not await button.is_visible()
                    observations.append({'locale':locale,'surface':surface,'viewport':[width,height],'footer':box,'legacy_event':legacy_event,'draft':retained,'controls':await page.evaluate('()=>qa.controls'),'scope':'Actual Quick HTML/preload + controlled IPC projection; actual host/controller separately Node qualified.'})
                assert not calls,calls;assert not errors,errors;await ctx.close()
        finally:
            await browser.close();(out/f'picker-{surface}-OBSERVATIONS.json').write_text(json.dumps(observations,ensure_ascii=False,indent=2)+'\n')
