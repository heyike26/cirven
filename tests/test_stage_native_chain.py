"""Eight API stages, original native Claude Code, loopback model and actual MCP file/check tools."""
from copy import deepcopy
import hashlib
import json

from test_engineering_stage_contracts import APPROVED, INPUT, scenario
from test_stage_kernel import local_gateway
from test_stage_workflow_runs import ROOT, action, ok, scope, start
from packages.dynamic_workflow import kernel_settings
from packages.dynamic_workflow.stage_contracts import SCENARIO_V4_CONTRACT_VERSION


def _extract(value):
    if isinstance(value,str):
        yield value
        try:parsed=json.loads(value)
        except ValueError:return
        if isinstance(parsed,(list,dict)):yield from _extract(parsed)
    elif isinstance(value,list):
        for part in value:yield from _extract(part)
    elif isinstance(value,dict):
        yield value
        for part in value.values():yield from _extract(part)


def test_actual_eight_stage_review_and_download(client,monkeypatch):
    import pytest
    if not kernel_settings.bundled("claude-code"):pytest.skip("Native Claude Code is unavailable")
    model_requests=[]
    observed_ids=[]
    payload='{"ready":true}\n'
    project=scope(client,"Engineering case")
    def respond(data):
        bundle=None
        for text in _extract(data["messages"]):
            if isinstance(text,str) and '{"annotations":' in text:
                try:b=json.JSONDecoder().raw_decode(text[text.index('{"annotations":'):])[0]
                except ValueError:continue
                if "stage" in b:bundle=b;break
        if bundle is None:return {"summary":"auxiliary request","items":[]}
        kind=bundle["stage"]["kind"]
        model_requests.append(kind)
        result=deepcopy(APPROVED[kind])
        seen=[v for v in _extract(data["messages"]) if isinstance(v,dict) and "recorded_at" in v and "kind" in v]
        def tool(name,args):return {"_call":("mcp__cirven__"+name,args)}
        if kind=="cases":
            result["items"][0]["actions"]=[{"id":"contents","method":"text_equals","path":"src/status.json","expected":payload}]
        if kind=="design":
            result["items"][0].update(directory="src/status.json",behavior_scenarios=[
                scenario("S-1","OR-1","contents"),scenario("S-2","OR-2","contents")])
        if kind=="development":
            f=next((r for r in seen if r["kind"]=="file"),None)
            if not f:return tool("workspace_write",dict(path="src/status.json",content=payload))
            done=next((r for r in seen if r["kind"]=="design_item"),None)
            if not done:return tool("complete_design_item",dict(design_id="DS-1",file_receipts=[f["id"]]))
            result["items"][0]["evidence_refs"]=[f["id"],done["id"]]
            observed_ids.append(f["id"])
        if kind=="testing":
            fact=next((r for r in seen if r["kind"]=="check"),None)
            if not fact:return tool("run_check",dict(case_id="TC-1",path="src/status.json",mode="text_equals",expected=payload))
            result["items"][0].update(evidence_refs=[fact["id"]],rework=[])
        if kind=="delivery":result["items"][0].update(path="src/status.json",sha256=hashlib.sha256(payload.encode()).hexdigest())
        return result
    monkeypatch.setenv("CIRVEN_E2E_TOKEN","test-local")
    with local_gateway(respond) as (url,seen):
        ok(client.put("/api/workflow-kernels/claude-code/route",json=dict(revision=0,endpoint=url,model="cirven-qa",credential_env="CIRVEN_E2E_TOKEN")))
        run=start(client,project,"default",stored_version=SCENARIO_V4_CONTRACT_VERSION)
        stage_sessions=[]
        for kind in APPROVED:
            assert run["active_node_id"]==kind
            run=ok(action(client,project,run,"set-"+kind,"configure",{"kernel":"claude-code"}))
            inputs=INPUT if kind=="requirements" else {}
            run=ok(action(client,project,run,"do-"+kind,"execute",{"user_input":inputs}))
            stage=next(s for s in run["stages"] if s["node_id"]==kind)
            print("REVIEW",kind,stage["status"],stage["approval_issues"],stage["error"],flush=True)
            assert stage["status"]=="review",stage["error"]
            assert stage["approval_issues"]==[]
            stage_sessions.append(stage["stage_session_id"])
            run=ok(action(client,project,run,"accept-"+kind,"approve"))
        assert len(set(stage_sessions))==8
        assert all(s["status"]=="approved" for s in run["stages"])
        assert len(observed_ids)==1
        response=client.get(f"{ROOT}/runs/{run['id']}/files/{observed_ids[0]}",params=project)
        assert response.status_code==200 and response.content==payload.encode()
        assert seen and set(model_requests)==set(APPROVED)
