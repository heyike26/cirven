"""Real Quick HTML + bundled preload; fake only Electron IPC transport. Zero model."""
import hashlib
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[3]
output = Path(sys.argv[1]).resolve()
states = json.loads((output / "states.json").read_text())
bundle = (root / "apps/desktop-host/quick-surface-preload.bundle.cjs").read_text()
transport = """
(() => {
 const initial=INITIAL_STATE;let listener,waiting;
 window.__qa={requests:[],publish:value=>listener?.(null,value),release:()=>waiting?.(),state:initial};
 window.require=name=>{if(name!=='electron')throw Error('Unexpected external '+name);return {ipcRenderer:{
  on:(_name,fn)=>{listener=fn;},invoke:async(_name,payload)=>{
   __qa.requests.push(payload);
   if(payload.kind==='state')return initial;
   if(payload.kind==='review-selection')return new Promise(resolve=>{waiting=resolve;});
   return {};
  }}};};
})();
"""

def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()

report = {"schema": "cirven.quick-reply-browser.v1", "model_calls": 0, "cases": []}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
    for locale, width, height in [("zh-CN", 380, 500), ("zh-CN", 320, 390), ("en", 520, 500)]:
        state = {**states["completed"], "locale": locale}
        context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1)
        page = context.new_page()
        page.set_default_timeout(7000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(transport.replace("INITIAL_STATE", json.dumps(state)) + bundle)
        page.goto((output / (locale + ".html")).as_uri())
        page.wait_for_selector("#reply table")
        page.wait_for_timeout(280)
        geometry = page.evaluate("""() => {
         const r=document.querySelector('#reply'),f=document.querySelector('.result-actions'),rect=f.getBoundingClientRect();
         return {font:getComputedStyle(r).fontSize,line:getComputedStyle(r).lineHeight,
          tables:r.querySelectorAll('table').length,headings:r.querySelectorAll('h3,h4,h5,h6').length,
          bodyOverflow:document.body.scrollWidth>innerWidth,replyHeight:r.clientHeight,
          replyScrollable:r.scrollHeight>r.clientHeight,footerInside:rect.top>=0&&rect.bottom<=innerHeight,
          rawTableSyntax:r.textContent.includes('|---'),tail:r.textContent.slice(-64)};
        }""")
        assert geometry["tables"] >= 1 and geometry["headings"] == 3, geometry
        assert geometry["font"] == "14px" and not geometry["bodyOverflow"], geometry
        assert geometry["footerInside"] and geometry["replyHeight"] >= 100 and geometry["replyScrollable"], geometry
        assert not geometry["rawTableSyntax"] and not errors, errors
        name = f"{locale}-{width}x{height}"
        page.screenshot(path=str(output / (name + "-top.png")))
        preserved = page.evaluate("""() => {
         const r=document.querySelector('#reply');r.focus();r.scrollTop=100;
         const n=r.querySelector('p').firstChild,range=document.createRange();range.setStart(n,0);range.setEnd(n,Math.min(8,n.length));
         getSelection().removeAllRanges();getSelection().addRange(range);
         window.__before={node:r.firstChild,scroll:r.scrollTop,quote:getSelection().toString()};
         __qa.publish({...__qa.state,notice:'fixture receipt observation'});
         return {same:r.firstChild===__before.node,scroll:r.scrollTop===__before.scroll,
          selection:getSelection().toString()===__before.quote,focus:document.activeElement===r};
        }""")
        assert all(preserved.values()), preserved
        page.locator("#reply").press("Control+End")
        page.wait_for_function("()=>{const el=document.querySelector('#reply');return el.scrollTop+el.clientHeight>=el.scrollHeight-2;}")
        page.screenshot(path=str(output / (name + "-end.png")))
        page.locator('[data-command="expand"]').focus()
        page.keyboard.press("Enter")
        assert page.evaluate("__qa.requests.filter(x=>x.command==='expand').length") == 1
        assert page.evaluate("__qa.requests.filter(x=>x.command==='send').length") == 0
        report["cases"].append({"name": name, "geometry": geometry, "same_reply_preservation": preserved, "errors": errors})
        context.close()

    # Historical reply is a read-only view; actual preload flushes its editor
    # before switching and restores the same textarea selection/scroll/focus.
    state = {**states["editing"], "visible": True}
    context = browser.new_context(viewport={"width": 380, "height": 500}, device_scale_factor=1)
    page = context.new_page()
    page.add_init_script(transport.replace("INITIAL_STATE", json.dumps(state)) + bundle)
    page.goto((output / "zh-CN.html").as_uri())
    page.wait_for_selector("#comment:not([disabled])")
    page.locator("#comment").fill("原Quick草稿保留\n" * 30 + "最后用户输入")
    before = page.evaluate("""() => {
      const el=document.querySelector('#comment');el.focus();el.setSelectionRange(3,11);el.scrollTop=55;
      window.__originalEditor=el;window.__originalView={value:el.value,start:el.selectionStart,end:el.selectionEnd,scroll:el.scrollTop};
      return __originalView;
    }""")
    page.evaluate("""() => __qa.publish({kind:'flush-request',id:'fixture-flush',epoch:__qa.state.epoch,draftId:__qa.state.draft.id})""")
    page.wait_for_function("__qa.requests.some(x=>x.kind==='flush-complete'&&x.status==='SAVED')")
    history = {**states["completed"], "epoch": state["epoch"] + 1, "draft": {**states["completed"]["draft"], "readonly": True, "hasSuspendedDraft": True}}
    page.evaluate("value=>__qa.publish(value)", history)
    page.wait_for_selector("#reply table")
    assert page.locator("#review-pending").is_hidden()
    assert page.locator('[data-command="stop"]').is_hidden()
    assert page.locator('[data-command="new-question"]').inner_text() == "返回草稿"
    page.screenshot(path=str(output / "history-readonly-380x500.png"))
    restored = {**state, "epoch": state["epoch"] + 2, "draft": {**state["draft"], "comment": before["value"]}}
    page.evaluate("value=>__qa.publish(value)", restored)
    kept = page.evaluate("""() => {
      const el=document.querySelector('#comment'),v=__originalView;
      return {sameNode:el===__originalEditor,value:el.value===v.value,start:el.selectionStart===v.start,end:el.selectionEnd===v.end,scroll:el.scrollTop===v.scroll,focus:document.activeElement===el};
    }""")
    assert all(kept.values()), kept
    assert page.evaluate("__qa.requests.filter(x=>x.command==='send'||x.kind==='review-control').length") == 0
    report["history_view_preserves_original_editor"] = kept
    context.close()

    # Malicious source, GFM structures, long tail and local horizontal scrollers.
    state = states["completed"]
    text = """# Semantic reply

Paragraph **strong** and *emphasis*.

1. First item
2. Second item

> A quotation

| Column A | Column B | Column C | Column D | Column E | Column F |
|---|---|---|---|---|---|
| one | two | three | four | five | TABLE_TAIL |

```python
CODE_START_""" + "x" * 600 + """_CODE_TAIL
```

[Safe web](https://example.com/report) [Email](mailto:qa@example.com)
[Bad](javascript:alert(1)) [Local](file:///D:/secret) [Command](command:run)
![Remote](https://example.com/tracker.png)
<script>window.__executed=true</script><img src=x onerror="window.__executed=true">

""" + ("Long paragraph keeps all words.\n\n" * 1500) + "FINAL_LONG_REPLY_TAIL"
    state = {**state, "result": {**state["result"], "text": text, "text_sha256": sha(json.dumps(text, ensure_ascii=False, separators=(",", ":")))}}
    context = browser.new_context(viewport={"width": 320, "height": 390})
    page = context.new_page()
    outgoing = []
    page.on("request", lambda request: outgoing.append(request.url) if request.url.startswith(("http:", "https:")) else None)
    page.add_init_script(transport.replace("INITIAL_STATE", json.dumps(state)) + bundle)
    page.goto((output / "zh-CN.html").as_uri())
    page.wait_for_selector("#reply pre")
    content = page.locator("#reply").inner_text()
    assert content.endswith("FINAL_LONG_REPLY_TAIL") and "TABLE_TAIL" in content and "_CODE_TAIL" in content
    assert page.locator("#reply script,#reply img,#reply iframe").count() == 0
    assert page.locator("#reply a").count() == 2
    assert page.evaluate("window.__executed===undefined") and not outgoing
    assert page.locator("#reply pre").evaluate("el=>el.scrollWidth>el.clientWidth")
    assert page.locator(".reply-table").evaluate("el=>el.scrollWidth>el.clientWidth")
    assert page.locator('#reply ol li').count() == 2 and page.locator('#reply blockquote').count() == 1
    assert not page.evaluate("document.body.scrollWidth>innerWidth")
    safe = page.get_by_role("link", name="Safe web", exact=True)
    safe.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(40)
    links = page.evaluate("__qa.requests.filter(x=>x.kind==='reply-link')")
    assert len(links) == 1 and links[0]["url"] == "https://example.com/report"
    safe.evaluate("el=>el.click()")
    assert page.evaluate("__qa.requests.filter(x=>x.kind==='reply-link').length") == 1
    report["safety_long_reply"] = {"source_characters": len(text), "rendered_characters": len(content), "tail": True,
                                   "external_requests": outgoing, "trusted_link_requests": len(links), "local_table_pre_overflow": True}
    page.evaluate("""()=>{const value={...__qa.state,draft:{...__qa.state.draft,phase:'FAILED'},error:'Local fixture error',
     result:{...__qa.state.result,text:'',reason:'<img src=x onerror=alert(1)> literal failure reason'}};__qa.publish(value);}""")
    assert page.locator('#reply').get_attribute('data-format') == 'text'
    assert page.locator('#reply img').count() == 0 and '<img src=x' in page.locator('#reply').inner_text()
    assert page.locator('#status').get_attribute('data-error') == 'true' and page.locator('#status').is_visible()
    report['plain_error'] = {'literal_reason': True, 'visible_status': True}
    context.close()

    # The selection change event must lock the controls synchronously, before IPC settles.
    state = {**states["editing"], "draft": {**states["editing"]["draft"], "nativeReview": {**states["editing"]["draft"]["nativeReview"], "process_start_consent": False}}, "reviewOpen": True, "reviewProcessRequired": True, "reviewCatalog": {
        "skills": [{"id": "skill", "title": "Skill", "selectable": True, "config_ref": {"id": "skill"}}],
        "tools": [{"id": "tool", "title": "Tool", "selectable": True, "selection": {"kind": "BUILTIN_READONLY"}}]}}
    context = browser.new_context(viewport={"width": 380, "height": 500})
    page = context.new_page()
    page.add_init_script(transport.replace("INITIAL_STATE", json.dumps(state)) + bundle)
    page.goto((output / "zh-CN.html").as_uri())
    page.wait_for_selector("#review-skill option[value=skill]", state="attached")
    page.locator("#review-skill").select_option("skill")
    assert page.locator("#review-process").is_disabled() and page.locator("#review-tool").is_disabled()
    page.evaluate("document.querySelector('#review-process').dispatchEvent(new Event('change'))")
    assert page.evaluate("__qa.requests.filter(x=>x.kind==='review-consent').length") == 0
    page.evaluate("__qa.publish({...__qa.state,reviewBusy:false})")
    assert page.locator("#review-process").is_disabled()
    page.evaluate("__qa.release()")
    page.wait_for_function("()=>!document.querySelector('#review-process').disabled")
    page.locator("#review-process").check()
    assert page.evaluate("__qa.requests.filter(x=>x.kind==='review-consent').length") == 1
    report["selection_transition"] = {"synchronous_lock": True, "early_consent": 0, "settled_consent": 1, "send": 0}
    context.close()
    browser.close()

(output / "RESULT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"result": str(output / "RESULT.json"), "cases": len(report["cases"]), "model_calls": 0}))
