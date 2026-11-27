"""Explicit source-bound SDK wire probes; never company/model qualification.

Only the external model transport is original synthetic data. API, source import,
SQLite, installed SDK clients/options/events and scope validation remain real.
This file is deliberately outside default pytest discovery.
"""
from __future__ import annotations

import base64
import copy
import importlib.metadata
import json
import os

from claude_agent_sdk import ClaudeSDKClient

from campaign_probes import Evidence, all_chunks, ingest, load_binding, sha
from packages.adapters import claude_sdk
from packages.adapters.settings_profile import ROUTE_ENV
from test_claude_native_transport import OriginalJSONTransport
from test_claude_sessions import FIXTURE_ENV, NativeFactory
from test_task_sdk_api import claude_review_runtime, until_review


class NativeEvidence(Evidence):
    def observe(self):
        captured = self.captured
        self.save("sdk-wire-boundary.json", {
            "source_kind": captured["source"]["source_kind"], "model_transport_source_kind": "synthetic",
            "runtime_kind": "mock", "company_model_calls": 0, "external_network_calls": 0,
            "sdk_version": importlib.metadata.version("claude-agent-sdk"),
            "model_compatibility": "NOT_VALIDATED", "wire_results_are_original_fixtures": True})
        value = {"schema": "eeas.campaign-observation.v1", "case_id": captured["binding"]["case_id"],
                 "binding_sha256": sha(captured["binding_raw"]), "source_sha256": sha(captured["raw"]),
                 "source_kind": captured["source"]["source_kind"], "runtime_kind": "mock",
                 "model_calls": 0, "model_transport_source_kind": "synthetic",
                 "origin_verification": "DECLARED_NOT_INDEPENDENTLY_VERIFIED",
                 "actual_receipts": self.receipts}
        with (self.root / "campaign-observation.json").open("xb") as stream:
            stream.write((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def fixture_route(monkeypatch, enabled):
    # Never discover an inherited company settings file or use ambient credentials.
    for key in (*ROUTE_ENV, "EEAS_CLAUDE_SETTINGS_PATH", "EEAS_SEARCH_MCP_CONFIG_PATH",
                "EEAS_MODEL_ID", "EEAS_MODEL_PROFILE_ID", "EEAS_LIVE_MAX_COST_USD",
                "EEAS_MODEL_VISION_ENABLED"):
        monkeypatch.setenv(key, "")
    if enabled:
        for key, value in FIXTURE_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")


def source_quote(captured, evidence):
    """Quote a complete original body line; composite wrappers require verified provenance."""
    if (captured["binding"].get("provenance_path") or captured["source"].get("provenance")
            or captured["text"].startswith("EEAS PUBLIC AUDIT MATERIAL\n")):
        try:
            from campaign_workflow_probes import source_lines
        except ImportError as error:
            raise ValueError("Composite source requires the verified source_lines provenance helper") from error
        lines = source_lines(captured, evidence)
        if not lines:
            raise ValueError("Composite source has no verified original bodies")
        line = lines[-1]
        if not 0 < len(line["content"]) <= 1000:
            raise ValueError("No bounded complete original body line; no source truncation fallback")
        selected = {"line_start": line["line"], "line_end": line["line"], "char_start": line["char_start"],
                    "char_end_exclusive": line["char_end"], "raw_line": line["physical_line"],
                    "quote": line["content"], "raw_sha256": sha(captured["raw"]),
                    "verified_source_line": line}
        evidence.save("actual-selected-source-line.json", selected)
        return selected
    candidates, offset = [], 0
    for number, raw_line in enumerate(captured["text"].splitlines(keepends=True), 1):
        start, end = offset, offset + len(raw_line)
        offset = end
        line = raw_line.rstrip("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")
        if not line.strip() or line.lstrip().startswith("#") or len(line) > 1000:
            continue
        value = {"line_start": number, "line_end": number, "char_start": start, "char_end_exclusive": end,
                 "raw_line": raw_line, "quote": line, "raw_sha256": sha(captured["raw"])}
        candidates.append(value)
    if not candidates:
        raise ValueError("No bounded complete original body line; no generated header or source padding fallback")
    selected = candidates[-1]
    assert captured["text"][selected["char_start"]:selected["char_end_exclusive"]] == selected["raw_line"]
    evidence.save("actual-selected-source-line.json", selected)
    return selected


def prepared(client, case_id):
    captured = load_binding(case_id)
    evidence = NativeEvidence(captured)
    pid, doc, base = ingest(client, captured, evidence)
    quote = source_quote(captured, evidence)
    parts = all_chunks(client, base, captured, evidence)
    physical_end = sum(len(line) for line in captured["text"].splitlines(keepends=True)[:quote["line_end"]])
    indexes = [part["index"] for part in parts if part["char_start"] < physical_end and
               quote["char_start"] < part["char_end"]]
    response = client.post(base + "/context", json={"revision": 1, "chunk_indexes": indexes})
    assert response.status_code == 200, response.text
    context = response.json()
    assert not context["truncated"] and context["sha256"] == doc["sha256"]
    assert context["included_chunks"] == indexes
    evidence.save("actual-selected-context.json", context)
    return captured, evidence, pid, doc, base, quote, parts, context


def proposal(doc, quote):
    # Original model-wire suggestion uses a literal body quote, not an upstream
    # engineering claim. The deliberately unsafe self-confirm flag must be reset.
    return {"schema_version": "eeas.requirements.v1", "title": "Source quote wire suggestion",
        "requirements": [{"id": "R1", "text": quote["quote"], "source_refs": [{
            "document_id": doc["id"], "revision": 1, "sha256": doc["sha256"],
            "line_start": quote["line_start"], "line_end": quote["line_end"]}]}],
        "acceptance": [{"id": "A1", "text": "Compare the quoted original source line and SHA", "requirement_ids": ["R1"]}],
        "assumptions": ["Original external-model wire fixture, not independently model-extracted business requirements"],
        "questions": [], "confirmed": True, "origin": "manual"}


def options_receipt(wire):
    options = wire.options
    return {"tools": options.tools, "permission_mode": options.permission_mode,
            "max_turns": options.max_turns, "max_budget_usd": options.max_budget_usd,
            "setting_sources": options.setting_sources, "skills": options.skills,
            "plugins": options.plugins, "mcp_server_names": sorted(options.mcp_servers or {})}


def test_extraction(client, monkeypatch):
    captured, evidence, pid, doc, base, quote, parts, context = prepared(client, "extraction")
    assert len(parts) >= 2, "Different actual source selections require two original chunks; no padding"
    chosen = context["included_chunks"]
    other = next(part["index"] for part in parts if part["index"] not in chosen)
    body = {"document_id": doc["id"], "revision": 1, "chunk_indexes": chosen}
    mock = client.post(f"/api/projects/{pid}/planning/extract", json={**body, "mode": "mock"})
    assert mock.status_code in {200, 422}, mock.text
    if mock.status_code == 200:
        assert not mock.json()["proposal"]["confirmed"] and mock.json()["proposal"]["origin"] == "mock"
    else:
        assert mock.json()["error"]["code"] == "EXTRACTION_INPUT_INVALID"
    evidence.save("actual-mock-extraction.json", {"status": mock.status_code, "body": mock.json()})
    fixture_route(monkeypatch, True)
    wires = []
    output = proposal(doc, quote)

    def factory(options):
        wire = OriginalJSONTransport(structured_output=copy.deepcopy(output), total_cost_usd=0.002)
        wire.options = options
        wires.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)

    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", factory)
    route = f"/api/projects/{pid}/planning/extract"
    first = client.post(route, json={**body, "mode": "live"})
    assert first.status_code == 200, first.text
    answer = first.json()
    assert not answer["proposal"]["confirmed"] and answer["proposal"]["origin"] == "live_suggestion"
    assert not answer["model_verified"] and answer["approval"] == "HUMAN_CONFIRMATION_REQUIRED"
    assert answer["context_sha256"] == context["context_sha256"]
    assert answer["coverage"] == context["coverage"] and answer["image_refs"] == []
    repeat = client.post(route, json={**body, "mode": "live"})
    assert repeat.status_code == 200 and repeat.json() == answer and len(wires) == 1
    alternate = client.post(route, json={**body, "mode": "live", "chunk_indexes": sorted([*chosen, other])})
    assert alternate.status_code == 200, alternate.text
    assert alternate.json()["execution_id"] != answer["execution_id"] and len(wires) == 2
    assert alternate.json()["context_sha256"] != answer["context_sha256"]
    # Deliberate original wire fault: cite the real line while omitting its chunk.
    omitted = client.post(route, json={**body, "mode": "live", "chunk_indexes": [other]})
    assert omitted.status_code == 422 and omitted.json()["error"]["code"] == "MODEL_PROPOSAL_INVALID"
    assert len(wires) == 3 and all(wire.closed for wire in wires)
    exports = []
    for wire, result in zip(wires[:2], [answer, alternate.json()]):
        exported = client.get(f"/api/projects/{pid}/agent-executions/{result['execution_id']}/result").json()
        assert exported["execution"]["execution_status"] == "RETURNED"
        assert exported["verification_status"] == "NOT_RUN" and not exported["live_evidence"]
        request = json.loads(exported["execution"]["request"])
        supplied = json.loads(request["prompt"].split("\n", 1)[1])["context"]
        assert supplied["sha256"] == doc["sha256"] and supplied["revision"] == 1 and not supplied["truncated"]
        for part in supplied["chunks"]:
            assert part["content"] == captured["text"][part["char_start"]:part["char_end"]]
        assert len([message for message in wire.writes if message["type"] == "user"]) == 1
        assert wire.options.tools == [] and wire.options.permission_mode == "default"
        exports.append(exported)
    assert client.get(f"/api/projects/{pid}/requirements").json()["requirements"] == []
    assert client.get(base + "/download", params={"revision": 1}).content == captured["raw"]
    evidence.save("actual-native-extraction.json", {"first": answer, "repeat": repeat.json(), "alternate": alternate.json(),
        "omitted_citation": {"status": omitted.status_code, "body": omitted.json()}, "exports": exports})
    evidence.save("actual-sdk-wire.json", [{"options": options_receipt(wire), "writes": wire.writes} for wire in wires])
    evidence.observe()


def formal(client, captured, evidence, pid, doc, quote):
    draft = proposal(doc, quote)
    draft["confirmed"] = False
    response = client.post(f"/api/projects/{pid}/requirements", json=draft)
    assert response.status_code == 201 and not response.json()["payload"]["confirmed"]
    requirement = response.json()
    response = client.put(f"/api/projects/{pid}/requirements/{requirement['id']}?expected_revision=1",
                          json={**draft, "confirmed": True, "origin": "manual"})
    assert response.status_code == 200 and response.json()["revision"] == 2
    requirement = response.json()
    task_body = {"goal": quote["quote"], "approval_required": True,
                 "requirement_refs": [{"requirement_id": requirement["id"], "revision": 2, "sha256": requirement["sha256"]}]}
    response = client.post(f"/api/projects/{pid}/tasks", json=task_body)
    assert response.status_code == 201, response.text
    task = response.json()
    route = f"/api/projects/{pid}/tasks/{task['id']}/runs"
    response = client.post(route, json={"revision": 1, "operation_id": "source-bound-formal"})
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["execution_status"] == "AWAITING_APPROVAL" and run["verification_status"] == "NOT_RUN"
    frozen = json.loads(run["frozen_inputs"])
    assert len(frozen["documents"]) == 1
    assert base64.b64decode(frozen["documents"][0]["raw_base64"], validate=True) == captured["raw"]
    assert frozen["documents"][0]["sha256"] == doc["sha256"]
    evidence.save("actual-human-confirmation-formal-run.json", {"draft": draft, "confirmed": requirement, "task": task, "run": run})
    return task, run, route + "/" + run["id"]


def test_native_review(client, monkeypatch):
    captured, evidence, pid, doc, base, quote, parts, context = prepared(client, "native-review")
    task, run, route = formal(client, captured, evidence, pid, doc, quote)
    scope = client.get(route + "/native-review-scope")
    assert scope.status_code == 200, scope.text
    scope = scope.json()
    body = {"operation_id": "source-bound-readonly", "task_binding": scope["task_binding"],
            "input_hash": scope["input_hash"], "source_hash": scope["source_hash"],
            "chunk_selections": [{"document_id": doc["id"], "revision": 1, "chunk_indexes": context["included_chunks"]}]}
    factory = NativeFactory([0.002])
    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", factory)
    fixture_route(monkeypatch, False)
    missing = client.post(route + "/native-reviews", json=body)
    assert missing.status_code == 503 and "NO_CREDENTIALS" in missing.json()["error"]["reasons"]
    fixture_route(monkeypatch, True)
    blocked_tools = client.post(route + "/native-reviews", json={**body, "needs_tools": True})
    assert blocked_tools.status_code == 503 and "OS_ISOLATION_NOT_QUALIFIED" in blocked_tools.json()["error"]["reasons"]
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM task_native_reviews").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0
    assert not factory.transports
    evidence.save("actual-native-denials.json", {"missing": missing.json(), "code_tools": blocked_tools.json(), "admissions": 0})
    response = client.post(route + "/native-reviews", json=body)
    assert response.status_code == 202, response.text
    ref = response.json()["admission_id"]
    completed = until_review(client, pid, ref, {"RETURNED"})
    prefix = f"/api/projects/{pid}/native-reviews/{ref}"
    exported = client.get(prefix + "/result").json()
    native = completed["native_execution"]
    request = json.loads(native["request"])
    assert request["document_refs"] == [{"document_id": doc["id"], "revision": 1, "sha256": doc["sha256"]}]
    assert request["task_binding"] == body["task_binding"]
    assert request["needs_tools"] is False and not request.get("search_enabled", False) and not request.get("image_refs")
    supplied = json.loads(next(iter(request["source_files"].values())))
    assert supplied == context and not supplied["truncated"]
    wire = factory.transports[0]
    assert wire.options.tools == [] and wire.options.permission_mode == "default"
    assert wire.options.setting_sources == [] and not wire.options.skills and not wire.options.plugins
    assert not wire.options.mcp_servers and wire.options.max_turns == task["spec"]["budget"]["max_steps"]
    assert json.loads(native["result"])["total_cost_usd"] == 0.002 and json.loads(native["result"])["usage"] is None
    assert native["session_id"] == wire.session_id and native["verification_status"] == "NOT_RUN"
    assert not completed["formal_run_changed"] and not exported["native_result"]["live_evidence"]
    events = client.get(prefix + "/events").json()["events"]
    assert [event["event_seq"] for event in events] == list(range(1, len(events) + 1))
    assert any(event["payload"]["type"] == "ResultMessage" for event in events)
    assert client.get(prefix + "/events", params={"after": events[-1]["event_seq"]}).json()["events"] == []
    repeat = client.post(route + "/native-reviews", json=body)
    assert repeat.status_code == 202 and repeat.json()["admission_id"] == ref
    for _ in range(3):
        assert client.get(prefix + "/result").json() == exported
        assert client.get(route + "/native-review-scope").status_code == 200
    from packages.adapters.task_sdk import TaskNativeReviews
    reopened = TaskNativeReviews(client.app.state.store, client.app.state.bridge)
    assert reopened.export(pid, ref) == exported and not reopened.jobs
    assert len(factory.transports) == 1 and wire.closed
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM task_native_reviews").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM runner_jobs").fetchone()[0] == 0
    evidence.save("actual-native-review.json", {"scope": scope, "submitted": response.json(), "completed": completed,
        "export": exported, "events": events, "repeated": repeat.json(), "reopened_export": reopened.export(pid, ref), "formal_run_after": run})
    evidence.save("actual-sdk-wire.json", {"options": options_receipt(wire), "writes": wire.writes})
    evidence.observe()
