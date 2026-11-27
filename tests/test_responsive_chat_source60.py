"""Real App event fragments/composer in isolated Linux Chromium, never the App/API."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'apps/agent-shell/src'
CHROMIUM = Path(os.environ.get('EEAS_TEST_CHROMIUM', Path.home()/'.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell'))
STYLES = ['native-input-attachments.css', 'native-composer.css', 'styles.css', 'native-collaboration.css', 'agent-studio.css', 'brand-and-spacing.css', 'chat-workbench-layout.css']


def check(value, message):
    if not value:
        raise AssertionError(message)


def record(path):
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


async def run():
    out = Path(os.environ['EEAS_EVIDENCE_DIR'])
    bundles = Path(os.environ.get('EEAS_LAYOUT_FIXTURES', str(out)))
    baseline = Path(os.environ['EEAS_LAYOUT_BASELINE'])
    check(out.is_absolute() and baseline.is_absolute(), 'Supply absolute output and frozen-source directories')
    out.mkdir(parents=True, exist_ok=True)
    check(record(baseline/'apps/agent-shell/src/styles.css')['sha256']=='f350601e2d4d327d581e28a867f265a006df456a979d73266eec65c9600e0a9b',
          'Original Source59 baseline stylesheet changed')
    fonts, sheets = {}, []
    for name in STYLES:
        path = SRC / name
        def font_url(match):
            file = (path.parent / match[1]).resolve()
            if file.suffix not in ('.ttf', '.woff2'):
                return match[0]
            url = f'http://cirven-component.test/font/{len(fonts)}'
            fonts[url] = file
            return f'url("{url}")'
        sheets.append(re.sub(r'url\([\'"]?([^\)\'\"]+)[\'"]?\)', font_url, path.read_text()))
    after_css = '\n'.join(sheets)
    before_css = after_css.replace(sheets[STYLES.index('styles.css')], (baseline / 'apps/agent-shell/src/styles.css').read_text())
    frame = '.qa-titlebar{height:36px;min-height:36px;flex:none;padding:4px 16px}.as-workspace-body{overflow:auto}'
    measurements = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
        try:
            for variant, preferred in [('before', 'split'), ('after', 'split'), ('after', 'chat'), ('after', 'workbench')]:
                context = await browser.new_context(viewport={'width': 1440, 'height': 900}, locale='zh-CN')
                page = await context.new_page()
                page.set_default_timeout(2500)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                async def route(request):
                    url = request.request.url
                    if url == 'http://cirven-component.test/':
                        await request.fulfill(body='<!doctype html><html lang="zh-CN"><head></head><body><div id="root"></div></body></html>', content_type='text/html')
                    elif url in fonts:
                        await request.fulfill(path=str(fonts[url]), content_type='font/ttf' if fonts[url].suffix == '.ttf' else 'font/woff2')
                    else:
                        await request.abort()
                await page.route('**/*', route)
                await page.goto('http://cirven-component.test/')
                await page.evaluate('(value)=>localStorage.setItem("eeas-workspace-view-v2",value)', preferred)
                await page.add_style_tag(content=(before_css if variant == 'before' else after_css) + frame)
                await page.add_script_tag(content=(bundles / f'{variant}.js').read_text())
                await page.evaluate('async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))}')
                diagnostic = await page.evaluate('()=>({text:document.body.innerText.slice(0,2000),inputs:[...document.querySelectorAll("textarea")].map(t=>({aria:t.getAttribute("aria-label"),value:t.value})),fixture:typeof fixture})')
                diagnostic['errors'] = errors
                (out / f'{variant}-{preferred}-mount.json').write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2))
                check(not errors, str(diagnostic))
                if preferred == 'workbench':
                    await page.set_viewport_size({'width': 768, 'height': 474})
                    await page.wait_for_function('fixture.state().single===true')
                    await page.get_by_role('button', name='返回对话', exact=True).click()
                area = page.get_by_role('textbox', name='描述要完成的工作', exact=True)
                await area.evaluate('(node)=>{window.originalTextarea=node;node.focus();node.setSelectionRange(4,10)}')
                original = await area.input_value()
                original_font = await area.evaluate('node=>getComputedStyle(node).fontSize')
                for width in ([768] if variant == 'before' else [1000, 768, 520]):
                    await page.set_viewport_size({'width': width, 'height': 474})
                    await page.wait_for_function('fixture.state().single===true')
                    await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                    before = await page.evaluate('fixture.state()')
                    before_scroll = await page.locator('.as-conversation').evaluate('node=>{node.scrollTop=Math.min(32,node.scrollHeight-node.clientHeight);return node.scrollTop}')
                    toggle = page.get_by_role('button', name='显示或收起工作台', exact=True)
                    if variant == 'after':
                        check(await toggle.get_attribute('aria-pressed') == 'false', 'Hidden mobile workbench must not claim pressed')
                    await toggle.click()
                    await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                    if variant == 'before':
                        value = await page.evaluate('fixture.state()')
                        check(value['panels']['workbench'] is False and value['mobile'] == 'agent', 'Preserved original first-toggle refusal not reproduced')
                        await page.screenshot(path=str(out / 'before-768.png'))
                        measurements.append({'variant': variant, 'width': width, 'before': before, 'after_first_click': value, 'known_failure_reproduced': True})
                        continue
                    await page.get_by_role('button', name='返回对话', exact=True).wait_for(state='visible')
                    check((await page.evaluate('fixture.state()'))['panels']['workbench'] is True, 'First click must show workbench')
                    await page.get_by_role('button', name='返回对话', exact=True).click()
                    await toggle.wait_for(state='visible')
                    motion = await page.evaluate('''async()=>{
                      const root=document.querySelector('.as-agent-content'),active=root.getAnimations({subtree:true}).filter(a=>a.playState==='running'&&Number.isFinite(a.effect.getComputedTiming().endTime));
                      let timer;try{await Promise.race([Promise.all(active.map(a=>a.finished.catch(()=>{}))),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Finite component motion did not settle')),1200)})]);}finally{clearTimeout(timer)}return {finite_animations:active.length,animations_disabled:false};
                    }''')
                    value = await page.evaluate('''()=>{
                      const t=document.querySelector('textarea'),c=document.querySelector('.as-composer'),f=document.querySelector('.as-native-composer-fields'),bar=document.querySelector('.as-native-composer-tools'),log=document.querySelector('.as-conversation');
                      const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right}};
                      const boxes=[...document.querySelectorAll('.as-app-frame,.qa-titlebar,.as-shell,.as-agent,.as-agent-content,.as-agent-header,.as-agent-header>*,.as-native-composer-meta,.as-composer-attachments,.as-native-composer-input')].map(el=>({tag:el.className,rect:rect(el),font:getComputedStyle(el).fontSize,padding:getComputedStyle(el).padding,display:getComputedStyle(el).display}));
                      return {boxes,sameTextarea:t===originalTextarea,value:t.value,start:t.selectionStart,end:t.selectionEnd,preferred:fixture.preferred(),state:fixture.state(),composer:rect(c),fields:rect(f),toolbar:rect(bar),transcript:rect(log),scroll:log.scrollTop,font:getComputedStyle(t).fontSize,input:rect(t),restoreBars:document.querySelectorAll('.as-panel-restore').length,viewport:{width:innerWidth,height:innerHeight}};
                    }''')
                    value['motion'] = motion
                    (out / f'{variant}-{preferred}-{width}-measurement.json').write_text(json.dumps(value, ensure_ascii=False, indent=2))
                    check(value['sameTextarea'] and value['value'] == original and [value['start'], value['end']] == [4, 10], str(value))
                    check(abs(value['scroll'] - before_scroll) <= 1, 'Reading position changed while switching panes: ' + str(value))
                    check(value['preferred'] == preferred, 'Narrow navigation cannot reset saved wide layout')
                    check(value['transcript']['height'] >= 108, 'Fewer than three readable lines remain: ' + str(value))
                    check(value['font'] == original_font and float(value['font'][:-2]) >= 14, 'Input typography shrank from the wide control: ' + str(value))
                    check(value['toolbar']['bottom'] <= value['composer']['bottom'] + 1 and value['toolbar']['bottom'] <= 474, 'Toolbar clipped: ' + str(value))
                    check(value['composer']['right'] <= width + 1 and value['restoreBars'] == 0, 'Composer width/empty restore overlay: ' + str(value))
                    measurements.append({'variant': variant, 'preferred': preferred, **value})
                    await page.screenshot(path=str(out / f'after-{preferred}-{width}.png'))
                if variant == 'after':
                    # Real input/composition/keyboard events use the original App textarea handler.
                    long_draft = '保留引用与未发送草稿\n' * 45
                    await area.fill(long_draft)
                    await area.evaluate('node=>{node.dispatchEvent(new CompositionEvent("compositionstart",{bubbles:true}));node.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",ctrlKey:true,isComposing:true,bubbles:true,cancelable:true}));node.dispatchEvent(new CompositionEvent("compositionend",{bubbles:true}))}')
                    check((await page.evaluate('fixture.state()'))['submits'] == 0, 'IME must not submit')
                    await area.press('Control+Enter')
                    check((await page.evaluate('fixture.state()'))['submits'] == 1, 'Single final shortcut event dispatch')
                    # Current composer shows two inline references at 520px. A smaller pane puts the overflow in its actual menu.
                    await page.set_viewport_size({'width': 320, 'height': 474})
                    await page.get_by_role('button', name='查看其余 1 个引用', exact=True).click()
                    await page.get_by_text('UNBREAKABLE_REFERENCE_' * 20 + '.docx', exact=True).wait_for()
                    popover = page.locator('.as-native-reference-popover')
                    box = await popover.bounding_box()
                    check(box and box['x'] >= -1 and box['x'] + box['width'] <= 321 and box['y'] >= -1 and box['y'] + box['height'] <= 475, str(box))
                    await page.keyboard.press('Escape')
                    await popover.wait_for(state='hidden')
                    await page.locator('.as-native-composer-fields').evaluate('node=>node.scrollTop=node.scrollHeight')
                    await page.get_by_role('button', name='发送消息', exact=True).focus()
                    footer = await page.get_by_role('button', name='发送消息', exact=True).bounding_box()
                    check(footer and footer['y'] >= 0 and footer['y'] + footer['height'] <= 474, 'Keyboard footer is unreachable')
                    check(await area.input_value() == long_draft and await area.evaluate('node=>node===originalTextarea'), 'Reference popup/scroll cannot replace or clear the draft')
                    await page.set_viewport_size({'width': 1440, 'height': 900})
                    await page.wait_for_function('fixture.state().single===false')
                    state = await page.evaluate('fixture.state()')
                    check(state['panels']['workbench'] == (preferred != 'chat') and state['panels']['agent'] == (preferred != 'workbench'), 'Original wide layout not restored: ' + str(state))
                    if preferred == 'split':
                        await page.get_by_role('button', name='隐藏 Agent', exact=True).click()
                        check((await page.evaluate('fixture.state()'))['panels']['agent'] is False, 'Wide hide must hide the actual agent')
                        await page.get_by_role('button', name='展开对话', exact=True).click()
                        check((await page.evaluate('fixture.state()'))['panels']['agent'] is True, 'Existing restore must return the same agent')
                    await page.evaluate('fixture.locale("en")')
                    translated = await page.evaluate('()=>({same:document.querySelector("textarea")===originalTextarea,value:originalTextarea.value})')
                    check(translated['same'] and translated['value'] == long_draft, 'Locale cannot replace or discard current input')
                    measurements.append({'variant': variant, 'preferred': preferred, 'long_reference_popover': box, 'keyboard_footer': footer, 'IME_submissions': state['submits'], 'restored_wide': state})
                await context.close()
        finally:
            await asyncio.wait_for(browser.close(), timeout=8)
    receipt = {'status': 'PASS', 'qualification': 'EXTRACTED_ACTUAL_APP_CALLBACKS_JSX_REAL_NATIVE_COMPOSER_LINUX_CHROMIUM_NOT_APP_OR_WINDOWS', 'styles': [record(SRC / name) for name in STYLES], 'fixtures': [record(bundles / (variant + '.js')) for variant in ['before', 'after']], 'measurements': measurements, 'chromium': record(CHROMIUM), 'library_provenance': record(ROOT / 'reports/E01-env/browser-libraries.json'), 'windows': False, 'App_API_model': False}
    (out / 'BROWSER_RECEIPT.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')


def test_actual_resize_toggle_draft_and_short_height_footer():
    asyncio.run(asyncio.wait_for(run(), timeout=60))
