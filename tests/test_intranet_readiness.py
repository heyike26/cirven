import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("intranet_readiness", ROOT / "scripts/intranet_readiness.py")
readiness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readiness)


def original_bundle(tmp_path):
    root, wheel_root = tmp_path / "source", tmp_path / "wheel-source"
    (root / "profiles").mkdir(parents=True)
    (root / "state").mkdir()
    selected = {}
    for target in ("windows-py310", "linux-py310"):
        selected[target] = f"vendor/{target}/original"
        directory = wheel_root / selected[target]
        (directory / "wheels").mkdir(parents=True)
        original = directory / "wheels/original.whl"
        original.write_bytes(b"original checksum fixture; not a package that is installed")
        manifest = {"target": target, "python": "3.10", "architecture": "x86_64",
                    "files_sha256": {"wheels/original.whl": hashlib.sha256(original.read_bytes()).hexdigest()}}
        (directory / "manifest.json").write_text(json.dumps(manifest))
    (root / "profiles/offline-dependencies.json").write_text(json.dumps({"targets": selected}))
    (root / "state/DELIVERY.json").write_text(json.dumps({"source_commit": "a" * 40, "sha256": "b" * 64,
                                                         "release_status": "DEVELOPMENT_PREVIEW"}))
    return root, wheel_root


