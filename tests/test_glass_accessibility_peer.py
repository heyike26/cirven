"""Independent new glass boundary cases; execute only in Root's assigned GUI slot."""
import hashlib
import json
import re
from urllib.parse import urlparse

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from test_glass_peer_browser import (
    glass_browser, glass_desk, keep, painted_contrast, settle, snapshot,
)
from test_web import launch_browser


@pytest.fixture
def empty_glass_workspace(live_server, request):
    """A genuinely empty original database, with no DOM-only fake empty state."""
    with httpx.Client(base_url=live_server.origin, trust_env=False, timeout=15) as api:
        token = api.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"}).json()["csrf_token"]
        api.headers.update({"Origin": live_server.origin, "X-EEAS-CSRF": token})
        assert api.get("/api/projects").json()["projects"] == []
        with sync_playwright() as playwright:
            browser = launch_browser(playwright)
            context = browser.new_context(reduced_motion="no-preference")
            page = context.new_page()
            errors, external = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda item: external.append(item.url)
                    if item.url.startswith(("http://", "https://"))
                    and not item.url.startswith(live_server.origin + "/") else None)
            try:
                yield {"page": page, "context": context, "api": api, "origin": live_server.origin}
            finally:
                keep(request.node.name + "-empty-network.json", {
                    "page_errors": errors, "external_requests": external, "browser_version": browser.version,
                })
                context.close()
                browser.close()
            assert not errors and not external


def surface_and_bounds(element):
    return element.evaluate("""e => {const c=getComputedStyle(e),r=e.getBoundingClientRect();
      const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
      const ctx=canvas.getContext('2d');ctx.fillStyle=c.backgroundColor;ctx.fillRect(0,0,1,1);
      return {id:e.id,rect:{x:r.x,y:r.y,width:r.width,height:r.height},
        background:c.backgroundColor,backgroundRGBA:[...ctx.getImageData(0,0,1,1).data],
        filter:c.backdropFilter,webkitFilter:c.webkitBackdropFilter??null,
        outlineStyle:c.outlineStyle,outlineWidth:parseFloat(c.outlineWidth)};}""")


