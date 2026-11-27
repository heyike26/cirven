"""Independent painted/font/interaction probes; run only in the assigned GUI capture slot."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

import httpx
from PIL import Image
import pytest
from playwright.sync_api import sync_playwright, expect

from conftest import ROOT
from test_web import launch_browser


def keep(name, value):
    folder = os.environ.get("EEAS_EVIDENCE_DIR")
    if folder:
        with (Path(folder) / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")


def settle(page):
    page.wait_for_load_state("networkidle")
    page.evaluate("""async () => { await document.fonts.ready;
      await Promise.all([...document.images].filter(e=>e.src).map(e=>e.decode().catch(()=>{}))); }""")
    page.wait_for_function("""() => !document.getAnimations().some(a => {
      const t=a.effect?.getComputedTiming();return a.playState==='running' && t &&
        Number.isFinite(t.duration) && t.duration<=1500 && t.iterations!==Infinity; })""", timeout=5000)


@pytest.fixture
def glass_desk(live_server):
    with httpx.Client(base_url=live_server.origin, trust_env=False, timeout=15) as api:
        token = api.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"}).json()["csrf_token"]
        api.headers.update({"Origin": live_server.origin, "X-EEAS-CSRF": token})
        response = api.post("/api/projects", json={"name": "原创玻璃交互 核验 A", "description": "无企业与模型调用"})
        response.raise_for_status()
        yield {"origin": live_server.origin, "project": response.json(), "api": api}


@pytest.fixture
def glass_browser(glass_desk, request):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="no-preference")
        page = context.new_page()
        errors, external, fonts = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda item: external.append(item.url) if item.url.startswith(("http://", "https://"))
                and not item.url.startswith(glass_desk["origin"] + "/") else None)

        def font_response(response):
            if response.request.resource_type == "font":
                fonts.append({"url": response.url, "status": response.status,
                              "sha256": hashlib.sha256(response.body()).hexdigest()})

        page.on("response", font_response)
        yield {"page": page, "context": context, "fonts": fonts, "browser": browser}
        keep(request.node.name + "-network.json", {"page_errors": errors, "external_requests": external,
                                                  "actual_font_responses": fonts, "browser_version": browser.version})
        context.close()
        browser.close()
        assert not errors and not external


def platform_fonts(context, page, selector):
    session = context.new_cdp_session(page)
    try:
        session.send("DOM.enable")
        session.send("CSS.enable")
        document = session.send("DOM.getDocument")
        node = session.send("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": selector})
        assert node["nodeId"]
        return session.send("CSS.getPlatformFontsForNode", {"nodeId": node["nodeId"]})["fonts"]
    finally:
        session.detach()


def snapshot(page, name):
    folder = os.environ.get("EEAS_EVIDENCE_DIR")
    raw = page.screenshot(full_page=False)  # Ordinary paint; do not fast-forward illustration animations.
    if folder:
        with (Path(folder) / name).open("xb") as stream:
            stream.write(raw)
    return raw


def luminance(rgb):
    values = [value / 255 for value in rgb]
    return sum(weight * (value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4)
               for value, weight in zip(values, (.2126, .7152, .0722)))


def trace_actual_motion(page, label):
    """Observe real rAF/WAAPI state after an action; never wait for a desired result."""
    trace = page.evaluate("""async () => {const frames=[];
      for(let i=0;i<12;i++){await new Promise(resolve=>requestAnimationFrame(resolve));
        const e=document.querySelector('#nav-active-indicator'),r=e?.getBoundingClientRect();
        frames.push({time:performance.now(),effectiveReduce:document.body.classList.contains('reduce-motion'),
          indicator:r?{x:r.x,y:r.y,width:r.width,height:r.height}:null,
          animations:document.getAnimations().map(a=>{const t=a.effect?.getComputedTiming(),target=a.effect?.target;
            return {state:a.playState,target:target?.id||target?.className||null,
              duration:Number.isFinite(t?.duration)?t.duration:String(t?.duration),
              infiniteIterations:t?.iterations===Infinity,
              currentTime:Number.isFinite(a.currentTime)?a.currentTime:null};})});}
      return frames;}""")
    keep(label + "-actual-motion.json", {"method": "12 actual requestAnimationFrame samples; no desired-state polling", "frames": trace})
    return trace


def assert_no_long_running_motion(trace):
    assert not any(animation["state"] == "running" and
                   (animation["infiniteIterations"] or not isinstance(animation["duration"], (int, float))
                    or animation["duration"] >= 80)
                   for frame in trace for animation in frame["animations"]), trace


def painted_contrast(page, selector, label):
    element = page.locator(selector).first
    element.scroll_into_view_if_needed()
    expect(element).to_be_visible()
    details = element.evaluate("""e => {const c=getComputedStyle(e),r=e.getBoundingClientRect();
      const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
      const ctx=canvas.getContext('2d');ctx.fillStyle=c.color;ctx.fillRect(0,0,1,1);
      const ancestors=[];for(let n=e;n;n=n.parentElement){const s=getComputedStyle(n);
        ancestors.push({opacity:s.opacity,filter:s.filter,blend:s.mixBlendMode});}
      return {rect:{x:r.x,y:r.y,width:r.width,height:r.height},color:[...ctx.getImageData(0,0,1,1).data],ancestors,
        family:c.fontFamily,size:parseFloat(c.fontSize),weight:c.fontWeight,
        oldColor:e.style.getPropertyValue('color'),oldPriority:e.style.getPropertyPriority('color'),
        oldShadow:e.style.getPropertyValue('text-shadow'),shadowPriority:e.style.getPropertyPriority('text-shadow')}; }""")
    snapshot(page, label + "-paint.png")
    try:
        element.evaluate("e => {e.style.setProperty('color','transparent','important');e.style.setProperty('text-shadow','none','important');}")
        background = Image.open(io.BytesIO(snapshot(page, label + "-background.png"))).convert("RGB")
    finally:
        element.evaluate("""(e,v)=>{for(const [key,value,priority] of [['color',v.oldColor,v.oldPriority],
          ['text-shadow',v.oldShadow,v.shadowPriority]]) {if(value)e.style.setProperty(key,value,priority);else e.style.removeProperty(key);}}""", details)
    rect, foreground = details["rect"], details["color"]
    ratios = []
    for x_fraction in (.1, .5, .9):
        for y_fraction in (.15, .5, .85):
            x = min(background.width - 1, max(0, round(rect["x"] + rect["width"] * x_fraction)))
            y = min(background.height - 1, max(0, round(rect["y"] + rect["height"] * y_fraction)))
            pixel = background.getpixel((x, y))
            alpha = foreground[3] / 255
            actual_foreground = [alpha * foreground[index] + (1 - alpha) * pixel[index] for index in range(3)]
            pair = sorted((luminance(pixel), luminance(actual_foreground)))
            ratios.append((pair[1] + .05) / (pair[0] + .05))
    result = {"selector": selector, "actual_text_style": details, "painted_background_sample_contrast": ratios,
              "method": "computed text color over actual screenshot pixels with this text temporarily transparent; ordinary paint restored; unsupported ancestor compositing fails instead of claiming contrast qualification"}
    keep(label + "-contrast.json", result)
    assert all(float(item["opacity"]) == 1 and item["filter"] == "none" and item["blend"] == "normal"
               for item in details["ancestors"]), result
    weight = {"normal": 400, "bold": 700}.get(details["weight"], details["weight"])
    required = 3 if details["size"] >= 24 or (details["size"] >= 18.66 and int(weight) >= 700) else 4.5
    assert min(ratios) >= required, result
    return result


def test_local_font_requests_and_actual_chinese_latin_glyph_families(glass_desk, glass_browser):
    page, context = glass_browser["page"], glass_browser["context"]
    page.goto(glass_desk["origin"])
    settle(page)
    used = {selector: platform_fonts(context, page, selector) for selector in ("#view-title", ".brand")}
    faces = page.evaluate("() => [...document.fonts].map(f=>({family:f.family,status:f.status,weight:f.weight}))")
    keep("actual-rendered-ui-fonts.json", {"rendered_fonts": used, "loaded_faces": faces,
                                          "computed_family": page.locator("body").evaluate("e=>getComputedStyle(e).fontFamily")})
    assert faces and all(any(font["glyphCount"] > 0 for font in value) for value in used.values())
    assert all(font["isCustomFont"] for value in used.values() for font in value if font["glyphCount"] > 0)
    assert glass_browser["fonts"]
    for response in glass_browser["fonts"]:
        assert response["status"] == 200 and response["url"].startswith(glass_desk["origin"] + "/")
        relative = Path(unquote(urlparse(response["url"]).path).lstrip("/"))
        assert ".." not in relative.parts
        original = ROOT / "apps/web" / relative
        assert original.is_file() and hashlib.sha256(original.read_bytes()).hexdigest() == response["sha256"]
    snapshot(page, "actual-local-font-home.png")
    page.locator('[data-view="library"]').click()
    trace_actual_motion(page, "glass-ordinary-navigation")
    settle(page)
    snapshot(page, "glass-ordinary-library.png")


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 540)])
def test_glass_readability_uses_real_painted_background_on_short_and_wide_views(glass_desk, glass_browser, width, height):
    page = glass_browser["page"]
    page.set_viewport_size({"width": width, "height": height})
    page.goto(glass_desk["origin"] + "/?view=settings")
    settle(page)
    painted_contrast(page, "#view-title", f"glass-heading-{width}")
    painted_contrast(page, "#settings-zone .settings-note", f"glass-body-{width}")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+1")


def test_glass_upload_backend_error_is_readable_focused_and_escape_restores_trigger(glass_desk, glass_browser):
    page = glass_browser["page"]
    page.set_viewport_size({"width": 390, "height": 540})
    page.goto(glass_desk["origin"] + "/?view=library&project=" + glass_desk["project"]["id"])
    settle(page)
    trigger = page.locator("#library-upload")
    trigger.click()
    expect(page.locator("#upload-drawer")).to_be_visible()
    page.locator("#document-file").set_input_files({"name": "original-invalid.png", "mimeType": "image/png",
                                                   "buffer": b"original deliberately invalid PNG, not an image"})
    page.locator("#document-form button[type=submit]").click()
    error = page.locator("#upload-error")
    expect(error).not_to_have_text("")
    expect(error).to_be_focused()
    painted_contrast(page, "#upload-error", "glass-upload-error")
    assert glass_desk["api"].get(f"/api/projects/{glass_desk['project']['id']}/documents").json()["documents"] == []
    page.keyboard.press("Escape")
    expect(page.locator("#upload-drawer")).not_to_be_visible()
    expect(trigger).to_be_focused()


def test_opaque_fallback_fixture_has_no_rendered_backdrop_filter_and_keeps_readability(glass_desk, glass_browser):
    page = glass_browser["page"]
    transformations = []

    def unsupported_filter(route):
        response = route.fetch()
        original = response.text()
        replaced = re.sub(r"@supports[^{}]+", lambda match: match.group().replace("backdrop-filter", "eeas-probe-unsupported-filter"), original)
        replaced += "\n*,*::before,*::after{backdrop-filter:none!important;-webkit-backdrop-filter:none!important}\n"
        transformations.append({"original_css_sha256": hashlib.sha256(original.encode()).hexdigest(),
                                "fixture_css_sha256": hashlib.sha256(replaced.encode()).hexdigest(),
                                "method": "test-only feature-support condition substitution plus disabled blur; NOT_NATIVE, no product files edited"})
        route.fulfill(response=response, body=replaced)

    page.route("**/assets/*.css*", unsupported_filter)
    page.add_init_script("""(() => {const original=CSS.supports.bind(CSS);CSS.supports=(...args)=>
      args.some(v=>String(v).includes('backdrop-filter'))?false:original(...args);})();""")
    page.goto(glass_desk["origin"] + "/?view=settings")
    settle(page)
    observed = page.evaluate("""() => ['.sidebar','.context-bar','#settings-zone .settings-panel'].map(s=>{
      const e=document.querySelector(s),c=getComputedStyle(e);return {selector:s,filter:c.backdropFilter,
        webkit:c.webkitBackdropFilter,background:c.backgroundColor,image:c.backgroundImage};})""")
    keep("glass-fallback-simulation.json", {"qualification": "NOT_NATIVE_FEATURE_ABSENCE_FIXTURE",
                                          "transformations": transformations, "actual_rendered_surfaces": observed})
    assert transformations and all(item["filter"] == "none" for item in observed)
    assert any(item["background"].startswith("rgb(") for item in observed), observed
    painted_contrast(page, "#view-title", "fallback-heading")
    painted_contrast(page, "#settings-zone .settings-note", "fallback-body")


def test_os_and_manual_reduce_cancel_real_glass_motion_with_native_event_sync(glass_desk, glass_browser):
    page = glass_browser["page"]
    page.goto(glass_desk["origin"] + "/?view=settings")
    settle(page)
    switch = page.locator("#reduce-motion")
    switch.focus()
    switch.press("Space")
    expect(switch).to_be_checked()
    assert page.evaluate("document.body.classList.contains('reduce-motion')")
    page.locator('[data-view="library"]').click()
    manual_trace = trace_actual_motion(page, "glass-manual-reduced-navigation")
    assert_no_long_running_motion(manual_trace)
    page.locator('[data-view="settings"]').click()
    settle(page)
    expect(switch).to_be_checked()
    switch.press("Space")
    expect(switch).not_to_be_checked()
    page.evaluate("""() => {const media=matchMedia('(prefers-reduced-motion: reduce)');
      window.__glassNativeMediaChange=new Promise((resolve,reject)=>{const done=e=>{clearTimeout(timer);resolve({matches:e.matches,trusted:e.isTrusted});};
        const timer=setTimeout(()=>{media.removeEventListener('change',done);reject(Error('native media event timeout'));},1000);
        media.addEventListener('change',done,{once:true});});}""")
    page.emulate_media(reduced_motion="reduce")
    actual_event = page.evaluate("() => window.__glassNativeMediaChange")
    assert actual_event == {"matches": True, "trusted": True}
    expect(switch).to_be_checked()
    expect(switch).to_be_disabled()
    page.locator('[data-view="library"]').click()
    native_trace = trace_actual_motion(page, "glass-native-reduced-navigation")
    keep("glass-effective-reduced-motion.json", {"native_event": actual_event, "manual_frames": manual_trace,
                                                "native_frames": native_trace})
    assert_no_long_running_motion(native_trace)
    settle(page)
    snapshot(page, "glass-os-reduced-library.png")
