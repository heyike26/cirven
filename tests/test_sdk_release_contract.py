"""Final SDK contracts at real extension, HTTP and release/restore boundaries."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch
import zipfile

from claude_agent_sdk import ClaudeSDKClient
import pytest

from conftest import ROOT
from packages.adapters.claude_sdk import ClaudeBridge, LiveBlocked
from packages.domain.store import Conflict
from packages.domain.tasks import TaskService
from packages.extensions.service import ExtensionRegistry
from scripts import native_user_state
from test_documents import project
from test_tasks import inputs
from test_extensions import archive, activate
from test_claude_sessions import FIXTURE_ENV, NativeFactory, SessionWire
from test_task_sdk_api import configured, formal, until_review


@pytest.fixture(autouse=True)
def select_claude_sdk(monkeypatch):
    # These contracts exercise the installed Claude SDK and its saved execution records.
    monkeypatch.setenv("EEAS_NATIVE_RUNTIME", "claude-agent-sdk")


def run_snapshot(*arguments):
    result = subprocess.run([sys.executable, str(ROOT / "scripts/snapshot_data.py"), *map(str, arguments)],
                            cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_sdk_task_binding_accepts_frozen_extension_combination_and_rejects_tamper(client, tmp_path):
    pid = project(client)
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    registry = ExtensionRegistry(service, tmp_path)
    version = registry.qualify(registry.import_snapshot(archive(1))["id"])
    activate(registry, version)
    registry.pin(pid, version["id"])
    doc, requirement, task_body = inputs(client, pid)
    task = client.post(f"/api/projects/{pid}/tasks", json=task_body).json()
    run = client.post(f"/api/projects/{pid}/tasks/{task['id']}/runs", json={"revision": 1, "operation_id": "original-bound"}).json()
    assert task["spec"]["stack_lock_hash"] != task["spec"]["base_stack_hash"]
    request = {"prompt": "Read this original frozen task", "needs_tools": False, "max_turns": 1, "timeout_seconds": 30,
        "task_binding": {"run_id": run["id"], "task_id": task["id"], "task_revision": 1,
                         "spec_hash": task["sha256"], "stack_lock_hash": run["stack_lock_hash"]}}
    bridge = ClaudeBridge(service, tmp_path)
    assert bridge._scope(pid, request)["input_hash"]
    assert "OS_ISOLATION_NOT_QUALIFIED" in bridge.preflight(True)
    Path(registry._row(version["id"])["directory"], "plugin.py").write_text("original tamper fixture\n")
    with pytest.raises(Conflict):
        bridge._scope(pid, request)


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_native_numbers_remain_unknown_and_actual_http_events_export_are_strict_json(client, tmp_path, number):
    pid = project(client)
    bridge = client.app.state.bridge
    request = {"prompt": "Original numeric fixture", "needs_tools": False, "max_turns": 1, "timeout_seconds": 30}

    class NumericWire(SessionWire):
        async def result(self, terminal_reason="completed", is_error=False):
            await self.send.send({"type": "result", "subtype": "success", "duration_ms": 1, "duration_api_ms": 1,
                "is_error": False, "num_turns": 1, "session_id": self.session_id, "terminal_reason": "completed",
                "total_cost_usd": number, "usage": {"input_tokens": number, "nested": [number]}, "result": "original result"})

    def installed_sdk(options):
        return ClaudeSDKClient(options=options, transport=NumericWire(options, number))

    with patch.dict(os.environ, FIXTURE_ENV), patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=installed_sdk):
        row = asyncio.run(bridge.submit(pid, "original-numeric", request))
        response = client.get(f"/api/projects/{pid}/agent-executions/{row['id']}/events")
        assert response.status_code == 200
        strict = json.dumps(response.json(), allow_nan=False)
        result = json.loads(row["result"])
        assert result["total_cost_usd"] is None
        assert result["usage"]["input_tokens"] is None and result["usage"]["nested"] == [None]
        assert result["budget"]["within_estimate_limit"] is None
        exported = bridge.export_result(pid, row["id"])
        json.dumps(exported, allow_nan=False)
        assert not exported["live_evidence"] and exported["verification_status"] == "NOT_RUN"
        assert any(event["payload"]["type"] == "sdk_numeric_invalid" for event in response.json()["events"])
        with pytest.raises(LiveBlocked, match="SESSION_COST_UNKNOWN"):
            bridge.control_preflight(pid, row["id"], "resume", expected_session_id=row["session_id"])


def test_legacy_nonfinite_history_projection_is_json_safe_without_rewriting_immutable_event(client, tmp_path):
    pid = project(client)
    bridge = client.app.state.bridge
    request = {"prompt": "Original legacy history", "needs_tools": False, "max_turns": 1, "timeout_seconds": 30}
    workspace = tmp_path / "agent-workspaces" / "original-legacy"
    workspace.mkdir(parents=True)
    row, created = bridge._record(pid, "original-legacy", request, workspace, "mocked-sdk-transport")
    legacy = '{"type":"ResultMessage","native":{"total_cost_usd":NaN,"usage":{"original":Infinity}}}'
    legacy_result = '{"total_cost_usd":NaN,"usage":null,"verification_status":"NOT_RUN"}'
    with bridge.service.connect() as connection:
        connection.execute("INSERT INTO agent_events VALUES (?,1,?,?)", (row["id"], legacy, "2026-10-12T00:00:00+00:00"))
        connection.execute("UPDATE agent_executions SET execution_status='RETURNED',result=? WHERE id=?", (legacy_result, row["id"]))
    response = client.get(f"/api/projects/{pid}/agent-executions/{row['id']}/events")
    assert response.status_code == 200
    json.dumps(response.json(), allow_nan=False)
    projected = client.get(f"/api/projects/{pid}/agent-executions/{row['id']}").json()
    assert json.loads(projected["result"])["total_cost_usd"] is None
    assert projected["result_projection"]["immutable_record_rewritten"] is False
    with bridge.service.connect() as connection:
        assert connection.execute("SELECT payload FROM agent_events WHERE execution_id=?", (row["id"],)).fetchone()[0] == legacy
        assert connection.execute("SELECT result FROM agent_executions WHERE id=?", (row["id"],)).fetchone()[0] == legacy_result


def test_native_sdk_checkpoint_survives_actual_snapshot_restore_and_deleted_original_root(tmp_path):
    source_install = tmp_path / "original-install"
    target_install = tmp_path / "private-restored-install"
    data = source_install / "user-data"
    # Only package compatibility and migration bytes are needed by the private
    # maintenance tool. No executable from this structural fixture is launched.
    compatibility = {name: b"private backup compatibility fixture" for name in native_user_state.COMPATIBILITY}
    migration_dir = ROOT / "packages/domain/migrations"
    compatibility.update({p.relative_to(ROOT).as_posix(): p.read_bytes() for p in migration_dir.glob("*.sql")})
    rows = []
    for name, raw in compatibility.items():
        path = source_install / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        rows.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    (source_install / "PACKAGE_MANIFEST.json").write_text(json.dumps({"schema": "eeas.native-companion-package.v1", "files": rows}))
    shutil.copytree(source_install, target_install)
    original = ClaudeBridge(TaskService(data / "eeas.sqlite3", ROOT), data)
    pid = original.service.create_project("SDK restore", "")["id"]
    request = {"prompt": "Original portable native session", "needs_tools": False, "max_turns": 1,
               "timeout_seconds": 30, "source_files": {"src/原创.txt": "original public source\n"}}
    factory = NativeFactory([0.002, 0.004])
    archive_path = tmp_path / "portable.zip"
    destination = tmp_path / "便携恢复"
    private_backup = tmp_path / "private-backup"
    with patch.dict(os.environ, FIXTURE_ENV), patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(original.submit(pid, "original-before-backup", request))
        old_checkpoint = original._checkpoint(pid, parent)
        old_events = original.stream_events(pid, parent["id"])
        state = Path(parent["workspace"]) / ".eeas-sdk-state"
        for name in (".credentials.json", "settings.json", ".claude.json", "arbitrary-cache.json"):
            (state / name).write_text("original private-state sentinel; must not leave backup", encoding="utf-8")
        private = native_user_state.backup(source_install, private_backup)
        backed_up = run_snapshot("backup", "--data", data, "--output", archive_path)
        restored = run_snapshot("restore", "--archive", archive_path, "--sha256", backed_up["sha256"], "--destination", destination)
        assert restored["status"] == "PASS"
        assert restored["claude_sdk_continuation"] == backed_up["claude_sdk_continuation"] == "REQUIRES_PRIVATE_BACKUP_WITH_ORIGINAL_TRANSCRIPT"
        with zipfile.ZipFile(archive_path) as bundle:
            assert any(name.startswith("agent-transcripts/") for name in bundle.namelist())
            assert not any(".eeas-sdk-state" in name for name in bundle.namelist())
        shutil.rmtree(source_install)
        portable = ClaudeBridge(TaskService(destination / "eeas.sqlite3", ROOT), destination)
        assert portable.inspect(pid, parent["id"]) == parent and portable.stream_events(pid, parent["id"]) == old_events
        with pytest.raises(Conflict, match="original Claude SDK transcript is missing"):
            portable.control_preflight(pid, parent["id"], "resume", expected_session_id=parent["session_id"])
        assert len(factory.transports) == 1
        native_user_state.restore(private_backup, target_install, private["manifest_sha256"])
        private_data = target_install / "user-data"
        bridge = ClaudeBridge(TaskService(private_data / "eeas.sqlite3", ROOT), private_data)
        assert bridge._checkpoint(pid, parent) == old_checkpoint
        assert bridge.inspect(pid, parent["id"]) == parent and bridge.stream_events(pid, parent["id"]) == old_events
        check = bridge.control_preflight(pid, parent["id"], "resume", expected_session_id=parent["session_id"])
        assert check["budget"]["remaining_usd"] == "0.008"
        child = asyncio.run(bridge.resume(pid, parent["id"], "original-after-restore", "Original followup",
                                           expected_session_id=parent["session_id"]))
        assert child["execution_status"] == "RETURNED"
        assert Path(child["workspace"]).is_relative_to(private_data)
        assert bridge.inspect(pid, parent["id"])["workspace"] == parent["workspace"]
        assert not bridge.export_result(pid, child["id"])["live_evidence"]
        with bridge.service.connect() as connection:
            frozen = dict(connection.execute("SELECT * FROM agent_checkpoints WHERE execution_id=?", (parent["id"],)).fetchone())
        assert frozen == old_checkpoint


def test_approved_native_review_missing_private_transcript_refuses_http_continuation(client, monkeypatch):
    pid, _, _, _, _, route, body = formal(client)
    factory = NativeFactory([0.002])
    configured(monkeypatch, factory)
    admission = client.post(route + "/native-reviews", json=body)
    assert admission.status_code == 202
    review_id = admission.json()["admission_id"]
    parent = until_review(client, pid, review_id, {"RETURNED"})["native_execution"]
    review = f"/api/projects/{pid}/native-reviews/{review_id}"
    result = client.get(review + "/result").json()
    transcripts = list((Path(parent["workspace"]) / ".eeas-sdk-state" / "projects").glob("*/" + parent["session_id"] + ".jsonl"))
    assert len(transcripts) == 1
    transcripts[0].unlink()
    for kind in ("resume", "fork"):
        response = client.post(review + "/" + kind, json={"operation_id": "missing-" + kind, "expected_session_id": parent["session_id"], "prompt": "Follow up"})
        assert response.status_code == 409
        assert "original Claude SDK transcript is missing" in response.json()["error"]["message"]
    assert client.get(review + "/result").json() == result
    assert len(factory.transports) == 1


def test_actual_release_bundle_fresh_python310_runs_bundled_cli_and_keeps_live_unconfigured(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_release_bundle import build
    from bundle_support import extract_bundle
    target = "windows-py310" if os.name == "nt" else "linux-py310"
    evidence_dir = Path(os.environ.get("EEAS_EVIDENCE_DIR", tmp_path))
    if (ROOT / ".git").exists():
        parent_checkout = Path(__file__).resolve().parents[3] / "EEAS_External_Harness_v0.5"
        wheel_root = Path(os.environ.get("EEAS_TEST_WHEEL_ROOT", str(parent_checkout if parent_checkout.is_dir() else ROOT)))
        selection = json.loads((ROOT / "profiles/offline-dependencies.json").read_bytes())
        if not (wheel_root / selection["targets"][target] / "manifest.json").is_file():
            pytest.skip("Target wheelhouse is required; set EEAS_TEST_WHEEL_ROOT to the verified dependency checkout")
        history = "reports/E04-E09-sdk-json-stack-final/20261012T113201408989Z"
        receipt = build(ROOT, target, wheel_root, [history], tmp_path / "sdk-preview.zip")
        release_source = "current-checkout-build"
    else:
        audit = ROOT / "reports/E10-final-delivery-audit/20261012T144428681946Z/delivery-audit.json"
        records = json.loads(audit.read_bytes())["trusted_artifacts_checked"]
        matched = [r for r in records if r["path"].startswith(f"artifacts/eeas-preview-{target}-") and r["path"].endswith(".zip")]
        assert len(matched) == 1
        receipt = matched[0]
        release_source = "archived-20261012-preview"
    application = tmp_path / "中文 独立解包 程序"
    manifest = extract_bundle(ROOT / receipt["path"], application, receipt["sha256"])
    assert manifest["target"] == target
    (evidence_dir / "sdk-release-input.json").write_text(json.dumps({
        "source": release_source, "archive_sha256": receipt["sha256"], "target": target,
        "source_commit": manifest["source_commit"],
    }, indent=2), encoding="utf-8")
    result = subprocess.run([sys.executable, "-B", str(application / "scripts/portable_acceptance.py"),
        "--output", str(evidence_dir / "sdk-fresh-portable")], cwd=application, capture_output=True, text=True, check=False)
    (evidence_dir / "sdk-fresh-portable-stdout.txt").write_text(result.stdout, encoding="utf-8")
    (evidence_dir / "sdk-fresh-portable-stderr.txt").write_text(result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    python = application / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    program = """import importlib.metadata,json,pathlib,subprocess,sys