def real_ax_node(context, page, selector):
    session = context.new_cdp_session(page)
    try:
        session.send("DOM.enable")
        document = session.send("DOM.getDocument")
        node = session.send("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": selector})
        assert node["nodeId"]
        return session.send("Accessibility.getPartialAXTree", {
            "nodeId": node["nodeId"], "fetchRelatives": False,
        })["nodes"]
    finally:
        session.detach()


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 540)])
def test_no_color_mix_and_no_backdrop_real_dialog_error_bounds_and_escape(
        glass_desk, glass_browser, width, height):
    page = glass_browser["page"]
    page.set_viewport_size({"width": width, "height": height})
    transformed, document_posts = [], []

    def feature_absence(route):
        response = route.fetch()
        original = response.body()
        modified = re.sub(r"\bcolor-mix\s*\(", "eeas-probe-unsupported-color-mix(",
                          original.decode("utf-8"), flags=re.IGNORECASE)
        modified = re.sub(r"@supports[^{}]+", lambda match: match.group().replace(
            "backdrop-filter", "eeas-probe-unsupported-filter"), modified)
        modified += "\n*,*::before,*::after{backdrop-filter:none!important;-webkit-backdrop-filter:none!important}\n"
        transformed.append({"path": urlparse(response.url).path,
                            "original_sha256": hashlib.sha256(original).hexdigest(),
                            "fixture_sha256": hashlib.sha256(modified.encode("utf-8")).hexdigest()})
        route.fulfill(response=response, body=modified)

    page.route("**/assets/*.css*", feature_absence)
    page.add_init_script("""(() => {const native=CSS.supports.bind(CSS);CSS.supports=(...args)=>
      args.some(value=>/backdrop-filter|color-mix/i.test(String(value)))?false:native(...args);})();""")
    endpoint = f"/api/projects/{glass_desk['project']['id']}/documents"

    def response_evidence(response):
        if response.request.method == "POST" and urlparse(response.url).path == endpoint:
            raw = response.body()
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                body = {"raw_text": raw.decode("utf-8", errors="replace")}
            document_posts.append({"status": response.status, "body": body,
                                   "response_sha256": hashlib.sha256(raw).hexdigest()})
            keep(f"joint-fallback-post-{width}-{len(document_posts)}.json", document_posts[-1])

    page.on("response", response_evidence)
    page.goto(glass_desk["origin"] + "/?view=library&project=" + glass_desk["project"]["id"])
    settle(page)
    trigger = page.locator("#library-upload")
    trigger.click()
    dialog = page.get_by_role("dialog", name="上传资料", exact=True)
    expect(dialog).to_be_visible()
    page.locator("#document-source").fill(
        "原创PNG解码拒绝夹具；中文空格目录中的机械控制接口资料，文件与来源均不包含企业数据。")
    page.locator("#document-file").set_input_files({
        "name": "机械控制接口 — 原始证据 第03修订 (人工核对) 无效图像.png",
        "mimeType": "image/png", "buffer": b"original deliberately invalid PNG bytes; not a valid image",
    })
    dialog_surface = surface_and_bounds(dialog)
    keep(f"joint-fallback-initial-surface-{width}.json", {
        "qualification": "NOT_NATIVE_SIMULTANEOUS_FEATURE_ABSENCE_FIXTURE",
        "transformed_css": transformed, "dialog_surface": dialog_surface,
    })
    snapshot(page, f"joint-fallback-initial-dialog-{width}.png")
    assert dialog_surface["filter"] == "none" and dialog_surface["backgroundRGBA"][3] == 255
    assert dialog_surface["webkitFilter"] in (None, "", "none")
    fields = []
    for index, selector in enumerate(("#choose-document-file", "#document-source", "#document-form button[type=submit]", "#close-upload")):
        control = page.locator(selector)
        control.scroll_into_view_if_needed()
        expect(control).to_be_visible()
        observed = surface_and_bounds(control)
        outer = surface_and_bounds(dialog)["rect"]
        rect = observed["rect"]
        keep(f"joint-fallback-field-{index}-{width}.json", {"selector": selector, "observed": observed, "dialog_bounds": outer})
        assert rect["x"] >= outer["x"] - 1 and rect["x"] + rect["width"] <= outer["x"] + outer["width"] + 1
        assert rect["y"] >= -1 and rect["y"] + rect["height"] <= height + 1
        if selector == "#document-source":
            assert observed["backgroundRGBA"][3] == 255
        fields.append({"selector": selector, "observed": observed, "dialog_bounds": outer})
    snapshot(page, f"joint-fallback-dialog-{width}.png")
    page.locator("#document-form button[type=submit]").click()
    error = page.locator("#upload-error")
    expect(error).not_to_have_text("")
    expect(error).to_be_focused()
    painted_contrast(page, "#upload-error", f"joint-fallback-upload-error-{width}")
    assert len(document_posts) == 1 and document_posts[0]["status"] == 422, document_posts
    assert glass_desk["api"].get(endpoint).json()["documents"] == []
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()
    paths = {record["path"] for record in transformed}
    assert {"/assets/app.css", "/assets/glass-shell.css", "/assets/glass-components.css"} <= paths
    keep(f"joint-fallback-{width}.json", {
        "qualification": "NOT_NATIVE_SIMULTANEOUS_FEATURE_ABSENCE_FIXTURE",
        "method": "test HTTP CSS response substitution only; actual rendered surfaces, real API error and focus; product files unchanged",
        "transformed_css": transformed, "dialog_surface": dialog_surface, "visible_fields": fields,
        "actual_document_posts": document_posts, "persisted_documents": [],
    })


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 540)])
def test_real_named_window_links_ax_and_keyboard_focus(glass_desk, glass_browser, width, height):
    page, context = glass_browser["page"], glass_browser["context"]
    page.set_viewport_size({"width": width, "height": height})
    page.goto(glass_desk["origin"] + "/?view=settings")
    settle(page)
    targets = {"open-workbench": "打开工作台窗口", "open-view-window": "在新窗口打开此视图"}
    ax = {}
    for identifier, name in targets.items():
        nodes = real_ax_node(context, page, "#" + identifier)
        ax[identifier] = nodes
        keep(f"named-window-link-ax-{identifier}-{width}.json", {"expected_name": name, "actual_ax_nodes": nodes})
        link = page.get_by_role("link", name=name, exact=True)
        expect(link).to_be_visible()
        assert link.get_attribute("id") == identifier
        visible_links = [node for node in nodes if not node.get("ignored") and node.get("role", {}).get("value") == "link"]
        assert len(visible_links) == 1 and visible_links[0].get("name", {}).get("value") == name, nodes
    seen, focused_surfaces = [], {}
    for _ in range(16):
        page.keyboard.press("Tab")
        identifier = page.evaluate("document.activeElement?.id || ''")
        seen.append(identifier)
        if identifier in targets:
            observed = surface_and_bounds(page.locator("#" + identifier))
            snapshot(page, f"named-window-link-{identifier}-{width}.png")
            keep(f"named-window-link-focus-{identifier}-{width}.json", observed)
            assert observed["outlineStyle"] != "none" and observed["outlineWidth"] >= 2
            rect = observed["rect"]
            assert rect["x"] >= -1 and rect["x"] + rect["width"] <= width + 1
            assert rect["y"] >= -1 and rect["y"] + rect["height"] <= height + 1
            focused_surfaces[identifier] = observed
        if set(targets) <= set(focused_surfaces):
            break
    keep(f"named-window-links-{width}.json", {
        "real_ax_nodes": ax, "actual_tab_sequence": seen, "focused_surfaces": focused_surfaces,
        "scope": "accessible names/reachability/focus only; existing real-popup tests retain run/admission assertions",
    })
    assert set(targets) <= set(focused_surfaces), seen


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 540)])
def test_real_zero_project_home_and_unselected_prompts_painted_contrast(empty_glass_workspace, width, height):
    desk = empty_glass_workspace
    page = desk["page"]
    page.set_viewport_size({"width": width, "height": height})
    page.goto(desk["origin"])
    settle(page)
    expect(page.locator("#projects-empty-help")).to_be_visible()
    painted_contrast(page, "#projects-empty-help", f"zero-project-home-{width}")
    assert desk["api"].get("/api/projects").json()["projects"] == []
    measured = []
    for view, section in (("library", "library-zone"), ("requirements", "requirements-zone"),
                          ("tasks", "tasks-zone"), ("approvals", "approvals-zone")):
        page.goto(desk["origin"] + "/?view=" + view)
        settle(page)
        selector = "#" + section + " .project-prompt"
        expect(page.locator(selector)).to_be_visible()
        measured.append(painted_contrast(page, selector, f"unselected-prompt-{view}-{width}"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")
    assert desk["api"].get("/api/projects").json()["projects"] == []
    keep(f"actual-empty-views-{width}.json", {
        "actual_project_count": 0, "measured_prompts": measured,
        "scope": "selected text colors over nine actual painted background samples; no whole-site WCAG or user aesthetic certification",
    })
