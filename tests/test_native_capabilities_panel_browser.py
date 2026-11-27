"""UNIT browser: actual Solid/Kobalte Panel + clients, external host callbacks only.
No live API, Store, native runtime, Windows, accounts or model qualification.
"""
from pathlib import Path
import json
import os
import socket
import subprocess
import time
import urllib.request

REPO = Path(__file__).resolve().parents[1]
FIXTURE = r'''
import {createSignal} from 'solid-js';
import {render} from 'solid-js/web';
import Panel from '__SRC__/NativeCapabilitiesPanel.tsx';
import {startUILanguage,setUILanguagePreference,t as translate} from '__SRC__/ui-language.ts';
const H='a'.repeat(64),J='b'.repeat(64),ref=(id,revision=1)=>({id,revision,sha256:revision===1?H:J});
const base=(id)=>({id,config_ref:ref(id),enabled:true,integrity:'CURRENT',loaded_in_original_session:false,qualified:false,native_binding:'DECLARED'});
let skills=['alpha','beta',...Array.from({length:10},(_,i)=>'review-'+i)].map(id=>({...base(id),title:id==='alpha'?'保存设置':id,description:'原始说明 原文不可翻译。 '.repeat(8)}));
let mcp=[{...base('local_fixture'),type:'local',timeout:60000,probe_permitted:true}];
const content=id=>'---\nname: '+id+'\ndescription: 原始说明\n---\nKeep 保存设置 exactly. 😀';
const sources=new Map(skills.map(s=>[s.id,content(s.id)])),ops=new Map();
const calls=[],held=[];let mode='OK',failure=409,readFailure=false,binding=H,ownerState='CURRENT';
function record(method,body){calls.push({method,body:structuredClone(body)});}
async function commit(kind,body){record(kind,body);if(mode==='REJECT')return {schema:'eeas.capability-rejection.v1',confirmed_rejected:true,code:'HTTP_REJECTED',status:failure,message:'配置已变化，请刷新'};
 const existing=(kind==='IMPORT'?skills:mcp).find(item=>item.id===body.id);if((existing?.config_ref.revision??0)!==body.expected_revision)return {schema:'eeas.capability-rejection.v1',confirmed_rejected:true,code:'HTTP_REJECTED',status:409,message:'配置已变化，请刷新'};
 const receipt={config_ref:ref(body.id,body.expected_revision+1),saved:true,loaded_in_original_session:false,qualified:false};
 const config=kind==='IMPORT'?{name:body.id,content:body.content,enabled:body.enabled}:body.configuration;
 const apply=()=>{if(kind==='IMPORT'){const old=skills.find(s=>s.id===body.id);if(old)Object.assign(old,{config_ref:receipt.config_ref,enabled:body.enabled});else skills.push({...base(body.id),title:body.id,description:'原始说明',config_ref:receipt.config_ref,enabled:body.enabled});sources.set(body.id,body.content);}
  ops.set(body.operation_id,{operation_id:body.operation_id,input_sha256:H,receipt,kind:kind==='IMPORT'?'skills':'mcp',configuration:config});};
 if(mode==='HOLD')return new Promise(resolve=>held.push(()=>{apply();resolve(receipt);}));
 apply();if(mode==='UNKNOWN')throw new Error('transport fixture unavailable');return receipt;
}
window.eeasDesktop={version:2,getUILanguage:async()=>({preference:'zh-CN',resolved_locale:'zh-CN'}),setUILanguage:async value=>({preference:value,resolved_locale:value}),onUILanguageChanged:()=>()=>{},
 getAgentCapabilities:async()=>{record('GET',null);if(readFailure)throw new Error('directory unavailable');return structuredClone({schema:'eeas.native-capabilities.v1',skills,mcp});},
 readAgentSkill:async id=>{record('READ_SKILL',id);return {config_ref:skills.find(s=>s.id===id).config_ref,content:sources.get(id),enabled:true,source:'EXACT_FIXTURE'};},
 importAgentSkill:body=>commit('IMPORT',body),
 setAgentSkillEnabled:async(id,body)=>{record('TOGGLE',{id,...body});const item=skills.find(s=>s.id===id);return new Promise(resolve=>held.push(()=>{item.enabled=body.enabled;item.config_ref=ref(id,body.expected_revision+1);resolve({config_ref:item.config_ref,saved:true,loaded_in_original_session:false,qualified:false});}));},
 configureAgentMCP:body=>commit('CONFIGURE',body),setAgentMCPEnabled:async()=>{throw new Error('unused');},
 readAgentMCP:async(id,config_ref)=>({config_ref,configuration:{type:'remote',url:'https://fixture.invalid/mcp',enabled:true,timeout:60000},current_configuration:true,integrity:'CURRENT'}),
 probeAgentMCP:async(id,body)=>{record('PROBE',{id,...body});return {id:'c'.repeat(32),config_ref:body.config_ref,status:'FAILED',initialized:false,list_tools_succeeded:false,loaded_in_original_session:false,qualified:false,tools_invoked:0};},
 inspectAgentCapabilityOperation:async id=>{record('INSPECT',id);const result=ops.get(id);if(!result)throw new Error('not found');return structuredClone(result);},
 getAgentTools:async scope=>{record('TOOLS',scope??null);return {schema:'eeas.native-tools.v1',capability_binding_sha256:binding,configuration_refs:{skills:Object.fromEntries(skills.map(s=>[s.id,s.config_ref])),mcp:{local_fixture:mcp[0].config_ref}},owner:{scope:scope??null,status:scope?ownerState:'NOT_SELECTED'},builtin_tools:scope&&ownerState==='CURRENT'?[{id:'read',description:'Original tool description',permission:'ask',input_schema_sha256:H,selectable_for_new_session:false,source:'ORIGINAL_OWNER_REGISTRY'}]:[],mcp_servers:[{id:'local_fixture',config_ref:mcp[0].config_ref,enabled:true,availability:'AVAILABLE',probe_ref:{id:'c'.repeat(32),sha256:H},tools:[{id:'local_fixture_lookup',name:'lookup',input_schema_sha256:H,selectable:true}],source:'PRIVATE_MCP_PROBE_NOT_ORIGINAL_OWNER'}],read_only:true,model_calls:0,effects_applied:false};}
};
const owner={project_id:'11111111-1111-4111-8111-111111111111',session_ref:ref('fixture_session'),generation:1,configuration_current:true};
const[open,setOpen]=createSignal(false),[initial,setInitial]=createSignal('skills'),[selectedOwner,setOwner]=createSignal(owner),[selection,setSelection]=createSignal();
startUILanguage();
window.qa={calls,text:translate,open:(tab)=>{setInitial(tab);setOpen(true);},mode:(value,status=409)=>{mode=value;failure=status;},release:()=>{const next=held.shift();if(!next)throw new Error('no held callback');next();},locale:setUILanguagePreference,readFailure:value=>readFailure=value,binding:value=>binding=value,ownerState:value=>ownerState=value,owner:value=>setOwner(value?owner:undefined),selection:()=>selection(),content};
render(()=><><button id="opener" onClick={()=>{setInitial('skills');setOpen(true);}}>Manage</button><input id="session-focus" aria-label="Session input"/><Panel open={open()} onOpenChange={setOpen} initialTab={initial()} owner={selectedOwner()} onOpenSessionSettings={()=>{setOpen(false);document.querySelector('#session-focus').focus();}} onChooseTools={value=>{setSelection(value);setOpen(false);document.querySelector('#session-focus').focus();}}/></>,document.getElementById('app'));
'''


