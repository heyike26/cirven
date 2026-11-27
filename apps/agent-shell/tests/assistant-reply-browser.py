"""Real component/browser fixture only; no application API, Windows or model."""
import argparse
import hashlib
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    out = Path(parser.parse_args().out).resolve()

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(out)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    scenes, requests = [], []
    long_word = 'COLUMN_' + 'W' * 160
    raw = ('## 完整回答 / Complete answer\n\n'
           '**粗体保留**，中文、English、🙂 都完整。\n\n'
           '> 引用内容 retained quote\n\n'
           '1. 首项 first\n2. 末项 last\n\n'
           '| 任务 / Task | 依据 / Basis |\n| --- | --- |\n'
           '| T01 | ' + long_word + ' |\n| TABLE_TAIL | 完整末行 |\n\n'
           '```python\n' + 'print("' + 'code_value_' * 32 + '")\nCODE_TAIL\n```\n\n'
           '[Safe web](https://example.com/review?q=1) · [Mail](mailto:review@example.com)\n\n'
           '[Blocked](javascript:alert(1))\n\n'
           '<img src="https://invalid.example/x" onerror="window.compromised=true">\n\n'
           '<script>window.compromised=true</script>\n\n' +
           '\n\n'.join(f'段落 {i} · Complete paragraph {i}，保留所有正文。' for i in range(75)) +
           '\n\nFINAL_TAIL_完整末段_🙂')
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 760, 'height': 640})
            page = context.new_page()
            page.on('request', lambda request: requests.append(request.url))
            for width, height, locale in [(760, 640, 'zh-CN'), (320, 390, 'en'), (420, 500, 'zh-CN')]:
                page.set_viewport_size({'width': width, 'height': height})
                page.goto(f'http://127.0.0.1:{server.server_port}/index.html')
                page.wait_for_function('!!window.replyFixture')
                assert page.evaluate('(locale)=>window.replyFixture.setLocale(locale)', locale) == locale
                page.evaluate('(text)=>window.replyFixture.setText(text)', raw)
                page.wait_for_function('document.querySelector(".as-assistant-reply table") && document.querySelector(".as-assistant-reply pre[tabindex]")')
                reply = page.locator('.as-assistant-reply')
                assert reply.locator('h3').inner_text() == '完整回答 / Complete answer'
                assert reply.locator('strong').first.inner_text() == '粗体保留'
                assert reply.locator('table tbody tr').count() == 2
                assert reply.locator('li').count() == 2
                assert reply.locator('details').count() == 0
                assert reply.locator('img,script,iframe,svg').count() == 0
                assert reply.locator('a').count() == 2
                assert page.evaluate('window.compromised === undefined')
                text = reply.inner_text()
                assert all(f'Complete paragraph {i}' in text for i in range(75))
                assert all(marker in text for marker in ['TABLE_TAIL', 'CODE_TAIL', long_word, 'FINAL_TAIL_完整末段_🙂'])
                geometry = page.evaluate('''()=>{
 const body=document.querySelector('.as-assistant-reply'),p=body.querySelector('p'),table=body.querySelector('.as-assistant-reply-table'),pre=body.querySelector('pre'),footer=document.querySelector('#last-action'),s=getComputedStyle(p),r=footer.getBoundingClientRect();
 return {body_overflow:document.documentElement.scrollWidth>innerWidth+1,font:s.fontSize,line:s.lineHeight,table_scroll:table.scrollWidth>table.clientWidth,code_scroll:pre.scrollWidth>pre.clientWidth,table_role:table.getAttribute('role'),table_label:table.getAttribute('aria-label'),code_label:pre.getAttribute('aria-label'),footer_visible:r.top>=0&&r.bottom<=innerHeight};
}''')
                assert geometry['body_overflow'] is False, geometry
                assert geometry['font'] == '14px' and abs(float(geometry['line'][:-2]) - 23.8) < .1, geometry
                assert geometry['table_scroll'] and geometry['code_scroll'], geometry
                assert geometry['table_role'] == 'region' and geometry['footer_visible'], geometry
                expected_labels = ('表格内容', '代码内容') if locale == 'zh-CN' else ('Table content', 'Code content')
                assert (geometry['table_label'], geometry['code_label']) == expected_labels, geometry
                page.screenshot(path=str(out / f'assistant-reply-{width}x{height}-{locale}-body.png'))
                for selector in ['.as-assistant-reply-table', '.as-assistant-reply pre']:
                    region = page.locator(selector)
                    region.focus()
                    assert region.evaluate('(node)=>document.activeElement===node')
                    page.keyboard.press('ArrowRight')
                    page.wait_for_function('(selector)=>document.querySelector(selector).scrollLeft>0', arg=selector, timeout=3000)
                    assert region.evaluate('(node)=>node.scrollLeft>0'), selector
                reply.locator('a').first.click()
                assert page.evaluate('window.replyFixture.opens') == ['https://example.com/review?q=1']
                page.evaluate('''()=>{window.replyFixture.opens.length=0;window.replyFixture.setLinks(false);}''')
                reply.locator('a').first.click()
                assert page.evaluate('window.replyFixture.opens') == []
                page.evaluate('window.replyFixture.setLinks(true)')
                reply.locator('a').first.click(button='middle')
                assert page.evaluate('window.replyFixture.opens') == []
                assert len(context.pages) == 1
                reply.locator('a').nth(1).click()
                assert page.evaluate('window.replyFixture.opens') == ['mailto:review@example.com']
                page.evaluate('window.replyFixture.opens.length=0')
                page.evaluate('''()=>{window.replyFixture.setLinks(true);document.querySelector('.as-assistant-reply a').click();}''')
                assert page.evaluate('window.replyFixture.opens') == []
                # Same prose through a surrounding receipt update retains actual
                # nodes, a focused local scroller, range and both scroll offsets.
                stable = page.evaluate('''()=>{
 const root=document.querySelector('.as-assistant-reply'),region=root.querySelector('.as-assistant-reply-table'),reading=document.querySelector('.reading'),p=root.querySelector('p'),text=p.querySelector('strong').firstChild;
 region.focus();region.scrollLeft=91;reading.scrollTop=151;const range=document.createRange();range.setStart(text,0);range.setEnd(text,4);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
 window.beforeReply={root,region,p,text,focus:document.activeElement,scroll:reading.scrollTop,horizontal:region.scrollLeft,quote:selection.toString()};window.replyFixture.receipt();return true;
}''')
                assert stable
                assert page.evaluate('''()=>{
 const b=window.beforeReply,root=document.querySelector('.as-assistant-reply');return root===b.root&&root.querySelector('.as-assistant-reply-table')===b.region&&root.querySelector('p')===b.p&&b.text.isConnected&&document.activeElement===b.focus&&document.querySelector('.reading').scrollTop===b.scroll&&b.region.scrollLeft===b.horizontal&&getSelection().toString()===b.quote;
}''')
                page.evaluate('''()=>{document.querySelector('.reading').scrollTop=document.querySelector('.reading').scrollHeight;}''')
                assert page.get_by_text('FINAL_TAIL_完整末段_🙂', exact=True).is_visible()
                assert page.get_by_text('FINAL_TAIL_完整末段_🙂', exact=True).evaluate('''node=>{
 const box=node.getBoundingClientRect(),clip=document.querySelector('.reading').getBoundingClientRect();return box.top>=clip.top&&box.bottom<=clip.bottom;
}''')
                page.locator('#last-action').focus()
                assert page.locator('#last-action').evaluate('(node)=>document.activeElement===node')
                page.screenshot(path=str(out / f'assistant-reply-{width}x{height}-{locale}-tail.png'))
                scenes.append({'width': width, 'height': height, 'locale': locale, 'body_overflow': False,
                               'complete_tail': True, 'keyboard_regions': True, **geometry})
            saved_receipt = None
            saved_path = out / 'saved-reply.json'
            if saved_path.exists():
                saved_raw = saved_path.read_bytes()
                value = json.loads(saved_raw)['value']
                assert value['operation_id'] == 'quick_6536a7bf-b792-4b05-92ca-2b6936fec327'
                assert len(value['text']) == 1860
                page.set_viewport_size({'width': 760, 'height': 640})
                page.goto(f'http://127.0.0.1:{server.server_port}/index.html')
                page.wait_for_function('!!window.replyFixture')
                page.evaluate('()=>window.replyFixture.setLocale("zh-CN")')
                page.evaluate('(text)=>window.replyFixture.setText(text)', value['text'])
                page.wait_for_function('document.querySelectorAll(".as-assistant-reply table").length===2')
                reply = page.locator('.as-assistant-reply')
                assert reply.locator('h3').count() == 3
                tasks = reply.locator('table').nth(1).locator('tbody tr')
                assert tasks.count() == 13
                actual_ids = tasks.locator('td:first-child').all_text_contents()
                assert actual_ids == [f'T{i:02d}' for i in list(range(1, 10)) + list(range(11, 15))]
                page.screenshot(path=str(out / 'assistant-reply-saved47-body.png'))
                page.evaluate('''()=>{document.querySelector('.reading').scrollTop=document.querySelector('.reading').scrollHeight;}''')
                assert reply.inner_text().endswith('本次只完成只读复核和原计划结构核对，未改文件，未启动任何计划任务。')
                assert reply.locator(':scope > p').last.evaluate('''node=>{
 const box=node.getBoundingClientRect(),clip=document.querySelector('.reading').getBoundingClientRect();return box.top>=clip.top&&box.bottom<=clip.bottom;
}''')
                page.screenshot(path=str(out / 'assistant-reply-saved47-tail.png'))
                saved_receipt = {'input_sha256': hashlib.sha256(saved_raw).hexdigest(),
                                 'text_sha256': hashlib.sha256(value['text'].encode()).hexdigest(),
                                 'operation_id': value['operation_id'], 'chars': 1860,
                                 'tables': 2, 'task_rows': 13, 'complete_tail': True,
                                 'scope': 'SAVED_47_TEXT_ON_ACTUAL_49_COMPONENT_ONLY_NOT_NEW_MODEL_OR_WINDOWS'}
            assert all(url.startswith(f'http://127.0.0.1:{server.server_port}/') for url in requests), requests
            context.close()
            browser.close()
        receipt = {'status': 'PASS', 'scenes': scenes, 'stable_receipt_dom': True,
                   'safe_links': True, 'external_requests': 0, 'model_calls': 0,
                   'application_API_calls': 0, 'Windows_qualification': False,
                   'saved_reply': saved_receipt}
        (out / 'BROWSER_RECEIPT.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(receipt, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
