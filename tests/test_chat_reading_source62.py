"""Actual chat components/current App callbacks in isolated Chromium, no App/API/model."""
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

METRICS = r'''()=>{const box=node=>{const r=node.getBoundingClientRect();let x=r.left,y=r.top,right=r.right,bottom=r.bottom;for(let p=node.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),b=p.getBoundingClientRect();if(/auto|scroll|hidden|clip/.test(s.overflowX)){x=Math.max(x,b.left+p.clientLeft);right=Math.min(right,b.left+p.clientLeft+p.clientWidth);}if(/auto|scroll|hidden|clip/.test(s.overflowY)){y=Math.max(y,b.top+p.clientTop);bottom=Math.min(bottom,b.top+p.clientTop+p.clientHeight);}}return{left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height,effective_width:Math.max(0,right-x),effective_height:Math.max(0,bottom-y)};};const find=s=>document.querySelector(s),area=find('.as-composer textarea'),conversation=find('.as-conversation');return{viewport:{width:innerWidth,height:innerHeight},transcript:box(conversation),composer:box(find('.as-composer')),input:box(area),toolbar:box(find('.as-composer-toolbar')),send:box(find('.as-composer-send')),context:box(find('.as-native-context-entry')||find('.as-native-context')),fields:box(find('.as-composer-fields')),font:{input:getComputedStyle(area).fontSize,line:getComputedStyle(area).lineHeight,body:getComputedStyle(document.body).fontSize},scroll:{top:conversation.scrollTop,client:conversation.clientHeight,total:conversation.scrollHeight},same:area===window.originalTextarea,value:area.value,selection:[area.selectionStart,area.selectionEnd],state:fixture.state()};}'''

VISIBLE_BOX = r'''node=>{const r=node.getBoundingClientRect();let x=Math.max(0,r.left),y=Math.max(0,r.top),right=Math.min(innerWidth,r.right),bottom=Math.min(innerHeight,r.bottom);for(let p=node.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),b=p.getBoundingClientRect();if(/auto|scroll|hidden|clip/.test(s.overflowX)){x=Math.max(x,b.left+p.clientLeft);right=Math.min(right,b.left+p.clientLeft+p.clientWidth);}if(/auto|scroll|hidden|clip/.test(s.overflowY)){y=Math.max(y,b.top+p.clientTop);bottom=Math.min(bottom,b.top+p.clientTop+p.clientHeight);}}const s=getComputedStyle(node);return{height:r.height,width:r.width,effective_height:Math.max(0,bottom-y),effective_width:Math.max(0,right-x),display:s.display,line_clamp:s.webkitLineClamp,scroll_height:node.scrollHeight,client_height:node.clientHeight};}'''

async def settle_motion(page):
    return await page.evaluate('''async()=>{const animations=document.getAnimations().filter(a=>a.playState==='running'&&Number.isFinite(a.effect?.getComputedTiming().endTime));let timeout;try{await Promise.race([Promise.all(animations.map(a=>a.finished.catch(()=>{}))),new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('Finite UI motion did not settle')),1500)})]);}finally{clearTimeout(timeout);}await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));return animations.length;}''')

