"""Real production-page focus geometry plus an explicitly isolated UI-input fixture.

The production page uses the real local API/SQLite. The separate fixture renders
the actual NativeContextPanel/helper/catalog, and proves presentation only: it
does not simulate a native capture, model, execution authority, API or store.
"""
from __future__ import annotations
import argparse
import io
import json
import math
import traceback
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', required=True)
parser.add_argument('--out', required=True, type=Path)
args = parser.parse_args()
url = urlparse(args.base_url)
if url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1'} or url.username or url.password:
    parser.error('Only an explicitly supplied local test renderer is accepted')
args.out.mkdir(parents=True, exist_ok=False)
rows, errors, requests = [], [], []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1280, 'height': 820}, locale='en-US')
    context.add_init_script("localStorage.setItem('eeas.ui-language.v1','en')")
    page = context.new_page()
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: requests.append({'method': request.method, 'path': urlparse(request.url).path}))

    def check(name, run):
        try:
            rows.append({'name': name, 'status': 'PASS', 'evidence': run()})
        except Exception as error:
            rows.append({'name': name, 'status': 'FAIL', 'error': str(error), 'traceback': traceback.format_exc()})
            page.screenshot(path=str(args.out / (name + '-failure.png')))

    def focus_ring():
        measurements = []
        for mode, width, height in [('workbench', 1280, 820), ('companion', 400, 650)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.goto(args.base_url.rstrip('/') + '/?mode=' + mode, wait_until='domcontentloaded')
            draft = page.get_by_role('textbox', name='Describe the work to do', exact=True)
            draft.fill('Keep the exact draft\n人工草稿 😀')
            trigger = page.get_by_role('button', name='Collaboration menu', exact=True)
            page.keyboard.press('Tab')
            trigger.focus()
            page.keyboard.press('Enter')
            page.locator('#as-companion-menu').wait_for(state='visible')
            page.keyboard.press('Escape')
            expect(trigger).to_be_focused()
            assert trigger.evaluate('(n)=>n.matches(":focus-visible")')
            geometry = trigger.evaluate('''(n)=>{
              const box=r=>({x:r.x,y:r.y,width:r.width,height:r.height,left:r.left,right:r.right,top:r.top,bottom:r.bottom});
              const b=n.getBoundingClientRect(),s=getComputedStyle(n),a=n.parentElement,as=getComputedStyle(a);
              const reach=parseFloat(s.outlineWidth)+parseFloat(s.outlineOffset);
              const ring={left:b.left-reach,right:b.right+reach,top:b.top-reach,bottom:b.bottom+reach};
              const clipping=[];for(let p=a;p;p=p.parentElement){const ps=getComputedStyle(p),r=p.getBoundingClientRect();
                if((['hidden','clip','auto','scroll'].includes(ps.overflowX)&&(r.left>ring.left+.5||r.right<ring.right-.5))||
                   (['hidden','clip','auto','scroll'].includes(ps.overflowY)&&(r.top>ring.top+.5||r.bottom<ring.bottom-.5)))clipping.push({tag:p.tagName,class:p.className,box:box(r),x:ps.overflowX,y:ps.overflowY});}
              return {button:box(b),anchor:box(a.getBoundingClientRect()),flex:as.flex,overflowX:as.overflowX,overflowY:as.overflowY,outline:s.outlineStyle,outlineWidth:parseFloat(s.outlineWidth),outlineOffset:parseFloat(s.outlineOffset),clipping};
            }''')
            assert abs(geometry['anchor']['width'] - geometry['button']['width']) < 0.6, geometry
            assert geometry['flex'] == '0 0 auto', geometry
            assert geometry['overflowX'] == geometry['overflowY'] == 'visible', geometry
            assert geometry['outline'] == 'solid' and geometry['outlineWidth'] >= 1.5, geometry
            assert geometry['clipping'] == [], geometry
            page.wait_for_timeout(200)
            png = page.screenshot(path=str(args.out / (mode + '-complete-focus-ring.png')))
            image = Image.open(io.BytesIO(png)).convert('RGB')
            b = geometry['button']; center_x = b['x'] + b['width']/2; center_y = b['y'] + b['height']/2
            o, w = geometry['outlineOffset'], geometry['outlineWidth']
            bands = {
                'top': (center_x-5, b['top']-o-w-1, center_x+5, b['top']-o+1),
                'bottom': (center_x-5, b['bottom']+o-1, center_x+5, b['bottom']+o+w+1),
                'left': (b['left']-o-w-1, center_y-5, b['left']-o+1, center_y+5),
                'right': (b['right']+o-1, center_y-5, b['right']+o+w+1, center_y+5),
            }
            pixels = {}
            for side, band in bands.items():
                crop = image.crop(tuple(math.floor(v) if i < 2 else math.ceil(v) for i, v in enumerate(band)))
                pixels[side] = sum(all(abs(value-target) <= 18 for value,target in zip(pixel,(119,126,131))) for pixel in crop.getdata())
            assert all(count >= 4 for count in pixels.values()), pixels
            assert draft.input_value() == 'Keep the exact draft\n人工草稿 😀'
            measurements.append({'mode': mode, 'geometry': geometry, 'actual_ring_pixels_by_side': pixels, 'draft_preserved': True})
        return measurements
    check('production-focus-ring-all-four-sides', focus_ring)

    def context_copy():
        # This route is added only by the isolated QA server, never by production.
        page.goto(f'{url.scheme}://{url.netloc}/__qa_context__/', wait_until='domcontentloaded')
        heading = page.locator('.as-native-context-line strong')
        expect(heading).to_have_text('Open the page you want to view')
        page.get_by_role('button', name='Expand current object and annotation', exact=True).click()
        comment = page.locator('.as-native-comment-label textarea')
        comment.fill('Keep my source note 中文 😀')
        details = page.locator('.as-native-context-expanded details')
        details.locator('summary').click()
        diagnostic = details.locator('pre')
        expect(diagnostic).to_contain_text('WINDOW_NOT_OBSERVABLE')
        page.locator('#fixture-language').select_option('zh-CN')
        expect(heading).to_have_text('请打开要查看的页面')
        expect(comment).to_have_value('Keep my source note 中文 😀')
        page.get_by_role('button', name='Unknown reason', exact=True).click()
        expect(heading).to_have_text('当前对象尚未取得')
        expect(diagnostic).to_contain_text('FUTURE_CONTEXT_CODE')
        page.locator('#fixture-language').select_option('en')
        expect(heading).to_have_text('Current object not available yet')
        page.get_by_role('button', name='Actual title', exact=True).click()
        expect(heading).to_have_text('WINDOW_NOT_OBSERVABLE — 用户标题')
        page.locator('#fixture-language').select_option('zh-CN')
        expect(heading).to_have_text('WINDOW_NOT_OBSERVABLE — 用户标题')
        expect(comment).to_have_value('Keep my source note 中文 😀')
        page.screenshot(path=str(args.out / 'context-author-copy-and-raw-diagnostics.png'))
        return {'scope': 'ACTUAL_COMPONENT_INPUT_FIXTURE_NOT_NATIVE', 'known_en_zh': True, 'unknown_human_fallback': True, 'original_diagnostic_retained': True, 'real_title_verbatim': True, 'same_comment_element_and_content': True}
    check('actual-context-panel-bilingual-failure-summary', context_copy)

    forbidden = [r for r in requests if r['method'] not in {'GET', 'HEAD'}]
    result = {'scope': 'REAL_API_PRODUCTION_FOCUS_AND_ISOLATED_COMPONENT_PRESENTATION_NOT_WINDOWS', 'browser': browser.version, 'results': rows, 'page_errors': errors, 'mutations': forbidden, 'requests': requests}
    (args.out / 'RESULT.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    context.close(); browser.close()
    print(json.dumps({'passed': sum(r['status']=='PASS' for r in rows), 'failed': sum(r['status']!='PASS' for r in rows), 'page_errors': errors, 'mutations': forbidden}))
    if any(r['status']!='PASS' for r in rows) or errors or forbidden:
        raise SystemExit(1)
