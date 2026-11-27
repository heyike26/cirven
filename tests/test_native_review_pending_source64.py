"""Actual component + Quick preload in isolated Chromium; static routes, no App/API."""
import asyncio
from copy import deepcopy
import json
from urllib.parse import urlparse
from hashlib import sha256
import os
from legacy_evidence import evidence_folder, chromium_executable
from pathlib import Path
import pytest
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
CHROMIUM = chromium_executable()

STYLE_FILES = ['native-composer.css', 'styles.css', 'native-collaboration.css', 'agent-studio.css', 'brand-and-spacing.css', 'chat-workbench-layout.css']
FONTS = {'/staging-native-ui/upstream/packages/ui/src/assets/fonts/Inter.ttf': ROOT / 'staging-native-ui/upstream/packages/ui/src/assets/fonts/Inter.ttf', '/web/assets/fonts/manrope-variable.woff2': ROOT / 'apps/web/assets/fonts/manrope-variable.woff2', '/web/assets/fonts/noto-sans-sc-variable.woff2': ROOT / 'apps/web/assets/fonts/noto-sans-sc-variable.woff2'}
async def actual_main_styles(page, out, bundle='main'):
    for name in STYLE_FILES:
        await page.add_style_tag(content=(ROOT / 'apps/agent-shell/src' / name).read_text())
    if (out / (bundle + '.css')).exists():
        await page.add_style_tag(path=str(out / (bundle + '.css')))
    await page.add_style_tag(content='#root{padding:16px;max-height:100dvh;overflow:auto;min-width:0}')
    await page.evaluate('()=>document.fonts.ready')

CLIP = """node=>{const r=node.getBoundingClientRect();let l=Math.max(0,r.left),t=Math.max(0,r.top),rr=Math.min(innerWidth,r.right),bb=Math.min(innerHeight,r.bottom);let hidden=false;for(let n=node;n;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden')hidden=true;if(n!==node){const b=n===document.documentElement?{left:0,top:0,right:innerWidth,bottom:innerHeight}:n.getBoundingClientRect();if(/auto|scroll|hidden|clip/.test(s.overflowX)){l=Math.max(l,b.left);rr=Math.min(rr,b.right);}if(/auto|scroll|hidden|clip/.test(s.overflowY)){t=Math.max(t,b.top);bb=Math.min(bb,b.bottom);}}}return {width:r.width,height:r.height,visibleWidth:hidden?0:Math.max(0,rr-l),visibleHeight:hidden?0:Math.max(0,bb-t),font:getComputedStyle(node).fontFamily,fontSize:getComputedStyle(node).fontSize};}"""


def original():
    pending = {'kind': 'QUESTION', 'request_ref': {'id': 'que_original', 'generation': 1, 'sha256': 'a' * 64}, 'native_request': {'questions': [{'question': '原创夹具：新稿采用哪一种结构？', 'options': [{'label': '保留原结构', 'description': '用户可继续补充'}], 'custom': True}]}, 'allowed_choices': ['reply', 'reject']}
    review = {'review_ref': {'id': 'original_review', 'revision': 1, 'sha256': 'b' * 64}, 'phase': 'WAITING_QUESTION', 'pending_permissions': [], 'pending_questions': [pending], 'can_guide': True}
    return {'operation_id': 'original_task', 'status': 'PROCESSING', 'review': review}


def quick_state(message, locale):
    return {'epoch': 1, 'locale': locale, 'visible': True, 'kind': 'ask', 'source': {'image': None, 'available': False, 'label': '原创冻结依据夹具'}, 'previewExpanded': False, 'draft': {'id': 'original_draft', 'phase': 'RUNNING', 'operationId': message['operation_id'], 'comment': '原创能力任务', 'marks': [], 'readonly': False}, 'result': message, 'guidanceEditable': False, 'canSend': False, 'canDraft': False, 'canConfirm': False, 'cancelSupported': True, 'canStop': True, 'canInspect': True}


@pytest.mark.parametrize('surface', ['main', 'quick'])
def test_pending_input_survives_new_get_object_and_new_pending(surface):
    asyncio.run(run(surface))


