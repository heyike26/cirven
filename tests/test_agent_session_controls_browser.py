"""Actual Solid panel + production CSS/locale; external session calls are fixtures.

This checks layout and local input handlers, not native Windows or a model session.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

REPO = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


FIXTURE = r'''
import {render} from 'solid-js/web';
import {createSignal} from 'solid-js';
import Panel from '__SRC__/AgentSessionPanel.tsx';
import OriginalPendingControls from '__SRC__/OriginalPendingControls.tsx';
import OriginalOwnerRecovery from '__SRC__/OriginalOwnerRecovery.tsx';
import {controlIdentity} from '__SRC__/original-pending-controls.ts';
import {agentSessionsApi,type OriginalAgent} from '__SRC__/agent-sessions.ts';
import {startUILanguage,setUILanguagePreference,uiLocale,t as uiText} from '__SRC__/ui-language.ts';
import '__SRC__/styles.css';
import '__SRC__/native-collaboration.css';
import '__SRC__/agent-studio.css';
import '__SRC__/brand-and-spacing.css';
import '__SRC__/chat-workbench-layout.css';
const H='a'.repeat(64), calls=[];
const question={kind:'QUESTION',request_ref:{id:'question_fixture',generation:2,sha256:H},allowed_choices:['reply','reject'],reply_is_execution_authorization:false,native_request:{questions:[
 {question:'选择处理方式 / Choose how to handle this saved document',multiple:false,custom:true,options:[
  {label:'保留原有人工修改 / Preserve the original manual changes',description:'原文与来源保持不变。Keep the original document and its exact source unchanged.'},
  {label:'另建可审阅版本 / Create a separate reviewable version',description:'独立新版本，不覆盖原稿。A separate version without overwriting the original.'}]},
 {question:'选择需要检查的项目 / Select the items to review',multiple:true,custom:false,options:[
  {label:'核对输入与输出 / Check the inputs and outputs',description:'保留每项真实来源。Keep each original source.'},
  {label:'核对长名称和可编辑内容 / Review long names and editable content',description:'很长的文件标识LongFileIdentifierWithoutAnySpaces0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789应在文字列换行。'}]}
]}};
const session={project_id:'fixture-project',session_ref:{id:'fixture-session',revision:1,sha256:H},owner_status:'READY',generation:2,connected_owner:true,configuration_current:true,observation_is_current:true,native_status:'idle',workspace_kind:'REGISTERED_WORKSPACE',original_session_id:'ses_fixture',messages:[],todo:[],children:[],pending_questions:[question],pending_permissions:[],active_operation_id:'fixture-parent',last_event_seq:0,automatic_post_replay:false};
agentSessionsApi.list=async()=>{calls.push({method:'list'});return[];};
agentSessionsApi.get=async()=>{calls.push({method:'get'});return session;};
agentSessionsApi.pendingReply=async(_owner,item,choice,answers)=>{calls.push({method:'reply',request_ref:item.request_ref,choice,answers});return{id:'fixture-reply',status:'RETURNED'};};
startUILanguage();
const[pendingOwner,setPendingOwner]=createSignal(session),[recoveryAttempt,setRecoveryAttempt]=createSignal(),[hasDraft,setHasDraft]=createSignal(false);
const[selectedOwner,setSelectedOwner]=createSignal(session);
window.qa={calls,locale:setUILanguagePreference,expire:()=>{window.qa.showStale(false);setPendingOwner(s=>({...s,owner_status:'EXPIRED',connected_owner:false,observation_is_current:false}));},unconfirmed:()=>{setRecoveryAttempt({phase:'ATTEMPTED',generation:2,parent:'fixture-parent',original_session_id:'ses_fixture'});setPendingOwner(s=>({...s,generation:3,owner_status:'READY',connected_owner:true,observation_is_current:true,pending_questions:[],pending_permissions:[]}));},finishRecovery:()=>{setRecoveryAttempt({phase:'RETURNED',generation:2,parent:'fixture-parent',original_session_id:'ses_fixture'});setPendingOwner(s=>({...s,generation:3,owner_status:'READY',connected_owner:true,observation_is_current:true,active_operation_id:null,pending_questions:[],pending_permissions:[]}));},draft:v=>setHasDraft(v),showStale:(failed=true)=>{
 const path='D:/当前项目/说明.txt',permission={kind:'PERMISSION',request_ref:{id:'per_stale',generation:2,sha256:H},allowed_choices:['once','reject'],reply_is_execution_authorization:true,native_request:{permission:'edit',patterns:[path],metadata:{filepath:path,diff:'@@ -1 +1 @@\n-状态：初稿\n+状态：已复核\n'}}};
 const next={...session,pending_questions:[],pending_permissions:[permission]};
 const key='eeas-original-controls-v1:'+JSON.stringify([session.project_id,session.session_ref]);if(failed)localStorage.setItem(key,JSON.stringify({version:1,attempts:[{identity:controlIdentity(next,permission),operationId:'fixture-stale',choice:'once',status:'FAILED',sourceChanged:true}]}));else localStorage.removeItem(key);setPendingOwner(next);
}};
window.qa.failedRecovery=()=>{setRecoveryAttempt({phase:'FAILED',generation:2,originGeneration:2,parent:'fixture-parent',original_session_id:'ses_fixture'});setPendingOwner(s=>({...s,generation:3,owner_status:'BLOCKED',connected_owner:false,observation_is_current:false,active_operation_id:null,pending_questions:[question],pending_permissions:[]}));};
window.qa.expireAgain=()=>setPendingOwner(s=>({...s,owner_status:'EXPIRED',connected_owner:false,observation_is_current:false}));
window.qa.owner=patch=>setSelectedOwner({...session,...patch});
agentSessionsApi.get=async()=>{calls.push({method:'get'});return selectedOwner();};
// Exact production App JSX fragment; only its surrounding session state is a fixture.
const[originalAgent,setOriginalAgent]=createSignal<OriginalAgent>('plan');
function Composer(){const originalSession=()=>({provider_profile_ref:{id:'fixture-provider'}});const chooseTool=()=>{};return __COMPOSER__;}
render(()=><div class="as-app-frame" style={{height:'auto',overflow:'visible'}}><div class="as-shell" style={{display:'block',width:'100%',height:'auto','min-height':'100vh',padding:'16px','box-sizing':'border-box'}}><Composer/><OriginalPendingControls session={pendingOwner()} locale={uiLocale()} ownerRecovery={(pendingOwner().owner_status==='EXPIRED'||recoveryAttempt())?<OriginalOwnerRecovery session={pendingOwner()} attempt={recoveryAttempt()} busy={false} unavailable={false} hasDraft={hasDraft()} error="" locale={uiLocale()} onResume={()=>calls.push({method:'resume'})} onInspect={()=>calls.push({method:'inspect'})} onRestore={()=>calls.push({method:'restore'})}/>:undefined} onUpdated={()=>{}}/><div class="as-session-management"><Panel agent={originalAgent()} active={false} projectId="fixture-project" workspace={{id:'fixture-workspace',project_id:'fixture-project',revision:1,binding_sha256:H,root:'/fixture',allowed_write_paths:['src/']}} provider={{id:'fixture-provider',revision:1,sha256:H}} selected={selectedOwner()} onSelect={()=>{}} onUse={()=>{}}/></div></div></div>,document.getElementById('app'));
'''


@contextmanager
def actual_panel(tmp_path):
    from playwright.sync_api import sync_playwright
    app = REPO / 'apps/agent-shell'
    fixture = tmp_path / 'browser'
    fixture.mkdir()
    (fixture / 'index.html').write_text('<!doctype html><meta charset="utf-8"><div id="app"></div><script type="module" src="/fixture.tsx"></script>')
    source = (app / 'src/App.tsx').read_text()
    start = source.index('<div class="as-owner-composer-mode">')
    end = source.index('</div>', start) + len('</div>')
    (fixture / 'fixture.tsx').write_text(FIXTURE.replace('__SRC__', '/@fs/' + str(app / 'src')).replace('__COMPOSER__', source[start:end]))
    (fixture / 'vite.config.mjs').write_text(f'''import solid from {json.dumps(str(app/'node_modules/vite-plugin-solid/dist/esm/index.mjs'))};export default {{root:{json.dumps(str(fixture))},cacheDir:{json.dumps(str(fixture/'vite-cache'))},plugins:[solid()],resolve:{{dedupe:['solid-js'],alias:{{'solid-js':{json.dumps(str(app/'node_modules/solid-js'))}}}}},server:{{hmr:false,host:'127.0.0.1',strictPort:true,fs:{{allow:[{json.dumps(str(REPO))},{json.dumps(str(tmp_path))}]}}}}}};''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, TMPDIR=str(tmp_path), TMP=str(tmp_path), TEMP=str(tmp_path), NODE_COMPILE_CACHE=str(tmp_path / 'node-cache'))
    log = (tmp_path / 'vite.log').open('w')
    server = subprocess.Popen([str(REPO / '.node-tools/node_modules/node/bin/node'), str(app / 'node_modules/vite/bin/vite.js'), '--config', str(fixture / 'vite.config.mjs'), '--port', str(port)], cwd=fixture, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}', timeout=1):
                    break
            except OSError:
                require(server.poll() is None, 'own fixture Vite exited')
                time.sleep(.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                yield browser, f'http://127.0.0.1:{port}'
            finally:
                browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()


def test_connection_progressive_disclosure_and_scope(tmp_path):
    evidence = Path(os.environ.get('EEAS_CONTROL_EVIDENCE', str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    result = {'scope': 'ACTUAL_SOLID_PANEL_PRODUCTION_CSS_EXTERNAL_SESSION_FIXTURE',
              'windows': False, 'model_calls': 0, 'cases': []}
    with actual_panel(tmp_path) as (browser, url):
        for width, locale in [(1280, 'zh-CN'), (430, 'zh-CN'), (1280, 'en'), (430, 'en')]:
            page = browser.new_page(viewport={'width': width, 'height': 640})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/**', lambda route: route.abort())
            page.goto(url, wait_until='domcontentloaded', timeout=90000)
            panel = page.locator('.as-owner-manager')
            panel.wait_for(timeout=90000)
            page.evaluate('(locale)=>qa.locale(locale)', locale)
            require(panel.get_attribute('aria-label') == ('Agent connection and permissions' if locale == 'en' else 'Agent 连接与权限'), 'connection controls have a distinct responsibility from chat organization')
            page.locator('.as-capability-form>summary').click()
            advanced = page.locator('.as-owner-advanced')
            require(not advanced.evaluate('e=>e.open'), 'raw policies start collapsed')
            require(not panel.locator('textarea').first.is_visible(), 'raw command lists are absent from the basic path')
            basic = page.locator('.as-owner-create>label input[type=checkbox]')
            require(basic.count() == 2 and all(box.is_visible() and not box.is_checked() for box in basic.all()), 'workspace choice and explicit write permission stay visible and off')
            advanced.locator('summary').click()
            commands = panel.locator('textarea').nth(0)
            tools = panel.locator('textarea').nth(1)
            exact = 'python -c "literal JSON {a:1}"\n中文 😀 second command'
            commands.fill(exact)
            tools.fill('fixture.local_tool\nfixture.remote_tool')
            consents = page.locator('.as-owner-create>label input[type=checkbox]')
            require(consents.count() == 4 and all(not box.is_checked() for box in consents.all()), 'manual tool names expose required consents without granting them')
            advanced.locator('summary').click()
            require(page.locator('.as-owner-configured').is_visible(), 'closed advanced settings signal configured values')
            require(commands.input_value() == exact and not commands.is_visible(), 'collapse retains exact local draft and removes it from the reading path')
            require(all(box.is_visible() and not box.is_checked() for box in consents.all()), 'necessary MCP consent stays visible even with advanced settings closed')
            mode = page.locator('.as-owner-composer-mode select')
            mode.select_option('build')
            require(commands.input_value() == exact, 'same-session mode selection preserves connection drafts')
            advanced.locator('summary').click()
            require(commands.input_value() == exact and tools.input_value() == 'fixture.local_tool\nfixture.remote_tool', 'reopening preserves both original lists')
            advanced.locator('summary').click()
            page.evaluate('qa.owner({workspace_ref:{id:"fixture-workspace",revision:1,binding_sha256:"a".repeat(64)}})')
            require('/fixture' in page.locator('.as-owner-workspace').inner_text(), 'directory comes from an exact workspace reference')
            require('ses_fixture' not in page.locator('.as-owner-workspace').inner_text(), 'SDK identity is not displayed as a directory')
            page.evaluate('qa.owner({workspace_ref:{id:"fixture-workspace",revision:2,binding_sha256:"a".repeat(64)},connected_owner:false,configuration_current:false})')
            require(page.locator('.as-owner-workspace').inner_text() == ('Verify the working directory' if locale == 'en' else '工作目录需重新核对'), 'mismatched workspace revisions never display another directory')
            actions = page.locator('.as-session-owner-controls .as-capability-actions>button')
            require(actions.nth(0).is_enabled() and actions.nth(1).is_disabled(), 'stale configuration permits inspection and prevents invalid reconnect')
            warning = page.locator('.as-session-owner-controls>.as-capability-result').inner_text()
            require(('Model or tool settings changed.' in warning) if locale == 'en' else ('配置已变化' in warning), 'configuration cause takes precedence over the generic disconnected status')
            page.evaluate('qa.owner({project_id:"other-project",workspace_ref:{id:"fixture-workspace",revision:1,binding_sha256:"a".repeat(64)},original_session_id:"ses_"+"长Name".repeat(80)})')
            require('/fixture' not in page.locator('.as-owner-workspace').inner_text(), 'a matching workspace identifier in a different project is insufficient')
            identity = page.locator('.as-owner-identity')
            require(not identity.evaluate('e=>e.open'), 'technical connection identity starts collapsed')
            identity.locator('summary').click()
            require(identity.locator('dd').inner_text().startswith('ses_'), 'full original identity remains inspectable')
            require(identity.evaluate('e=>e.scrollWidth<=e.clientWidth+1'), 'long connection identity wraps inside its available column')
            page.locator('.as-capability-form>summary').scroll_into_view_if_needed()
            page.screenshot(path=str(evidence / f'connection-{width}-{locale}.png'), full_page=True)
            require(page.evaluate('qa.calls.length') == 0, 'local disclosure, scope and mode interactions do not call the SDK or model')
            require(not errors, str(errors))
            result['cases'].append({'width': width, 'height': 640, 'locale': locale, 'status': 'PASS', 'sdk_calls': 0, 'errors': errors})
            page.close()
    (evidence / 'CONNECTION_RESPONSIBILITY_RECEIPT.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


def test_actual_session_choices_inline_and_interactive(tmp_path):
    evidence = Path(os.environ.get('EEAS_CONTROL_EVIDENCE', str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    outcome = {'scope': 'ACTUAL_SOLID_PANEL_PRODUCTION_CSS_EXTERNAL_SESSION_BOUNDARY', 'cases': [], 'page_errors': [], 'unexpected_api': [], 'model_calls': 0, 'windows': False}
    phase = os.environ.get('EEAS_CONTROL_PHASE', 'after')
    with actual_panel(tmp_path) as (browser, url):
        try:
            for width, locale in [(1280, 'zh-CN'), (430, 'zh-CN'), (1280, 'en'), (430, 'en')]:
                page = browser.new_page(viewport={'width': width, 'height': 1000})
                page.on('pageerror', lambda error: outcome['page_errors'].append(str(error)))
                page.route('**/api/**', lambda route: (outcome['unexpected_api'].append(route.request.url), route.abort()))
                page.goto(url, wait_until='domcontentloaded', timeout=90000)
                page.locator('.as-owner-manager').wait_for(timeout=90000)
                page.evaluate('(locale)=>qa.locale(locale)', locale)
                page.locator('.as-capability-form>summary').click()
                page.locator('.as-owner-advanced>summary').click()
                page.screenshot(path=str(evidence / f'{phase}-{width}-{locale}.png'), full_page=True)
                measured = page.locator('.as-owner-manager input:is([type=checkbox],[type=radio]),.as-pending-card input:is([type=checkbox],[type=radio])').evaluate_all('''items=>items.map(input=>{const r=input.getBoundingClientRect(),label=input.closest('label'),text=label.querySelector('span'),t=text?.getBoundingClientRect(),s=getComputedStyle(input);return{type:input.type,width:r.width,height:r.height,left:r.left,top:r.top,right:r.right,disabled:input.disabled,labelWidth:label.getBoundingClientRect().width,textLeft:t?.left,textTop:t?.top,textRight:t?.right,direction:getComputedStyle(label).flexDirection,padding:s.padding};})''')
                row = {'width': width, 'locale': locale, 'controls': measured}
                row['owner_mode'] = {'text':page.locator('.as-owner-mode').inner_text(),'select_count':page.locator('.as-owner-mode select').count()}
                outcome['cases'].append(row)
                (evidence / 'BROWSER_RECEIPT.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + '\n')
                require(len(measured) == 7, 'three applicable creation options and four actual pending choices; unselected MCP grants stay absent')
                require(all(18 <= item['width'] <= 20 and 18 <= item['height'] <= 20 for item in measured), 'checkbox/radio must remain 18–20px, including disabled controls')
                require(all(item['direction'] == 'row' and item['textLeft'] >= item['right'] + 7 and abs(item['textTop'] - item['top']) <= 4 for item in measured), 'each control must align beside its independently wrapping text')
                require(all(item['textRight'] <= width - 15 for item in measured), 'long option copy must fit the content column')
                require(row['owner_mode']['select_count']==0, 'manager reads the composer agent, without a second conflicting selector')
                require('fixture-provider' not in page.locator('.as-owner-composer-mode').inner_text(), 'composer must not repeat provider technical metadata')
                mode = page.locator('.as-owner-composer-mode select')
                require(mode.input_value() == 'plan', 'original default agent remains plan')
                mode.select_option('build');require(mode.input_value() == 'build', 'composer changes originalAgent directly without opening another panel');require('Build' in page.locator('.as-owner-mode').inner_text(),'manager reflects the same current agent')
                mode.select_option('plan')
                mode_rect = mode.bounding_box()
                row['composer'] = {**mode_rect, 'fontSize': mode.evaluate("el=>getComputedStyle(el).fontSize")}
                require(mode_rect['width'] >= 88 and 30 <= mode_rect['height'] <= 40, 'composer selector retains a compact usable size')
                require(mode.evaluate("el=>getComputedStyle(el).fontSize") == '13px', 'composer uses the requested control type role')
                require(page.locator('.as-owner-composer-mode').evaluate('el=>el.scrollWidth<=el.clientWidth+1'), 'composer mode wraps within the viewport')
                create = page.locator('.as-owner-create input[type=checkbox]')
                require(all(not create.nth(i).is_checked() for i in range(3)), 'all effect defaults remain false')
                require(create.nth(1).is_disabled() and create.nth(2).is_disabled(), 'write/workflow preconditions remain disabled')
                create.nth(0).locator('..').click()
                require(create.nth(0).is_checked() and create.nth(1).is_enabled(), 'whole option label selects workspace and preserves write gating')
                create.nth(1).locator('..').click()
                require(create.nth(1).is_checked(), 'authorized write option remains locally selectable')
                create.nth(0).locator('..').click()
                require(not create.nth(0).is_checked() and create.nth(1).is_disabled(), 'deselect workspace disables the write option')
                row['write_after_workspace_deselection'] = {'checked': create.nth(1).is_checked(), 'disabled': create.nth(1).is_disabled()}
                require(not create.nth(1).is_checked(), 'deselect workspace clears the explicit write choice')
                create.nth(0).locator('..').click()
                require(create.nth(1).is_enabled() and not create.nth(1).is_checked(), 'reselecting workspace never restores a previous write grant')
                create.nth(1).locator('..').click()
                require(create.nth(1).is_checked(), 'write requires another explicit selection')
                create.nth(0).focus();page.keyboard.press('Space')
                require(not create.nth(0).is_checked() and not create.nth(1).is_checked(), 'keyboard workspace deselection clears the dependent write choice')
                page.keyboard.press('Space');require(create.nth(0).is_checked() and not create.nth(1).is_checked(), 'keyboard reselect never restores write permission')
                textareas = page.locator('.as-owner-create textarea')
                textareas.nth(0).fill('preserve exact draft\nline two')
                for box in textareas.all():
                    rect = box.bounding_box()
                    require(rect['width'] > 150 and rect['height'] >= 90, 'textareas retain full input geometry')
                radio = page.locator('.as-pending-card input[type=radio]')
                radio.nth(0).locator('..').click();radio.nth(1).locator('..').click()
                require(not radio.nth(0).is_checked() and radio.nth(1).is_checked(), 'single-choice remains exclusive')
                multi = page.locator('.as-pending-card input[type=checkbox]')
                multi.nth(0).locator('..').click();multi.nth(1).locator('..').click()
                require(all(box.is_checked() for box in multi.all()), 'multi-choice remains independent')
                require(page.locator('.as-owner-manager .as-owner-question').count()==0,'management has no duplicate question form')
                page.locator('.as-pending-other summary').click()
                custom = page.locator('.as-pending-custom textarea')
                custom.fill('Original answer 原始回答')
                require(all(not radio.nth(i).is_checked() for i in range(radio.count())),'custom single answer clears stale radio selection')
                require(custom.bounding_box()['width'] > 150 and custom.bounding_box()['height'] >= 30, 'custom text input is not constrained to checkbox dimensions')
                page.evaluate('(locale)=>qa.locale(locale)', 'en' if locale == 'zh-CN' else 'zh-CN')
                require(textareas.nth(0).input_value() == 'preserve exact draft\nline two' and custom.input_value() == 'Original answer 原始回答', 'locale switch preserves local input drafts')
                require(page.evaluate('qa.calls.length') == 0, 'local layout/input interactions dispatch no external session action')
                page.locator('.as-pending-other').evaluate("e=>e.open=true")
                row['interaction'] = 'PASS_LABEL_KEYBOARD_GATES_RADIO_MULTI_DRAFTS_NO_DISPATCH'
                page.close()
            page = browser.new_page(viewport={'width':320,'height':480})
            page.goto(url,wait_until='domcontentloaded');page.locator('.as-owner-manager').wait_for(timeout=90000)
            page.evaluate("async()=>{await qa.locale('en');qa.showStale()}")
            from playwright.sync_api import expect
            alert=page.locator('.as-pending-card [role=alert]');expect(alert).to_contain_text('The file changed')
            preview=page.locator('.as-pending-patch pre');removed=preview.locator('[data-kind=remove]');added=preview.locator('[data-kind=add]');expect(removed).to_have_text('-状态：初稿');expect(added).to_have_text('+状态：已复核')
            before,after=removed.bounding_box(),added.bounding_box();require(after['y']>=before['y']+before['height']-1 and abs(before['x']-after['x'])<=1,'actual removed/added lines are stacked vertically, never placed side by side')
            outcome['cases'].append({'diff_before':before,'diff_after':after,'line_order':'VERTICAL_EXACT_CONTENT'})
            reject=page.locator('.as-pending-card').get_by_role('button',name='Reject old proposal',exact=True)
            reject.scroll_into_view_if_needed();page.screenshot(path=str(evidence/'stale-320-en.png'))
            for label,control in [('error',alert),('reject',reject)]:
                geometry=control.evaluate("e=>{const r=e.getBoundingClientRect();let c={l:0,t:0,r:innerWidth,b:innerHeight};for(let p=e.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),a=p.getBoundingClientRect();if(s.overflowX!=='visible'){c.l=Math.max(c.l,a.left);c.r=Math.min(c.r,a.right)}if(s.overflowY!=='visible'){c.t=Math.max(c.t,a.top);c.b=Math.min(c.b,a.bottom)}}return{rect:{l:r.left,t:r.top,r:r.right,b:r.bottom},clip:c}}")
                outcome['cases'].append({'stale_320_en':label,**geometry});r,c=geometry['rect'],geometry['clip'];require(r['l']>=c['l']-1 and r['t']>=c['t']-1 and r['r']<=c['r']+1 and r['b']<=c['b']+1,'stale error and reject must be visible together through every clip ancestor')
            page.close()
            require(not outcome['page_errors'], str(outcome['page_errors']))
            require(not outcome['unexpected_api'], str(outcome['unexpected_api']))
            outcome['status'] = 'PASS'
        finally:
            (evidence / 'BROWSER_RECEIPT.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + '\n')


def test_actual_pending_diff_default_visible_with_approval(tmp_path):
    from playwright.sync_api import expect
    expect.set_options(timeout=30000)
    evidence=Path(os.environ.get('EEAS_CONTROL_EVIDENCE',str(tmp_path)));evidence.mkdir(parents=True,exist_ok=True)
    result={'scope':'ACTUAL_PRODUCTION_SHARED_CARD_WITH_CONTROLLED_ORIGINAL_REQUEST_NO_API_OR_MODEL','cases':[]}
    try:
        with actual_panel(tmp_path) as(browser,url):
            for width,height,locale in [(1280,820,'zh-CN'),(360,560,'en'),(320,480,'en')]:
                page=browser.new_page(viewport={'width':width,'height':height});page.goto(url,wait_until='domcontentloaded');page.locator('.as-owner-manager').wait_for(timeout=90000);page.evaluate("async locale=>{await qa.locale(locale);qa.showStale(false)}",locale)
                card=page.locator('.as-pending-card');expect(card.locator('header')).to_have_text('Review file changes'if locale=='en'else'确认文件修改')
                for name,control in [('before',card.locator('[data-kind=remove]')),('after',card.locator('[data-kind=add]')),('allow',card.get_by_role('button',name='Allow once'if locale=='en'else'仅允许这一次',exact=True))]:
                    expect(control).to_be_visible();g=control.evaluate("e=>{const r=e.getBoundingClientRect();let c={l:0,t:0,r:innerWidth,b:innerHeight};for(let p=e.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),a=p.getBoundingClientRect();if(s.overflowX!=='visible'){c.l=Math.max(c.l,a.left);c.r=Math.min(c.r,a.right)}if(s.overflowY!=='visible'){c.t=Math.max(c.t,a.top);c.b=Math.min(c.b,a.bottom)}}return{rect:{l:r.left,t:r.top,r:r.right,b:r.bottom},clip:c,hit:e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}}")
                    result['cases'].append({'width':width,'height':height,'locale':locale,'control':name,**g});r,c=g['rect'],g['clip'];require(r['l']>=c['l']-1 and r['t']>=c['t']-1 and r['r']<=c['r']+1 and r['b']<=c['b']+1 and g['hit'],'fresh changes and approval must be visible together before any scroll')
                page.screenshot(path=str(evidence/f'fresh-diff-{width}-{locale}.png'));page.close()
        result['status']='PASS_INITIAL_DIFF_AND_APPROVAL_VISIBLE'
    finally:
        (evidence/'FRESH_DIFF_RECEIPT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))


def test_expired_owner_recovery_is_visible_and_old_permissions_inactive(tmp_path):
    from playwright.sync_api import expect
    expect.set_options(timeout=30000)
    evidence=Path(os.environ.get('EEAS_CONTROL_EVIDENCE',str(tmp_path)));evidence.mkdir(parents=True,exist_ok=True)
    result={'scope':'PRODUCTION_CARD_RECOVERY_COMPONENT_AND_CSS_WITH_EXTERNAL_OWNER_BOUNDARY_NO_APP_OR_NATIVE_ACCOUNT','cases':[]}
    try:
        with actual_panel(tmp_path) as(browser,url):
            for width,height,locale in [(1280,820,'zh-CN'),(360,560,'en'),(320,480,'en')]:
                page=browser.new_page(viewport={'width':width,'height':height});page.goto(url,wait_until='domcontentloaded');page.locator('.as-owner-manager').wait_for(timeout=90000)
                page.evaluate("async locale=>{await qa.locale(locale);qa.expire()}",locale)
                recovery=page.locator('[data-original-owner-recovery]');expect(recovery).to_contain_text('connection expired'if locale=='en'else'连接已到期')
                button=recovery.get_by_role('button',name='Reconnect this chat'if locale=='en'else'恢复此会话',exact=True);expect(button).to_be_enabled()
                page.locator('.as-original-pending').evaluate("async e=>{await Promise.all(e.getAnimations({subtree:true}).filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})))}")
                g=button.evaluate("e=>{const r=e.getBoundingClientRect();let c={l:0,t:0,r:innerWidth,b:innerHeight};for(let p=e.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),a=p.getBoundingClientRect();if(s.overflowX!=='visible'){c.l=Math.max(c.l,a.left);c.r=Math.min(c.r,a.right)}if(s.overflowY!=='visible'){c.t=Math.max(c.t,a.top);c.b=Math.min(c.b,a.bottom)}}return{rect:{l:r.left,t:r.top,r:r.right,b:r.bottom},clip:c,hit:e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}}")
                result['cases'].append({'width':width,'height':height,'locale':locale,**g});r,c=g['rect'],g['clip'];require(r['l']>=c['l']-1 and r['t']>=c['t']-1 and r['r']<=c['r']+1 and r['b']<=c['b']+1 and g['hit'],'expired explanation and primary recovery must be available before scrolling')
                # The old proposal is readable on demand, never an enabled
                # response surface. No hidden old allow action is clickable.
                expect(page.locator('.as-expired-proposal')).not_to_have_attribute('open','')
                page.locator('.as-expired-proposal > summary').click();expect(page.locator('.as-pending-card button[data-primary=true]')).to_have_count(0)
                page.locator('.as-expired-proposal > summary').click();page.wait_for_function("document.querySelector('.as-original-pending').scrollTop===0")
                for name,control in [('explanation',recovery.locator('p').first),('reconnect',button),('focused_summary',page.locator('.as-expired-proposal > summary'))]:
                    state=control.evaluate("e=>{const r=e.getBoundingClientRect(),p=e.closest('.as-original-pending').getBoundingClientRect();return{top:r.top,bottom:r.bottom,clipTop:p.top,clipBottom:p.bottom}}")
                    require(state['top']>=state['clipTop']-1 and state['bottom']<=state['clipBottom']+1,'closing the inactive request must restore the complete explanation and primary action together')
                    result['cases'].append({'width':width,'height':height,'locale':locale,'after_close':name,**state})
                page.screenshot(path=str(evidence/f'expired-recovery-{width}-{locale}.png'));button.click()
                require(page.evaluate("qa.calls.filter(c=>c.method==='resume').length")==1,'one explicit recovery handler')
                page.evaluate('qa.unconfirmed()');expect(page.locator('[data-original-owner-recovery]')).to_contain_text('Reconnection needs checking'if locale=='en'else'恢复结果待核查');expect(page.locator('[data-original-owner-recovery]').get_by_role('button',name='Check original reconnection'if locale=='en'else'核查原恢复结果',exact=True)).to_be_enabled()
                page.evaluate('qa.failedRecovery()');expect(recovery).to_contain_text('did not complete'if locale=='en'else'原恢复已确认未完成')
                retry=recovery.get_by_role('button',name='Try reconnecting this chat'if locale=='en'else'重新恢复此会话',exact=True);expect(retry).to_be_enabled();retry.click()
                expect(recovery.get_by_role('button',name='Restore original request'if locale=='en'else'取回原要求',exact=True)).to_have_count(0)
                expect(page.locator('.as-expired-proposal')).not_to_have_attribute('open','')
                expect(page.locator('.as-pending-card input:visible')).to_have_count(0)
                page.screenshot(path=str(evidence/f'failed-recovery-{width}-{locale}.png'))
                page.evaluate('qa.finishRecovery();qa.draft(true)')
                restore=page.locator('[data-original-owner-recovery]').get_by_role('button',name='Restore original request'if locale=='en'else'取回原要求',exact=True);expect(restore).to_be_disabled()
                page.evaluate('qa.draft(false)');expect(restore).to_be_enabled();restore.click()
                require(page.evaluate("qa.calls.filter(c=>c.method==='restore').length")==1,'restore requires explicit action')
                require(page.evaluate("qa.calls.filter(c=>c.method==='reply').length")==0,'expired controls never reply or grant')
                page.screenshot(path=str(evidence/f'reconnected-recovery-{width}-{locale}.png'))
                page.evaluate('qa.expireAgain()');expect(recovery).to_contain_text('connection expired'if locale=='en'else'连接已到期')
                expect(recovery.get_by_role('button',name='Reconnect this chat'if locale=='en'else'恢复此会话',exact=True)).to_be_enabled()
                expect(recovery.get_by_role('button',name='Restore original request'if locale=='en'else'取回原要求',exact=True)).to_have_count(0)
                page.close()
        result['status']='PASS_VISIBLE_EXPLICIT_RECOVERY_NO_OLD_PERMISSION'
    finally:
        (evidence/'EXPIRED_RECOVERY_RECEIPT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
