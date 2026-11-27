"""Exact visible-command metadata handoff; no parent App/LiveMessage edits."""
import json
import os
from pathlib import Path
import test_agent_session_controls_browser as fixture
from test_original_command_results_browser import FIXTURE as ORIGINAL_FIXTURE

FIXTURE = ORIGINAL_FIXTURE.replace("import {createSignal} from 'solid-js';", "import {createSignal,Show} from 'solid-js';").replace('const[session,setSession]', 'const[mounted,setMounted]=createSignal(true);const[session,setSession]').replace('window.qa={calls,pending,', 'window.qa={calls,pending,coverageEvents:[],unmount:()=>setMounted(false),').replace('<OriginalCommandResults session={session()} contextKey={\'thread:\'+session().session_ref.id} locale={locale()}/>', '<Show when={mounted()}><OriginalCommandResults session={session()} contextKey={\'thread:\'+session().session_ref.id} locale={locale()} onCoverage={value=>window.qa.coverageEvents.push(structuredClone(value))}/></Show>')


def test_only_visible_scoped_keys_are_published_and_old_coverage_clears(tmp_path, monkeypatch):
    from playwright.sync_api import expect
    evidence = Path(os.environ['EEAS_COMMAND_DISPLAY_COVERAGE_EVIDENCE'])
    evidence.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fixture, 'FIXTURE', FIXTURE)
    with fixture.actual_panel(tmp_path) as (browser, url):
        page = browser.new_page(viewport={'width':390, 'height':650})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(url, wait_until='networkidle')
        page.wait_for_function('qa.coverageEvents.length>0')
        current = lambda: page.evaluate('qa.coverageEvents.at(-1)')
        assert [v['call_id'] for v in current()['entries']] == ['call_1','call_2','call_3']
        assert current()['scope']['generation'] == 2 and current()['scope']['workspace_ref']['revision'] == 1
        assert current()['original_session_id'] == 'ses_first' and current()['contextKey'] == 'thread:first'
        section = page.locator('.as-command-results')
        section.get_by_role('button', name='全部命令', exact=False).click()
        page.wait_for_function('qa.coverageEvents.at(-1).entries.length===4')
        section.get_by_role('button', name='收起', exact=True).click()
        page.wait_for_function('qa.coverageEvents.at(-1).entries.length===3')
        kept = current()
        draft = page.get_by_role('textbox', name='Draft')
        draft.focus()
        page.evaluate('qa.fail()')
        retry = section.get_by_role('button', name='重新核对', exact=True)
        expect(retry).to_have_attribute('aria-disabled','false')
        assert current() == kept
        assert draft.evaluate('e=>document.activeElement===e')
        retry.click()
        expect(section).to_have_attribute('aria-busy','false')
        page.wait_for_function('qa.coverageEvents.at(-1).entries.at(-1).call_id==="call_4"')
        page.evaluate('qa.delay()')
        page.wait_for_function('qa.pending.length===1')
        page.evaluate('qa.switch("second")')
        page.wait_for_function('qa.coverageEvents.at(-1).original_session_id==="ses_second"')
        assert page.evaluate('qa.coverageEvents.some(v=>v.original_session_id==="ses_first"&&v.entries.length===0)')
        before_resolve = current()
        page.evaluate('qa.resolve()')
        page.wait_for_timeout(60)
        assert current() == before_resolve
        page.evaluate('qa.generation()')
        page.wait_for_function('qa.coverageEvents.at(-1).scope.generation===3')
        assert page.evaluate('qa.coverageEvents.some(v=>v.original_session_id==="ses_second"&&v.scope.generation===2&&v.entries.length===0)')
        page.evaluate('qa.unmount()')
        expect(section).to_have_count(0)
        assert current()['scope']['generation'] == 3 and current()['original_session_id'] == 'ses_second' and current()['entries'] == []
        assert page.evaluate('qa.calls.every(x=>x.method==="GET")')
        assert errors == []
        value={'status':'PASS','scope':'ACTUAL_SOLID_CALLBACK_VISIBLE_KEYS_AND_SCOPE_CLEAR_TRANSPORT_FIXTURE','events':page.evaluate('qa.coverageEvents'),'requests':page.evaluate('qa.calls.length'),'page_errors':errors,'Windows':False,'model_calls':0,'command_execution':0}
        (evidence/'DISPLAY_COVERAGE_RESULT.r1.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
        page.close()
