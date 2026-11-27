"""Original browser fixtures for independent task windows, never Windows Runner qualification."""
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

from test_web import launch_browser, open_project_form


def view(page, name):
    if page.locator("#menu-toggle").is_visible() and page.locator("#menu-toggle").get_attribute("aria-expanded") == "false":
        page.locator("#menu-toggle").click()
    page.locator(f"nav [data-view='{name}']").click()
    expect(page.locator("[data-zone]:visible")).to_have_count(1)


def route(page):
    return {key: values[0] for key, values in parse_qs(urlparse(page.url).query).items()}


def proof_path(name):
    directory = os.environ.get("EEAS_EVIDENCE_DIR")
    return Path(directory) / name if directory else None


def test_separate_work_views_restore_and_mobile_layout(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.goto(live_server.origin)
        expect(page.locator("[data-zone]:visible")).to_have_count(1)
        expect(page.locator("#project-zone")).to_be_visible()
        view(page, "tasks")
        expect(page.locator("#tasks-zone .project-prompt")).to_be_visible()
        view(page, "capabilities")
        expect(page.locator("#capability-summary")).to_contain_text("Python 3.10")
        page.reload()
        expect(page.locator("#capabilities-zone")).to_be_visible()
        assert route(page)["view"] == "capabilities"
        view(page, "projects")
        open_project_form(page)
        page.locator("#name").fill("原创多窗口项目")
        page.get_by_role("button", name="创建项目", exact=True).click()
        page.locator("#projects").get_by_role("button", name="原创多窗口项目", exact=True).click()
        expect(page.locator("#project-context")).to_have_text("原创多窗口项目")
        with page.expect_popup() as opened:
            page.locator("#open-view-window").click()
        other = opened.value
        expect(other.locator("#project-context")).to_have_text("原创多窗口项目")
        assert other.evaluate("window.opener === null")
        assert route(other)["project"] == route(page)["project"]
        view(other, "requirements")
        expect(page.locator("#project-zone")).to_be_visible()
        assert route(page)["view"] == "projects"
        if proof_path("project-workspace.png"):
            page.screenshot(path=str(proof_path("project-workspace.png")), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#menu-toggle").click()
        expect(page.locator("#retry-connection")).to_be_visible()
        view(page, "results")
        expect(page.locator("#results-empty")).to_be_visible()
        if proof_path("mobile-empty-result.png"):
            page.screenshot(path=str(proof_path("mobile-empty-result.png")), full_page=True)
        browser.close()


def test_two_real_task_windows_no_mixed_events_refresh_and_backend_effects(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = browser.new_context(viewport={"width": 1440, "height": 950})
        main = context.new_page()
        errors = []
        main.on("pageerror", lambda error: errors.append(str(error)))
        main.goto(live_server.origin)
        open_project_form(main)
        main.locator("#name").fill("原创并列运行窗口")
        main.get_by_role("button", name="创建项目", exact=True).click()
        main.locator("#projects").get_by_role("button", name="原创并列运行窗口", exact=True).click()
        view(main, "requirements")
        main.locator("#requirement-title").fill("原创窗口隔离需求")
        main.locator("#requirement-text").fill("各运行保持独立输入、事件与证据")
        main.locator("#acceptance-text").fill("独立检查冻结目标，不串任务")
        main.locator("#confirmed").check()
        main.get_by_role("button", name="保存需求新修订", exact=True).click()
        expect(main.locator("#requirement-state")).to_contain_text("已保存 v1")
        view(main, "tasks")
        main.locator("#task-goal").fill("原创窗口甲候选")
        main.get_by_role("button", name="创建执行任务", exact=True).click()
        main.locator("#execution-tasks li").filter(has_text="原创窗口甲候选").get_by_role("button", name="启动任务", exact=True).click()
        expect(main.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
        first_route = route(main)
        with main.expect_popup() as first_opened:
            main.locator("#open-run-window").click()
        first = first_opened.value
        first_requests = []
        first.on("request", lambda request: first_requests.append(request.url) if "/events?" in request.url else None)
        first.on("pageerror", lambda error: errors.append(str(error)))
        expect(first.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
        assert first.evaluate("window.opener === null")
        view(main, "tasks")
        main.locator("#task-goal").fill("原创窗口乙等待取消")
        main.locator("#mock-behavior").select_option("wait_for_cancel")
        main.locator("#task-timeout").fill("120")
        main.get_by_role("button", name="创建执行任务", exact=True).click()
        main.locator("#execution-tasks li").filter(has_text="原创窗口乙等待取消").get_by_role("button", name="启动任务", exact=True).click()
        expect(main.locator("#run-status")).to_contain_text("执行中", timeout=15000)
        second_route = route(main)
        assert first_route["run"] != second_route["run"]
        with main.expect_popup() as second_opened:
            main.locator("#open-run-window").click()
        second = second_opened.value
        second_requests = []
        second.on("request", lambda request: second_requests.append(request.url) if "/events?" in request.url else None)
        second.on("pageerror", lambda error: errors.append(str(error)))
        expect(second.locator("#run-status")).to_contain_text("执行中", timeout=15000)
        assert route(first)["run"] == first_route["run"]
        assert route(second)["run"] == second_route["run"]
        expect(first.locator("#run-identity")).to_contain_text(first_route["run"])
        expect(second.locator("#run-identity")).to_contain_text(second_route["run"])
        first.reload()
        second.reload()
        expect(first.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
        expect(second.locator("#run-status")).to_contain_text("执行中", timeout=15000)
        first.get_by_role("button", name="独立验收原创夹具", exact=True).click()
        expect(first.locator("#run-status")).to_contain_text("验收通过", timeout=15000)
        expect(second.locator("#run-status")).to_contain_text("未验收")
        second.get_by_role("button", name="取消运行", exact=True).click()
        expect(second.locator("#run-status")).to_contain_text("已取消", timeout=15000)
        expect(first.locator("#run-status")).to_contain_text("验收通过")
        first.get_by_role("button", name="读取运行日志", exact=True).click()
        expect(first.locator("#run-logs")).to_contain_text("original-candidate-written")
        for page in (first, second):
            lines = page.locator("#run-events").inner_text().splitlines()
            sequences = [int(line.split(" · ")[0]) for line in lines]
            assert sequences == sorted(set(sequences))
        assert all(f"/runs/{first_route['run']}/events?" in url for url in first_requests)
        assert all(f"/runs/{second_route['run']}/events?" in url for url in second_requests)
        assert first_requests and second_requests
        assert "独立验收：PASSED" in first.locator("#run-events").inner_text()
        assert "独立验收：PASSED" not in second.locator("#run-events").inner_text()
        pid = first_route["project"]
        backend_first = first.request.get(f"{live_server.origin}/api/projects/{pid}/runs/{first_route['run']}").json()
        backend_second = second.request.get(f"{live_server.origin}/api/projects/{pid}/runs/{second_route['run']}").json()
        assert backend_first["execution_status"] == "RETURNED"
        assert backend_first["verification_status"] == "PASSED"
        assert backend_second["execution_status"] == "CANCELLED"
        assert backend_second["verification_status"] == "NOT_APPLICABLE"
        artifact = first.request.get(f"{live_server.origin}/api/projects/{pid}/runs/{first_route['run']}/artifacts/candidate.txt")
        assert artifact.status == 200 and artifact.text() == "原创窗口甲候选\n"
        with sqlite3.connect(live_server.data_dir / "eeas.sqlite3") as connection:
            assert connection.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 2
            assert connection.execute("SELECT COUNT(*) FROM runner_jobs").fetchone()[0] == 2
        # Preserve the original project/run route without view/window parameters.
        legacy = context.new_page()
        legacy.goto(f"{live_server.origin}/?project={pid}&run={first_route['run']}")
        expect(legacy.locator("#run-status")).to_contain_text("验收通过", timeout=15000)
        expect(legacy.locator("#results-zone")).to_be_visible()
        view(main, "tasks")
        main.locator("#refresh-tasks").click()
        row = main.locator("#execution-tasks li").filter(has_text="原创窗口甲候选")
        with main.expect_popup() as historical_opened:
            row.get_by_role("link", name="独立窗口查看最近运行", exact=True).click()
        historical = historical_opened.value
        expect(historical.locator("#run-status")).to_contain_text("验收通过", timeout=15000)
        assert route(historical)["run"] == first_route["run"]
        if proof_path("first-run-window.png"):
            first.screenshot(path=str(proof_path("first-run-window.png")), full_page=True)
            second.screenshot(path=str(proof_path("second-run-window.png")), full_page=True)
            main.screenshot(path=str(proof_path("task-workspace.png")), full_page=True)
            proof_path("windows-backend.json").write_text(json.dumps({
                "platform_qualification": "WSL Chromium only; Windows Native Runner NOT_VALIDATED",
                "first": backend_first, "second": backend_second,
                "first_event_requests": first_requests, "second_event_requests": second_requests,
                "artifact": artifact.text(), "run_count": 2, "runner_job_count": 2
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        assert not errors
        browser.close()


def test_each_historical_attempt_has_an_independent_window(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.goto(live_server.origin)
        open_project_form(page)
        expect(page.get_by_role("button", name="创建项目", exact=True)).to_be_enabled()
        page.locator("#close-project-drawer").click()
        expect(page.locator("#project-drawer")).to_be_hidden()
        csrf = page.request.get(live_server.origin + "/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"}).json()["csrf_token"]
        headers = {"Origin": live_server.origin, "X-EEAS-CSRF": csrf}
        def post(path, payload):
            response = page.request.post(live_server.origin + path, data=payload, headers=headers)
            assert response.ok, response.text()
            return response.json()
        project = post("/api/projects", {"name": "原创历史运行窗口", "description": "旧运行保持可查看"})
        pid = project["id"]
        requirement = post(f"/api/projects/{pid}/requirements", {
            "schema_version": "eeas.requirements.v1", "title": "原创重复运行需求",
            "requirements": [{"id": "R1", "text": "保持每次运行的冻结证据", "source_refs": []}],
            "acceptance": [{"id": "A1", "text": "每项运行都有独立上下文", "requirement_ids": ["R1"]}],
            "confirmed": True, "origin": "manual", "assumptions": [], "questions": []})
        task = post(f"/api/projects/{pid}/tasks", {"goal": "原创历史候选", "requirement_refs": [{
            "requirement_id": requirement["id"], "revision": requirement["revision"], "sha256": requirement["sha256"]}],
            "allowed_write_paths": ["candidate.txt"], "dependencies": []})
        runs = [post(f"/api/projects/{pid}/tasks/{task['id']}/runs", {
            "revision": task["revision"], "operation_id": f"original_history_{ordinal}"}) for ordinal in (1, 2)]
        page.goto(f"{live_server.origin}/?project={pid}&view=tasks")
        history = page.locator("#execution-tasks details")
        history.locator("summary").click()
        expect(history.locator("[data-run-id]")).to_have_count(2)
        windows = []
        for ordinal, run in enumerate(runs, start=1):
            with page.expect_popup() as opened:
                history.locator(f"[data-run-id='{run['id']}']").get_by_role("link", name="独立窗口", exact=True).click()
            window = opened.value
            expect(window.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
            expect(window.locator("#run-status")).to_contain_text(f"第 {ordinal} 次运行")
            expect(window.locator("#run-title")).to_contain_text("原创历史候选")
            assert route(window)["run"] == run["id"]
            windows.append(window)
        windows[0].reload()
        expect(windows[0].locator("#run-status")).to_contain_text("第 1 次运行", timeout=15000)
        expect(windows[1].locator("#run-status")).to_contain_text("第 2 次运行")
        revised = page.request.put(f"{live_server.origin}/api/projects/{pid}/tasks/{task['id']}?expected_revision=1", data={
            "goal": "新版任务目标不覆盖旧运行", "requirement_refs": [{
                "requirement_id": requirement["id"], "revision": requirement["revision"], "sha256": requirement["sha256"]}],
            "allowed_write_paths": ["candidate.txt"], "dependencies": []}, headers=headers)
        assert revised.ok and revised.json()["revision"] == 2
        page.reload()
        expect(page.locator("#execution-tasks")).to_contain_text("新版任务目标不覆盖旧运行")
        windows[0].reload()
        windows[1].reload()
        for window in windows:
            expect(window.locator("#run-title")).to_contain_text("原创历史候选", timeout=15000)
            expect(window.locator("#run-title")).to_contain_text("任务 v1")
            expect(window.locator("#run-title")).not_to_contain_text("新版任务目标")
        with sqlite3.connect(live_server.data_dir / "eeas.sqlite3") as connection:
            assert connection.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 2
            assert connection.execute("SELECT COUNT(*) FROM runner_jobs").fetchone()[0] == 2
        browser.close()


def test_late_restore_and_retry_cannot_override_explicit_navigation(live_server):
    """Hold real requests to reproduce response ordering; every response still comes from the API."""
    import time
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.goto(live_server.origin)
        open_project_form(page)
        page.locator("#name").fill("原创导航顺序夹具")
        page.get_by_role("button", name="创建项目", exact=True).click()
        project = page.locator("#projects").get_by_role("button", name="原创导航顺序夹具", exact=True)
        project.wait_for()
        pid = project.get_attribute("data-project-id")
        detail_url = f"{live_server.origin}/api/projects/{pid}"
        held = []
        def wait_for_held():
            deadline = time.monotonic() + 8
            while not held and time.monotonic() < deadline:
                page.wait_for_timeout(10)
            assert held, "real project request was not reached"
        page.route(detail_url, lambda request: held.append(request))
        page.goto(f"{live_server.origin}/?project={pid}&view=requirements")
        wait_for_held()
        view(page, "projects")
        held.pop().continue_()
        page.unroute(detail_url)
        page.wait_for_load_state("networkidle")
        expect(page.locator("#project-zone")).to_be_visible()
        expect(page.locator("#project-context")).to_have_text("原创导航顺序夹具")
        assert route(page)["view"] == "projects"
        page.route(detail_url, lambda request: held.append(request))
        page.locator("#retry-connection").click()
        wait_for_held()
        view(page, "requirements")
        held.pop().continue_()
        page.unroute(detail_url)
        page.wait_for_load_state("networkidle")
        expect(page.locator("#requirements-zone")).to_be_visible()
        assert route(page)["view"] == "requirements"
        actual = page.request.get(detail_url)
        assert actual.status == 200 and actual.json()["name"] == "原创导航顺序夹具"
        browser.close()
