"""Settings keyboard flows and service recovery against a real local API."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from test_web import launch_browser


def evidence(page, name, observations):
    if not os.environ.get("EEAS_EVIDENCE_DIR"):
        return
    target = Path(os.environ["EEAS_EVIDENCE_DIR"])
    page.evaluate("async () => { await document.fonts.ready; await Promise.all(document.getAnimations().filter(a => a.playState === 'running').map(a => a.finished.catch(() => {}))); }")
    page.screenshot(path=str(target / (name + ".png")), full_page=True)
    (target / (name + ".json")).write_text(json.dumps(observations, ensure_ascii=False, indent=2) + "\n")


@pytest.mark.parametrize("width,height", [(1440, 1000), (900, 760), (390, 844)])
def test_settings_readable_actual_state_keyboard_disclosure_and_responsive_controls(live_server, width, height):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": width, "height": height})
        errors, external = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: external.append(request.url) if not request.url.startswith(live_server.origin + "/") else None)
        page.goto(live_server.origin + "/?view=settings")
        page.wait_for_load_state("networkidle")
        expect(page.locator("#settings-route-status")).to_contain_text("尚未验证")
        expect(page.locator("#settings-connection")).to_contain_text("Python")
        expect(page.locator("#settings-route-sources")).not_to_be_visible()
        details = page.get_by_text("查看配置来源明细", exact=True)
        details.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#settings-route-sources")).to_be_visible()
        expect(page.locator("#settings-route-sources")).to_contain_text("配置 SHA256")
        before = page.locator("#settings-route-status").inner_text()
        details.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#settings-route-sources")).not_to_be_visible()
        switch = page.get_by_role("switch", name="减少界面动画")
        switch.focus()
        page.keyboard.press("Space")
        expect(switch).to_be_checked()
        assert page.locator("body").evaluate("e => e.classList.contains('reduce-motion')")
        switch.press("Space")
        expect(switch).not_to_be_checked()
        assert not page.locator("body").evaluate("e => e.classList.contains('reduce-motion')")
        page.locator("#refresh-settings").click()
        expect(page.locator("#settings-refresh-state")).to_have_text("运行状态已更新")
        assert page.locator("#settings-route-status").inner_text() == before
        bounds = page.locator("#settings-zone button, #settings-zone summary, #settings-zone input, #settings-route-status").evaluate_all("nodes => nodes.map(e => ({text:e.textContent, left:e.getBoundingClientRect().left, right:e.getBoundingClientRect().right}))")
        assert all(item["left"] >= 0 and item["right"] <= width for item in bounds)
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
        evidence(page, f"settings-{width}", {"width": width, "height": height, "bounds": bounds, "page_errors": errors, "external": external})
        assert not errors and not external
        browser.close()


def test_real_service_loss_settings_feedback_releases_button_and_recovers(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_server.origin + "/?view=settings")
        page.wait_for_load_state("networkidle")
        before = page.locator("#settings-route-status").inner_text()
        live_server.stop()
        page.locator("#refresh-settings").click()
        expect(page.locator("#settings-refresh-state")).to_have_text("刷新失败，请重试")
        expect(page.locator("#refresh-settings")).to_be_enabled()
        expect(page.locator("#refresh-settings")).not_to_have_attribute("aria-busy", "true")
        assert page.locator("#settings-route-status").inner_text() == before
        evidence(page, "settings-real-service-loss", {"api_state": "STOPPED", "original_status_preserved": True, "page_errors": errors})
        live_server.start()
        page.locator("#refresh-settings").click()
        expect(page.locator("#settings-refresh-state")).to_have_text("运行状态已更新")
        expect(page.locator("#refresh-settings")).to_be_enabled()
        assert not errors
        browser.close()
