"""Real API/native SDK/local HTTP MCP; only external model JSON is synthetic."""
import copy
import json

from claude_agent_sdk import ClaudeSDKClient
import pytest

from test_documents import project, upload
from test_extraction_context import bundle, extract
from test_sdk_images import environment
from test_sdk_search_mcp import SearchWire, local_mcp, search_config
from test_task_sdk_api import formal, until_review


def factory(monkeypatch, output=None):
    wires = []
    def installed(options):
        wire = SearchWire(options)
        if output is not None:
            wire.structured_output = copy.deepcopy(output)
        wires.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)
    monkeypatch.setattr("packages.adapters.claude_sdk.ClaudeSDKClient", installed)
    return wires


def test_explicit_extraction_search_is_real_mcp_and_still_an_unconfirmed_original_citation(claude_review_client, tmp_path, monkeypatch, local_mcp):
    client = claude_review_client
    environment(monkeypatch, enabled=False)
    search_config(tmp_path, monkeypatch, local_mcp)
    pid = project(client)
    doc = upload(client, pid).json()
    wires = factory(monkeypatch, bundle(doc, 1))
    response = extract(client, pid, doc, mode="live", search_enabled=True)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert not answer["proposal"]["confirmed"] and answer["proposal"]["origin"] == "live_suggestion"
    assert answer["proposal"]["requirements"][0]["source_refs"][0]["sha256"] == doc["sha256"]
    assert local_mcp["calls"] == [{"tool": "original_approved_search", "arguments": {"q": "Original public search clue"}}]
    lead = json.loads(wires[0].rpc_results["call"]["result"]["content"][0]["text"])
    assert lead["kind"] == "SEARCH_LEAD" and not lead["original_document_read"]
    assert not answer["model_verified"] and len(wires) == 1
    execution = client.get(f"/api/projects/{pid}/agent-executions/{answer['execution_id']}").json()
    assert json.loads(execution["request"])["search_enabled"] is True
    assert execution["verification_status"] == "NOT_RUN"
    assert extract(client, pid, doc, mode="live", search_enabled=True).json()["execution_id"] == answer["execution_id"]
    assert len(wires) == len(local_mcp["calls"]) == 1
    assert client.get(f"/api/projects/{pid}/requirements").json()["requirements"] == []


def test_task_search_and_resume_share_one_query_allowance_and_preserve_formal_run(claude_review_client, tmp_path, monkeypatch, local_mcp):
    client = claude_review_client
    environment(monkeypatch, enabled=False)
    search_config(tmp_path, monkeypatch, local_mcp, limits={"max_calls": 1})
    pid, _, _, _, run, route, body = formal(client)
    wires = factory(monkeypatch)
    submitted = client.post(route + "/native-reviews", json={**body, "search_enabled": True})
    assert submitted.status_code == 202, submitted.text
    first = until_review(client, pid, submitted.json()["admission_id"], {"RETURNED"})
    assert not wires[0].rpc_results["call"]["result"].get("isError")
    native = first["native_execution"]
    assert json.loads(native["request"])["search_enabled"] is True
    assert "without code or file tools" in json.loads(native["request"])["prompt"]
    resumed = client.post(f"/api/projects/{pid}/native-reviews/{first['admission_id']}/resume", json={
        "operation_id": "search-resume-shared-cap", "expected_session_id": native["session_id"],
        "prompt": "Find another original clue within the same approved allowance"})
    assert resumed.status_code == 202, resumed.text
    final = until_review(client, pid, resumed.json()["admission_id"], {"RETURNED"})
    assert len(wires) == 2 and wires[1].rpc_results["call"]["result"]["isError"]
    assert len(local_mcp["calls"]) == 1
    assert final["verification_status"] == "NOT_RUN" and not final["formal_run_changed"]
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run


def test_missing_search_configuration_blocks_task_and_extraction_without_admission(claude_review_client, monkeypatch):
    client = claude_review_client
    environment(monkeypatch, enabled=False)
    monkeypatch.delenv("EEAS_SEARCH_MCP_CONFIG_PATH", raising=False)
    pid, doc, _, _, _, route, body = formal(client)
    wires = factory(monkeypatch)
    planning = extract(client, pid, doc, mode="live", search_enabled=True)
    assert planning.status_code == 503 and "SEARCH_MCP_NOT_CONFIGURED" in planning.json()["error"]["message"]
    task = client.post(route + "/native-reviews", json={**body, "search_enabled": True})
    assert task.status_code == 503 and "SEARCH_MCP_NOT_CONFIGURED" in task.json()["error"]["reasons"]
    assert not wires
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM task_native_reviews").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0
    capability = client.get("/api/runtime").json()["search_mcp"]
    assert capability["status"] == "NOT_CONFIGURED" and not capability["page_fetch_available"]


@pytest.mark.parametrize("value", [1, "true", {}, None])
def test_search_consent_is_strict_boolean_for_both_routes(client, value):
    pid, doc, _, _, _, route, body = formal(client)
    assert extract(client, pid, doc, search_enabled=value).status_code == 422
    assert client.post(route + "/native-reviews", json={**body, "search_enabled": value}).status_code == 422
    assert extract(client, pid, doc, search_enabled=True).status_code == 422  # Mock never searches.
