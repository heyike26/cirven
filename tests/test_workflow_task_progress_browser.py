"""Actual progress component against successive recorded native observations."""
import json
import os
from pathlib import Path

import test_agent_session_controls_browser as fixture

FIXTURE = r'''
import {render} from 'solid-js/web';
import {createSignal} from 'solid-js';
import WorkflowProgress from '__SRC__/WorkflowProgress.tsx';
import {startUILanguage,setUILanguagePreference} from '__SRC__/ui-language.ts';
import '__SRC__/styles.css';
import '__SRC__/agent-studio.css';
startUILanguage();const[session,setSession]=createSignal(__SESSION__);
window.qa={replace:setSession,locale:setUILanguagePreference};
render(()=><div class="as-app-frame"><main style={{width:'100%',padding:'16px'}}><WorkflowProgress session={session()}/></main></div>,document.getElementById('app'));
'''


def test_same_generation_new_task_does_not_claim_previous_completed_todos(tmp_path, monkeypatch):
    evidence = Path(os.environ['EEAS_NATIVE_TASK_PROGRESS_EVIDENCE'])
    evidence.mkdir(parents=True, exist_ok=True)
    native = Path(os.environ['EEAS_NATIVE_TASK_RECORDS'])
    first = json.loads((native/'FIRST_TASK_READ_RECOVERY.r3.json').read_text(encoding='utf-8-sig'))['session']
    second = json.loads((native/'SECOND_TASK_HELD_API.r1.json').read_text(encoding='utf-8-sig'))['session']
    assert first['generation'] == second['generation'] and first['todo'] == second['todo']
    assert first['work_mode_message']['operation_id'] != second['work_mode_message']['operation_id']
    content = FIXTURE.replace('__SESSION__', json.dumps(first, ensure_ascii=False))
    if os.environ.get('EEAS_PROGRESS_BEFORE_COMPONENT'):
        before = tmp_path/'before.tsx'
        original = Path(os.environ['EEAS_PROGRESS_BEFORE_COMPONENT']).read_text()
        source = fixture.REPO/'apps/agent-shell/src'
        before.write_text(original.replace("'./ui-language'", repr('/@fs/'+str(source/'ui-language.ts'))))
        content = content.replace('__SRC__/WorkflowProgress.tsx', '/@fs/'+str(before))
    monkeypatch.setattr(fixture, 'FIXTURE', content)
    with fixture.actual_panel(tmp_path) as (browser, url):
        page = browser.new_page(viewport={'width': 430, 'height': 640})
        page.goto(url)
        summary = page.locator('.as-workflow-progress summary')
        summary.wait_for()
        page.evaluate("qa.locale('zh-CN')")
        from playwright.sync_api import expect
        expect(summary).to_contain_text('工作进度')
        assert '上次' not in summary.inner_text() and '2/2' in summary.inner_text()
        page.evaluate('s=>qa.replace(s)', second)
        expect(summary).to_contain_text('上次工作进度')
        summary.click()
        assert '尚未核实为当前任务' in page.locator('.as-workflow-progress').inner_text()
        page.screenshot(path=str(evidence/'same-generation-previous-todos-zh.png'))
        page.evaluate("qa.locale('en')")
        expect(summary).to_contain_text('Last observed')
        assert page.locator('.as-workflow-progress').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
        page.screenshot(path=str(evidence/'same-generation-previous-todos-en.png'))
        page.close()
    (evidence/'RESULT.json').write_text(json.dumps({'status': 'PASS', 'native_records': str(native),
        'scope': 'ACTUAL_BROWSER_COMPONENT_RECORDED_WINDOWS_OBSERVATIONS', 'native_windows_repair_qualified': False}, indent=2))
