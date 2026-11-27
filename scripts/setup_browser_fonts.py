"""Install a CJK font for WSL/Ubuntu browser tests, without sudo or package scripts."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
archives = ROOT / ".browser-libs/font-download"
extracted = ROOT / ".browser-libs/font-extract" / stamp
archives.mkdir(parents=True, exist_ok=True)
subprocess.run(["apt-get", "download", "fonts-noto-cjk"], cwd=archives, check=True)
packages = sorted(archives.glob("fonts-noto-cjk_*.deb"))
if len(packages) != 1:
    raise ValueError("Use exactly one pinned CJK package in this test environment")
package = packages[0]
extracted.mkdir(parents=True, exist_ok=False)
subprocess.run(["dpkg-deb", "--extract", str(package), str(extracted)], check=True)
font = extracted / "usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
font_hash = hashlib.sha256(font.read_bytes()).hexdigest()
directory = Path.home() / ".local/share/fonts" / ("eeas-cjk-" + font_hash[:12])
if directory.is_symlink():
    raise ValueError("Task font directory must not be a symlink")
directory.mkdir(parents=True, exist_ok=True)
target = directory / font.name
if target.exists():
    if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != font_hash:
        raise ValueError("Existing task font differs from the package")
else:
    with target.open("xb") as stream, font.open("rb") as source:
        shutil.copyfileobj(source, stream)
subprocess.run(["fc-cache", "-f", str(directory)], check=True)
match = subprocess.check_output(["fc-match", "sans-serif:lang=zh-cn"], text=True).strip()
if "Noto Sans CJK" not in match:
    raise RuntimeError("Browser Chinese fallback font is still unavailable")
receipt = {"schema_version": "eeas.browser-font.v1", "result": "PASS",
           "package_metadata": subprocess.check_output(["dpkg-deb", "--field", str(package), "Package", "Version"], text=True).strip(),
           "package_hash": hashlib.sha256(package.read_bytes()).hexdigest(), "font_hash": font_hash,
           "source": "Configured Ubuntu apt repository; fonts-noto-cjk", "scope": "WSL browser tests only",
           "installed_file": str(target), "fc_match": match, "maintainer_scripts_executed": False,
           "remove_path": str(directory), "windows_native": "NOT_VALIDATED"}
report = ROOT / "reports/browser-fonts" / stamp
report.mkdir(parents=True, exist_ok=False)
(report / "result.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
shutil.copyfile(extracted / "usr/share/doc/fonts-noto-cjk/copyright", report / "copyright.txt")
print(json.dumps({"result": "PASS", "fc_match": match, "evidence": str(report.relative_to(ROOT))}, ensure_ascii=False))
