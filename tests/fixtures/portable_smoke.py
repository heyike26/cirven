"""Original portable API startup/persistence/snapshot fixtures; no model query."""
from __future__ import annotations
import argparse
import json
import importlib.metadata
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from claude_agent_sdk import ClaudeAgentOptions

ROOT = Path(__file__).resolve().parents[2]
# Observe the database's existing 10s busy wait plus durable commit/close.
# Only these writes get extra read time; connection/GET/start/stop stay at 2s,
# and a timed-out mutation is never retried.
DURABLE_WRITE_TIMEOUT = httpx.Timeout(2, read=15)
sys.path.insert(0, str(ROOT / "scripts"))
from bundle_support import canonical_json, digest_file
from snapshot_data import backup, restore


def start(data, output, label):
    with socket.socket() as allocated:
        allocated.bind(("127.0.0.1", 0))
        port = allocated.getsockname()[1]
    env = {**os.environ, "EEAS_DATA_DIR": str(data), "EEAS_PORT": str(port), "EEAS_HOST": "127.0.0.1", "EEAS_WORKER": "0"}
    stream = (output / (label + "-server.txt")).open("xb")
    process = subprocess.Popen([sys.executable, "-m", "apps.api", "serve"], cwd=ROOT, env=env, stdout=stream, stderr=stream)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2)
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            try:
                client.get("/health").raise_for_status()
                break
            except httpx.HTTPError:
                if process.poll() is not None:
                    raise RuntimeError("Actual API process exited")
                time.sleep(0.05)
        else:
            raise RuntimeError("Actual API did not become healthy")
        response = client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
        response.raise_for_status()
        client.headers.update({"Origin": client.base_url.__str__().rstrip("/"), "X-EEAS-CSRF": response.json()["csrf_token"]})
        return process, client, stream
    except BaseException:
        process.terminate()
        process.wait(timeout=5)
        client.close()
        stream.close()
        raise


def stop(process, client, stream):
    try:
        client.post("/api/shutdown").raise_for_status()
        process.wait(timeout=8)
        if process.returncode != 0:
            raise RuntimeError("API did not exit cleanly")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        client.close()
        stream.close()


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    try:
        socket.getaddrinfo("pypi.org", 443)
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Public network guard is missing")
    runtime_data = ROOT / ".offline-probe" / "portable-runtime-data"
    runtime_data.mkdir(parents=True, exist_ok=False)
    data = runtime_data / "original-data"
    process, client, stream = start(data, output, "install")
    try:
        runtime = client.get("/api/runtime").json()
        if runtime["credential_present"] or "NO_CREDENTIALS" not in runtime["planning_blockers"]:
            raise RuntimeError("Model should be NOT_CONFIGURED")
        index = client.get("/")
        index.raise_for_status()
        script = client.get("/assets/app.js")
        script.raise_for_status()
        if "script-src 'self'" not in index.headers.get("content-security-policy", ""):
            raise RuntimeError("Local UI content policy changed")
        project = client.post("/api/projects", json={"name": "原创 移机项目", "description": "offline-portable-original"}, timeout=DURABLE_WRITE_TIMEOUT)
        project.raise_for_status()
        project = project.json()
        doc = client.post(f"/api/projects/{project['id']}/documents", files={"file": ("原创.md", "REQ: 原创内容\nACCEPT: 内容保留\n".encode())}, timeout=DURABLE_WRITE_TIMEOUT)
        doc.raise_for_status()
        doc = doc.json()
        proposal = client.post(f"/api/projects/{project['id']}/planning/extract", json={"document_id": doc["id"], "revision": 1})
        proposal.raise_for_status()
        proposal = proposal.json()
        if proposal["mode"] != "mock" or proposal["model_verified"]:
            raise RuntimeError("Fixture mode incorrectly reported live model verification")
        requirement = proposal["proposal"]
        requirement["confirmed"] = True
        saved = client.post(f"/api/projects/{project['id']}/requirements", json=requirement, timeout=DURABLE_WRITE_TIMEOUT)
        saved.raise_for_status()
    finally:
        stop(process, client, stream)
    receipt = backup(data, runtime_data / "original-backup.zip")
    restored = runtime_data / "restored-data"
    restore(runtime_data / "original-backup.zip", restored, receipt["sha256"])
    if (restored / "local-access.token").exists():
        raise RuntimeError("Old local access token was copied")
    process, client, stream = start(restored, output, "restore")
    try:
        loaded = client.get("/api/projects").json()["projects"]
        if loaded != [project]:
            raise RuntimeError("Restored project differs from original SQLite row")
        sources = client.get(f"/api/projects/{project['id']}/documents").json()["documents"]
        if sources[0]["id"] != doc["id"]:
            raise RuntimeError("Restored source document disappeared")
        runtime = client.get("/api/runtime").json()
        if runtime["credential_present"] or "MODEL_ID_NOT_CONFIGURED" not in runtime["planning_blockers"]:
            raise RuntimeError("Restored process unexpectedly configured a model")
    finally:
        stop(process, client, stream)
    if ClaudeAgentOptions(allowed_tools=["Read"]).allowed_tools != ["Read"]:
        raise RuntimeError("Bundled SDK cannot import")
    distribution = importlib.metadata.distribution("claude-agent-sdk")
    binary = Path(distribution.locate_file("claude_agent_sdk/_bundled/" + ("claude.exe" if os.name == "nt" else "claude")))
    if not binary.is_file():
        raise RuntimeError("SDK bundled native CLI is missing")
    with (output / "sdk-cli-stdout.txt").open("xb") as stdout, (output / "sdk-cli-stderr.txt").open("xb") as stderr:
        cli = subprocess.run([str(binary), "--version"], cwd=ROOT, timeout=15, check=False,
            stdout=stdout, stderr=stderr, env={**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1"})
    if cli.returncode != 0:
        raise RuntimeError("Fresh offline SDK native CLI version probe failed")
    result = {"status": "PASS", "source": "synthetic", "runtime": "mock", "model": "NOT_CONFIGURED",
              "api": "ACTUAL_PROCESS_PASS", "local_ui_assets": "PASS", "sqlite_restore": "PASS",
              "snapshot": receipt, "sdk": "IMPORT_OPTIONS_PASS", "live_model": "NOT_RUN",
              "bundled_cli": {"argv": [str(binary), "--version"], "exit_code": cli.returncode,
                              "binary_sha256": digest_file(binary),
                              "version": (output / "sdk-cli-stdout.txt").read_text(encoding="utf-8").strip()[:160],
                              "network_qualification": "Python guard does not enforce OS isolation on native executables"},
              "sandbox": "NOT_VALIDATED"}
    with (output / "result.json").open("xb") as stream:
        stream.write(canonical_json(result))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
