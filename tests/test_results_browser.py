import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright, expect
from test_web import launch_browser, open_project_form


def test_six_zones_actual_budget_logs_download_refresh_and_error(live_server, tmp_path):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_server.origin)
        expect(page.locator("[data-zone]")).to_have_count(8)
        expect(page.locator("#library-zone")).to_have_count(1)
        expect(page.locator("#settings-zone")).to_have_count(1)
        page.locator("nav [data-view='capabilities']").click()
        expect(page.locator("#capability-summary")).to_contain_text("LIVE：BLOCKED")
        expect(page.locator("#capability-summary")).to_contain_text("Python 3.10")
        page.locator("nav [data-view='results']").click()
        expect(page.locator("#results-empty")).to_be_visible()
        page.locator("nav [data-view='projects']").click()
        open_project_form(page)
        page.locator("#name").fill("原创 六区验收")
        page.get_by_role("button", name="创建项目", exact=True).click()
        page.locator("#projects").get_by_role("button", name="原创 六区验收", exact=True).click()
        page.locator("nav [data-view='tasks']").click()
        expect(page.locator("#execution-tasks")).to_contain_text("尚无执行任务")
        page.locator("nav [data-view='requirements']").click()
        page.locator("#requirement-title").fill("原创中文需求")
        page.locator("#requirement-text").fill("产生冻结的候选文本")
        page.locator("#acceptance-text").fill("独立检查冻结内容")
        page.locator("#confirmed").check()
        page.get_by_role("button", name="保存需求新修订", exact=True).click()
        expect(page.locator("#requirement-state")).to_contain_text("已保存 v1")
        page.locator("nav [data-view='tasks']").click()
        page.locator("#task-goal").fill("原创 中文 候选")
        page.locator("#task-paths").fill("../escape.txt")
        page.get_by_role("button", name="创建执行任务", exact=True).click()
        expect(page.locator("#error")).to_contain_text("traversal")
        expect(page.locator("#execution-tasks")).to_contain_text("尚无执行任务")
        page.get_by_role("button", name="重试连接", exact=True).click()
        expect(page.locator("#error")).to_have_text("")
        # Restore the saved requirement after a real project refresh.
        page.locator("nav [data-view='requirements']").click()
        page.locator("#requirements button").first.click()
        page.locator("nav [data-view='tasks']").click()
        page.locator("#task-paths").fill("中文 结果.txt")
        page.locator("#task-steps").fill("4")
        page.locator("#task-repairs").fill("1")
        page.get_by_role("button", name="创建执行任务", exact=True).click()
        page.locator("#execution-tasks").get_by_role("button", name="启动任务", exact=True).click()
        expect(page.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
        expect(page.locator("#run-budget")).to_contain_text("4 步 · 最多 1 次修复")
        expect(page.locator("#run-budget")).to_contain_text("费用：未知")
        page.get_by_role("button", name="读取运行日志", exact=True).click()
        expect(page.locator("#run-logs")).to_contain_text("original-candidate-written")
        page.get_by_role("button", name="独立验收原创夹具", exact=True).click()
        expect(page.locator("#verification-result")).to_contain_text("PASSED")
        with page.expect_download() as artifact:
            page.locator("#run-artifacts").get_by_role("link", name="中文 结果.txt", exact=True).click()
        assert Path(artifact.value.path()).read_text(encoding="utf-8") == "原创 中文 候选\n"
        with page.expect_download() as proof:
            page.get_by_role("link", name="下载本次证据 JSON", exact=True).click()
        assert Path(proof.value.path()).is_file()
        page.reload()
        expect(page.locator("#verification-result")).to_contain_text("PASSED", timeout=15000)
        expect(page.locator("#verification-result")).to_contain_text("CURRENT")
        expect(page.locator("#run-artifacts")).to_contain_text("SHA256")
        query = parse_qs(urlparse(page.url).query)
        backend = page.request.get(f"{live_server.origin}/api/projects/{query['project'][0]}/runs/{query['run'][0]}").json()
        assert backend["verification_status"] == "PASSED" and backend["execution_status"] == "RETURNED"
        if os.environ.get("EEAS_EVIDENCE_DIR"):
            page.screenshot(path=str(Path(os.environ["EEAS_EVIDENCE_DIR"]) / "six-zones.png"), full_page=True)
        assert not errors
        browser.close()
