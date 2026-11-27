"""Real local records/HTTP/official SDK and one exact original-CLI observation.

External provider returns a held protocol denial; no paid model, GUI, desktop
input, user credentials or fake native server is used for qualification.
"""
import asyncio
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import site
import sys
import threading

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import psutil
import pytest

from apps.api.native_capabilities import register_native_capability_routes
from packages.adapters.opencode_desktop import OpenCodeDesktopTransport
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.store import Conflict, InvalidInput, Store
from packages.domain.tasks import canonical, digest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "a" * 64
SKILL = "---\nname: page-review\ndescription: Review the selected immutable page.\n---\nRead the exact context; no mutation permission. 中文🙂\n"


@pytest.fixture
def service(tmp_path):
    return NativeCapabilities(Store(tmp_path / "capabilities.sqlite3"), tmp_path)


def skill_input(**changes):
    return {"operation_id": "import-original", "id": "page-review", "expected_revision": 0,
        "content": SKILL, "enabled": False, **changes}


def local_mcp(tmp_path, *, broken=False):
    source = tmp_path / "original_server.py"
    source.write_text("import sys\nsys.path.insert(0, " + repr(site.getsitepackages()[0]) + ")\n" +
        ("raise RuntimeError('original startup failure')\n" if broken else
        "from mcp.server import MCPServer\nserver=MCPServer('original-capability-fixture')\n"
        "@server.tool()\ndef local_echo(text:str)->str:\n    return text\n"
        "server.run(transport='stdio')\n"))
    command = [str(Path(sys.executable).resolve()), str(source)]
    config = {"type": "local", "command": command,
        "files_sha256": {x: hashlib.sha256(Path(x).read_bytes()).hexdigest() for x in command},
        "enabled": True, "timeout": 5000}
    return config, source


def configure(service, tmp_path, **kwargs):
    config, source = local_mcp(tmp_path, **kwargs)
    result = service.configure_mcp({"operation_id": "save-mcp", "id": "original-local",
        "expected_revision": 0, "configuration": config})
    return result["config_ref"], config, source


@pytest.mark.parametrize('timeout', [100, 60000, 120000])
def test_explicit_cold_mcp_deadline_is_saved_without_start_or_load_claim(service, tmp_path, timeout):
    config, _ = local_mcp(tmp_path)
    config.update(timeout=timeout, enabled=False)
    receipt = service.configure_mcp({'operation_id':'cold-start-timeout','id':'cold-start',
        'expected_revision':0,'configuration':config})
    saved = service._configuration('mcp', receipt['config_ref'])['configuration']
    assert saved['timeout'] == timeout and saved['enabled'] is False
    assert not receipt['loaded_in_original_session'] and not receipt['qualified']


@pytest.mark.parametrize('timeout', [True, 0, 99, 120001, 60000.0])
def test_cold_mcp_deadline_bounds_and_integer_type_remain_explicit(service, tmp_path, timeout):
    config, _ = local_mcp(tmp_path)
    config['timeout'] = timeout
    with pytest.raises(InvalidInput):
        service.configure_mcp({'operation_id':'invalid-cold-timeout','id':'cold-start',
            'expected_revision':0,'configuration':config})


def test_skill_original_bytes_discovery_enable_restart_and_frozen_future_binding(service):
    result = service.import_skill(skill_input())
    assert result["saved"] and not result["loaded_in_original_session"] and not result["qualified"]
    assert service.import_skill(skill_input()) == result
    row = service.get()["skills"][0]
    assert row["description"] == "Review the selected immutable page." and row["integrity"] == "CURRENT"
    assert row["native_binding"] == "NOT_ADAPTED" and not row["permission_granted"]
    before = service.native_binding_snapshot()
    assert before["value"]["native_config_fragment"]["skills"]["paths"] == []
    enabled = service.set_enabled("skills", "page-review", {"operation_id": "enable-skill", "expected_revision": 1, "enabled": True})
    revived = NativeCapabilities(service.store, service.data_dir)
    row = revived.get()["skills"][0]
    assert row["enabled"] and row["config_ref"] == enabled["config_ref"]
    binding = revived.native_binding_snapshot()
    path = Path(binding["value"]["native_config_fragment"]["skills"]["paths"][0]) / "SKILL.md"
    assert path.read_text() == SKILL and not binding["value"]["permission_granted"]
    assert before["sha256"] == digest(before["value"]) and before["value"]["native_config_fragment"]["skills"]["paths"] == []
    assert Path(service.data_dir / "native-capabilities/skill-files/page-review/0000000001/SKILL.md").read_text() == SKILL
    with pytest.raises(Conflict):
        service.set_enabled("skills", "page-review", {"operation_id": "stale-enable", "expected_revision": 1, "enabled": False})
    with pytest.raises(Conflict):
        service.import_skill(skill_input(content=SKILL + "changed"))


