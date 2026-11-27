"""Actual mode picker/progress/CSS and App mode handler; SDK observation is a fixture."""
import json
import os
from pathlib import Path

import test_agent_session_controls_browser as panel_fixture

REPO = Path(__file__).resolve().parents[1]
FIXTURE = r"""
import {render} from 'solid-js/web';
import {createSignal} from 'solid-js';
import TaskMethodPicker from '__SRC__/TaskMethodPicker.tsx';
import WorkflowProgress from '__SRC__/WorkflowProgress.tsx';
import {startUILanguage,setUILanguagePreference,uiLocale} from '__SRC__/ui-language.ts';
import type {OriginalAgent} from '__SRC__/agent-sessions.ts';
import '__SRC__/styles.css';
import '__SRC__/native-collaboration.css';
import '__SRC__/agent-studio.css';
import '__SRC__/brand-and-spacing.css';
import '__SRC__/chat-workbench-layout.css';
startUILanguage();
const id='original:'+'a'.repeat(64),key='eeas-conversation-agent-mode-v1:'+id;
const[originalAgent,setOriginalAgent]=createSignal<OriginalAgent>((localStorage.getItem(key) as OriginalAgent)||'build');
const[draft,setDraft]=createSignal('Keep exact user JSON {"literal":true} 中文 😀\\nline two');
const[error,setError]=createSignal(''),[session,setSession]=createSignal(__NATIVE_SESSION__);
const threadScope=()=>({metadata_source:{conversation_ref:{id}}});
__MODE_HANDLER__
const storageSet=Storage.prototype.setItem;
window.qa={locale:setUILanguagePreference,current:()=>originalAgent(),binding:()=>session().work_mode_message,
 unavailable:()=>setSession({...session(),workflow_mode_supported:false}),
 stale:()=>setSession({...session(),generation:session().generation+1,observation_is_current:false}),
 worst:()=>setSession({...session(),todo:Array.from({length:18},(_,i)=>({content:'任务 '+i+' LongUnbrokenName0123456789'.repeat(7)+' 中文 😀',status:i<3?'completed':i===3?'in_progress':'pending'}))}),
 failStorage:()=>{Storage.prototype.setItem=function(k,v){if(k===key)throw Error('fixture storage failure');return storageSet.call(this,k,v);};},restoreStorage:()=>{Storage.prototype.setItem=storageSet;}};
render(()=><div class="as-app-frame"><div class="as-shell" style={{display:'block',padding:'16px',height:'100vh',width:'100%','box-sizing':'border-box',overflow:'auto'}}>
 <main style={{'max-width':'760px',margin:'auto'}}><WorkflowProgress session={session()}/>
 <textarea aria-label="Chat draft" style={{width:'100%','min-height':'100px','margin-top':'16px'}} value={draft()} onInput={e=>setDraft(e.currentTarget.value)}/>
 <div class="as-composer-toolbar"><TaskMethodPicker mode="original" locale={uiLocale()} agentMode={originalAgent()} workflowUnavailable={session().workflow_mode_supported===false} activeTask={true} onAgentMode={changeConversationAgentMode} onChoose={()=>{throw Error('unexpected task navigation')}} onOpening={()=>{}}/></div>
 <p role="alert">{error()}</p></main></div></div>,document.getElementById('app'));
"""


def test_workflow_choice_progress_and_recovery_layout(tmp_path, monkeypatch):
    evidence = Path(os.environ['EEAS_WORKFLOW_BROWSER_EVIDENCE'])
    evidence.mkdir(parents=True, exist_ok=True)
    native = json.loads(Path(os.environ['EEAS_WORKFLOW_NATIVE_OBSERVATION']).read_text())['session']
    source = (REPO/'apps/agent-shell/src/App.tsx').read_text()
    start = source.index('function changeConversationAgentMode(')
    handler = source[start:source.index('\n', start)]
    monkeypatch.setattr(panel_fixture, 'FIXTURE', FIXTURE.replace('__NATIVE_SESSION__', json.dumps(native, ensure_ascii=False)).replace('__MODE_HANDLER__', handler))
    result = {'scope':'ACTUAL_SOLID_COMPONENTS_AND_APP_MODE_HANDLER_RECORDED_ORIGINAL_SDK_TODO', 'native_windows':False, 'cases':[]}
    with panel_fixture.actual_panel(tmp_path) as (browser, url):
        for width, locale in [(1280,'zh-CN'),(430,'zh-CN'),(1280,'en'),(430,'en')]:
            page = browser.new_page(viewport={'width':width,'height':640})
            errors, requests = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/**', lambda route:(requests.append(route.request.url),route.abort()))
            page.goto(url, wait_until='domcontentloaded')
            page.wait_for_function('window.qa || document.querySelector("vite-error-overlay")')
            assert not errors, str(errors)
            page.locator('.as-workflow-progress').wait_for()
            page.evaluate('(locale)=>qa.locale(locale)', locale)
            progress = page.locator('.as-workflow-progress')
            assert not progress.evaluate('e=>e.open')
            assert '1/1' in progress.locator('summary').inner_text()
            draft = page.get_by_role('textbox', name='Chat draft')
            original = draft.input_value()
            draft.evaluate('e=>window.originalDraft=e')
            picker = page.locator('.as-composer-tool')
            picker.click()
            menu = page.locator('#as-task-method-options')
            menu.get_by_role('button', name='Workflow', exact=False).click()
            assert page.evaluate('qa.current()')=='workflow'
            assert draft.input_value()==original and draft.evaluate('e=>e===window.originalDraft')
            assert 'Workflow' in picker.inner_text()
            assert picker.evaluate('e=>e===document.activeElement'), 'closing mode menu returns focus'
            picker.click();menu.get_by_text('Build' if locale=='en' else '执行', exact=True).click()
            assert page.evaluate('qa.current()')=='build'
            assert page.evaluate('qa.binding().agent')=='workflow', 'future preference cannot rewrite the active task'
            assert progress.is_visible()
            progress.locator('summary').click()
            assert progress.locator('li').inner_text().startswith(native['todo'][0]['content'])
            page.evaluate('qa.worst()')
            last = progress.locator('li').last
            last.scroll_into_view_if_needed()
            assert progress.locator('.as-workflow-progress-body').evaluate('e=>e.scrollTop>0')
            assert last.evaluate('e=>{const r=e.getBoundingClientRect(),p=e.closest(".as-workflow-progress-body").getBoundingClientRect();return r.left>=p.left&&r.right<=p.right+1&&r.bottom<=p.bottom+1}')
            page.screenshot(path=str(evidence/f'workflow-{width}-{locale}.png'))
            page.evaluate('qa.stale()')
            assert ('Last observed' in progress.inner_text()) if locale=='en' else ('上次工作进度' in progress.inner_text())
            page.evaluate('qa.failStorage()')
            picker.click();menu.get_by_role('button', name='Workflow', exact=False).click()
            assert page.evaluate('qa.current()')=='build' and page.locator('[role=alert]').inner_text()
            assert draft.input_value()==original
            page.evaluate('qa.restoreStorage();qa.unavailable()')
            picker.click()
            assert menu.get_by_role('button',name='Workflow',exact=False).is_disabled()
            page.keyboard.press('Escape')
            assert not errors and not requests
            result['cases'].append({'width':width,'locale':locale,'status':'PASS','API_requests':0,'model_calls':0,'draft_preserved':True,'active_mode_preserved':True})
            page.close()
    (evidence/'WORKFLOW_BROWSER_RECEIPT.r1.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
