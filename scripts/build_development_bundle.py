"""Package current development code with the exact Windows dependency wheels."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
selection = json.loads((ROOT / "profiles/offline-dependencies.json").read_text())
directory = ROOT / selection["targets"]["windows-py310"]
from install_offline import verify
verify(directory, "windows-py310")
files = []
for folder in ("apps", "packages", "scripts", "tests", ".claude", "contracts", "milestones", "state", "docs"):
    files.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
files.extend(ROOT / name for name in ("AGENTS.md", "CLAUDE.md", "ROUTER.md", "pyproject.toml",
    "requirements-core.lock", "requirements-runtime.lock", "install-windows.cmd", "start-windows.cmd", "stop-windows.cmd", "verify-windows.cmd"))
files.extend(p for p in directory.rglob("*") if p.is_file())
files.extend(ROOT / name for name in ("profiles/offline-dependencies.json", "profiles/environment.json",
                                     "profiles/stack.lock.json", "profiles/dependencies.json",
                                     "profiles/windows-dependencies.json"))
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
target = ROOT / "artifacts" / f"eeas-development-windows-py310-{stamp}.zip"
target.parent.mkdir(exist_ok=True)
manifest = {"schema_version": "eeas.development-bundle.v1", "release_status": "IN_DEVELOPMENT",
            "target": "Windows x86_64 / Python3.10", "native_validation": "NOT_VALIDATED",
            "live_model": "NOT_CONNECTED", "files_sha256": {}}
with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(set(files)):
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
        manifest["files_sha256"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        archive.write(path, relative)
    archive.writestr("BUNDLE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
with zipfile.ZipFile(target) as archive:
    corrupt = archive.testzip()
    if corrupt:
        raise RuntimeError("Corrupt bundle member: " + corrupt)
digest = hashlib.sha256(target.read_bytes()).hexdigest()
target.with_suffix(".zip.sha256").write_text(digest + "  " + target.name + "\n")
print(json.dumps({"path": str(target.relative_to(ROOT)), "sha256": digest, "bytes": target.stat().st_size,
                  "status": "DEVELOPMENT_ONLY", "windows_native": "NOT_VALIDATED"}))
