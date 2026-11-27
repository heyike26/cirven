"""Fixed actual CLI/Bun loader/task loop. Only external HTTP model replies are fixtures."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.request
import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from packages.adapters.opencode_workflow_plugin import prepare_workflow_plugin, write_workflow_policy
from packages.adapters.opencode_profile import CLI_SHA
from packages.dynamic_workflow import DynamicWorkflowStore

ROOT = Path(__file__).resolve().parents[4]
CLI = ROOT / "var/opencode-ui-offline-guard/opencode"


def check(value, text="workflow runtime acceptance failed"):
    if not value:
        raise AssertionError(text)


class Wire:
    def __init__(self, model, parent=False):
        self.model, self.parent, self.requests, self.task_aliases = model, parent, [], []
        self.replan_args = None
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size < 2 * 1024 * 1024:
                    self.send_error(413)
                    return
                body = json.loads(self.rfile.read(size))
                owner.requests.append({"path": self.path, "body": body})
                if body.get("model") != owner.model or body.get("stream") is not True or len(owner.requests) > 5:
                    self.send_error(400)
                    return
                if owner.parent and len(owner.requests) == 1 and owner.replan_args is not None:
                    calls = [{"index": 0, "id": "call_actual_model_replan", "type": "function", "function": {
                        "name": "eeas_workflow_replan", "arguments": json.dumps(owner.replan_args)}}]
                    delta, finish = {"role": "assistant", "tool_calls": calls}, "tool_calls"
                elif owner.parent and len(owner.requests) == (2 if owner.replan_args else 1):
                    calls = [{"index": i, "id": "call_workflow_" + str(i), "type": "function", "function": {
                        "name": "task", "arguments": json.dumps({"description": "Actual planned review " + str(i),
                        "prompt": "Return a short original child review", "subagent_type": alias})}}
                        for i, alias in enumerate(owner.task_aliases)]
                    delta, finish = {"role": "assistant", "tool_calls": calls}, "tool_calls"
                else:
                    delta, finish = {"role": "assistant", "content": "ACTUAL_PLUGIN_NATIVE_" + owner.model}, "stop"
                chunks = [{"id": "chatcmpl-workflow", "object": "chat.completion.chunk", "created": 0, "model": owner.model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {"id": "chatcmpl-workflow", "object": "chat.completion.chunk", "created": 0, "model": owner.model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}}]
                raw = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def start(self):
        self.thread.start()
        return self
    @property
    def url(self):
        return "http://127.0.0.1:" + str(self.server.server_port) + "/v1"
    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


class PlanAPI:
    """HTTP capability fixture backed by the actual Core; production API is Root's separate qualification."""
    def __init__(self, base, owner, plan):
        self.core = DynamicWorkflowStore(base / "actual-workflow.sqlite3")
        self.created = self.core.create(owner["project_id"], plan, operation_id="seed_user_plan", workflow_id="workflow", actor="USER")
        self.requests, self.receipts, self.prepared = [], {}, None
        self.owner = owner
        parent = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(raw)
                if self.path == "/api/dynamic-workflow/plugin-events":
                    check(self.headers.get("Authorization") == "Bearer " + "e" * 48)
                    self.send_response(204)
                    self.end_headers()
                    return
                check(self.path == "/api/dynamic-workflow/plugin-plan" and self.headers.get("Authorization") == "Bearer " + "p" * 48)
                check(set(body) == {"schema_version", "owner_ref", "operation_id", "expected_ref", "plan", "reason"} and body["owner_ref"] == parent.owner)
                parent.requests.append(body)
                from packages.domain.tasks import digest
                version = parent.core.replan(parent.owner["project_id"], "workflow", body["plan"],
                    expected_ref=body["expected_ref"], operation_id="core_fixture_" + body["operation_id"], actor="MODEL", reason=body["reason"])
                actual_plan = version["plan"]
                policy_nodes = [{"id": node["id"], "title": node["title"], "agent_policy": node["agent_policy"]} for node in actual_plan["nodes"]]
                dependencies = [{"source": dependency, "target": node["id"]} for node in actual_plan["nodes"] for dependency in node["depends_on"]]
                parent.policy_ref = write_workflow_policy(parent.prepared["admission"], nodes=policy_nodes, plan_ref=version["workflow_ref"],
                    dependencies=dependencies, expected_ref=parent.prepared["admission"]["policy_ref"])
                result = {**version, "operation_id": body["operation_id"], "core_operation_id": version["operation_id"],
                    "plugin_request_sha256": digest(body), "write_status": "COMMITTED", "runtime_activation": [{"session_id": "owner", "generation": 1, "status": "NEXT_DELEGATION"}]}
                parent.receipts[body["operation_id"]] = result
                response = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
    @property
    def sink(self):
        return "http://127.0.0.1:" + str(self.server.server_port) + "/api/dynamic-workflow/plugin-events"
    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)


