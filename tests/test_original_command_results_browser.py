"""Actual Solid command-result cards; transport fixtures, no execution/model/native App."""
import json
import os
from pathlib import Path

import test_agent_session_controls_browser as fixture

FIXTURE = r'''
import {render} from 'solid-js/web';import {createSignal} from 'solid-js';
import OriginalCommandResults from '__SRC__/OriginalCommandResults.tsx';
import {OriginalCommandObservationsApi} from '__SRC__/original-command-observations.ts';
import {ApiClient} from '__SRC__/api.ts';import {originalFileScope} from '__SRC__/original-session-files.ts';
import '__SRC__/styles.css';import '__SRC__/agent-studio.css';
const H='a'.repeat(64),calls=[],pending=[];let failed=false,delay=false;
const make=(name='first',generation=2,count=4)=>({project_id:'project',session_ref:{id:name,revision:1,sha256:H},generation,workspace_ref:{id:'b'.repeat(64),revision:1,binding_sha256:'c'.repeat(64)},original_session_id:'ses_'+name,active_operation_id:'task-still-running',pending_permissions:[],pending_questions:[],messages:Array.from({length:count},(_,i)=>({info:{id:'assistant_'+i,role:'assistant',sessionID:'ses_'+name,parentID:'user_'+i},parts:[{id:'part_'+i,messageID:'assistant_'+i,sessionID:'ses_'+name,type:'tool',tool:'bash',callID:'call_'+i,state:{status:i===2?'error':'completed'}}]}))});
const snapshot=()=>({state:'UTF8',sha256:H,bytes:4,line_count:1,blob:'blobs/'+H+'.utf8',stat:{dev:'1',ino:'2',size:'4',mtime_ns:'3',ctime_ns:'4'}});
async function catalog(s){const output='combined output\n中文结果 😀\n'+'LongOutputWithoutSpaces'.repeat(32),digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(output)))].map(v=>v.toString(16).padStart(2,'0')).join('');return {schema:'eeas.original-command-observations.v1',scope:originalFileScope(s),snapshot_ref:{id:'snapshot',revision:1,sha256:H},commands:s.messages.map((m,i)=>({record_ref:{id:'record_'+s.session_ref.id+'_'+i,revision:1,sha256:H},operation_id:'operation_'+i,root_operation_id:'operation_'+i,native_message_id:m.info.parentID,message_id:m.info.id,part_id:m.parts[0].id,call_id:m.parts[0].callID,source_generation:s.generation,source_scope:originalFileScope(s),command:s.session_ref.id+' python -m unittest '+(i===3?'超长文件名称NoSpaces'.repeat(32):'tests/测试.py'),workdir:'D:/工作项目/LongWorkingDirectory'.repeat(4),exit_code:i===2?null:i===1?1:0,status:i===2?'UNKNOWN_OUTCOME':'EXITED',source_status:i===2?'UNAVAILABLE':i===1?'CHANGED':'UNCHANGED',sources:[{path:'源码/'+('很长的文件名WithoutSpaces'.repeat(8))+'.py',before:snapshot(),after:i===2?null:i===1?{...snapshot(),stat:{...snapshot().stat,mtime_ns:'9'}}:snapshot()}],output:i===2?null:{text:output,sha256:digest,bytes:new TextEncoder().encode(output).length,truncated:i===3,original_truncated:i===3,original_output_path:i===3?'D:/saved/original-output.txt':null},qualification:'ORIGINAL_COMMAND_OBSERVATION_ONLY'})),coverage:{basis_scope:'DECLARED_FILES_ONLY',all_dependencies_captured:false,reason_codes:[]},read_only:true,model_calls:0,automatic_replay:false};}
const original=OriginalCommandObservationsApi.prototype.list;
OriginalCommandObservationsApi.prototype.list=function(s,signal){const client=new OriginalCommandObservationsApi(new ApiClient(async(url,init)=>{if(url==='/api/bootstrap')return Response.json({csrf_token:'fixture'});calls.push({url,method:init.method,signal:init.signal});const body=await catalog(s);if(delay){delay=false;return new Promise(resolve=>pending.push({resolve:()=>resolve(Response.json(body)),signal:init.signal}));}if(failed){failed=false;return Response.json({error:'unavailable'},{status:503});}return Response.json(body);}));return original.call(client,s,signal);};
const[session,setSession]=createSignal(make()),[locale,setLocale]=createSignal('zh-CN'),[draft,setDraft]=createSignal('人工草稿 must survive 😀');
window.qa={calls,pending,locale:setLocale,idle:()=>setSession(s=>({...s,observation_at:String(Date.now()),last_event_seq:99,active_operation_id:null})),newCall:()=>setSession(s=>make(s.session_ref.id,s.generation,s.messages.length+1)),switch:name=>setSession(make(name)),generation:()=>setSession(s=>make(s.session_ref.id,s.generation+1)),fail:()=>{failed=true;window.qa.newCall();},delay:()=>{delay=true;window.qa.newCall();},resolve:()=>pending.splice(0).forEach(x=>x.resolve()),aborted:()=>pending.every(x=>x.signal.aborted)};
render(()=><main class="fixture" style={{'max-width':'900px',margin:'0 auto',padding:'16px','box-sizing':'border-box'}}><textarea aria-label="Draft" value={draft()} onInput={e=>setDraft(e.currentTarget.value)} style={{width:'100%','box-sizing':'border-box','font-size':'14px'}}/><OriginalCommandResults session={session()} contextKey={'thread:'+session().session_ref.id} locale={locale()}/></main>,document.getElementById('app'));
'''


