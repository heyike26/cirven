"""Original real-browser desktop workspace; fixtures never qualify Windows or LIVE models."""
import hashlib
import base64
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

from test_image_documents import original_image
from test_diagram_import import archive, visio_parts, xmind_json, original_png, original_jpeg
from test_pdf_documents import pdf_fixture
from test_web import launch_browser
from test_windows_browser import test_two_real_task_windows_no_mixed_events_refresh_and_backend_effects as verify_independent_windows


def proof(page, name):
    if os.environ.get("EEAS_EVIDENCE_DIR"):
        if page.locator("#project-zone").is_visible():
            page.wait_for_function("() => document.querySelector('#studio-hero-image')?.complete && document.querySelector('#studio-hero-image').naturalWidth > 0")
        page.evaluate("async () => { await document.fonts.ready; await Promise.all(document.getAnimations().filter(a => a.playState === 'running').map(a => a.finished.catch(() => {}))); }")
        page.screenshot(path=str(Path(os.environ["EEAS_EVIDENCE_DIR"]) / name), full_page=True)


def project(page, name="原创工程桌面项目"):
    if not page.locator("#project-drawer").is_visible():
        page.locator("nav [data-view='projects']").click()
        page.locator("#new-project").click()
    page.locator("#name").fill(name)
    page.locator("#description").fill("原文、需求、执行和独立验收分开记录")
    page.get_by_role("button", name="创建项目", exact=True).click()
    expect(page.locator("#project-drawer")).to_be_hidden(timeout=15000)
    item = page.locator("#projects").get_by_role("button", name=name, exact=True)
    item.wait_for()
    pid = item.get_attribute("data-project-id")
    item.click()
    expect(page.locator("#project-context")).to_have_text(name)
    expect(page.locator("#home-document-count")).to_have_text("0")
    return pid


def upload(page, filename, raw, mime="text/plain", *, drop=False):
    page.locator("#toolbar-upload").click()
    expect(page.locator("#upload-drawer")).to_be_visible()
    if drop:
        page.locator("#upload-dropzone").evaluate("""(zone, data) => {
            const bytes = Uint8Array.from(atob(data.base64), char => char.charCodeAt(0));
            const transfer = new DataTransfer();
            transfer.items.add(new File([bytes], data.name, {type: data.mime}));
            zone.dispatchEvent(new DragEvent('drop', {dataTransfer: transfer, bubbles: true, cancelable: true}));
        }""", {"name": filename, "base64": base64.b64encode(raw).decode(), "mime": mime})
        expect(page.locator("#upload-file-state")).to_contain_text(filename)
    else:
        page.locator("#document-file").set_input_files({"name": filename, "mimeType": mime, "buffer": raw})
    page.locator("#document-form").get_by_role("button", name="导入资料", exact=True).click()
    expect(page.locator("#upload-drawer")).to_be_hidden(timeout=15000)
    expect(page.locator("#reader-title")).to_have_text(filename)


def test_home_actual_counts_upload_drawer_keyboard_errors_and_mobile_menu(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_server.origin)
        expect(page.locator("#home-project-count")).to_have_text("0")
        expect(page.locator("#projects-empty-help")).to_be_visible()
        proof(page, "desktop-empty-home.png")
        page.locator("#home-upload").click()
        expect(page.locator("#toast")).to_contain_text("先创建或选择一个项目")
        expect(page.locator("#name")).to_be_focused()
        pid = project(page)
        expect(page.locator("#home-project-count")).to_have_text("1")
        expect(page.locator("#sidebar-project-name")).to_have_text("原创工程桌面项目")
        expect(page.locator("#toast")).to_be_hidden()
        proof(page, "desktop-home.png")
        page.locator("#toolbar-upload").click()
        expect(page.locator("#upload-drawer")).to_be_visible()
        page.locator("#document-file").set_input_files({"name": "unsupported.exe", "mimeType": "application/octet-stream", "buffer": b"original"})
        expect(page.locator("#upload-error")).to_contain_text("暂不支持 EXE")
        page.locator("#document-file").set_input_files({"name": "too-large.txt", "mimeType": "text/plain", "buffer": b"x" * (1024 * 1024 + 1)})
        expect(page.locator("#upload-error")).to_contain_text("超过 1 MiB 上限")
        proof(page, "desktop-upload-error.png")
        page.keyboard.press("Escape")
        expect(page.locator("#upload-drawer")).to_be_hidden()
        assert page.request.get(f"{live_server.origin}/api/projects/{pid}/documents").json()["documents"] == []
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator("#desktop-sidebar").evaluate("el => el.inert")
        page.locator("#menu-toggle").click()
        expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "true")
        page.locator("nav [data-view='library']").click()
        expect(page.locator("#library-zone")).to_be_visible()
        expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "false")
        expect(page.locator("#menu-toggle")).to_be_focused()
        assert page.locator("#desktop-sidebar").evaluate("el => el.inert")
        proof(page, "mobile-library.png")
        page.emulate_media(reduced_motion="reduce")
        assert page.locator("#desktop-sidebar").evaluate("el => parseFloat(getComputedStyle(el).transitionDuration) < .01")
        page.locator("#toolbar-upload").click()
        expect(page.locator("#upload-drawer")).to_be_visible()
        assert page.locator("#upload-drawer").evaluate("el => parseFloat(getComputedStyle(el).animationDuration) < .01")
        proof(page, "mobile-upload-reduced-motion.png")
        assert not errors
        browser.close()