@pytest.mark.parametrize("with_replan", [False, True], ids=["existing-plan-task", "native-replan-core-task"])
def test_actual_fixed_cli_loads_plugin_and_original_task_uses_two_provider_models_with_real_variants(tmp_path, with_replan):
    check(os.name == "posix", "This qualification is Linux only; Windows is NOT_RUN")
    check(CLI.is_file() and hashlib.sha256(CLI.read_bytes()).hexdigest() == CLI_SHA[os.name], "Pinned original CLI is mandatory")
    fast, deep = Wire("model-fast", True).start(), Wire("model-deep").start()
    base = tmp_path
    home = base / "home"
    work = base / "selected-original-workspace"
    work.mkdir()
    for name in ("config", "data", "cache", "state", "tmp"):
        (home / name).mkdir(parents=True)
    owner = {"project_id": "runtime", "session_ref": {"id": "owner", "revision": 1, "sha256": "a" * 64}, "generation": 1}
    nodes = [{"id": "fastnode", "agent_policy": {"model": {"mode": "MANUAL", "value": "fast"}, "depth": {"mode": "MANUAL", "value": "low"}}},
             {"id": "deepnode", "agent_policy": {"model": {"mode": "MANUAL", "value": "deep"}, "depth": {"mode": "MANUAL", "value": "high"}}}]
    catalog = [{"id": ident, "provider_id": "wire-" + ident, "model_id": "model-" + ident,
                "variants": [None, "low", "high"], "read_only": True} for ident in ("fast", "deep")]
    plan_api = None
    if with_replan:
        for node in nodes:
            node["title"] = "Actual review " + node["id"]
        initial_plan = {"title": "Actual initial plan", "goal": "Review original evidence", "nodes": nodes, "questions": [], "external_refs": []}
        plan_api = PlanAPI(base, owner, initial_plan)
    prepared = prepare_workflow_plugin(base / "workflow-runtime", owner_ref=owner, catalog=catalog, nodes=nodes,
        plan_ref=plan_api.created["workflow_ref"] if plan_api else None, sink_url=plan_api.sink if plan_api else None, model_replan=with_replan)
    if plan_api:
        plan_api.prepared = prepared
        proposed = {**initial_plan, "goal": "Review original evidence and document remaining uncertainties"}
        fast.replan_args = {"operation_id": "native_model_replan", "expected_ref": plan_api.created["workflow_ref"], "plan": proposed, "reason": "Refine the goal from newly reviewed evidence"}
    # Deliberately request the wrong catalog/depth for each node; the real before
    # hook must enforce independent manual locks without replacing native task.
    fast.task_aliases = [next(row["alias"] for row in prepared["aliases"] if row["node_id"] == node and row["route_id"] == "fast" and row["variant"] is None)
                         for node in ("fastnode", "deepnode")]
    permission = {"*": "deny", "task": {"*": "deny", "eeas_wf_*": "allow"}, "eeas_workflow_graph": "allow"}
    if with_replan:
        permission.update(eeas_workflow_replan="allow", eeas_workflow_plan_operation="allow")
    config = {"autoupdate": False, "share": "disabled", "snapshot": False, "formatter": False, "lsp": False,
              "instructions": [], "skills": {"paths": []}, "mcp": {}, "permission": permission,
              "enabled_providers": ["wire-fast", "wire-deep"], "model": "wire-fast/model-fast", "small_model": "wire-fast/model-fast",
              "agent": {"build": {"model": "wire-fast/model-fast", "steps": 8, "permission": permission}},
              "provider": {}, **prepared["config_fragment"]}
    for ident, wire in (("fast", fast), ("deep", deep)):
        config["provider"]["wire-" + ident] = {"npm": "@ai-sdk/openai-compatible", "name": "Original fixture " + ident,
            "options": {"baseURL": wire.url, "apiKey": "only-external-fixture-key", "timeout": 20000},
            "models": {"model-" + ident: {"tool_call": True, "reasoning": True, "attachment": False,
                "limit": {"context": 65536, "output": 1024}, "modalities": {"input": ["text"], "output": ["text"]},
                "variants": {"low": {"reasoningEffort": "low"}, "high": {"reasoningEffort": "high"}}}}}
    config_path = home / "config" / "opencode.json"
    config_path.write_text(json.dumps(config))
    env = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
    env.update({"HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / "config"), "XDG_DATA_HOME": str(home / "data"),
        "XDG_CACHE_HOME": str(home / "cache"), "XDG_STATE_HOME": str(home / "state"), "TMPDIR": str(home / "tmp"),
        "OPENCODE_CONFIG": str(config_path), "OPENCODE_SERVER_USERNAME": "eeas", "OPENCODE_SERVER_PASSWORD": "fixed-plugin-qualification-only",
        "OPENCODE_PURE": "false", "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_MODELS_FETCH": "true", "OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER": "true",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "true", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true", "OPENCODE_DISABLE_LSP_DOWNLOAD": "true", "OPENCODE_DISABLE_FFF": "true",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true", "OPENCODE_DISABLE_CLAUDE_CODE": "true", "OPENCODE_DISABLE_EMBEDDED_WEB_UI": "true", "OPENCODE_CLIENT": "eeas",
        "npm_config_offline": "true", "npm_config_ignore_scripts": "true", "npm_config_audit": "false", "npm_config_fund": "false", "npm_config_cache": str(home / "npm-cache")})
    if with_replan:
        env.update(EEAS_WORKFLOW_PLAN_TOKEN="p" * 48, EEAS_WORKFLOW_EVENT_TOKEN="e" * 48)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    url = "http://127.0.0.1:" + str(port)
    def request(method, route, body=None, timeout=15):
        raw = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url + route, data=raw, method=method,
            headers={"Authorization": "Basic " + base64.b64encode(b"eeas:fixed-plugin-qualification-only").decode(), "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(4 * 1024 * 1024)
        return json.loads(raw) if raw else None
    with (base / "original-cli.log").open("wb") as log:
        process = subprocess.Popen([str(CLI), "serve", "--hostname", "127.0.0.1", "--port", str(port)], cwd=work, env=env, stdout=log, stderr=log)
        try:
            until = time.monotonic() + 30
            while True:
                try:
                    health = request("GET", "/global/health")
                    break
                except Exception:
                    check(process.poll() is None and time.monotonic() < until, "Actual original CLI did not start")
                    time.sleep(.1)
            check(health == {"healthy": True, "version": "1.19.0"})
            agents = request("GET", "/agent", timeout=30)
            check(all(any(agent["name"] == row["alias"] for agent in agents) for row in prepared["aliases"]), "Actual Bun loader did not register all aliases")
            session = request("POST", "/session", {"title": "Actual plugin original task"})
            reply = request("POST", "/session/" + session["id"] + "/message", {"agent": "build", "model": {"providerID": "wire-fast", "modelID": "model-fast"},
                "parts": [{"type": "text", "text": "Use the two actual planned review aliases"}]}, timeout=60)
            (base / "actual-reply.json").write_text(json.dumps(reply, indent=2))
            (base / "actual-wire-requests.json").write_text(json.dumps({"fast": fast.requests, "deep": deep.requests}, indent=2))
            check(len(fast.requests) == (4 if with_replan else 3) and len(deep.requests) == 1, "Original loop did not execute the exact expected native tools and two children")
            fast_child = fast.requests[2 if with_replan else 1]["body"]
            deep_child = deep.requests[0]["body"]
            check(fast_child.get("model") == "model-fast" and fast_child.get("reasoning_effort") == "low", "Fast child reasoning depth not received by real wire")
            check(deep_child.get("model") == "model-deep" and deep_child.get("reasoning_effort") == "high", "Deep child reasoning depth not received by real wire")
            children = request("GET", "/session/" + session["id"] + "/children")
            child_messages = {child["id"]: request("GET", "/session/" + child["id"] + "/message") for child in children}
            (base / "actual-child-messages.json").write_text(json.dumps(child_messages, indent=2))
            check(len(children) == 2, "Two actual original child sessions required")
            until = time.monotonic() + 10
            while True:
                projected = json.loads(Path(prepared["admission"]["state_path"]).read_bytes())
                delegations = [edge for edge in projected["actual"]["edges"] if edge["kind"] == "DELEGATION"]
                observed = [item.get("observed_model") for item in projected["actual"]["nodes"] if item.get("observed_model")]
                if (len(delegations) == 2 and all(edge["observed_tool_status"] == "completed" for edge in delegations)
                        and any(item.get("variant") == "low" for item in observed) and any(item.get("variant") == "high" for item in observed)):
                    break
                check(time.monotonic() < until, "Actual event projection lacks original delegation evidence")
                time.sleep(.1)
            check(all(record["effect_status"] == "SELECTED_NOT_YET_OBSERVED" for record in projected["actual"]["dispatches"]), "Alias selection must not fake effective inference")
            check(any(item.get("observed_model", {}).get("variant") == "high" for item in projected["actual"]["nodes"] if item.get("observed_model")), "Actual assistant depth evidence missing")
            if plan_api:
                check(len(plan_api.requests) == 1, "Native replan must make one actual HTTP POST")
                check(plan_api.core.get("runtime", "workflow")["workflow_ref"]["revision"] == 2, "Actual Core revision did not advance exactly once")
                check(projected["plan"]["plan_ref"]["revision"] == 2, "Committed plan was not synchronized into route policy")
                check(any(tool["function"]["name"] == "eeas_workflow_replan" and tool["function"]["parameters"].get("properties", {}).get("plan", {}).get("type") == "object" for tool in fast.requests[0]["body"]["tools"]), "Actual native model must receive real replan object schema")
                (base / "actual-core-plan-receipt.json").write_text(json.dumps(plan_api.receipts, indent=2))
            (base / "qualification.json").write_text(json.dumps({"qualification": "ACTUAL_PINNED_ORIGINAL_CLI_PLUGIN_TASK_LOOP_EXTERNAL_MODEL_FIXTURES_ONLY", "model_calls": 5 if with_replan else 4,
                "source_cli_sha256": CLI_SHA[os.name], "two_providers": True, "two_models": True, "reasoning_efforts_received": ["low", "high"],
                "native_model_replan_actual_core": with_replan, "production_api_adapter": "NOT_RUN_SEPARATE_ROOT_QUALIFICATION", "native_windows": "NOT_RUN", "company_gateway": "NOT_RUN", "paid_model_calls": 0}, indent=2))
            request("POST", "/instance/dispose")
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=8)
            finally:
                fast.close()
                deep.close()
                if plan_api:
                    plan_api.close()
