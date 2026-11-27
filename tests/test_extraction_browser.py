"""Original source/transport fixtures; real drawer/browser/API/SQLite/installed SDK/MCP."""
import base64
import copy
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

from claude_agent_sdk import ClaudeSDKClient
from playwright.sync_api import expect, sync_playwright
import pytest

from conftest import LiveServer
from test_claude_native_transport import OriginalJSONTransport
from test_desktop_design import project, proof, record_json, upload
from test_extraction_context import bundle
from test_image_documents import original_image
from test_native_consent_browser import actual_native_consent_server
from test_sdk_search_mcp import SearchWire, local_mcp
from test_web import launch_browser


@pytest.fixture
def extraction_server(actual_native_consent_server, monkeypatch):
    server = actual_native_consent_server
    server.output = None
    def actual_client(options):
        wire = SearchWire(options) if options.mcp_servers else OriginalJSONTransport(total_cost_usd=.002)
        wire.structured_output = copy.deepcopy(server.output)
        server.wires.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)
    monkeypatch.setattr("packages.adapters.claude_sdk.ClaudeSDKClient", actual_client)
    return server


def open_drawer(page):
    page.locator("#reader-extract").click()
    expect(page.locator("#extraction-drawer")).to_be_visible()
    expect(page.locator("#submit-extraction")).to_be_enabled()


def source_rows(page, server, pid):
    return page.request.get(f"{server.origin}/api/projects/{pid}/documents").json()["documents"]


def test_actual_mock_tail_context_receipt_manual_boundary_and_mobile_keyboard(live_server):
    raw = ("工程需求采集与边界条件\n" * 6000 + "REQ: preserve tail\nACCEPT: original source matches\n").encode()
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_server.origin)
        pid = project(page, "原创提取末段")
        upload(page, "完整七万字需求.txt", raw)
        doc = source_rows(page, live_server, pid)[0]
        open_drawer(page)
        expect(page.locator("#extract-include-images")).not_to_be_checked()
        expect(page.locator("#extract-search-enabled")).not_to_be_checked()
        page.locator("#extract-select-last").click()
        expect(page.locator("#extract-context-badge")).to_contain_text("明确选择 1 段")
        with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path.endswith("/context")) as preview:
            page.locator("#extract-preview-context").click()
        assert preview.value.status == 200
        receipt = preview.value.json()
        assert receipt["coverage"] == "PARTIAL_EXPLICIT_SELECTION" and len(receipt["included_chunks"]) == 1
        expect(page.locator("#extract-context-receipt")).to_contain_text(receipt["context_sha256"])
        page.locator("#extract-chunks-prev").click()
        expect(page.locator("#extract-context-receipt")).to_contain_text(receipt["context_sha256"])
        page.locator("#close-extraction").click()
        expect(page.locator("#extraction-drawer")).to_be_hidden()
        open_drawer(page)
        expect(page.locator("#extract-context-badge")).to_contain_text("明确选择 1 段")
        expect(page.locator("#extract-context-receipt")).to_contain_text(receipt["context_sha256"])
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#extract-preview-context").focus()
        page.keyboard.press("Tab")
        assert page.evaluate("document.querySelector('#extraction-drawer').contains(document.activeElement)")
        assert page.evaluate("document.querySelector('#extraction-drawer').scrollWidth <= document.querySelector('#extraction-drawer').clientWidth + 1")
        proof(page, "extraction-mobile-last-context.png")
        with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path.endswith("/planning/extract")) as extracted:
            page.locator("#submit-extraction").click()
        assert extracted.value.status == 200
        posted = extracted.value.request.post_data_json
        assert posted["mode"] == "mock" and posted["chunk_indexes"] == [doc["chunk_count"] - 1]
        assert posted["include_images"] is False and posted["search_enabled"] is False
        answer = extracted.value.json()
        assert answer["coverage"] == "PARTIAL_EXPLICIT_SELECTION" and answer["context_sha256"] == receipt["context_sha256"]
        expect(page.locator("#extraction-drawer")).to_be_hidden()
        expect(page.locator("#requirements-zone")).to_be_visible()
        expect(page.locator("#requirement-state")).to_contain_text("Mock草案")
        expect(page.locator("#confirmed")).not_to_be_checked()
        assert answer["proposal"]["requirements"][0]["source_refs"][0]["line_start"] == 6001
        assert page.request.get(f"{live_server.origin}/api/projects/{pid}/requirements").json()["requirements"] == []
        with sqlite3.connect(live_server.data_dir / "eeas.sqlite3") as database:
            assert database.execute("SELECT COUNT(*) FROM agent_executions").fetchone()[0] == 0
        record_json("extraction-mock-tail.json", {"document": doc, "posted": posted, "receipt": receipt, "response": answer, "requirements_autosaved": 0, "agent_executions": 0})
        assert not errors
        browser.close()


