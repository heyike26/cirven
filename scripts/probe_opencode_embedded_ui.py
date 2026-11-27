"""Verify a sealed local UI candidate. Unqualified native UI routes are never requested."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.adapters.opencode_profile import CLI_SHA, VERSION, binary_hash
from packages.adapters.opencode_ui import UIAssetGate


def write(path, value):
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2); out.write("\n")


def probe(args):
    """No subprocess, HTTP client or network fallback exists in the offline validation path."""
    evidence = args.evidence.absolute()
    evidence.mkdir(parents=True, exist_ok=False)
    sources = (Path(__file__).resolve(), ROOT / "packages/adapters/opencode_ui.py", ROOT / "packages/adapters/opencode_profile.py")
    frozen_sources = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    before = after = None
    binary = None
    failure = None
    asset_count = 0
    manifest_hash = None
    status = "BLOCKED"
    try:
        binary = args.binary.absolute()
        if binary.is_symlink() or any(parent.is_symlink() for parent in binary.parents) or not binary.is_file():
            raise ValueError("Original CLI path is not a regular selected file")
        before = binary_hash(binary)
        if before not in CLI_SHA.values(): raise ValueError("Original CLI identity is unknown")
        if args.local_manifest is not None:
            selected = args.local_manifest.absolute()
            if selected.is_symlink() or any(parent.is_symlink() for parent in selected.parents):
                raise ValueError("Local UI manifest path is a symlink")
            if selected.stat().st_size > 1024 * 1024: raise ValueError("Local UI manifest exceeds byte bound")
            raw = selected.read_bytes(); manifest_hash = hashlib.sha256(raw).hexdigest()
            manifest = json.loads(raw)
            if manifest.get("cli_sha256") != before:
                raise ValueError("Local UI manifest belongs to another CLI")
            gate = UIAssetGate(selected.parent, manifest)
            if "/" not in gate.assets: raise ValueError("Local UI entry is missing from the exact manifest")
            entry = gate.read("GET", "/")
            if "text/html" not in entry["content_type"] or b"<html" not in entry["body"].lower():
                raise ValueError("Local UI entry is not HTML")
            for asset in gate.assets.values(): gate.read("GET", asset.path)
            asset_count = len(gate.assets)
            if selected.read_bytes() != raw: raise ValueError("Local UI manifest changed during verification")
            status = "PASS_STATIC_CACHE_ONLY"
        else:
            failure = {"type": "NativeUIBlocked", "detail": "ORIGINAL_CLI_EMBEDDED_UI_EGRESS_NOT_QUALIFIED"}
    except Exception as error:
        status = "FAIL"
        failure = {"type": type(error).__name__, "detail": str(error)}
    if before is not None:
        try:
            if binary.is_symlink() or any(parent.is_symlink() for parent in binary.parents):
                raise ValueError("Original CLI path changed during verification")
            after = binary_hash(binary)
            if before != after: raise ValueError("Original CLI bytes changed during verification")
        except Exception as error:
            status = "FAIL"; failure = {"type": type(error).__name__, "detail": str(error)}
    source_after = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in frozen_sources}
    source_stable = source_after == frozen_sources
    if not source_stable:
        status = "FAIL"; failure = {"type": "SourceChanged", "detail": "Probe sources changed during verification"}
    write(evidence / "freeze.json", {"schema": "eeas.opencode-local-ui-candidate-probe.v2", "version": VERSION,
        "binary": str(args.binary.absolute()), "cli_sha256": before, "source_files": frozen_sources,
        "local_manifest": str(args.local_manifest.absolute()) if args.local_manifest else None,
        "local_manifest_sha256": manifest_hash, "model_calls_permitted": False})
    write(evidence / "source-after.json", {"sha256": source_after, "unchanged": source_stable})
    write(evidence / "requests.json", [])
    result = {"status": status, "failure": failure, "asset_count": asset_count,
        "cli_before": before, "cli_after": after, "unchanged": before is not None and before == after,
        "native_ui_route": "BLOCKED", "native_ui_reason": "ORIGINAL_CLI_EMBEDDED_UI_EGRESS_NOT_QUALIFIED",
        "native_processes_started": 0, "native_http_requests": 0, "all_owned_processes_closed": True,
        "model_calls": 0, "network_connections": 0, "native_windows": "NOT_RUN", "ui_browser_rendering": "NOT_RUN",
        "product_route_integration": "NOT_IMPLEMENTED", "embedded_asset_acquisition": "NOT_QUALIFIED",
        "all_dynamic_capabilities": "NOT_VALIDATED", "finished_at": datetime.now(timezone.utc).isoformat()}
    write(evidence / "result.json", result)
    return 0 if status == "PASS_STATIC_CACHE_ONLY" else 2 if status == "BLOCKED" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--local-manifest", type=Path,
                        help="Explicit sealed local candidate; static bytes only, not native UI qualification")
    return probe(parser.parse_args())


if __name__ == "__main__": raise SystemExit(main())
