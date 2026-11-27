import subprocess
import sys
import os
import importlib.metadata
import json
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright, expect

from conftest import ROOT


def launch_browser(playwright):
    env = os.environ.copy()
    local_libs = ROOT / ".browser-libs/root/usr/lib/x86_64-linux-gnu"
    if local_libs.is_dir():
        env["LD_LIBRARY_PATH"] = str(local_libs) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if cache:
        registry = Path(importlib.metadata.distribution("playwright").locate_file("playwright/driver/package/browsers.json"))
        shell = next(item for item in json.loads(registry.read_bytes())["browsers"] if item["name"] == "chromium-headless-shell")
        if (Path(cache) / ("chromium_headless_shell-" + shell["revision"])).is_dir():
            return playwright.chromium.launch(headless=True, env=env)
    return playwright.chromium.launch(headless=True, channel="chromium", env=env)


def open_project_form(page):
    """Use the actual accessible creation dialog under the studio home."""
    if not page.locator("#project-drawer").is_visible():
        page.locator("nav [data-view='projects']").click()
        page.locator("#new-project").click()
    expect(page.locator("#project-drawer")).to_be_visible()


def test_real_browser_create_refresh_and_process_restart(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        errors = []
        requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto(live_server.origin)
        open_project_form(page)
        page.locator("#name").fill("浏览器原创夹具")
        page.locator("#description").fill("重启后仍可查询")
        page.get_by_role("button", name="创建项目", exact=True).click()
        page.locator("#projects").get_by_role("button", name="浏览器原创夹具").wait_for()
        page.reload()
        page.locator("#projects").get_by_role("button", name="浏览器原创夹具").wait_for()
        live_server.stop()
        live_server.start()
        page.reload()
        page.locator("#projects").get_by_role("button", name="浏览器原创夹具").click()
        page.locator("nav [data-view='library']").click()
        page.locator(".project-info > summary").click()
        expect(page.locator("#detail")).to_contain_text("重启后仍可查询")
        assert not errors
        assert all(url.startswith(live_server.origin + "/") for url in requests)
        if os.environ.get("EEAS_EVIDENCE_DIR"):
            page.screenshot(path=os.environ["EEAS_EVIDENCE_DIR"] + "/browser.png", full_page=True)
        browser.close()


def test_occupied_port_is_reported_and_stop_releases_port(live_server):
    occupied = subprocess.run([sys.executable, "-m", "apps.api", "start"], cwd=ROOT,
                              env=live_server.env, capture_output=True, text=True, timeout=10)
    assert occupied.returncode == 2
    assert "occupied" in occupied.stderr
    stopped = subprocess.run([sys.executable, "-m", "apps.api", "stop"], cwd=ROOT,
                             env=live_server.env, capture_output=True, text=True, timeout=10)
    assert stopped.returncode == 0, stopped.stderr
    live_server.process.wait(timeout=5)
    live_server.start()


def test_real_browser_import_confirm_plan_revise_and_switch_project(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.goto(live_server.origin)
        open_project_form(page)
        page.locator("#name").fill("资料项目A")
        page.get_by_role("button", name="创建项目", exact=True).click()
        page.locator("#projects").get_by_role("button", name="资料项目A", exact=True).click()
        page.locator("#toolbar-upload").click()
        page.locator("#document-file").set_input_files({"name": "original.md", "mimeType": "text/markdown",
            "buffer": b"REQ: create original output\nACCEPT: content equals fixture\n"})
        page.locator("#document-form").get_by_role("button", name="导入资料", exact=True).click()
        expect(page.locator("#upload-drawer")).to_be_hidden()
        page.locator("#documents").get_by_role("button", name="original.md · v1", exact=True).click()
        expect(page.locator("#document-content")).to_contain_text("REQ:")
        page.locator("nav [data-view='requirements']").click()
        page.get_by_role("button", name="从所选资料生成草案（Mock）", exact=True).click()
        expect(page.locator("#requirement-text")).to_have_value("create original output")
        expect(page.locator("#requirement-state")).to_contain_text("Mock")
        page.locator("#confirmed").check()
        page.get_by_role("button", name="保存需求新修订", exact=True).click()
        expect(page.locator("#requirement-state")).to_contain_text("已保存 v1")
        page.get_by_role("button", name="为已保存需求生成规划任务", exact=True).click()
        expect(page.locator("#planning-tasks")).to_contain_text("规划草稿")
        page.locator("#requirement-text").fill("changed original output")
        page.get_by_role("button", name="保存需求新修订", exact=True).click()
        expect(page.locator("#requirement-state")).to_contain_text("已保存 v2")
        expect(page.locator("#planning-tasks")).to_contain_text("待复核")
        page.reload()
        page.locator("nav [data-view='projects']").click()
        page.locator("#projects").get_by_role("button", name="资料项目A", exact=True).click()
        page.locator("nav [data-view='requirements']").click()
        expect(page.locator("#requirements")).to_contain_text("v2")
        expect(page.locator("#planning-tasks")).to_contain_text("待复核")
        open_project_form(page)
        page.locator("#name").fill("资料项目B")
        page.get_by_role("button", name="创建项目", exact=True).click()
        page.locator("#projects").get_by_role("button", name="资料项目B", exact=True).click()
        page.locator("nav [data-view='library']").click()
        expect(page.locator("#documents")).to_contain_text("尚无资料")
        page.locator("nav [data-view='requirements']").click()
        expect(page.locator("#requirements")).to_be_empty()
        expect(page.locator("#planning-tasks")).to_be_empty()
        browser.close()
    assert httpx.get(live_server.origin + "/health", trust_env=False).status_code == 200


def test_background_start_diagnose_and_safe_stop(live_server):
    live_server.stop()
    try:
        start = subprocess.run([sys.executable, "-m", "apps.api", "start"], cwd=ROOT,
                               env=live_server.env, capture_output=True, text=True, timeout=200)
        assert start.returncode == 0, start.stderr
        diagnosis = subprocess.run([sys.executable, "-m", "apps.api", "diagnose"], cwd=ROOT,
                                   env=live_server.env, capture_output=True, text=True, timeout=5)
        assert diagnosis.returncode == 0
        assert '"status": "ok"' in diagnosis.stdout
    finally:
        stop = subprocess.run([sys.executable, "-m", "apps.api", "stop"], cwd=ROOT,
                              env=live_server.env, capture_output=True, text=True, timeout=10)
    assert stop.returncode == 0, stop.stderr
    live_server.start()
