"""Offline, stdlib-only report metadata index and append-only EEAS lesson ledger."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata
import zipfile

CATALOG_SCHEMA = "eeas.experience-catalog.v1"
LESSON_SCHEMA = "eeas.development-lesson.v1"
MAX_METADATA_BYTES = 1024 * 1024
MAX_CASES = 64
MAX_TEXT = 1200
SNAPSHOTS = {"snapshot", "snapshots", "startup-snapshot"}
SHA = re.compile(r"[0-9a-f]{64}\Z")


class CatalogError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def strict_json(raw):
    def pairs(values):
        output = {}
        for key, value in values:
            if key in output:
                raise CatalogError("Duplicate JSON key")
            output[key] = value
        return output

    def constant(_):
        raise CatalogError("Non-finite JSON number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def portable_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value or ":" in value:
        raise CatalogError("Expected a portable relative path")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or value != path.as_posix() or any(p in (".", "..") for p in path.parts):
        raise CatalogError("Relative path traversal/alias is forbidden")
    for part in path.parts:
        if part.endswith((" ", ".")) or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            raise CatalogError("Windows path alias is forbidden")
    return path


def safe_source(root, relative):
    path = Path(root)
    for part in portable_relative(relative).parts:
        path = path / part
        if path.is_symlink():
            raise CatalogError("Symlink evidence is forbidden")
    return path


def inspect_file(path, limit=MAX_METADATA_BYTES):
    """Hash every raw byte; bound only parsing. Never execute or import report data."""
    if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
        raise CatalogError("Evidence must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CatalogError("Evidence must be a regular file")
        digest, prefix, total = hashlib.sha256(), bytearray(), 0
        while True:
            block = os.read(descriptor, 65536)
            if not block:
                break
            digest.update(block)
            total += len(block)
            if len(prefix) < limit:
                prefix.extend(block[:limit-len(prefix)])
        after = os.fstat(descriptor)
        current = path.stat(follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if identity(before) != identity(after) or identity(after) != identity(current) or total != after.st_size:
            raise CatalogError("Evidence changed while hashing")
        return {"sha256": digest.hexdigest(), "byte_length": total,
                "metadata_bytes_read": len(prefix), "metadata_truncated": total > len(prefix)}, bytes(prefix)
    finally:
        os.close(descriptor)


def redacted_text(value, maximum=MAX_TEXT):
    text = str(value)
    text = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(r'''(?i)((?:api[_-]?key|authorization|password|token|secret)["']?\s*[:=]\s*)("(?:\\.|[^"])*"|'(?:\\.|[^'])*'|[^\s,;]+)''',
                  r"\1[REDACTED]", text)
    return {"text": text[:maximum], "truncated": len(text) > maximum}


def command_preview(value):
    if not isinstance(value, list):
        return redacted_text(value)
    hidden, output = False, []
    for item in value:
        text = str(item)
        output.append("[REDACTED]" if hidden else text)
        hidden = text.lower().lstrip("-") in ("api-key", "api_key", "token", "password", "secret", "authorization")
    return redacted_text(output)


def parse_object(raw, source):
    if source["metadata_truncated"]:
        return None, "JSON_NOT_PARSED_OVERSIZE"
    try:
        value = strict_json(raw)
    except (ValueError, UnicodeError, RecursionError):
        return None, "INVALID_JSON"
    if not isinstance(value, dict):
        return None, "JSON_NOT_OBJECT"
    return value, None


def report_kind(name):
    lower = name.lower()
    if lower in ("result.json", "freeze.json"):
        return lower.split(".")[0]
    if lower in ("readme", "readme.md", "readme.txt"):
        return "readme"
    if re.search(r"(?:^|[-_.])(stdout|stderr)(?:[-_.]|$)", lower) and lower.endswith((".txt", ".log")):
        return "log"
    return None


def failure_titles(raw, source_path):
    output, seen, observed = [], set(), 0
    for line_number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
        match = re.match(r"^(FAILED|ERROR)\s+(.+)", line) or re.match(r"^_+\s+(.+?)\s+_+\s*$", line)
        if not match:
            continue
        title = match.group(2) if len(match.groups()) == 2 else match.group(1)
        key = hashlib.sha256(title.encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        observed += 1
        if len(output) < MAX_CASES:
            output.append({"source_path": source_path, "line": line_number,
                           "title": redacted_text(title), "observed_title_sha256": key})
    return output, observed


def metadata_status(result):
    if result is None:
        return "UNKNOWN", "RESULT_MISSING_OR_UNREADABLE"
    declared, code = result.get("status"), result.get("exit_code")
    if declared == "PASS" and type(code) is int and code == 0:
        return "PASS", "DECLARED_RESULT_AND_EXIT_CODE_ONLY"
    if declared == "FAIL" and type(code) is int and code != 0:
        return "FAIL", "DECLARED_RESULT_AND_EXIT_CODE_ONLY"
    if declared in ("BLOCKED", "NOT_RUN", "UNKNOWN"):
        return declared, "DECLARED_METADATA_ONLY"
    return "UNKNOWN", "INCOMPLETE_OR_CONFLICTING_RESULT"


def scan_reports(reports_root):
    reports_root = Path(reports_root)
    if reports_root.is_symlink() or not reports_root.is_dir():
        raise CatalogError("Reports root must be a real directory")
    root = reports_root.resolve()
    groups, refs, rejected, aliases = {}, [], [], {}
    coverage = Counter({key: 0 for key in (
        "directories_enumerated", "filesystem_leaf_entries_seen", "non_metadata_files_not_read", "metadata_files_hashed",
        "raw_bytes_hashed", "metadata_prefix_bytes_read", "truncated_metadata_files", "status_PASS", "status_FAIL",
        "status_UNKNOWN", "status_BLOCKED", "status_NOT_RUN",
    )})
    stack = [root]
    while stack:
        directory = stack.pop()
        coverage["directories_enumerated"] += 1
        for item in sorted(os.scandir(directory), key=lambda e: e.name):
            relative = Path(item.path).relative_to(root).as_posix()
            if item.is_symlink():
                rejected.append({"path": relative, "reason": "SYMLINK_NOT_READ"})
                continue
            if item.is_dir(follow_symlinks=False):
                if item.name.lower() in SNAPSHOTS or item.name.lower().endswith("-snapshot"):
                    refs.append({"path": relative, "status": "SOURCE_SNAPSHOT_REFERENCE_ONLY", "traversed": False})
                else:
                    stack.append(Path(item.path))
                continue
            coverage["filesystem_leaf_entries_seen"] += 1
            kind = report_kind(item.name)
            if kind is None:
                coverage["non_metadata_files_not_read"] += 1
                continue
            try:
                portable_relative(relative)
                alias = unicodedata.normalize("NFC", relative).casefold()
                if alias in aliases:
                    raise CatalogError("Windows case/Unicode alias with " + aliases[alias])
                aliases[alias] = relative
                source, raw = inspect_file(safe_source(root, relative))
                source.update({"path": relative, "kind": kind})
                parent = PurePosixPath(relative).parent.as_posix()
                group = groups.setdefault(parent, {"path": parent, "sources": [], "failure_cases": [],
                                                   "observed_failure_title_count": 0, "parse_notes": []})
                group["sources"].append(source)
                coverage["metadata_files_hashed"] += 1
                coverage["raw_bytes_hashed"] += source["byte_length"]
                coverage["metadata_prefix_bytes_read"] += source["metadata_bytes_read"]
                coverage["truncated_metadata_files"] += int(source["metadata_truncated"])
                if kind in ("result", "freeze"):
                    value, error = parse_object(raw, source)
                    group[kind] = value
                    if error:
                        group["parse_notes"].append({"path": relative, "reason": error})
                elif kind == "log":
                    cases, count = failure_titles(raw, relative)
                    group["observed_failure_title_count"] += count
                    group["failure_cases"].extend(cases)
                elif kind == "readme":
                    group.setdefault("readme_headings", []).extend(
                        redacted_text(line) for line in raw.decode("utf-8", errors="replace").splitlines()
                        if line.startswith("#"))
            except (CatalogError, OSError) as error:
                rejected.append({"path": relative, "reason": str(error)})
    entries = []
    for key in sorted(groups):
        entry = groups[key]
        result, freeze = entry.pop("result", None), entry.pop("freeze", None)
        freeze = freeze or {}
        declared = result.get("status") if result else None
        entry["declared_status"] = declared if isinstance(declared, str) and declared in (
            "PASS", "FAIL", "BLOCKED", "NOT_RUN", "UNKNOWN") else None
        entry["status"], entry["status_basis"] = metadata_status(result)
        entry["git_base"] = freeze.get("git_base") if re.fullmatch(r"[0-9a-f]{40}", str(freeze.get("git_base"))) else None
        entry["scope"] = {"slice": redacted_text(freeze.get("slice", "UNKNOWN")),
                          "acceptance": redacted_text(freeze.get("acceptance_before_run", "UNKNOWN")),
                          "command": command_preview(freeze.get("command", "UNKNOWN")),
                          "source_hash_entry_count": len(freeze["code_sha256"]) if isinstance(freeze.get("code_sha256"), dict) else None}
        entry["qualification"] = {"assessment": "NOT_INFERRED", "native_windows": "UNKNOWN",
                                  "company_model": "UNKNOWN", "os_isolation": "UNKNOWN",
                                  "declared_preview": redacted_text(result.get("qualification", "UNKNOWN") if result else "UNKNOWN")}
        entry["failure_cases_truncated"] = len(entry["failure_cases"]) > MAX_CASES or any(
            source["metadata_truncated"] for source in entry["sources"] if source["kind"] == "log")
        entry["failure_cases"] = entry["failure_cases"][:MAX_CASES]
        coverage["status_" + entry["status"]] += 1
        entries.append(entry)
    return {"schema_version": CATALOG_SCHEMA, "generated_at": utc_now(), "reports_root": str(root),
            "method": "machine metadata index only; no LLM interpretation, test replay or qualification inference",
            "coverage": {**dict(coverage), "metadata_parent_groups": len(entries), "snapshot_refs_not_traversed": len(refs),
                         "rejected_entries": len(rejected), "human_curated_lessons_count": None},
            "entries": entries, "snapshot_refs": refs, "rejected_entries": rejected}


def record_digest(record):
    return hashlib.sha256(canonical({k: v for k, v in record.items() if k != "record_sha256"})).hexdigest()


def validate_lesson(record, root, previous=None):
    required = {"schema_version", "lesson_key", "revision", "recorded_at", "area", "trigger", "observation",
                "decision", "scope", "qualification", "unvalidated", "evidence", "previous_sha256", "record_sha256"}
    if not isinstance(record, dict) or set(record) != required or record["schema_version"] != LESSON_SCHEMA:
        raise CatalogError("Lesson schema fields are missing or unknown")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", str(record["lesson_key"])):
        raise CatalogError("Invalid lesson key")
    if type(record["revision"]) is not int or record["revision"] < 1:
        raise CatalogError("Invalid lesson revision")
    for field in ("recorded_at", "area", "trigger", "observation", "decision", "scope"):
        if not isinstance(record[field], str) or not record[field] or len(record[field]) > 8000:
            raise CatalogError("Lesson text field invalid: " + field)
    if not isinstance(record["qualification"], dict) or not isinstance(record["unvalidated"], list):
        raise CatalogError("Explicit qualification/unvalidated scope is required")
    if not all(isinstance(v, str) and v for v in record["qualification"].values()) or not all(
            isinstance(v, str) and v for v in record["unvalidated"]):
        raise CatalogError("Qualification boundaries must be explicit strings")
    expected_revision, expected_sha = (1, None) if previous is None else (previous["revision"] + 1, previous["record_sha256"])
    if (record["revision"], record["previous_sha256"]) != (expected_revision, expected_sha):
        raise CatalogError("Lesson revision chain is not append-only")
    if record.get("record_sha256") != record_digest(record):
        raise CatalogError("Lesson record hash differs")
    if not isinstance(record["evidence"], list) or not 1 <= len(record["evidence"]) <= 64:
        raise CatalogError("Lesson requires bounded evidence")
    seen = set()
    for evidence in record["evidence"]:
        if not isinstance(evidence, dict) or set(evidence) != {"path", "sha256", "role"} or not SHA.fullmatch(str(evidence["sha256"])):
            raise CatalogError("Evidence schema/hash invalid")
        if not isinstance(evidence["role"], str) or not evidence["role"] or len(evidence["role"]) > 100:
            raise CatalogError("Evidence role is required")
        portable_relative(evidence["path"])
        if evidence["path"] in seen:
            raise CatalogError("Duplicate evidence path")
        seen.add(evidence["path"])
        actual, _ = inspect_file(safe_source(root, evidence["path"]), limit=0)
        if actual["sha256"] != evidence["sha256"]:
            raise CatalogError("Evidence hash differs: " + evidence["path"])
    return record


def load_lessons(root, relative):
    path = safe_source(root, relative)
    if not path.exists():
        return []
    source, raw = inspect_file(path, limit=16 * MAX_METADATA_BYTES)
    if source["metadata_truncated"]:
        raise CatalogError("Lesson ledger exceeds explicit 16 MiB bound")
    records, previous = [], {}
    for line in raw.splitlines():
        if not line.strip():
            raise CatalogError("Blank ledger lines are forbidden")
        try:
            record = strict_json(line)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise CatalogError("Invalid lesson JSONL") from error
        key = record.get("lesson_key") if isinstance(record, dict) else None
        validate_lesson(record, root, previous.get(key))
        records.append(record)
        previous[key] = record
    return records


def append_lesson(root, relative, record):
    root = Path(root).resolve()
    path = safe_source(root, relative)
    lock = path.with_name(path.name + ".append-lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        if not isinstance(record, dict):
            raise CatalogError("Lesson must be an object")
        records = load_lessons(root, relative)
        previous = next((old for old in reversed(records) if old["lesson_key"] == record.get("lesson_key")), None)
        validate_lesson(record, root, previous)
        existing = path.read_bytes() if path.exists() else b""
        if existing and not existing.endswith(b"\n"):
            raise CatalogError("Ledger lacks final newline; do not repair old bytes automatically")
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(canonical(record) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        lock.unlink()


def write_new_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def bundle_experience(root, catalog_path, output):
    """Small snapshot, with selected raw lesson evidence; all other report bytes stay external."""
    root = Path(root).resolve()
    lessons = load_lessons(root, "experience/lessons.jsonl")
    included = {"experience/README.md", "experience/lessons.jsonl", "scripts/experience_catalog.py",
                ".claude/skills/eeas-development-lessons/SKILL.md",
                ".claude/skills/eeas-development-lessons/references/ledger-schema.md"}
    external_snapshots = [e for record in lessons for e in record["evidence"] if any(
        part.lower() in SNAPSHOTS or part.lower().endswith("-snapshot") for part in PurePosixPath(e["path"]).parts)]
    included.update(e["path"] for record in lessons for e in record["evidence"] if e not in external_snapshots)
    payloads, members, total = {}, [], 0
    aliases = set()
    for relative in sorted(included):
        alias = unicodedata.normalize("NFC", relative).casefold()
        parts = PurePosixPath(relative).parts
        if alias in ("experience_manifest.json", "experience/catalog.json"):
            raise CatalogError("Selected evidence collides with reserved snapshot members")
        if any(part.lower() in (".env", ".venv", "node_modules", ".eeas-sdk-state") or
               part.lower().endswith(".token") for part in parts) or alias.startswith(".claude/settings"):
            raise CatalogError("Private settings/token/cache cannot be bundled")
        if alias in aliases:
            raise CatalogError("Experience bundle has Windows path aliases")
        aliases.add(alias)
        path = safe_source(root, relative)
        source, raw = inspect_file(path, limit=16*MAX_METADATA_BYTES)
        if source["metadata_truncated"]:
            raise CatalogError("Selected raw evidence exceeds 16 MiB; use a separate evidence archive")
        total += len(raw)
        if total > 64*MAX_METADATA_BYTES:
            raise CatalogError("Experience snapshot exceeds 64 MiB; use separate raw evidence archive")
        payloads[relative] = raw
        members.append({"path": relative, "sha256": source["sha256"], "byte_length": len(raw)})
    catalog_source, catalog_raw = inspect_file(Path(catalog_path), limit=16*MAX_METADATA_BYTES)
    if catalog_source["metadata_truncated"]:
        raise CatalogError("Catalog exceeds snapshot bound")
    if total + len(catalog_raw) > 64*MAX_METADATA_BYTES:
        raise CatalogError("Experience snapshot exceeds 64 MiB")
    catalog = strict_json(catalog_raw)
    if not isinstance(catalog, dict) or catalog.get("schema_version") != CATALOG_SCHEMA:
        raise CatalogError("Catalog schema differs")
    payloads["experience/catalog.json"] = catalog_raw
    members.append({"path": "experience/catalog.json", "sha256": catalog_source["sha256"], "byte_length": len(catalog_raw)})
    manifest = {"schema_version": "eeas.experience-snapshot.v1", "created_at": utc_now(), "members": members,
                "scope": "experience/catalog/skill/tool plus selected original evidence only; not product/final delivery/public-campaign completion",
                "external_raw_reports_required": True, "source_snapshots_included": False,
                "external_selected_snapshot_evidence": external_snapshots,
                "catalog_coverage": catalog.get("coverage"), "curated_lesson_records": len(lessons)}
    with Path(output).open("xb") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative, raw in payloads.items():
                archive.writestr(relative, raw)
            archive.writestr("EXPERIENCE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--reports-root", required=True)
    scan.add_argument("--output", required=True)
    for action in ("validate", "append"):
        command = sub.add_parser(action)
        command.add_argument("--repo-root", required=True)
        command.add_argument("--lessons-file", default="experience/lessons.jsonl")
        if action == "append":
            command.add_argument("--record", required=True)
    bundle = sub.add_parser("bundle")
    bundle.add_argument("--repo-root", required=True)
    bundle.add_argument("--catalog", required=True)
    bundle.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "scan":
            catalog = scan_reports(args.reports_root)
            write_new_json(args.output, catalog)
            print(json.dumps({"output": args.output, "coverage": catalog["coverage"]}, ensure_ascii=False))
        elif args.action == "bundle":
            manifest = bundle_experience(args.repo_root, args.catalog, args.output)
            print(json.dumps({"output": args.output, "members": len(manifest["members"]), "scope": manifest["scope"]}))
        else:
            root = Path(args.repo_root).resolve()
            if args.action == "append":
                raw = safe_source(root, args.record).read_bytes()
                append_lesson(root, args.lessons_file, strict_json(raw))
            records = load_lessons(root, args.lessons_file)
            print(json.dumps({"records": len(records), "status": "HASH_SCHEMA_AND_CHAIN_VALID_ONLY"}))
        return 0
    except (CatalogError, OSError, ValueError, RecursionError) as error:
        print(json.dumps({"status": "ERROR", "reason": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
