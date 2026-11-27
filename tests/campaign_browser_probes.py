"""Explicit source-bound browser nodes; never default discovery or company qualification.

Run exactly one frozen binding with record_slice and scripts.public_campaign.
Public material is data; the generated candidate and protected verifier are
original EEAS Mock fixtures, not completed upstream engineering.
"""
from __future__ import annotations

import base64
import io
import json
import sqlite3
from urllib.parse import parse_qs, quote, urlparse

import pytest
from PIL import Image, ImageChops
from playwright.sync_api import expect, sync_playwright

from campaign_probes import Evidence, load_binding, sha
from campaign_workflow_probes import source_lines
from conftest import LiveServer
from test_web import launch_browser
from test_desktop_design import project
from test_ui_hero_motion import media_change, settled


class Desk:
    def __init__(self, case_id, tmp_path):
        self.captured = load_binding(case_id)
        self.evidence = Evidence(self.captured)
        self.lines = source_lines(self.captured, self.evidence)
        self.tail = self.lines[-1]
        self.tmp_path, self.calls, self.errors, self.external = tmp_path, [], [], []
        self.version = "NOT_STARTED"
        self.server = LiveServer(tmp_path / "real-data")
        # No inherited credential, endpoint, MCP or compatibility approval.
        for name in list(self.server.env):
            if name.startswith(("ANTHROPIC_", "EEAS_LIVE_", "EEAS_IMAGE_", "EEAS_SEARCH_")) or name in {
                "EEAS_CLAUDE_SETTINGS_PATH", "CLAUDE_CODE_OAUTH_TOKEN"}:
                self.server.env.pop(name, None)
        self.server.env.update(EEAS_CLAUDE_SETTINGS_PATH="", EEAS_SEARCH_MCP_CONFIG_PATH="",
                               CLAUDE_CODE_OAUTH_TOKEN="")
        self.completed = False
        self.evidence.save("actual-source-consumption.json", {
            "parentRawSHA": sha(self.captured["raw"]), "source_lines": self.lines,
            "candidate_source_kind": "synthetic", "company_model": "NOT_RUN",
            "upstream_engineering": "NOT_RUN", "public_material_modified": False})

    def attach(self, page):
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on("request", lambda request: self.external.append(request.url)
                if request.url.startswith(("http://", "https://")) and not request.url.startswith(self.server.origin + "/") else None)
        page.on("response", self.response)
        return page

    def response(self, response):
        if "/api/" not in response.url or "/events?" in response.url:
            return
        item = {"method": response.request.method, "url": response.url, "status": response.status}
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            item["response"] = response.json()
        self.calls.append(item)

    def api(self, method, route, status=200, **kwargs):
        response = self.page.request.fetch(self.server.origin + route, method=method, **kwargs)
        raw = response.body()
        try:
            value = response.json()
        except ValueError:
            value = {"sha256": sha(raw), "byte_length": len(raw)}
        self.calls.append({"method": method, "url": response.url, "status": response.status,
                           "request": kwargs.get("data"), "response": value})
        assert response.status == status, response.text()
        return value

    def view(self, name, page=None):
        page = page or self.page
        nav = page.locator(f"nav [data-view='{name}']")
        if page.locator("#menu-toggle").is_visible() and page.locator("#menu-toggle").get_attribute("aria-expanded") != "true":
            page.locator("#menu-toggle").click()
        nav.click()

    def shot(self, name, page=None):
        page = page or self.page
        page.evaluate("""async () => { await document.fonts.ready;
          await Promise.all(document.getAnimations().filter(a => Number.isFinite(a.effect?.getComputedTiming().endTime))
            .map(a => a.finished.catch(() => {}))); }""")
        self.evidence.save(name + ".png", page.screenshot(full_page=True))

    def import_source(self):
        self.page.goto(self.server.origin)
        self.page.wait_for_load_state("networkidle")
        self.pid = project(self.page, "Source-bound " + self.captured["source"]["project_id"])
        self.base = f"/api/projects/{self.pid}"
        self.upload()
        docs = self.api("GET", self.base + "/documents")["documents"]
        assert len(docs) == 1
        self.doc = docs[0]
        self.doc_route = self.base + "/documents/" + self.doc["id"]
        assert self.doc["revision"] == 1 and self.doc["sha256"] == sha(self.captured["raw"])
        original = self.page.request.get(self.server.origin + self.doc_route + "/download?revision=1")
        assert original.status == 200 and original.body() == self.captured["raw"]
        parsed = self.api("GET", self.doc_route + "?revision=1")
        assert parsed["content"] == self.captured["text"]
        self.evidence.save("actual-original.bin", original.body())
        self.evidence.save("actual-document.json", parsed)
        self.evidence.save("source-declaration.json", self.captured["source"])

    def upload(self, *, revision=False):
        if revision:
            self.page.locator("#documents li").filter(has_text=self.captured["name"] + " · v1").get_by_role(
                "button", name="上传新修订", exact=True).click()
            expect(self.page.locator("#document-target")).to_contain_text("确认当前 v1")
        else:
            self.page.locator("#toolbar-upload").click()
        expect(self.page.locator("#upload-drawer")).to_be_visible()
        self.page.locator("#document-file").set_input_files({"name": self.captured["name"],
            "mimeType": "text/markdown", "buffer": self.captured["raw"]})
        self.page.locator("#document-source").fill(
            f"campaign:{self.captured['source']['source_kind']}:{self.captured['source']['project_id']}:{sha(self.captured['raw'])}")
        self.page.locator("#document-form").get_by_role("button", name="导入资料", exact=True).click()
        expect(self.page.locator("#upload-drawer")).to_be_hidden(timeout=15000)
        expect(self.page.locator("#reader-title")).to_have_text(self.captured["name"])

    def locate_tail(self):
        self.view("library")
        # A genuine substring of a complete original line, never a wrapper label.
        keyword = self.tail["content"][:min(120, len(self.tail["content"]))]
        assert keyword and self.captured["text"][self.tail["char_start"]:].startswith(keyword)
        self.page.locator("#keyword").fill(keyword)
        self.page.locator("#search-form button[type=submit]").click()
        results = self.page.locator("#search-results")
        expect(results).to_contain_text(keyword)
        while not results.locator("li").filter(has_text=f"第 {self.tail['line']} 行：").count():
            more = results.locator(".search-more")
            assert more.count(), "Actual original tail was absent from all search pages"
            before = results.locator("li").count()
            more.click()
            expect(results.locator("li")).not_to_have_count(before)
        item = results.locator("li").filter(has_text=f"第 {self.tail['line']} 行：")
        expect(item).to_have_count(1)
        item.get_by_role("button", name="查看命中原文", exact=True).click()
        expect(self.page.locator("#reader-meta")).to_contain_text("命中原文位置")
        expect(self.page.locator("#document-content")).to_contain_text(keyword)
        expect(self.page.locator("#reader-version")).to_have_text("修订 v1")
        expect(self.page.locator("#reader-identity")).to_contain_text(self.doc["sha256"])
        return item

    def manual_requirement(self):
        item = self.locate_tail()
        item.get_by_role("button", name="引用该来源", exact=True).click()
        self.view("requirements")
        expect(self.page.locator("#source-refs")).to_contain_text(f"行 {self.tail['line']}—{self.tail['line']}")
        self.page.locator("#requirement-title").fill("明确人工核对的原文来源")
        self.page.locator("#requirement-text").fill(self.tail["content"])
        self.page.locator("#acceptance-text").fill("原创候选严格等于冻结目标；不代表上游工程完成")
        self.page.locator("#confirmed").uncheck()
        self.page.get_by_role("button", name="保存需求新修订", exact=True).click()
        expect(self.page.locator("#requirement-state")).to_contain_text("已保存 v1")
        draft = self.api("GET", self.base + "/requirements")["requirements"][0]
        assert not draft["payload"]["confirmed"] and draft["source_status"] == "CURRENT"
        self.view("tasks")
        expect(self.page.locator("#task-source-status")).to_have_text("需求待确认")
        self.view("requirements")
        self.page.locator("#confirmed").check()  # Explicit human action after reading original.
        self.page.get_by_role("button", name="保存需求新修订", exact=True).click()
        expect(self.page.locator("#requirement-state")).to_contain_text("已保存 v2")
        row = self.api("GET", self.base + "/requirements")["requirements"][0]
        expected = {"document_id": self.doc["id"], "revision": 1, "sha256": self.doc["sha256"],
                    "line_start": self.tail["line"], "line_end": self.tail["line"]}
        assert row["revision"] == 2 and row["payload"]["confirmed"]
        assert row["payload"]["requirements"][0]["source_refs"] == [expected]
        return row

    def finish(self):
        assert not self.errors and not self.external
        self.completed = True

    def persist(self):
        self.server.stop()
        database = self.server.data_dir / "eeas.sqlite3"
        if not database.is_file():
            return
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            rows = {name: [{key: ({"sqlite_type": "BLOB", "raw_base64": base64.b64encode(value).decode(),
                "byte_length": len(value), "sha256": sha(value)} if isinstance(value, bytes) else value)
                for key, value in dict(row).items()} for row in connection.execute('SELECT * FROM "' + name + '"')]
                for name in tables if not name.startswith("sqlite_")}
            target = self.tmp_path / "browser-export.sqlite3"
            with sqlite3.connect(target) as exported:
                connection.backup(exported)
        self.evidence.save("actual-store.sqlite3", target.read_bytes())
        self.evidence.save("actual-sqlite-rows.json", rows)
        self.evidence.save("actual-ui-api-receipts.json", self.calls)
        self.evidence.save("actual-browser-environment.json", {"page_errors": self.errors,
            "external_requests": self.external, "browser": self.version, "origin": self.server.origin,
            "model_calls": 0, "native_execution_count": len(rows["agent_executions"])})
        assert not rows["agent_executions"] and not rows["task_native_reviews"]
        if self.completed:
            self.evidence.save("campaign-observation.json", {"schema": "eeas.campaign-observation.v1",
                "case_id": self.captured["binding"]["case_id"], "binding_sha256": sha(self.captured["binding_raw"]),
                "source_sha256": sha(self.captured["raw"]), "source_kind": self.captured["source"]["source_kind"],
                "runtime_kind": "mock" if rows["runner_jobs"] else "none", "model_calls": 0,
                "original_fixture_runner_jobs": len(rows["runner_jobs"]), "candidate_source_kind": "synthetic",
                "origin_verification": "DECLARED_NOT_INDEPENDENTLY_VERIFIED", "company_model": "NOT_RUN",
                "upstream_engineering": "NOT_RUN", "windows_native": "NOT_VALIDATED", "os_sandbox": "NOT_VALIDATED",
                "actual_receipts": list(self.evidence.receipts)})


