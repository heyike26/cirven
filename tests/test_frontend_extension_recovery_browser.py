"""Original extension import recovery counters against the real local API.

Only the response delivery / follow-up refresh is fault-injected. Successful
imports and invalid ZIP rejection still execute the real extension service.
These browser cases do not qualify Windows, LIVE plugins or user aesthetics.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

from test_extensions import archive
from test_web import launch_browser


def evidence(page, name, facts):
    if not os.environ.get("EEAS_EVIDENCE_DIR"):
        return
    target = Path(os.environ["EEAS_EVIDENCE_DIR"])
    target.mkdir(parents=True, exist_ok=True)
    (target / (name + ".json")).write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    page.screenshot(path=str(target / (name + ".png")), full_page=True)


def open_extensions(page, origin):
    page.goto(origin)
    expect(page.locator("#home-project-count")).to_have_text("0")
    page.locator("nav [data-view='capabilities']").click()
    expect(page.locator("#extension-state")).to_contain_text("尚无本地扩展")
    expect(page.locator("#choose-extension-file")).to_be_enabled()


def choose_zip(page, name, raw):
    with page.expect_file_chooser() as chosen:
        page.locator("#choose-extension-file").click()
    chosen.value.set_files({"name": name, "mimeType": "application/zip", "buffer": raw})
    expect(page.locator("#extension-file-name")).to_have_text(name)
    expect(page.locator("#extension-import-form [type=submit]")).to_be_enabled()


def registered(page, origin):
    response = page.request.get(origin + "/api/extensions")
    assert response.ok, response.text()
    return response.json()["extensions"]


def observe(page):
    errors, posts = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: posts.append(request.url)
            if request.method == "POST" and urlparse(request.url).path == "/api/extensions" else None)
    return errors, posts


def assert_only_unapproved_candidate(rows):
    assert len(rows) == 1, rows
    assert rows[0]["channel"] == "CANDIDATE", rows
    assert not rows[0]["qualified"] and not rows[0]["loaded"], rows


def test_saved_extension_refresh_failure_keeps_success_and_does_not_repost(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors, posts = observe(page)
        open_extensions(page, live_server.origin)
        choose_zip(page, "原创已保存刷新失败.zip", archive(1))
        failures = []

        def fail_first_runtime_refresh(route):
            if not failures:
                failures.append(route.request.url)
                route.fulfill(status=503, content_type="application/json", body=json.dumps({
                    "error": {"message": "原创运行资格刷新失败"}}, ensure_ascii=False))
            else:
                route.continue_()

        page.route("**/api/runtime", fail_first_runtime_refresh)
        page.locator("#extension-import-form [type=submit]").click()
        expect(page.locator("#error")).to_contain_text("原创运行资格刷新失败")
        rows = registered(page, live_server.origin)
        assert_only_unapproved_candidate(rows)
        state = page.locator("#extension-state").inner_text()
        evidence(page, "extension-post-success-refresh-failure", {
            "registered": rows, "state": state, "refresh_failures": failures,
            "extension_posts": posts, "page_errors": errors})
        assert "导入未成功" not in state and "导入失败" not in state, state
        assert "已登记" in state and "刷新" in state, state
        expect(page.locator("#extension-file-name")).to_have_text("尚未选择文件")
        expect(page.locator("#choose-extension-file")).to_be_enabled()
        expect(page.locator("#extension-import-form [type=submit]")).to_be_disabled()
        page.locator("#extension-import-form").evaluate("form => form.requestSubmit()")
        assert len(posts) == 1, posts
        page.locator("#refresh-capabilities").click()
        expect(page.locator("#extensions > li")).to_have_count(1)
        expect(page.locator("#extension-state")).to_contain_text("已登记 1")
        assert len(posts) == 1 and len(failures) == 1, (posts, failures)
        assert_only_unapproved_candidate(registered(page, live_server.origin))
        assert not errors, errors
        browser.close()


def test_real_extension_post_failure_preserves_file_and_unlocks_retry(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors, posts = observe(page)
        open_extensions(page, live_server.origin)
        invalid_name = "原创无效ZIP保留重试.zip"
        choose_zip(page, invalid_name, b"original bytes are not a ZIP archive")
        page.locator("#extension-import-form [type=submit]").click()
        expect(page.locator("#error")).to_contain_text("Invalid local extension archive")
        expect(page.locator("#extension-file-name")).to_have_text(invalid_name)
        expect(page.locator("#extension-file")).to_be_enabled()
        expect(page.locator("#choose-extension-file")).to_be_enabled()
        expect(page.locator("#extension-import-form [type=submit]")).to_be_enabled()
        assert page.locator("#extension-file").evaluate("input => input.files[0].name") == invalid_name
        assert registered(page, live_server.origin) == []
        assert len(posts) == 1, posts
        evidence(page, "extension-real-post-failure-retry-ready", {
            "registered": [], "selected_name": invalid_name,
            "extension_posts": posts, "page_errors": errors})
        choose_zip(page, "原创修正后重试.zip", archive(1))
        page.locator("#extension-import-form [type=submit]").click()
        expect(page.locator("#extensions > li")).to_have_count(1)
        expect(page.locator("#extension-file-name")).to_have_text("尚未选择文件")
        assert_only_unapproved_candidate(registered(page, live_server.origin))
        assert len(posts) == 2, posts
        assert not errors, errors
        browser.close()


def test_busy_extension_import_stays_locked_through_refresh_and_duplicate_submit(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors, posts = observe(page)
        open_extensions(page, live_server.origin)
        filename = "原创等待服务端确认.zip"
        choose_zip(page, filename, archive(1))
        held = []

        def hold_import(route):
            if route.request.method == "POST":
                held.append(route)
            else:
                route.continue_()

        page.route("**/api/extensions", hold_import)
        page.locator("#extension-import-form [type=submit]").click()
        expect(page.locator("#extension-file")).to_be_disabled()
        expect(page.locator("#choose-extension-file")).to_be_disabled()
        expect(page.locator("#extension-import-form [type=submit]")).to_be_disabled()
        expect(page.locator("#extension-file-name")).to_have_text(filename)
        page.locator("#refresh-capabilities").click()
        expect(page.locator("#capability-summary")).to_contain_text("本地扩展：登记 0")
        expect(page.locator("#choose-extension-file")).to_be_disabled()
        expect(page.locator("#extension-import-form [type=submit]")).to_be_disabled()
        page.locator("#extension-import-form").evaluate("""form => {
          for (let index = 0; index < 2; index++)
            form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
        }""")
        assert len(held) == 1 and len(posts) == 1, (len(held), posts)
        assert registered(page, live_server.origin) == []
        evidence(page, "extension-busy-single-request", {
            "registered": [], "selected_name": filename, "held_posts": len(held),
            "extension_posts": posts, "page_errors": errors})
        held.pop().continue_()
        expect(page.locator("#extensions > li")).to_have_count(1)
        expect(page.locator("#choose-extension-file")).to_be_enabled()
        expect(page.locator("#extension-file-name")).to_have_text("尚未选择文件")
        expect(page.locator("#extension-import-form [type=submit]")).to_be_disabled()
        assert_only_unapproved_candidate(registered(page, live_server.origin))
        assert len(posts) == 1, posts
        assert not errors, errors
        browser.close()
