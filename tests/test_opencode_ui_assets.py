"""Original local static/guard contracts; these do not qualify native UI rendering."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import socket
import subprocess
import urllib.request

import pytest

from packages.adapters.opencode_profile import CLI_SHA, VERSION
from packages.adapters.opencode_ui import asset_references, local_path, UIAssetGate, NativeUIReadGate
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest


def test_html_css_and_vite_graph_use_exact_local_paths():
    html = b'<html><script src="/assets/main.js"></script><link rel="stylesheet" href="/assets/main.css"><link rel="manifest" href="/site.webmanifest"><img src="/assets/example.png"></html>'
    assert asset_references("/", "text/html", html)["local"] == ["/assets/example.png", "/assets/main.css", "/assets/main.js", "/site.webmanifest"]
    css = b'@font-face{src:url("./local.woff2")} .x{background:url(data:image/svg+xml;base64,ZXhhbXBsZQ==)}'
    assert asset_references("/assets/main.css", "text/css", css)["local"] == ["/assets/local.woff2"]
    js = b'import("./chunk.js");let d=["assets/chunk.css","/assets/example.png"];import(dynamicModule);new URL("./term.wasm",import.meta.url)'
    graph = asset_references("/assets/main.js", "text/javascript", js)
    assert graph["local"] == ["/assets/chunk.css", "/assets/chunk.js", "/assets/example.png", "/assets/term.wasm"]
    assert graph["unresolved_dynamic"] == ["dynamicModule"]


def test_external_font_stylesheet_and_changed_base_are_not_fetched():
    result = asset_references("/", "text/html", b'<link href="https://external.invalid/font.css" rel="stylesheet">')
    assert not result["local"] and result["external_or_rejected"][0]["reference"].startswith("https://")
    with pytest.raises(ValueError): asset_references("/", "text/html", b'<base href="https://external.invalid/">')


@pytest.mark.parametrize("path", ["../api/config", "/assets/%2e%2e/%2e%2e/api/config", "//external.invalid/a.js", "/assets/..\\api", "/assets/%252e%252e/a.js"])
def test_path_guard_rejects_traversal_and_network_origins(path):
    with pytest.raises(ValueError): local_path(path)


def original_manifest(tmp_path):
    body = b'original static fixture'; (tmp_path / "asset.bin").write_bytes(body)
    return {"schema": "eeas.opencode-local-ui-assets.v1", "version": VERSION, "cli_sha256": CLI_SHA["posix"],
            "assets": [{"path": "/assets/original.css", "content_type": "text/css", "sha256": hashlib.sha256(body).hexdigest(),
                        "byte_length": len(body), "stored_path": "asset.bin"}]}


def test_cached_assets_cannot_forward_automatic_permission_or_other_api(tmp_path):
    gate = UIAssetGate(tmp_path, original_manifest(tmp_path))
    assert gate.read("GET", "/assets/original.css")["body"] == b'original static fixture'
    assert gate.read("HEAD", "/assets/original.css")["body"] == b''
    with pytest.raises(Conflict): gate.read("POST", "/permission/fixture/reply")
    with pytest.raises(NotFound): gate.read("GET", "/config")
    (tmp_path / "asset.bin").write_bytes(b'changed fixture')
    with pytest.raises(Conflict): gate.read("GET", "/assets/original.css")


def test_cache_manifest_cannot_select_escaped_or_unknown_binary(tmp_path):
    manifest = original_manifest(tmp_path); manifest["assets"][0]["stored_path"] = "../outside.bin"
    with pytest.raises(ValueError): UIAssetGate(tmp_path, manifest)
    manifest = original_manifest(tmp_path); manifest["cli_sha256"] = "0" * 64
    with pytest.raises(ValueError): UIAssetGate(tmp_path, manifest)


def test_native_read_seam_checks_real_sqlite_project_session_request_and_denies_mutation():
    # An original in-memory guard fixture, not a fabricated native execution/session qualification.
    db = sqlite3.connect(":memory:"); db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE agent_executions(id TEXT,project_id TEXT,runtime TEXT,sdk_version TEXT,session_id TEXT,request TEXT,request_hash TEXT)")
    db.execute("CREATE TABLE agent_scopes(execution_id TEXT,scope TEXT,scope_hash TEXT)")
    request = {"original_guard_fixture": True, "task_binding": {"run_id": "guard-run"}}
    scope = {"runtime": "opencode-http", "cli_sha256": CLI_SHA["posix"], "input_hash": digest(request)}
    db.execute("INSERT INTO agent_executions VALUES (?,?,?,?,?,?,?)", ("guard-execution", "guard-project", "opencode-http", VERSION,
        "guard-not-actual-native-session", json.dumps(request), digest(request)))
    db.execute("INSERT INTO agent_scopes VALUES (?,?,?)", ("guard-execution", json.dumps(scope), digest(scope)))

    class OriginalSQLite:
        @contextmanager
        def connect(self): yield db

    gate = NativeUIReadGate(OriginalSQLite())
    try:
        result = gate.authorize("guard-project", "guard-execution", "GET", "/session/guard-not-actual-native-session/message")
        assert result["native_mutation_allowed"] is False
        with pytest.raises(NotFound): gate.authorize("other-project", "guard-execution", "GET", "/session/guard-not-actual-native-session")
        for method, path in [("POST", "/permission/guard/reply"), ("POST", "/session/guard-not-actual-native-session/prompt_async"),
                             ("GET", "/session/other-session"), ("GET", "/config")]:
            with pytest.raises(Conflict): gate.authorize("guard-project", "guard-execution", method, path)
        db.execute("UPDATE agent_executions SET request='{}'")
        with pytest.raises(Conflict): gate.authorize("guard-project", "guard-execution", "GET", "/session/guard-not-actual-native-session")
    finally: db.close()


@pytest.mark.parametrize("path", ["/assets/%00evil.css", "/assets/%0aevil.js", "/assets/%7fevil.js",
                                  "/assets/%2f%2fevil.js", "%2f%2fexternal.invalid/a.js",
                                  "%68ttps%3a%2f%2fexternal.invalid/a.js"])
def test_encoded_controls_and_origins_are_rejected(path):
    with pytest.raises(ValueError): local_path(path)


def test_literal_external_import_and_inline_dependencies_are_observed():
    js = asset_references("/assets/main.js", "text/javascript", b'import("https://external.invalid/x.js"); import "./side.js"; import("chunk.js")')
    assert js["local"] == ["/assets/chunk.js", "/assets/side.js"]
    assert js["external_or_rejected"][0]["reference"] == "https://external.invalid/x.js"
    html = asset_references("/", "text/html", b'<html><style>.x{background:URL("/assets/x.png")}</style><script>import("https://external.invalid/module")</script></html>')
    assert html["local"] == ["/assets/x.png"]
    assert html["external_or_rejected"][0]["reference"] == "https://external.invalid/module"


def original_graph(tmp_path, files=None):
    files = files or {"/": ("text/html", b'<html><script src="/assets/main.js"></script><link href="/assets/main.css" rel="stylesheet"></html>'),
                      "/assets/main.js": ("text/javascript", b'import("./chunk.js")'),
                      "/assets/chunk.js": ("text/javascript", b'console.log("original local fixture")'),
                      "/assets/main.css": ("text/css", b'@font-face{src:url("./local.woff2")}'),
                      "/assets/local.woff2": ("font/woff2", b'original fixture bytes, not a qualified font')}
    assets = []
    for index, (path, (media, body)) in enumerate(files.items()):
        stored = "original-%d.bin" % index; (tmp_path / stored).write_bytes(body)
        assets.append({"path": path, "content_type": media, "sha256": hashlib.sha256(body).hexdigest(),
                       "byte_length": len(body), "stored_path": stored})
    return {"schema": "eeas.opencode-local-ui-assets.v1", "version": VERSION, "cli_sha256": CLI_SHA["posix"],
            "assets": assets, "external_or_rejected": [], "unresolved_dynamic": []}


def test_sealed_original_static_graph_verifies_real_bytes_before_entry(tmp_path):
    gate = UIAssetGate(tmp_path, original_graph(tmp_path))
    assert len(gate.assets) == 5
    assert b"<html>" in gate.read("GET", "/")["body"]
    assert gate.read("GET", "/assets/local.woff2")["native_api_forwarded"] is False


@pytest.mark.parametrize("field", ["external_or_rejected", "unresolved_dynamic"])
def test_manifest_with_declared_incomplete_graph_is_not_servable(tmp_path, field):
    manifest = original_graph(tmp_path); manifest[field] = ["original rejected reference"]
    with pytest.raises(ValueError, match="incomplete"): UIAssetGate(tmp_path, manifest)


@pytest.mark.parametrize("body", [b'import("https://external.invalid/module.js")', b'import(dynamicModule)', b'import (dynamicModule)'])
def test_manifest_cannot_hide_external_or_dynamic_refs_in_actual_bytes(tmp_path, body):
    manifest = original_graph(tmp_path, {"/assets/main.js": ("text/javascript", body)})
    with pytest.raises(Conflict, match="external or unresolved"): UIAssetGate(tmp_path, manifest)


def test_missing_dependency_blocks_the_entry_before_any_read(tmp_path):
    manifest = original_graph(tmp_path); manifest["assets"] = manifest["assets"][:-1]
    with pytest.raises(Conflict, match="dependency is absent"): UIAssetGate(tmp_path, manifest)


def test_spa_html_fallback_is_never_accepted_as_javascript(tmp_path):
    manifest = original_graph(tmp_path, {"/assets/main.js": ("text/html", b'<html>original fallback fixture</html>')})
    with pytest.raises(ValueError, match="HTML fallback"): UIAssetGate(tmp_path, manifest)


def test_manifest_symlink_root_or_member_are_denied(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir(); manifest = original_graph(cache)
    alias = tmp_path / "alias"; alias.symlink_to(cache, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"): UIAssetGate(alias, manifest)
    member = cache / manifest["assets"][0]["stored_path"]; member.unlink()
    external = tmp_path / "external.bin"; external.write_bytes(b'original fixture')
    member.symlink_to(external)
    with pytest.raises(Conflict, match="path changed"): UIAssetGate(cache, manifest)


def no_native_or_network(monkeypatch):
    calls = []
    def rejected(*args, **kwargs):
        calls.append(True); raise AssertionError("Offline UI validation attempted native execution or network")
    monkeypatch.setattr(subprocess, "Popen", rejected)
    monkeypatch.setattr(socket, "socket", rejected)
    monkeypatch.setattr(urllib.request, "urlopen", rejected)
    return calls


def original_probe_args(monkeypatch, tmp_path, manifest=None):
    from scripts import probe_opencode_embedded_ui as script
    binary = tmp_path / "original-no-execute-fixture"; binary.write_bytes(b'original test fixture, not native CLI')
    # Unit-only identity seam. Actual acquired CLI is tested separately by the recorded command.
    monkeypatch.setattr(script, "binary_hash", lambda _path: CLI_SHA["posix"])
    selected = None
    if manifest is not None:
        selected = tmp_path / "local-manifest.json"; selected.write_text(json.dumps(manifest))
    return script, SimpleNamespace(binary=binary, evidence=tmp_path / "evidence", local_manifest=selected)


def test_missing_local_candidate_blocks_before_native_spawn_or_network(tmp_path, monkeypatch):
    script, args = original_probe_args(monkeypatch, tmp_path)
    calls = no_native_or_network(monkeypatch)
    assert script.probe(args) == 2
    result = json.loads((args.evidence / "result.json").read_text())
    assert result["status"] == "BLOCKED" and result["native_ui_route"] == "BLOCKED"
    assert result["native_processes_started"] == result["native_http_requests"] == result["network_connections"] == 0
    assert not calls


def test_explicit_local_cache_pass_is_limited_and_never_calls_native(tmp_path, monkeypatch):
    script, args = original_probe_args(monkeypatch, tmp_path, original_graph(tmp_path))
    calls = no_native_or_network(monkeypatch)
    assert script.probe(args) == 0
    result = json.loads((args.evidence / "result.json").read_text())
    assert result["status"] == "PASS_STATIC_CACHE_ONLY" and result["asset_count"] == 5
    assert result["native_ui_route"] == "BLOCKED" and result["embedded_asset_acquisition"] == "NOT_QUALIFIED"
    assert result["product_route_integration"] == "NOT_IMPLEMENTED" and not calls


def test_incomplete_candidate_fails_without_native_or_network(tmp_path, monkeypatch):
    manifest = original_graph(tmp_path); manifest["assets"] = manifest["assets"][:-1]
    script, args = original_probe_args(monkeypatch, tmp_path, manifest)
    calls = no_native_or_network(monkeypatch)
    assert script.probe(args) == 1
    result = json.loads((args.evidence / "result.json").read_text())
    assert result["status"] == "FAIL" and result["unchanged"] is True and not calls


def test_unknown_binary_cannot_be_used_for_candidate_validation(tmp_path, monkeypatch):
    from scripts import probe_opencode_embedded_ui as script
    binary = tmp_path / "unknown-original-fixture"; binary.write_bytes(b'unknown original binary fixture')
    args = SimpleNamespace(binary=binary, evidence=tmp_path / "evidence", local_manifest=None)
    calls = no_native_or_network(monkeypatch)
    assert script.probe(args) == 1
    assert json.loads((args.evidence / "result.json").read_text())["failure"]["detail"] == "Original CLI identity is unknown"
    assert not calls
