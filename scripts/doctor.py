"""Read-only environment probe. Never opens developer login stores or sends a prompt."""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path


def tool_version(name: str) -> dict:
    root = Path(__file__).resolve().parents[1]
    probe_env = os.environ.copy()
    probe_env["PATH"] = os.pathsep.join([str(root / ".node-tools/node_modules/.bin"),
        str(root / ".tools/node_modules/.bin"), os.environ.get("PATH", "")])
    executable = shutil.which(name, path=probe_env["PATH"])
    if not executable:
        return {"status": "MISSING", "version": None}
    try:
        result = subprocess.run([executable, "--version"], capture_output=True,
                                text=True, timeout=10, check=False, env=probe_env)
        # A version probe must not persist arbitrary error output.
        first = result.stdout.strip().splitlines()
        return {"status": "AVAILABLE" if result.returncode == 0 else "ERROR",
                "version": first[0][:160] if first and result.returncode == 0 else None}
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "ERROR", "version": None}


def diagnose() -> dict:
    sdk = None
    binary = None
    try:
        sdk = metadata.version("claude-agent-sdk")
        dist = metadata.distribution("claude-agent-sdk")
        binary_name = "claude.exe" if os.name == "nt" else "claude"
        candidate = Path(dist.locate_file("claude_agent_sdk/_bundled/" + binary_name))
        if candidate.is_file():
            result = subprocess.run([str(candidate), "--version"], capture_output=True,
                                    text=True, timeout=10, check=False)
            if result.returncode == 0:
                binary = result.stdout.strip()[:160]
    except (metadata.PackageNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    # This is availability, never proof of valid authentication.
    auth = {"api_key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "provider_flags_present": [key for key in (
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_ANTHROPIC_AWS")
                if os.environ.get(key) == "1"],
            "authentication_verified": False, "live_status": "BLOCKED",
            "reason": "No live runtime probe executed; developer login is not runtime evidence"}
    with tempfile.TemporaryDirectory(prefix="eeas-probe-") as directory:
        path = Path(directory) / "probe.sqlite3"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE probe (value TEXT)")
            connection.execute("INSERT INTO probe VALUES (?)", ("original-fixture",))
        with closing(sqlite3.connect(path)) as connection:
            persisted = connection.execute("SELECT value FROM probe").fetchone()[0]
    return {"schema_version": "eeas.environment-probe.v1",
            "development_os": platform.system(), "kernel": platform.release(),
            "wsl": "microsoft" in platform.release().lower(),
            "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
            "tools": {name: tool_version(name) for name in ("node", "npm", "git", "claude")},
            "developer_agent": {"actual": "codex", "target": "claude-code",
                                "login_cache_inspected": False},
            "runtime": {"sdk_version": sdk, "bundled_binary_version": binary, **auth},
            "temporary_persistence_probe": persisted == "original-fixture",
            "platform_verification": {"local": "PASS", "windows_native": "NOT_VALIDATED",
                                      "intranet": "NOT_VALIDATED"}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = diagnose()
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Evidence is immutable: a second execution needs a fresh output path.
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    sys.stdout.write(payload)
    return 0 if result["temporary_persistence_probe"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