def test_command_results_layout_lifecycle_and_recovery(tmp_path, monkeypatch):
    from playwright.sync_api import expect
    evidence = Path(os.environ['EEAS_COMMAND_RESULTS_EVIDENCE'])
    evidence.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fixture, 'FIXTURE', FIXTURE)
    results = []
    with fixture.actual_panel(tmp_path) as (browser, url):
        for width, locale in [(1200, 'zh-CN'), (390, 'zh-CN'), (1200, 'en'), (390, 'en')]:
            page = browser.new_page(viewport={'width': width, 'height': 650})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(url, wait_until='networkidle')
            page.evaluate('locale=>qa.locale(locale)', locale)
            section = page.locator('.as-command-results')
            expect(section.locator('.as-command-result')).to_have_count(3)
            assert page.evaluate('qa.calls.length') == 1
            assert page.evaluate('qa.calls.every(x=>x.method==="GET")')
            assert '测试通过' not in section.inner_text() and 'Tests passed' not in section.inner_text()
            assert section.locator('details[open]').count() == 0
            draft = page.get_by_role('textbox', name='Draft')
            draft.focus()
            for _ in range(3):
                page.evaluate('qa.idle()')
            page.wait_for_timeout(60)
            assert page.evaluate('qa.calls.length') == 1
            assert draft.evaluate('e=>document.activeElement===e')
            assert draft.input_value() == '人工草稿 must survive 😀'
            # Real local progressive disclosure: no extra request or execution.
            section.locator('.as-command-result>details>summary').last.click()
            expect(section.locator('.as-command-details').last).to_be_visible()
            assert ('已截断' if locale == 'zh-CN' else 'Truncated') in section.inner_text()
            assert ('执行期间' if locale == 'zh-CN' else 'during the command') in section.inner_text()
            section.locator('.as-command-result').last.get_by_text('源码指纹' if locale == 'zh-CN' else 'Source fingerprints', exact=False).click()
            assert section.locator('.as-command-source').last.is_visible()
            assert page.evaluate('qa.calls.length') == 1
            assert section.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
            assert page.locator('main').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
            page.screenshot(path=str(evidence/f'cards-{width}-{locale}.png'), full_page=True)
            # New arrivals retain a focused older card instead of evicting its DOM.
            first_summary = section.locator('.as-command-result>details>summary').first
            first_summary.focus()
            first_identity = section.locator('.as-command-result').first.get_attribute('data-command-record')
            page.evaluate('qa.newCall()')
            expect(section.locator('.as-command-result')).to_have_count(5)
            assert page.locator('[data-command-record="'+first_identity+'"]>details>summary').evaluate('e=>document.activeElement===e')
            section.get_by_role('button', name='收起' if locale == 'zh-CN' else 'Show fewer', exact=True).click()
            expect(section.locator('.as-command-result')).to_have_count(3)
            section.get_by_role('button', name='全部命令' if locale == 'zh-CN' else 'All commands', exact=False).click()
            expect(section.locator('.as-command-result')).to_have_count(5)
            # Read failure retains draft and offers only GET recovery; trigger stays focused.
            draft.focus()
            page.evaluate('qa.fail()')
            retry = section.get_by_role('button', name='重新核对' if locale == 'zh-CN' else 'Check again', exact=True)
            expect(retry).to_have_attribute('aria-disabled', 'false')
            assert draft.evaluate('e=>document.activeElement===e')
            retry.click()
            expect(retry).to_have_attribute('aria-disabled', 'false')
            expect(section).to_have_attribute('aria-busy', 'false')
            assert retry.evaluate('e=>document.activeElement===e')
            assert page.evaluate('qa.calls.length') == 4
            assert draft.input_value() == '人工草稿 must survive 😀'
            # A delayed result is ignored after session and generation switches, even if transport ignores abort.
            page.evaluate('qa.delay()')
            page.wait_for_function('qa.pending.length===1')
            draft.focus()
            page.evaluate('qa.switch("second")')
            expect(section.locator('.as-command-result')).to_have_count(3)
            page.wait_for_function('document.querySelector(".as-command-result-title code")?.textContent.startsWith("second")')
            assert page.evaluate('qa.aborted()')
            page.evaluate('qa.resolve()')
            page.wait_for_timeout(60)
            assert all(s.startswith('second') for s in section.locator('.as-command-result-title code').all_text_contents())
            assert draft.evaluate('e=>document.activeElement===e')
            assert draft.input_value() == '人工草稿 must survive 😀'
            page.evaluate('qa.delay()')
            page.wait_for_function('qa.pending.length===1')
            page.evaluate('qa.generation()')
            page.wait_for_function('qa.calls.length===8')
            assert page.evaluate('qa.aborted()')
            page.evaluate('qa.resolve()')
            page.wait_for_timeout(60)
            assert page.evaluate('qa.calls.every(x=>x.method==="GET")')
            assert errors == []
            results.append({'width': width, 'locale': locale, 'requests': page.evaluate('qa.calls.length'), 'page_errors': errors})
            page.close()
    (evidence/'BROWSER_RESULT.r1.json').write_text(json.dumps({'status':'PASS', 'scope':'ACTUAL_SOLID_COMPONENT_AND_PRODUCTION_CLIENT_EXTERNAL_TRANSPORT_FIXTURE', 'cases':results, 'Windows':False, 'model_calls':0, 'command_execution':0}, indent=2))