def test_actual_live_image_search_same_request_idempotency_and_false_confirmation(extraction_server):
    server = extraction_server
    raw = original_image("PNG")
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.goto(server.origin)
        pid = project(page, "原创图像提取建议")
        upload(page, "原创提取原图.png", raw, "image/png")
        doc = source_rows(page, server, pid)[0]
        server.output = bundle(doc, 1)
        asset = page.request.get(f"{server.origin}/api/projects/{pid}/documents/{doc['id']}/assets?revision=1").json()["assets"][0]
        open_drawer(page)
        page.locator("input[name=extraction-mode][value=live]").check()
        expect(page.locator("#extract-include-images")).not_to_be_checked()
        expect(page.locator("#extract-search-enabled")).not_to_be_checked()
        page.locator("#extract-include-images").check()
        page.locator("#extract-image-list input").check()
        page.locator("#extract-search-enabled").check()
        page.wait_for_function("() => document.querySelector('#extract-search-badge').getAnimations().some(a => a.playState === 'running' && a.currentTime > 0)")
        page.emulate_media(reduced_motion="reduce")
        page.locator("#extract-search-enabled").uncheck()
        page.locator("#extract-search-enabled").check()
        assert page.locator("#extract-search-badge").evaluate("el => el.getAnimations().length") == 0
        page.locator("#extract-preview-context").click()
        expect(page.locator("#extract-context-receipt")).to_contain_text("完整覆盖")
        proof(page, "extraction-live-explicit-source.png")
        with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path.endswith("/planning/extract")) as extracted:
            page.locator("#submit-extraction").click()
        assert extracted.value.status == 200
        posted, answer = extracted.value.request.post_data_json, extracted.value.json()
        assert posted["include_images"] is True and posted["search_enabled"] is True and posted["image_asset_ids"] == [asset["asset_id"]]
        assert not answer["proposal"]["confirmed"] and answer["proposal"]["origin"] == "live_suggestion"
        assert answer["image_refs"] == [{"document_id": doc["id"], "revision": 1, "asset_id": asset["asset_id"], "sha256": asset["sha256"]}]
        expect(page.locator("#extraction-drawer")).to_be_hidden()
        expect(page.locator("#confirmed")).not_to_be_checked()
        expect(page.locator("#plan-requirements")).to_be_disabled()
        assert page.request.get(f"{server.origin}/api/projects/{pid}/requirements").json()["requirements"] == []
        assert len(server.wires) == len(server.mcp["calls"]) == 1
        users = [event for event in server.wires[0].writes if event["type"] == "user"]
        assert base64.b64decode(users[0]["message"]["content"][1]["source"]["data"]) == raw
        page.locator("#configure-extraction").click()
        expect(page.locator("#extract-include-images")).to_be_checked()
        expect(page.locator("#extract-search-enabled")).to_be_checked()
        with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path.endswith("/planning/extract")) as repeated:
            page.locator("#submit-extraction").click()
        assert repeated.value.status == 200 and repeated.value.request.post_data_json == posted
        assert repeated.value.json()["execution_id"] == answer["execution_id"]
        assert len(server.wires) == len(server.mcp["calls"]) == 1
        assert server.mcp["calls"][0]["tool"] == "original_approved_search"
        with sqlite3.connect(server.data_dir / "eeas.sqlite3") as database:
            assert database.execute("SELECT COUNT(*) FROM agent_executions").fetchone()[0] == 1
        record_json("extraction-live-idempotent.json", {"posted": posted, "response": answer, "repeat_execution": repeated.value.json()["execution_id"], "native_wires": 1, "actual_mcp_calls": server.mcp["calls"], "requirements_autosaved": 0})
        browser.close()


