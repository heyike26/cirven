"""Copy the real archive away from the checkout, safely unpack, then run offline acceptance."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
from bundle_support import assert_no_links, canonical_json, digest_file, extract_bundle, validate_bundle

ROOT = Path(__file__).resolve().parents[1]


def probe(archive: Path, expected_sha256: str, destination: Path, evidence: Path):
    manifest = validate_bundle(archive, expected_sha256)
    assert_no_links(destination)
    if os.path.lexists(destination):
        raise ValueError("Copy destination exists; preserve old probe environments")
    destination.mkdir(parents=True, exist_ok=False)
    copied = destination / archive.name
    shutil.copyfile(archive, copied)
    if digest_file(copied) != expected_sha256:
        raise ValueError("Copied archive digest changed")
    application = destination / "中文 程序 copy"
    extract_bundle(copied, application, expected_sha256)
    record = {"archive": str(archive), "copied_archive": str(copied), "sha256": expected_sha256,
              "source_commit": manifest["source_commit"], "source_snapshot_sha256": manifest["source_snapshot_sha256"],
              "application": str(application), "target": manifest["target"],
              "input_kind": "Actual built bundle, not the live checkout"}
    with (evidence / "bundle-copy-inputs.json").open("xb") as stream:
        stream.write(canonical_json(record))
    result = subprocess.run([sys.executable, "-B", str(application / "scripts/portable_acceptance.py"),
                             "--output", str(evidence / "portable")], cwd=application, check=False)
    print(json.dumps({**record, "exit_code": result.returncode}, ensure_ascii=False))
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--destination", type=Path, default=ROOT / ".offline-probe" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    evidence = args.evidence or Path(os.environ.get("EEAS_EVIDENCE_DIR", ""))
    if not evidence.is_dir():
        parser.error("Use record_slice.py or provide an existing --evidence directory")
    try:
        return probe(args.archive, args.sha256, args.destination.absolute(), evidence.absolute())
    except (ValueError, KeyError, OSError) as exc:
        print(f"Bundle-copy probe failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