def test_real_70000_character_taskbook_tail_paging_search_and_confirmed_citation(live_server):
    raw = ("原创长任务书\n" + "工程参数与边界条件；" * 7200 + "唯一末尾核查目标\n").encode("utf-8")
    assert len(raw.decode()) > 70000 and len(raw) < 1024 * 1024
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors = []
        requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto(live_server.origin)
        pid = project(page, "原创七万字任务书")
        upload(page, "七万字任务书.md", raw, "text/markdown")
        expect(page.locator("#reader-page")).to_contain_text("第 1 /")
        expect(page.locator("#reader-last")).to_be_enabled()
        assert "唯一末尾核查目标" not in page.locator("#document-content").inner_text()
        page.locator("#reader-last").click()
        expect(page.locator("#document-content")).to_contain_text("唯一末尾核查目标")
        expect(page.locator("#reader-next")).to_be_disabled()
        page.locator(".reader-paper").evaluate("el => { el.scrollTop = el.scrollHeight; }")
        proof(page, "desktop-longbook-tail.png")
        page.locator("#keyword").fill("唯一末尾核查目标")
        page.get_by_role("button", name="检索资料", exact=True).click()
        expect(page.locator("#search-results")).to_contain_text("唯一末尾核查目标")
        page.get_by_role("button", name="查看命中原文", exact=True).click()
        expect(page.locator("#reader-meta")).to_contain_text("命中原文位置")
        expect(page.locator("#document-content")).to_contain_text("唯一末尾核查目标")
        assert any("/content?" in url and "char_start=0" not in url for url in requests)
        page.get_by_role("button", name="引用该来源", exact=True).click()
        page.locator("nav [data-view='requirements']").click()
        expect(page.locator("#source-refs")).to_contain_text("行 2—2")
        page.locator("#requirement-title").fill("原创尾段来源需求")
        page.locator("#requirement-text").fill("处理任务书尾段要求")
        page.locator("#acceptance-text").fill("核对所引原文的修订、哈希和行号")
        page.locator("#confirmed").check()
        page.get_by_role("button", name="保存需求新修订", exact=True).click()
        expect(page.locator("#requirement-state")).to_contain_text("已保存 v1")
        doc = page.request.get(f"{live_server.origin}/api/projects/{pid}/documents").json()["documents"][0]
        requirement = page.request.get(f"{live_server.origin}/api/projects/{pid}/requirements").json()["requirements"][0]
        source = requirement["payload"]["requirements"][0]["source_refs"][0]
        assert doc["sha256"] == hashlib.sha256(raw).hexdigest()
        assert doc["character_length"] == len(raw.decode()) and doc["chunk_count"] > 5
        assert source == {"document_id": doc["id"], "revision": 1, "sha256": doc["sha256"], "line_start": 2, "line_end": 2}
        page.locator("nav [data-view='projects']").click()
        expect(page.locator("#home-document-count")).to_have_text("1")
        assert len(page.request.get(live_server.origin + "/api/projects").json()["projects"]) == 1
        if os.environ.get("EEAS_EVIDENCE_DIR"):
            Path(os.environ["EEAS_EVIDENCE_DIR"], "longbook-backend.json").write_text(json.dumps({"doc": doc, "requirement": requirement,
                "raw_sha256": hashlib.sha256(raw).hexdigest(), "actual_characters": len(raw.decode()), "requests": requests}, ensure_ascii=False, indent=2), encoding="utf-8")
        assert not errors
        browser.close()


def test_original_image_actual_preview_download_and_no_vision_claim(live_server):
    raw = original_image()
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.goto(live_server.origin)
        pid = project(page, "原创图像资料")
        upload(page, "原创系统框图.png", raw, "image/png", drop=True)
        image = page.locator("#document-assets img")
        expect(image).to_be_visible()
        page.wait_for_function("() => document.querySelector('#document-assets img')?.complete && document.querySelector('#document-assets img').naturalWidth === 240 && document.querySelector('#document-assets img').naturalHeight === 160")
        expect(page.locator("#document-warnings")).to_contain_text("未提取文字")
        expect(page.locator("#document-warnings")).to_contain_text("图像理解尚未验证")
        with page.expect_download() as downloaded:
            page.locator("#document-download").click()
        assert Path(downloaded.value.path()).read_bytes() == raw
        doc = page.request.get(f"{live_server.origin}/api/projects/{pid}/documents").json()["documents"][0]
        assert doc["sha256"] == hashlib.sha256(raw).hexdigest() and doc["asset_count"] == 1
        proof(page, "desktop-image-preview.png")
        browser.close()


