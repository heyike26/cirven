"""Linux Chromium measurements of actual JSX markup plus production CSS; no App/API."""
import asyncio
import hashlib
import json
import os
from legacy_evidence import evidence_folder, chromium_executable
from pathlib import Path
import re

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'apps/agent-shell/src'
CHROMIUM = chromium_executable()


def check(value, message):
    if not value:
        raise AssertionError(message)


def record(path):
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


async def run():
    output = evidence_folder('experience_rule_layout_source59')
    fixture = Path(os.environ['EEAS_REVIEW_HTML'])
    baseline = Path(os.environ['EEAS_REVIEW_BASELINE_CSS'])
    check(record(fixture)['sha256']=='3dd1e3a8265fcb6fb1d1c9ce09c705d7023099638744ded906280b62a2c0798c', 'Original Source59 review markup changed')
    check(record(baseline)['sha256']=='083915da4dcabee78fb12562758b763f7215463bd60f98813ed19a6b38a73d0e', 'Original Source59 baseline stylesheet changed')
    markup = fixture.read_text()
    fonts = {}
    styles = []
    for name in ['styles.css', 'native-collaboration.css', 'agent-studio.css', 'brand-and-spacing.css']:
        path = SOURCE / name
        def font_url(match):
            source = (path.parent / match[1]).resolve()
            if source.suffix not in ('.ttf', '.woff2'):
                return match[0]
            url = f'http://cirven-static.test/font/{len(fonts)}'
            fonts[url] = source
            return f'url("{url}")'
        styles.append(re.sub(r'url\([\'"]?([^\)\'\"]+)[\'"]?\)', font_url, path.read_text()))
    css = '\n'.join(styles)
    # Baseline differs only by the known selector; use the preserved full old CSS,
    # not a hand-written browser reset that would hide later production overrides.
    def baseline_font(match):
        source = (SOURCE / match[1]).resolve()
        url = next((url for url, path in fonts.items() if path == source), None)
        return f'url("{url}")' if url else match[0]
    before = css.replace(styles[0], re.sub(r'url\([\'"]?([^\)\'\"]+)[\'"]?\)', baseline_font, baseline.read_text()))
    measures = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
        try:
            for mode, width in [('before', 750), ('after', 750), ('after', 390), ('after', 300)]:
                page = await browser.new_page(viewport={'width': max(420, width + 64), 'height': 960})
                async def route(request):
                    source = fonts.get(request.request.url)
                    if source:
                        await request.fulfill(path=str(source), content_type='font/ttf' if source.suffix == '.ttf' else 'font/woff2')
                    else:
                        await request.abort()
                await page.route('**/*', route)
                document = '<!doctype html><html lang="zh-CN"><head><style>' + (before if mode == 'before' else css) + '</style></head><body><main style="width:' + str(width) + 'px;margin:16px">' + markup + '</main></body></html>'
                await page.set_content(document)
                await page.evaluate('async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))}')
                value = await page.evaluate('''()=>({panel:document.querySelector('main').getBoundingClientRect().width,rules:[...document.querySelectorAll('.as-writing-rule')].map(rule=>{const t=rule.querySelector('textarea'),r=rule.getBoundingClientRect(),b=t.getBoundingClientRect(),s=getComputedStyle(t);return {label:t.getAttribute('aria-label'),rule_width:r.width,textarea_width:b.width,left:b.left,rule_left:r.left,right:b.right,rule_right:r.right,display:s.display,width:s.width,min_width:s.minWidth,font_size:s.fontSize,padding:s.padding,box_sizing:s.boxSizing,client_width:t.clientWidth,scroll_width:t.scrollWidth}})})''')
                value.update(mode=mode, requested_panel_width=width)
                check(len(value['rules']) == 2, 'Expected actual two-rule JSX fixture')
                if mode == 'before':
                    check(all(rule['textarea_width'] < rule['rule_width'] * .6 for rule in value['rules']), 'Original narrow default textarea was not reproduced')
                else:
                    for rule in value['rules']:
                        check(abs(rule['textarea_width'] - rule['rule_width']) <= 1, str(value))
                        check(rule['display'] == 'block' and rule['min_width'] == '0px', str(value))
                        check(rule['left'] >= rule['rule_left'] - 1 and rule['right'] <= rule['rule_right'] + 1, str(value))
                        check(rule['scroll_width'] <= rule['client_width'] + 1, str(value))
                await page.screenshot(path=str(output / f'{mode}-{width}.png'), full_page=True)
                measures.append(value)
                await page.close()
        finally:
            await asyncio.wait_for(browser.close(), timeout=8)
    libraries = ROOT / 'vendor/test-linux-py310/20261012T122102817223Z/system-libs'
    receipt = {'scope': 'Static actual JSX markup/CSS Linux Chromium, not full native App or Windows qualification', 'fixture': record(fixture), 'baseline_css': record(baseline), 'styles': [record(SOURCE / name) for name in ['styles.css', 'native-collaboration.css', 'agent-studio.css', 'brand-and-spacing.css']], 'chromium': record(CHROMIUM), 'playwright_registry': record(ROOT / '.venv/lib/python3.10/site-packages/playwright/driver/package/browsers.json'), 'library_provenance': record(ROOT / 'reports/E01-env/browser-libraries.json'), 'selected_existing_libraries': [record(libraries / name) for name in ['libnspr4.so', 'libnss3.so', 'libnssutil3.so', 'libsmime3.so', 'libasound.so.2']], 'fonts': [record(path) for path in fonts.values()], 'measurements': measures, 'windows': False, 'app_api_model': False}
    (output / 'BROWSER_LAYOUT_RECEIPT.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')


def test_actual_rule_textarea_uses_available_width_without_overflow():
    asyncio.run(asyncio.wait_for(run(), timeout=40))
