"""Real live-message component: interrupted reply is distinct from unknown task."""
import json
import os
from pathlib import Path

import test_agent_session_controls_browser as fixture

FIXTURE = r'''
import {render} from 'solid-js/web';
import {createSignal} from 'solid-js';
import OriginalLiveMessage from '__SRC__/OriginalLiveMessage.tsx';
import '__SRC__/styles.css';import '__SRC__/agent-studio.css';
const [locale,setLocale]=createSignal('zh-CN');
const [message,setMessage]=createSignal({info:{id:'aborted',role:'assistant',error:{name:'MessageAbortedError'}},parts:[]});
window.qa={locale:setLocale,error:name=>setMessage({info:{id:name,role:'assistant',error:{name}},parts:[]})};
render(()=><main style={{padding:'16px'}}><OriginalLiveMessage message={message()} locale={locale()}/></main>,document.getElementById('app'));
'''


def test_interrupted_reply_is_readable_and_does_not_claim_whole_task_cancelled(tmp_path, monkeypatch):
    from playwright.sync_api import expect
    evidence = Path(os.environ['EEAS_REPLY_STATUS_EVIDENCE'])
    evidence.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fixture, 'FIXTURE', FIXTURE)
    with fixture.actual_panel(tmp_path) as (browser, url):
        for locale, text, unknown in [('zh-CN', '本次回复已中断，原输入已保留。', '处理结果待核查'),
                                      ('en', 'This reply was interrupted. Your input is kept.', 'Result needs checking')]:
            page = browser.new_page(viewport={'width': 430, 'height': 480})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            page.wait_for_function('window.qa')
            page.evaluate('l=>qa.locale(l)', locale)
            expect(page.get_by_role('status')).to_have_text(text)
            assert '任务已停止' not in page.locator('body').inner_text()
            assert 'task stopped' not in page.locator('body').inner_text()
            page.screenshot(path=str(evidence/f'interrupted-{locale}.png'))
            for name in ['UnknownError', 'MessageAbortedErrorOther', 'TransportTimeoutError']:
                page.evaluate('n=>qa.error(n)', name)
                expect(page.get_by_role('status')).to_contain_text(unknown)
            assert page.locator('pre').count() == 0
            assert page.locator('main').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
            assert errors == []
            page.close()
    (evidence/'RESULT.json').write_text(json.dumps({'status':'PASS', 'scope':'ACTUAL_LIVE_COMPONENT_BROWSER',
        'native_Windows_qualified':False, 'model_calls':0}, indent=2))