@pytest.mark.parametrize("content", [
    "No YAML metadata", SKILL.replace("page-review", "another-name"),
    SKILL.replace("description:", "name: page-review\ndescription:"),
    "---\nname: page-review\ndescription: &anchor x\ncopy: *anchor\n---\nbody",
])
def test_unqualified_skill_metadata_cannot_enter_discovery(service, content):
    with pytest.raises(InvalidInput):
        service.import_skill(skill_input(content=content))
    assert service.get()["skills"] == []


def test_skill_file_change_not_advertised_as_current_or_bound(service):
    service.import_skill(skill_input(enabled=True))
    path = service.data_dir / "native-capabilities/skill-files/page-review/0000000001/SKILL.md"
    path.write_text(SKILL + "tampered")
    assert service.get()["skills"][0]["integrity"] == "INTEGRITY_ERROR"
    with pytest.raises(Conflict):
        service.native_binding_snapshot()


def test_interrupted_pointer_write_has_exact_operation_recovery(service, monkeypatch):
    original = service._write
    def fail_index(path, value, **kwargs):
        if path.name == "index.json":
            raise OSError("original interrupted pointer")
        return original(path, value, **kwargs)
    monkeypatch.setattr(service, "_write", fail_index)
    with pytest.raises(OSError):
        service.import_skill(skill_input())
    revived = NativeCapabilities(service.store, service.data_dir)
    assert revived.get()["skills"] == []
    saved = revived.import_skill(skill_input())
    assert revived.get()["skills"][0]["config_ref"] == saved["config_ref"]
    assert saved["config_ref"]["revision"] == 1


def test_mcp_save_does_not_start_connect_or_grant_tools(service, tmp_path):
    ref, configuration, _ = configure(service, tmp_path)
    listed = service.get()["mcp"][0]
    assert listed["config_ref"] == ref and listed["last_probe"] is None
    assert listed["probe_permitted"] and not listed["qualified"] and not listed["loaded_in_original_session"]
    binding = service.native_binding_snapshot()["value"]
    assert binding["native_config_fragment"]["mcp"][ref["id"]]["command"] == configuration["command"]
    assert "files_sha256" not in binding["native_config_fragment"]["mcp"][ref["id"]]
    assert not binding["permission_granted"]
    with pytest.raises(Conflict):
        asyncio.run(service.probe_mcp(ref["id"], {"config_ref": ref}))
    with pytest.raises(Conflict):
        asyncio.run(service.probe_mcp(ref["id"], {"config_ref": ref, "allowed_effects": ["PROCESS_START"], "network_mode": "STRICT_OFFLINE"}))
    assert not service.get(network_mode="STRICT_OFFLINE")["mcp"][0]["probe_permitted"]


def test_actual_official_sdk_initializes_and_lists_then_drops_current_on_revision_change(service, tmp_path):
    ref, _, _ = configure(service, tmp_path)
    receipt = asyncio.run(service.probe_mcp(ref["id"], {"config_ref": ref, "allowed_effects": ["PROCESS_START"]}))
    assert receipt["status"] == "INITIALIZED_AND_LISTED", receipt
    assert receipt["initialized"] and receipt["list_tools_succeeded"] and receipt["tool_count"] == 1
    assert receipt["tools"][0]["name"] == "local_echo" and receipt["tools"][0]["permission"] == "NOT_GRANTED"
    assert receipt["tools_invoked"] == 0 and not receipt["qualified"] and not receipt["offline_qualified"]
    assert receipt["cleanup"] == "OWNED_STDIO_CONTEXT_EXITED" and not receipt["loaded_in_original_session"]
    revived = NativeCapabilities(service.store, service.data_dir)
    assert revived.get()["mcp"][0]["last_probe"]["current_configuration"]
    revived.set_enabled("mcp", ref["id"], {"operation_id": "disable-mcp", "expected_revision": 1, "enabled": False})
    row = revived.get()["mcp"][0]
    assert not row["last_probe"]["current_configuration"] and not row["probe_permitted"]
    with pytest.raises(Conflict):
        asyncio.run(revived.probe_mcp(ref["id"], {"config_ref": ref, "allowed_effects": ["PROCESS_START"]}))


