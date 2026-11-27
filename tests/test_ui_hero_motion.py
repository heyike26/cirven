"""Real painted illustration feedback, with still controls and no touch/reduce motion."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import expect, sync_playwright

from test_web import launch_browser


def settled(page):
    page.evaluate("""async () => {
        await document.fonts.ready;
        await document.querySelector('#studio-hero-image').decode();
        await new Promise(requestAnimationFrame);
        await Promise.all(document.getAnimations().filter(a => a.playState === 'running')
            .map(a => a.finished.catch(() => {})));
    }""")


def note(name, value):
    if os.environ.get('EEAS_EVIDENCE_DIR'):
        (Path(os.environ['EEAS_EVIDENCE_DIR']) / (name + '.json')).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def photograph(page, name):
    raw = page.screenshot()
    if os.environ.get('EEAS_EVIDENCE_DIR'):
        (Path(os.environ['EEAS_EVIDENCE_DIR']) / (name + '.png')).write_bytes(raw)
    return Image.open(io.BytesIO(raw)).convert('RGB')


def media_change(page, preference):
    page.evaluate("""() => { window.__heroMediaEvent = new Promise((resolve, reject) => {
        const done = e => { clearTimeout(timer); resolve({matches: e.matches, trusted: e.isTrusted}); };
        const timer = setTimeout(() => { motionPreference.removeEventListener('change', done);
            reject(new Error('NATIVE_MEDIA_EVENT_TIMEOUT')); }, 1000);
        motionPreference.addEventListener('change', done, {once: true});
    }); }""")
    page.emulate_media(reduced_motion=preference)
    event = page.evaluate('() => window.__heroMediaEvent')
    assert event['trusted'] and event['matches'] == (preference == 'reduce')


def test_inspection_light_is_painted_locally_controls_stay_still_and_leave_restores(live_server):
    errors, external = [], []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='no-preference')
        page = context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda r: external.append(r.url) if not r.url.startswith(live_server.origin + '/') else None)
        page.goto(live_server.origin); page.wait_for_load_state('networkidle'); settled(page)
        hero = page.locator('.studio-hero'); bounds = hero.bounding_box()
        controls = page.locator('.hero-actions').bounding_box()
        crop = (round(bounds['x'] + bounds['width'] * .6), round(bounds['y'] + 10),
                round(bounds['x'] + bounds['width'] - 10), round(bounds['y'] + bounds['height'] - 10))
        before = photograph(page, 'illustration-default').crop(crop)
        page.mouse.move(bounds['x'] + bounds['width'] * .72, bounds['y'] + bounds['height'] * .27)
        expect(hero).to_have_attribute('data-inspecting', 'true'); settled(page)
        first = photograph(page, 'illustration-inspection-top').crop(crop)
        page.mouse.move(bounds['x'] + bounds['width'] * .91, bounds['y'] + bounds['height'] * .74)
        settled(page); second = photograph(page, 'illustration-inspection-bottom').crop(crop)
        assert ImageChops.difference(first, before).getbbox(), 'No material feedback is painted'
        assert ImageChops.difference(first, second).getbbox(), 'Feedback does not follow the inspected drawing'
        assert page.locator('.hero-actions').bounding_box() == controls
        assert hero.bounding_box() == bounds
        page.mouse.move(230, 80); settled(page)
        restored = photograph(page, 'illustration-left').crop(crop)
        extrema = ImageChops.difference(before, restored).getextrema()
        assert max(peak for _, peak in extrema) <= 2, 'Leaving the illustration retains an active light'
        assert page.evaluate("() => !document.querySelector('.studio-hero').hasAttribute('data-inspecting')")
        assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
        note('inspection-paint-and-controls', {'hero': bounds, 'controls': controls, 'restored_difference': extrema,
             'page_errors': errors, 'external_requests': external, 'browser_version': browser.version})
        assert not errors and not external
        context.close(); browser.close()


def test_native_and_manual_reduce_cancel_pending_feedback_and_touch_stays_static(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='no-preference')
        errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(live_server.origin); page.wait_for_load_state('networkidle'); settled(page)
        hero = page.locator('.studio-hero'); box = hero.bounding_box()
        page.mouse.move(box['x'] + box['width'] * .8, box['y'] + box['height'] * .5)
        expect(hero).to_have_attribute('data-inspecting', 'true')
        media_change(page, 'reduce'); settled(page)
        assert page.evaluate("() => !document.querySelector('.studio-hero').hasAttribute('data-inspecting')")
        assert page.evaluate("() => getComputedStyle(document.querySelector('.studio-hero'),'::before').content") == 'none'
        media_change(page, 'no-preference')
        page.locator('nav [data-view=settings]').click()
        expect(page.locator('#reduce-motion')).to_be_enabled(); page.locator('#reduce-motion').check()
        page.locator('nav [data-view=projects]').click(); settled(page)
        page.mouse.move(box['x'] + box['width'] * .9, box['y'] + box['height'] * .4)
        settled(page)
        assert page.evaluate("() => getComputedStyle(document.querySelector('.studio-hero'),'::before').content") == 'none'
        photograph(page, 'illustration-manual-reduce')
        phone = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        mobile = phone.new_page(); mobile.on('pageerror', lambda e: errors.append(str(e)))
        mobile.goto(live_server.origin); mobile.wait_for_load_state('networkidle'); settled(mobile)
        touch_box = mobile.locator('.studio-hero').bounding_box()
        mobile.touchscreen.tap(touch_box['x'] + touch_box['width'] - 20, touch_box['y'] + 20); settled(mobile)
        assert mobile.evaluate("() => matchMedia('(pointer:coarse)').matches")
        assert mobile.evaluate("() => getComputedStyle(document.querySelector('.studio-hero'),'::before').content") == 'none'
        assert mobile.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
        photograph(mobile, 'illustration-mobile-touch')
        note('inspection-reduce-touch', {'native_event_received': True, 'manual_reduced': True,
             'mobile_touch_static': True, 'page_errors': errors, 'browser_version': browser.version})
        assert not errors
        phone.close(); browser.close()
