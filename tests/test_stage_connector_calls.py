"""SDK stdio integration. The fixture reports its own data, never real hardware."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import site
import sys

import pytest

from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Store
from packages.dynamic_workflow import stage_connectors as connectors
from packages.dynamic_workflow.stage_assertions import case_receipt_issues, count_passing_cases, readback_in_window
from packages.dynamic_workflow.stage_workspace import ledger, operation_receipts


def install_readback_meter(tmp_path, service):
    """Report controlled device values over a real MCP transport without physical devices."""
    server = tmp_path / "readback.py"
    mode = tmp_path / "fixture-mode"
    mode.write_text("fresh")
    server.write_text(
        "import sys\nfrom pathlib import Path\nfrom datetime import datetime, timedelta, timezone\n"
        "sys.path.insert(0, " + repr(site.getsitepackages()[0]) + ")\n"
        "from mcp.server import MCPServer\nfrom mcp.types import CallToolResult\ns=MCPServer('test-meter')\n"
        "@s.tool()\ndef measure(channel:int)->CallToolResult:\n"
        "    mode=Path(__file__).with_name('fixture-mode').read_text()\n"
        "    if mode=='ack': return CallToolResult(content=[],structured_content={'reading':17.25,'sent':True})\n"
        "    stamp=datetime.now(timezone.utc)\n"
        "    if mode=='cached': stamp-=timedelta(minutes=1)\n"
        "    if mode=='future': stamp+=timedelta(minutes=1)\n"
        "    if mode=='naive': stamp=stamp.replace(tzinfo=None)\n"
        "    if mode=='offset': stamp=stamp.astimezone(timezone(timedelta(hours=-4)))\n"
        "    return CallToolResult(content=[],structured_content={'reading':0 if mode=='zero' else 17.25,"
        " 'device':'DUT-other' if mode=='other' else 'DUT-07', 'time':stamp.isoformat()})\n"
        "s.run(transport='stdio')\n"
    )
    cmd = [str(Path(sys.executable).resolve()), str(server)]
    config = {"type":"local","enabled":True,"command":cmd,"timeout":15000,
              "files_sha256":{item:hashlib.sha256(Path(item).read_bytes()).hexdigest() for item in cmd}}
    accepted = service.configure_mcp({"operation_id":"register-readback","id":"meter","configuration":config,
                                      "expected_revision":0})
    return mode, accepted["config_ref"]


def readback_action():
    return {"id":"read-meter","method":"mcp_response","server":"meter","tool":"measure","arguments":{"channel":7},
            "pointer":"/reading","expected_json":"17.25",
            "hardware_readback":{"device_id":"DUT-07","device_id_pointer":"/device","sample_time_pointer":"/time"}}


def test_sdk_hardware_readback_rejects_acks_and_stale_or_misdirected_samples(tmp_path):
    data = tmp_path / "data"
    service = NativeCapabilities(Store(data/"store.sqlite3"), data)
    mode, ref = install_readback_meter(tmp_path, service)
    action = readback_action()
    case = {"id":"TC-1","actions":[action]}
    ctx = {"_data_dir":data,"project_id":"p","stage_session_id":"node-session","run_id":"flow","operation_id":"read",
           "capabilities":{"mcp":["meter"]},"capability_refs":{"mcp":{"meter":ref}},
           "approved_by_kind":{"cases":{"items":[case]}}}

    def probe(name):
        mode.write_text(name)
        return asyncio.run(connectors.call_connector(ctx,"meter","measure",action["arguments"],case["id"],
                                                     action["pointer"],action["expected_json"]))["receipt"]

    for name, issue in (("ack","missing_readback"),("cached","outside_dispatch"),("future","outside_dispatch"),
                        ("other","wrong_device"),("naive","invalid_sample_time")):
        receipt = probe(name)
        assert receipt["status"]=="failed" and receipt["readback_issue"]==issue
        assert receipt["observed_sha256"]==receipt["expected_sha256"]
        assert receipt["hardware_readback"]["source"]=="connector_reported"
    wrong_reading = probe("zero")
    assert wrong_reading["status"]=="failed" and wrong_reading["hardware_readback"]["observed_preview"]=="0"
    passed = probe("offset")
    data_view = passed["hardware_readback"]
    assert passed["status"]=="passed" and readback_in_window(data_view)
    assert data_view["sampled_at"].endswith("-04:00") and data_view["device_id"]=="DUT-07"
    assert data_view["observed_preview"]=="17.25" and passed["config_ref"]==ref
    artifact = {"items":[{"case_id":case["id"],"evidence_refs":[passed["id"]]}]}
    assert not case_receipt_issues(artifact,ctx["approved_by_kind"],operation_receipts(ctx))
    forged = {key:value for key,value in passed.items() if key!="hardware_readback"}
    assert case_receipt_issues(artifact,ctx["approved_by_kind"],[forged])
    probe("cached")
    assert any("请求与返回" in problem for problem in
               case_receipt_issues(artifact,ctx["approved_by_kind"],operation_receipts(ctx)))


@pytest.fixture
def selected(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRVEN_TEST_PARENT_TOKEN", "local-control")
    data = tmp_path / "data"
    service = NativeCapabilities(Store(data / "store.sqlite3"), data)
    server = tmp_path / "meter.py"
    server.write_text(
        "import os, sys\nfrom pathlib import Path\nsys.path.insert(0, " + repr(site.getsitepackages()[0]) + ")\n"
        "from mcp.server import MCPServer\nfrom mcp.types import CallToolResult, TextContent\ns=MCPServer('meter')\n"
        "@s.tool()\ndef observe(channel:int)->CallToolResult:\n"
        "    fixture=Path(__file__).parent\n"
        "    with (fixture/'dispatches.txt').open('a') as log:\n        log.write(f'{channel}\\n')\n"
        "    if (fixture/'disconnect').exists(): os._exit(23)\n"
        "    fields={'channel':channel,'bits':[True,0,None],'mode/word':{'~raw':'ok'},"
        "'secret_present':'CIRVEN_TEST_PARENT_TOKEN' in os.environ,'private_home':os.getcwd()==os.environ['HOME']}\n    return CallToolResult(content=[TextContent(type='text',text='measured')],structured_content=fields)\n"
        "@s.tool()\ndef broken()->dict:\n    raise ValueError('fixture error')\n"
        "s.run(transport='stdio')\n"
    )
    cmd = [str(Path(sys.executable).resolve()), str(server)]
    config = {"type":"local", "enabled":True, "command":cmd, "timeout":15000,
              "files_sha256":{s:hashlib.sha256(Path(s).read_bytes()).hexdigest() for s in cmd}}
    ref = service.configure_mcp({"operation_id":"register", "id":"meter", "configuration":config,
                                 "expected_revision":0})["config_ref"]
    ctx = {"_data_dir":data, "capabilities":{"mcp":["meter"]}, "capability_refs":{"mcp":{"meter": ref}},
           "project_id":"p", "stage_session_id":"node-session", "run_id":"flow", "operation_id":"execution",
           "approved_by_kind":{"cases":{"items":[{"id":"TC-1"}]}}}
    calls = [("observe","/mode~1word/~0raw",'"ok"'),("observe","/bits/0","1"),
             ("observe","/missing","null"),("observe","/bits/2","null"),("broken","/channel","7")]
    ctx["approved_by_kind"]["cases"]["items"][0]["actions"] = [
        {"id":f"check-{i}","method":"mcp_response","server":"meter","tool":tool,
         "arguments":{"channel":7} if tool=="observe" else {},"pointer":pointer,"expected_json":expected}
        for i,(tool,pointer,expected) in enumerate(calls)]
    return ctx, service, server


def test_sdk_observation_and_case_bound_receipt(selected):
    ctx, _, _ = selected
    tools = asyncio.run(connectors.list_connector_tools(ctx,"meter"))["tools"]
    assert {row["name"] for row in tools} == {"observe","broken"}
    assert next(row for row in tools if row["name"]=="observe")["inputSchema"]["properties"]["channel"]["type"] == "integer"
    args = {"channel":7}
    def probe(pointer="",expected="",tool="observe"):
        return asyncio.run(connectors.call_connector(ctx,"meter",tool,args if tool=="observe" else {},
                    "TC-1" if pointer else "",pointer,expected))
    obs = probe()
    assert obs["structured_content"] == {"channel":7,"bits":[True,0,None],"mode/word":{"~raw":"ok"},
            "secret_present":False,"private_home":True}
    assert obs["content"] and not obs["is_error"] and obs["receipt"]["status"]=="connector_replied"
    good = probe("/mode~1word/~0raw",'"ok"')["receipt"]
    assert good["method"] == "mcp_response" and good["case_id"]=="TC-1" and good["exit_code"]==0
    assert good["observed_sha256"]==good["expected_sha256"] and good["status"]=="passed"
    type_mismatch = probe("/bits/0","1")["receipt"]
    missing = probe("/missing","null")["receipt"]
    observed_null = probe("/bits/2","null")["receipt"]
    error = probe("/channel","7","broken")
    assert type_mismatch["status"]=="failed" and type_mismatch["observed_sha256"]!=type_mismatch["expected_sha256"]
    assert missing["status"]=="failed" and missing["observed_sha256"] is None and observed_null["status"]=="passed"
    assert error["is_error"] and error["receipt"]["status"]=="failed"
    assert [r["id"] for r in operation_receipts(ctx)] == [
        r["id"] for r in [obs["receipt"],good,type_mismatch,missing,observed_null,error["receipt"]]]


def test_disconnected_tool_is_saved_before_dispatch_and_never_reissued_in_same_attempt(selected):
    ctx, _, source = selected
    case = ctx["approved_by_kind"]["cases"]["items"][0]
    case["actions"] = [case["actions"][0]]

    def call(context):
        return asyncio.run(connectors.call_connector(context,"meter","observe",{"channel":7},
                                                     "TC-1","/mode~1word/~0raw",'"ok"'))

    def count(records):
        return count_passing_cases([case],records,(),lambda: pytest.fail("Connector checks have no file snapshot"))

    prior = call(ctx)["receipt"]
    assert count(operation_receipts(ctx))==1
    artifact = {"items":[{"case_id":"TC-1","evidence_refs":[prior["id"]]}]}
    assert not case_receipt_issues(artifact,ctx["approved_by_kind"],[prior])
    disconnect = source.parent/"disconnect"
    disconnect.touch()
    with pytest.raises(RuntimeError):
        call(ctx)
    marker = source.parent/"dispatches.txt"
    assert marker.read_text().splitlines()==["7","7"]
    prior_seen, unknown = operation_receipts(ctx)
    assert prior_seen==prior
    assert unknown["kind"]=="check" and unknown["status"]=="unconfirmed"
    assert unknown["action_sha256"]==prior["action_sha256"] and unknown["tool"]=="observe"
    assert "exit_code" not in unknown and "observed_sha256" not in unknown
    assert count([prior_seen,unknown])==0
    assert case_receipt_issues(artifact,ctx["approved_by_kind"],[prior_seen,unknown])
    with ledger(ctx["_data_dir"]) as db:
        journal = [json.loads(row[0]) for row in db.execute("SELECT record FROM receipts ORDER BY rowid")]
    assert journal[0]["status"]=="unconfirmed" and prior["connector_dispatch_id"]==journal[0]["id"]
    assert journal[1:]==[prior,unknown]
    disconnect.unlink()
    with pytest.raises(RuntimeError):
        call(ctx)
    assert marker.read_text().splitlines()==["7","7"]
    assert operation_receipts(ctx)==[prior_seen,unknown]
    next_attempt = {**ctx,"operation_id":"explicit-run-again"}
    new_receipt = call(next_attempt)["receipt"]
    assert new_receipt["status"]=="passed" and marker.read_text().splitlines()==["7","7","7"]
    assert operation_receipts(ctx)==[prior_seen,unknown]
    assert operation_receipts(next_attempt)==[new_receipt]


def test_unlisted_tool_never_enters_dispatch_history(selected):
    ctx, _, source = selected
    with pytest.raises(RuntimeError):
        asyncio.run(connectors.call_connector(ctx,"meter","not_listed",{}))
    assert not operation_receipts(ctx)
    assert not (source.parent/"dispatches.txt").exists()


def test_dispatch_does_not_leave_host_if_ledger_write_fails(selected,monkeypatch):
    ctx, _, source = selected
    def deny_write(*args,**kwargs):
        raise OSError("fixture ledger unavailable")
    monkeypatch.setattr(connectors,"_record",deny_write)
    with pytest.raises(RuntimeError):
        asyncio.run(connectors.call_connector(ctx,"meter","observe",{"channel":7}))
    assert not operation_receipts(ctx)
    assert not (source.parent/"dispatches.txt").exists()


def test_revocation_replacement_and_scoped_budget(selected,monkeypatch):
    ctx, service, source = selected
    with pytest.raises(ValueError):
        asyncio.run(connectors.list_connector_tools({**ctx,"capabilities":{"mcp":[]}},"meter"))
    with pytest.raises(ValueError):
        asyncio.run(connectors.call_connector(ctx,"meter","observe",{},"other","/val","0"))
    with pytest.raises(ValueError):
        asyncio.run(connectors.call_connector(ctx,"meter","observe",{},"TC-1","/a~2","null"))
    with pytest.raises(ValueError):
        asyncio.run(connectors.call_connector(ctx,"meter","observe",{},"TC-1","/val",""))
    with pytest.raises(ValueError):
        asyncio.run(connectors.call_connector(ctx,"meter","observe",{},"TC-1","/val","NaN"))
    with pytest.raises(ValueError):
        asyncio.run(connectors.call_connector(ctx,"meter","observe",{},"TC-1","/val",'{"x":1,"x":2}'))
    with pytest.raises(ValueError, match="不一致"):
        asyncio.run(connectors.call_connector(ctx,"meter","observe",{"channel":99},"TC-1","/bits/2","null"))
    monkeypatch.setattr(connectors,"MAX_CALLS",1)
    assert asyncio.run(connectors.list_connector_tools(ctx,"meter"))["tools"]
    with pytest.raises(ValueError, match="上限"):
        asyncio.run(connectors.list_connector_tools(ctx,"meter"))
    next_op = {**ctx,"operation_id":"another"}
    source.write_text(source.read_text()+"\n# changed bytes\n")
    with pytest.raises(ValueError, match="校验"):
        asyncio.run(connectors.list_connector_tools(next_op,"meter"))
    service.set_enabled("mcp","meter",{"expected_revision":1,"operation_id":"disable","enabled":False})
    with pytest.raises(ValueError, match="变化"):
        asyncio.run(connectors.list_connector_tools(next_op,"meter"))
    assert not operation_receipts(ctx)


def test_native_image_forwarding_keeps_image_out_of_text():
    import base64
    image = base64.b64encode(b"test image payload").decode()
    source = {"server":"desktop","tool":"screen","is_error":False,"structured_content":{"width":640},
              "content":[{"type":"image","mimeType":"image/png","data":image}]}
    output = connectors.as_tool_result(source)
    assert output.content[0].type=="image" and output.content[0].data==image
    assert image not in output.content[-1].text
    assert output.structured_content["structured_content"]=={"width":640}


def test_https_redirect_is_never_followed(selected,monkeypatch):
    """No Internet service: real MCP client over a controlled HTTP transport."""
    import httpx2
    ctx,service,_ = selected
    ref = service.configure_mcp({"operation_id":"register-remote","id":"remote","expected_revision":0,
        "configuration":{"type":"remote","url":"https://meter.invalid/mcp","timeout":3000,"enabled":True}})["config_ref"]
    ctx.update(capabilities={"mcp":["remote"]},capability_refs={"mcp":{"remote":ref}})
    captured = []
    def redirect(req):
        captured.append((str(req.url),dict(req.headers)))
        return httpx2.Response(307,headers={"location":"https://other.invalid/steal"})
    def fixture_transport(*,trust_env,retries):
        assert not trust_env and retries == 0
        return httpx2.MockTransport(redirect)
    monkeypatch.setattr(httpx2,"AsyncHTTPTransport",fixture_transport)
    with pytest.raises(RuntimeError):
        asyncio.run(connectors.list_connector_tools(ctx,"remote"))
    assert captured and all(url=="https://meter.invalid/mcp" and "authorization" not in headers
                              and "cookie" not in headers for url,headers in captured)
    assert not operation_receipts(ctx)
