"""Prepare pinned test wheels and official browser artifacts; never overwrite old kits."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

from bundle_support import canonical_json, digest_file, safe_name
from offline_test_support import browser_members, verify_test_kit
from prepare_dependencies import closure

ROOT = Path(__file__).resolve().parents[1]


def linux_libraries(directory, source_root):
    receipt = json.loads((source_root / "reports/E01-env/browser-libraries.json").read_bytes())
    for entry in receipt:
        archive = source_root / ".browser-libs/archives" / entry["archive"]
        if digest_file(archive) != entry["sha256"]:
            raise ValueError("Public Linux .deb source changed")
        raw = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(archive)])
        regular, aliases = {}, {}
        with tarfile.open(fileobj=io.BytesIO(raw)) as source:
            for member in source.getmembers():
                name = member.name.removeprefix("./").rstrip("/")
                if not name or name == "." and member.isdir():
                    continue
                safe_name(name)
                if member.isfile() and (name.startswith("usr/lib/x86_64-linux-gnu/") and ".so" in name or name.endswith("/copyright")):
                    regular[name] = source.extractfile(member).read()
                elif member.issym() and name.startswith("usr/lib/x86_64-linux-gnu/") and ".so" in name:
                    if "/" in member.linkname or member.linkname in {".", ".."}:
                        raise ValueError("Unsafe native library alias")
                    aliases[name] = str(Path(name).parent / member.linkname)
        for name, target in aliases.items():
            if target not in regular:
                raise ValueError("Unresolved public library alias")
            regular[name] = regular[target]  # Copy bytes; never create filesystem symlinks.
        for name, data in regular.items():
            output = directory / ("system-libs/" + Path(name).name if ".so" in name else "licenses/linux/" + Path(name).parent.name + "-copyright.txt")
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists() and output.read_bytes() != data:
                raise ValueError("Linux library case/filename collision")
            output.write_bytes(data)
    (directory / "licenses/linux/source.json").write_bytes(canonical_json({"archives": receipt,
        "scope": "Ubuntu24 x86_64 test-only NSS/NSPR/ASOUND; original .deb SHA verified; aliases copied as regular bytes; no maintainer script or package installation"}))


def prepare(target, linux_source):
    versions = closure(["pytest", "playwright"], target)
    if versions["playwright"] != "1.63.0" or versions["pytest"] != "9.1.1":
        raise ValueError("Development test versions changed; review pins before preparing")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = ROOT / "vendor" / ("test-" + target) / stamp
    (directory / "wheels").mkdir(parents=True, exist_ok=False)
    (directory / ".gitignore").write_text("wheels/\nbrowser/*.zip\nsystem-libs/\n", encoding="utf-8")
    (directory / "requirements-test.lock").write_text("".join(f"{name}=={version}\n" for name, version in sorted(versions.items())), encoding="utf-8")
    command = [sys.executable, "-m", "pip", "download", "--isolated", "--no-cache-dir", "--index-url", "https://pypi.org/simple",
               "--only-binary=:all:", "--no-deps", "--dest", str(directory / "wheels"), "-r", str(directory / "requirements-test.lock")]
    if target == "windows-py310":
        command += ["--platform", "win_amd64", "--python-version", "3.10", "--implementation", "cp", "--abi", "cp310"]
    subprocess.run(command, check=True)
    registry = Path(importlib.metadata.distribution("playwright").locate_file("playwright/driver/package/browsers.json"))
    registry_bytes = registry.read_bytes()
    (directory / "playwright-registry.json").write_bytes(registry_bytes)
    selected = next(item for item in json.loads(registry_bytes)["browsers"] if item["name"] == "chromium-headless-shell")
    platform = "win64" if target == "windows-py310" else "linux64"
    url = f"https://cdn.playwright.dev/builds/cft/{selected['browserVersion']}/{platform}/chrome-headless-shell-{platform}.zip"
    (directory / "browser").mkdir()
    archive = directory / "browser/headless-shell.zip"
    with urllib.request.urlopen(url, timeout=60) as response, archive.open("xb") as stream:
        if not response.url.startswith("https://storage.googleapis.com/chrome-for-testing-public/"):
            raise ValueError("Unexpected nonofficial browser redirect")
        final_url = response.url
        expected = int(response.headers["Content-Length"])
        count = 0
        for block in iter(lambda: response.read(1024 * 1024), b""):
            count += len(block)
            if count > 256 * 1024 * 1024:
                raise ValueError("Browser download limit")
            stream.write(block)
    if count != expected:
        raise ValueError("Browser download truncated")
    members = browser_members(archive)
    with zipfile.ZipFile(archive) as source:
        for name in members:
            if "LICENSE" in Path(name).name.upper() or "NOTICE" in Path(name).name.upper():
                output = directory / "licenses/browser" / Path(name).name
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(source.read(name))
    if not (directory / "licenses/browser").is_dir():
        raise ValueError("Official browser archive lacks licence material")
    if target == "linux-py310":
        linux_libraries(directory, linux_source)
    files = {path.relative_to(directory).as_posix(): digest_file(path) for path in directory.rglob("*") if path.is_file() and path.name != ".gitignore"}
    manifest = {"schema_version": "eeas.offline-tests.v1", "target": target, "python": "3.10", "architecture": "x86_64",
                "versions": versions, "source": "Exact public PyPI binary wheels; official Playwright registry and Chrome-for-Testing artifacts",
                "browser": {"kind": "chromium-headless-shell", "revision": selected["revision"], "version": selected["browserVersion"],
                            "archive": "browser/headless-shell.zip", "source_url": url, "final_url": final_url,
                            "archive_bytes": count, "files": members,
                            "registry_sha256": hashlib.sha256(registry_bytes).hexdigest(), "license_scope": "Actual upstream archive licence/third-party notices preserved"},
                "files_sha256": files, "video": "NOT_SUPPORTED_FFMPEG_NOT_INCLUDED", "full_chromium": "NOT_INCLUDED",
                "native_windows": "BLOCKED", "qualification": "PREPARED_NOT_EXECUTED"}
    (directory / "manifest.json").write_bytes(canonical_json(manifest))
    verify_test_kit(directory, target)
    return directory.relative_to(ROOT).as_posix()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--linux-source-root", type=Path, required=True)
    args = parser.parse_args()
    targets = {target: prepare(target, args.linux_source_root) for target in ("linux-py310", "windows-py310")}
    selection = {"schema_version": "eeas.offline-test-selection.v1", "targets": targets, "optional": True,
                 "install_entry": "scripts/install_test_dependencies.py", "native_windows": "BLOCKED"}
    (ROOT / "profiles/offline-test-dependencies.json").write_bytes(canonical_json(selection))
    print(json.dumps(selection))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
