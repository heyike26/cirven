"""Real browser rejects unconfigured native review without inventing a session or changing formal evidence."""
import os
import json
from pathlib import Path
import socket
import sqlite3
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from apps.api.config import Settings
from apps.api.main import create_app
from packages.adapters import claude_sdk
from conftest import LiveServer
from test_claude_sessions import FIXTURE_ENV, NativeFactory
from test_web import launch_browser, open_project_form


@pytest.fixture
def native_unconfigured_server(tmp_path, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "EEAS_MODEL_ID", "EEAS_LIVE_MAX_COST_USD"):
        monkeypatch.setenv(key, "")
    server = LiveServer(tmp_path)
    try:
        yield server.start()
    finally:
        server.stop()


@pytest.fixture
def native_original_wire_server(tmp_path, monkeypatch):
    """Actual HTTP app and SDK client; only its external native JSON transport is original test data."""
    for key, value in FIXTURE_ENV.items():
        monkeypatch.setenv(key, value)
    factory = NativeFactory([.002, .003])
    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", factory)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = Settings(tmp_path, port=port, worker_enabled=True)
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, name="original-native-http-fixture", daemon=True)
    thread.start()
    try:
        with httpx.Client(trust_env=False) as client:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    if client.get(settings.origin + "/health", timeout=.2).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.02)
            else:
                pytest.fail("Original native API fixture did not start")
        yield SimpleNamespace(origin=settings.origin, data_dir=tmp_path, factory=factory)
    finally:
        server.should_exit = True
        thread.join(10)
        assert not thread.is_alive(), "Actual native API owner did not shut down"


def test_run_window_native_readonly_preflight_is_visible_and_has_no_fake_execution(native_unconfigured_server):
    server = native_unconfigured_server
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.goto(server.origin)
        open_project_form(page)
        page.locator("#name").fill("原创只读审阅预检")
        page.get_by_role("button", name="创建项目", exact=True).click()
        page.locator("#projects").get_by_role("button", name="原创只读审阅预检", exact=True).click()
        page.locator("nav [data-view='requirements']").click()
        page.locator("#requirement-title").fill("原创审阅边界")
        page.locator("#requirement-text").fill("原生建议与正式执行、独立验收分开记录")
        page.locator("#acceptance-text").fill("预检拒绝时不生成伪会话或改变已通过的原创验收")
        page.locator("#confirmed").check()
        page.get_by_role("button", name="保存需求新修订", exact=True).click()
        expect(page.locator("#requirement-state")).to_contain_text("已保存 v1")
        page.locator("nav [data-view='tasks']").click()
        page.locator("#task-goal").fill("original-native-readonly-browser")
        page.get_by_role("button", name="创建执行任务", exact=True).click()
        page.locator("#execution-tasks").get_by_role("button", name="启动任务", exact=True).click()
        expect(page.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
        page.locator("#verify-run").click()
        expect(page.locator("#run-status")).to_contain_text("验收通过", timeout=15000)
        route = parse_qs(urlparse(page.url).query)
        pid, run_id = route["project"][0], route["run"][0]
        before = page.request.get(f"{server.origin}/api/projects/{pid}/runs/{run_id}").json()
        page.locator("#native-review-panel summary").click()
        page.locator("#start-native-review").click()
        expect(page.locator("#native-review-status")).to_contain_text("BLOCKED：NO_CREDENTIALS")
        expect(page.locator("#native-review-status")).to_contain_text("MODEL_ID_NOT_CONFIGURED")
        expect(page.locator("#native-review-identity")).to_have_text("")
        expect(page.locator("#native-review-controls")).to_be_hidden()
        expect(page.locator("#run-status")).to_contain_text("验收通过")
        after = page.request.get(f"{server.origin}/api/projects/{pid}/runs/{run_id}").json()
        assert after == before
        with sqlite3.connect(server.data_dir / "eeas.sqlite3") as db:
            assert db.execute("SELECT COUNT(*) FROM task_native_reviews").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM agent_executions").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM runner_jobs").fetchone()[0] == 1
        if os.environ.get("EEAS_EVIDENCE_DIR"):
            page.evaluate("scrollTo(0, 0)")
            page.screenshot(path=str(Path(os.environ["EEAS_EVIDENCE_DIR"]) / "native-readonly-blocked.png"), full_page=True)
        browser.close()


