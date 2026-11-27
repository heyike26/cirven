"""Production result cards + live transcript: one visible command result.

The transport is a controlled fixture. This tests the actual composition,
identity guard and DOM; it is not a full App or Windows acceptance claim.
"""
import json
import os
from pathlib import Path

import test_agent_session_controls_browser as fixture
from test_original_command_results_browser import FIXTURE as BASE


FIXTURE = BASE.replace("import {createSignal} from 'solid-js';", "import {createSignal,createMemo,For,Show} from 'solid-js';")
FIXTURE = FIXTURE.replace("import OriginalCommandResults", "import OriginalLiveMessage from '__SRC__/OriginalLiveMessage.tsx';\nimport {displayedCommandKeys} from '__SRC__/original-command-display.ts';\nimport OriginalCommandResults", 1)
FIXTURE = FIXTURE.replace("import OriginalCommandResults", "import {stableItems} from '__SRC__/stable-items.ts';\nimport OriginalCommandResults", 1)
FIXTURE = FIXTURE.replace('const make=', 'let make=', 1)
FIXTURE = FIXTURE.replace("import OriginalCommandResults", "import {startUILanguage,setUILanguagePreference} from '__SRC__/ui-language.ts';startUILanguage();\nimport OriginalCommandResults", 1)
FIXTURE = FIXTURE.replace('locale:setLocale,', 'locale:value=>{setLocale(value);setUILanguagePreference(value);},', 1)
EXTRAS = r'''
const baseMake=make;
make=(...args)=>{const s=baseMake(...args),m=s.messages[0];m.parts.push(
 {id:'authored_text',type:'text',text:'AUTHORED_ANSWER_MUST_STAY'},
 ...[['pending','bash','pending'],['running','bash','running'],['edit','edit','completed']].map(([id,tool,status])=>({id:'extra_'+id,type:'tool',tool,callID:'call_extra_'+id,sessionID:s.original_session_id,messageID:m.info.id,state:{status}}))
);return s;};
'''
FIXTURE = FIXTURE.replace('const snapshot=()', EXTRAS + '\nconst snapshot=()', 1)
FIXTURE = FIXTURE.replace('window.qa={calls,pending,', "const[coverage,setCoverage]=createSignal(),[showResults,setShowResults]=createSignal(true);const contextKey=()=> 'thread:'+session().session_ref.id;const keys=createMemo(()=>displayedCommandKeys(coverage(),session(),contextKey()));const originalMessageRows=stableItems(()=>session()?.messages??[],message=>JSON.stringify([session()?.project_id,session()?.session_ref,session()?.generation,message.info.id,message.info.role]));\nwindow.qa={calls,pending,hideResults:()=>setShowResults(false),", 1)
OLD = '<OriginalCommandResults session={session()} contextKey={\'thread:\'+session().session_ref.id} locale={locale()}/>'
NEW = '<For each={originalMessageRows()}>{message=><OriginalLiveMessage message={message} locale={locale()} displayedCommands={keys()}/>}</For><OriginalLiveMessage message={{info:{id:"user_file",role:"user",sessionID:"ses_first"},parts:[],input_file:{filename:"USER_FILE_ONLY.txt"}}} locale={locale()} displayedCommands={keys()}/><Show when={showResults()}><OriginalCommandResults session={session()} contextKey={contextKey()} locale={locale()} onCoverage={setCoverage}/></Show>'
assert FIXTURE.count(OLD) == 1
FIXTURE = FIXTURE.replace(OLD, NEW, 1)


def test_visible_checked_cards_replace_only_their_exact_terminal_captions(tmp_path, monkeypatch):
    from playwright.sync_api import expect
    evidence = Path(os.environ['EEAS_COMMAND_DISPLAY_EVIDENCE'])
    evidence.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fixture, 'FIXTURE', FIXTURE)
    results = []
    with fixture.actual_panel(tmp_path) as (browser, url):
        for width, locale in [(1200, 'zh-CN'), (390, 'en')]:
            page = browser.new_page(viewport={'width': width, 'height': 650})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url, wait_until='networkidle')
            page.evaluate('locale=>qa.locale(locale)', locale)
            section = page.locator('.as-command-results')
            captions = page.locator('.as-history-message:not([data-original-live-message="user_file"]) .as-capability-result')
            expect(page.get_by_text('USER_FILE_ONLY.txt', exact=True)).to_be_visible()
            expect(section.locator('.as-command-result')).to_have_count(3)
            for index in (1, 2, 3):
                expect(page.locator(f'[data-original-live-message="assistant_{index}"]')).to_have_count(0)
            # The older undisplayed call keeps its fallback, as do pending,
            # running and the non-bash tool. Checked error has one result card.
            expect(captions).to_have_count(4)
            expect(page.get_by_text('AUTHORED_ANSWER_MUST_STAY', exact=True)).to_have_count(1)
            page.get_by_text('AUTHORED_ANSWER_MUST_STAY', exact=True).evaluate('e=>window.authoredNode=e')
            assert page.evaluate('qa.calls.length') == 1
            section.get_by_role('button', name='全部命令' if locale == 'zh-CN' else 'All commands', exact=False).click()
            expect(section.locator('.as-command-result')).to_have_count(4)
            expect(captions).to_have_count(3)
            assert page.get_by_text('AUTHORED_ANSWER_MUST_STAY', exact=True).evaluate('e=>e===window.authoredNode')
            assert page.evaluate('qa.calls.length') == 1
            draft = page.get_by_role('textbox', name='Draft')
            draft.focus()
            page.evaluate('qa.fail()')
            retry = section.get_by_role('button', name='重新核对' if locale == 'zh-CN' else 'Check again')
            expect(retry).to_have_attribute('aria-disabled', 'false')
            # Retained checked cards still own their four captions. The new
            # unobserved call keeps its fallback after this GET failure.
            expect(section.locator('.as-command-result')).to_have_count(4)
            expect(captions).to_have_count(4)
            assert draft.evaluate('e=>document.activeElement===e')
            assert draft.input_value() == '人工草稿 must survive 😀'
            assert page.get_by_text('AUTHORED_ANSWER_MUST_STAY', exact=True).evaluate('e=>e===window.authoredNode')
            page.screenshot(path=str(evidence/f'checked-and-fallback-{width}-{locale}.png'), full_page=True)
            page.evaluate('qa.hideResults()')
            expect(section).to_have_count(0)
            expect(captions).to_have_count(8)
            assert page.get_by_text('AUTHORED_ANSWER_MUST_STAY', exact=True).evaluate('e=>e===window.authoredNode')
            assert draft.evaluate('e=>document.activeElement===e')
            assert page.evaluate('qa.calls.length') == 2
            assert errors == []
            assert page.locator('main').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
            results.append({'width': width, 'locale': locale, 'GETs': 2, 'page_errors': errors})
            page.close()
    (evidence/'DISPLAY_RESULT.r1.json').write_text(json.dumps({'status': 'PASS', 'scope': 'PRODUCTION_COMPONENT_COMPOSITION_WITH_TRANSPORT_FIXTURE', 'cases': results, 'Windows': False, 'model_calls': 0, 'command_execution': 0}, ensure_ascii=False, indent=2))
