"""Validate a release with a separately supplied SHA256, optionally safely unpack it."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

from bundle_support import extract_bundle, validate_bundle, validate_extracted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?")
    parser.add_argument("--sha256", help="Digest from the trusted delivery record")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--directory", type=Path, help="Check every file of a freshly unpacked bundle")
    args = parser.parse_args()
    if args.directory:
        if args.archive or args.destination:
            parser.error("--directory cannot be combined with archive/destination")
        action = lambda: validate_extracted(args.directory)
    else:
        if not args.archive or not args.sha256:
            parser.error("archive and --sha256 are required")
        action = lambda: (extract_bundle(args.archive, args.destination, args.sha256) if args.destination
                          else validate_bundle(args.archive, args.sha256))
    try:
        result = action()
        print(json.dumps({"status": "PASS", "target": result["target"],
                          "source_commit": result["source_commit"], "release_status": result["release_status"]}))
        return 0
    except (ValueError, KeyError, OSError) as exc:
        print(f"Bundle validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