def test_desktop_task_windows_keep_original_backend_isolation_and_restore(live_server):
    """Reuse the original full window contract, including actual jobs, artifacts and cancellation."""
    verify_independent_windows(live_server)


def record_json(name, value):
    if os.environ.get("EEAS_EVIDENCE_DIR"):
        Path(os.environ["EEAS_EVIDENCE_DIR"], name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def visible_source(page, text):
    target = page.locator("#document-content mark").filter(has_text=text)
    try:
        expect(target).to_be_in_viewport()
    except AssertionError:
        record_json("source-viewport-failure.json", page.evaluate("""() => ({scrollY, viewport: {width: innerWidth, height: innerHeight}, reader: {rect: document.querySelector('.reader-paper').getBoundingClientRect().toJSON(), scrollTop: document.querySelector('.reader-paper').scrollTop, scrollHeight: document.querySelector('.reader-paper').scrollHeight}, marks: [...document.querySelectorAll('#document-content mark')].map(el => ({text: el.textContent.slice(0, 180), rect: el.getBoundingClientRect().toJSON()}))})"""))
        proof(page, "source-viewport-failure.png")
        raise


def test_structured_xmind_bounded_nodes_tail_source_and_real_original(live_server):
    sheets = xmind_json()
    sheets[0]["rootTopic"]["children"]["attached"].extend({"id": f"node-{i}", "title": f"原创分页节点 {i}"} for i in range(65))
    raw = archive([("content.json", json.dumps(sheets, ensure_ascii=False)), ("resources/原创.png", original_png()), ("resources/原创.jpg", original_jpeg())])
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto(live_server.origin)
        pid = project(page, "原创结构化思维导图")
        upload(page, "原创图册.xmind", raw, "application/zip")
        expect(page.locator("#structure-kind")).to_contain_text("结构化预览")
        expect(page.locator("#structure-help")).to_contain_text("排版尚未渲染")
        assert page.locator("#structure-list button").count() == 50
        expect(page.locator("#structure-page")).to_have_text("1–50 / 71 节点")
        page.locator("#structure-next").click()
        assert page.locator("#structure-list button").count() == 21
        tail = page.locator('#structure-list button[data-node-id="sheet:0/topic:node-64"]')
        tail.click()
        expect(page.locator("#reader-meta")).to_contain_text("content.json · node-64")
        visible_source(page, "原创分页节点 64")
        expect(page.locator("#document-warnings")).to_contain_text("节点外链仅保存为数据，未访问")
        assert not any("example.invalid" in url for url in requests)
        doc = page.request.get(f"{live_server.origin}/api/projects/{pid}/documents").json()["documents"][0]
        path = f"/api/projects/{pid}/documents/{doc['id']}"
        structure = page.request.get(live_server.origin + path + "/structure?revision=1").json()
        assert "content" not in structure and "raw_bytes" not in structure and len(structure["graph"]["nodes"]) == 71
        node = next(node for node in structure["graph"]["nodes"] if node["id"] == "sheet:0/topic:node-64")
        assert int(page.locator("#document-content mark").first.get_attribute("data-source-line")) == node["line_start"]
        assert all(urlparse(url).path != path for url in requests)
        page.locator("#document-assets .asset-source").get_by_role("button", name="来源 sheet:0 · root", exact=True).click()
        expect(page.locator("#structure-active")).to_contain_text("content.json · root")
        expect(page.locator('#structure-list button[data-node-id="sheet:0/topic:root"]')).to_have_attribute("aria-current", "true")
        expect(page.locator("#structure-links")).to_contain_text("父子")
        with page.expect_download() as download:
            page.locator("#document-download").click()
        assert Path(download.value.path()).read_bytes() == raw
        assert doc["sha256"] == hashlib.sha256(raw).hexdigest()
        proof(page, "structured-xmind-source.png")
        record_json("xmind-browser-backend.json", {"document": doc, "tail": node, "structure": structure, "requests": requests})
        assert not errors
        browser.close()


def test_visio_same_named_pages_and_ids_keep_exact_source_highlight(live_server):
    raw = archive(visio_parts())
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.goto(live_server.origin)
        pid = project(page, "原创 Visio 页面来源")
        upload(page, "原创架构.vsdx", raw, "application/zip")
        expect(page.locator("#structure-kind")).to_contain_text("结构化预览")
        page.locator("#structure-scope").select_option("page:1")
        assert page.locator("#structure-list button").count() == 4
        page.locator('#structure-list button[data-node-id="page:1/shape:2"]').click()
        expect(page.locator("#reader-meta")).to_contain_text("page:1 · visio/pages/page2.xml · 2")
        visible_source(page, "另一页起点含格式尾部")
        expect(page.locator("#structure-links")).to_contain_text("连接端点")
        expect(page.locator("#document-warnings")).to_contain_text("不推断箭头方向")
        doc = page.request.get(f"{live_server.origin}/api/projects/{pid}/documents").json()["documents"][0]
        assets = page.request.get(f"{live_server.origin}/api/projects/{pid}/documents/{doc['id']}/assets?revision=1").json()["assets"]
        assert len(assets) == 2 and all(asset["source_anchors"] for asset in assets)
        assert doc["sha256"] == hashlib.sha256(raw).hexdigest()
        proof(page, "structured-visio-page-source.png")
        record_json("visio-browser-backend.json", {"document": doc, "assets": assets, "selected_source": page.locator("#structure-active").inner_text()})
        browser.close()


def test_long_pdf_metadata_directory_tail_no_full_fetch_and_actual_download(live_server):
    raw = pdf_fixture(["Original timestamp collection.\n" * 2500, "UNIQUE_PDF_TAIL_REQUIREMENT\nOriginal acceptance mapping."])
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        requests = []
        page.on("request", lambda request: requests.append(request.url))
        page.goto(live_server.origin)
        pid = project(page, "原创 PDF 长任务书")
        upload(page, "原创长任务书.pdf", raw, "application/pdf")
        expect(page.locator("#structure-kind")).to_have_text("PDF 页目录与解析原文")
        page.locator('#structure-list button[data-node-id="pdf:2"]').click()
        visible_source(page, "UNIQUE_PDF_TAIL_REQUIREMENT")
        expect(page.locator("#reader-meta")).to_contain_text("PDF · 2")
        doc = page.request.get(f"{live_server.origin}/api/projects/{pid}/documents").json()["documents"][0]
        assert doc["character_length"] > 70000 and doc["sha256"] == hashlib.sha256(raw).hexdigest()
        path = f"/api/projects/{pid}/documents/{doc['id']}"
        assert all(urlparse(url).path != path for url in requests)
        assert any(urlparse(url).path == path + "/structure" for url in requests)
        with page.expect_download() as download:
            page.locator("#document-download").click()
        assert Path(download.value.path()).read_bytes() == raw
        proof(page, "long-pdf-tail-source.png")
        record_json("pdf-browser-backend.json", {"document": doc, "requests": requests, "actual_original_sha256": hashlib.sha256(raw).hexdigest()})
        browser.close()


def test_native_context_explicit_last_chunk_partial_receipt_and_no_fake_execution(live_server):
    raw = ("原创上下文边界\n" + "参数" * 45000 + "上下文末段唯一来源\n").encode()
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        native_requests = []
        page.on("request", lambda request: native_requests.append(request.post_data_json) if request.method == "POST" and request.url.endswith("/native-reviews") else None)
        page.goto(live_server.origin)
        pid = project(page, "原创显式审阅上下文")
        upload(page, "原创九万字资料.txt", raw)
        page.locator("#keyword").fill("上下文末段唯一来源")
        page.get_by_role("button", name="检索资料", exact=True).click()
        page.get_by_role("button", name="引用该来源", exact=True).click()
        page.locator("nav [data-view='requirements']").click()
        page.locator("#requirement-title").fill("人工确认末段来源")
        page.locator("#requirement-text").fill("核对所选末段参数")
        page.locator("#acceptance-text").fill("所选上下文明确保留来源并显示省略范围")
        page.locator("#confirmed").check()
        page.get_by_role("button", name="保存需求新修订", exact=True).click()
        expect(page.locator("#requirement-state")).to_contain_text("已保存 v1")
        page.locator("nav [data-view='tasks']").click()
        page.locator("#task-goal").fill("原创上下文审阅范围")
        page.get_by_role("button", name="创建执行任务", exact=True).click()
        page.locator("#execution-tasks li").filter(has_text="原创上下文审阅范围").get_by_role("button", name="启动任务", exact=True).click()
        expect(page.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
        run_id = parse_qs(urlparse(page.url).query)["run"][0]
        page.locator("#native-review-panel summary").click()
        page.locator("#start-native-review").click()
        expect(page.locator("#native-review-status")).to_contain_text("No source text was truncated")
        page.locator("#load-native-context").click()
        expect(page.locator("#native-context-panel")).to_be_visible()
        assert page.locator("#native-context-chunks input").count() == 20
        page.locator("#preview-native-context").click()
        expect(page.locator("#native-context-receipt")).to_contain_text("No text was truncated")
        page.locator("#native-context-select-last").click()
        expect(page.locator("#native-context-summary")).to_contain_text("显式选择 1 /")
        page.locator("#preview-native-context").click()
        expect(page.locator("#native-context-receipt")).to_contain_text("部分上下文 · 显式选择")
        doc = page.request.get(f"{live_server.origin}/api/projects/{pid}/documents").json()["documents"][0]
        response = page.request.post(f"{live_server.origin}/api/projects/{pid}/documents/{doc['id']}/context", headers={"Origin": live_server.origin, "X-EEAS-CSRF": page.evaluate("csrf")}, data={"revision": 1, "chunk_indexes": [doc["chunk_count"] - 1]})
        record_json("native-context-probe-response.json", {"status": response.status, "body": response.json()})
        assert response.status == 200, response.text()
        context = response.json()
        assert context["coverage"] == "PARTIAL_EXPLICIT_SELECTION" and context["omitted_chunk_count"] == doc["chunk_count"] - 1
        assert "上下文末段唯一来源" in context["chunks"][0]["content"]
        expect(page.locator("#native-context-receipt")).to_contain_text(context["context_sha256"])
        page.locator("#start-native-review").click()
        expect(page.locator("#native-review-status")).to_contain_text("BLOCKED")
        expect(page.locator("#native-review-status")).not_to_contain_text("No source text was truncated")
        assert native_requests[-1]["chunk_selections"] == [{"document_id": doc["id"], "revision": 1, "chunk_indexes": [doc["chunk_count"] - 1]}]
        assert native_requests[-1]["task_binding"]["run_id"] == run_id and native_requests[-1]["needs_tools"] is False
        with sqlite3.connect(live_server.data_dir / "eeas.sqlite3") as connection:
            assert connection.execute("SELECT COUNT(*) FROM agent_executions").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM task_native_reviews").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == 1
        expect(page.locator("#native-review-identity")).to_have_text("")
        expect(page.locator("#run-status")).to_contain_text("未验收")
        proof(page, "native-last-chunk-explicit-receipt.png")
        record_json("native-context-browser-backend.json", {"document": doc, "context": context, "native_requests": native_requests, "fake_executions": 0, "task_runs": 1})
        browser.close()


def test_studio_v2_real_motion_file_chooser_upload_progress_revision_and_stale_drawer(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_server.origin)
        pid = project(page, "原创动画与修订桌面")
        page.locator("nav [data-view='library']").click()
        page.wait_for_function("() => document.querySelector('#nav-active-indicator').getAnimations().some(a => a.playState === 'running' && a.currentTime > 0 && a.currentTime < a.effect.getTiming().duration)")
        motion = page.locator("#nav-active-indicator").evaluate("el => ({transform:getComputedStyle(el).transform, target:Number(el.dataset.position), animations:el.getAnimations().map(a=>({time:a.currentTime,duration:a.effect.getTiming().duration}))})")
        assert motion["animations"] and any(0 < item["time"] < item["duration"] for item in motion["animations"])
        page.locator("#toolbar-upload").click()
        page.wait_for_function("() => document.querySelector('#upload-drawer').getAnimations().some(a => a.playState === 'running')")
        page.locator("#close-upload").click()
        # Exercise native dialog close/reopen while an old exit animation is pending.
        page.locator("#upload-drawer").evaluate("el => el.close()")
        page.locator("#toolbar-upload").click()
        page.evaluate("async () => { await Promise.all(document.getAnimations().map(a => a.finished.catch(() => {}))); }")
        expect(page.locator("#upload-drawer")).to_be_visible()
        cdp = page.context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 80, "downloadThroughput": 1024 * 1024, "uploadThroughput": 32768})
        raw = b"original-browser-progress\n" * 5000
        with page.expect_file_chooser() as chooser:
            page.locator("#choose-document-file").click()
        chooser.value.set_files({"name": "progress-original.txt", "mimeType": "text/plain", "buffer": raw})
        expect(page.locator("#chosen-document-file-name")).to_have_text("progress-original.txt")
        page.locator("#document-form [type=submit]").click()
        page.wait_for_function("() => Number(document.querySelector('#upload-progress-panel').dataset.loadedBytes)>0")
        progress = page.locator("#upload-progress-panel").evaluate("el => ({loaded:Number(el.dataset.loadedBytes),total:Number(el.dataset.totalBytes),label:document.querySelector('#upload-progress-label').textContent})")
        assert 0 < progress["loaded"] <= progress["total"]
        proof(page, "studio-v2-actual-upload-progress.png")
        page.locator("#close-upload").click()
        expect(page.locator("#upload-drawer")).to_be_hidden()
        page.locator("#toolbar-upload").click()
        expect(page.locator("#chosen-document-file-name")).to_have_text("尚未选择文件")
        expect(page.locator("#upload-progress-panel")).to_be_hidden()
        page.wait_for_function("async () => (await (await fetch('/api/projects/' + new URLSearchParams(location.search).get('project') + '/documents')).json()).documents.length === 1")
        # The old real upload finished; its callbacks must leave this new empty drawer alone.
        expect(page.locator("#upload-drawer")).to_be_visible()
        expect(page.locator("#chosen-document-file-name")).to_have_text("尚未选择文件")
        expect(page.locator("#upload-progress-panel")).to_be_hidden()
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
        page.locator("#close-upload").click()
        expect(page.locator("#upload-drawer")).to_be_hidden()
        page.locator("#retry-connection").click()
        expect(page.locator("#documents")).to_contain_text("progress-original.txt · v1")
        doc = page.request.get(f"{live_server.origin}/api/projects/{pid}/documents").json()["documents"][0]
        assert doc["sha256"] == hashlib.sha256(raw).hexdigest()
        page.locator("#documents li").filter(has_text="progress-original.txt · v1").get_by_role("button", name="上传新修订", exact=True).click()
        expect(page.locator("#document-target")).to_contain_text("确认当前 v1")
        replacement = b"original explicit revision\n"
        with page.expect_file_chooser() as chooser:
            page.locator("#choose-document-file").click()
        chooser.value.set_files({"name": "progress-original.txt", "mimeType": "text/plain", "buffer": replacement})
        page.locator("#document-form [type=submit]").click()
        expect(page.locator("#reader-version")).to_have_text("修订 v2", timeout=15000)
        revised = page.request.get(f"{live_server.origin}/api/projects/{pid}/documents").json()["documents"]
        assert len(revised) == 1 and revised[0]["id"] == doc["id"] and revised[0]["revision"] == 2 and revised[0]["sha256"] == hashlib.sha256(replacement).hexdigest()
        page.emulate_media(reduced_motion="reduce")
        page.locator("nav [data-view='settings']").click()
        assert page.locator("#settings-zone").evaluate("el => el.getAnimations().every(a => a.playState !== 'running')")
        assert page.locator("#nav-active-indicator").evaluate("el => el.getAnimations().every(a => a.playState !== 'running')")
        expect(page.locator("#settings-route-status")).to_contain_text("尚未验证")
        proof(page, "studio-v2-settings-actual-sources.png")
        record_json("studio-v2-motion-upload-revision.json", {"motion": motion, "actual_progress": progress, "original": doc, "revised": revised, "old_upload_preserved_in_new_drawer": True})
        assert not errors
        browser.close()


