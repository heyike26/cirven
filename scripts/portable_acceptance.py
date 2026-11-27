"""Fresh offline install and API/UI/SQLite/snapshot smoke checks on the actual host."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

sys.dont_write_bytecode = True  # Keep a fresh bundle unchanged before its first integrity check.

from bundle_support import assert_no_links, canonical_json, digest_file, validate_extracted

ROOT = Path(__file__).resolve().parents[1]


def probe(root: Path, output: Path):
    assert_no_links(root)
    assert_no_links(output)
    manifest = validate_extracted(root)
    target = "windows-py310" if os.name == "nt" else "linux-py310"
    if sys.version_info[:2] != (3, 10) or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("This bundle requires Python3.10 x86_64")
    if manifest["target"] != target:
        raise ValueError("A mismatched host cannot validate target-native dependencies")
    if os.path.lexists(output) or os.path.lexists(root / ".venv"):
        raise ValueError("Use a fresh output directory and an unpacked bundle without .venv")
    output.mkdir(parents=True, exist_ok=False)
    selection = json.loads((root / "profiles/offline-dependencies.json").read_text(encoding="utf-8"))
    # No inherited credentials, proxy, SDK config, developer PYTHONPATH or package cache.
    env = {key: value for key, value in os.environ.items() if key in {"SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "PATH", "COMSPEC"}}
    env.update({"PYTHONPATH": os.pathsep.join([str(root / "tests/fixtures/offline_guard"), str(root)]),
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "PIP_NO_INDEX": "1",
                "PIP_INDEX_URL": "http://127.0.0.1:1/unavailable", "PIP_EXTRA_INDEX_URL": "",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1", "ANTHROPIC_API_KEY": "", "EEAS_MODEL_ID": "",
                "HOME": str(output / "empty-user-home"), "USERPROFILE": str(output / "empty-user-home"), "XDG_CACHE_HOME": str(output / "empty-user-home"),
                "XDG_CONFIG_HOME": str(output / "empty-user-home"), "CLAUDE_CONFIG_DIR": str(output / "empty-claude-config")})
    (output / "empty-user-home").mkdir()
    (output / "empty-claude-config").mkdir()
    executable = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    commands = [[sys.executable, str(root / "scripts/install_offline.py"), "--destination", str(root / ".venv"),
                 "--wheelhouse", str(root / selection["targets"][target])],
                [str(executable), str(root / "tests/fixtures/portable_smoke.py"), "--output", str(output / "platform")],
                [str(executable), str(root / "scripts/probe_native_runner.py"), "--output", str(output / "native-runner")]]
    if os.name != "nt":
        commands[2].append("--allow-non-windows")
    freeze = {"schema_version": "eeas.portable-acceptance.v1", "source_commit": manifest["source_commit"],
              "source_snapshot_sha256": manifest["source_snapshot_sha256"], "target": target, "commands": commands,
              "host": platform.system(), "python": sys.version, "wsl": "microsoft" in platform.release().lower(),
              "environment_variables": {key: env[key] for key in ("HOME", "USERPROFILE", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "CLAUDE_CONFIG_DIR", "PIP_NO_INDEX", "PYTHONDONTWRITEBYTECODE")},
              "guard": "Original Python loopback guard plus pip --no-index; not an OS sandbox",
              "acceptance_before_run": "Fresh bundled installation, pip check, actual HTTP API/local assets/persistence, no configured model, quiescent snapshot and restore; original native runner process fixtures"}
    with (output / "freeze.json").open("xb") as stream:
        stream.write(canonical_json(freeze))
    receipts = []
    failed = None
    for index, command in enumerate(commands, 1):
        stdout, stderr = output / f"{index}-stdout.txt", output / f"{index}-stderr.txt"
        with stdout.open("xb") as out, stderr.open("xb") as err:
            result = subprocess.run(command, cwd=root, env=env, stdout=out, stderr=err, check=False)
        receipts.append({"argv": command, "exit_code": result.returncode, "stdout": stdout.name,
                         "stdout_sha256": digest_file(stdout), "stderr": stderr.name, "stderr_sha256": digest_file(stderr)})
        if result.returncode:
            failed = index
            break
    result = {"schema_version": "eeas.portable-result.v1", "status": "FAIL" if failed else "PASS", "receipts": receipts,
              "target": target, "environment": platform.system(), "python": platform.python_version(),
              "windows_native": "PASS_ORIGINAL_FIXTURES" if os.name == "nt" and not failed else "BLOCKED",
              "model": "NOT_CONFIGURED", "live_model_verification": "NOT_RUN", "isolation": "NOT_VALIDATED",
              "failed_step": failed, "release_status": "DEVELOPMENT_PREVIEW"}
    with (output / "result.json").open("xb") as stream:
        stream.write(canonical_json(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/portable" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    args = parser.parse_args()
    try:
        result = probe(ROOT, args.output.absolute())
        print(json.dumps({**result, "evidence": str(args.output)}, ensure_ascii=False))
        return 0 if result["status"] == "PASS" else 1
    except (ValueError, KeyError, OSError) as exc:
        print(f"Portable acceptance blocked: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
