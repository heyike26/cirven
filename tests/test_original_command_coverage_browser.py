"""Small coverage-message addition: existing cards/draft/focus remain owned."""
import json
import os
from pathlib import Path
import test_agent_session_controls_browser as fixture
from test_original_command_results_browser import FIXTURE as ORIGINAL_FIXTURE

FIXTURE = ORIGINAL_FIXTURE.replace('reason_codes:[]', 'reason_codes:window.qa?.coverageReasons??[]').replace('commands:s.messages.map', 'commands:s.messages.slice(0,4).map').replace('window.qa={calls,pending,', 'window.qa={calls,pending,coverageReasons:[],coverage:reasons=>{window.qa.coverageReasons=reasons;window.qa.newCall();},')


def test_command_observation_coverage_is_readable_without_replacing_results(tmp_path, monkeypatch):
    from playwright.sync_api import expect
    evidence = Path(os.environ['EEAS_COMMAND_COVERAGE_EVIDENCE'])
    evidence.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fixture, 'FIXTURE', FIXTURE)
    cases = []
    with fixture.actual_panel(tmp_path) as (browser, url):
        for width, locale in [(1200, 'zh-CN'), (390, 'en')]:
            page = browser.new_page(viewport={'width': width, 'height': 650})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(url, wait_until='networkidle')
            page.evaluate('locale=>qa.locale(locale)', locale)
            section = page.locator('.as-command-results')
            expect(section.locator('.as-command-result')).to_have_count(3)
            notice = section.locator('[data-command-coverage]')
            expect(notice).to_have_count(0)
            stable = section.locator('[data-command-record="record_first_3"]')
            stable.evaluate('e=>window.coverageCard=e')
            section.locator('.as-command-result').evaluate_all('nodes=>window.coverageCards=nodes')
            draft = page.get_by_role('textbox', name='Draft')
            draft.focus()
            page.evaluate('qa.coverage(["COMMAND_GENERATION_CAPACITY_REACHED"])')
            expect(notice).to_have_text('本轮未启用新的命令记录，下方保留已有结果。' if locale == 'zh-CN' else 'New command records were not enabled for this generation. Existing results are kept below.')
            expect(section.locator('.as-command-result')).to_have_count(3)
            assert stable.evaluate('e=>e===window.coverageCard')
            assert section.locator('.as-command-result').evaluate_all('nodes=>nodes.every((e,i)=>e===window.coverageCards[i])')
            assert draft.evaluate('e=>document.activeElement===e')
            assert draft.input_value() == '人工草稿 must survive 😀'
            assert 'COMMAND_GENERATION_CAPACITY_REACHED' not in section.inner_text()
            assert section.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
            page.screenshot(path=str(evidence/f'capacity-{width}-{locale}.png'))
            page.evaluate('qa.coverage(["COMMAND_OPERATION_INDEX_UNAVAILABLE"])')
            expect(notice).to_have_text('部分命令结果尚无法核对，以下为已核对记录。' if locale == 'zh-CN' else 'Some command results could not be checked. Checked records are shown below.')
            assert stable.evaluate('e=>e===window.coverageCard')
            assert section.locator('.as-command-result').evaluate_all('nodes=>nodes.every((e,i)=>e===window.coverageCards[i])')
            assert draft.evaluate('e=>document.activeElement===e')
            assert 'COMMAND_OPERATION_INDEX_UNAVAILABLE' not in section.inner_text()
            page.screenshot(path=str(evidence/f'unavailable-{width}-{locale}.png'))
            page.evaluate('qa.coverage([])')
            expect(notice).to_have_count(0)
            assert draft.evaluate('e=>document.activeElement===e')
            assert page.evaluate('qa.calls.length') == 4
            assert page.evaluate('qa.calls.every(x=>x.method==="GET")')
            assert errors == []
            cases.append({'width':width,'locale':locale,'requests':4,'page_errors':errors})
            page.close()
    (evidence/'COVERAGE_RESULT.r1.json').write_text(json.dumps({'status':'PASS','scope':'SMALL_COVERAGE_STATUS_ADDITION_ACTUAL_SOLID_CLIENT_TRANSPORT_FIXTURE','cases':cases,'Windows':False,'model_calls':0,'command_execution':0},indent=2))