def test_mobile_keyboard_sidebar_focus_tab_cycle_escape_and_current_route(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_server.origin)
        expect(page.locator("#project-zone")).to_be_visible()
        menu = page.locator("#menu-toggle")
        menu.focus()
        page.keyboard.press("Enter")
        expect(menu).to_have_attribute("aria-expanded", "true")
        expect(page.locator("nav [data-view='projects']")).to_be_focused()
        page.keyboard.press("Tab")
        expect(page.locator("nav [data-view='library']")).to_be_focused()
        expect(page.locator("#toolbar-upload")).not_to_be_focused()
        proof(page, "mobile-keyboard-navigation.png")
        page.keyboard.press("Enter")
        expect(page.locator("#library-zone")).to_be_visible()
        expect(menu).to_have_attribute("aria-expanded", "false")
        expect(menu).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.locator("nav [data-view='library']")).to_be_focused()
        page.keyboard.press("Shift+Tab")
        expect(page.locator("nav [data-view='projects']")).to_be_focused()
        page.keyboard.press("Shift+Tab")
        expect(page.locator("#retry-connection")).to_be_focused()
        page.keyboard.press("Tab")
        expect(page.locator("nav [data-view='projects']")).to_be_focused()
        page.keyboard.press("Escape")
        expect(menu).to_have_attribute("aria-expanded", "false")
        expect(menu).to_be_focused()
        assert page.locator("#desktop-sidebar").evaluate("el => el.inert")
        page.keyboard.press("Tab")
        assert not page.evaluate("document.querySelector('#desktop-sidebar').contains(document.activeElement)")
        assert parse_qs(urlparse(page.url).query)["view"] == ["library"]
        record_json("mobile-keyboard-observation.json", {"viewport": {"width": 390, "height": 844}, "enter_focus": "active navigation link", "tab": "next visible navigation link", "shift_tab_wrap": "retry-connection", "escape_focus": "menu-toggle", "actual_route": page.url, "background_tab_after_close": True})
        page.set_viewport_size({"width": 1440, "height": 1050})
        expect(page.locator("#desktop-sidebar")).to_be_visible()
        assert not page.locator("#desktop-sidebar").evaluate("el => el.inert")
        assert not errors
        browser.close()