async def run(surface):
    raw = evidence_folder('native_review_pending_source64')
    out = raw.resolve()
    assert raw.is_absolute() and raw == out and out.is_relative_to((ROOT.parent / '.development').resolve())
    variant = os.environ.get('EEAS_PENDING_VARIANT', 'after')
    observations = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
            try:
                cases = [('zh-CN', 768, 474)] if variant == 'before' else ([('zh-CN', 420, 620), ('en', 520, 620)] if surface == 'quick' else [('zh-CN', 768, 474), ('en', 1180, 760)])
                for locale, width, height in cases:
                    context = await browser.new_context(viewport={'width': width, 'height': height}, locale=locale, bypass_csp=True)
                    page = await context.new_page()
                    page.set_default_timeout(2500)
                    errors, calls = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    async def route(request):
                        if request.request.url == 'http://127.0.0.1:17864/':
                            body = (out / ('quick-' + locale + '.html')).read_text() if surface == 'quick' else '<!doctype html><html><body><main id="root"></main></body></html>'
                            await request.fulfill(body=body, content_type='text/html')
                        elif urlparse(request.request.url).path in FONTS:
                            await request.fulfill(body=FONTS[urlparse(request.request.url).path].read_bytes(), content_type='font/woff2' if request.request.url.endswith('.woff2') else 'font/ttf')
                        else:
                            calls.append(request.request.url)
                            await request.abort()
                    await page.route('**/*', route)
                    await page.goto('http://127.0.0.1:17864/')
                    if surface == 'main' and locale == 'zh-CN':
                        probe = await context.new_page()
                        async def probe_route(request):
                            await request.fulfill(body='<!doctype html><body>Original isolated origin audit</body>', content_type='text/html')
                        await probe.route('**/*', probe_route)
                        await probe.goto('http://cirven-pending.test/')
                        previous_origin = await probe.evaluate('()=>({secure:isSecureContext,uuid_type:typeof crypto.randomUUID})')
                        await probe.close()
                        current_origin = await page.evaluate('()=>({secure:isSecureContext,uuid_type:typeof crypto.randomUUID})')
                        observations.append({'surface': surface, 'phase': 'fixture_origin_audit', 'previous_origin': previous_origin, 'current_origin': current_origin})
                        assert not previous_origin['secure'] and previous_origin['uuid_type'] == 'undefined'
                        assert current_origin['secure'] and current_origin['uuid_type'] == 'function'
                    initial = original()
                    if surface == 'main':
                        await page.evaluate('(value)=>window.qaInitial=value', initial)
                        await actual_main_styles(page, out)
                        await page.add_script_tag(path=str(out / 'main.js'))
                        field = page.locator('.as-native-review-actions input[aria-label]').first
                    else:
                        await page.evaluate('''value=>{const listeners=new Map();window.qa={current:value,controls:[],publish(next){this.current=structuredClone(next);listeners.get('eeas:quick-surface:state')?.(null,this.current);}};window.qaIPC={on:(key,fn)=>listeners.set(key,fn),invoke:async(key,payload)=>{if(payload.kind==='state')return qa.current;qa.controls.push(structuredClone(payload));return qa.current;}};}''', quick_state(initial, locale))
                        await page.add_script_tag(path=str(out / 'quick.js'))
                        await page.evaluate("window.dispatchEvent(new Event('DOMContentLoaded'))")
                        field = page.locator('#review-pending input[aria-label]').first
                    await field.wait_for(state='visible')
                    assert not errors, errors
                    text = '原创未发送答复 Abc 😀'
                    await field.fill(text)
                    await field.evaluate('(node)=>{window.originalPendingInput=node;node.focus();node.setSelectionRange(3,8);node.scrollLeft=4;}')
                    next_message = deepcopy(initial)
                    next_message['text'] = '原创任务进度已更新，仍等待本条答复。'
                    if surface == 'main':
                        await page.evaluate('value=>qa.publish(value)', next_message)
                    else:
                        await page.evaluate('value=>qa.publish(value)', quick_state(next_message, locale))
                    await page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    first = await page.evaluate('''()=>{const n=window.originalPendingInput;return {connected:n.isConnected,focused:document.activeElement===n,value:n.value,start:n.selectionStart,end:n.selectionEnd};}''')
                    observations.append({'surface': surface, 'locale': locale, 'phase': 'same_get_new_objects', 'measure': first})
                    await page.screenshot(path=str(out / f'{surface}-{locale}-same-get.png'))
                    assert first == {'connected': True, 'focused': True, 'value': text, 'start': 3, 'end': 8}, first
                    assert (await page.locator('#qa-response' if surface == 'main' else '#reply').inner_text()).strip() == next_message['text']
                    added = deepcopy(initial['review']['pending_questions'][0])
                    added['request_ref'] = {'id': 'que_additional', 'generation': 1, 'sha256': 'c' * 64}
                    added['native_request']['questions'][0]['question'] = '原创夹具：另一条待答问题'
                    next_message['review']['pending_questions'].append(added)
                    if surface == 'main':
                        await page.evaluate('value=>qa.publish(value)', next_message)
                    else:
                        await page.evaluate('value=>qa.publish(value)', quick_state(next_message, locale))
                    await page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    after = await page.evaluate('''()=>{const n=window.originalPendingInput;return {connected:n.isConnected,focused:document.activeElement===n,value:n.value,start:n.selectionStart,end:n.selectionEnd};}''')
                    observations.append({'surface': surface, 'locale': locale, 'phase': 'new_pending_added', 'measure': after})
                    await page.screenshot(path=str(out / f'{surface}-{locale}-new-pending.png'))
                    assert after == first, after
                    if surface == 'quick':
                        first_row = page.locator('#review-pending article').first
                        actions = first_row.locator('.review-pending-buttons button')
                        default_actions = [await action.evaluate(CLIP) for action in await actions.all()]
                        assert len(default_actions) == 2 and all(box['visibleWidth'] >= box['width'] - 1 and box['visibleHeight'] >= box['height'] - 1 for box in default_actions), default_actions
                        observations.append({'surface': surface, 'locale': locale, 'phase': 'first_question_actions_visible_by_default', 'measure': default_actions})
                        await field.focus()
                        await page.keyboard.press('Tab')
                        assert await actions.first.evaluate('(n)=>document.activeElement===n')
                        await page.keyboard.press('Tab')
                        assert await actions.last.evaluate('(n)=>document.activeElement===n')
                        await page.keyboard.press('Tab')
                        await page.keyboard.press('Tab')
                        second_field = page.locator('#review-pending input[aria-label]').nth(1)
                        assert await second_field.evaluate('(n)=>document.activeElement===n')
                        later_box = await second_field.evaluate(CLIP)
                        assert later_box['visibleHeight'] >= later_box['height'] - 1, later_box
                        observations.append({'surface': surface, 'locale': locale, 'phase': 'keyboard_reaches_later_pending', 'measure': later_box})
                        await field.evaluate('(node)=>{node.focus();node.setSelectionRange(3,8);}')
                    await field.scroll_into_view_if_needed()
                    field_box = await field.evaluate(CLIP)
                    parent_width = await field.evaluate('(node)=>node.parentElement.getBoundingClientRect().width')
                    assert field_box['width'] >= parent_width - 2 and field_box['visibleWidth'] >= field_box['width'] - 1 and field_box['visibleHeight'] >= field_box['height'] - 1, field_box
                    assert field_box['fontSize'] == '13px', field_box
                    observations.append({'surface': surface, 'locale': locale, 'phase': 'production_style_question_input', 'measure': field_box, 'parent_width': parent_width})
                    args = {'destination': 'D:/原创目标/一份有界输出.md', 'text': '原创待执行参数。' * 160}
                    permission = {'kind': 'PERMISSION', 'request_ref': {'id': 'per_selected_export', 'generation': 1, 'sha256': 'd' * 64}, 'native_request': {'permission': 'qa_export_selected', 'patterns': ['*'], 'metadata': {}}, 'allowed_choices': ['once', 'reject'], 'capability_call': {'tool_id': 'qa_export_selected', 'input_json': json.dumps(args, ensure_ascii=False), 'input_truncated': False, 'input_redacted': False, 'coverage': 'EXACT_ORIGINAL_TOOL_INPUT'}}
                    next_message['review']['pending_permissions'] = [permission]
                    await page.evaluate('value=>qa.publish(value)', next_message if surface == 'main' else quick_state(next_message, locale))
                    await page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    assert await page.evaluate('()=>document.activeElement===originalPendingInput&&originalPendingInput.isConnected'), 'new permission must retain the active answer'
                    article = page.locator(('article[data-native-request-key]' if surface == 'main' else '#review-pending article')).filter(has_text='qa_export_selected').first
                    assert 'D:/原创目标/一份有界输出.md' in await article.inner_text()
                    pre = article.locator('.as-native-capability-input' if surface == 'main' else '.review-capability-input')
                    await pre.scroll_into_view_if_needed()
                    measured = await pre.evaluate('(node)=>({height:node.getBoundingClientRect().height,client:node.clientHeight,total:node.scrollHeight,overflow:getComputedStyle(node).overflowY,text:node.textContent})')
                    assert measured['height'] <= 184 and measured['total'] > measured['client'] and measured['overflow'] == 'auto', measured
                    assert measured['text'] == permission['capability_call']['input_json'] and '原创待执行参数。' in measured['text']
                    await pre.evaluate('(node)=>node.scrollTop=node.scrollHeight')
                    assert await pre.evaluate('(node)=>Math.abs(node.scrollHeight-node.clientHeight-node.scrollTop)<=1')
                    allow = article.get_by_role('button', name='允许一次' if locale == 'zh-CN' else 'Allow once', exact=True)
                    await allow.scroll_into_view_if_needed()
                    allow_box = await allow.evaluate(CLIP)
                    assert allow_box['visibleWidth'] >= allow_box['width'] - 1 and allow_box['visibleHeight'] >= allow_box['height'] - 1, allow_box
                    observations.append({'surface': surface, 'locale': locale, 'phase': 'permission_action_effective_clip', 'measure': allow_box})
                    await page.screenshot(path=str(out / f'{surface}-{locale}-permission-action.png'))
                    await article.get_by_role('button', name='允许一次' if locale == 'zh-CN' else 'Allow once', exact=True).click()
                    await page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    control = await page.evaluate('()=>qa.controls[0]')
                    assert control is not None, {'visible_status': await page.locator('.as-native-review-actions p[role=status]').all_text_contents() if surface == 'main' else [], 'crypto_uuid_type': await page.evaluate('()=>typeof crypto.randomUUID')}
                    if surface == 'main':
                        assert control['body']['request_ref'] == permission['request_ref'] and control['body']['reply'] == 'once', control
                    else:
                        assert control['requestId'] == permission['request_ref']['id'] and control['action'] == 'once', control
                    observations.append({'surface': surface, 'locale': locale, 'phase': 'permission_range_and_scroll_end', 'measure': {key: value for key, value in measured.items() if key != 'text'}, 'control': control})
                    next_message['review']['pending_permissions'][0]['capability_call'] = {'coverage': 'UNAVAILABLE', 'reason': 'ORIGINAL_CALL_NOT_UNIQUE'}
                    next_message['review']['pending_permissions'][0]['allowed_choices'] = ['reject']
                    await page.evaluate('value=>qa.publish(value)', next_message if surface == 'main' else quick_state(next_message, locale))
                    await page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    assert await article.get_by_role('button', name='允许一次' if locale == 'zh-CN' else 'Allow once', exact=True).is_disabled()
                    assert ('未取得本次调用参数' if locale == 'zh-CN' else 'Call arguments are unavailable') in await article.inner_text()
                    next_message['review']['pending_questions'][0]['request_ref'] = {'id': 'que_original', 'generation': 2, 'sha256': 'e' * 64}
                    next_message['review']['pending_questions'][0]['native_request']['questions'][0]['question'] = '原创新依据：必须重新作答'
                    await page.evaluate('value=>qa.publish(value)', next_message if surface == 'main' else quick_state(next_message, locale))
                    await page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    assert not await page.evaluate('()=>originalPendingInput.isConnected'), 'changed exact basis must create a new answer field'
                    replacement = page.locator(('.as-native-review-actions input[aria-label]' if surface == 'main' else '#review-pending input[aria-label]')).first
                    assert await replacement.input_value() == '', 'new basis cannot inherit the original answer'
                    observations.append({'surface': surface, 'locale': locale, 'phase': 'new_exact_request_basis', 'old_input_detached': True, 'new_input_empty': True})
                    await page.screenshot(path=str(out / f'{surface}-{locale}-new-basis.png'))
                    assert not calls, calls
                    assert not errors, errors
                    await context.close()
            finally:
                await browser.close()
    finally:
        (out / 'STYLE_INPUTS.json').write_text(json.dumps([{'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha256(path.read_bytes()).hexdigest()} for path in [*(ROOT / 'apps/agent-shell/src' / name for name in STYLE_FILES), *FONTS.values()]], indent=2) + '\n')
        (out / f'{surface}-OBSERVATIONS.json').write_text(json.dumps({'variant': variant, 'surface': surface, 'observations': observations}, ensure_ascii=False, indent=2) + '\n')