def test_mcp_original_startup_failure_is_preserved_and_not_connected(service, tmp_path):
    ref, _, _ = configure(service, tmp_path, broken=True)
    receipt = asyncio.run(service.probe_mcp(ref["id"], {"config_ref": ref, "allowed_effects": ["PROCESS_START"]}))
    assert receipt["status"] == "FAILED" and not receipt["initialized"] and receipt["tools_invoked"] == 0
    assert service.get()["mcp"][0]["last_probe"]["id"] == receipt["id"]


def test_real_mcp_timeout_unwinds_only_its_owned_child(service, tmp_path):
    marker = tmp_path / "owned-child.pid"
    source = tmp_path / "original_hanging_server.py"
    source.write_text("import os,time\nfrom pathlib import Path\nPath(" + repr(str(marker)) + ").write_text(str(os.getpid()))\ntime.sleep(30)\n")
    command = [str(Path(sys.executable).resolve()), str(source)]
    configuration = {"type": "local", "command": command, "files_sha256":
        {x: hashlib.sha256(Path(x).read_bytes()).hexdigest() for x in command}, "enabled": True, "timeout": 100}
    saved = service.configure_mcp({"operation_id": "save-hanging", "id": "hanging", "configuration": configuration})
    receipt = asyncio.run(service.probe_mcp("hanging", {"config_ref": saved["config_ref"], "allowed_effects": ["PROCESS_START"]}))
    assert receipt["status"] == "FAILED" and receipt["cleanup"] == "OWNED_STDIO_CONTEXT_UNWOUND"
    assert marker.is_file()
    # pid_exists is a read-only check on both targets. os.kill(pid, 0) has
    # different Windows behavior and must never become a process-control probe.
    assert not psutil.pid_exists(int(marker.read_text()))


def test_configuration_record_and_symlink_tampering_fail_before_probe(service, tmp_path):
    ref, _, source = configure(service, tmp_path)
    record = service.data_dir / "native-capabilities/mcp/original-local/0000000001.json"
    original = record.read_bytes(); record.write_bytes(original.replace(b'"enabled":true', b'"enabled":false'))
    with pytest.raises(Conflict):
        service.get()
    record.write_bytes(original)
    actual = source.with_suffix(".moved.py"); source.rename(actual); source.symlink_to(actual)
    with pytest.raises(InvalidInput):
        asyncio.run(service.probe_mcp(ref["id"], {"config_ref": ref, "allowed_effects": ["PROCESS_START"]}))


def test_command_source_hash_change_cannot_start_or_bind_mcp(service, tmp_path):
    ref, _, path = configure(service, tmp_path)
    path.write_text("raise RuntimeError('changed source')")
    assert service.get()["mcp"][0]["integrity"] == "INTEGRITY_ERROR"
    with pytest.raises(Conflict):
        asyncio.run(service.probe_mcp(ref["id"], {"config_ref": ref, "allowed_effects": ["PROCESS_START"]}))
    with pytest.raises(Conflict):
        service.native_binding_snapshot()


def test_remote_configuration_never_uses_hidden_auth_network_or_offline_claim(service):
    remote = {"type": "remote", "url": "https://mcp.example.invalid/mcp", "enabled": True, "timeout": 2000}
    saved = service.configure_mcp({"operation_id": "remote-declaration", "id": "remote", "configuration": remote})
    ref = saved["config_ref"]
    assert not service.get()["mcp"][0]["probe_permitted"]
    with pytest.raises(Conflict):
        asyncio.run(service.probe_mcp("remote", {"config_ref": ref, "allowed_effects": ["PROCESS_START"]}))
    with pytest.raises(InvalidInput):
        service.configure_mcp({"operation_id": "secret-rejection", "id": "secret", "configuration": {**remote, "headers": {"Authorization": "secret"}}})


