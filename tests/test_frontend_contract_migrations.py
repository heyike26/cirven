"""Explicit migration: multiline Enter inserts text; actual submit retains old source gates.
Original historical file and failed receipts remain immutable. All original
assertions are retained; only two submit gestures use the real native button.
"""
from pathlib import Path
import importlib.util
from playwright.sync_api import expect

_PATH = Path(__file__).resolve().parents[1] / "reports/E07-independent-task-layout/microcases_schema_sync.py"
_SPEC = importlib.util.spec_from_file_location("eeas_original_task_layout_contract", _PATH)
_ORIGINAL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_ORIGINAL)
desk = _ORIGINAL.desk
select_requirement = _ORIGINAL.select_requirement
photograph = _ORIGINAL.photograph
note = _ORIGINAL.note
task_posts = _ORIGINAL.task_posts
preparation = _ORIGINAL.preparation

def test_multiline_goal_explicit_submit_preserves_original_source_gates(desk):
    page = desk["page"]
    page.goto(desk["server"].origin + f"/?view=tasks&project={desk['pid']}")
    expect(page.locator("#task-source-note")).to_contain_text("先选择已确认需求")
    photograph(page, "reload-saved-but-not-selected")
    note("reload-saved-but-not-selected", preparation(page))
    assert "先确认并保存" not in page.locator("#execution-tasks").inner_text()
    select_requirement(desk, desk["pending"])
    expect(page.locator("#task-source-status")).to_have_text("需求待确认")
    page.locator("#task-goal").fill("未确认状态必须拒绝创建")
    page.locator("#task-goal").press("Enter")
    expect(page.locator("#task-goal")).to_have_value('未确认状态必须拒绝创建\n')
    assert len(task_posts(desk)) == 0
    page.locator("#task-form").get_by_role("button", name="创建执行任务", exact=True).click()
    expect(page.locator("#error")).to_contain_text("已确认")
    photograph(page, "real-unconfirmed-submit-refused")
    note("real-unconfirmed-submit-refused", preparation(page))
    assert not task_posts(desk)
    select_requirement(desk, desk["manual"])
    expect(page.locator("#task-source-status")).to_have_text("已确认 · 可创建")
    expect(page.locator("#task-source-note")).to_contain_text("含无来源需求")
    assert "先确认并保存" not in page.locator("#execution-tasks").inner_text()
    page.locator("#task-goal").fill("人工已确认需求可创建但不冒充有来源")
    page.locator("#task-form").get_by_role("button", name="创建执行任务", exact=True).click()
    expect(page.locator("#task-list-count")).to_have_text("1 项任务")
    created = desk["api"]("GET", f"/api/projects/{desk['pid']}/tasks")["tasks"]
    assert len(created) == 1 and created[0]["spec"]["requirement_refs"] == [{
        "requirement_id": desk["manual"]["id"], "revision": desk["manual"]["revision"], "sha256": desk["manual"]["sha256"]}]
    photograph(page, "confirmed-unsourced-real-created-task")
    note("confirmed-unsourced-real-created-task", {"preparation": preparation(page), "tasks": created})
    select_requirement(desk, desk["current"])
    expect(page.locator("#task-source-status")).to_have_text("已确认 · 可创建")
    desk["api"]("PUT", f"/api/projects/{desk['pid']}/documents/{desk['document']['id']}",
                files={"file": ("独立 来源.md", b"changed original source\n")}, data={"expected_revision": 1})
    page.reload()
    page.wait_for_load_state("networkidle")
    stale = desk["api"]("GET", f"/api/projects/{desk['pid']}/requirements/{desk['current']['id']}")
    assert stale["payload"]["confirmed"] is True and stale["source_status"] == "STALE"
    select_requirement(desk, stale)
    expect(page.locator("#task-source-status")).to_have_text("来源待复核")
    page.locator("#task-goal").fill("旧来源即使已确认仍不得新建")
    page.locator("#task-goal").press("Enter")
    expect(page.locator("#task-goal")).to_have_value('旧来源即使已确认仍不得新建\n')
    assert len(task_posts(desk)) == 1
    page.locator("#task-form").get_by_role("button", name="创建执行任务", exact=True).click()
    expect(page.locator("#error")).to_contain_text("来源未失效")
    photograph(page, "confirmed-stale-real-gate-refused")
    note("confirmed-stale-real-gate-refused", {"preparation": preparation(page), "real_requirement": stale})
    assert len(task_posts(desk)) == 1
