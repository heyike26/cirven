"""Offline audit ledger: explicit single-case runs, never an Agent loop or corpus downloader."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
from urllib.parse import unquote, urlsplit
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CATALOG = "profiles/public-campaign-features.json"
CLASSES = {"PUBLIC_MATERIAL", "PROJECT_WORKFLOW", "FAULT_BOUNDARY", "UPSTREAM_QUALIFICATION"}
PROVENANCE_SCHEMA = "eeas.public-material-provenance.v1"
PROVENANCE_MAX_BYTES = 512 * 1024
PROVENANCE_MAX_FILES = 256
MATERIAL_MAX_BYTES = 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def put(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(encode(value))


def contained(base: Path, name: str) -> Path:
    path = base / name
    if Path(name).is_absolute() or ".." in Path(name).parts or path.is_symlink():
        raise ValueError("unsafe artifact path")
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("artifact escapes its owner directory")
    return path


def ident(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("invalid project/case identifier")
    return value


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = set()
        self.elements = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.elements[attrs["id"]] = tag
        if tag not in {"a", "button", "input", "select", "textarea", "form"}:
            return
        name = attrs.get("id")
        for key, prefix in (("data-view", "view:"), ("data-go-view", "goto:")):
            name = name or (prefix + attrs[key] if key in attrs else None)
        if not name and "name" in attrs:
            name = "name:" + attrs["name"] + ":" + attrs.get("value", "")
        if name:
            self.names.add(name)


def surfaces(root: Path) -> dict:
    routes = set()
    for node in ast.walk(ast.parse((root / "apps/api/main.py").read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"
                        and dec.args and isinstance(dec.args[0], ast.Constant)
                        and dec.func.attr in {"get", "post", "put", "delete", "middleware"}):
                    routes.add(dec.func.attr.upper() + " " + dec.args[0].value)
    controls = Controls()
    controls.feed((root / "apps/web/index.html").read_text(encoding="utf-8"))
    scripts = {p.as_posix() for p in (root / "scripts").glob("*.py")}
    scripts = {str(Path(p).relative_to(root)) for p in scripts if Path(p).name != "public_campaign.py"}
    scripts |= {p.name for p in root.glob("*-windows.cmd")}
    scripts.add("apps/api/__main__.py")
    return {"routes": routes, "controls": controls.names, "entry_files": scripts}


def check_catalog(root: Path, catalog: dict) -> dict:
    if catalog.get("schema") != "eeas.public-campaign-catalog.v1":
        raise ValueError("unsupported campaign catalog")
    cases = catalog["cases"]
    ids = [ident(case["id"]) for case in cases]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("missing/duplicate campaign cases")
    for case in cases:
        if case["evidence_class"] not in CLASSES or not case.get("acceptance"):
            raise ValueError("case requires evidence class and concrete acceptance")
        if type(case.get("external_qualification", False)) is not bool:
            raise ValueError("external qualification must be boolean")
    if {case["evidence_class"] for case in cases} != CLASSES:
        raise ValueError("all four evidence classes must remain explicit")
    actual = surfaces(root)
    for key, values in actual.items():
        mappings = catalog["surfaces"][key]
        if set(mappings) != values or any(not refs or not set(refs) <= set(ids) for refs in mappings.values()):
            raise ValueError("unmapped/stale audit surface: " + key)
    display = Controls()
    display.feed((root / "apps/web/index.html").read_text(encoding="utf-8"))
    for name, region in catalog.get("display_regions", {}).items():
        if (name in actual["controls"] or display.elements.get(name) != region.get("element")
                or not region.get("cases") or not set(region["cases"]) <= set(ids)):
            raise ValueError("unmapped/stale noninteractive display region: " + name)
    required_files = actual["entry_files"] | {"apps/api/main.py", "apps/web/index.html",
                                             "apps/web/assets/app.js", "apps/web/assets/app.css"}
    required_files |= {p.relative_to(root).as_posix() for pattern in ("*.py", "*.sql")
                       for p in (root / "packages").rglob(pattern)}
    required_files |= {p.relative_to(root).as_posix() for p in (root / "apps/api").glob("*.py")}
    required_files |= {p.relative_to(root).as_posix() for p in (root / "profiles").glob("*.json")
                       if p.name != Path(CATALOG).name}
    if set(catalog["source_sha256"]) != required_files:
        raise ValueError("unmapped/stale product source inventory")
    for name, expected in catalog["source_sha256"].items():
        if digest(contained(root, name).read_bytes()) != expected:
            raise ValueError("catalog source changed: " + name)
    return {"case_count": len(ids), **{key + "_count": len(values) for key, values in actual.items()},
            "meaning": "inventory consistency only; no feature was executed"}


def init(root: Path, directory: Path) -> dict:
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("campaign must be inside the harness for record_slice input freezing")
    catalog = load(root / CATALOG)
    check_catalog(root, catalog)
    directory.mkdir(parents=True, exist_ok=False)
    put(directory / "catalog.json", catalog)
    manifest = {"schema": "eeas.public-campaign.v1", "catalog_sha256": digest(encode(catalog)),
                "minimum_distinct_public_projects": 10, "state_scope": "executed_probe_only",
                "source_verification": "origin/license declarations need independent acquisition review"}
    put(directory / "campaign.json", manifest)
    put(directory / "campaign-seal.json", {"campaign_sha256": digest(encode(manifest))})
    return manifest


def catalog_for(root: Path, directory: Path) -> dict:
    catalog = load(directory / "catalog.json")
    manifest = load(directory / "campaign.json")
    if (manifest.get("schema") != "eeas.public-campaign.v1"
            or digest((directory / "campaign.json").read_bytes()) != load(directory / "campaign-seal.json")["campaign_sha256"]
            or digest(encode(catalog)) != manifest["catalog_sha256"]
            or (directory / "catalog.json").read_bytes() != (root / CATALOG).read_bytes()):
        raise ValueError("campaign catalog is immutable or no longer current")
    check_catalog(root, catalog)
    return catalog


def origin_key(url: str) -> str:
    value = urlsplit(url)
    if (value.scheme != "https" or not value.hostname or value.username or value.password
            or value.query or value.fragment or value.port not in {None, 443} or not value.path.strip("/")):
        raise ValueError("public project requires a credential-free HTTPS repository origin")
    path = unquote(value.path).rstrip("/")
    host = value.hostname.lower()
    segments = path.strip("/").split("/")
    if (any(part in {"", ".", ".."} for part in segments)
            or host in {"github.com", "codeberg.org"} and len(segments) != 2
            or host == "gitlab.com" and "-" in segments):
        raise ValueError("public origin must identify the repository root, not a file/tree URL")
    if host in {"github.com", "gitlab.com", "codeberg.org"}:
        path = path.lower()
    return host + path.removesuffix(".git")


def provenance_path(base: Path, name: str) -> Path:
    """A portable, canonical relative filename; reject aliases before touching the filesystem."""
    if (not isinstance(name, str) or not name or len(name) > 1024 or name.startswith("/")
            or any(ord(char) < 32 for char in name) or any(char in name for char in '\\:<>"|?*')):
        raise ValueError("unsafe provenance path")
    parts = name.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {prefix + str(n) for prefix in ("COM", "LPT") for n in range(1, 10)}
    if any(part in {"", ".", ".."} or part.endswith((".", " "))
           or part.split(".", 1)[0].upper() in reserved for part in parts):
        raise ValueError("unsafe provenance path alias")
    path = base.joinpath(*parts)
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("provenance path escapes its owner")
    return path


def provenance_linked(path: Path) -> bool:
    metadata = path.stat(follow_symlinks=False)
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def provenance_read(path: Path, maximum: int) -> bytes:
    """Only bounded regular files, with no symlink in their supplied absolute path."""
    path = path.absolute()
    if any(provenance_linked(parent) for parent in (path, *path.parents)):
        raise ValueError("provenance symlinks/reparse points are forbidden")
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ValueError("provenance is not a bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size > maximum):
            raise ValueError("provenance file identity changed")
        chunks, length = [], 0
        while length <= maximum:
            block = os.read(fd, min(65536, maximum + 1 - length))
            if not block:
                break
            chunks.append(block)
            length += len(block)
        after = os.fstat(fd)
        current = path.stat(follow_symlinks=False)
        if (length > maximum or any(provenance_linked(parent) for parent in (path, *path.parents))
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                or (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)):
            raise ValueError("provenance changed while reading or exceeds its size limit")
        return b"".join(chunks)
    finally:
        os.close(fd)


def provenance_json(raw: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate provenance JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite provenance JSON")))
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid provenance JSON") from exc
    return value


def provenance_fields(value: dict, names: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError("unsupported provenance schema fields")


def provenance_sha(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid provenance SHA256")


def provenance_span(text: str, start: int, end: int) -> dict:
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise ValueError("invalid provenance text range")
    prefix, fragment = text[:start], text[start:end]
    ends_line = prefix.endswith(("\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"))
    line_start = len(prefix.splitlines()) + int(not prefix or ends_line)
    return {"char_start": start, "char_end_exclusive": end,
            "byte_start": len(prefix.encode("utf-8")), "byte_end_exclusive": len(text[:end].encode("utf-8")),
            "line_start": line_start, "line_end": line_start + len(fragment.splitlines()) - 1}


def provenance_range(actual: dict, expected: dict) -> None:
    provenance_fields(actual, set(expected))
    if any(type(actual[key]) is not int or actual[key] != value for key, value in expected.items()):
        raise ValueError("provenance body/line/character/byte mapping mismatch")


def validate_provenance(sidecar_raw: bytes, raw: bytes, base: Path, *, origin: str | None,
                        revision: str) -> tuple[dict, dict[str, bytes]]:
    """Verify complete original bodies and exact generated complement; no network or archive execution."""
    if len(sidecar_raw) > PROVENANCE_MAX_BYTES or not raw or len(raw) > MATERIAL_MAX_BYTES:
        raise ValueError("provenance/material size limit")
    sidecar = provenance_json(sidecar_raw)
    provenance_fields(sidecar, {"schema", "parent_archive", "material", "selected", "synthetic_ranges",
                               "conversion", "source_authentication"})
    if (sidecar["schema"] != PROVENANCE_SCHEMA
            or sidecar["source_authentication"] != "DECLARED_NOT_INDEPENDENTLY_VERIFIED"):
        raise ValueError("unsupported provenance schema or source qualification")
    parent = sidecar["parent_archive"]
    provenance_fields(parent, {"sha256", "origin", "commit_sha", "archive_url"})
    provenance_sha(parent["sha256"])
    if (not isinstance(parent["commit_sha"], str) or re.fullmatch(r"[0-9a-f]{40}", parent["commit_sha"]) is None
            or not isinstance(parent["origin"], str) or not isinstance(origin, str)
            or parent["commit_sha"] != revision or not origin or origin_key(parent["origin"]) != origin_key(origin)):
        raise ValueError("provenance frozen commit/repository binding mismatch")
    # v1 describes the fixed official GitHub archive/raw-file layout used by this corpus.
    key = origin_key(parent["origin"])
    if not key.startswith("github.com/"):
        raise ValueError("provenance v1 requires a GitHub repository origin")
    repository_path = urlsplit(parent["origin"]).path.strip("/").removesuffix(".git")
    repository = "https://github.com/" + repository_path
    archive_url = "https://codeload.github.com/" + repository_path + "/zip/" + revision
    if parent["archive_url"] != archive_url:
        raise ValueError("provenance archive URL is not bound to the fixed repository commit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("provenance material must be UTF8") from exc
    if "\x00" in text:
        raise ValueError("provenance material contains NUL")
    metadata = sidecar["material"]
    provenance_fields(metadata, {"sha256", "byte_length", "characters"})
    provenance_sha(metadata["sha256"])
    if (metadata["sha256"] != digest(raw) or type(metadata["byte_length"]) is not int or metadata["byte_length"] != len(raw)
            or type(metadata["characters"]) is not int or metadata["characters"] != len(text)):
        raise ValueError("provenance material identity mismatch")
    conversion = sidecar["conversion"]
    provenance_fields(conversion, {"version", "input_manifest_sha256"})
    provenance_sha(conversion["input_manifest_sha256"])
    if not isinstance(conversion["version"], str) or not 1 <= len(conversion["version"]) <= 128:
        raise ValueError("invalid provenance converter version")
    selected = sidecar["selected"]
    if not isinstance(selected, list) or not 1 <= len(selected) <= PROVENANCE_MAX_FILES:
        raise ValueError("provenance selected file count limit")
    captured, aliases, cursor, wrappers, source_hashes = {}, set(), 0, [], set()
    for item in selected:
        provenance_fields(item, {"path", "source_path", "archive_member", "source_url", "sha256", "byte_length",
                                 "characters", "body", "original"})
        path = provenance_path(base, item["path"])
        provenance_path(base, item["source_path"])
        provenance_path(base, item["archive_member"])
        alias = item["path"].casefold()
        if (item["path"] != "original/" + item["source_path"] or alias in aliases
                or item["archive_member"] != repository_path.rsplit("/", 1)[1] + "-" + revision + "/" + item["source_path"]
                or item["source_url"] != repository + "/blob/" + revision + "/" + item["source_path"]):
            raise ValueError("provenance path alias or fixed original source URL mismatch")
        aliases.add(alias)
        provenance_sha(item["sha256"])
        original = provenance_read(path, MATERIAL_MAX_BYTES)
        try:
            original_text = original.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError("provenance selected original must be UTF8") from exc
        if (not original or "\x00" in original_text or digest(original) != item["sha256"] or item["sha256"] in source_hashes
                or type(item["byte_length"]) is not int or item["byte_length"] != len(original)
                or type(item["characters"]) is not int or item["characters"] != len(original_text)):
            raise ValueError("provenance original checksum/length mismatch or repeated body")
        source_hashes.add(item["sha256"])
        body = item["body"]
        if not isinstance(body, dict):
            raise ValueError("invalid provenance body range")
        start, end = body.get("char_start"), body.get("char_end_exclusive")
        expected = provenance_span(text, start, end)
        if start < cursor or start and text[start - 1] not in "\n\r":
            raise ValueError("provenance bodies overlap or do not start at a source line")
        provenance_range(body, expected)
        provenance_range(item["original"], provenance_span(original_text, 0, len(original_text)))
        if (text[start:end] != original_text or raw[body["byte_start"]:body["byte_end_exclusive"]] != original
                or text.splitlines()[body["line_start"] - 1:body["line_end"]] != original_text.splitlines()):
            raise ValueError("provenance original body differs from material byte/character/line slice")
        if cursor < start:
            wrappers.append({"kind": "GENERATED_WRAPPER", **provenance_span(text, cursor, start)})
        cursor = end
        captured[item["path"]] = original
    if cursor < len(text):
        wrappers.append({"kind": "GENERATED_WRAPPER", **provenance_span(text, cursor, len(text))})
    supplied = sidecar["synthetic_ranges"]
    if not isinstance(supplied, list) or len(supplied) != len(wrappers):
        raise ValueError("provenance synthetic wrapper complement mismatch")
    for actual, expected in zip(supplied, wrappers):
        provenance_fields(actual, set(expected))
        if actual["kind"] != "GENERATED_WRAPPER":
            raise ValueError("invalid provenance synthetic wrapper kind")
        provenance_range({key: value for key, value in actual.items() if key != "kind"},
                         {key: value for key, value in expected.items() if key != "kind"})
    return sidecar, captured


def register(root: Path, directory: Path, project_id: str, source: Path, license_file: Path,
             *, source_kind: str, origin: str | None, revision: str, license_id: str,
             expected_sha256: str, expected_license_sha256: str, provenance_file: Path | None = None) -> dict:
    catalog_for(root, directory)
    ident(project_id)
    if source_kind not in {"public", "synthetic"} or not revision.strip() or not license_id.strip():
        raise ValueError("explicit source kind, frozen revision and license required")
    raw = provenance_read(source, MATERIAL_MAX_BYTES) if provenance_file is not None else source.read_bytes()
    legal = provenance_read(license_file, MATERIAL_MAX_BYTES) if provenance_file is not None else license_file.read_bytes()
    if not raw or not legal or digest(raw) != expected_sha256 or digest(legal) != expected_license_sha256:
        raise ValueError("raw source/license checksum mismatch or empty input")
    key = origin_key(origin) if source_kind == "public" and origin else None
    if source_kind == "public" and key is None:
        raise ValueError("public origin required; fixtures cannot count as public projects")
    sidecar_raw, originals = None, {}
    if provenance_file is not None:
        sidecar_raw = provenance_read(provenance_file, PROVENANCE_MAX_BYTES)
        if digest(raw) != expected_sha256:
            raise ValueError("raw source checksum changed before provenance validation")
        _, originals = validate_provenance(sidecar_raw, raw, provenance_file.parent, origin=origin, revision=revision)
    for existing in (directory / "projects").glob("*/source.json"):
        old = source_for(existing.parent)
        if old["raw_sha256"] == digest(raw) or (key is not None and old["origin_key"] == key):
            raise ValueError("duplicate raw source/repository; another version cannot count as another project")
    target = directory / "projects" / project_id
    target.mkdir(parents=True, exist_ok=False)
    (target / "source.raw").write_bytes(raw)
    (target / "license.raw").write_bytes(legal)
    record = {"schema": "eeas.public-project-source.v1", "project_id": project_id,
              "source_kind": source_kind, "origin": origin, "origin_key": key,
              "revision": revision, "license_id": license_id,
              "raw_sha256": digest(raw), "license_sha256": digest(legal),
              "original_filename": source.name, "license_filename": license_file.name,
              "provenance_status": "DECLARED_NOT_INDEPENDENTLY_VERIFIED"}
    if sidecar_raw is not None:
        record["provenance"] = {"schema": PROVENANCE_SCHEMA, "path": "provenance/sidecar.json", "sha256": digest(sidecar_raw)}
        captured = target / "provenance"
        captured.mkdir()
        with (captured / "sidecar.json").open("xb") as stream:
            stream.write(sidecar_raw)
        for name, original in originals.items():
            destination = provenance_path(captured, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(original)
    put(target / "source.json", record)
    put(target / "source-seal.json", {"source_json_sha256": digest(encode(record))})
    return record


def source_for(project: Path) -> dict:
    value = load(project / "source.json")
    raw = provenance_read(project / "source.raw", MATERIAL_MAX_BYTES) if "provenance" in value else (project / "source.raw").read_bytes()
    if (digest((project / "source.json").read_bytes()) != load(project / "source-seal.json")["source_json_sha256"]
            or digest(raw) != value["raw_sha256"]
            or digest((project / "license.raw").read_bytes()) != value["license_sha256"]):
        raise ValueError("immutable source/license binding changed")
    if "provenance" in value:
        record = value["provenance"]
        provenance_fields(record, {"schema", "path", "sha256"})
        if record["schema"] != PROVENANCE_SCHEMA or record["path"] != "provenance/sidecar.json":
            raise ValueError("unsupported immutable provenance binding")
        provenance_sha(record["sha256"])
        sidecar = provenance_path(project, record["path"])
        sidecar_raw = provenance_read(sidecar, PROVENANCE_MAX_BYTES)
        if digest(sidecar_raw) != record["sha256"]:
            raise ValueError("immutable provenance binding changed")
        validate_provenance(sidecar_raw, raw,
                            sidecar.parent, origin=value["origin"], revision=value["revision"])
    return value


def checked_evidence(root: Path, evidence: Path, binding: Path, source: dict,
                     command: list[str], exit_code: int, observation_required: bool) -> dict:
    frozen = load(evidence / "freeze.json")
    result = load(evidence / "result.json")
    if frozen.get("schema_version") != "eeas.slice-run.v1" or frozen["command"] != command:
        raise ValueError("not the executed record_slice command")
    if result["exit_code"] != exit_code or result["status"] != ("PASS" if exit_code == 0 else "FAIL"):
        raise ValueError("actual subprocess and evidence exit status disagree")
    for name, sha in frozen["code_sha256"].items():
        if digest(contained(evidence / "snapshot", name).read_bytes()) != sha:
            raise ValueError("record_slice frozen snapshot changed")
    binding_name = binding.relative_to(root).as_posix()
    if frozen["code_sha256"].get(binding_name) != digest(binding.read_bytes()):
        raise ValueError("case input was not frozen before the actual run")
    files = ["freeze.json", "result.json", "stdout.txt", "stderr.txt", "campaign-tests.xml"]
    suites = ET.parse(evidence / "campaign-tests.xml").getroot()
    tests = list(suites.iter("testcase"))
    counts = {"executed": len(tests), "skipped": sum(t.find("skipped") is not None for t in tests),
              "failed": sum(t.find("failure") is not None or t.find("error") is not None for t in tests)}
    if not tests or counts["skipped"]:
        raise ValueError("no executed tests or hidden skipped audit cases")
    if exit_code == 0 and counts["failed"]:
        raise ValueError("pytest success contradicts actual test report")
    if observation_required:
        observation = load(evidence / "campaign-observation.json")
        expected = {"schema": "eeas.campaign-observation.v1", "binding_sha256": digest(binding.read_bytes()),
                    "source_sha256": source["raw_sha256"], "case_id": load(binding)["case_id"]}
        if any(observation.get(key) != value for key, value in expected.items()) or not observation.get("actual_receipts"):
            raise ValueError("executed test did not bind actual receipts to this source/case")
        files.append("campaign-observation.json")
        for artifact in observation["actual_receipts"]:
            path = contained(evidence, artifact["path"])
            if not path.is_file() or not path.stat().st_size or digest(path.read_bytes()) != artifact["sha256"]:
                raise ValueError("missing/changed actual audit artifact")
            files.append(artifact["path"])
    return {"test_counts": counts, "files_sha256": {name: digest(contained(evidence, name).read_bytes()) for name in files},
            "frozen_input_sha256": frozen["code_sha256"]}


def run_case(root: Path, directory: Path, project_id: str, case_id: str, node_ids: list[str]) -> dict:
    catalog = catalog_for(root, directory)
    case = next((c for c in catalog["cases"] if c["id"] == ident(case_id)), None)
    if not case or case.get("external_qualification"):
        raise ValueError("unknown case or external qualification cannot be granted by this harness")
    if not node_ids:
        raise ValueError("an explicit pytest probe is required")
    for node in node_ids:
        file = node.split("::", 1)[0]
        if not file.startswith("tests/") or not file.endswith(".py") or not contained(root, file).is_file():
            raise ValueError("probe must be an existing explicit test inside tests/")
    project = directory / "projects" / ident(project_id)
    source = source_for(project)
    if source["source_kind"] != "public" and case["evidence_class"] in {"PUBLIC_MATERIAL", "PROJECT_WORKFLOW"}:
        raise ValueError("original fixtures cannot satisfy a public-material/project-workflow case")
    attempt = project / "attempts" / uuid.uuid4().hex
    attempt.mkdir(parents=True)
    binding = attempt / "binding.json"
    put(binding, {"schema": "eeas.campaign-binding.v1", "attempt_id": attempt.name,
                  "started_at": datetime.now(timezone.utc).isoformat(),
                  "case_id": case_id, "case": case, "project": source,
                  "source_json_sha256": digest((project / "source.json").read_bytes()),
                  "catalog_sha256": digest((directory / "catalog.json").read_bytes()),
                  "raw_path": (project / "source.raw").relative_to(root).as_posix(),
                  **({"provenance_path": (project / source["provenance"]["path"]).relative_to(root).as_posix()}
                     if "provenance" in source else {})})
    # JUnit output is placed in record_slice's actual evidence directory by the tiny pytest plugin below.
    command = [sys.executable, "-m", "pytest", *node_ids, "-q", "-ra", "-p", "scripts.public_campaign"]
    references = [binding, project / "source.json", project / "source.raw", project / "license.raw",
                  directory / "catalog.json"]
    if "provenance" in source:
        sidecar = provenance_path(project, source["provenance"]["path"])
        references.append(sidecar)
        references += [provenance_path(sidecar.parent, item["path"]) for item in provenance_json(sidecar.read_bytes())["selected"]]
    references += [root / name for name in catalog["source_sha256"] if name.startswith("profiles/")]
    launcher = [sys.executable, str(root / "scripts/record_slice.py"), "--acceptance", case["acceptance"]]
    for reference in references:
        launcher += ["--reference", reference.relative_to(root).as_posix()]
    launcher += ["E11-campaign-" + attempt.name, "--", *command]
    process = subprocess.run(launcher, cwd=root, capture_output=True, check=False,
                             env={**os.environ, "EEAS_CAMPAIGN_BINDING": str(binding),
                                  "PYTHONPATH": str(root) + os.pathsep + os.environ.get("PYTHONPATH", "")})
    (attempt / "launcher-stdout.txt").write_bytes(process.stdout)
    (attempt / "launcher-stderr.txt").write_bytes(process.stderr)
    receipt = {"schema": "eeas.campaign-attempt.v1", "case_id": case_id, "attempt_id": attempt.name,
               "binding_sha256": digest(binding.read_bytes()), "probe_exit_code": process.returncode,
               "state": "FAIL", "review_required": True, "state_scope": "executed_probe_only",
               "evidence_class": case["evidence_class"], "source_kind": source["source_kind"],
               "environment": {"sys_platform": sys.platform, "platform": platform.platform(),
                               "python": sys.version, "machine": platform.machine()},
               "runtime_kind": None, "runtime_kind_reason": "probe transport must be established from actual artifacts, never inferred as company-live",
               "finished_at": datetime.now(timezone.utc).isoformat()}
    try:
        matches = re.findall(r"^Evidence: (reports/[^\r\n]+)$", process.stdout.decode("utf-8", "replace"), re.M)
        if len(matches) != 1:
            raise ValueError("record_slice did not return one actual evidence directory")
        evidence = contained(root, matches[0])
        receipt["evidence"] = evidence.relative_to(root).as_posix()
        # Retain raw output even when a later acceptance guard rejects the run.
        receipt["files_sha256"] = {name: digest((evidence / name).read_bytes()) for name in
                                   ("freeze.json", "result.json", "stdout.txt", "stderr.txt",
                                    "campaign-tests.xml", "campaign-observation.json")
                                   if (evidence / name).is_file()}
        receipt["frozen_input_sha256"] = load(evidence / "freeze.json")["code_sha256"]
        receipt.update(checked_evidence(root, evidence, binding, source, command, process.returncode,
                                        observation_required=process.returncode == 0 or (evidence / "campaign-observation.json").is_file()))
        source_for(project)
        catalog_for(root, directory)
        required_inputs = {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in references}
        required_inputs.update(catalog["source_sha256"])
        if any(receipt["frozen_input_sha256"].get(name) != sha for name, sha in required_inputs.items()):
            raise ValueError("frozen code/source/license/catalog differs from this campaign")
        receipt["state"] = "PASS" if process.returncode == 0 else "FAIL"
    except (ValueError, KeyError, OSError, ET.ParseError) as exc:
        receipt["validation_error"] = str(exc)
    receipt["attempt_files_sha256"] = {name: digest((attempt / name).read_bytes()) for name in
                                      ("binding.json", "launcher-stdout.txt", "launcher-stderr.txt")}
    put(attempt / "receipt.json", receipt)
    put(attempt / "receipt-seal.json", {"receipt_sha256": digest(encode(receipt))})
    return receipt


def summary(root: Path, directory: Path) -> dict:
    catalog = catalog_for(root, directory)
    projects = []
    seen_sources, seen_origins = set(), set()
    for project in sorted((directory / "projects").glob("*")):
        source = source_for(project)
        if source["raw_sha256"] in seen_sources or source["origin_key"] and source["origin_key"] in seen_origins:
            raise ValueError("duplicate campaign source/repository")
        seen_sources.add(source["raw_sha256"])
        if source["origin_key"]:
            seen_origins.add(source["origin_key"])
        matrix = {case["id"]: {"state": "QUALIFICATION_REQUIRED" if case.get("external_qualification") else "NOT_RUN",
                               "evidence_class": case["evidence_class"], "attempts": []} for case in catalog["cases"]}
        attempts = list((project / "attempts").glob("*"))
        if any(not (attempt / "receipt.json").is_file() for attempt in attempts):
            raise ValueError("incomplete attempt retained; cannot silently omit interrupted execution")
        for path in sorted((attempt / "receipt.json" for attempt in attempts),
                           key=lambda p: load(p.parent / "binding.json")["started_at"]):
            receipt = load(path)
            if digest(path.read_bytes()) != load(path.parent / "receipt-seal.json")["receipt_sha256"]:
                raise ValueError("attempt receipt changed")
            if receipt["case_id"] not in matrix or matrix[receipt["case_id"]]["state"] == "QUALIFICATION_REQUIRED":
                raise ValueError("attempt is outside this project's full case matrix")
            for name, sha in receipt["attempt_files_sha256"].items():
                if digest(contained(path.parent, name).read_bytes()) != sha:
                    raise ValueError("attempt input/output changed")
            if "files_sha256" in receipt:
                evidence = contained(root, receipt["evidence"])
                for name, sha in receipt["files_sha256"].items():
                    if digest(contained(evidence, name).read_bytes()) != sha:
                        raise ValueError("raw evidence output/artifact changed")
                for name, sha in receipt["frozen_input_sha256"].items():
                    if digest(contained(evidence / "snapshot", name).read_bytes()) != sha:
                        raise ValueError("frozen evidence input changed")
            row = matrix[receipt["case_id"]]
            row["state"] = receipt["state"]
            row["attempts"].append(receipt)
        projects.append({"source": source, "cases": matrix, "counts": dict(Counter(c["state"] for c in matrix.values()))})
    return {"schema": "eeas.campaign-summary.v1", "projects": projects,
            "distinct_declared_public_sources": sum(p["source"]["source_kind"] == "public" for p in projects),
            "verified_public_sources": None, "minimum_distinct_public_projects": 10,
            "meaning": "PASS means a bound probe executed successfully; independent semantic/source review remains required",
            "provenance_limit": "hashes detect byte changes, not an author's ability to forge the entire evidence tree"}


def pytest_configure(config):
    """Set the installed pytest JUnit reporter's output; does not fabricate test results."""
    evidence = os.environ.get("EEAS_EVIDENCE_DIR")
    if evidence:
        config.option.xmlpath = str(Path(evidence) / "campaign-tests.xml")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("catalog")
    for name in ("init", "register", "run", "summary"):
        item = sub.add_parser(name)
        item.add_argument("directory", type=Path)
        if name in {"register", "run"}:
            item.add_argument("project_id")
        if name == "register":
            item.add_argument("source", type=Path)
            item.add_argument("license_file", type=Path)
            item.add_argument("--source-kind", choices=["public", "synthetic"], required=True)
            item.add_argument("--origin")
            item.add_argument("--provenance-file", type=Path)
            for field in ("revision", "license-id", "expected-sha256", "expected-license-sha256"):
                item.add_argument("--" + field, required=True)
        if name == "run":
            item.add_argument("case_id")
            item.add_argument("node_ids", nargs="+")
    args = parser.parse_args()
    try:
        if args.action == "catalog":
            value = check_catalog(ROOT, load(ROOT / CATALOG))
        elif args.action == "init":
            value = init(ROOT, args.directory.resolve())
        elif args.action == "register":
            value = register(ROOT, args.directory.resolve(), args.project_id, args.source, args.license_file,
                             source_kind=args.source_kind, origin=args.origin, revision=args.revision,
                             license_id=args.license_id, expected_sha256=args.expected_sha256,
                             expected_license_sha256=args.expected_license_sha256, provenance_file=args.provenance_file)
        elif args.action == "run":
            value = run_case(ROOT, args.directory.resolve(), args.project_id, args.case_id, args.node_ids)
        else:
            value = summary(ROOT, args.directory.resolve())
        print(encode(value).decode(), end="")
        return 1 if value.get("state") == "FAIL" else 0
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, "Campaign rejected: " + str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
