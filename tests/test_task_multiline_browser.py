"""A multiline Task goal retains exact LF through form, frozen run, window and artifact."""
import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from test_web import launch_browser
from test_desktop_design import project


def test_multiline_goal_survives_real_form_frozen_run_popup_verification_and_download(live_server):
    goal = "原创多行候选目标\n参数 Straße 😀 不能被压成空格\n末行独立核查"
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(live_server.origin)
            pid = project(page, "原创多行 Task 回归")
            page.locator("nav [data-view=requirements]").click()
            page.locator("#requirement-title").fill("人工原创多行候选")
            page.locator("#requirement-text").fill("生成冻结的完整多行目标")
            page.locator("#acceptance-text").fill("逐字核对 LF 和 Unicode，不归一化为单行")
            page.locator("#confirmed").check()
            page.get_by_role("button", name="保存需求新修订", exact=True).click()
            expect(page.locator("#requirement-state")).to_contain_text("已保存 v1")
            page.locator("nav [data-view=tasks]").click()
            field = page.locator("#task-goal")
            assert field.evaluate("el=>el.tagName") == "TEXTAREA"
            expect(field).to_have_attribute("required", "")
            expect(field).to_have_attribute("maxlength", "4000")
            field.fill(goal)
            assert field.input_value() == goal
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/tasks")) as created:
                page.locator("#task-form button[type=submit]").click()
            assert created.value.status == 201
            task = created.value.json()
            assert task["spec"]["goal"] == goal
            page.locator("#execution-tasks").get_by_role("button", name="启动任务", exact=True).click()
            expect(page.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
            with page.expect_popup() as opened:
                page.locator("#open-run-window").click()
            popup = opened.value
            popup.on("pageerror", lambda error: errors.append(str(error)))
            expect(popup.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
            assert popup.locator("#run-title").text_content() == goal + " · 任务 v1"
            assert popup.locator("#run-title").evaluate("e=>getComputedStyle(e).whiteSpace") == "pre-wrap"
            popup.reload()
            expect(popup.locator("#run-title")).to_contain_text(goal)
            popup.get_by_role("button", name="独立验收原创夹具", exact=True).click()
            expect(popup.locator("#verification-result")).to_contain_text("PASSED", timeout=15000)
            with popup.expect_download() as downloaded:
                popup.locator("#run-artifacts").get_by_role("link", name="candidate.txt", exact=True).click()
            raw = Path(downloaded.value.path()).read_bytes()
            assert raw == (goal + "\n").encode()
            rows = page.request.get(f"{live_server.origin}/api/projects/{pid}/tasks/{task['id']}/runs").json()["runs"]
            assert len(rows) == 1
            run = page.request.get(f"{live_server.origin}/api/projects/{pid}/runs/{rows[0]['id']}").json()
            assert json.loads(run["frozen_inputs"])["spec"]["goal"] == goal
            assert run["verification_status"] == "PASSED"
            target = os.environ.get("EEAS_EVIDENCE_DIR")
            if target:
                evidence = Path(target)
                popup.screenshot(path=str(evidence / "multiline-independent-window.png"), full_page=True)
                (evidence / "multiline-candidate.bin").write_bytes(raw)
                (evidence / "multiline-frozen-task.json").write_text(json.dumps({"goal": goal, "task": task, "run": run}, ensure_ascii=False, indent=2) + "\n")
            assert not errors
        finally:
            context.close()
            browser.close()