import claude_agent_sdk
from apps.api.config import Settings
from apps.api.main import create_app
root=pathlib.Path(claude_agent_sdk.__file__).parent
cli=root/'_bundled'/('claude.exe' if sys.platform=='win32' else 'claude')
r=subprocess.run([str(cli),'--version'],capture_output=True,text=True,check=False)
caps=create_app(Settings(pathlib.Path(sys.argv[1]))).state.bridge.describe_capabilities()
print(json.dumps({'python':list(sys.version_info[:2]),'sdk':importlib.metadata.version('claude-agent-sdk'),
'cli_exit':r.returncode,'cli_stdout':r.stdout,'cli_stderr':r.stderr,'caps':caps}))
"""
    empty_home = tmp_path / "empty-sdk-user-home"
    empty_home.mkdir()
    environment = {key: value for key, value in os.environ.items() if key in {"PATH", "SystemRoot", "WINDIR", "COMSPEC", "TMP", "TEMP"}}
    environment.update(HOME=str(empty_home), USERPROFILE=str(empty_home), CLAUDE_CONFIG_DIR=str(empty_home / "empty-claude-config"), EEAS_NATIVE_RUNTIME="claude-agent-sdk",
                       ANTHROPIC_API_KEY="", EEAS_MODEL_ID="", EEAS_LIVE_MAX_COST_USD="", PYTHONDONTWRITEBYTECODE="1")
    checked = subprocess.run([str(python), "-c", program, str(tmp_path / "fresh-sdk-data")], cwd=application,
                             env=environment, capture_output=True, text=True, check=False)
    assert checked.returncode == 0, checked.stderr
    observed = json.loads(checked.stdout)
    (evidence_dir / "sdk-fresh-runtime.json").write_text(json.dumps(observed, ensure_ascii=False, indent=2), encoding="utf-8")
    assert observed["python"] == [3, 10] and observed["sdk"] == "0.2.165"
    assert observed["cli_exit"] == 0 and "2.1.294" in observed["cli_stdout"]
    assert not observed["caps"]["developer_login_used"] and not observed["caps"]["credential_present"]
    assert "NO_CREDENTIALS" in observed["caps"]["planning_blockers"]
    assert "OS_ISOLATION_NOT_QUALIFIED" in observed["caps"]["code_execution_blockers"]
    assert observed["caps"]["live_verification"] == "BLOCKED"
