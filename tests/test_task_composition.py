"""Actual Task source guidance, useful desktop geometry and short-phone keyboard."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from test_web import launch_browser
from test_desktop_design import project


def snapshot(page, name, facts):
    target = os.environ.get("EEAS_EVIDENCE_DIR")
    if not target:
        return
    page.evaluate("async () => {await Promise.all(document.getAnimations().filter(a=>Number.isFinite(a.effect?.getComputedTiming().endTime)).map(a=>a.finished.catch(()=>{})));}")
    page.screenshot(path=str(Path(target) / (name + ".png")), full_page=True)
    (Path(target) / (name + ".json")).write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n")


def save_requirement(page, confirmed):
    page.locator("nav [data-view=requirements]").click()
    page.locator("#requirement-title").fill("原创布局需求")
    page.locator("#requirement-text").fill("为原创候选记录设备状态")
    page.locator("#acceptance-text").fill("独立验收核对原创候选")
    page.locator("#confirmed").set_checked(confirmed)
    page.get_by_role("button", name="保存需求新修订", exact=True).click()
    expect(page.locator("#requirement-state")).to_contain_text("已保存")


def test_task_desktop_actual_saved_source_three_states_and_full_width(live_server):
    with sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        facts = {}
        try:
            page.goto(live_server.origin)
            pid = project(page, "原创任务排版与状态")
            save_requirement(page, False)
            page.locator("nav [data-view=tasks]").click()
            expect(page.locator("#execution-tasks")).to_contain_text("先确认")
            rows = page.request.get(f"{live_server.origin}/api/projects/{pid}/requirements").json()["requirements"]
            assert len(rows) == 1 and rows[0]["payload"]["confirmed"] is False
            page.locator("#task-goal").fill("original-unconfirmed-must-not-submit")
            posted = []
            page.on("request", lambda r: posted.append(r) if r.method == "POST" and r.url.endswith("/tasks") else None)
            page.locator("#task-form").get_by_role("button", name="创建执行任务", exact=True).click()
            expect(page.locator("#error")).to_contain_text("先选择一份已确认")
            assert posted == []
            facts["unconfirmed"] = rows[0]

            save_requirement(page, True)
            page.locator("nav [data-view=tasks]").click()
            expect(page.locator("#task-source-status")).to_have_text("已确认 · 可创建")
            expect(page.locator("#execution-tasks")).to_contain_text("填写任务目标")
            rows = page.request.get(f"{live_server.origin}/api/projects/{pid}/requirements").json()["requirements"]
            selected = rows[0]
            assert selected["revision"] == 2 and selected["payload"]["confirmed"] is True
            assert selected["source_status"] == "UNSOURCED"  # Same gate as the actual submit API.
            geometry = page.locator("#task-form").evaluate("e=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height,submitBottom:e.querySelector('button[type=submit]').getBoundingClientRect().bottom,viewport:innerHeight})")
            facts["geometry"] = geometry
            snapshot(page, "task-composition-ready-desktop", facts)
            assert geometry["width"] >= 800 and geometry["height"] <= 510
            assert geometry["submitBottom"] <= geometry["viewport"] - 10

            page.reload()
            expect(page.locator("#task-source-status")).to_have_text("待选择需求")
            expect(page.locator("#execution-tasks")).to_contain_text("选择已确认需求")
            assert page.request.get(f"{live_server.origin}/api/projects/{pid}/tasks").json()["tasks"] == []
            page.locator("#task-select-requirement").click()
            page.locator("#requirements button").filter(has_text="原创布局需求").click()
            page.locator("nav [data-view=tasks]").click()
            expect(page.locator("#task-source-status")).to_have_text("已确认 · 可创建")
            page.locator("#task-goal").fill("original-confirmed-task")
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/tasks")) as created:
                page.locator("#task-form").get_by_role("button", name="创建执行任务", exact=True).click()
            assert created.value.status == 201
            task = created.value.json()
            assert task["spec"]["requirement_refs"] == [{"requirement_id": selected["id"], "revision": 2, "sha256": selected["sha256"]}]
            expect(page.locator("#execution-tasks [data-task-id]")).to_have_count(1)
            facts.update({"confirmed": selected, "created_task": task, "task_post_count": len(posted)})
            assert len(posted) == 1
        finally:
            snapshot(page, "task-composition-desktop-final", facts)
            browser.close()


@pytest.mark.parametrize("reduced", [False, True])
def test_task_short_phone_keyboard_real_create_and_independent_window(live_server, reduced):
    with sync_playwright() as p:
        browser = launch_browser(p)
        context = browser.new_context(viewport={"width": 390, "height": 540}, reduced_motion="reduce" if reduced else "no-preference")
        page = context.new_page()
        facts = {"reduced": reduced}
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            page.goto(live_server.origin)
            page.locator("#menu-toggle").click()
            pid = project(page, "原创短屏任务")
            # Use the real menu navigation under the short mobile viewport.
            page.locator("#menu-toggle").click()
            save_requirement(page, True)
            page.locator("#menu-toggle").click()
            page.locator("nav [data-view=tasks]").click()
            expect(page.locator("#task-source-status")).to_have_text("已确认 · 可创建")
            page.locator("#task-goal").fill("original-short-phone-task")
            page.locator("#task-goal").focus()
            expected = ["task-paths", "mock-behavior", "task-timeout", "task-steps", "task-repairs", "task-dependencies", "approval-required"]
            for ident in expected:
                page.keyboard.press("Tab")
                expect(page.locator("#" + ident)).to_be_focused()
            page.keyboard.press("Tab")
            submit = page.locator("#task-form button[type=submit]")
            expect(submit).to_be_focused()
            observed = page.evaluate("({root:[document.documentElement.scrollWidth,innerWidth],button:document.querySelector('#task-form button[type=submit]').getBoundingClientRect().toJSON(),viewport:innerHeight,running:document.getAnimations().filter(a=>a.playState==='running'&&a.effect.getTiming().duration>1).length})")
            assert observed["root"][0] <= observed["root"][1] + 1
            assert 0 <= observed["button"]["top"] < observed["button"]["bottom"] <= observed["viewport"]
            if reduced:
                assert observed["running"] == 0
            facts["keyboard_geometry"] = observed
            snapshot(page, "task-short-phone-ready-" + str(reduced), facts)
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/tasks")) as created:
                page.keyboard.press("Enter")
            assert created.value.status == 201
            task = created.value.json()
            expect(page.locator("#execution-tasks [data-task-id]")).to_have_count(1)
            page.locator("#execution-tasks").get_by_role("button", name="启动任务", exact=True).click()
            expect(page.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
            with page.expect_popup() as opened:
                page.locator("#open-run-window").click()
            popup = opened.value
            expect(popup.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
            runs = page.request.get(f"{live_server.origin}/api/projects/{pid}/tasks/{task['id']}/runs").json()["runs"]
            assert len(runs) == 1
            assert page.request.get(f"{live_server.origin}/api/projects/{pid}/tasks").json()["tasks"][0]["spec"] == task["spec"]
            facts.update({"task": task, "runs": runs, "popup_url": popup.url, "pageerrors": errors})
            assert not errors
        finally:
            snapshot(page, "task-short-phone-final-" + str(reduced), facts)
            context.close()
            browser.close()