def test_body_run_link_opens_existing_native_review_and_tracks_session_switch(native_original_wire_server):
    server = native_original_wire_server
    evidence = Path(os.environ["EEAS_EVIDENCE_DIR"]) if os.environ.get("EEAS_EVIDENCE_DIR") else None
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(server.origin)
        open_project_form(page)
        page.locator("#name").fill("原创原生正文窗口")
        page.get_by_role("button", name="创建项目", exact=True).click()
        page.locator("#projects").get_by_role("button", name="原创原生正文窗口", exact=True).click()
        page.locator("nav [data-view='requirements']").click()
        page.locator("#requirement-title").fill("原创审阅窗口绑定")
        page.locator("#requirement-text").fill("正文新窗口恢复实际原生审阅与会话，不重新收费提交")
        page.locator("#acceptance-text").fill("引用、事件和导出属于指定项目和运行；切换运行不泄漏旧审阅")
        page.locator("#confirmed").check()
        page.get_by_role("button", name="保存需求新修订", exact=True).click()
        expect(page.locator("#requirement-state")).to_contain_text("已保存 v1")
        page.locator("nav [data-view='tasks']").click()
        page.locator("#task-goal").fill("original-native-popup-first")
        page.get_by_role("button", name="创建执行任务", exact=True).click()
        page.locator("#execution-tasks").get_by_role("button", name="启动任务", exact=True).click()
        expect(page.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
        page.locator("#verify-run").click()
        expect(page.locator("#run-status")).to_contain_text("验收通过", timeout=15000)
        route = parse_qs(urlparse(page.url).query)
        pid, run_id = route["project"][0], route["run"][0]
        formal_before = page.request.get(f"{server.origin}/api/projects/{pid}/runs/{run_id}").json()
        page.locator("#native-review-panel summary").click()
        page.locator("#start-native-review").click()
        expect(page.locator("#native-review-status")).to_contain_text("RETURNED", timeout=20000)
        original_ref = parse_qs(urlparse(page.url).query)["review"][0]
        original_review = page.request.get(f"{server.origin}/api/projects/{pid}/native-reviews/{original_ref}").json()
        assert original_review["native_execution"]["session_id"]
        assert len(server.factory.transports) == 1
        original_events = page.request.get(f"{server.origin}/api/projects/{pid}/native-reviews/{original_ref}/events").json()
        with page.expect_popup() as opened:
            page.locator("#open-run-window").click()
        popup = opened.value
        popup.on("pageerror", lambda error: errors.append(str(error)))
        popup.wait_for_load_state()
        popup.locator("#native-review-panel summary").click()
        if evidence:
            page.screenshot(path=str(evidence / "native-body-link-source.png"), full_page=True)
            popup.screenshot(path=str(evidence / "native-body-link-popup.png"), full_page=True)
            with (evidence / "native-body-link-observation.json").open("x", encoding="utf-8") as output:
                json.dump({"source_url": page.url, "body_href": page.locator("#open-run-window").get_attribute("href"),
                    "popup_url": popup.url, "original_review": original_review, "original_events": original_events,
                    "actual_native_calls": len(server.factory.transports)}, output, ensure_ascii=False, indent=2)
        expect(popup.locator("#native-review-identity")).to_contain_text(original_ref)
        expect(popup.locator("#native-review-status")).to_contain_text("RETURNED")
        expect(popup.locator("#native-review-events")).to_contain_text("ResultMessage")
        assert parse_qs(urlparse(popup.url).query)["review"] == [original_ref]
        assert popup.evaluate("window.opener === null")
        assert popup.request.get(f"{server.origin}/api/projects/{pid}/native-reviews/{original_ref}/events").json() == original_events
        popup.reload()
        popup.locator("#native-review-panel summary").click()
        expect(popup.locator("#native-review-identity")).to_contain_text(original_ref)
        expect(popup.locator("#native-review-events")).to_contain_text("ResultMessage")
        assert parse_qs(urlparse(popup.locator("#open-run-window").get_attribute("href")).query)["review"] == [original_ref]
        assert len(server.factory.transports) == 1

        page.locator("#native-followup-prompt").fill("Original readonly fork to check child-window binding")
        pending_fork = {}
        def observe_actual_fork_request(route):
            pending_fork.update(page.evaluate("""() => ({status: document.querySelector('#native-review-status').textContent,
                identity: document.querySelector('#native-review-identity').textContent,
                fork_disabled: document.querySelector('#fork-native-review').disabled,
                resume_disabled: document.querySelector('#resume-native-review').disabled,
                prompt_disabled: document.querySelector('#native-followup-prompt').disabled})"""))
            route.fulfill(response=route.fetch())
        fork_url = f"{server.origin}/api/projects/{pid}/native-reviews/{original_ref}/fork"
        page.route(fork_url, observe_actual_fork_request)
        with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == f"/api/projects/{pid}/native-reviews/{original_ref}/fork") as fork_admitted:
            page.locator("#fork-native-review").click()
        assert fork_admitted.value.status == 202
        accepted_fork = fork_admitted.value.json()
        page.unroute(fork_url, observe_actual_fork_request)
        assert "等待服务器批准" in pending_fork["status"] and "RETURNED" not in pending_fork["status"]
        assert original_ref in pending_fork["identity"]
        assert pending_fork["fork_disabled"] and pending_fork["resume_disabled"] and pending_fork["prompt_disabled"]
        if evidence:
            (evidence / "native-fork-pending.json").write_text(json.dumps({"pending": pending_fork, "http_status": fork_admitted.value.status, "accepted_reference": accepted_fork["admission_id"]}, ensure_ascii=False, indent=2), encoding="utf-8")
        expect(page.locator("#native-review-identity")).to_contain_text(accepted_fork["admission_id"])
        expect(page.locator("#native-review-status")).to_contain_text("RETURNED", timeout=20000)
        expect(page.locator("#native-review-identity")).not_to_contain_text("审阅引用 " + original_ref)
        expect(page.locator("#native-review-status")).to_contain_text("RETURNED", timeout=20000)
        child_ref = parse_qs(urlparse(page.url).query)["review"][0]
        assert child_ref != original_ref
        child_review = page.request.get(f"{server.origin}/api/projects/{pid}/native-reviews/{child_ref}").json()
        assert child_review["native_execution_id"] != original_review["native_execution_id"]
        with page.expect_popup() as child_opened:
            page.locator("#open-run-window").click()
        child_popup = child_opened.value
        child_popup.wait_for_load_state()
        child_popup.locator("#native-review-panel summary").click()
        expect(child_popup.locator("#native-review-identity")).to_contain_text(child_ref)
        expect(child_popup.locator("#native-review-status")).to_contain_text("RETURNED")
        expect(popup.locator("#native-review-identity")).to_contain_text(original_ref)
        assert len(server.factory.transports) == 2

        page.locator("nav [data-view='tasks']").click()
        page.locator("#task-goal").fill("original-native-popup-second")
        page.get_by_role("button", name="创建执行任务", exact=True).click()
        second = page.locator("#execution-tasks li").filter(has=page.get_by_role("heading", name="original-native-popup-second", exact=True))
        second.get_by_role("button", name="启动任务", exact=True).click()
        expect(page.locator("#run-title")).to_contain_text("original-native-popup-second")
        expect(page.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
        second_route = parse_qs(urlparse(page.url).query)
        assert second_route["run"] != [run_id] and "review" not in second_route
        assert "review" not in parse_qs(urlparse(page.locator("#open-run-window").get_attribute("href")).query)
        with page.expect_popup() as clear_opened:
            page.locator("#open-run-window").click()
        clear_popup = clear_opened.value
        clear_popup.wait_for_load_state()
        clear_popup.locator("#native-review-panel summary").click()
        expect(clear_popup.locator("#native-review-identity")).to_have_text("")
        expect(clear_popup.locator("#native-review-status")).to_have_text("尚无原生审阅")
        assert "review" not in parse_qs(urlparse(clear_popup.url).query)
        assert len(server.factory.transports) == 2
        page.locator("nav [data-view='tasks']").click()
        first = page.locator("#execution-tasks li").filter(has=page.get_by_role("heading", name="original-native-popup-first", exact=True))
        first.get_by_role("button", name="查看最近运行", exact=True).click()
        expect(page.locator("#native-review-identity")).to_contain_text(child_ref)
        assert parse_qs(urlparse(page.locator("#open-run-window").get_attribute("href")).query)["review"] == [child_ref]
        page.locator("nav [data-view='projects']").click()
        page.locator("#projects").get_by_role("button", name="原创原生正文窗口", exact=True).click()
        expect(page.locator("#run-panel")).to_be_hidden()
        cleared_route = parse_qs(urlparse(page.url).query)
        cleared_link = parse_qs(urlparse(page.locator("#open-run-window").get_attribute("href")).query)
        assert cleared_route["project"] == cleared_link["project"] == [pid]
        assert not {"run", "review"}.intersection(cleared_route)
        assert not {"run", "review"}.intersection(cleared_link)
        expect(popup.locator("#native-review-identity")).to_contain_text(original_ref)
        expect(child_popup.locator("#native-review-identity")).to_contain_text(child_ref)
        assert len(server.factory.transports) == 2
        assert page.request.get(f"{server.origin}/api/projects/{pid}/runs/{run_id}").json() == formal_before
        with sqlite3.connect(server.data_dir / "eeas.sqlite3") as db:
            assert db.execute("SELECT COUNT(*) FROM task_native_reviews").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM agent_executions").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 2
        if evidence:
            child_popup.screenshot(path=str(evidence / "native-child-window.png"), full_page=True)
            clear_popup.screenshot(path=str(evidence / "native-cleared-window.png"), full_page=True)
        assert not errors
        browser.close()