def require(value, message):
    if not value:
        raise AssertionError(message)


def test_actual_manager_modal_and_recovery(tmp_path):
    from playwright.sync_api import sync_playwright
    app = REPO / 'apps/agent-shell'
    node = REPO / '.node-tools/node_modules/node/bin/node'
    fixture = tmp_path / 'browser'
    fixture.mkdir()
    (fixture / 'index.html').write_text('<!doctype html><html><meta charset="utf-8"><body style="margin:0;background:#f3f4f5"><div id="app"></div><script type="module" src="/fixture.tsx"></script></body></html>')
    (fixture / 'fixture.tsx').write_text(FIXTURE.replace('__SRC__', '/@fs/' + str(app / 'src')))
    config = fixture / 'vite.config.mjs'
    config.write_text(f'''import solid from {json.dumps(str(app / 'node_modules/vite-plugin-solid/dist/esm/index.mjs'))};
export default {{root:{json.dumps(str(fixture))},cacheDir:{json.dumps(str(fixture/'vite-cache'))},plugins:[solid()],resolve:{{dedupe:['solid-js'],alias:{{'solid-js':{json.dumps(str(app/'node_modules/solid-js'))},'@kobalte/core/dialog':{json.dumps(str(app/'node_modules/@kobalte/core/dist/dialog/index.jsx'))},'@kobalte/core/switch':{json.dumps(str(app/'node_modules/@kobalte/core/dist/switch/index.jsx'))}}}}},server:{{hmr:false,host:'127.0.0.1',strictPort:true,fs:{{allow:[{json.dumps(str(REPO))},{json.dumps(str(tmp_path))}]}}}}}};
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, TMPDIR=str(tmp_path), TMP=str(tmp_path), TEMP=str(tmp_path), NODE_COMPILE_CACHE=str(tmp_path/'node-cache'))
    log = (tmp_path/'vite.log').open('w')
    server = subprocess.Popen([str(node), str(app/'node_modules/vite/bin/vite.js'), '--config', str(config), '--port', str(port)], cwd=fixture, env=env, stdout=log, stderr=subprocess.STDOUT)
    outcome = {'scope':'UNIT_ACTUAL_SOLID_KOBALTE_EXTERNAL_HOST_FIXTURE', 'checks':[], 'console_errors':[]}
    try:
        deadline = time.monotonic()+30
        while time.monotonic()<deadline:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}', timeout=1):
                    break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError((tmp_path/'vite.log').read_text())
                time.sleep(.1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width':1280,'height':860})
                page.set_default_timeout(6000)
                page.on('pageerror', lambda error: outcome['console_errors'].append(str(error)))
                page.add_init_script("localStorage.setItem('eeas.ui-language.v1','zh-CN')")
                # Cold Vite dependency transforms are fixture setup, not a UI action deadline.
                page.goto(f'http://127.0.0.1:{port}', wait_until='domcontentloaded', timeout=90000)
                page.locator('#opener').wait_for(timeout=90000)
                page.locator('#opener').click()
                page.get_by_role('dialog').wait_for()
                page.get_by_role('button',name='保存设置 原始说明',exact=False).first.wait_for()
                require(page.locator('.as-nc-grid').evaluate("e=>getComputedStyle(e).gridTemplateColumns.split(' ').length") == 2, 'wide catalog must have two columns')
                require(page.get_by_role('tab',name='技能',exact=True).count()==1, 'Chinese tabs')
                page.screenshot(path=str(tmp_path/'catalog-1280-zh.png'))
                page.get_by_role('searchbox').fill('alpha')
                page.locator('.as-nc-card-main').click()
                source=page.get_by_role('textbox',name='SKILL.md 原文',exact=True)
                require(source.input_value().endswith('Keep 保存设置 exactly. 😀'),'source verbatim')
                source.fill(source.input_value()+'\nlocal draft')
                page.get_by_role('button',name='返回目录',exact=False).click()
                require(page.get_by_role('searchbox').input_value()=='alpha','search retained')
                page.locator('.as-nc-card-main').click()
                require(source.input_value().endswith('local draft'),'draft retained after detail navigation')
                before=len(page.evaluate('qa.calls'))
                page.evaluate("qa.locale('en')")
                require(page.get_by_role('textbox',name='SKILL.md source',exact=True).input_value().endswith('local draft'),'locale preserves draft')
                require(len(page.evaluate('qa.calls'))==before,'locale adds no reads or writes')
                page.get_by_role('button',name='Back to catalog',exact=False).click()
                page.get_by_role('searchbox').fill('')
                page.set_viewport_size({'width':430,'height':640})
                require(page.locator('.as-nc-grid').evaluate("e=>getComputedStyle(e).gridTemplateColumns.split(' ').length")==1,'narrow one column')
                require(page.get_by_role('tab',name='Skills',exact=True).count()==1,'English tabs')
                page.screenshot(path=str(tmp_path/'catalog-430-en.png'))
                require(page.locator('.as-nc-dialog').evaluate('e=>e.scrollWidth<=e.clientWidth+1'),'dialog no horizontal overflow')
                page.keyboard.press('Escape')
                require(page.locator('#opener').evaluate('e=>document.activeElement===e'),'escape returns focus to opener')
                outcome['checks'].append('wide/narrow catalog, source, locale, draft, search, focus')
                # Pending switch stays old until exact revision comes back.
                page.locator('#opener').click()
                switch=page.get_by_role('switch',name='Enable 保存设置',exact=True)
                switch.focus()
                switch.press('Space')
                require(switch.is_disabled() and switch.is_checked(),'pending switch must retain saved checked state')
                page.evaluate('qa.release()')
                page.wait_for_function("qa.calls.filter(x=>x.method==='GET').length>=3")
                require(not switch.is_disabled() and not switch.is_checked(),'switch commits only after response')
                require(switch.evaluate('e=>document.activeElement===e'),'keyboard focus survives saved switch and directory refresh')
                outcome['checks'].append('direct switch exact acknowledgement')
                # File selection can remove one redundant manual field, without overwriting IDs.
                page.get_by_role('button',name='Import skill',exact=True).click()
                file=page.get_by_label('Choose original SKILL.md',exact=True)
                raw='---\r\nname: new-fixture\r\ndescription: 原样保存\r\n---\r\nBody 保存设置 😀\r\n'
                file.set_input_files({'name':'SKILL.md','mimeType':'text/markdown','buffer':raw.encode()})
                page.wait_for_function("document.querySelector('input[aria-label=\"Skill ID\"]').value==='new-fixture'")
                require(page.get_by_role('textbox',name='SKILL.md source',exact=True).input_value()==raw.replace('\r\n','\n'),'textarea preserves text apart from HTML newline normalization')
                page.get_by_role('textbox',name='Skill ID',exact=True).fill('user-selected')
                file.set_input_files({'name':'SKILL.md','mimeType':'text/markdown','buffer':raw.encode()})
                require(page.get_by_role('textbox',name='Skill ID',exact=True).input_value()=='user-selected','manual ID not overwritten')
                page.get_by_role('textbox',name='Skill ID',exact=True).fill('new-fixture')
                # Known 400/409 can correct with a new operation, not a permanent unknown lock.
                for status in [400,409]:
                    page.evaluate(f"qa.mode('REJECT',{status})")
                    page.get_by_role('button',name='Import source',exact=True).click()
                    page.get_by_role('alert').wait_for()
                    require(not page.get_by_role('textbox',name='Skill ID',exact=True).is_disabled(),'known rejection unlocks form')
                    require(page.get_by_role('button',name='Check original operation',exact=True).count()==0,'known rejection not unknown')
                outcome['checks'].append('file frontmatter hint and known 400/409 correction')
                # UNKNOWN only GET; an exact success stays resolved even if refresh then fails.
                page.evaluate("qa.mode('UNKNOWN')")
                page.get_by_role('button',name='Import source',exact=True).click()
                inspect=page.get_by_role('button',name='Check original operation',exact=True)
                inspect.wait_for()
                count=len([x for x in page.evaluate('qa.calls') if x['method']=='IMPORT'])
                require(page.get_by_role('textbox',name='Skill ID',exact=True).is_disabled(),'unknown locks matching draft')
                require([x for x in page.evaluate('qa.calls') if x['method']=='IMPORT'][-1]['body']['content']==raw,'file original CRLF bytes are retained in the submitted source')
                page.evaluate('qa.readFailure(true)')
                inspect.click()
                page.wait_for_function("qa.calls.some(x=>x.method==='INSPECT')")
                page.wait_for_function("!document.querySelector('fieldset').disabled")
                require(len([x for x in page.evaluate('qa.calls') if x['method']=='IMPORT'])==count,'inspect never repeats POST')
                require(inspect.count()==0,'exact recovered commit remains resolved across directory failure')
                require(page.get_by_role('button',name='Save new version',exact=True).count()==1,'recovery adopts original receipt basis')
                page.evaluate('qa.readFailure(false)')
                outcome['checks'].append('UNKNOWN original GET, no replay, committed recovery despite refresh failure')
                # A save returns while B is edited: must not transplant A basis into B.
                page.get_by_role('button',name='Back to catalog',exact=False).click()
                page.get_by_role('searchbox').fill('alpha')
                page.locator('.as-nc-card-main').click()
                page.evaluate("qa.mode('OK')")
                page.get_by_role('button',name='Save new version',exact=True).click()
                latest=lambda: page.get_by_role('button',name=page.evaluate("qa.text('读取最新版本')"),exact=True)
                restore=lambda: page.get_by_role('button',name=page.evaluate("qa.text('恢复原草稿')"),exact=True)
                latest().wait_for()
                require(page.get_by_role('textbox',name='SKILL.md source',exact=True).input_value().endswith('local draft'),'CAS 409 preserves old draft')
                latest().click()
                page.wait_for_function("document.querySelector('textarea').value===qa.content('alpha')")
                restore().click()
                require(page.get_by_role('textbox',name='SKILL.md source',exact=True).input_value().endswith('local draft'),'explicit original draft restore retains old basis and text')
                latest().click()
                page.wait_for_function("document.querySelector('textarea').value===qa.content('alpha')")
                outcome['checks'].append('real revision CAS conflict, explicit latest read and original draft restore')
                page.evaluate("qa.mode('HOLD')")
                page.get_by_role('button',name='Save new version',exact=True).click()
                page.get_by_role('button',name='Back to catalog',exact=False).click()
                page.get_by_role('searchbox').fill('beta')
                page.locator('.as-nc-card-main').click()
                b=page.get_by_role('textbox',name='SKILL.md source',exact=True)
                b.fill(b.input_value()+'\nB draft preserved')
                page.evaluate('qa.release()')
                require(page.get_by_role('textbox',name='Skill ID',exact=True).input_value()=='beta','late A does not select A')
                require(b.input_value().endswith('B draft preserved'),'late A does not overwrite B draft')
                page.evaluate("qa.mode('OK')")
                page.get_by_role('button',name='Save new version',exact=True).click()
                page.wait_for_function("qa.calls.filter(x=>x.method==='IMPORT'&&x.body.id==='beta').length===1")
                beta=[x for x in page.evaluate('qa.calls') if x['method']=='IMPORT' and x['body']['id']=='beta'][-1]
                require(beta['body']['expected_revision']==1,'B retains its own exact basis')
                outcome['checks'].append('late save A cannot overwrite current B basis or draft')
                page.get_by_role('button',name='Back to catalog',exact=False).click()
                page.get_by_role('searchbox').fill('')
                page.get_by_role('tab',name='Tools',exact=True).click()
                page.locator('.as-nc-card-main').filter(has_text='local_fixture').click()
                page.get_by_role('checkbox').check()
                require(page.evaluate('qa.selection()') is None,'selection does not create or mutate session')
                page.get_by_role('button',name='Use in new conversation',exact=True).click()
                require(page.evaluate('qa.selection().items[0].tool_id')=='local_fixture_lookup','exact selected tool refs')
                require(page.locator('#session-focus').evaluate('e=>document.activeElement===e'),'choose does not steal parent manager focus')
                outcome['checks'].append('read-only catalog to exact new-session selection and focus handoff')
                # Explicit closes restore focus, callback destinations keep their own focus.
                page.locator('#opener').click()
                page.get_by_role('button',name='Close',exact=True).click()
                page.get_by_role('dialog').wait_for(state='hidden')
                page.wait_for_function("document.activeElement===document.getElementById('opener')")
                require(page.locator('#opener').evaluate('e=>document.activeElement===e'),'close button returns opener focus')
                page.locator('#opener').click()
                page.mouse.click(3,3)
                page.get_by_role('dialog').wait_for(state='hidden')
                page.wait_for_function("document.activeElement===document.getElementById('opener')")
                require(page.get_by_role('dialog').count()==0,'outside click closes dialog')
                require(page.locator('#opener').evaluate('e=>document.activeElement===e'),'outside close returns focus')
                page.locator('#opener').click()
                page.get_by_role('searchbox').fill('alpha')
                page.get_by_role('tab',name='Tools',exact=True).click()
                require(page.get_by_role('searchbox').input_value()=='','tabs have independent searches')
                page.get_by_role('tab',name='Skills',exact=True).click()
                require(page.get_by_role('searchbox').input_value()=='alpha','returning tab retains its search')
                page.get_by_role('tab',name='Tools',exact=True).focus()
                page.keyboard.press('ArrowRight')
                require(page.get_by_role('tab',name='MCP',exact=True).evaluate('e=>document.activeElement===e&&e.getAttribute("aria-selected")==="true"'),'tab keyboard semantics')
                page.get_by_role('tab',name='Tools',exact=True).click()
                page.evaluate("qa.ownerState('STALE')")
                page.get_by_role('button',name='Refresh catalog',exact=True).click()
                page.get_by_text('Configuration updated. Create a new conversation.',exact=True).wait_for()
                page.get_by_role('button',name='Conversation settings',exact=True).click()
                require(page.locator('#session-focus').evaluate('e=>document.activeElement===e'),'settings callback keeps destination focus')
                page.locator('#opener').click()
                page.evaluate('qa.owner(false)')
                page.get_by_role('tab',name='Tools',exact=True).click()
                page.get_by_role('button',name='Conversation settings',exact=True).wait_for()
                page.get_by_text('Select a conversation',exact=True).wait_for()
                require(page.get_by_text('Select a conversation',exact=True).count()==1,'unselected owner gets a direct settings action')
                outcome['checks'].append('close/outside/keyboard focus, per-tab search, stale and unselected owner settings')
                require(not outcome['console_errors'],str(outcome['console_errors']))
                outcome['status']='PASS'
            except Exception:
                if 'page' in locals():
                    page.screenshot(path=str(tmp_path/'failure.png'))
                    (tmp_path/'failure-dom.txt').write_text(page.locator('body').inner_text())
                raise
            finally:
                browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()
        (tmp_path/'BROWSER_RECEIPT.json').write_text(json.dumps(outcome,ensure_ascii=False,indent=2)+'\n')
