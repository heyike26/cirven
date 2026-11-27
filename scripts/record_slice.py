"""Freeze original inputs, acceptance and code before executing a slice's tests."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def canonical_revision(code: dict[str, str], dependencies: dict[str, str]) -> str:
    """Identify the actual frozen bytes, independently of VCS and run metadata."""
    manifest = {"code_sha256": code, "dependency_sha256": dependencies}
    raw = json.dumps(manifest, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def source_paths(slice_id: str, references: list[Path]) -> list[Path]:
    paths = []
    for folder in ("src", "apps", "packages", "scripts", "tests"):
        paths.extend(path for path in (ROOT / folder).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts)
    paths.extend(ROOT / name for name in (
        "pyproject.toml", "requirements.lock", "requirements-core.lock", "requirements-runtime.lock",
        "install-windows.cmd", "start-windows.cmd", "stop-windows.cmd", "verify-windows.cmd",
        "AGENTS.md", "ROUTER.md", "CLAUDE.md",
        "profiles/environment.json", "profiles/stack.lock.json",
        f"milestones/{slice_id.split('-')[0]}.md") if (ROOT / name).is_file())
    paths.extend(references)
    return sorted(set(paths))


def dependency_paths() -> list[Path]:
    return sorted(path for path in (ROOT / "toolchain").rglob("*")
                  if path.is_file() and "__pycache__" not in path.parts)


def current_manifest(paths: list[Path]) -> tuple[dict[str, str], list[str]]:
    hashes, unreadable = {}, []
    for path in paths:
        name = path.relative_to(ROOT).as_posix()
        try:
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            unreadable.append(name)
    return hashes, unreadable


def source_state(slice_id: str, references: list[Path], code: dict[str, str],
                 dependencies: dict[str, str]) -> dict[str, object]:
    """A changed/deleted/new input is an observation, never a rewritten exit."""
    current_code, code_errors = current_manifest(source_paths(slice_id, references))
    current_dependencies, dependency_errors = current_manifest(dependency_paths())
    return {"stable": not code_errors and not dependency_errors and
            current_code == code and current_dependencies == dependencies,
            "source_revision": canonical_revision(current_code, current_dependencies),
            "unreadable_paths": sorted(set(code_errors + dependency_errors))}


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def split_cli(argv: list[str]) -> tuple[list[str], list[str]]:
    """Read only the recorder prefix; command options are opaque after dispatch.

    argparse.REMAINDER at the slice positional consumes the recorder's own
    options. Parsing the entire command with parse_known_args would instead
    steal its --acceptance/--reference/-h options. Stop before the executable.
    """
    prefix, index, has_slice = [], 0, False
    acceptance_before_slice = False
    options = {"--acceptance": True, "--reference": True,
               "--snapshot-dependencies": False, "--help": False}
    while index < len(argv):
        # Preserve the full legacy tail, including executable names resembling
        # recorder options and later command-owned --. Slice-first ordering is
        # unambiguous because it has not already supplied --acceptance.
        if has_slice and acceptance_before_slice:
            return prefix, argv[index + 1:] if argv[index] == "--" else argv[index:]
        token = argv[index]
        if token == "--":
            return prefix, argv[index + 1:]
        name = token.split("=", 1)[0]
        matches = [key for key in options if name.startswith("--") and key.startswith(name)]
        option = "--help" if name == "-h" else matches[0] if len(matches) == 1 else None
        if option:
            if option == "--acceptance" and not has_slice:
                acceptance_before_slice = True
            prefix.append(token)
            if options[option] and "=" not in token:
                # Leave missing/option-looking values to argparse's normal error.
                prefix.extend(argv[index + 1:index + 2])
                index += 1
        elif not has_slice:
            prefix.append(token)
            if not token.startswith("-"):
                has_slice = True
        else:
            return prefix, argv[index:]
        index += 1
    return prefix, []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slice")
    parser.add_argument("--acceptance", required=True)
    parser.add_argument("--reference", action="append", default=[])
    parser.add_argument("--snapshot-dependencies", action="store_true",
                        help="also copy hashed toolchain files; default snapshots source only")
    prefix, command = split_cli(sys.argv[1:])
    args = parser.parse_args(prefix)
    if not command:
        parser.error("a test command is required after --")
    if not args.slice.replace("-", "").replace("_", "").isalnum():
        parser.error("invalid slice id")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = ROOT / "reports" / args.slice / run_id
    target.mkdir(parents=True, exist_ok=False)
    references = []
    for name in args.reference:
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            parser.error("reference must be an existing file inside the repository")
        references.append(path)
    hashes = {}
    for path in source_paths(args.slice, references):
        relative = path.relative_to(ROOT)
        snapshot = target / "snapshot" / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, snapshot)
        hashes[relative.as_posix()] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    dependencies = {}
    for path in dependency_paths():
        relative = path.relative_to(ROOT)
        if args.snapshot_dependencies:
            snapshot = target / "snapshot" / relative
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            # Explicit references have already frozen this dependency's bytes.
            if relative.as_posix() not in hashes:
                shutil.copyfile(path, snapshot)
            data = snapshot.read_bytes()
        else:
            data = path.read_bytes()
        dependencies[relative.as_posix()] = hashlib.sha256(data).hexdigest()
    before = source_state(args.slice, references, hashes, dependencies)
    packages = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}
    freeze = {"schema_version": "eeas.slice-run.v1", "slice": args.slice,
              "run_id": run_id, "git_base": None, "source_control": "DIRECT_FILES",
              "source_revision": canonical_revision(hashes, dependencies), "code_sha256": hashes,
              "dependency_sha256": dependencies,
              "snapshot_scope": "SOURCE_AND_DEPENDENCIES" if args.snapshot_dependencies else "SOURCE_ONLY",
              "snapshot_self_contained": False,
              "source_stable_before_run": before["stable"],
              "source_revision_before_run": before["source_revision"],
              "source_unreadable_before_run": before["unreadable_paths"],
              "python": sys.version, "packages": packages, "command": command,
              "acceptance_before_run": args.acceptance,
              "fixtures": "Original test fixtures frozen under snapshot/tests"}
    write_json(target / "freeze.json", freeze)
    with (target / "stdout.txt").open("x") as stdout, (target / "stderr.txt").open("x") as stderr:
        result = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr, check=False,
                                env={**os.environ, "EEAS_EVIDENCE_DIR": str(target)})
    after = source_state(args.slice, references, hashes, dependencies)
    write_json(target / "result.json", {"exit_code": result.returncode,
                "status": "PASS" if result.returncode == 0 else "FAIL",
                "source_stable_after_run": after["stable"],
                "source_revision_after_run": after["source_revision"],
                "source_unreadable_after_run": after["unreadable_paths"],
                "finished_at": datetime.now(timezone.utc).isoformat()})
    print((target / "stdout.txt").read_text(), end="")
    print((target / "stderr.txt").read_text(), end="", file=sys.stderr)
    print(f"Evidence: {target.relative_to(ROOT)}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
