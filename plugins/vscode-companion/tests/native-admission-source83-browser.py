"""Production webview with controlled VS Code messages; no native/model qualification."""
import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import subprocess
import threading

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / 'plugins/vscode-companion'
NODE = ROOT / '.node-tools/node_modules/node/bin/node'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    out = parser.parse_args().out.resolve(); out.mkdir(exist_ok=False)
    (out / 'tmp').mkdir()
    (out / 'host-theme.css').write_text(':root{--vscode-font-size:18px;}')

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            target = {'/': out / 'webview.html', '/host-theme.css': out / 'host-theme.css',
                '/media/view.css': PLUGIN / 'media/view.css', '/media/view.js': PLUGIN / 'media/view.js',
                '/media/vendor/marked.js': PLUGIN / 'media/vendor/marked.js'}.get(self.path)
            if target is None:self.send_error(404); return
            raw = target.read_bytes(); self.send_response(200)
            self.send_header('Content-Type', 'text/html;charset=utf-8' if self.path == '/' else
                'text/css' if self.path.endswith('.css') else 'text/javascript')
            self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def log_message(self, *_args):pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    env = dict(os.environ, TMPDIR=str(out / 'tmp'), TEMP=str(out / 'tmp'), TMP=str(out / 'tmp'))
    records, errors = [], []
    try:
        result = subprocess.run([str(NODE), str(PLUGIN / 'tests/theme-html.cjs'), origin,
            str(out / 'webview.html'), str(out / 'html-state')], cwd=ROOT, env=env, capture_output=True, text=True)
        (out / 'html-factory.log').write_text(result.stdout + result.stderr); assert result.returncode == 0
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, env=env)
            for locale, width in [('zh-CN', 320), ('en', 320), ('zh-CN', 620), ('en', 620)]:
                context = browser.new_context(viewport={'width': width, 'height': 520})
                context.add_init_script('window.__posted=[];window.acquireVsCodeApi=()=>({getState:()=>undefined,setState:x=>window.__saved=x,postMessage:x=>window.__posted.push(x)});')
                page = context.new_page(); page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(origin, wait_until='networkidle')
                page.evaluate('()=>{const link=document.createElement("link");link.rel="stylesheet";link.href="/host-theme.css";document.head.append(link)}')
                page.wait_for_function('()=>getComputedStyle(document.documentElement).getPropertyValue("--vscode-font-size").trim()==="18px"')
                parent = {'native_message_phase': 'STARTING', 'control_capabilities': {'can_guide': False, 'can_cancel': True}}
                messages = [{'info': {'id': str(i), 'role': 'assistant'}, 'parts': [{'type': 'text',
                    'text': f'第 {i} 段 / Original answer paragraph {i}. 保留人工内容 😀。'}]} for i in range(30)]
                session = {'kind': 'NATIVE_CONVERSATION', 'project_id': 'project', 'conversation_id': 'discussion',
                    'messages': messages, 'active_operation_id': 'parent', 'active_native_message': parent}
                snapshot = {'projectId': 'project', 'projects': [{'id': 'project', 'name': '当前工程'}],
                    'session': session, 'sessions': [session], 'sessionTokens': ['c' * 64], 'scopeToken': 'a' * 64,
                    'draft': {'text': 'Keep the exact new draft 中文 😀', 'revision': 0}, 'pending': [],
                    'recoverable': [], 'status': {'connected': True}, 'draftUnresolved': False}

                def emit():
                    page.evaluate('m=>window.postMessage(m,"*")', {'type': 'state', 'connection': 'PAIRED',
                        'trusted': True, 'locale': locale, 'notice': '', 'snapshot': snapshot})

                emit(); entry = page.locator('#input'); entry.evaluate('e=>window.__input=e')
                expect(page.locator('#send')).to_be_disabled(); expect(page.locator('#cancel')).to_be_visible()
                assert page.locator('#operation-status').inner_text() == ('原任务启动中' if locale == 'zh-CN' else 'Starting the task')
                before = page.evaluate('()=>window.__posted.filter(x=>x.type==="send").length')
                entry.press('Control+Enter'); assert page.evaluate('()=>window.__posted.filter(x=>x.type==="send").length') == before
                parent['native_message_phase'] = 'STOP_REQUESTED'; parent['control_capabilities']['can_cancel'] = False; emit()
                expect(page.locator('#cancel')).to_be_hidden(); expect(page.locator('#send')).to_be_disabled()
                assert page.locator('#operation-status').inner_text() == ('正在停止原任务' if locale == 'zh-CN' else 'Stopping the task')
                session.pop('active_operation_id'); session.pop('active_native_message')
                snapshot['draftUnresolved'] = True
                snapshot['pending'] = [{'request_id': 'original-rpc', 'method': 'native.message', 'operation_id': 'original-op', 'status': 'UNKNOWN'}]
                emit(); expect(page.locator('#send')).to_be_disabled()
                assert page.locator('#pending button').count() == 1
                page.locator('#pending button').click()
                assert page.evaluate('()=>window.__posted.at(-1).type==="inspect"&&window.__posted.at(-1).id==="original-rpc"')
                page.screenshot(path=str(out / f'unknown-{locale}-{width}.png'))
                snapshot['pending'] = []; snapshot['draftUnresolved'] = False; emit()
                expect(page.locator('#send')).to_be_enabled()
                history = page.locator('#history'); history.evaluate('e=>e.scrollTop=0')
                # A background answer must preserve reading position.
                messages.append({'info': {'id': 'background', 'role': 'assistant'}, 'parts': [{'type': 'text', 'text': '后台新回复 / Background answer'}]})
                emit(); assert history.evaluate('e=>e.scrollTop') < 5
                page.locator('#send').click()
                assert page.evaluate('()=>window.__posted.at(-1).type==="send"')
                assert history.evaluate('e=>e.scrollHeight-e.scrollTop-e.clientHeight') < 3
                snapshot['draft'] = {'text': '', 'revision': snapshot['draft']['revision'] + 1}
                messages.append({'info': {'id': 'own-result', 'role': 'assistant'}, 'parts': [{'type': 'text', 'text': '本次回复 / Answer to your latest task 😀'}]})
                emit(); assert history.evaluate('e=>e.scrollHeight-e.scrollTop-e.clientHeight') < 3
                assert entry.evaluate('e=>e===window.__input')
                expect(entry).to_have_value(snapshot['draft']['text'])
                assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth+1')
                page.screenshot(path=str(out / f'own-reply-{locale}-{width}.png'))
                records.append({'locale': locale, 'width': width, 'height': 520, 'status': 'PASS',
                    'startup_and_stop_no_send': True, 'single_exact_check': True, 'background_preserves_reading': True,
                    'own_send_follows_result': True, 'same_input_DOM': True})
                context.close()
            browser.close()
        assert not errors, errors
    finally:
        server.shutdown(); server.server_close(); thread.join(5)
        (out / 'RESULT.json').write_text(json.dumps({'scope': 'PRODUCTION_WEBVIEW_CONTROLLED_VSCODE_MESSAGES',
            'cases': records, 'page_errors': errors, 'server_stopped': not thread.is_alive(), 'model_calls': 0,
            'Windows_native': 'NOT_RUN', 'sources': {n: hashlib.sha256((PLUGIN / n).read_bytes()).hexdigest()
                for n in ['media/view.js', 'media/view.css', 'extension.cjs', 'controller.cjs', 'i18n/vscode.json']}},
            ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'PASS', 'cases': len(records)}))


if __name__ == '__main__':main()