@pytest.mark.parametrize("ident", ["CON", "NUL", "COM1", "LPT9"])
def test_windows_reserved_capability_paths_rejected_without_runtime(service, ident):
    with pytest.raises(InvalidInput):
        service.configure_mcp({"operation_id": "reserved-" + ident, "id": ident,
            "configuration": {"type": "remote", "url": "https://mcp.example.invalid/mcp", "enabled": False, "timeout": 2000}})
    assert service.get()["mcp"] == []


def test_mcp_case_alias_cannot_change_configuration_across_windows_and_linux(service):
    remote = {"type": "remote", "url": "https://mcp.example.invalid/mcp", "enabled": False, "timeout": 2000}
    service.configure_mcp({"operation_id": "upper", "id": "Server", "configuration": remote})
    with pytest.raises(Conflict):
        service.configure_mcp({"operation_id": "lower-alias", "id": "server", "configuration": remote})
    assert len(service.get()["mcp"]) == 1


def test_user_mcp_configuration_cannot_replace_execution_private_bridge(service):
    with pytest.raises(Conflict):
        service.configure_mcp({"operation_id": "replace-host-bridge", "id": "eeas_desktop",
            "configuration": {"type": "remote", "url": "https://mcp.example.invalid/mcp", "enabled": True, "timeout": 2000}})
    assert service.get()["mcp"] == []


def test_private_api_actual_records_deny_unknown_host_and_preserve_public_gaps(service, tmp_path):
    app = FastAPI()
    @app.exception_handler(Conflict)
    async def conflict(_request: Request, error: Conflict):
        return JSONResponse(status_code=409, content={"error": str(error)})
    authority = NativeCollaborationAuthority(tmp_path / "host", token=TOKEN, owner_pid=123, platform="posix")
    register_native_capability_routes(app, service.store, tmp_path, authority=authority, service=service)
    with TestClient(app) as client:
        assert client.get("/api/desktop-host/capabilities/get").status_code == 409
        response = client.post("/api/desktop-host/capabilities/skills/import", json=skill_input(), headers={"X-EEAS-Desktop-Host": TOKEN})
        assert response.status_code == 200 and response.json()["saved"]
        original = client.get("/api/desktop-host/capabilities/skills/page-review", headers={"X-EEAS-Desktop-Host": TOKEN})
        assert original.status_code == 200 and original.json()["content"] == SKILL
        state = client.get("/api/desktop-host/capabilities/get", headers={"X-EEAS-Desktop-Host": TOKEN}).json()
        assert state["skills"][0]["title"] == "page-review" and not state["silent_paid_fallback"]
        assert any(x["id"] == "long_lived_owner" and x["state"] == "NOT_ADAPTED" for x in state["base_interfaces"])
        assert state["provider_management"]["subscription_oauth_in_native_companion"] == "NOT_ADAPTED"
        rejected = client.post("/api/desktop-host/capabilities/executions/missing/probe?project_id=missing", headers={"X-EEAS-Desktop-Host": TOKEN})
        assert rejected.status_code == 409 and service.get()["host_methods"] == []


class HeldDenial:
    """Only the external inference endpoint is replaced; native CLI stays original."""
    def __init__(self):
        self.received, self.release = threading.Event(), threading.Event()
        self.count = 0
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                owner.count += 1; owner.received.set(); owner.release.wait(35)
                raw = b'{"error":{"message":"Original held protocol denial"}}'
                self.send_response(401); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw))); self.end_headers()
                try:
                    self.wfile.write(raw)
                except BrokenPipeError:
                    pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def start(self):
        self.thread.start(); return self
    def close(self):
        self.release.set(); self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)