def test_linux_cannot_be_promoted_to_windows_by_environment_or_manifest(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("This negative identity fixture requires a non-Windows host")
    root, wheels = original_bundle(tmp_path)
    for key in ("EEAS_NATIVE_WINDOWS_PASS", "EEAS_OS_ISOLATION_QUALIFIED", "EEAS_SDK_LIVE_PASS", "EEAS_OS", "OS"):
        monkeypatch.setenv(key, "Windows")
    result = readiness.collect(root, wheels, "windows-py310", {})
    assert result["host"]["native_windows_identity"] is False
    assert result["host"]["identity_status"] == "NOT_NATIVE_WINDOWS"
    assert result["dependencies"]["status"] == "INTEGRITY_PASS"
    assert result["gates"]["native_windows"] == "BLOCKED"
    assert result["gates"]["os_isolation"] == "BLOCKED"
    assert result["gates"]["sdk_live"] == "BLOCKED"
    assert result["company_model"]["protocol_compatibility"] == "NOT_VALIDATED"
    assert result["readiness"] == "BLOCKED"


def test_missing_configuration_does_not_send_any_live_request(tmp_path, monkeypatch):
    root, wheels = original_bundle(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("No network or CLI prompt should be attempted with this fixture")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    result = readiness.collect(root, wheels, "windows-py310", {})
    assert result["configuration"]["status"] == "NOT_CONFIGURED"
    assert result["sdk"]["model_requests_sent"] == 0
    assert result["sdk"]["live_qualification"] == "BLOCKED"
    assert result["configuration"]["authentication_verified"] is False


def test_private_values_are_never_exported_or_qualified(tmp_path):
    root, wheels = original_bundle(tmp_path)
    env = {"ANTHROPIC_BASE_URL": "https://private-original-company-fixture.invalid/messages",
           "EEAS_MODEL_ID": "private-original-model-id-fixture", "ANTHROPIC_API_KEY": "private-original-api-key-fixture",
           "ANTHROPIC_AUTH_TOKEN": "private-original-bearer-token-fixture", "EEAS_LIVE_MAX_COST_USD": "0.0234567"}
    result = readiness.collect(root, wheels, "windows-py310", env)
    rendered = json.dumps(result)
    assert result["configuration"]["status"] == "PRESENT_NOT_QUALIFIED"
    assert result["configuration"]["budget_approval"] == "NOT_VALIDATED"
    assert result["gates"]["sdk_live"] == "BLOCKED"
    assert result["company_model"]["protocol_compatibility"] == "NOT_VALIDATED"
    for value in env.values():
        assert value not in rendered


@pytest.mark.parametrize("budget", ["NaN", "Infinity", "-1", "0", "101", "not-money"])
def test_nonfinite_out_of_bound_or_invalid_budget_never_qualifies(budget):
    result = readiness.configuration({"ANTHROPIC_BASE_URL": "https://original.invalid", "EEAS_MODEL_ID": "original",
                                      "ANTHROPIC_API_KEY": "original-fixture", "EEAS_LIVE_MAX_COST_USD": budget})
    assert result["budget_syntax_valid"] is False
    assert result["status"] != "PRESENT_NOT_QUALIFIED"
    assert result["authentication_verified"] is False


@pytest.mark.parametrize("endpoint", ["file:///private-fixture", "https://user:secret@original.invalid", "https://original.invalid?secret=fixture",
                                      "https://original.invalid:bad", "https://original.invalid:70000"])
def test_invalid_or_credential_bearing_url_not_echoed(endpoint):
    result = readiness.configuration({"ANTHROPIC_BASE_URL": endpoint})
    assert result["endpoint_syntax_valid"] is False
    assert endpoint not in json.dumps(result)


def test_bearer_token_for_developer_cli_is_not_product_api_key():
    result = readiness.configuration({"ANTHROPIC_BASE_URL": "https://original.invalid", "EEAS_MODEL_ID": "original",
                                      "ANTHROPIC_AUTH_TOKEN": "original-bearer-fixture", "EEAS_LIVE_MAX_COST_USD": "1"})
    assert result["developer_auth_token_present"] is True
    assert result["product_api_key_present"] is False
    assert "PRODUCT_ADAPTER_API_KEY_REQUIRED" in result["blockers"]


def test_selected_wheel_tamper_and_path_escape_are_blocked(tmp_path):
    root, wheels = original_bundle(tmp_path)
    original = wheels / "vendor/windows-py310/original/wheels/original.whl"
    original.write_bytes(b"changed original fixture")
    result = readiness.collect(root, wheels, "windows-py310", {})
    assert result["dependencies"]["status"] == "BLOCKED"
    assert result["sdk"]["status"] == "NOT_PROBED"
    path = root / "profiles/offline-dependencies.json"
    path.write_text(json.dumps({"targets": {"windows-py310": "../outside-private-fixture"}}))
    assert readiness.collect(root, wheels, "windows-py310", {})["dependencies"]["status"] == "BLOCKED"
    path.write_text(json.dumps({"targets": []}))
    assert readiness.collect(root, wheels, "windows-py310", {})["dependencies"]["status"] == "BLOCKED"


def test_installed_manifest_precedes_external_delivery_and_does_not_require_own_zip_sha(tmp_path):
    root, wheels = original_bundle(tmp_path)
    (root / "BUNDLE_MANIFEST.json").write_text(json.dumps({"schema_version": "eeas.release-bundle.v1",
        "source_commit": "c" * 40, "source_snapshot_sha256": "d" * 64, "release_status": "DEVELOPMENT_PREVIEW"}))
    result = readiness.collect(root, wheels, "windows-py310", {})
    assert result["delivery"]["reference"] == "BUNDLE_MANIFEST.json"
    assert result["delivery"]["source_commit"] == "c" * 40
    assert result["delivery"]["source_snapshot_sha256"] == "d" * 64
    assert result["delivery"]["bundle_sha256"] is None
    assert result["delivery"]["status"] == "DECLARATION_ONLY"
    assert result["readiness"] == "BLOCKED"


def test_packaged_source_receipt_without_zip_hash_is_a_valid_dynamic_pointer(tmp_path):
    root, wheels = original_bundle(tmp_path)
    (root / "state/DELIVERY.json").write_text(json.dumps({"source_commit": "e" * 40, "release_status": "DEVELOPMENT_PREVIEW"}))
    result = readiness.collect(root, wheels, "windows-py310", {})
    assert result["delivery"]["source_commit"] == "e" * 40
    assert result["delivery"]["bundle_sha256"] is None
    assert result["delivery"]["reference"] == "state/DELIVERY.json"


def test_changed_gateway_configuration_keeps_real_api_and_original_mock_functional(client, monkeypatch):
    from test_documents import project
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:1/original-fake-company-gateway")
    monkeypatch.setenv("EEAS_MODEL_ID", "original-private-gateway-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "original-test-only-key")
    monkeypatch.setenv("EEAS_LIVE_MAX_COST_USD", "0.05")
    assert client.get("/health").status_code == 200
    pid = project(client, "original gateway-change fixture")
    response = client.post(f"/api/projects/{pid}/documents",
        files={"file": ("original.md", b"REQ: original gateway-independent content\nACCEPT: original content remains\n")})
    assert response.status_code == 201
    extracted = client.post(f"/api/projects/{pid}/planning/extract", json={"document_id": response.json()["id"], "revision": 1})
    assert extracted.status_code == 200
    assert extracted.json()["mode"] == "mock"
    assert extracted.json()["model_verified"] is False
    assert readiness.configuration(os.environ)["status"] == "PRESENT_NOT_QUALIFIED"
    assert client.get("/api/runtime").json()["live_verification"] == "BLOCKED"


def test_actual_sdk_cli_and_selected_public_bundles_observed_without_live_qualification(tmp_path):
    public_wheels = Path(os.environ.get("EEAS_PUBLIC_WHEEL_ROOT", str(ROOT)))
    selected = json.loads((ROOT / "profiles/offline-dependencies.json").read_bytes())["targets"]["windows-py310"]
    if not (public_wheels / selected / "wheels").is_dir():
        pytest.skip("Original qualification test requires the separately prepared public wheel snapshots")
    evidence = Path(os.environ["EEAS_EVIDENCE_DIR"]) / "readiness-original" if os.environ.get("EEAS_EVIDENCE_DIR") else tmp_path / "readiness-original"
    command = [sys.executable, "-B", str(ROOT / "scripts/intranet_readiness.py"), "--root", str(ROOT),
               "--wheel-root", str(public_wheels), "--target", "windows-py310", "--output", str(evidence)]
    env = {**os.environ, "ANTHROPIC_BASE_URL": "", "EEAS_MODEL_ID": "", "ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "", "EEAS_LIVE_MAX_COST_USD": ""}
    result = subprocess.run(command, capture_output=True, env=env, timeout=60, check=False)
    assert result.returncode == 3, result.stderr.decode("utf-8", errors="replace")
    report = json.loads(result.stdout)
    assert report["dependencies"]["status"] == "INTEGRITY_PASS"
    assert report["sdk"]["native_cli_version"]
    assert report["sdk"]["native_cli_exit_code"] == 0
    assert report["sdk"]["model_requests_sent"] == 0
    assert report["configuration"]["status"] == "NOT_CONFIGURED"
    assert report["gates"]["sdk_live"] == "BLOCKED"
    assert report["gates"]["native_windows"] == "BLOCKED"
    assert report["company_model"]["protocol_compatibility"] == "NOT_VALIDATED"
    assert (evidence / "freeze.json").is_file()
    assert json.loads((evidence / "freeze.json").read_bytes())["command"] == command
    assert json.loads((evidence / "result.json").read_bytes()) == report
    before = (evidence / "result.json").read_bytes()
    duplicate = subprocess.run(command, capture_output=True, env=env, timeout=10, check=False)
    assert duplicate.returncode == 2
    assert (evidence / "result.json").read_bytes() == before
