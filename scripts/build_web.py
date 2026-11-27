"""Build the dependency-free local Web artifact, including a content manifest."""
import hashlib
import json
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
source = root / "apps/web"
target = root / "dist/web"
target.mkdir(parents=True, exist_ok=True)
manifest = {}
for path in source.rglob("*"):
    if path.is_file():
        output = target / path.relative_to(source)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, output)
        manifest[str(output.relative_to(target))] = hashlib.sha256(output.read_bytes()).hexdigest()
(target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("Local web build: dist/web (no CDN)")
