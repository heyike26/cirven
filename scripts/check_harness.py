#!/usr/bin/env python3
"""Validate the instruction bundle, NOT an EEAS application or live Agent."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

class BundleError(ValueError):
    """Invalid package content."""

def local_file(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise BundleError(f"Invalid relative path: {name!r}")
    p = root / name
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise BundleError(f"Path escapes bundle: {name}") from exc
    if p.is_symlink() or not p.is_file():
        raise BundleError(f"Missing file or unsupported symlink: {name}")
    return p

def read_json(root: Path, name: str) -> Any:
    return json.loads(local_file(root, name).read_text(encoding="utf-8"))

def chars(root: Path, name: str) -> int:
    return len(local_file(root, name).read_text(encoding="utf-8"))

def check_skill_registry(root: Path, route_skills: set[str], optional: list[str]) -> set[str]:
    """Registered optional skills are packaged, never silently added to context."""
    if not isinstance(optional, list):
        raise BundleError("Optional skills must be an explicit list")
    registered = set(route_skills)
    for name in optional:
        if not isinstance(name, str) or not re.fullmatch(r"\.claude/skills/[a-z0-9-]+/SKILL\.md", name):
            raise BundleError("Invalid optional skill path")
        if name in registered:
            raise BundleError("Duplicate optional or route skill")
        text = local_file(root, name).read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) != 3 or parts[0].strip() or not re.search(r"^name:\s*\S", parts[1], re.M) or not re.search(r"^description:\s*\S", parts[1], re.M):
            raise BundleError("Missing optional skill metadata")
        registered.add(name)
    actual = {p.relative_to(root).as_posix() for p in (root / ".claude/skills").glob("*/SKILL.md")}
    if registered != actual:
        raise BundleError("Orphan or missing skills")
    return registered

def select_files(config: dict, milestones: dict, route: str, mid: str | None) -> list[str]:
    if route not in config["routes"]:
        raise BundleError(f"Unknown route: {route}")
    entry = config["routes"][route]
    mid = mid or entry["default_milestone"]
    if mid and mid not in milestones:
        raise BundleError(f"Unknown milestone: {mid}")
    files = list(config["baseline_read"])
    if entry["skill"]:
        files.append(entry["skill"])
    if mid:
        files.append(milestones[mid]["file"])
    references = entry["references"]
    if mid and milestones[mid]["route"] == route:
        references = milestones[mid]["suggested_references"]
    return list(dict.fromkeys(files + references))

def check_bundle(root: Path, manifest: bool = False) -> dict:
    config = read_json(root, "harness.json")
    if config["kind"] != "INSTRUCTION_PACKAGE_NOT_PLATFORM_IMPLEMENTATION":
        raise BundleError("Wrong bundle kind")
    limits = config["limits"]
    for name in config["entrypoints"].values():
        if chars(root, name) > limits["entry_max_chars"]:
            raise BundleError(f"Entry too large: {name}")
    if local_file(root, "CLAUDE.md").read_text().strip() != "@AGENTS.md":
        raise BundleError("CLAUDE.md must only import AGENTS.md")
    baseline = sum(chars(root, p) for p in config["baseline_read"])
    if baseline > limits["baseline_max_chars"]:
        raise BundleError("Baseline too large")
    items = read_json(root, config["milestone_index"])["milestones"]
    milestones = {m["id"]: m for m in items}
    expected = {f"E{i:02d}" for i in range(11)}
    if len(items) != len(milestones) or set(milestones) != expected:
        raise BundleError("Invalid or duplicate milestone IDs")
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(mid: str) -> None:
        if mid in visiting:
            raise BundleError(f"Dependency cycle: {mid}")
        if mid in visited:
            return
        if mid not in milestones:
            raise BundleError(f"Missing dependency: {mid}")
        visiting.add(mid)
        for dep in milestones[mid]["implementation_dependencies"]:
            visit(dep)
        visiting.remove(mid)
        visited.add(mid)
    for mid, m in milestones.items():
        visit(mid)
        local_file(root, m["file"])
        if m["route"] not in config["routes"]:
            raise BundleError(f"Unknown route for {mid}")
        if len(m["suggested_references"]) > limits["max_references"]:
            raise BundleError(f"Too many references: {mid}")
        for ref in m["suggested_references"]:
            if chars(root, ref) > limits["reference_max_chars"]:
                raise BundleError(f"Reference too large: {ref}")
    skills = set()
    for route, entry in config["routes"].items():
        if entry["default_milestone"] and entry["default_milestone"] not in milestones:
            raise BundleError(f"Unknown default milestone: {route}")
        if len(entry["references"]) > limits["max_references"]:
            raise BundleError(f"Too many route references: {route}")
        for ref in entry["references"]:
            if chars(root, ref) > limits["reference_max_chars"]:
                raise BundleError(f"Reference too large: {ref}")
        if entry["skill"]:
            p = local_file(root, entry["skill"])
            text = p.read_text(encoding="utf-8")
            parts = text.split("---", 2)
            if len(parts) != 3 or parts[0].strip():
                raise BundleError(f"Missing skill frontmatter: {p}")
            name = re.search(r"^name:\s*([a-z0-9-]+)\s*$", parts[1], re.M)
            desc = re.search(r"^description:\s*\S.+$", parts[1], re.M)
            if not name or name.group(1) != p.parent.name or not desc:
                raise BundleError(f"Invalid skill: {p}")
            skills.add(entry["skill"])
    registered_skills = check_skill_registry(root, skills, config.get("optional_skills", []))
    largest = ("", 0)
    for route in config["routes"]:
        combinations = [None] if route == "resume" else [None, *milestones]
        for mid in combinations:
            total = sum(chars(root, p) for p in select_files(config, milestones, route, mid))
            if total > limits["route_max_chars"]:
                raise BundleError(f"Context too large: {route}/{mid}: {total}")
            if total > largest[1]:
                largest = (f"{route}/{mid or '-'}", total)
    progress = read_json(root, config["progress_file"])
    mids = [m["id"] for m in progress["milestones"]]
    if len(mids) != len(set(mids)) or set(mids) != expected:
        raise BundleError("Progress IDs are inconsistent")
    for item in progress["milestones"]:
        if item["implementation"] not in {"NOT_STARTED", "IN_PROGRESS", "IMPLEMENTED"}:
            raise BundleError(f"Invalid implementation status: {item['id']}")
        if item["verification"] not in {"NOT_RUN", "PASS", "FAIL", "BLOCKED"}:
            raise BundleError(f"Invalid verification status: {item['id']}")
        if item["verification"] == "PASS" and not item["evidence"]:
            raise BundleError(f"PASS without evidence references: {item['id']}")
    json_count = 1
    product_scope_count = 0
    product = config.get("product_workflow", {})
    if "capability_catalog" in product:
        catalog_tool = local_file(root, "scripts/product_catalog.py")
        module_spec = importlib.util.spec_from_file_location("eeas_product_scope", catalog_tool)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        catalog = module.read_catalog(root, product["capability_catalog"])
        if catalog["user_max_children"] != product["max_child_agents"]:
            raise BundleError("Product scope and workflow agent limits disagree")
        local_file(root, product["interface_contract"])
        product_scope_count = len(catalog["capabilities"])
        json_count += 1
    for folder in ("profiles", "templates", "state", "milestones"):
        for p in (root / folder).glob("*.json"):
            read_json(root, p.relative_to(root).as_posix())
            json_count += 1
    entries = 0
    if manifest:
        seen = set()
        for line in local_file(root, "FILE_MANIFEST.sha256").read_text().splitlines():
            if not line.strip():
                continue
            digest, name = line.split("  ", 1)
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or name in seen:
                raise BundleError(f"Invalid manifest entry: {name}")
            seen.add(name)
            if hashlib.sha256(local_file(root, name).read_bytes()).hexdigest() != digest:
                raise BundleError(f"Checksum mismatch: {name}")
            entries += 1
    return {"result": "PASS_INSTRUCTION_BUNDLE_ONLY", "milestones": len(milestones),
            "skills": len(skills), "json_files_checked": json_count,
            "optional_skills": len(registered_skills - skills),
            "product_scope_count": product_scope_count,
            "baseline_chars": baseline, "largest_route": largest[0],
            "largest_route_chars": largest[1], "manifest_entries_checked": entries,
            "application_tested": False, "live_agent_tested": False,
            "config": config, "milestone_map": milestones}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", action="store_true", help="Check original delivered hashes")
    parser.add_argument("--route", help="List fixed context paths, not file contents")
    parser.add_argument("--milestone", help="Current milestone, e.g. E01")
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        result = check_bundle(root, args.manifest)
        config, milestones = result.pop("config"), result.pop("milestone_map")
        if args.route:
            files = select_files(config, milestones, args.route, args.milestone)
            print("FIXED FILES TO READ; add only necessary source/log excerpts:")
            for p in files:
                print(f"{chars(root, p):5d} chars  {p}")
            print(f"Total: {sum(chars(root, p) for p in files)} characters (not tokens)")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
