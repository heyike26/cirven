"""Isolated full-browser representative shell; actual App width callbacks and dividers, no App/API."""
import asyncio
import hashlib
import json
import os
from legacy_evidence import evidence_folder, chromium_executable
from pathlib import Path
import re
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'apps/agent-shell/src'
CHROMIUM = chromium_executable()
STYLES = ['native-composer.css', 'styles.css', 'native-collaboration.css', 'agent-studio.css', 'brand-and-spacing.css', 'chat-workbench-layout.css']

def check(value, message):
    if not value:
        raise AssertionError(message)

def record(path):
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}

async def run():
    out = evidence_folder('pane_width_intent_source61')
    fonts, sheets, observations = {}, [], []
    for name in STYLES:
        path = SRC / name
        def font_url(match):
            file = (path.parent / match[1]).resolve()
            if file.suffix not in ('.ttf', '.woff2'):
                return match[0]
            url = f'http://cirven-width.test/font/{len(fonts)}'
            fonts[url] = file
            return f'url("{url}")'
        sheets.append(re.sub(r'url\([\'\"]?([^\)\'\"]+)[\'\"]?\)', font_url, path.read_text()))
    css = '\n'.join(sheets) + '.qa-titlebar{height:36px;min-height:36px;flex:none;padding:4px 16px}'
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
        try:
            async def mount(variant, storage, width=1920):
                context = await browser.new_context(viewport={'width': width, 'height': 900}, locale='zh-CN')
                page = await context.new_page()
                page.set_default_timeout(2500)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                async def route(request):
                    url = request.request.url
                    if url == 'http://cirven-width.test/':
                        await request.fulfill(body='<!doctype html><html lang="zh-CN"><body><div id="root"></div></body></html>', content_type='text/html')
                    elif url in fonts:
                        await request.fulfill(path=str(fonts[url]), content_type='font/ttf' if fonts[url].suffix == '.ttf' else 'font/woff2')
                    else:
                        await request.abort()
                await page.route('**/*', route)
                await page.goto('http://cirven-width.test/')
                await page.evaluate('(storage)=>{localStorage.clear();for(const [k,v] of Object.entries(storage))localStorage.setItem(k,v)}', storage)
                await page.add_style_tag(content=css)
                await page.add_script_tag(content=(Path(os.environ.get('EEAS_LAYOUT_FIXTURES',out)) / f'{variant}.js').read_text())
                try:
                    await page.wait_for_function('typeof fixture!=="undefined"')
                except Exception:
                    (out / f'{variant}-mount-errors.json').write_text(json.dumps({'errors': errors, 'text': await page.locator('body').inner_text()}, ensure_ascii=False, indent=2))
                    raise
                await page.evaluate('async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))}')
                check(not errors, str(errors))
                return context, page, errors

            async def state(page, label):
                value = await page.evaluate('()=>({...fixture.state(),css:{files:document.querySelector(".as-sidebar").getBoundingClientRect().width,agent:document.querySelector(".as-agent").getBoundingClientRect().width,columns:getComputedStyle(document.querySelector(".as-shell")).gridTemplateColumns},textarea:{value:document.querySelector("textarea").value,start:document.querySelector("textarea").selectionStart,end:document.querySelector("textarea").selectionEnd,same:document.querySelector("textarea")===window.originalTextarea}})')
                observations.append({'label': label, 'value': value})
                return value

            async def resize(page, width, height=900):
                await page.set_viewport_size({'width': width, 'height': height})
                await page.wait_for_function('(width)=>fixture.state().single===(width<=1100)', arg=width)
                await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')

            storage = {'eeas-file-width': '320', 'eeas-agent-width-v2': '820', 'eeas-workspace-view-v2': 'split'}
            context, page, errors = await mount('before', storage)
            before = await state(page, 'old60-wide-before')
            await resize(page, 768, 474)
            await resize(page, 1920)
            broken = await state(page, 'old60-wide-after')
            check(before['files'] == 320 and before['agent'] == 820, 'old initial widths')
            check(broken['files'] == 220 and broken['agent'] == 360, 'old actual width handler reproduces lost intent')
            await page.screenshot(path=str(out / 'old60-wide-after.png'))
            await context.close()

            context, page, errors = await mount('after', {'eeas-workspace-view-v2': 'split'}, 1440)
            default_before = await state(page, 'new61-default1440')
            await resize(page, 768, 474)
            await resize(page, 1920)
            default_after = await state(page, 'new61-default1920')
            check(default_after['files'] == 236 and default_after['agent'] == 800, 'fresh responsive chat default restored')
            check(default_before['agent'] < default_after['agent'] and default_after['agentKey'] is None and default_after['fileKey'] is None, 'automatic default never creates explicit preferences')
            await context.close()

            context, page, errors = await mount('after', storage)
            area = page.get_by_role('textbox', name='描述要完成的工作', exact=True)
            await area.evaluate('(node)=>{window.originalTextarea=node;node.focus();node.setSelectionRange(4,11)}')
            draft = await area.input_value()
            async def drag(kind, dx):
                bar = page.locator(f'.as-pane-separator[data-kind="{kind}"]')
                box = await bar.bounding_box()
                check(box is not None and box['width'] > 0, f'{kind} actual divider visible')
                x, y = box['x'] + box['width'] / 2, box['y'] + min(300, box['height'] / 2)
                await page.mouse.move(x, y)
                await page.mouse.down()
                await page.mouse.move(x + dx, y, steps=3)
                await page.mouse.up()
                await page.evaluate('()=>new Promise(r=>requestAnimationFrame(r))')
            await drag('files', 40)
            one = await state(page, 'new61-file-drag')
            check(one['fileKey'] == '360' and one['agentKey'] == '820', 'file drag changes only file intent')
            await drag('agent', -30)
            dragged = await state(page, 'new61-both-drag')
            check(dragged['fileKey'] == '360' and dragged['agentKey'] == '850', 'agent drag changes only agent intent')
            await resize(page, 768, 474)
            small = await state(page, 'new61-minimum')
            check(small['intent'] == {'files': 360, 'agent': 850}, 'small projection preserves intent')
            await resize(page, 1920)
            restored = await state(page, 'new61-wide-restored')
            check(restored['files'] == 360 and restored['agent'] == 850 and restored['css']['files'] == 360 and restored['css']['agent'] == 850, 'actual wide CSS widths restored')
            check(restored['textarea'] == {'value': draft, 'start': 4, 'end': 11, 'same': True}, 'same textarea draft and selection preserved through real pointer/resize')
            await page.screenshot(path=str(out / 'new61-wide-restored.png'))

            await page.evaluate('()=>{fixture.workspace("A");fixture.resolveWorkspace("A",{files_width:400,files_hidden:false})}')
            a = await state(page, 'workspace-A-restored400')
            check(a['files'] == 400 and a['agentKey'] == '850' and a['fileKey'] == '360', 'workspace file preference does not replace global keys or chat intention')
            await resize(page, 768, 474)
            a_small = await state(page, 'workspace-A-minimum-keeps400')
            check(a_small['files'] == 220 and a_small['intent']['files'] == 400 and [x for x in a_small['writes'] if x['workspace'] == 'A' and x['key'] == 'panes'][-1]['value']['files_width'] == 400, 'workspace persists intent rather than auto clamp')
            await resize(page, 1920)
            await page.evaluate('()=>fixture.workspace("B")')
            await drag('files', -40)
            await page.evaluate('()=>fixture.resolveWorkspace("B",{files_width:440,files_hidden:false})')
            late = await state(page, 'workspace-B-late-restore-after-drag')
            check(late['intent']['files'] == 360 and late['files'] == 360, 'late workspace restore cannot overwrite newer same-workspace drag')
            check([x for x in late['writes'] if x['workspace'] == 'B' and x['key'] == 'panes'][-1]['value']['files_width'] == 360, 'newer user intent persists once workspace becomes ready')
            await page.evaluate('()=>{fixture.workspace("old");fixture.workspace("current");fixture.resolveWorkspace("old",{files_width:220});fixture.resolveWorkspace("current",{files_width:300})}')
            scoped = await state(page, 'workspace-current-not-old')
            check(scoped['files'] == 300, 'old workspace receipt cannot overwrite current scope')
            await page.evaluate('()=>fixture.workspace("A")')
            cached = await state(page, 'cached-ready-A-restores-without-new-emit')
            check(cached['files'] == 400 and cached['intent']['files'] == 400, 'cached ready workspace restores its own file intent without another GET or emit')

            await page.evaluate('()=>fixture.fullscreen(true)')
            await resize(page, 1440)
            await page.evaluate('()=>fixture.fullscreen(false)')
            fs = await state(page, 'fullscreen-exit-reprojects')
            check(fs['files'] + fs['agent'] <= 1440 - 332 and fs['intent'] == {'files': 400, 'agent': 850}, 'fullscreen exit reprojects without mutating intent')
            await page.evaluate('()=>fixture.mode("companion")')
            await resize(page, 520, 760)
            await resize(page, 1920)
            await page.evaluate('()=>fixture.mode("workbench")')
            companion = await state(page, 'companion-return-wide')
            check(companion['files'] == 400 and companion['agent'] == 850 and companion['agentKey'] == '850', 'companion roundtrip does not replace wide intent')
            check(companion['textarea'] == {'value': draft, 'start': 4, 'end': 11, 'same': True}, 'mode transitions keep original textarea and selection')
            check(not errors, str(errors))
            await context.close()
            context, page, errors = await mount('after', {'eeas-file-width': '360', 'eeas-agent-width-v2': '850', 'eeas-workspace-view-v2': 'split'}, 768)
            await resize(page, 1920)
            reload = await state(page, 'narrow-restart-wide-explicit-keys')
            check(reload['files'] == 360 and reload['agent'] == 850, 'narrow restart preserves stored wide intent')
            await context.close()
        finally:
            await browser.close()
    receipt = {'status': 'PASS', 'qualification': 'ACTUAL_APP_WIDTH_CALLBACKS_REAL_DIVIDER_BROWSER_FIXTURE_NOT_FULL_APP_OR_WINDOWS', 'old60_regression_reproduced': True, 'observations': observations, 'styles': [record(SRC / name) for name in STYLES], 'chromium': record(CHROMIUM), 'windows': False, 'API_model': False}
    (out / 'BROWSER_RECEIPT.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')


def test_pane_width_intent_source61():
    asyncio.run(run())
