"""Standard-library bundle validation. No archive code is imported or executed."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
import zipfile

MANIFEST = "BUNDLE_MANIFEST.json"
SCHEMA = "eeas.release-bundle.v1"
MAX_FILES = 10000
MAX_MEMBER = 256 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
SHA = re.compile(r"^[0-9a-f]{64}$")
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10)), "com¹", "com²", "com³", "lpt¹", "lpt²", "lpt³"}
PRIVATE = {".git", ".venv", ".venv313", "node_modules", "__pycache__", ".pytest_cache",
           ".mypy_cache", ".tools", ".runtimes", ".browser-libs", ".offline-probe", ".node-tools",
           "var", "workspaces", "local-access.token", "credentials.json", "settings.local.json",
           ".credentials.json", ".env", ".env.local", "id_rsa", "id_ed25519"}


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def read_json(data: bytes) -> dict:
    try:
        result = json.loads(data, object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid UTF-8 JSON") from exc
    if not isinstance(result, dict):
        raise ValueError("JSON object required")
    return result


def safe_name(name: str, *, private: bool = True) -> str:
    """Reject ambiguous POSIX/Windows names instead of normalizing them."""
    if not isinstance(name, str) or not name or len(name) > 1024 or "\\" in name:
        raise ValueError("Unsafe archive path")
    if name != unicodedata.normalize("NFC", name):
        raise ValueError("Noncanonical Unicode path")
    parts = name.split("/")
    for part in parts:
        if (not part or len(part.encode("utf-16-le")) > 510 or part in {".", ".."} or part.endswith((".", " "))
                or any(ord(c) < 32 or c in ':<>\"|?*' for c in part)
                or part.split(".")[0].casefold() in RESERVED):
            raise ValueError("Unsafe archive path")
        lowered = part.casefold()
        if private and (lowered in PRIVATE or lowered.startswith(".env.") or lowered.endswith((".pem", ".key", ".pyc"))):
            raise ValueError("Private/cache path is forbidden")
    return name


def validate_names(names, *, private=True):
    seen = {}
    files = set()
    for name in names:
        safe_name(name, private=private)
        components = name.split("/")
        for i in range(1, len(components) + 1):
            prefix = "/".join(components[:i])
            key = prefix.casefold()
            if key in seen and seen[key] != prefix:
                raise ValueError("Windows case-insensitive path collision")
            seen[key] = prefix
            if i != len(components) and key in files:
                raise ValueError("Archive file/directory collision")
        key = name.casefold()
        if key in files or any(other.startswith(key + "/") for other in seen if other != key):
            raise ValueError("Duplicate or file/directory collision")
        files.add(key)


def assert_no_links(path: Path):
    """Includes ancestor links and Windows junctions/reparse points."""
    absolute = Path(os.path.abspath(path))
    for item in (absolute, *absolute.parents):
        if os.path.lexists(item):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Symlink/reparse-point path is forbidden")


def _manifest(archive: zipfile.ZipFile, *, expected_schema=SCHEMA, private=True):
    members = archive.infolist()
    if not members or len(members) > MAX_FILES:
        raise ValueError("Archive file count limit")
    validate_names([entry.filename for entry in members], private=private)
    total = 0
    for entry in members:
        mode = (entry.external_attr >> 16) & 0xFFFF
        if entry.is_dir() or stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
            raise ValueError("Archive contains symlink or nonregular member")
        if entry.flag_bits & 1 or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError("Unsupported/encrypted ZIP member")
        total += entry.file_size
        if entry.file_size > MAX_MEMBER or total > MAX_TOTAL:
            raise ValueError("Archive size limit")
        if entry.file_size > 1024 * 1024 and entry.file_size > max(entry.compress_size, 1) * 200:
            raise ValueError("Archive compression ratio limit")
    try:
        info = archive.getinfo(MANIFEST)
    except KeyError as exc:
        raise ValueError("Missing bundle manifest") from exc
    if info.file_size > MAX_MANIFEST:
        raise ValueError("Manifest size limit")
    manifest = read_json(archive.read(info))
    if manifest.get("schema_version") != expected_schema:
        raise ValueError("Unsupported bundle schema")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files or MANIFEST in files:
        raise ValueError("Empty or self-referential manifest")
    validate_names(files, private=private)
    if set(files) | {MANIFEST} != {entry.filename for entry in members}:
        raise ValueError("Unlisted or missing archive member")
    for name, record in files.items():
        if (not isinstance(record, dict) or not SHA.fullmatch(str(record.get("sha256", "")))
                or type(record.get("bytes")) is not int or record["bytes"] < 0
                or archive.getinfo(name).file_size != record["bytes"]):
            raise ValueError("Invalid member digest/size record")
    if expected_schema == SCHEMA:
        if (manifest.get("release_status") != "DEVELOPMENT_PREVIEW"
                or manifest.get("target") not in {"windows-py310", "linux-py310"}
                or not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("source_commit", "")))):
            raise ValueError("Invalid release target/status/source commit")
    return manifest


def validate_bundle(path: Path, expected_sha256: str, *, expected_schema=SCHEMA, private=True) -> dict:
    """The expected digest must come from the separately trusted delivery record."""
    assert_no_links(path)
    if not SHA.fullmatch(expected_sha256) or digest_file(path) != expected_sha256:
        raise ValueError("Bundle SHA256 mismatch")
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = _manifest(archive, expected_schema=expected_schema, private=private)
            for name, record in manifest["files"].items():
                count, h = 0, hashlib.sha256()
                with archive.open(name) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        count += len(block)
                        if count > record["bytes"]:
                            raise ValueError("Expanded member size mismatch")
                        h.update(block)
                if count != record["bytes"] or h.hexdigest() != record["sha256"]:
                    raise ValueError("Member SHA256 mismatch: " + name)
            return manifest
    except (zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError) as exc:
        raise ValueError("Invalid ZIP archive") from exc


def extract_bundle(path: Path, destination: Path, expected_sha256: str, *, expected_schema=SCHEMA, private=True) -> dict:
    # Complete validation before creating any path. Never use ZipFile.extractall.
    manifest = validate_bundle(path, expected_sha256, expected_schema=expected_schema, private=private)
    assert_no_links(destination)
    if os.path.lexists(destination):
        raise ValueError("Destination exists; select a fresh directory")
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    with zipfile.ZipFile(path) as archive:
        for name in [*manifest["files"], MANIFEST]:
            output = destination.joinpath(*name.split("/"))
            output.parent.mkdir(parents=True, exist_ok=True)
            assert_no_links(output)
            h, count = hashlib.sha256(), 0
            limit = manifest["files"][name]["bytes"] if name != MANIFEST else MAX_MANIFEST
            with archive.open(name) as source, output.open("xb") as stream:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    count += len(block)
                    if count > limit:
                        raise ValueError("Archive changed while unpacking")
                    h.update(block)
                    stream.write(block)
            if name != MANIFEST and (count != limit or h.hexdigest() != manifest["files"][name]["sha256"]):
                raise ValueError("Archive changed while unpacking")
            output.chmod(0o600 if expected_schema != SCHEMA else 0o644)
    if digest_file(path) != expected_sha256:
        raise ValueError("Archive changed while unpacking")
    return manifest


def validate_extracted(root: Path) -> dict:
    assert_no_links(root)
    manifest = read_json((root / MANIFEST).read_bytes())
    if manifest.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported extracted bundle schema")
    files = manifest.get("files", {})
    validate_names(files)
    actual = set()
    for path in root.rglob("*"):
        assert_no_links(path)
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != set(files) | {MANIFEST}:
        raise ValueError("Unlisted or missing extracted file")
    for name, record in files.items():
        path = root / name
        if path.stat().st_size != record["bytes"] or digest_file(path) != record["sha256"]:
            raise ValueError("Extracted file SHA256 mismatch: " + name)
    return manifest
