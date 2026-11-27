"""Observe intranet prerequisites without sending prompts or exposing private configuration."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import zipfile

sys.dont_write_bytecode = True
try:
    from install_offline import verify
except ModuleNotFoundError:
    from scripts.install_offline import verify

ROOT = Path(__file__).resolve().parents[1]


def file_hash(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(data)
    return value.hexdigest()


def no_links(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if os.path.lexists(item):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("LINKED_PATH")


def public_json(path, limit=1024 * 1024):
    no_links(path)
    if path.stat().st_size > limit:
        raise ValueError("OVERSIZED_METADATA")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("INVALID_METADATA")
    return value


def configuration(environment):
    """Only booleans and fixed reason codes escape this function."""
    endpoint = environment.get("ANTHROPIC_BASE_URL", "")
    model = environment.get("EEAS_MODEL_ID", "")
    key = environment.get("ANTHROPIC_API_KEY", "")
    token = environment.get("ANTHROPIC_AUTH_TOKEN", "")
    budget = environment.get("EEAS_LIVE_MAX_COST_USD", "")
    valid_endpoint = False
    try:
        address = urlsplit(endpoint)
        valid_endpoint = bool(address.scheme in {"http", "https"} and address.hostname
            and not address.username and not address.password and not address.query and not address.fragment
            and (address.port is None or 1 <= address.port <= 65535)
            and not any(ord(c) < 32 for c in endpoint))
    except ValueError:
        pass
    valid_model = bool(model.strip() and len(model) <= 256 and not any(ord(c) < 32 for c in model))
    valid_budget = False
    try:
        amount = Decimal(budget)
        valid_budget = bool(amount.is_finite() and 0 < amount <= 100)
    except (InvalidOperation, ValueError):
        pass
    key_present, token_present = bool(key.strip()), bool(token.strip())
    present = bool(endpoint.strip() or model.strip() or key_present or token_present or budget.strip())
    valid = valid_endpoint and valid_model and key_present and valid_budget
    reasons = []
    for accepted, reason in ((valid_endpoint, "EXPLICIT_COMPANY_ENDPOINT_REQUIRED"),
                             (valid_model, "MODEL_ID_REQUIRED"), (key_present, "PRODUCT_ADAPTER_API_KEY_REQUIRED"),
                             (valid_budget, "EXPLICIT_FINITE_BUDGET_REQUIRED")):
        if not accepted:
            reasons.append(reason)
    return {"status": "PRESENT_NOT_QUALIFIED" if valid else "PARTIAL_CONFIGURATION" if present else "NOT_CONFIGURED",
            "endpoint_present": bool(endpoint.strip()), "endpoint_syntax_valid": valid_endpoint,
            "model_id_present": bool(model.strip()), "model_id_syntax_valid": valid_model,
            "product_api_key_present": key_present, "developer_auth_token_present": token_present,
            "budget_present": bool(budget.strip()), "budget_syntax_valid": valid_budget,
            "budget_approval": "NOT_VALIDATED", "authentication_verified": False,
            "configuration_values_exported": False, "blockers": reasons,
            "note": "Developer bearer-token configuration is separate from this preview product adapter's API-key input"}


def host_identity():
    system, release = platform.system(), platform.release()
    native_windows = os.name == "nt" and system == "Windows"
    return {"system": system, "os_name": os.name, "architecture": platform.machine(),
            "python": platform.python_version(), "python310": sys.version_info[:2] == (3, 10),
            "x86_64": platform.machine().lower() in {"amd64", "x86_64"},
            "wsl": system == "Linux" and "microsoft" in release.lower(),
            "native_windows_identity": native_windows,
            "identity_status": "OBSERVED_NATIVE_WINDOWS" if native_windows else "NOT_NATIVE_WINDOWS",
            "runner_qualification": "BLOCKED"}


def wheelhouse(root, wheel_root, target):
    selection = public_json(root / "profiles/offline-dependencies.json")
    targets = selection.get("targets", {})
    if not isinstance(targets, dict):
        raise ValueError("INVALID_WHEEL_SELECTION")
    name = targets.get(target)
    if not isinstance(name, str) or not re.fullmatch(r"vendor/(?:linux|windows)-py310/[A-Za-z0-9_-]+", name):
        raise ValueError("INVALID_WHEEL_SELECTION")
    directory = wheel_root / name
    no_links(directory)
    no_links(directory / "manifest.json")
    manifest = verify(directory, target)
    if manifest.get("architecture", "x86_64") != "x86_64":
        raise ValueError("WRONG_WHEEL_ARCHITECTURE")
    return directory, manifest


def native_hash_from_wheel(directory, filename):
    wheels = list((directory / "wheels").glob("claude_agent_sdk-*.whl"))
    if len(wheels) != 1:
        raise ValueError("AMBIGUOUS_SDK_WHEEL")
    with zipfile.ZipFile(wheels[0]) as archive:
        member = archive.getinfo("claude_agent_sdk/_bundled/" + filename)
        if member.file_size > 512 * 1024 * 1024 or stat.S_ISLNK(member.external_attr >> 16):
            raise ValueError("INVALID_NATIVE_BINARY")
        value = hashlib.sha256()
        with archive.open(member) as stream:
            for data in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(data)
    return value.hexdigest()


def sdk_observation(root, wheel_root, target, target_manifest):
    expected = target_manifest.get("runtime", {}).get("claude-agent-sdk")
    result = {"expected_version": expected, "installed_version": None, "native_cli_version": None,
              "native_cli_exit_code": None, "native_cli_sha256": None, "target_host_matches": False,
              "status": "MISSING", "live_qualification": "BLOCKED", "model_requests_sent": 0}
    try:
        distribution = importlib.metadata.distribution("claude-agent-sdk")
    except importlib.metadata.PackageNotFoundError:
        return result
    result["installed_version"] = distribution.version
    host = "windows-py310" if os.name == "nt" else "linux-py310" if platform.system() == "Linux" else None
    result["target_host_matches"] = host == target
    if not expected or distribution.version != expected or host is None:
        result["status"] = "VERSION_OR_HOST_MISMATCH"
        return result
    try:
        directory, manifest = wheelhouse(root, wheel_root, host)
        if manifest.get("runtime", {}).get("claude-agent-sdk") != distribution.version:
            raise ValueError("HOST_SDK_VERSION_MISMATCH")
        filename = "claude.exe" if os.name == "nt" else "claude"
        binary = Path(distribution.locate_file("claude_agent_sdk/_bundled/" + filename))
        no_links(binary)
        actual = file_hash(binary)
        if actual != native_hash_from_wheel(directory, filename):
            result["status"] = "NATIVE_BINARY_INTEGRITY_ERROR"
            return result
        result["native_cli_sha256"] = actual
        with tempfile.TemporaryDirectory(prefix="eeas-readiness-empty-config-") as temporary:
            env = {name: os.environ[name] for name in ("PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT") if name in os.environ}
            env.update({"CLAUDE_CONFIG_DIR": temporary, "USERPROFILE": temporary,
                        "TEMP": temporary, "TMP": temporary, "TMPDIR": temporary,
                        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1"})
            # Version observation only. No CLI prompt, SDK query, HTTP or developer login store.
            command = subprocess.run([str(binary), "--version"], cwd=temporary, env=env,
                                     capture_output=True, timeout=15, check=False)
        result["native_cli_exit_code"] = command.returncode
        result["version_stdout_sha256"] = hashlib.sha256(command.stdout).hexdigest()
        result["version_stderr_sha256"] = hashlib.sha256(command.stderr).hexdigest()
        text = command.stdout.decode("utf-8", errors="replace").strip()
        match = re.fullmatch(r"([0-9]+\.[0-9]+\.[0-9]+)(?: \(Claude Code\))?", text)
        if command.returncode == 0 and match:
            result["native_cli_version"] = match[1]
            result["status"] = "TARGET_BINARY_OBSERVED" if host == target else "HOST_BINARY_ONLY_OBSERVED"
        else:
            result["status"] = "VERSION_PROBE_FAILED"
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, subprocess.TimeoutExpired):
        result["status"] = "NATIVE_VERSION_NOT_VALIDATED"
    return result


def collect(root=ROOT, wheel_root=None, target="windows-py310", environment=None):
    root, wheel_root = Path(root), Path(wheel_root or root)
    identity = host_identity()
    config = configuration(os.environ if environment is None else environment)
    dependencies = {"status": "BLOCKED", "target": target, "manifest_sha256": None, "files_checked": 0}
    sdk = {"status": "NOT_PROBED", "live_qualification": "BLOCKED", "model_requests_sent": 0}
    try:
        directory, manifest = wheelhouse(root, wheel_root, target)
        dependencies.update({"status": "INTEGRITY_PASS", "manifest_sha256": file_hash(directory / "manifest.json"),
                             "files_checked": len(manifest["files_sha256"]), "sdk_version": manifest.get("runtime", {}).get("claude-agent-sdk")})
        sdk = sdk_observation(root, wheel_root, target, manifest)
    except (ValueError, KeyError, OSError, TypeError, AttributeError):
        dependencies["reason"] = "SELECTED_WHEELS_MISSING_OR_INTEGRITY_ERROR"
    delivery = {"reference": "state/DELIVERY.json", "status": "METADATA_UNAVAILABLE"}
    for name in ("BUNDLE_MANIFEST.json", "state/DELIVERY.json"):
        try:
            declared = public_json(root / name, limit=8 * 1024 * 1024)
            def public_digest(key, length):
                value = declared.get(key)
                return value if isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{length}}}", value) else None
            release = declared.get("release_status")
            delivery.update({"reference": name, "status": "DECLARATION_ONLY", "record_sha256": file_hash(root / name),
                             "source_commit": public_digest("source_commit", 40),
                             "source_snapshot_sha256": public_digest("source_snapshot_sha256", 64),
                             "bundle_sha256": public_digest("sha256", 64) if name == "state/DELIVERY.json" else None,
                             "release_status": release if release in {"IN_DEVELOPMENT", "DEVELOPMENT_PREVIEW", "EXTERNAL_FRAMEWORK_READY"} else None,
                             "note": "Installed manifest/source receipt need not contain their ZIP's own hash; use the separately trusted external delivery digest"})
            break
        except (ValueError, KeyError, OSError, TypeError):
            continue
    return {"schema_version": "eeas.intranet-readiness.v1", "release_status": "DEVELOPMENT_PREVIEW",
            "readiness": "BLOCKED", "observed_at": datetime.now(timezone.utc).isoformat(),
            "host": identity, "dependencies": dependencies, "sdk": sdk, "configuration": config, "delivery": delivery,
            "company_model": {"label": "deepseek 4.1flash", "label_source": "User requirement",
                              "protocol_compatibility": "NOT_VALIDATED", "authentication": "NOT_VALIDATED"},
            "gates": {"python310_x86_64": "OBSERVED" if identity["python310"] and identity["x86_64"] else "BLOCKED",
                      "native_windows": "BLOCKED", "os_isolation": "BLOCKED", "sdk_live": "BLOCKED",
                      "company_protocol": "BLOCKED", "company_device_or_business": "NOT_RUN"},
            "limits": ["Presence/CLI version is not real gateway or tool qualification", "No model requests are sent",
                       "No developer login/configuration is copied", "Readiness does not certify OS isolation or native networking"],
            "next_steps": ["Run verified Windows bundle portable acceptance and verify-windows.cmd on native Windows",
                           "Obtain gateway protocol, model ID, credential header and explicit approved test budget",
                           "Qualify SDK gateway streaming/tools/interrupt and OS isolation with immutable original evidence",
                           "Continue handoff/WINDOWS_CONTINUE.md; keep private material out of the public core"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--wheel-root", type=Path)
    parser.add_argument("--target", choices=["windows-py310", "linux-py310"], default="windows-py310")
    parser.add_argument("--output", type=Path, help="Fresh local evidence directory; never overwrite old results")
    args = parser.parse_args()
    if args.output:
        no_links(args.output)
        if os.path.lexists(args.output):
            parser.error("Output exists; use a fresh evidence directory")
        args.output.mkdir(parents=True, exist_ok=False)
        freeze = {"schema_version": "eeas.readiness-input.v1", "acceptance_before_run": "Observe actual prerequisites, preserve unresolved gates, send zero model requests, export zero credential/config values",
                  "target": args.target, "script_sha256": file_hash(Path(__file__)),
                  "command": getattr(sys, "orig_argv", [sys.executable, *sys.argv]),
                  "selection_sha256": file_hash(args.root / "profiles/offline-dependencies.json"),
                  "configuration_recording": "Presence only; no environment dump"}
        (args.output / "freeze.json").write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = collect(args.root, args.wheel_root, args.target)
    value = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with (args.output / "result.json").open("x", encoding="utf-8") as stream:
            stream.write(value)
    print(value, end="")
    # 3 means the diagnostic completed with unresolved qualifications, not a crashed probe.
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
