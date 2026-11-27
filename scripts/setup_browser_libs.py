"""WSL/Ubuntu local browser dependency fallback, without sudo or maintainer scripts."""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
archives = root / ".browser-libs/archives"
libraries = root / ".browser-libs/root"
archives.mkdir(parents=True, exist_ok=True)
libraries.mkdir(parents=True, exist_ok=True)
subprocess.run(["apt-get", "download", "libnspr4", "libnss3", "libasound2t64"],
               cwd=archives, check=True)
receipt = []
for archive in sorted(archives.glob("*.deb")):
    subprocess.run(["dpkg-deb", "--extract", str(archive), str(libraries)], check=True)
    fields = subprocess.check_output(["dpkg-deb", "--field", str(archive), "Package", "Version"], text=True)
    receipt.append({"archive": archive.name, "metadata": fields.strip(),
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "source": "configured Ubuntu apt repository; apt-get download",
                    "scope": "test-only local libraries; no maintainer scripts executed"})
target = root / "reports/E01-env/browser-libraries.json"
target.parent.mkdir(parents=True, exist_ok=True)
with target.open("x") as stream:
    json.dump(receipt, stream, indent=2)
print("Local browser libraries ready; receipt:", target.relative_to(root))
