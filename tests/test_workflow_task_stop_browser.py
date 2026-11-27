"""Actual in-chat stop controls, persisted unknown recovery and late scope results."""
import json,os
from pathlib import Path
import test_agent_session_controls_browser as fixture

FIXTURE=r'''
import {render} from 'solid-js/web';
import {createSignal} from 'solid-js';
import OriginalTaskControls from '__SRC__/OriginalTaskControls.tsx';
import {startUILanguage,setUILanguagePreference} from '__SRC__/ui-language.ts';
import '__SRC__/styles.css';import '__SRC__/agent-studio.css';
startUILanguage();const ref={id:'test-owner',revision:1,sha256:'a'.repeat(64)};
const initial={project_id:'test-project',session_ref:ref,generation:1,owner_status:'READY',connected_owner:true,configuration_current:true,observation_is_current:true,native_status:'busy',active_operation_id:'first-task',work_mode_message:{operation_id:'first-task',agent:'workflow',generation:1},messages:[],todo:[],children:[],pending_questions:[],pending_permissions:[],automatic_post_replay:false,last_event_seq:1};
const[session,setSession]=createSignal(initial),[draft,setDraft]=createSignal('Keep this exact draft 中文 😀 {"literal":true}');
let fail=localStorage.getItem('fixture-stop-fail')!=='no',calls=[],resolve;let delay=false,deferIdle=false,nativeIdle=false;
const receipt=(id,status='RETURNED')=>({id,status,request_sha256:'b'.repeat(64),automatic_replay:false,original_abort_acknowledged:true});
const client={interruptTask:async(s,target,key)=>{calls.push({method:'POST',target,key});if(delay)await new Promise(done=>resolve=done);if(fail)throw Error('possible dispatch');return receipt(key);},operation:async(s,key)=>{calls.push({method:'GET',key});if(fail)throw Error('result unavailable');return receipt(key,key==='first-task'?(deferIdle&&!nativeIdle?'ACCEPTED':'CANCELLED'):'RETURNED');},get:async()=>deferIdle&&!nativeIdle?initial:({...initial,active_operation_id:null})};
window.qa={deferIdle:()=>{fail=false;deferIdle=true;},idle:()=>{nativeIdle=true;setSession({...initial,active_operation_id:null});},locale:setUILanguagePreference,calls:()=>calls,resolve:()=>resolve?.(),delay:()=>{delay=true;fail=false;},succeed:()=>{fail=false;localStorage.setItem('fixture-stop-fail','no');},other:()=>setSession({...initial,active_operation_id:'second-task',work_mode_message:{...initial.work_mode_message,operation_id:'second-task'}}),current:()=>session(),expire:()=>setSession({...initial,owner_status:'EXPIRED',connected_owner:false})};
render(()=><div class="as-app-frame"><main style={{width:'100%','box-sizing':'border-box','max-width':'700px',padding:'16px',margin:'auto'}}><OriginalTaskControls session={session()} client={client} onFinished={()=>document.querySelector('textarea')?.focus()} onUpdated={setSession}/><textarea aria-label="Draft" style={{width:'100%','min-height':'140px'}} value={draft()} onInput={e=>setDraft(e.currentTarget.value)}/></main></div>,document.getElementById('app'));
'''

def test_actual_stop_button_unknown_recreation_and_scope_change(tmp_path,monkeypatch):
    from playwright.sync_api import expect
    evidence=Path(os.environ['EEAS_NATIVE_TASK_STOP_EVIDENCE']);evidence.mkdir(parents=True,exist_ok=True)
    monkeypatch.setattr(fixture,'FIXTURE',FIXTURE)
    with fixture.actual_panel(tmp_path)as(browser,url):
        for width,locale in [(780,'zh-CN'),(430,'zh-CN'),(430,'en')]:
            context=browser.new_context(viewport={'width':width,'height':480});page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(url);page.wait_for_function('window.qa');page.evaluate('(l)=>qa.locale(l)',locale)
            draft=page.get_by_role('textbox',name='Draft');original=draft.input_value();draft.evaluate('e=>window.originalDraft=e')
            stop=page.get_by_role('button',name='Stop current task'if locale=='en'else'停止当前任务',exact=True)
            page.screenshot(path=str(evidence/f'active-{width}-{locale}.png'))
            stop.click();check=page.get_by_role('button',name='Check stop result'if locale=='en'else'核查停止结果',exact=True);expect(check).to_be_enabled()
            assert len([x for x in page.evaluate('qa.calls()')if x['method']=='POST'])==1
            assert draft.input_value()==original and draft.evaluate('e=>e===window.originalDraft')
            page.screenshot(path=str(evidence/f'unknown-{width}-{locale}.png'))
            page.reload();page.wait_for_function('window.qa');page.evaluate('(l)=>qa.locale(l)',locale);expect(check).to_be_visible()
            assert page.evaluate('qa.calls()')==[],'recreation may not repeat the original stop'
            check.click();expect(check).to_be_enabled();assert all(x['method']=='GET'for x in page.evaluate('qa.calls()'))
            page.evaluate('qa.succeed()');check.click();expect(page.get_by_role('status')).to_contain_text('stopped'if locale=='en'else'已停止')
            assert draft.input_value()==original and page.locator('pre').count()==0
            expect(draft).to_be_focused()
            assert page.locator('.as-original-task-controls').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
            page.screenshot(path=str(evidence/f'stop-{width}-{locale}.png'));assert not errors
            context.close()
        context=browser.new_context();page=context.new_page();page.goto(url);page.wait_for_function('window.qa');page.evaluate("qa.locale('en');qa.deferIdle()")
        page.get_by_role('button',name='Stop current task',exact=True).click();expect(page.get_by_role('status')).to_contain_text('Stopping')
        page.evaluate('qa.idle()');expect(page.get_by_role('status')).to_contain_text('stopped')
        expect(page.get_by_role('textbox',name='Draft')).to_be_focused()
        assert len([x for x in page.evaluate('qa.calls()')if x['method']=='POST'])==1,'native idle completes the status without another click or POST'
        context.close()
        context=browser.new_context();page=context.new_page();page.goto(url);page.wait_for_function('window.qa');page.evaluate("qa.locale('en');qa.delay()")
        page.get_by_role('button',name='Stop current task',exact=True).click();page.evaluate('qa.other();qa.resolve()')
        page.wait_for_timeout(200);assert page.evaluate('qa.current().active_operation_id')=='second-task','late stop result cannot replace the new task'
        assert len([x for x in page.evaluate('qa.calls()')if x['method']=='POST'])==1
        page.evaluate('qa.expire()');expect(page.get_by_role('button',name='Stop current task',exact=True)).to_have_count(0)
        expect(page.get_by_role('status')).to_contain_text('Stop result needs checking')
        context.close()
    (evidence/'RESULT.json').write_text(json.dumps({'status':'PASS','scope':'ACTUAL_COMPONENT_BROWSER_LOCAL_CLIENT_FIXTURE','native_windows_stop_qualified':False,'model_calls':0},indent=2))