def test_word_real_upload_complete_parts_image_sources_frozen_task_revision_restart(live_server):
    from test_docx_import import word_parts
    text = "🛠️工程前言\n" + "长任务书" * 18001 + "最终中文尾部条件"
    raw = archive(word_parts(text))
    revised_raw = archive(word_parts(text + "原创第二修订末尾判定"))
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto(live_server.origin)
        pid = project(page, "原创 Word 完整资料工作空间")
        page.locator("#toolbar-upload").click()
        expect(page.locator("#document-file")).to_have_attribute("accept", ".md,.txt,.docx,.pdf,.png,.jpg,.jpeg,.vsdx,.xmind")
        with page.expect_file_chooser() as chooser:
            page.locator("#choose-document-file").click()
        chooser.value.set_files({"name": "原创完整长任务书.docx", "mimeType": mime, "buffer": raw})
        expect(page.locator("#upload-error")).to_have_text("")
        with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == f"/api/projects/{pid}/documents") as imported:
            page.locator("#document-form").get_by_role("button", name="导入资料", exact=True).click()
        assert imported.value.status == 201
        doc = imported.value.json()
        assert doc["format"] == "docx" and doc["character_length"] > 70000
        assert doc["sha256"] == hashlib.sha256(raw).hexdigest() and doc["asset_count"] == 2
        expect(page.locator("#upload-drawer")).to_be_hidden(timeout=15000)
        expect(page.locator("#reader-title")).to_have_text(doc["filename"])
        expect(page.locator("#structure-kind")).to_have_text("Word 结构化预览 · 正文与部件")
        expect(page.locator("#document-warnings")).to_contain_text("原始排版")
        base = f"{live_server.origin}/api/projects/{pid}/documents/{doc['id']}"
        structure = page.request.get(base + "/structure?revision=1").json()
        assert "content" not in structure and structure["outline"]["parts"]
        page.locator("#structure-scope").select_option("docx:word/headers/header.xml")
        page.locator("#structure-list button").filter(has_text="原创页眉末端").click()
        visible_source(page, "原创页眉末端")
        page.locator("#structure-scope").select_option("docx:word/notes/footnotes.xml")
        page.locator("#structure-list button").filter(has_text="脚注完整末端").click()
        visible_source(page, "脚注完整末端")
        proof(page, "word-footnote-original-source.png")
        assets = page.request.get(base + "/assets?revision=1").json()["assets"]
        assert len(assets) == 2 and all(asset["source_anchors"] for asset in assets)
        expect(page.locator("#document-assets img")).to_have_count(2)
        expect(page.locator("#document-assets .asset-source button")).to_have_count(2)
        page.locator("#document-assets img").first.scroll_into_view_if_needed()
        page.wait_for_function("() => [...document.querySelectorAll('#document-assets img')].some(image => image.complete && image.naturalWidth > 0)")
        for asset in assets:
            actual = page.request.get(base + f"/assets/{asset['asset_id']}?revision=1")
            assert actual.status == 200 and hashlib.sha256(actual.body()).hexdigest() == asset["sha256"]
        assert page.request.get(base + "/download?revision=1").body() == raw
        page.locator("#keyword").fill("最终中文尾部条件")
        page.get_by_role("button", name="检索资料", exact=True).click()
        expect(page.locator("#search-results")).to_contain_text("最终中文尾部条件")
        page.get_by_role("button", name="查看命中原文", exact=True).click()
        visible_source(page, "最终中文尾部条件")
        page.get_by_role("button", name="引用该来源", exact=True).click()
        page.locator("nav [data-view='requirements']").click()
        page.locator("#requirement-title").fill("完整 Word 尾部来源")
        page.locator("#requirement-text").fill("处理完整 DOCX 尾部条件")
        page.locator("#acceptance-text").fill("历史来源保持其原件、修订、哈希与精确行号")
        page.locator("#confirmed").check()
        page.get_by_role("button", name="保存需求新修订", exact=True).click()
        expect(page.locator("#requirement-state")).to_contain_text("已保存 v1")
        requirement = page.request.get(f"{live_server.origin}/api/projects/{pid}/requirements").json()["requirements"][0]
        source = requirement["payload"]["requirements"][0]["source_refs"][0]
        assert source["document_id"] == doc["id"] and source["revision"] == 1 and source["sha256"] == doc["sha256"]
        page.locator("nav [data-view='tasks']").click()
        page.locator("#task-goal").fill("word-frozen-original-source")
        page.get_by_role("button", name="创建执行任务", exact=True).click()
        page.locator("#execution-tasks").get_by_role("button", name="启动任务", exact=True).click()
        expect(page.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
        page.locator("#verify-run").click()
        expect(page.locator("#run-status")).to_contain_text("验收通过", timeout=15000)
        route = parse_qs(urlparse(page.url).query)
        run = page.request.get(f"{live_server.origin}/api/projects/{pid}/runs/{route['run'][0]}").json()
        frozen = json.loads(run["frozen_inputs"])
        assert base64.b64decode(frozen["documents"][0]["raw_base64"]) == raw
        assert frozen["documents"][0]["parse_receipt"] and frozen["documents"][0]["sha256"] == doc["sha256"]
        scope = page.request.get(f"{live_server.origin}/api/projects/{pid}/tasks/{run['task_id']}/runs/{run['id']}/native-review-scope")
        assert scope.status == 200 and scope.json()["document_contexts"][0]["revision"] == 1
        page.locator("nav [data-view='library']").click()
        page.locator("#documents").get_by_role("button", name="上传新修订", exact=True).click()
        page.locator("#document-file").set_input_files({"name": "原创完整长任务书.docx", "mimeType": mime, "buffer": revised_raw})
        expect(page.locator("#upload-error")).to_have_text("")
        with page.expect_response(lambda response: response.request.method == "PUT" and urlparse(response.url).path == f"/api/projects/{pid}/documents/{doc['id']}") as revised:
            page.locator("#document-form").get_by_role("button", name="导入资料", exact=True).click()
        assert revised.value.status == 200
        revision = revised.value.json()
        assert revision["id"] == doc["id"] and revision["revision"] == 2 and revision["sha256"] == hashlib.sha256(revised_raw).hexdigest()
        expect(page.locator("#upload-drawer")).to_be_hidden(timeout=15000)
        expect(page.locator("#reader-version")).to_contain_text("v2")
        assert len(page.request.get(f"{live_server.origin}/api/projects/{pid}/documents").json()["documents"]) == 1
        assert page.request.get(base + "/download?revision=1").body() == raw
        assert page.request.get(f"{live_server.origin}/api/projects/{pid}/requirements/{requirement['id']}").json()["source_status"] == "STALE"
        saved_run = page.request.get(f"{live_server.origin}/api/projects/{pid}/runs/{run['id']}").json()
        assert saved_run["frozen_inputs"] == run["frozen_inputs"] and saved_run["verification_status"] == run["verification_status"] == "PASSED"
        live_server.stop()
        live_server.start()
        page.reload()
        expect(page.locator("#documents")).to_contain_text("原创完整长任务书.docx")
        page.locator("#documents").get_by_role("button", name="原创完整长任务书.docx · v2", exact=True).click()
        expect(page.locator("#reader-version")).to_contain_text("v2")
        expect(page.locator("#structure-kind")).to_contain_text("Word")
        assert page.request.get(base + "/download?revision=1").body() == raw
        assert page.request.get(base + "/download?revision=2").body() == revised_raw
        proof(page, "word-reader-revision-after-restart.png")
        upload(page, "原创纯文本资料.txt", "🧩原创纯文本检索句".encode("utf-8"))
        page.locator("#keyword").fill("最终中文尾部条件")
        page.get_by_role("button", name="检索资料", exact=True).click()
        page.get_by_role("button", name="查看命中原文", exact=True).click()
        visible_source(page, "最终中文尾部条件")
        expect(page.locator("#structure-kind")).to_contain_text("Word")
        expect(page.locator("#document-assets img")).to_have_count(2)
        expect(page.locator("#document-download")).to_have_attribute("href", f"/api/projects/{pid}/documents/{doc['id']}/download?revision=2")
        page.locator("#keyword").fill("原创纯文本检索句")
        page.get_by_role("button", name="检索资料", exact=True).click()
        page.get_by_role("button", name="查看命中原文", exact=True).click()
        visible_source(page, "原创纯文本检索句")
        expect(page.locator("#reader-title")).to_have_text("原创纯文本资料.txt")
        expect(page.locator("#document-assets img")).to_have_count(0)
        expect(page.locator("#document-structure")).to_be_hidden()
        expect(page.locator("#document-warnings")).to_be_hidden()
        proof(page, "word-to-text-search-pinned-context.png")
        record_json("word-original-backend.json", {"document": doc, "revision": revision, "assets": assets, "source": source, "frozen_run_id": run["id"], "frozen_inputs_sha256": hashlib.sha256(run["frozen_inputs"].encode()).hexdigest(), "verification_status": saved_run["verification_status"], "native_scope_document_contexts": scope.json()["document_contexts"], "restart_originals_preserved": True, "requests": requests})
        assert not any(urlparse(url).netloc and urlparse(url).netloc != urlparse(live_server.origin).netloc for url in requests)
        assert not errors
        browser.close()
