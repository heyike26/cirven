"""Build one original test plugin locally; no registry, Git checkout or install script."""
import argparse
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.extensions.schema import fixture_manifest


def build(version, output):
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(fixture_manifest(version), ensure_ascii=False))
        archive.write(ROOT / "packages/extensions/fixtures" / ("v" + str(version)) / "plugin.py", "plugin.py")
        archive.writestr("SKILL.md", "# Original formatter fixture\nPure formatting only; no authority to grant permissions.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", choices=["1", "2"])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.version, args.output)