async def run():
    out, baseline = evidence_folder('chat_reading_source62'), Path(os.environ['EEAS_CHAT_BASELINE'])
    check(baseline.is_absolute(), 'Select the original frozen chat baseline')
    frozen = os.environ.get('EEAS_CHAT_ARCHIVE')
    bundle_root = Path(os.environ.get('EEAS_LAYOUT_FIXTURES',out))
    style_root = SRC
    if frozen:
        style_root = bundle_root = Path(frozen)
        pins = {'before.js':'e1ccc047a1851393353eaa3236ed9143ec2c4140594f552fc3bb5325b15ca403',
                'after.js':'5f7bb84353833cb88bb016776f16f75ef1c4c5c34bc3947a7c23c11b3c062438',
                'BROWSER_RECEIPT.json':'61891bca0e7306445cb694bb330fc696fb28e2533e885873eda4661af5dea5e4'}
        for name, expected in pins.items():
            check(record(bundle_root/name)['sha256']==expected, 'Frozen Source62 bytes differ: '+name)
        for obj in json.loads((bundle_root/'BROWSER_RECEIPT.json').read_bytes())['styles']:
            name=Path(obj['path']).name
            check(record(style_root/name)['sha256']==obj['sha256'], 'Frozen Source62 stylesheet differs: '+name)
    check(record(baseline/'apps/agent-shell/src/styles.css')['sha256']=='243168865243b0b58b7745468e9e0cd5d78ac4cb7c0ff8735099ae30e807bdd3','Original Source61 stylesheet changed')
    fonts, sheets, observations = {}, [], []
    for name in STYLES:
        path = style_root / name
        def font_url(match):
            file = (SRC / match[1]).resolve()
            if file.suffix not in ('.ttf', '.woff2'):
                return match[0]
            url = f'http://cirven-reading.test/font/{len(fonts)}'
            fonts[url] = file
            return f'url("{url}")'
        sheets.append(re.sub(r'url\([\'\"]?([^\)\'\"]+)[\'\"]?\)', font_url, path.read_text()))
    css = '\n'.join(sheets)
    old_css = css.replace(sheets[1], (baseline / 'apps/agent-shell/src/styles.css').read_text())
    frame = '.qa-titlebar{height:36px;min-height:36px;flex:none;padding:4px 16px}'
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
            try:
                before = None
                for variant in ['before', 'after']:
                    context = await browser.new_context(viewport={'width': 768, 'height': 474}, locale='zh-CN')
                    page = await context.new_page()
                    page.set_default_timeout(3000)
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    async def route(request):
                        url = request.request.url
                        if url == 'http://cirven-reading.test/':
                            await request.fulfill(body='<!doctype html><html lang="zh-CN"><body><div id="root"></div></body></html>', content_type='text/html')
                        elif url in fonts:
                            await request.fulfill(path=str(fonts[url]), content_type='font/ttf' if fonts[url].suffix == '.ttf' else 'font/woff2')
                        else:
                            await request.abort()
                    await page.route('**/*', route)
                    await page.goto('http://cirven-reading.test/')
                    await page.add_style_tag(content=(old_css if variant == 'before' else css) + frame)
                    await page.add_script_tag(content=(bundle_root / f'{variant}.js').read_text())
                    try:
                        await page.wait_for_function('typeof fixture!=="undefined"')
                        await page.evaluate('async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))}')
                        check(not errors, str(errors))
                        await settle_motion(page)
                        area = page.get_by_role('textbox', name='描述要完成的工作', exact=True)
                        await area.evaluate('(node)=>{window.originalTextarea=node;node.focus();node.setSelectionRange(4,11)}')
                        initial = await page.evaluate(METRICS)
                        observations.append({'variant': variant, 'phase': 'short_initial', 'measure': initial})
                        await page.screenshot(path=str(out / f'{variant}-short-initial.png'))
                        if variant == 'before':
                            before = initial
                            check(initial['composer']['height'] > initial['transcript']['height'], 'Old real chat state does not reproduce composer-dominant reading')
                            await context.close()
                            continue
                        check(initial['transcript']['height'] >= before['transcript']['height'] + 48 and initial['transcript']['height'] >= 170, 'Reading area did not recover meaningful space: ' + str(initial))
                        check(initial['font'] == before['font'], 'No global/input font shrinking permitted')
                        check(initial['input']['effective_height'] >= 36 and initial['send']['effective_height'] >= 36 and initial['toolbar']['effective_height'] >= initial['toolbar']['height'] - 1, 'Input or main send clipped')
                        check(initial['same'] and initial['selection'] == [4, 11], 'Same textarea not retained')
                        check(await page.locator('.as-native-context-copy small').count() == 0, 'Ordinary available empty context regained permanent commentary')
                        unavailable_reason = '只读观察尚未获得允许。' + '服务返回的完整理由必须按原文保留，摘要不能把顶部无限撑高。' * 60 + '完整理由末端标记'
                        await page.evaluate('(reason)=>{fixture.setUnavailableReason(reason);fixture.setAvailable(false)}', unavailable_reason)
                        permission = page.locator('.as-native-context-copy small[role=status]')
                        reason_box = await permission.evaluate(VISIBLE_BOX)
                        check(await permission.is_visible() and await permission.inner_text() == unavailable_reason and reason_box['effective_height'] >= reason_box['height'] - 1 and 0 < reason_box['height'] <= 40 and reason_box['line_clamp'] == '2', 'Unavailable reason not effectively visible and bounded: ' + str(reason_box))
                        check(await page.get_by_role('button', name='展开当前对象与批注', exact=True).get_attribute('aria-expanded') == 'false', 'Unavailable reason forced details open')
                        risk = await page.evaluate(METRICS)
                        check(risk['context']['height'] <= 110 and risk['send']['effective_height'] >= 36 and risk['toolbar']['effective_height'] >= risk['toolbar']['height'] - 1, 'Long unavailable reason displaced composer controls')
                        observations.append({'variant': variant, 'phase': 'collapsed_unavailable_reason_effectively_visible_bounded', 'reason_box': reason_box, 'reason_length': len(unavailable_reason), 'measure': risk})
                        await page.screenshot(path=str(out / 'after-short-unavailable-risk.png'))
                        await page.get_by_role('button', name='展开当前对象与批注', exact=True).click()
                        reason_detail = page.locator('.as-native-context-reason')
                        check(await reason_detail.inner_text() == unavailable_reason, 'Expanded reason lost original full text')
                        reason_end = await page.evaluate('''()=>{const panel=document.querySelector('.as-native-context-expanded'),p=panel.querySelector('.as-native-context-reason'),text=p.firstChild,range=document.createRange();range.setStart(text,text.length-10);range.setEnd(text,text.length);let r=range.getBoundingClientRect(),b=panel.getBoundingClientRect();panel.scrollTop+=r.bottom-b.bottom+24;r=range.getBoundingClientRect();b=panel.getBoundingClientRect();return{top:r.top,bottom:r.bottom,panel_top:b.top,panel_bottom:b.bottom,viewport:innerHeight,scroll:panel.scrollTop};}''')
                        check(reason_end['scroll'] > 0 and reason_end['top'] >= reason_end['panel_top'] and reason_end['bottom'] <= reason_end['panel_bottom'] and reason_end['panel_bottom'] <= reason_end['viewport'], 'Full reason last characters inaccessible: ' + str(reason_end))
                        reason_footer = page.locator('.as-native-context-expanded').get_by_role('button', name='加入草稿', exact=True)
                        await reason_footer.scroll_into_view_if_needed()
                        footer_box = await reason_footer.evaluate(VISIBLE_BOX)
                        check(footer_box['effective_height'] >= footer_box['height'] - 1, 'Long expanded reason hid original detail footer: ' + str(footer_box))
                        observations.append({'variant': variant, 'phase': 'full_unavailable_reason_end_and_footer_reachable', 'end': reason_end, 'footer': footer_box})
                        await page.keyboard.press('Escape')
                        await page.evaluate('()=>{fixture.setAvailable(true);fixture.setUnavailableReason("");fixture.setSnapshot({operation_id:"qa62-invalid-context",current:false,observation:{status:"OBSERVED",context_ref:{id:"qa62-frame",revision:1,sha256:"a".repeat(64)},window:{title:"原窗口仍有标题"}}})}')
                        invalid_caption = page.locator('.as-native-context-copy small[data-native-risk=true]')
                        invalid_box = await invalid_caption.evaluate(VISIBLE_BOX)
                        check(await invalid_caption.is_visible() and await invalid_caption.inner_text() == '需要重新定位' and invalid_box['effective_height'] >= invalid_box['height'] - 1, 'Snapshot without valid current ref lost effectively visible risk: ' + str(invalid_box))
                        observations.append({'variant': variant, 'phase': 'collapsed_snapshot_without_effective_ref_visible', 'caption': await invalid_caption.inner_text(), 'box': invalid_box})
                        await page.evaluate('fixture.setSnapshot(undefined)')
                        check(await page.locator('.as-native-context-copy small').count() == 0, 'Normal empty context retained risk commentary after recovery')
                        check(await area.evaluate('node=>node===originalTextarea') and await area.input_value() == initial['value'], 'Availability/snapshot observation replaced current draft input')
                        await settle_motion(page)
                        await page.evaluate('()=>{const n=document.querySelector(".as-conversation");n.scrollTop=460}')
                        anchor = await page.evaluate('()=>{const n=document.querySelector(".as-conversation"),r=n.getBoundingClientRect(),a=[...n.querySelectorAll("article")].find(a=>a.getBoundingClientRect().bottom>r.top+4);window.readingAnchor=a;window.readingOffset=a.getBoundingClientRect().top-r.top;return{id:a.dataset.qaMessage,offset:readingOffset,top:n.scrollTop};}')
                        check(anchor['top'] > 0, 'Nonzero transcript reading required')
                        await page.get_by_role('button', name='显示或收起工作台', exact=True).click()
                        await page.wait_for_function('fixture.state().panels.workbench&&!fixture.state().panels.agent')
                        await page.get_by_role('button', name='返回对话', exact=True).click()
                        await page.wait_for_function('fixture.state().panels.agent&&!fixture.state().panels.workbench')
                        await settle_motion(page)
                        returned = await page.evaluate('()=>{const n=document.querySelector(".as-conversation"),r=n.getBoundingClientRect();return{same:document.querySelector("textarea[aria-label=描述要完成的工作]")===originalTextarea,selection:[originalTextarea.selectionStart,originalTextarea.selectionEnd],anchor:readingAnchor.dataset.qaMessage,offset:readingAnchor.getBoundingClientRect().top-r.top,scroll:n.scrollTop};}')
                        observations.append({'variant': variant, 'phase': 'nonzero_anchor_workbench_return', 'before': anchor, 'after': returned})
                        check(returned['same'] and returned['selection'] == [4, 11] and abs(returned['offset'] - anchor['offset']) <= 1 and returned['scroll'] > 0, 'Workbench roundtrip moved the original reading anchor: ' + str(returned))
                        await area.focus()
                        await area.dispatch_event('compositionstart')
                        await area.dispatch_event('keydown', {'key': 'Enter', 'code': 'Enter', 'ctrlKey': True, 'isComposing': True})
                        await area.dispatch_event('compositionend')
                        check((await page.evaluate('fixture.state()'))['submits'] == 0, 'IME Ctrl+Enter sent a message')
                        long_text = '\n'.join(f'{i}：这是待继续编辑的长草稿，正文和原引用都要保留。' for i in range(60))
                        await area.fill(long_text)
                        await area.evaluate('node=>node.setSelectionRange(24,40)')
                        await page.evaluate('fixture.flush()')
                        long_state = await page.evaluate(METRICS)
                        check(long_state['state']['draft']['text'] == long_text and len(long_state['state']['draft']['feedback_refs']) == 2, 'Actual App composer flush lost text or references')
                        check(long_state['input']['height'] <= 113 and long_state['toolbar']['effective_height'] >= long_state['toolbar']['height'] - 1, 'Long editor displaced main toolbar')
                        await area.evaluate('node=>node.scrollTop=node.scrollHeight')
                        check(await area.evaluate('node=>node.scrollHeight-node.clientHeight-node.scrollTop<2'), 'Long input end unreachable')
                        await page.evaluate('fixture.rejectFlush(true)')
                        refusal = await page.evaluate('async()=>{try{await fixture.flush();return false}catch(e){return String(e).includes("Original controlled flush rejection")}}')
                        check(refusal and await area.input_value() == long_text, 'Flush failure did not keep input')
                        await page.evaluate('fixture.rejectFlush(false)')
                        await page.evaluate('fixture.flush()')
                        await page.get_by_role('button', name='2 个引用', exact=True).click()
                        popup = page.get_by_role('dialog', name='本轮引用')
                        await popup.wait_for(state='visible')
                        check((await popup.bounding_box())['width'] <= 744, 'Reference popup exceeded viewport')
                        last_remove = popup.locator('button[aria-label^="移除引用"]').last
                        await last_remove.scroll_into_view_if_needed()
                        await last_remove.click()
                        await page.keyboard.press('Escape')
                        check(len((await page.evaluate('fixture.state()'))['refs']) == 1 and await area.input_value() == long_text, 'Reference removal lost draft or was unreachable')
                        await page.evaluate('fixture.setWarning("引用依据版本已变化，继续前须核查原文件。")')
                        for status in ['RUNNING', 'UNKNOWN', 'FAILED']:
                            await page.evaluate('(value)=>fixture.setStatus(value)', status)
                            field = page.locator('.as-composer-fields')
                            await field.evaluate('node=>node.scrollTop=0')
                            check(await field.locator('text=当前页检查').is_visible(), 'Current-page action unreachable')
                            if status in ['RUNNING', 'UNKNOWN']:
                                await field.get_by_role('button', name='新问题', exact=True).scroll_into_view_if_needed()
                                check(await field.get_by_role('button', name='新问题', exact=True).is_visible(), 'Running/unknown new-question action hidden')
                            await field.locator('[role=status]').last.scroll_into_view_if_needed()
                            m = await page.evaluate(METRICS)
                            check(m['toolbar']['effective_height'] >= m['toolbar']['height'] - 1 and m['state']['originalOperation'] == 'qa62-original-operation-unchanged' and m['state']['submits'] == 0, 'Status changed request or hid main send')
                            observations.append({'variant': variant, 'phase': status.lower() + '_long_refs_warning', 'measure': m})
                        await page.evaluate('()=>{fixture.setStatus("IDLE");fixture.setWarning("")}')
                        await area.fill('保留当前问题')
                        await page.evaluate('()=>fixture.setSnapshot({operation_id:"qa62-observation",current:true,observation:{status:"OBSERVED",context_ref:{id:"qa62-frame",revision:1,sha256:"a".repeat(64)},window:{title:"当前文档与页面".repeat(30)}}})')
                        await page.get_by_role('button', name='展开当前对象与批注', exact=True).click()
                        expanded = page.locator('.as-native-context-expanded')
                        await expanded.wait_for(state='visible')
                        await expanded.get_by_role('button', name='加入草稿', exact=True).scroll_into_view_if_needed()
                        check(await expanded.get_by_role('button', name='加入草稿', exact=True).is_visible(), 'Context bottom controls unreachable')
                        comment = expanded.get_by_role('textbox', name='当前对象批注', exact=True)
                        await comment.fill('旧页面依据的批注要保留')
                        await page.evaluate('()=>{fixture.rejectFeedback(true);fixture.setSnapshot({operation_id:"qa62-observation-new",current:true,observation:{status:"OBSERVED",context_ref:{id:"qa62-frame",revision:2,sha256:"b".repeat(64)},window:{title:"新页面标题"}}})}')
                        check(await expanded.get_by_role('button', name='直接发送反馈', exact=True).is_disabled(), 'Changed annotation basis became sendable')
                        await expanded.get_by_role('button', name='加入草稿', exact=True).click()
                        await page.locator('.as-native-context [role=alert]').wait_for(state='visible')
                        check(await comment.input_value() == '旧页面依据的批注要保留', 'Failed context draft lost user text')
                        await page.keyboard.press('Escape')
                        check(await page.get_by_role('button', name='展开当前对象与批注', exact=True).evaluate('node=>node===document.activeElement'), 'Context Esc did not return focus')
                        check(await area.evaluate('node=>node===originalTextarea'), 'Context detail replaced composer')
                        check(await page.locator('.as-native-context-copy small').inner_text() == '需要重新定位' and await page.locator('.as-native-context [role=alert]').is_visible(), 'Collapsed summary hid version risk or explicit error')
                        observations.append({'variant': variant, 'phase': 'context_changed_basis_and_explicit_error_remain_visible', 'measure': await page.evaluate(METRICS)})
                        await page.evaluate('fixture.locale("en")')
                        await page.wait_for_function('document.documentElement.lang==="en"')
                        check('reference' in (await page.locator('.as-native-reference-chip').inner_text()).lower(), 'Actual reference summary did not switch language')
                        await page.screenshot(path=str(out / 'after-short-english-risk.png'))
                        await page.set_viewport_size({'width': 1440, 'height': 900})
                        await page.wait_for_function('fixture.state().panels.agent')
                        await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                        wide = await page.evaluate(METRICS)
                        check(await page.locator('.as-native-composer-meta').evaluate('node=>getComputedStyle(node).display') == 'contents', 'Wide breathing space changed to compact mode')
                        check(wide['font'] == initial['font'], 'Wide typography changed')
                        observations.append({'variant': variant, 'phase': 'wide_restored', 'measure': wide})
                        await page.screenshot(path=str(out / 'after-wide-restored.png'))
                        check(not errors, str(errors))
                    finally:
                        (out / f'{variant}-runtime-errors.json').write_text(json.dumps(errors, ensure_ascii=False, indent=2))
                        await context.close()
            finally:
                await browser.close()
    finally:
        (out / 'OBSERVATIONS.json').write_text(json.dumps(observations, ensure_ascii=False, indent=2) + '\n')
    (out / 'BROWSER_RECEIPT.json').write_text(json.dumps({'status': 'PASS', 'qualification': 'ACTUAL_CONTEXT_COMPOSER_APP_PRESENTATION_IME_AND_SAVE_CALLBACKS_LINUX_BROWSER_FIXTURE_NOT_FULL_APP_OR_WINDOWS', 'observations': observations, 'chromium': record(CHROMIUM), 'styles': [record(style_root / n) for n in STYLES], 'bundles': [record(bundle_root / (v+'.js')) for v in ('before','after')], 'subject': 'FROZEN_SOURCE62_R4' if frozen else 'CHECKOUT', 'no_App_API_model_Vite_install': True}, ensure_ascii=False, indent=2) + '\n')


def test_chat_reading_source62():
    asyncio.run(run())