def test_actual_fixed_original_owner_registry_config_permissions_mcp_and_expiry(tmp_path):
    cli = ROOT / "var/opencode-ui-offline-guard/opencode"
    assert cli.is_file() and hashlib.sha256(cli.read_bytes()).hexdigest() == CLI_SHA[os.name]
    wire = HeldDenial().start()
    store = Store(tmp_path / "actual.sqlite3")
    raw = "Explain the exact local test message. 当前内容🙂"
    message = {"raw_text": raw, "raw_text_sha256": hashlib.sha256(raw.encode()).hexdigest(), "context_ref": None}
    path = tmp_path / "original-message.json"; path.write_text(canonical(message))
    message_ref = {"id": "actual-message", "revision": 1, "sha256": digest(message)}
    def read_message(ref):
        assert ref == message_ref
        actual = json.loads(path.read_bytes())
        assert digest(actual) == ref["sha256"]
        return actual
    profile = {"schema": "eeas.opencode-provider.v1", "id": "capability-wire", "revision": 1,
        "version": VERSION, "source_commit": COMMIT, "executable": str(cli), "executable_sha256": CLI_SHA[os.name],
        "provider_id": "eeas-capability-wire", "protocol": "OPENAI_COMPATIBLE_CHAT_COMPLETIONS",
        "provider_url": "http://127.0.0.1:" + str(wire.server.server_port) + "/v1", "model_id": "capability-wire",
        "secret_ref": "secret:capability-test", "transport_kind": "MOCK_WIRE"}
    registry = OpenCodeProviderRegistry(lambda *_: copy.deepcopy(profile), lambda *_: "original-test-key")
    native = OpenCodeDesktopTransport(store, tmp_path, provider_registry=registry, message_reader=read_message)
    manager = NativeCapabilities(store, tmp_path, transport_reader=lambda: native)
    request = {"project_id": None, "operation_id": "original-capabilities", "prompt": raw, "mode": "OBSERVE",
        "desktop_intent": {"user_message_ref": message_ref, "context_ref": None},
        "provider_profile_ref": {"id": profile["id"], "revision": 1, "sha256": digest(profile)},
        "budget": {"max_calls": 1, "timeout_seconds": 40, "max_output_tokens": 512, "max_tool_calls": 1},
        "output_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False},
        "method_ids": ["read_message"]}
    async def check():
        row = native.prepare(request)
        with pytest.raises(Conflict):
            await manager.probe_execution(row["project_id"], row["id"])
        owner = asyncio.create_task(native.start(row["project_id"], row["id"]))
        try:
            assert await asyncio.to_thread(wire.received.wait, 30), "Original CLI did not reach the controlled wire"
            receipt = await manager.probe_execution(row["project_id"], row["id"], network_mode="STRICT_OFFLINE")
            assert receipt["status"] == "ACTUAL_ORIGINAL_OWNER_OBSERVED", receipt
            assert receipt["version"] == VERSION and any(x["name"] == "plan" for x in receipt["agents"])
            assert "task" in receipt["native_registered_tool_ids"] and not receipt["native_builtin_tools_invocable"]
            assert receipt["native_mcp"]["eeas_desktop"]["status"] == "connected"
            assert receipt["session_permission_matches_config"]
            assert receipt["host_methods"][0]["id"] == "read_message" and receipt["host_methods"][0]["execution_permitted"]
            assert receipt["host_methods"][0]["offline_qualified"] and not receipt["automatic_input_authorized"]
            assert not receipt["qualified_model_access"] and not receipt["native_windows_qualified"]
            public = canonical(receipt)
            assert all(secret not in public for secret in native.active[row["id"]]["secrets"])
            assert "baseURL" not in public and wire.count == 1
            active_rows = manager.get()["recent_executions"]
            assert active_rows[0]["id"] == row["id"] and active_rows[0]["connected_owner"]
            if os.environ.get("EEAS_CAPABILITIES_EVIDENCE"):
                destination = Path(os.environ["EEAS_CAPABILITIES_EVIDENCE"]); destination.mkdir(parents=True, exist_ok=True)
                (destination / "actual-original-owner.json").write_text(canonical({"receipt": receipt,
                    "qualification": "ORIGINAL_CLI_READONLY_CAPABILITY_ENDPOINTS_ONLY", "paid_model_calls": 0, "gui": "NOT_RUN"}))
            wire.release.set(); await owner
            with pytest.raises(Conflict):
                await manager.probe_execution(row["project_id"], row["id"])
            assert not manager.get()["recent_executions"][0]["connected_owner"]
            assert path.read_text() == canonical(message) and wire.count == 1
        finally:
            wire.release.set()
            if not owner.done():
                owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
            await native.close()
    try:
        asyncio.run(check())
    finally:
        wire.close()