@pytest.fixture
def desk(request, tmp_path):
    owner = Desk(request.node.name.removeprefix("test_").replace("_", "-"), tmp_path)
    try:
        owner.server.start()
        with sync_playwright() as playwright:
            browser = launch_browser(playwright)
            owner.version = browser.version
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="no-preference")
            owner.context, owner.browser = context, browser
            owner.page = owner.attach(context.new_page())
            try:
                owner.import_source()
                yield owner
            finally:
                owner.shot("actual-browser-final")
                context.close()
                browser.close()
    finally:
        owner.persist()


def test_source_reader(desk):
    d = desk
    assert len(d.captured["text"]) >= 70000, "This reader acceptance requires actual 70k+ material; no source padding"
    offset, chunks, pages = 0, [], []
    while True:
        page = d.api("GET", d.doc_route + f"/chunks?revision=1&offset={offset}&limit=20")
        assert page["document_id"] == d.doc["id"] and page["revision"] == 1 and page["sha256"] == d.doc["sha256"]
        pages.append(page)
        for chunk in page["chunks"]:
            assert chunk["content"] == d.captured["text"][chunk["char_start"]:chunk["char_end"]]
            assert chunk["sha256"] == sha(chunk["content"].encode())
        chunks += page["chunks"]
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert "".join(chunk["content"] for chunk in chunks) == d.captured["text"]
    structure = d.api("GET", d.doc_route + "/structure?revision=1")
    assert structure["format"] == d.doc["format"] and structure["sha256"] == d.doc["sha256"]
    assert isinstance(structure["graph"], dict)
    assert not structure["graph"].get("nodes"), "This text-only source must not acquire a fabricated diagram"
    expect(d.page.locator("#document-structure")).to_be_hidden()
    expect(d.page.locator("#document-toc button")).not_to_have_count(0)
    expect(d.page.locator("#reader-last")).to_be_enabled()
    d.page.locator("#reader-last").click()
    expect(d.page.locator("#reader-next")).to_be_disabled()
    expect(d.page.locator("#document-content")).to_contain_text(d.tail["content"])
    d.page.locator(".reader-paper").evaluate("el => {el.scrollTop = el.scrollHeight}")
    d.shot("actual-original-tail-page")
    requirement = d.manual_requirement()
    d.evidence.save("actual-reader-pagination.json", pages)
    d.evidence.save("actual-reader-structure.json", structure)
    d.evidence.save("actual-tail-citation-requirement.json", requirement)
    d.page.reload()
    d.view("requirements")
    expect(d.page.locator("#requirements")).to_contain_text("v2")
    assert d.api("GET", d.doc_route + "?revision=1")["sha256"] == d.doc["sha256"]
    d.finish()


