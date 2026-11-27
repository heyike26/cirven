"""Current mobile menu intent survives delayed native breakpoint delivery."""
import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from campaign_menu_debug import TIMELINE
from test_web import launch_browser


def change_event(page):
    page.evaluate("""() => { window.__actualBreakpointChange = new Promise((resolve,reject)=>{
      const mql=matchMedia('(max-width:700px)');
      const done=e=>{clearTimeout(deadline);resolve({trusted:e.isTrusted,matches:e.matches});};
      const deadline=setTimeout(()=>{mql.removeEventListener('change',done);
        reject(new Error('NATIVE_BREAKPOINT_EVENT_TIMEOUT_1000MS'))},1000);
      mql.addEventListener('change',done,{once:true});
    }); }""")


def test_current_mobile_open_intent_native_delivery_and_desktop_close_are_consistent(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_init_script("(" + TIMELINE + ")()")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(live_server.origin)
            page.wait_for_load_state("networkidle")
            change_event(page)
            page.set_viewport_size({"width": 390, "height": 540})
            page.locator("#menu-toggle").focus()
            page.keyboard.press("Enter")
            # The original assertion remains immediate, without waiting for expected state.
            assert page.locator("#desktop-sidebar").evaluate("e=>e.contains(document.activeElement)")
            native = page.evaluate("window.__actualBreakpointChange")
            assert native == {"trusted": True, "matches": True}
            assert page.locator("#desktop-sidebar").evaluate("e=>e.contains(document.activeElement)")
            expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "true")
            assert not page.locator("#desktop-sidebar").evaluate("e=>e.inert")
            page.keyboard.press("Tab")
            assert page.locator("#desktop-sidebar").evaluate("e=>e.contains(document.activeElement)")
            page.keyboard.press("Escape")
            expect(page.locator("#menu-toggle")).to_be_focused()
            assert page.locator("#desktop-sidebar").evaluate("e=>e.inert")
            page.keyboard.press("Enter")
            change_event(page)
            page.set_viewport_size({"width": 1440, "height": 1000})
            native_desktop = page.evaluate("window.__actualBreakpointChange")
            assert native_desktop == {"trusted": True, "matches": False}
            assert not page.locator("body").evaluate("e=>e.classList.contains('sidebar-open')")
            assert not page.locator("#desktop-sidebar").evaluate("e=>e.inert")
            expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "false")
            change_event(page)
            page.set_viewport_size({"width": 390, "height": 540})
            native_return = page.evaluate("window.__actualBreakpointChange")
            assert native_return == {"trusted": True, "matches": True}
            assert page.locator("#desktop-sidebar").evaluate("e=>e.inert")
            assert not page.locator("body").evaluate("e=>e.classList.contains('sidebar-open')")
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            assert not errors
        finally:
            target = os.environ.get("EEAS_EVIDENCE_DIR")
            if target:
                (Path(target) / "mobile-desktop-native-breakpoints.json").write_text(json.dumps({
                    "timeline": page.evaluate("window.__menuTimeline"), "page_errors": errors}, ensure_ascii=False, indent=2) + "\n")
                page.screenshot(path=str(Path(target) / "mobile-desktop-native-breakpoints.png"), full_page=True)
            context.close()
            browser.close()
