"""Actual comparison-page continuity and keyboard checks, using original demo data."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from test_web import launch_browser


def save(name, value):
    folder = os.environ.get("EEAS_EVIDENCE_DIR")
    if folder:
        (Path(folder) / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 540)])
def test_three_layouts_keep_selected_source_and_native_drawer_keyboard_reduction(live_server, width, height):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="no-preference")
        page = context.new_page()
        errors, foreign, api_calls = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: foreign.append(request.url)
                if not request.url.startswith(live_server.origin + "/") else None)
        page.on("request", lambda request: api_calls.append(request.url) if "/api/" in request.url else None)
        try:
            page.goto(live_server.origin + "/assets/glass-lab/index.html")
            page.wait_for_load_state("networkidle")
            page.evaluate("async () => { await document.fonts.ready; }")
            expect(page.locator(".experiment-label")).to_contain_text("原创示意")
            source = page.locator("#lab-article").inner_text()
            original_object = page.locator("#lab-current-object").text_content()
            page.evaluate("window.__originalArticle = document.querySelector('#lab-article')")
            geometries = []
            for mode in ("floating", "top", "rail"):
                button = page.locator("#lab-mode-" + mode)
                button.focus()
                page.keyboard.press("Enter")
                expect(button).to_have_attribute("aria-pressed", "true")
                expect(page.locator("#lab-nav-source")).to_have_attribute("aria-current", "page")
                assert page.locator("#lab-article").inner_text() == source
                assert page.locator("#lab-current-object").text_content() == original_object
                assert page.evaluate("window.__originalArticle === document.querySelector('#lab-article')")
                page.evaluate("async () => { await Promise.all(document.getAnimations().map(a => a.finished.catch(() => {}))); }")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                geometries.append(page.locator("#lab-navigation").bounding_box())
                page.evaluate("window.scrollTo(0, 0)")
                folder = os.environ.get("EEAS_EVIDENCE_DIR")
                if folder:
                    page.screenshot(path=str(Path(folder) / f"lab-{mode}-{width}.png"), full_page=True)
            assert len({round(box["width"]) for box in geometries}) > 1
            # Trusted clicks execute consecutive navigation and preserve the last choice.
            for key in ("notes", "task", "review", "source"):
                page.locator("#lab-nav-" + key).click()
            expect(page.locator("#lab-nav-source")).to_have_attribute("aria-current", "page")
            assert page.locator("#lab-article").inner_text() == source
            page.locator("#lab-open-context").focus()
            page.keyboard.press("Enter")
            drawer = page.locator("#lab-object-drawer")
            expect(drawer).to_be_visible()
            assert page.evaluate("document.querySelector('#lab-object-drawer').contains(document.activeElement)")
            page.set_viewport_size({"width": 390 if width == 1440 else 1440, "height": 540})
            expect(drawer).to_be_visible()
            assert page.locator("#lab-current-object").text_content() == original_object
            page.keyboard.press("Escape")
            expect(drawer).not_to_be_visible()
            expect(page.locator("#lab-open-context")).to_be_focused()
            reduce = page.locator("#lab-reduce-motion")
            reduce.focus()
            page.keyboard.press("Space")
            expect(reduce).to_be_checked()
            page.locator("#lab-mode-floating").click()
            assert not page.evaluate("document.getAnimations().some(a => a.playState === 'running')")
            reduce.uncheck()
            page.evaluate("""() => { const media=matchMedia('(prefers-reduced-motion: reduce)');
              window.__nativeReduction = new Promise(resolve => media.addEventListener('change',
                event => resolve({matches:event.matches,trusted:event.isTrusted}), {once:true})); }""")
            page.emulate_media(reduced_motion="reduce")
            actual_event = page.evaluate("window.__nativeReduction")
            assert actual_event == {"matches": True, "trusted": True}
            expect(page.locator("#lab-motion-status")).to_contain_text("系统已要求减少动态")
            page.locator("#lab-mode-top").click()
            assert not page.evaluate("document.getAnimations().some(a => a.playState === 'running')")
            save(f"lab-continuity-{width}.json", {"native_event": actual_event, "geometries": geometries,
                 "source_preserved": True, "object_preserved": True, "article_identity_preserved": True,
                 "keyboard_drawer_resize_escape": True, "foreign_requests": foreign, "api_calls": api_calls,
                 "page_errors": errors, "qualification": "Original isolated layout experiment; WSL Chromium only"})
            assert not errors and not foreign and not api_calls
        finally:
            context.close()
            browser.close()
