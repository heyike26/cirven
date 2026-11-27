"""Startup intent races using real API/SQLite, original inputs and an actual Mock runner.

Only GET delivery is held. No response body, clock, history, view class or
product function is replaced. These author nodes require Root's sole GUI run.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from conftest import ROOT
from test_tasks import inputs
from test_web import launch_browser


TABLES = ("tasks", "task_runs", "runner_jobs", "agent_executions")


def counts(data_dir):
    with sqlite3.connect((data_dir / "eeas.sqlite3").as_uri() + "?mode=ro", uri=True) as connection:
        return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}


def keep(name, value):
    target = os.environ.get("EEAS_EVIDENCE_DIR")
    if target:
        with (Path(target) / (name + ".json")).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2); stream.write("\n")


@pytest.fixture
def startup_scene(live_server):
    with httpx.Client(base_url=live_server.origin, trust_env=False, timeout=15) as api:
        bootstrap = api.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
        bootstrap.raise_for_status()
        api.headers.update({"Origin": live_server.origin, "X-EEAS-CSRF": bootstrap.json()["csrf_token"]})
        created = api.post("/api/projects", json={"name": "原创启动恢复项目", "description": "真实冻结任务的导航竞态"})
        created.raise_for_status(); project = created.json(); pid = project["id"]
        document, requirement, body = inputs(api, pid)
        assert requirement["payload"]["confirmed"] is True
        body["goal"] = "original-startup-navigation-candidate"
        task_response = api.post(f"/api/projects/{pid}/tasks", json=body)
        task_response.raise_for_status(); task = task_response.json()
        admitted = api.post(f"/api/projects/{pid}/tasks/{task['id']}/runs",
                            json={"revision": task["revision"], "operation_id": "original-startup-run-once"})
        admitted.raise_for_status(); rid = admitted.json()["id"]
        until = time.monotonic() + 20
        while True:
            response = api.get(f"/api/projects/{pid}/runs/{rid}?include_inputs=false")
            response.raise_for_status(); run = response.json()
            if run["execution_status"] == "RETURNED": break
            assert run["execution_status"] not in {"FAILED", "CANCELLED", "INTERRUPTED"}, run
            if time.monotonic() >= until: pytest.fail("Actual original Mock runner did not return: " + json.dumps(run))
            time.sleep(.02)
        assert run["verification_status"] == "NOT_RUN"
        events_response = api.get(f"/api/projects/{pid}/runs/{rid}/events?after=0")
        events_response.raise_for_status(); events = events_response.json()["events"]
        assert any(event["payload"]["type"] == "tool_result" and event["payload"]["exit_code"] == 0 for event in events)
        other_response = api.post("/api/projects", json={"name": "原创用户重新选择项目", "description": "晚到启动恢复不能覆盖"})
        other_response.raise_for_status(); other = other_response.json()
        served = api.get("/assets/app.js"); served.raise_for_status()
        source_sha = hashlib.sha256((ROOT / "apps/web/assets/app.js").read_bytes()).hexdigest()
        assert hashlib.sha256(served.content).hexdigest() == source_sha
        scene = {"api": api, "origin": live_server.origin, "project": project, "other": other,
                 "pid": pid, "rid": rid, "run_path": f"/api/projects/{pid}/runs/{rid}",
                 "data_dir": live_server.data_dir, "before": counts(live_server.data_dir),
                 "source_sha256": source_sha, "original_inputs": {"document": document, "requirement": requirement,
                     "task": task, "run": run, "actual_events": events}}
        assert scene["before"]["tasks"] == scene["before"]["task_runs"] == 1
        assert scene["before"]["agent_executions"] == 0
        yield scene


class HeldGET:
    def __init__(self, page, origin, path, first_only=True):
        self.held = []; self.requests = []; self.first_only = first_only; self.released = False
        self.pattern = re.compile(re.escape(origin + path) + r"(?:\?.*)?$")
        page.route(self.pattern, self.handle)

    def handle(self, route):
        self.requests.append({"method": route.request.method, "url": route.request.url})
        if (route.request.method == "GET" and not self.released and
                (not self.first_only or len(self.requests) == 1)):
            self.held.append(route)
        else: route.continue_()

    def release(self):
        self.released = True
        for route in self.held: route.continue_()
        self.held.clear()


def close_gates(browser, *gates):
    try:
        for gate in gates: gate.release()
    finally: browser.close()


def pump(page, predicate, label):
    """Pump actual delivery; no fake browser clock or invented response is used."""
    until = time.monotonic() + 15
    while not predicate():
        if time.monotonic() >= until: pytest.fail("Actual browser delivery not observed: " + label)
        page.wait_for_timeout(10)


def frames(page):
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(resolve))))")


def observe(page):
    errors, requests, pending = [], [], set()
    page.on("pageerror", lambda error: errors.append(str(error)))
    def requested(request):
        if "/api/" in urlsplit(request.url).path:
            requests.append({"method": request.method, "url": request.url}); pending.add(request)
    page.on("request", requested)
    page.on("requestfinished", lambda request: pending.discard(request))
    page.on("requestfailed", lambda request: pending.discard(request))
    return errors, requests, pending


def monitor(page):
    page.evaluate("""() => {
      const proof=window.__startupIntentProof={samples:[],clicks:window.__startupClicks||[],running:true};
      const sample=(phase,oldView=null)=>{
        const query=new URLSearchParams(location.search);
        proof.samples.push({phase,time:performance.now(),bodyView:document.body.dataset.view,
          queryView:query.get('view'),queryProject:query.get('project'),queryRun:query.get('run'),oldView,
          projectName:document.querySelector('#sidebar-project-name').textContent,
          runIdentity:document.querySelector('#run-identity').textContent});
      };
      proof.sample=sample;
      const observer=new MutationObserver(records=>{
        for(const record of records)sample('body-view-mutation',record.oldValue);
      });
      observer.observe(document.body,{attributes:true,attributeFilter:['data-view'],attributeOldValue:true});
      const frame=()=>{if(proof.running){sample('native-rAF');requestAnimationFrame(frame);}};
      requestAnimationFrame(frame);sample('monitor-start');
      proof.stop=()=>{proof.running=false;observer.disconnect();sample('monitor-stop');
        return {samples:proof.samples,clicks:proof.clicks};};
    }""")


def trusted_click(page, selector):
    page.evaluate("""selector => {
      window.__startupClicks=window.__startupClicks||[];
      document.querySelector(selector).addEventListener('click',event=>{
        window.__startupClicks.push({trusted:event.isTrusted,type:event.type,
          target:event.currentTarget.id||event.currentTarget.dataset.view||event.currentTarget.dataset.projectId});
      },{capture:true,once:true});
    }""", selector)
    page.locator(selector).click()


def proof(page):
    return page.evaluate("() => ({samples:window.__startupIntentProof.samples,clicks:window.__startupIntentProof.clicks})")


def assert_intent(page, view, project=None, project_name=None):
    recorded = proof(page)
    violations = [sample for sample in recorded["samples"]
                  if sample["bodyView"] != view or sample["queryView"] != view
                  or (sample["oldView"] is not None and sample["oldView"] != view)
                  or (project is not None and sample["queryProject"] != project)
                  or (project_name is not None and sample["projectName"] != project_name)]
    assert not violations, {"violations": violations, "last_samples": recorded["samples"][-10:]}


def save_final(page, scene, name, errors, requests):
    captured = page.evaluate("() => window.__startupIntentProof?.stop() || null")
    after = counts(scene["data_dir"])
    keep(name, {"browser_proof": captured, "requests": requests, "page_errors": errors,
                "before": scene["before"], "after": after, "source_sha256": scene["source_sha256"],
                "original_inputs": scene["original_inputs"]})
    assert after == scene["before"]
    assert not [request for request in requests if request["method"] != "GET"], requests
    assert not errors, errors


@pytest.mark.parametrize("boundary", ["bootstrap", "project-detail"])
def test_startup_initial_run_never_overrides_explicit_settings(startup_scene, boundary):
    scene = startup_scene
    with sync_playwright() as playwright:
        browser = launch_browser(playwright); page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors, requests, _ = observe(page)
        path = "/api/bootstrap" if boundary == "bootstrap" else f"/api/projects/{scene['pid']}"
        start = HeldGET(page, scene["origin"], path)
        run = HeldGET(page, scene["origin"], scene["run_path"])
        try:
            page.goto(f"{scene['origin']}/?project={scene['pid']}&run={scene['rid']}", wait_until="domcontentloaded")
            pump(page, lambda: len(start.held) == 1, boundary + " GET held")
            expect(page.locator("body")).to_have_attribute("data-view", "results")
            # Start recording after the actual click has chosen settings, so initial results is outside the asserted interval.
            trusted_click(page, "nav [data-view='settings']")
            expect(page.locator("body")).to_have_attribute("data-view", "settings")
            assert parse_qs(urlsplit(page.url).query)["view"] == ["settings"]
            monitor(page)
            assert proof(page)["clicks"] == [{"trusted": True, "type": "click", "target": "settings"}]
            start.release()
            pump(page, lambda: len(run.held) == 1, "actual original run GET held")
            frames(page)
            assert_intent(page, "settings")
            run.release()
            expect(page.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
            expect(page.locator("#run-events")).to_contain_text("真实工具退出码：0", timeout=15000)
            frames(page)
            assert_intent(page, "settings")
            assert parse_qs(urlsplit(page.url).query)["run"] == [scene["rid"]]
        finally:
            try: save_final(page, scene, "startup-settings-" + boundary, errors, requests)
            finally: close_gates(browser, start, run)


def test_startup_untouched_initial_run_restores_results_and_actual_events(startup_scene):
    scene = startup_scene
    with sync_playwright() as playwright:
        browser = launch_browser(playwright); page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors, requests, _ = observe(page)
        start = HeldGET(page, scene["origin"], "/api/bootstrap")
        run = HeldGET(page, scene["origin"], scene["run_path"])
        try:
            page.goto(f"{scene['origin']}/?project={scene['pid']}&run={scene['rid']}", wait_until="domcontentloaded")
            pump(page, lambda: len(start.held) == 1, "untouched bootstrap GET held")
            expect(page.locator("body")).to_have_attribute("data-view", "results")
            assert "window" not in parse_qs(urlsplit(page.url).query)
            start.release(); pump(page, lambda: len(run.held) == 1, "untouched run GET held")
            expect(page.locator("body")).to_have_attribute("data-view", "results")
            assert parse_qs(urlsplit(page.url).query)["view"] == ["results"]
            monitor(page); frames(page); assert_intent(page, "results", scene["pid"])
            run.release()
            expect(page.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
            expect(page.locator("#run-events")).to_contain_text("真实工具退出码：0", timeout=15000)
            frames(page); assert_intent(page, "results", scene["pid"])
            assert parse_qs(urlsplit(page.url).query)["run"] == [scene["rid"]]
        finally:
            try: save_final(page, scene, "startup-untouched-results", errors, requests)
            finally: close_gates(browser, start, run)


def test_startup_initial_project_never_overrides_new_project_selection(startup_scene):
    scene = startup_scene
    with sync_playwright() as playwright:
        browser = launch_browser(playwright); page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors, requests, pending = observe(page)
        start = HeldGET(page, scene["origin"], "/api/bootstrap")
        capabilities = HeldGET(page, scene["origin"], "/api/runtime")
        run = HeldGET(page, scene["origin"], scene["run_path"])
        try:
            page.goto(f"{scene['origin']}/?project={scene['pid']}&run={scene['rid']}", wait_until="domcontentloaded")
            pump(page, lambda: len(start.held) == 1, "other-project bootstrap GET held")
            start.release()
            pump(page, lambda: len(capabilities.held) == 1, "startup runtime GET held after real project list")
            trusted_click(page, "#sidebar-projects")
            expect(page.locator("#projects button[data-project-id]")).to_have_count(2)
            trusted_click(page, f"#projects button[data-project-id='{scene['other']['id']}']")
            expect(page.locator("#sidebar-project-name")).to_have_text(scene["other"]["name"])
            pump(page, lambda: not [request for request in pending if urlsplit(request.url).path.startswith(
                f"/api/projects/{scene['other']['id']}")], "actual other-project reads finished")
            assert parse_qs(urlsplit(page.url).query)["project"] == [scene["other"]["id"]]
            monitor(page)
            assert len(proof(page)["clicks"]) == 2 and all(event["trusted"] for event in proof(page)["clicks"])
            capabilities.release()
            pump(page, lambda: bool(run.held) or (not pending and
                page.locator("#capability-summary").text_content() != "正在读取实际版本"), "late startup complete or actual original run held")
            frames(page)
            assert_intent(page, "projects", scene["other"]["id"], scene["other"]["name"])
            assert not run.requests, "Implicit original startup run was restored after another project was chosen"
            expect(page.locator(f"#projects button[data-project-id='{scene['other']['id']}']")).to_have_attribute("aria-current", "true")
            assert "run" not in parse_qs(urlsplit(page.url).query)
        finally:
            try: save_final(page, scene, "startup-other-project", errors, requests)
            finally: close_gates(browser, start, capabilities, run)
