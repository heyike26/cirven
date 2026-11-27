"""Actual App JSX + AssistantReply/CSS display fixture; no shared API or model."""
import argparse
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    out = Path(parser.parse_args().out).resolve()
    actual = (out / 'actual-summary.txt').read_text(encoding='utf8')
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(out)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    cases, unexpected, errors = [], [], []
    def settled(page):
        page.evaluate('''async()=>{await document.fonts.ready;await Promise.all(document.getAnimations().filter(a=>a.effect?.getComputedTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})));await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));}''')
    def inspect_reading(reply):
        expect(reply).to_be_visible()
        assert reply.locator('h3,h4,h5,h6').count() >= 1
        assert reply.locator('li').count() >= 1
        assert not re.search(r'^#{1,6}\s', reply.inner_text(), re.M)
        assert reply.locator('img,script,iframe,svg').count() == 0
        assert reply.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                for width, height in [(1440, 900), (430, 720)]:
                    context = browser.new_context(viewport={'width': width, 'height': height})
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.route('**/*', lambda route: route.continue_() if route.request.method == 'GET' and route.request.url.startswith(origin + '/') else (unexpected.append(route.request.url), route.abort()))
                    name = f'{width}x{height}'
                    try:
                        page.goto(origin + '/index.html', wait_until='networkidle')
                        page.wait_for_function('!!window.summaryFixture')
                        settled(page)
                        reply = page.locator('.as-agent-understanding > .as-assistant-reply')
                        inspect_reading(reply)
                        assert page.evaluate('summaryFixture.state().text') == actual
                        identity = page.evaluate('summaryFixture.state().identity')
                        field = page.get_by_role('textbox', name='Unsent draft')
                        field.fill('未发送的新草稿 ## literal')
                        handle = field.element_handle()
                        style = reply.evaluate('e=>({font:getComputedStyle(e).fontSize,line:getComputedStyle(e).lineHeight,width:e.getBoundingClientRect().width,heading:getComputedStyle(e.querySelector("h3,h4,h5,h6")).fontSize})')
                        assert style['font'] == '16px' and style['heading'] == '18px', style
                        assert style['width'] <= 760 and not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                        page.screenshot(path=str(out / f'{name}-actual-body.png'))
                        reply.locator(':scope > :last-child').scroll_into_view_if_needed()
                        settled(page)
                        assert reply.locator(':scope > :last-child').evaluate('''e=>{const r=e.getBoundingClientRect(),p=e.closest('.as-conversation').getBoundingClientRect();return r.top>=p.top-1&&r.bottom<=p.bottom+1;}''')
                        page.screenshot(path=str(out / f'{name}-actual-tail.png'))
                        page.get_by_role('button', name='Read historical version').click()
                        dialog = page.locator('dialog.as-history-preview')
                        expect(dialog).to_be_visible()
                        preview = dialog.locator('.as-assistant-reply')
                        inspect_reading(preview)
                        assert page.evaluate('summaryFixture.state().preview') == actual
                        assert dialog.locator(':scope > p').inner_text() == '## 用户原话保持字面量'
                        page.screenshot(path=str(out / f'{name}-history-body.png'))
                        preview.locator(':scope > :last-child').scroll_into_view_if_needed()
                        settled(page)
                        assert preview.locator(':scope > :last-child').evaluate('''e=>{const r=e.getBoundingClientRect(),p=e.closest('dialog').getBoundingClientRect();return r.top>=p.top-1&&r.bottom<=p.bottom-1;}''')
                        page.screenshot(path=str(out / f'{name}-history-tail.png'))
                        page.keyboard.press('Escape')
                        expect(dialog).not_to_be_visible()
                        security = '## 安全展示反例\n\n- 中文与 English\n- 保留业务编号 N1\n\n[可读参考](https://example.invalid/reference)\n\n[危险链接](javascript:alert(1))\n\n<img src="https://external.invalid/x" onerror="window.compromised=true">\n\n<script>window.compromised=true</script>\n\n```text\n' + 'LONG_CODE_' * 60 + '\n代码尾部 CODE_TAIL\n```\n\n安全末段。'
                        page.evaluate('text=>summaryFixture.setText(text)', security)
                        inspect_reading(reply)
                        assert reply.locator('a').count() == 1
                        assert reply.locator('a').get_attribute('href') == 'https://example.invalid/reference'
                        assert reply.locator('pre').text_content().endswith('代码尾部 CODE_TAIL\n')
                        assert page.evaluate('window.compromised===undefined')
                        current_url = page.url
                        reply.locator('a').click()
                        assert page.url == current_url and len(context.pages) == 1
                        code = reply.locator('pre')
                        code.focus()
                        page.keyboard.press('ArrowRight')
                        page.wait_for_function('document.querySelector(".as-agent-understanding pre").scrollLeft>0')
                        page.screenshot(path=str(out / f'{name}-safe-code.png'))
                        assert handle.evaluate('e=>e.isConnected') and field.input_value() == '未发送的新草稿 ## literal'
                        assert page.evaluate('summaryFixture.state().identity') == identity
                        assert page.evaluate('summaryFixture.state().calls') == []
                        cases.append({'viewport': [width, height], 'actual_summary_complete': True, 'history_preview_complete': True, 'style': style, 'unsafe_HTML_inert': True, 'link_navigation': 0, 'local_code_keyboard_scroll': True, 'draft_and_identity_preserved': True})
                    except BaseException:
                        page.screenshot(path=str(out / f'{name}-FAILURE.png'))
                        raise
                    finally:
                        context.close()
                assert not unexpected and not errors, (unexpected, errors)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        assert not thread.is_alive()
        (out / 'BROWSER_OBSERVATION.r1.json').write_text(json.dumps({'cases': cases, 'unexpected_requests': unexpected, 'page_errors': errors, 'server_joined': True}, ensure_ascii=False, indent=2) + '\n')
    receipt = {'status': 'PASS', 'scope': 'ACTUAL_APP_PRESENTATION_BOUNDARIES_WITH_REAL_COMPONENT_CSS_NOT_FULL_APP_STARTUP_OR_WINDOWS', 'cases': cases, 'original_summary_sha256': hashlib.sha256(actual.encode()).hexdigest(), 'model_calls': 0, 'shared_API_calls': 0}
    (out / 'BROWSER_RESULT.r1.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