def test_actual_oversize_rejected_without_truncation_then_explicit_tail_and_revision_guard(extraction_server):
    server = extraction_server
    raw = ("超长中文条件\n" * 40000).encode()
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.goto(server.origin)
        pid = project(page, "原创超限明确选择")
        upload(page, "超限原文.txt", raw)
        doc = source_rows(page, server, pid)[0]
        server.output = bundle(doc, doc["line_count"] - 1)
        open_drawer(page)
        page.locator("input[name=extraction-mode][value=live]").check()
        with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path.endswith("/planning/extract")) as refused:
            page.locator("#submit-extraction").click()
        assert refused.value.status == 422
        expect(page.locator("#extraction-state")).to_contain_text("超限不截断")
        assert len(server.wires) == len(server.mcp["calls"]) == 0
        page.locator("#extract-select-last").click()
        page.locator("#extract-preview-context").click()
        expect(page.locator("#extract-context-receipt")).to_contain_text("部分上下文")
        with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path.endswith("/planning/extract")) as selected:
            page.locator("#submit-extraction").click()
        assert selected.value.status == 200
        assert selected.value.request.post_data_json["chunk_indexes"] == [doc["chunk_count"] - 1]
        assert selected.value.json()["coverage"] == "PARTIAL_EXPLICIT_SELECTION"
        expect(page.locator("#extraction-drawer")).to_be_hidden()
        page.locator("#configure-extraction").click()
        page.locator("#close-extraction").click()
        expect(page.locator("#extraction-drawer")).to_be_hidden()
        page.locator("nav [data-view='library']").click()
        page.locator("#documents").get_by_role("button", name="上传新修订", exact=True).click()
        page.locator("#document-file").set_input_files({"name": "超限原文.txt", "mimeType": "text/plain", "buffer": b"REQ: new revision\nACCEPT: reviewed source\n"})
        page.locator("#document-form").get_by_role("button", name="导入资料", exact=True).click()
        expect(page.locator("#upload-drawer")).to_be_hidden()
        # A different revision must obtain its own choices rather than inherit v1 indexes/permissions.
        open_drawer(page)
        expect(page.locator("#extraction-source")).to_contain_text("固定 v2")
        expect(page.locator("#extract-custom-chunks")).not_to_be_checked()
        expect(page.locator("#extract-search-enabled")).not_to_be_checked()
        expect(page.locator("#extract-include-images")).not_to_be_checked()
        expect(page.locator("input[name=extraction-mode][value=mock]")).to_be_checked()
        assert len(server.wires) == 1 and len(server.mcp["calls"]) == 0
        assert page.request.get(f"{server.origin}/api/projects/{pid}/documents/{doc['id']}/download?revision=1").body() == raw
        record_json("extraction-oversize-revision.json", {"full_status": refused.value.status, "explicit_posted": selected.value.request.post_data_json, "partial_response": selected.value.json(), "native_wires": 1, "no_mcp_calls": True, "v2_choices_reset_for_actual_revision": True})
        browser.close()


def test_missing_live_configuration_visible_no_admission_or_saved_requirements(tmp_path, monkeypatch):
    for key in ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "EEAS_MODEL_ID", "EEAS_LIVE_MAX_COST_USD", "EEAS_MODEL_VISION_ENABLED"]:
        monkeypatch.setenv(key, "")
    for key in ["EEAS_CLAUDE_SETTINGS_PATH", "EEAS_SEARCH_MCP_CONFIG_PATH"]:
        monkeypatch.delenv(key, raising=False)
    server = LiveServer(tmp_path).start()
    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright)
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            page.goto(server.origin)
            pid = project(page, "原创缺配置提取")
            upload(page, "未接通.txt", b"REQ: actual source\nACCEPT: no fake model\n")
            open_drawer(page)
            page.locator("input[name=extraction-mode][value=live]").check()
            expect(page.locator("#extract-include-images")).to_be_disabled()
            expect(page.locator("#extract-search-enabled")).to_be_disabled()
            expect(page.locator("#extract-image-status")).to_contain_text("MODEL_VISION_NOT_ENABLED")
            expect(page.locator("#extract-search-status")).to_contain_text("NOT_CONFIGURED")
            with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path.endswith("/planning/extract")) as refused:
                page.locator("#submit-extraction").click()
            assert refused.value.status == 503
            expect(page.locator("#extraction-error")).to_contain_text("NO_CREDENTIALS")
            assert refused.value.request.post_data_json["include_images"] is False and refused.value.request.post_data_json["search_enabled"] is False
            assert page.request.get(f"{server.origin}/api/projects/{pid}/requirements").json()["requirements"] == []
            with sqlite3.connect(tmp_path / "eeas.sqlite3") as database:
                assert database.execute("SELECT COUNT(*) FROM agent_executions").fetchone()[0] == 0
            proof(page, "extraction-live-unconfigured-refusal.png")
            browser.close()
    finally:
        server.stop()
