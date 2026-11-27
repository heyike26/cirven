"""Whole production renderer and real isolated HTTP/Store, without model calls.

The retained renderer is admitted only when its source and output hashes match.
These are Linux Chromium development checks, not Windows, Word or IDE acceptance.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
INTERNAL = ROOT.parent / '.development'

SERVER = '''
import os
from pathlib import Path
import uvicorn
from fastapi.staticfiles import StaticFiles
from apps.api.config import Settings
from apps.api.main import create_app
app=create_app(Settings(Path(os.environ['EEAS_DATA_DIR']),port=int(os.environ['EEAS_PORT']),worker_enabled=False))
app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None)!='/agent-shell']
app.mount('/agent-shell',StaticFiles(directory=os.environ['EEAS_BROWSER_RENDERER'],html=True),name='current-browser-renderer')
uvicorn.run(app,host='127.0.0.1',port=int(os.environ['EEAS_PORT']),log_level='warning',access_log=False)
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def current_app(tmp_path):
    assert tmp_path.resolve().is_relative_to(INTERNAL.resolve())
    renderer = Path(os.environ['EEAS_BROWSER_REUSE_RENDERER']).resolve()
    assert renderer.is_relative_to(INTERNAL.resolve())
    receipt = renderer / 'build-receipt.json'
    built = json.loads(receipt.read_text())
    assert all(sha(Path(x['path'])) == x['sha256'] for x in built['inputs'])
    output_receipt = Path(os.environ['EEAS_BROWSER_OUTPUT_RECEIPT']).resolve()
    assert output_receipt.is_relative_to(INTERNAL.resolve())
    output_record = json.loads(output_receipt.read_text())
    assert Path(output_record['source_receipt']).resolve() == receipt
    assert output_record['source_receipt_sha256'] == sha(receipt)
    original_outputs = output_record['files']
    assert all(sha(renderer / 'dist' / x['path']) == x['sha256'] for x in original_outputs)
    out = tmp_path / 'evidence'
    out.mkdir()
    (out / 'renderer.json').write_text(json.dumps({
        'source_receipt': str(receipt), 'source_receipt_sha256': sha(receipt),
        'output_receipt': str(output_receipt), 'output_receipt_sha256': sha(output_receipt),
        'source_inputs': len(built['inputs']), 'output_files': original_outputs,
        'business_API_replaced': False, 'worker_enabled': False,
        'scope': 'Current production assets; isolated actual API, no native/model qualification',
    }, indent=2))
    script = tmp_path / 'serve.py'
    script.write_text(SERVER)
    workspace = tmp_path / 'authored-workspace'
    workspace.mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    env = dict(os.environ, EEAS_DATA_DIR=str(tmp_path / 'private-api'), EEAS_PORT=str(port),
               EEAS_BROWSER_RENDERER=str(renderer / 'dist'), EEAS_WORKER='0',
               PYTHONDONTWRITEBYTECODE='1')
    with (out / 'server.log').open('xb') as log:
        process = subprocess.Popen([sys.executable, '-B', str(script)], cwd=ROOT, env=env,
                                   stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            while True:
                assert process.poll() is None, (out / 'server.log').read_text()
                try:
                    with urllib.request.urlopen(origin + '/health', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.1)
            yield SimpleNamespace(origin=origin, out=out, workspace=workspace, port=port)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            (out / 'server-terminal.json').write_text(json.dumps({
                'pid': process.pid, 'exit_code': process.returncode,
                'proc_absent': not Path('/proc', str(process.pid)).exists(),
            }))


def settle(page):
    page.evaluate('''async()=>{
      await document.fonts.ready;
      await Promise.all(document.getAnimations().filter(a=>
        Number.isFinite(a.effect?.getComputedTiming().endTime)).map(a=>a.finished.catch(()=>{})));
      await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
    }''')


@contextmanager
def surface(app):
    trace = {'steps': [], 'responses': [], 'blocked': [], 'page_errors': [], 'console_errors': [],
             'scope': 'Full current App + real isolated API; no SDK/model/native Windows/IDE'}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width': 1440, 'height': 900}, locale='zh-CN')

        def guard(route):
            from urllib.parse import urlsplit
            request = route.request
            url = urlsplit(request.url)
            execution = request.method not in {'GET', 'HEAD', 'OPTIONS'} and re.search(
                r'/(messages|guidance|execute|interrupt|control|cancel)(/|$)', url.path)
            if url.hostname != '127.0.0.1' or url.port != app.port or execution:
                trace['blocked'].append({'method': request.method, 'url': request.url})
                route.abort()
            else:
                route.continue_()

        context.route('**/*', guard)
        page = context.new_page()
        page.on('pageerror', lambda error: trace['page_errors'].append({
            'message': str(error), 'stack': error.stack, 'completed_steps': len(trace['steps']),
        }))
        page.on('console', lambda msg: trace['console_errors'].append(msg.text) if msg.type == 'error' else None)

        def observed(response):
            from urllib.parse import urlsplit
            request = response.request
            row = {'method': request.method, 'path': urlsplit(request.url).path, 'status': response.status}
            if request.method == 'POST' and request.post_data:
                try:
                    row['request'] = request.post_data_json
                except ValueError:
                    pass
            trace['responses'].append(row)

        page.on('response', observed)
        try:
            response = page.goto(app.origin + '/agent-shell/', wait_until='networkidle', timeout=45000)
            assert response.status == 200
            page.locator('.as-shell').wait_for(state='visible')
            settle(page)
            yield page, trace
            assert not trace['blocked'], trace['blocked']
            assert not trace['page_errors'], trace['page_errors']
        finally:
            try:
                page.screenshot(path=str(app.out / 'final.png'), full_page=True)
                (app.out / 'final.html').write_text(page.content())
            finally:
                (app.out / 'browser.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2))
                context.close()
                browser.close()


def state(page):
    return page.evaluate('''()=>({
      viewport:[innerWidth,innerHeight],
      shell:{...document.querySelector('.as-shell').dataset},
      widths:{files:localStorage.getItem('eeas-file-width'),agent:localStorage.getItem('eeas-agent-width-v2')},
      view:localStorage.getItem('eeas-workspace-view-v2'),
      overflow:document.documentElement.scrollWidth>innerWidth
    })''')


def show_split(page):
    page.get_by_role('button', name='显示或收起工作台', exact=True).click()
    settle(page)
    expect(page.locator('.as-agent')).to_be_visible()
    expect(page.locator('.as-workbench')).to_be_visible()


def test_current_app_chat_workbench_resize_and_reload_keep_the_same_unsent_draft(current_app):
    with surface(current_app) as (page, trace):
        composer = page.get_by_role('textbox', name='描述要完成的工作', exact=True)
        text = '当前讨论的未发送原稿 😀\n保持原句、空白与中文输入。'
        composer.fill(text)
        node = composer.element_handle()
        for width, height in [(1440, 900), (960, 720), (620, 540), (1440, 900)]:
            page.set_viewport_size({'width': width, 'height': height})
            settle(page)
            page.get_by_role('button', name='显示或收起工作台', exact=True).click()
            settle(page)
            expect(page.locator('.as-workbench')).to_be_visible()
            if composer.is_visible():
                # Wide screens show both panels; the same toggle closes it.
                page.get_by_role('button', name='显示或收起工作台', exact=True).click()
            else:
                page.get_by_role('button', name='返回对话', exact=True).click()
            settle(page)
            expect(composer).to_have_value(text)
            assert composer.evaluate('(e,original)=>e===original', node)
            observed = state(page)
            assert not observed['overflow'], observed
            trace['steps'].append({'action': 'workbench_and_return', **observed})
        # Explicitly return to chat before reload; this is a saved view choice.
        expect(page.locator('.as-workbench')).to_be_hidden()
        page.wait_for_function('text=>Object.values(localStorage).some(v=>v.includes(text))', arg='当前讨论的未发送原稿')
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('textbox', name='描述要完成的工作', exact=True)).to_have_value(text)
        trace['steps'].append({'action': 'reload_retained_unsent_draft', **state(page)})


def test_current_app_real_pointer_and_keyboard_width_intent_survives_narrow_projection(current_app):
    with surface(current_app) as (page, trace):
        show_split(page)
        divider = page.get_by_role('separator', name='调整 Agent 面板宽度', exact=True)
        box = divider.bounding_box()
        assert box and box['height'] > 100
        page.mouse.move(box['x'] + box['width'] / 2, box['y'] + 60)
        page.mouse.down()
        page.mouse.move(box['x'] + box['width'] / 2 - 70, box['y'] + 60, steps=8)
        page.mouse.up()
        settle(page)
        after_pointer = state(page)
        assert after_pointer['widths']['agent'] is not None
        divider.focus()
        before_key = int(after_pointer['widths']['agent'])
        page.keyboard.press('ArrowLeft')
        settle(page)
        intent = state(page)['widths']
        assert int(intent['agent']) == before_key + 16
        wide_width = int(divider.get_attribute('aria-valuenow'))
        for width, height in [(1160, 760), (960, 720), (620, 540), (1440, 900)]:
            page.set_viewport_size({'width': width, 'height': height})
            settle(page)
            observed = state(page)
            assert observed['widths'] == intent, observed
            assert not observed['overflow'], observed
            trace['steps'].append({'action': 'resize_after_explicit_width', **observed})
        assert abs(int(divider.get_attribute('aria-valuenow')) - wide_width) <= 1


def test_current_app_file_save_conflict_and_original_draft_recovery_protect_manual_bytes(current_app):
    filename = '原件.md'
    path = current_app.workspace / filename
    original = '人工作者的原始段落 😀\n第二段保持独立。\n'
    path.write_text(original)
    untouched = current_app.workspace / 'untouched.txt'
    untouched.write_bytes(b'Unrelated manual file\r\n')
    with surface(current_app) as (page, trace):
        page.get_by_role('tab', name='文件', exact=True).click()
        page.get_by_role('button', name='打开目录', exact=True).click()
        dialog = page.get_by_role('dialog', name='打开目录', exact=True)
        dialog.get_by_role('textbox', name='工程名称', exact=True).fill('完整 App 连续工作场景')
        dialog.get_by_role('textbox', name='本机目录的绝对路径', exact=True).fill(str(current_app.workspace))
        dialog.get_by_role('checkbox', name='允许在此目录内保存编辑', exact=True).check()
        dialog.get_by_role('button', name='打开目录', exact=True).click()
        expect(dialog).to_be_hidden(timeout=20000)
        page.get_by_role('treeitem', name=filename, exact=True).click()
        editor = page.get_by_role('textbox', name='编辑工作文件', exact=True)
        expect(editor).to_have_value(original)
        saved = original + '本次明确保存的补充。\n'
        editor.fill(saved)
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/edits')) as edited:
            page.get_by_role('button', name='保存文件', exact=True).click()
        assert edited.value.status == 200, edited.value.text()
        expect(page.locator('.as-inspector:not([hidden]) .as-object-meta')).to_contain_text('文件版本 2')
        expect(page.locator('.as-inspector:not([hidden]) .as-file-editor')).to_have_value(saved)
        assert path.read_text() == saved
        private_draft = saved + '尚未提交的人工新稿，不覆盖外部手改。\n'
        page.get_by_role('textbox', name='编辑工作文件', exact=True).fill(private_draft)
        expect(page.locator('.as-inspector:not([hidden]) .as-draft-status')).to_have_attribute('data-state', 'saved')
        manual = saved + '外部人工程序实际保存的另一处修改。\n'
        path.write_text(manual)
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/edits')) as conflict:
            page.get_by_role('button', name='保存文件', exact=True).click()
        assert conflict.value.status == 409, conflict.value.text()
        warning = page.locator('.as-inspector:not([hidden]) .as-inline-error')
        expect(warning).to_have_text('文件已发生变化；请先核对最新内容，再继续操作。')
        expect(warning).not_to_contain_text('SOURCE_DRIFT')
        expect(page.get_by_role('textbox', name='编辑工作文件', exact=True)).to_have_value(private_draft)
        assert path.read_text() == manual
        page.get_by_role('button', name='返回项目工作台', exact=True).click()
        page.locator('.as-review-drafts > summary').click()
        draft_button = page.get_by_role('button', name=re.compile(r'^原件\.md · 原版本 v2$'))
        draft_button.click()
        expect(page.get_by_role('textbox', name='编辑工作文件', exact=True)).to_have_value(private_draft)
        assert path.read_text() == manual
        edits = [x for x in trace['responses'] if x['method'] == 'POST' and x['path'].endswith('/edits')]
        assert [x['status'] for x in edits] == [200, 409], edits
        assert untouched.read_bytes() == b'Unrelated manual file\r\n'
        trace['steps'].append({'action': 'actual_save_then_CAS_rejection_then_original_draft_recovery',
                               'source_sha256': sha(path), 'draft_sha256': hashlib.sha256(private_draft.encode()).hexdigest(),
                               'file_edit_requests': edits, **state(page)})


def test_current_app_late_actual_read_keeps_new_input_and_successful_original_write(current_app):
    """Delay only a real GET acknowledgement; never substitute its API result."""
    from urllib.parse import urlsplit
    filename = '迟到读取.md'
    path = current_app.workspace / filename
    original = '原始人工段落。\n'
    path.write_text(original)
    with surface(current_app) as (page, trace):
        page.get_by_role('tab', name='文件', exact=True).click()
        page.get_by_role('button', name='打开目录', exact=True).click()
        dialog = page.get_by_role('dialog', name='打开目录', exact=True)
        dialog.get_by_role('textbox', name='工程名称', exact=True).fill('迟到读取保护原稿')
        dialog.get_by_role('textbox', name='本机目录的绝对路径', exact=True).fill(str(current_app.workspace))
        dialog.get_by_role('checkbox', name='允许在此目录内保存编辑', exact=True).check()
        dialog.get_by_role('button', name='打开目录', exact=True).click()
        expect(dialog).to_be_hidden(timeout=20000)
        page.get_by_role('treeitem', name=filename, exact=True).click()
        editor = page.get_by_role('textbox', name='编辑工作文件', exact=True)
        expect(editor).to_have_value(original)
        held = []

        def hold_original_read(route):
            if route.request.method == 'GET' and urlsplit(route.request.url).path.endswith('/file') and not held:
                response = route.fetch()
                assert response.status == 200
                held.append((route, response))
                page.evaluate('window.__continuityReadHeld=true')
            else:
                route.fallback()

        page.route('**/local-workspaces/**', hold_original_read)
        submitted = original + '本次明确保存的版本。\n'
        editor.fill(submitted)
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/edits')) as written:
            page.get_by_role('button', name='保存文件', exact=True).click()
        assert written.value.status == 200
        page.wait_for_function('()=>window.__continuityReadHeld===true')
        assert path.read_text() == submitted
        late = submitted + '读取期间新增的人工文字 😀\n'
        editor.fill(late)
        held[0][0].fulfill(response=held[0][1])
        expect(editor).to_have_value(late)
        expect(page.get_by_text('实际更新版本 v2', exact=False)).to_be_visible()
        expect(page.get_by_role('button', name='保存文件', exact=True)).to_be_disabled()
        expect(page.locator('.as-inspector:not([hidden]) .as-draft-status')).to_have_attribute('data-state', 'saved')
        page.get_by_role('button', name='返回项目工作台', exact=True).click()
        page.locator('.as-review-drafts > summary').click()
        page.get_by_role('button', name=re.compile(r'^迟到读取\.md · 原版本 v1$')).click()
        expect(page.get_by_role('textbox', name='编辑工作文件', exact=True)).to_have_value(late)
        assert path.read_text() == submitted
        edits = [x for x in trace['responses'] if x['method'] == 'POST' and x['path'].endswith('/edits')]
        assert len(edits) == 1 and edits[0]['status'] == 200
        trace['steps'].append({'action': 'held_actual_GET_did_not_replace_late_input_or_replay_write',
                               'source_sha256': sha(path), 'draft_sha256': hashlib.sha256(late.encode()).hexdigest(),
                               'held_actual_GET_source_ref': held[0][1].json()['source_ref'],
                               'file_edit_requests': edits, **state(page)})
