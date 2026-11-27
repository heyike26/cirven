"""Original UI contract migration, with real API and browser interactions.

These cases do not change historical manual-reduce assertions or qualify
Windows Electron, upstream OpenCode UI, enterprise models or aesthetics.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile

from playwright.sync_api import expect, sync_playwright

from test_extensions import archive
from test_web import launch_browser


def evidence(page, name, value=None):
    if not os.environ.get("EEAS_EVIDENCE_DIR"):
        return
    target = Path(os.environ["EEAS_EVIDENCE_DIR"])
    if value is not None:
        (target / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    page.evaluate("async () => { await document.fonts.ready; await Promise.all(document.getAnimations().map(a => a.finished.catch(() => {}))); }")
    page.screenshot(path=str(target / (name + ".png")), full_page=True)


def route(page, view):
    if page.locator("body").evaluate("e => e.classList.contains('navigation-hidden')"):
        page.locator("#navigation-reveal").click()
    page.locator(f"nav [data-view='{view}']").click()
    expect(page.locator(f"[data-zone='{view}']")).to_be_visible()


def headers(page, origin):
    response = page.request.get(origin + "/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
    assert response.ok, response.text()
    return {"Origin": origin, "X-EEAS-CSRF": response.json()["csrf_token"]}


def style_state(button):
    return button.evaluate("""button => {
      const color = value => value.match(/[\\d.]+/g).map(Number);
      const luminance = rgb => rgb.slice(0,3).map(x => x/255).map(x => x<=.04045 ? x/12.92 : ((x+.055)/1.055)**2.4).reduce((sum,x,i) => sum+x*[.2126,.7152,.0722][i],0);
      const background = color(getComputedStyle(button).backgroundColor);
      const bg = luminance(background);
      const text = [...button.querySelectorAll('strong,span,b')].map(node => {
        const computed = getComputedStyle(node); const fg=luminance(color(computed.color));
        return {text:node.textContent,color:computed.color,contrast:(Math.max(bg,fg)+.05)/(Math.min(bg,fg)+.05)};
      });
      return {background, text, hover:button.matches(':hover'),focusVisible:button.matches(':focus-visible'),active:button.matches(':active')};
    }""")


def test_workflow_hover_keyboard_focus_and_press_have_readable_matched_colors(live_server):
    with sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_server.origin)
        expect(page.locator("#home-project-count")).to_have_text("0")
        first = page.locator(".workspace-flow > button").first
        first.hover()
        hover = style_state(first)
        assert hover["hover"] and hover["background"][:3] == [245, 246, 247], hover
        assert all(row["contrast"] >= 4.5 for row in hover["text"]), hover
        page.mouse.down()
        active = style_state(first)
        assert active["active"] and all(row["contrast"] >= 4.5 for row in active["text"]), active
        page.mouse.up()
        expect(page.locator("#library-zone")).to_be_visible()
        route(page, "projects")
        first.focus()
        page.keyboard.press("Tab")
        second = page.locator(".workspace-flow > button").nth(1)
        expect(second).to_be_focused()
        focused = style_state(second)
        assert focused["focusVisible"] and all(row["contrast"] >= 4.5 for row in focused["text"]), focused
        evidence(page, "workflow-keyboard-colors", {"hover": hover, "active": active, "focus": focused})
        assert not errors
        browser.close()


def test_extension_chinese_real_chooser_open_layout_and_import_only_register_candidate(live_server):
    with sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors, mutations = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: mutations.append({"url": request.url, "method": request.method}) if request.method == "POST" else None)
        page.goto(live_server.origin)
        route(page, "capabilities")
        expect(page.locator("#extension-state")).to_contain_text("尚无本地扩展")
        for panel in page.locator("#capabilities-zone .extension-layout > div").all():
            assert panel.evaluate("e => getComputedStyle(e).backgroundColor") == "rgba(0, 0, 0, 0)"
        chooser = page.locator("#choose-extension-file")
        expect(chooser).to_have_text("选择 ZIP 文件")
        expect(page.locator("#extension-import-form [type=submit]")).to_be_disabled()
        chooser.focus()
        with page.expect_file_chooser() as chosen:
            page.keyboard.press("Enter")
        chosen.value.set_files({"name": "不是插件.txt", "mimeType": "text/plain", "buffer": b"original invalid format"})
        expect(page.locator("#extension-file-error")).to_contain_text("请选择 ZIP")
        assert not mutations
        with page.expect_file_chooser() as chosen:
            chooser.click()
        chosen.value.set_files({"name": "超过大小.zip", "mimeType": "application/zip", "buffer": b"x" * (1048576 + 1)})
        expect(page.locator("#extension-file-error")).to_contain_text("超过 1 MiB")
        assert not mutations
        raw = archive(1)
        filename = "原创本地扩展-" + "长文件名用于检验换行" * 5 + ".zip"
        with page.expect_file_chooser() as chosen:
            chooser.click()
        chosen.value.set_files({"name": filename, "mimeType": "application/zip", "buffer": raw})
        expect(page.locator("#extension-file-name")).to_have_text(filename)
        expect(page.locator("#extension-file-error")).to_be_empty()
        page.locator("#extension-import-form [type=submit]").click()
        expect(page.locator("#extensions > li")).to_have_count(1)
        expect(page.locator("#extension-file-name")).to_have_text("尚未选择文件")
        registered = page.request.get(live_server.origin + "/api/extensions").json()["extensions"]
        assert len(registered) == 1
        candidate = registered[0]
        assert candidate["channel"] == "CANDIDATE" and not candidate["qualified"] and not candidate["loaded"]
        with zipfile.ZipFile(io.BytesIO(raw)) as original:
            expected_files = {name: hashlib.sha256(original.read(name)).hexdigest() for name in original.namelist()}
        assert candidate["files_sha256"] == expected_files
        expect(page.locator("#extensions .extension-facts")).to_contain_text("已加载：否 · 资格通过：否")
        assert mutations == [{"url": live_server.origin + "/api/extensions", "method": "POST"}], mutations
        evidence(page, "extension-open-desktop", {"registered": registered, "raw_zip_sha256": hashlib.sha256(raw).hexdigest(), "mutations": mutations})
        page.set_viewport_size({"width": 390, "height": 540})
        expect(page.locator("#choose-extension-file")).to_be_visible()
        page.locator("#extension-file").set_input_files({"name": filename, "mimeType": "application/zip", "buffer": raw})
        expect(page.locator("#extension-file-name")).to_have_text(filename)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "extension name/control overflow"
        evidence(page, "extension-open-mobile")
        assert not errors
        browser.close()


def test_new_view_and_reload_start_stably_while_document_scroll_is_independent(live_server):
    with sync_playwright() as p:
        browser = launch_browser(p)
        context = browser.new_context(viewport={"width": 1440, "height": 540})
        page = context.new_page()
        page.goto(live_server.origin)
        h = headers(page, live_server.origin)
        response = page.request.post(live_server.origin + "/api/projects", headers=h, data={"name": "原创滚动策略", "description": "页面与原文的独立滚动"})
        assert response.ok, response.text()
        pid = response.json()["id"]
        raw = ("\n".join(f"原文第 {i} 行：" + "source body " * 10 for i in range(1000)) + "\n").encode()
        document = page.request.post(f"{live_server.origin}/api/projects/{pid}/documents", headers=h, multipart={"file": {"name": "滚动原文.txt", "mimeType": "text/plain", "buffer": raw}, "source": "原创滚动夹具"})
        assert document.ok, document.text()
        page.goto(f"{live_server.origin}/?view=projects&project={pid}")
        expect(page.locator("#home-document-count")).to_have_text("1")
        page.mouse.move(500, 440)
        page.mouse.wheel(0, 700)
        page.wait_for_function("scrollY > 150")
        route(page, "library")
        page.wait_for_function("scrollY === 0")
        page.locator("#documents").get_by_role("button", name="滚动原文.txt · v1", exact=True).click()
        expect(page.locator("#reader-title")).to_have_text("滚动原文.txt")
        expect(page.locator("#document-content")).to_contain_text("原文第 0 行")
        reader = page.locator(".reader-paper")
        reader.scroll_into_view_if_needed()
        box = reader.bounding_box()
        before = page.evaluate("scrollY")
        page.mouse.move(box["x"] + box["width"] / 2, min(box["y"] + 60, 510))
        page.mouse.wheel(0, 400)
        page.wait_for_function("document.querySelector('.reader-paper').scrollTop > 0")
        assert page.evaluate("scrollY") == before
        route(page, "projects")
        page.wait_for_function("scrollY === 0")
        page.mouse.move(500, 440)
        page.mouse.wheel(0, 700)
        page.wait_for_function("scrollY > 150")
        page.reload()
        expect(page.locator("#home-document-count")).to_have_text("1")
        assert page.evaluate("scrollY") == 0
        assert page.evaluate("history.scrollRestoration") == "manual"
        window = context.new_page()
        window.goto(page.url)
        expect(window.locator("#home-document-count")).to_have_text("1")
        assert window.evaluate("scrollY") == 0
        current = page.request.get(f"{live_server.origin}/api/projects/{pid}/documents").json()["documents"]
        assert len(current) == 1 and current[0]["sha256"] == hashlib.sha256(raw).hexdigest()
        evidence(page, "view-start-scroll", {"document_sha256": current[0]["sha256"], "reader_outer_scroll_y": before, "reload_scroll_y": page.evaluate("scrollY"), "new_window_scroll_y": window.evaluate("scrollY")})
        browser.close()


def test_manual_reduce_removed_default_real_motion_and_trusted_os_preference(live_server):
    with sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="no-preference")
        page.add_init_script("""(() => {
          const original=Element.prototype.animate;window.actualAnimations=[];
          Element.prototype.animate=function(...args) {
            const animation=original.apply(this,args);
            window.actualAnimations.push({target:this.id,duration:animation.effect.getTiming().duration,active:this.getAnimations().includes(animation)});
            return animation;
          };
        })();""")
        page.goto(live_server.origin)
        expect(page.locator("#home-project-count")).to_have_text("0")
        route(page, "settings")
        expect(page.locator("#reduce-motion")).to_have_count(0)
        expect(page.locator("#motion-preference-note")).to_contain_text("完整动效已启用")
        normal = page.evaluate("actualAnimations.filter(a => a.target === 'settings-zone')")
        assert normal and all(row["active"] and row["duration"] > 0 for row in normal), normal
        page.evaluate("""() => {
          window.actualOSChange=new Promise((resolve,reject) => {
            const delivery=setTimeout(() => reject(new Error('OS preference event not delivered within 1000ms')),1000);
            matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',e => {clearTimeout(delivery);resolve({trusted:e.isTrusted,matches:e.matches});},{once:true});
          });
        }""")
        page.emulate_media(reduced_motion="reduce")
        changed = page.evaluate("actualOSChange")
        assert changed == {"trusted": True, "matches": True}, changed
        expect(page.locator("#motion-preference-note")).to_contain_text("静态反馈")
        assert page.evaluate("document.getAnimations().filter(a => a.playState === 'running' || a.playState === 'pending').length") == 0
        baseline = page.evaluate("actualAnimations.length")
        route(page, "projects")
        first = page.locator(".workspace-flow > button").first
        first.hover()
        assert first.locator(".icon").evaluate("e => getComputedStyle(e).transform") == "none"
        first.click()
        expect(page.locator("#library-zone")).to_be_visible()
        assert page.evaluate("actualAnimations.length") == baseline
        evidence(page, "motion-policy-migration", {"normal_native_WAAPI": normal, "trusted_os_change": changed})
        browser.close()