def test_ui_workspace(desk):
    d = desk
    # Read a selected original context; Mock may honestly reject non-REQ public prose.
    d.page.locator("#reader-extract").click()
    expect(d.page.locator("#extract-search-enabled")).not_to_be_checked()
    expect(d.page.locator("#extract-include-images")).not_to_be_checked()
    d.page.locator("#extract-select-last").click()
    d.page.locator("#extract-preview-context").click()
    expect(d.page.locator("#extract-context-receipt")).to_contain_text("上下文 SHA256")
    with d.page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/planning/extract")) as extracted:
        d.page.locator("#extraction-form button[type=submit]").click()
    response = extracted.value
    assert response.status in (200, 422), response.text()
    if response.status == 422:
        expect(d.page.locator("#extraction-error")).to_contain_text("提取被拒绝")
        d.shot("actual-mock-prose-extraction-rejection")
        d.page.keyboard.press("Escape")
    else:
        expect(d.page.locator("#confirmed")).not_to_be_checked()
    assert d.api("GET", d.base + "/requirements")["requirements"] == []
    # The human fixture deliberately writes its own requirement after exact original citation.
    row = d.manual_requirement()
    d.page.locator("#plan-requirements").click()
    expect(d.page.locator("#planning-tasks")).to_contain_text("规划草稿")
    # An explicit manual UNSOURCED item is allowed by the real gate, and described honestly.
    d.page.locator("#requirement-new").click()
    d.page.locator("#clear-sources").click()
    d.page.locator("#requirement-title").fill("人工无来源对照项（未创建任务）")
    d.page.locator("#requirement-text").fill(d.lines[0]["content"])
    d.page.locator("#acceptance-text").fill("人工对照，未声明来源已验证")
    d.page.locator("#confirmed").check()
    d.page.get_by_role("button", name="保存需求新修订", exact=True).click()
    expect(d.page.locator("#requirement-state")).to_contain_text("含无来源需求")
    d.view("tasks")
    expect(d.page.locator("#task-source-status")).to_have_text("已确认 · 可创建")
    expect(d.page.locator("#task-source-note")).to_contain_text("含无来源需求")
    d.view("requirements")
    d.page.locator("#requirements button").filter(has_text=row["payload"]["title"]).click()
    d.view("tasks")
    expect(d.page.locator("#task-source-status")).to_have_text("已确认 · 可创建")
    geometry = d.page.locator("#task-form").evaluate("e=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height})")
    assert geometry["width"] >= 800 and geometry["height"] <= 510
    d.page.reload()
    expect(d.page.locator("#task-source-status")).to_have_text("待选择需求")
    d.page.locator("#task-select-requirement").click()
    d.page.locator("#requirements button").filter(has_text=row["payload"]["title"]).click()
    d.view("tasks")
    goal = f"ORIGINAL_MOCK, upstream engineering NOT_RUN\nparentRawSHA={sha(d.captured['raw'])}\nsourceLine[{d.tail['line']}]={d.tail['content']}"
    d.page.locator("#task-goal").fill(goal)
    assert d.page.locator("#task-goal").input_value() == goal
    d.page.locator("#task-paths").fill("来源 候选.txt")
    d.page.locator("#task-steps").fill("4")
    d.page.locator("#task-repairs").fill("1")
    d.page.locator("#approval-required").check()
    with d.page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/tasks")) as created:
        d.page.locator("#task-form button[type=submit]").click()
    task = created.value.json()
    assert task["spec"]["goal"] == goal
    assert created.value.status == 201 and task["spec"]["requirement_refs"] == [
        {"requirement_id": row["id"], "revision": 2, "sha256": row["sha256"]}]
    d.page.locator("#execution-tasks [data-task-id]").get_by_role("button", name="启动任务", exact=True).click()
    expect(d.page.locator("#run-status")).to_contain_text("等待审批", timeout=15000)
    run_id = parse_qs(urlparse(d.page.url).query)["run"][0]
    run_route = d.base + "/runs/" + run_id
    run = d.api("GET", run_route)
    frozen = json.loads(run["frozen_inputs"])
    assert frozen["spec"] == task["spec"] and run["verification_status"] == "NOT_RUN"
    assert base64.b64decode(frozen["documents"][0]["raw_base64"]) == d.captured["raw"]
    with sqlite3.connect(d.server.data_dir / "eeas.sqlite3") as c:
        assert c.execute("SELECT count(*) FROM runner_jobs").fetchone()[0] == 0
    d.view("approvals")
    expect(d.page.locator("#approvals")).to_contain_text("来源 候选.txt")
    d.page.get_by_role("button", name="批准本次动作", exact=True).click()
    d.view("results")
    expect(d.page.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
    with d.page.expect_popup() as opened:
        d.page.locator("#open-run-window").click()
    popup = d.attach(opened.value)
    expect(popup.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
    expect(popup.locator("#run-title")).to_contain_text(goal)
    assert popup.evaluate("window.opener === null")
    popup.reload()
    expect(popup.locator("#run-identity")).to_contain_text(run_id)
    expect(popup.locator("#run-status")).to_contain_text("未验收")
    popup.get_by_role("button", name="查证 Runner", exact=True).click()
    expect(popup.locator("#runner-facts")).to_contain_text("FINISHED")
    popup.get_by_role("button", name="独立验收原创夹具", exact=True).click()
    expect(popup.locator("#verification-result")).to_contain_text("PASSED", timeout=15000)
    with popup.expect_download() as download:
        popup.locator("#run-artifacts").get_by_role("link", name="来源 候选.txt", exact=True).click()
    candidate = open(download.value.path(), "rb").read()
    assert candidate == (goal + "\n").encode()
    d.evidence.save("actual-source-candidate.bin", candidate)
    d.shot("actual-independent-run-window", popup)
    # Select actual frozen text; later project change must drop this scope.
    d.page.locator("#native-review-panel > summary").click()
    d.page.locator("#load-native-context").click()
    expect(d.page.locator("#native-context-select-last")).to_be_enabled()
    d.page.locator("#native-context-select-last").click()
    expect(d.page.locator("#native-context-custom")).to_be_checked()
    expect(d.page.locator("#native-search-enabled")).not_to_be_checked()
    expect(d.page.locator("#native-include-images")).not_to_be_checked()
    d.view("library")
    d.page.locator("#documents button").filter(has_text=d.captured["name"] + " · v1").click()
    d.upload(revision=True)  # Identical captured bytes; no invented upstream text.
    expect(d.page.locator("#reader-version")).to_have_text("修订 v2")
    old = d.api("GET", d.doc_route + "?revision=1")
    assert old["content"] == d.captured["text"] and old["sha256"] == d.doc["sha256"]
    d.view("requirements")
    expect(d.page.locator("#requirements")).to_contain_text("来源已变更，待复核")
    d.page.locator("#requirements button").filter(has_text=row["payload"]["title"]).click()
    d.view("tasks")
    expect(d.page.locator("#task-source-status")).to_have_text("来源待复核")
    tasks_before = d.api("GET", d.base + "/tasks")["tasks"]
    d.page.locator("#task-form button[type=submit]").click()
    expect(d.page.locator("#error")).to_contain_text("先选择一份已确认")
    assert d.api("GET", d.base + "/tasks")["tasks"] == tasks_before
    d.view("projects")
    settled(d.page)
    hero = d.page.locator(".studio-hero")
    box, controls = hero.bounding_box(), d.page.locator(".hero-actions").bounding_box()
    before = Image.open(io.BytesIO(hero.screenshot())).convert("RGB")
    d.page.mouse.move(box["x"] + box["width"] * .78, box["y"] + box["height"] * .35)
    expect(hero).to_have_attribute("data-inspecting", "true")
    settled(d.page)
    after = Image.open(io.BytesIO(hero.screenshot())).convert("RGB")
    assert ImageChops.difference(before, after).getbbox()
    assert hero.bounding_box() == box and d.page.locator(".hero-actions").bounding_box() == controls
    d.page.mouse.move(230, 80)
    settled(d.page)
    restored = Image.open(io.BytesIO(hero.screenshot())).convert("RGB")
    assert max(peak for _, peak in ImageChops.difference(before, restored).getextrema()) <= 2
    for name in ("settings", "library", "projects"):
        d.view(name)
    foreign = project(d.page, "Independent empty scope")
    assert foreign != d.pid
    expect(d.page.locator("#native-context-panel")).to_be_hidden()
    expect(d.page.locator("#native-search-enabled")).not_to_be_checked()
    expect(d.page.locator("#native-include-images")).not_to_be_checked()
    assert d.api("GET", f"/api/projects/{foreign}/tasks")["tasks"] == []
    assert parse_qs(urlparse(popup.url).query)["project"] == [d.pid]
    expect(popup.locator("#run-status")).to_contain_text("验收通过")
    d.page.locator("#toolbar-upload").click()
    expect(d.page.locator("#upload-drawer")).to_be_visible()
    d.page.keyboard.press("Escape")
    expect(d.page.locator("#upload-drawer")).to_be_hidden()
    d.page.set_viewport_size({"width": 390, "height": 540})
    d.page.locator("#menu-toggle").focus()
    d.page.keyboard.press("Enter")
    assert d.page.locator("#desktop-sidebar").evaluate("el=>el.contains(document.activeElement)")
    d.page.keyboard.press("Tab")
    assert d.page.locator("#desktop-sidebar").evaluate("el=>el.contains(document.activeElement)")
    d.page.keyboard.press("Escape")
    expect(d.page.locator("#menu-toggle")).to_be_focused()
    d.view("tasks")
    d.page.locator("#task-goal").focus()
    for _ in range(30):
        if d.page.locator("#task-form button[type=submit]").evaluate("e=>e===document.activeElement"):
            break
        d.page.keyboard.press("Tab")
    expect(d.page.locator("#task-form button[type=submit]")).to_be_focused()
    button = d.page.locator("#task-form button[type=submit]").bounding_box()
    assert 0 <= button["y"] and button["y"] + button["height"] <= 540
    assert d.page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    d.shot("actual-workspace-mobile")
    final = d.api("GET", run_route)
    assert final["verification_status"] == "PASSED" and final["execution_status"] == "RETURNED"
    with sqlite3.connect(d.server.data_dir / "eeas.sqlite3") as c:
        assert c.execute("SELECT count(*) FROM task_runs").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM runner_jobs").fetchone()[0] == 1
    d.evidence.save("actual-workspace-run.json", final)
    d.evidence.save("actual-workspace-geometry.json", geometry)
    d.finish()


def test_ui_settings(desk):
    d = desk
    d.view("settings")
    expect(d.page.locator("#settings-route-status")).to_contain_text("尚未选择")
    runtime = d.api("GET", "/api/runtime")
    assert runtime["route_configuration"]["settings_selected"] is False
    assert not runtime["route_configuration"]["authentication_verified"]
    summary = d.page.locator(".settings-provenance summary")
    summary.focus()
    d.page.keyboard.press("Enter")
    expect(d.page.locator("#settings-route-sources")).to_be_visible()
    expect(summary).to_be_focused()
    d.page.keyboard.press("Enter")
    expect(d.page.locator("#settings-route-sources")).not_to_be_visible()
    switch = d.page.locator("#reduce-motion")
    switch.focus()
    d.page.keyboard.press("Space")
    expect(switch).to_be_checked()
    expect(d.page.locator("#motion-preference-note")).to_contain_text("当前窗口开启")
    assert d.page.locator("body").evaluate("e=>e.classList.contains('reduce-motion')")
    switch.press("Space")
    expect(switch).not_to_be_checked()
    media_change(d.page, "reduce")
    expect(switch).to_be_checked()
    expect(switch).to_be_disabled()
    assert not d.page.evaluate("document.getAnimations().some(a=>a.playState==='running'&&a.effect.getTiming().duration>1)")
    media_change(d.page, "no-preference")
    expect(switch).not_to_be_checked()
    d.view("projects")
    settled(d.page)
    hero = d.page.locator(".studio-hero")
    box = hero.bounding_box()
    d.page.mouse.move(box["x"] + box["width"] * .8, box["y"] + box["height"] * .4)
    expect(hero).to_have_attribute("data-inspecting", "true")
    media_change(d.page, "reduce")
    assert not hero.get_attribute("data-inspecting")
    assert hero.evaluate("e=>getComputedStyle(e,'::before').content") == "none"
    media_change(d.page, "no-preference")
    d.view("settings")
    switch.check()
    d.view("projects")
    d.page.mouse.move(box["x"] + box["width"] * .7, box["y"] + box["height"] * .5)
    settled(d.page)
    assert hero.evaluate("e=>getComputedStyle(e,'::before').content") == "none"
    d.view("settings")
    d.server.stop()
    d.page.locator("#refresh-settings").click()
    expect(d.page.locator("#settings-refresh-state")).to_have_text("刷新失败，请重试")
    expect(d.page.locator("#refresh-settings")).to_be_enabled()
    d.shot("actual-settings-stopped-service")
    d.server.start()
    d.page.locator("#refresh-settings").click()
    expect(d.page.locator("#settings-refresh-state")).to_have_text("运行状态已更新")
    expect(d.page.locator("#error")).to_have_text("")
    # Explicitly selected invalid owned file differs from an absent route.
    config = d.tmp_path / "invalid-owned-settings.json"
    config.write_text("{invalid-original-test-json", encoding="utf-8")
    d.server.stop()
    d.server.env["EEAS_CLAUDE_SETTINGS_PATH"] = str(config)
    d.server.start()
    d.page.locator("#refresh-settings").click()
    expect(d.page.locator("#settings-refresh-state")).to_have_text("运行状态已更新")
    expect(d.page.locator("#settings-route-status")).to_contain_text("配置无效")
    invalid = d.api("GET", "/api/runtime")
    assert invalid["route_configuration"]["status"] == "INVALID"
    assert invalid["route_configuration"]["reason"] == "SETTINGS_ROUTE_INVALID_JSON"
    d.shot("actual-settings-invalid-selected-config")
    phone = d.browser.new_context(viewport={"width": 390, "height": 540}, is_mobile=True, has_touch=True)
    mobile = d.attach(phone.new_page())
    mobile.goto(d.server.origin + f"/?project={d.pid}&view=projects")
    mobile.wait_for_load_state("networkidle")
    settled(mobile)
    touch = mobile.locator(".studio-hero")
    mobile_box = touch.bounding_box()
    mobile.touchscreen.tap(mobile_box["x"] + mobile_box["width"] - 20, mobile_box["y"] + 20)
    settled(mobile)
    assert mobile.evaluate("matchMedia('(pointer:coarse)').matches")
    assert touch.evaluate("e=>getComputedStyle(e,'::before').content") == "none"
    d.view("settings", mobile)
    expect(mobile.locator("#settings-route-status")).to_contain_text("配置无效")
    assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
    d.shot("actual-settings-mobile-touch", mobile)
    phone.close()
    d.evidence.save("actual-settings-state.json", {"absent": runtime, "invalid": invalid,
        "source_sha256": d.doc["sha256"], "project_id": d.pid, "configuration_write_api": "NOT_PROVIDED"})
    d.finish()
