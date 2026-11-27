"""Actual browser interactions against a running EEAS renderer and real local API.

The harness never substitutes API/store responses or provides a native bridge.
It performs no model, login, write-file, external-application or Windows action.
Start the real API separately with an isolated data directory. Playwright and its
existing headless Chromium are test-only environment dependencies.
"""
from __future__ import annotations
import argparse
import json
import traceback
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', required=True)
parser.add_argument('--out', required=True, type=Path)
args = parser.parse_args()
url = urlparse(args.base_url)
if url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1'} or url.username or url.password:
    parser.error('Only an explicitly supplied local test renderer is accepted')
args.out.mkdir(parents=True, exist_ok=False)
rows = []
errors = []
requests = []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1280, 'height': 820}, locale='en-US')
    context.add_init_script("localStorage.setItem('eeas.ui-language.v1','en')")
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: requests.append({'method': request.method, 'path': urlparse(request.url).path}))

    def check(name, run):
        try:
            result = run()
            rows.append({'name': name, 'status': 'PASS', 'evidence': result})
        except Exception as error:
            rows.append({'name': name, 'status': 'FAIL', 'error': str(error)[:1400], 'traceback': traceback.format_exc()})
            page.screenshot(path=str(args.out / (name + '-failure.png')))

    def goto(mode='workbench', width=1280, height=820):
        page.set_viewport_size({'width': width, 'height': height})
        page.goto(args.base_url.rstrip('/') + '/?mode=' + mode, wait_until='domcontentloaded')
        page.locator('.as-app-frame').wait_for()
        page.get_by_role('textbox', name='Describe the work to do', exact=True).wait_for()
        # Wait for the actual intro surface and initial local reads, without
        # requiring global network idle from an application with observers.
        page.wait_for_timeout(500)

    def focus_label():
        return page.evaluate("document.activeElement?.getAttribute('aria-label')")

    def visible_popovers():
        return page.locator('.as-studio-popover:visible').count()

    goto()
    draft = page.get_by_role('textbox', name='Describe the work to do', exact=True)
    draft.fill('Keep this private draft\n不翻译我的内容 😀')

    def outside_then_escape():
        trigger = page.get_by_role('button', name='Collaboration menu', exact=True)
        trigger.click()
        page.locator('#as-companion-menu').wait_for(state='visible')
        draft.click()
        page.locator('#as-companion-menu').wait_for(state='hidden')
        trigger.click()
        page.keyboard.press('Escape')
        page.locator('#as-companion-menu').wait_for(state='hidden')
        expect(trigger).to_be_focused(timeout=1000)
        assert focus_label() == 'Collaboration menu', focus_label()
        assert draft.input_value().endswith('不翻译我的内容 😀')
        return {'focus': focus_label(), 'draft_preserved': True}
    check('outside-reopen-escape', outside_then_escape)

    def rapid_menu():
        trigger = page.get_by_role('button', name='Collaboration menu', exact=True)
        box = trigger.bounding_box()
        for _ in range(10):
            page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
            page.wait_for_timeout(12)
        page.wait_for_timeout(220)
        assert trigger.get_attribute('aria-expanded') == 'false', 'rapid pointer toggles left menu open'
        assert visible_popovers() == 0
        trigger.focus()
        page.keyboard.press('Enter')
        page.locator('#as-companion-menu').wait_for(state='visible')
        surface = page.locator('.as-studio-popover')
        assert surface.get_attribute('data-input-modality') == 'keyboard', 'keyboard source was not recorded'
        assert surface.evaluate('(n)=>n.getAnimations().length') == 0, 'keyboard opening is animated'
        page.keyboard.press('Escape')
        return {'pointer_toggles': 10, 'remaining_popovers': visible_popovers(), 'keyboard_animations': 0}
    check('rapid-menu-keyboard', rapid_menu)

    def drawer_focus():
        trigger = page.get_by_role('button', name='Choose skills and tools', exact=True)
        trigger.click()
        drawer = page.locator('#as-agent-skills')
        drawer.wait_for(state='visible')
        expect(drawer.locator(':focus')).to_have_count(1,timeout=1000)
        page.keyboard.press('Tab')
        assert drawer.evaluate('(n)=>n.contains(document.activeElement)')
        assert draft.evaluate('(n)=>n.closest("form").inert')
        page.keyboard.press('Escape')
        expect(drawer).to_have_attribute('data-open','false',timeout=1000)
        assert focus_label() == 'Choose skills and tools', focus_label()
        assert not draft.evaluate('(n)=>n.closest("form").inert')
        assert draft.input_value() == 'Keep this private draft\n不翻译我的内容 😀'
        return {'hidden_composer_inert_during_drawer': True, 'focus_restored': True, 'draft_preserved': True}
    check('drawer-focus-and-draft', drawer_focus)

    def rapid_drawer():
        trigger = page.get_by_role('button', name='Choose skills and tools', exact=True)
        for _ in range(5):
            trigger.click()
            page.get_by_role('button', name='Collapse skills and tools', exact=True).click()
        page.wait_for_timeout(250)
        drawer = page.locator('#as-agent-skills')
        assert drawer.get_attribute('data-open') == 'false'
        assert drawer.evaluate('(n)=>n.inert')
        assert not draft.evaluate('(n)=>n.closest("form").inert')
        assert page.locator('body').evaluate('(n)=>n.scrollWidth<=innerWidth')
        page.screenshot(path=str(args.out/'workbench.png'))
        return {'cycles': 5, 'closed_inert': True, 'no_horizontal_overflow': True}
    check('rapid-drawer', rapid_drawer)

    def model_geometry():
        goto('companion', 400, 650)
        trigger = page.get_by_role('button', name='Select model connection', exact=True)
        before = trigger.bounding_box()
        trigger.click()
        page.locator('#as-composer-model-options').wait_for(state='visible')
        page.wait_for_timeout(220)
        pop = page.locator('.as-studio-popover').bounding_box()
        assert pop['x'] >= -1 and pop['y'] >= -1
        assert pop['x'] + pop['width'] <= 401 and pop['y'] + pop['height'] <= 651, pop
        after = trigger.bounding_box()
        assert abs(before['x'] - after['x']) < 1 and abs(before['y'] - after['y']) < 1, {'before':before,'after':after}
        page.screenshot(path=str(args.out/'companion-model.png'))
        page.keyboard.press('Escape')
        expect(trigger).to_be_focused(timeout=1000)
        return {'viewport': [400, 650], 'popover': pop, 'trigger_did_not_move': True}
    check('companion-anchored-model', model_geometry)

    def login_hover():
        # Open the actual provider view, without clicking login/authorization.
        goto('workbench')
        page.get_by_role('button', name='Choose skills and tools', exact=True).click()
        page.locator('#as-agent-skills .as-tool-group').first.locator('summary').click()
        page.locator('#as-agent-skills').get_by_role('button', name='Connect a model', exact=False).click()
        primary = page.locator('.as-subscription-primary:visible').first
        primary.wait_for(timeout=15000)
        box = primary.bounding_box()
        page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2)
        samples=[]
        for _ in range(6):
            page.wait_for_timeout(45)
            samples.append(primary.evaluate('(n)=>({rect:[n.getBoundingClientRect().x,n.getBoundingClientRect().y,n.getBoundingClientRect().width,n.getBoundingClientRect().height],background:getComputedStyle(n).backgroundColor,image:getComputedStyle(n).backgroundImage})'))
        assert all(sample['image']=='none' for sample in samples),samples
        assert all(sample['rect']==samples[0]['rect'] for sample in samples),samples
        assert len({sample['background']for sample in samples[-3:]})==1,samples
        page.screenshot(path=str(args.out/'subscription-hover.png'))
        return {'samples': samples, 'login_clicked': False, 'no_hitbox_motion': True, 'paint_settled': True}
    check('subscription-hover-stability', login_hover)

    def reduced_motion():
        page.emulate_media(reduced_motion='reduce')
        goto('companion', 360, 520)
        page.get_by_role('button',name='Collaboration menu',exact=True).click()
        page.locator('#as-companion-menu').wait_for(state='visible')
        assert page.locator('.as-studio-popover').evaluate('(n)=>n.getAnimations().length') == 0
        page.keyboard.press('Escape')
        assert focus_label() == 'Collaboration menu'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        return {'system_reduce_only': True, 'viewport': [360,520], 'focus_and_bounds': True}
    check('reduced-motion-small-viewport', reduced_motion)

    def canonical_brand_and_header():
        goto('workbench')
        emblem = page.locator('.as-empty-emblem img')
        emblem.wait_for()
        assert emblem.get_attribute('src') == page.locator('.as-brand-mark img').get_attribute('src')
        assert emblem.evaluate('(n)=>n.complete&&n.naturalWidth>0')
        size = emblem.bounding_box()
        assert size['width'] == 38 and size['height'] == 38, size
        page.get_by_role('button', name='Choose skills and tools', exact=True).click()
        page.locator('#as-agent-skills .as-tool-group').first.locator('summary').click()
        page.locator('#as-agent-skills').get_by_role('button', name='Connect a model', exact=False).click()
        header = page.locator('.as-agent-tools-host .as-interaction-panel[data-visible=true] .as-interaction-header strong')
        expect(header).to_have_text('Model connection')
        page.get_by_role('button',name='Collaboration menu',exact=True).click()
        language = page.locator('.as-ui-language select')
        language.select_option('zh-CN')
        expect(header).to_have_text('模型连接')
        language.select_option('en')
        expect(header).to_have_text('Model connection')
        page.keyboard.press('Escape')
        page.screenshot(path=str(args.out/'canonical-brand-english-header.png'))
        return {'empty_state_same_src_as_canonical_sidebar': True, 'emblem_pixels': [38,38], 'builtin_header': header.inner_text(), 'same_panel_live_en_zh_en': True, 'external_plugin_labels_unchanged': 'Only registerBuiltins descriptors use authored translator'}
    check('canonical-brand-and-english-header', canonical_brand_and_header)

    forbidden=[r for r in requests if r['method'] not in {'GET','HEAD'} and any(word in r['path']for word in ['/messages','/guidance','/login','/authorize','/runs','/reports'])]
    result={'scope':'ACTUAL_HEADLESS_WEB_AND_REAL_LOCAL_API_NOT_WINDOWS','browser':browser.version,'results':rows,'page_errors':errors,'model_or_auth_mutations':forbidden,'requests':requests}
    (args.out/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    context.close();browser.close()
    print(json.dumps({'passed':sum(r['status']=='PASS'for r in rows),'failed':sum(r['status']=='FAIL'for r in rows),'page_errors':errors,'model_or_auth_mutations':forbidden}))
    if any(r['status']!='PASS'for r in rows) or errors or forbidden:
        raise SystemExit(1)
