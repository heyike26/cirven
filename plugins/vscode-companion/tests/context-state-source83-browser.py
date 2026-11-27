"""Production controller frames + production webview, controlled VS Code boundary only."""
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
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--out', type=Path, required=True)
    out = parser.parse_args().out.resolve(); out.mkdir(exist_ok=False); (out / 'tmp').mkdir()
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
    errors, records = [], []
    try:
        for name, args in [('html-factory', [str(PLUGIN / 'tests/theme-html.cjs'), origin, str(out / 'webview.html'), str(out / 'html-state')]),
                           ('controller', [str(PLUGIN / 'tests/context-state-browser-fixture.cjs'), str(out)])]:
            result = subprocess.run([str(NODE), *args], cwd=ROOT, env=env, text=True, capture_output=True)
            (out / f'{name}.log').write_text(result.stdout + result.stderr); assert result.returncode == 0
        frames = json.loads((out / 'controller-frames.json').read_bytes()); assert frames['private_file_unchanged']
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, env=env)
            for locale, width, dark in [('zh-CN', 320, False), ('en', 320, True), ('zh-CN', 620, True), ('en', 620, False)]:
                context = browser.new_context(viewport={'width': width, 'height': 520})
                context.add_init_script('window.__posted=[];window.acquireVsCodeApi=()=>({getState:()=>undefined,setState:x=>window.__saved=x,postMessage:x=>window.__posted.push(x)});')
                page = context.new_page(); page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(origin, wait_until='networkidle')
                page.evaluate('()=>{const l=document.createElement("link");l.rel="stylesheet";l.href="/host-theme.css";document.head.append(l)}')
                page.wait_for_function('()=>getComputedStyle(document.documentElement).getPropertyValue("--vscode-font-size").trim()==="18px"')
                page.evaluate('dark=>{for(const[k,v]of Object.entries(dark?{"--vscode-sideBar-background":"#202326","--vscode-sideBar-foreground":"#edf0f3","--vscode-foreground":"#edf0f3","--vscode-descriptionForeground":"#b6bfc6","--vscode-icon-foreground":"#edf0f3","--vscode-editorWidget-background":"#30363a","--vscode-disabledForeground":"#8f9ba5","--vscode-button-background":"#145eb0","--vscode-button-foreground":"#ffffff","--vscode-focusBorder":"#75baff","--vscode-editor-background":"#202326","--vscode-input-background":"#30363a","--vscode-input-foreground":"#edf0f3","--vscode-dropdown-background":"#30363a","--vscode-dropdown-foreground":"#edf0f3","--vscode-panel-border":"#59636a"}:{}))document.documentElement.style.setProperty(k,v)}', dark)

                def emit(key):
                    page.evaluate('m=>window.postMessage(m,"*")', {'type': 'state', 'connection': 'PAIRED',
                        'trusted': True, 'locale': locale, 'notice': '', 'snapshot': frames[key]})

                emit('current'); entry = page.locator('#input'); entry.evaluate('e=>window.__input=e')
                colors = page.locator('body').evaluate("e=>{const s=getComputedStyle(e);return {foreground:s.color,background:s.backgroundColor}}")
                if dark:assert colors['foreground'] == 'rgb(237, 240, 243)', colors
                contrast = page.locator('body').evaluate("e=>{const s=getComputedStyle(e),lum=color=>{const a=color.match(/[0-9.]+/g).slice(0,3).map(Number).map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4});return a[0]*.2126+a[1]*.7152+a[2]*.0722},a=lum(s.color),b=lum(s.backgroundColor);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)}")
                assert contrast >= 4.5, (locale, width, colors, contrast)

                assert page.locator('#sessions').evaluate('e=>e.selectedOptions[0].textContent') == frames['current']['session']['title']
                expect(page.locator('#pending')).to_be_hidden()
                preview = page.get_by_role('button', name='查看已应用修改' if locale == 'zh-CN' else 'View applied change', exact=True)
                expect(preview).to_be_visible()
                assert page.locator('#history').inner_text().count('已准备预览，尚未写入。') == 1
                assert ('此预览上次已应用。' if locale == 'zh-CN' else 'This preview was previously applied.') in page.locator('#history').inner_text()
                assert page.locator('.native-consent').count() == 0
                page.screenshot(path=str(out / f'applied-{locale}-{width}.png'))
                preview.click(); assert page.evaluate('()=>window.__posted.at(-1).type==="nativePreview"')
                page.locator('#recovery summary').click()
                assert ('另有 1 条待核查请求' if locale == 'zh-CN' else '1 requests in other discussions') in page.locator('#old-drafts').inner_text()
                page.locator('#sessions').select_option('1')
                assert page.evaluate('()=>window.__posted.at(-1).type==="session"&&window.__posted.at(-1).index===1')
                emit('pending'); expect(page.locator('#pending')).to_be_visible()
                assert not page.locator('#recovery').evaluate('e=>e.open')
                assert page.locator('#pending button').count() == 1
                page.locator('#pending button').click()
                assert page.evaluate('()=>window.__posted.at(-1).type==="inspect"&&window.__posted.at(-1).id==="other-rpc"')
                emit('observed')
                expect(page.locator('#pending')).to_contain_text('已返回，原稿保留' if locale == 'zh-CN' else 'Returned · draft kept')
                expect(entry).to_have_value(frames['pending']['draft']['text'])
                expect(page.locator('#send')).to_be_disabled()
                assert page.locator('.native-consent').count() == 0 and page.locator('.native-review').count() == 0
                assert entry.evaluate('e=>e===window.__input')
                assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth+1')
                assert not page.evaluate('()=>window.__posted.some(x=>x.type==="send"||x.type==="nativeApply")')
                page.screenshot(path=str(out / f'pending-{locale}-{width}.png'))
                records.append({'locale': locale, 'width': width, 'height': 520, 'dark': dark, 'status': 'PASS',
                    'human_title': True, 'applied_from_real_controller': True, 'other_pending_only_in_disclosure': True,
                    'pending_in_original_discussion': True, 'same_input_DOM': True, 'no_send_or_apply': True, 'actual_colors': colors, 'body_contrast': contrast})
                context.close()
            browser.close()
        assert not errors, errors
    finally:
        server.shutdown(); server.server_close(); thread.join(5)
        (out / 'RESULT.json').write_text(json.dumps({'scope': 'PRODUCTION_CONTROLLER_AND_WEBVIEW_CONTROLLED_VSCODE',
            'cases': records, 'page_errors': errors, 'server_stopped': not thread.is_alive(), 'model_calls': 0,
            'Windows_native': 'NOT_RUN', 'sources': {n: hashlib.sha256((PLUGIN / n).read_bytes()).hexdigest()
                for n in ['controller.cjs', 'native-conversation.cjs', 'extension.cjs', 'media/view.js', 'media/view.css', 'i18n/vscode.json',
                    'tests/context-state-browser-fixture.cjs', 'tests/context-state-source83-browser.py']}}, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'PASS', 'cases': len(records)}))


if __name__ == '__main__':main()
