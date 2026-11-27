"""Validation and safe extraction for optional, platform-specific offline test kits."""
from __future__ import annotations
import hashlib
import json
from email.parser import BytesParser
from pathlib import Path
import re
import stat
import zipfile

try:
    from bundle_support import assert_no_links, digest_file, safe_name, validate_names
except ModuleNotFoundError:
    from scripts.bundle_support import assert_no_links, digest_file, safe_name, validate_names


def browser_members(archive_path):
    result, total = {}, 0
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > 10000:
            raise ValueError("Browser archive file limit")
        names = []
        for entry in infos:
            safe_name(entry.filename.rstrip("/"))
            mode = entry.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or entry.flag_bits & 1:
                raise ValueError("Browser archive symlink/nonregular/encrypted member")
            if entry.is_dir():
                continue
            total += entry.file_size
            if entry.file_size > 512 * 1024 * 1024 or total > 1024 * 1024 * 1024:
                raise ValueError("Browser archive size limit")
            if entry.file_size > 1024 * 1024 and entry.file_size > max(entry.compress_size, 1) * 200:
                raise ValueError("Browser archive compression ratio limit")
            names.append(entry.filename)
            h = hashlib.sha256()
            with archive.open(entry) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
            result[entry.filename] = {"sha256": h.hexdigest(), "bytes": entry.file_size,
                                      "executable": bool(mode & 0o111)}
        validate_names(names)
    return result


def wheel_identity(wheel, target):
    """Read actual wheel metadata/tags without importing or installing its contents."""
    with zipfile.ZipFile(wheel) as archive:
        names, total = [], 0
        for entry in archive.infolist():
            safe_name(entry.filename.rstrip("/"))
            mode = entry.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or entry.flag_bits & 1:
                raise ValueError("Test wheel symlink/nonregular/encrypted member")
            if entry.is_dir():
                continue
            names.append(entry.filename)
            total += entry.file_size
            if entry.file_size > 512 * 1024 * 1024 or total > 1024 * 1024 * 1024 or len(names) > 10000:
                raise ValueError("Test wheel size/file limit")
            if entry.file_size > 1024 * 1024 and entry.file_size > max(entry.compress_size, 1) * 200:
                raise ValueError("Test wheel compression ratio limit")
        validate_names(names)
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Test wheel requires one package identity")
        info = metadata[0].rsplit("/", 1)[0] + "/WHEEL"
        if info not in names or any(archive.getinfo(name).file_size > 2 * 1024 * 1024 for name in [metadata[0], info]):
            raise ValueError("Test wheel metadata/tags missing or oversized")
        identity = BytesParser().parsebytes(archive.read(metadata[0]), headersonly=True)
        tags = BytesParser().parsebytes(archive.read(info), headersonly=True).get_all("Tag", [])
        compatible = False
        for tag in tags:
            parts = tag.split("-")
            if len(parts) != 3:
                raise ValueError("Invalid wheel tag")
            interpreter, abi, platform = parts
            python_ok = bool({"py3", "py310", "cp310"} & set(interpreter.split(".")))
            abi_ok = abi in {"none", "abi3", "cp310"}
            platform_ok = (platform == "any" or platform == "win_amd64" if target == "windows-py310" else
                           platform == "any" or platform == "linux_x86_64" or
                           platform.startswith(("manylinux", "musllinux")) and platform.endswith("_x86_64"))
            compatible |= python_ok and abi_ok and platform_ok
        if not compatible:
            raise ValueError("Test wheel is incompatible with target Python3.10/platform")
        name, version = identity.get("Name"), identity.get("Version")
        if not name or not version:
            raise ValueError("Test wheel identity is missing")
        return re.sub(r"[-_.]+", "-", name).lower(), version


def verify_test_kit(directory, target):
    assert_no_links(directory)
    manifest_path = directory / "manifest.json"
    assert_no_links(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != "eeas.offline-tests.v1" or manifest.get("target") != target
            or manifest.get("python") != "3.10" or manifest.get("architecture") != "x86_64"):
        raise ValueError("Test-kit target/version mismatch")
    files = manifest.get("files_sha256", {})
    if not files:
        raise ValueError("Empty test-kit manifest")
    validate_names(files)
    actual = set()
    for path in directory.rglob("*"):
        assert_no_links(path)
        if path.is_file() and path.relative_to(directory).as_posix() not in {"manifest.json", ".gitignore"}:
            actual.add(path.relative_to(directory).as_posix())
    if actual != set(files):
        raise ValueError("Unlisted or missing test-kit file")
    for name, expected in files.items():
        if not re.fullmatch(r"[0-9a-f]{64}", str(expected)) or digest_file(directory / name) != expected:
            raise ValueError("Test-kit SHA256 mismatch")
    pins = {}
    for line in (directory / "requirements-test.lock").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)", line)
        if not match:
            raise ValueError("Test lock requires exact pins; URLs/options are forbidden")
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        if name in pins:
            raise ValueError("Duplicate test dependency pin")
        pins[name] = match[2]
    if pins != manifest.get("versions"):
        raise ValueError("Test lock/manifest disagree")
    wheel_versions = {}
    for wheel in sorted((directory / "wheels").glob("*.whl")):
        name, version = wheel_identity(wheel, target)
        if name in wheel_versions:
            raise ValueError("Duplicate test wheel identity")
        wheel_versions[name] = version
    if wheel_versions != pins:
        raise ValueError("Test wheel identities/lock disagree")
    browser = manifest["browser"]
    safe_name(browser["archive"])
    if browser["archive"] not in files:
        raise ValueError("Unlisted browser archive")
    if not re.fullmatch(r"[0-9]+", browser["revision"]) or browser["kind"] != "chromium-headless-shell":
        raise ValueError("Invalid pinned browser registry")
    if browser_members(directory / browser["archive"]) != browser["files"]:
        raise ValueError("Browser member manifest mismatch")
    expected_top = "chrome-headless-shell-win64/" if target == "windows-py310" else "chrome-headless-shell-linux64/"
    if not browser["files"] or any(not name.startswith(expected_top) for name in browser["files"]):
        raise ValueError("Browser archive platform/layout mismatch")
    return manifest


def extract_browser(directory, destination, manifest):
    browser = manifest["browser"]
    safe_name(browser["archive"])
    if browser_members(directory / browser["archive"]) != browser["files"]:
        raise ValueError("Browser changed before extraction")
    validate_names(browser["files"])
    assert_no_links(destination)
    if destination.exists():
        raise ValueError("Browser cache exists; choose a fresh directory")
    destination.mkdir(parents=True, exist_ok=False)
    revision = destination / ("chromium_headless_shell-" + browser["revision"])
    revision.mkdir()
    with zipfile.ZipFile(directory / browser["archive"]) as archive:
        for name, record in browser["files"].items():
            output = revision.joinpath(*name.split("/"))
            output.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            count = 0
            with archive.open(name) as source, output.open("xb") as stream:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    count += len(block)
                    if count > record["bytes"]:
                        raise ValueError("Browser member changed while extracting")
                    h.update(block)
                    stream.write(block)
            if count != record["bytes"] or h.hexdigest() != record["sha256"]:
                raise ValueError("Browser extraction SHA256 mismatch")
            output.chmod(0o755 if record["executable"] else 0o644)
    (revision / "INSTALLATION_COMPLETE").write_text("Hash-checked EEAS offline test kit\n", encoding="utf-8")
    return destination
