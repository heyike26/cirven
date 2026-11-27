"""Package only reviewed source/lock/local-file inputs missing from a base ZIP.

No package manager, build, network, Git, App or runtime-tree operation.  The
reviewed plan supplies every allowed byte; the base/runtime archive hashes are
prior Root facts, while selected members and embedded manifests are read here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import zipfile


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def name(value):
    require(isinstance(value, str) and value and "\\" not in value, "Invalid relative source path")
    p = PurePosixPath(value)
    require(not p.is_absolute() and str(p) == value, "Non-canonical source path")
    for part in p.parts:
        require(part not in {".", ".."} and part.rstrip(" .") == part
                and not any(ord(c) < 32 or c in ':<>"|?*' for c in part), "Unsafe source path")
        stem = part.split(".", 1)[0].upper()
        require(stem not in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                            *[f"LPT{i}" for i in range(1, 10)]}, "Reserved Windows path")
    return value


def no_links(path, boundary):
    require(path.is_relative_to(boundary), "Source escaped its pinned root")
    for p in (path, *path.parents):
        s = p.lstat()
        require(not stat.S_ISLNK(s.st_mode) and not getattr(s, "st_file_attributes", 0) & 0x400,
                "Unexpected linked/reparse input")
        if p == boundary:
            break


def same_bytes(raw, row):
    require(len(raw) == row["bytes"] and sha(raw) == row["sha256"], "Frozen input bytes changed: " + row["path"])
    return raw


def archive_index(archive):
    rows = archive.infolist()
    require(all(not r.is_dir() and not r.flag_bits & 1 for r in rows), "Unexpected directory/encrypted ZIP entry")
    require(len(rows) == len({name(r.filename).casefold() for r in rows}), "Duplicate/case-colliding ZIP entries")
    require(all(not stat.S_ISLNK(r.external_attr >> 16) for r in rows), "ZIP links are forbidden")
    return {r.filename: r for r in rows}


def stamp(path):
    s = path.stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]



def checked_output_path(value, *, root, inputs):
    require(value.is_absolute() and ".." not in value.parts, "Output must be absolute without parent traversal")
    original = value.absolute()
    require(original.parent.is_dir(), "Output parent must already exist")
    no_links(original.parent, Path(original.anchor))
    resolved = original.resolve(strict=False)
    require(resolved.parent.is_dir() and not os.path.lexists(resolved), "Output must be a new file")
    no_links(resolved.parent, Path(resolved.anchor))
    on_d = resolved.drive.upper() == "D:" if os.name == "nt" else resolved.is_relative_to(Path("/mnt/d"))
    require(on_d, "Resolved output must remain on D")
    require(not resolved.is_relative_to(root.resolve()) and resolved not in {p.resolve() for p in inputs}, "Output overlaps inputs")
    return resolved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--base-source", type=Path)
    parser.add_argument("--runtime-archive", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--inspect", action="store_true", help="Verify the plan/input bytes; create no files")
    args_list = sys.argv[1:] if argv is None else argv
    if not args_list:
        parser.print_help()
        return 0
    args = parser.parse_args(args_list)
    for field in ("source_root", "base_source", "runtime_archive", "plan", "expected_plan_sha256"):
        require(getattr(args, field) is not None, "Missing --" + field.replace("_", "-"))
    require(args.inspect or args.output is not None and args.receipt is not None, "Supply --inspect or new output and receipt")
    plan_raw = args.plan.read_bytes()
    require(sha(plan_raw) == args.expected_plan_sha256, "Exact reviewed plan SHA required")
    plan = json.loads(plan_raw)
    require(plan["schema"] == "cirven.source35.source-build-supplement-plan.v1", "Wrong plan schema")
    root = args.source_root.absolute()
    require(str(root) == plan["source_root_expected"], "Exact reviewed source root required")
    no_links(root, Path(root.anchor))
    base = args.base_source.absolute()
    runtime = args.runtime_archive.absolute()
    require(str(base) == plan["base_source_archive"]["path"] and base.stat().st_size == plan["base_source_archive"]["bytes"], "Base source archive identity differs")
    require(str(runtime) == plan["runtime_metadata_archive"]["path"] and runtime.stat().st_size == plan["runtime_metadata_archive"]["bytes"], "Runtime metadata archive identity differs")
    no_links(base, Path(base.anchor))
    no_links(runtime, Path(runtime.anchor))
    stamps = {str(p): stamp(p) for p in (args.plan, base, runtime)}
    prefix = plan["logical_prefix"]
    require(prefix == "Cirven-Source35-source/", "Exact source namespace required")
    missing, reused = plan["payload_missing_only"], plan["existing_base_reused"]
    names = [name(r["path"]) for r in [*missing, *reused]]
    require(len(names) == len({n.casefold() for n in names}), "Duplicate source paths")
    require(len(missing) == plan["supplement_payload_files"] and sum(r["bytes"] for r in missing) == plan["supplement_payload_bytes"], "Plan counts differ")
    license_ex = set(plan["Root_authorized_exceptions"]["license_text_paths_containing_node_modules"])
    ghostty_ex = set(plan["Root_authorized_exceptions"]["ghostty_fixed_third_party_dist"]["paths"])
    for row in missing:
        n = row["path"]
        require(not n.startswith(("runtime/", "apps/agent-shell/dist/", "user-data/")), "Excluded payload namespace")
        require(not {".venv", ".git", ".cache"}.intersection(PurePosixPath(n).parts), "Excluded payload tree")
        require("node_modules" not in PurePosixPath(n).parts or n in license_ex and row["bytes"] < 65536, "Only exact pinned license notices may contain node_modules in their path")
        require("/dist/" not in n or n in ghostty_ex, "Only fixed third-party Ghostty dist is allowed")
    with zipfile.ZipFile(base) as bz, zipfile.ZipFile(runtime) as rz:
        base_index = archive_index(bz)
        fixed = plan["base_frozen_source_manifest"]
        require(sha(bz.read(fixed["member"])) == fixed["sha256"], "Base frozen manifest changed")
        require(sha(rz.read(plan["runtime_metadata_archive"]["package_manifest_member"])) == plan["runtime_metadata_archive"]["package_manifest_sha256"], "Runtime manifest changed")
        require(not any(prefix + r["path"] in base_index for r in missing), "Supplement cannot overwrite base members")

        def read_input(row):
            source = row["source"]
            if source["kind"] == "runtime_zip_member":
                raw = rz.read(source["member"])
            elif source["kind"] == "frozen_metadata_file":
                f = Path(source["path"])
                no_links(f, Path(f.anchor))
                raw = f.read_bytes()
            else:
                require(source["kind"] == "source_root_file" and name(source["path"]) == row["path"], "Unsupported source producer")
                f, boundary = root / source["path"], root
                for link in plan["source_symlink_roots"]:
                    if source["path"].startswith(link["logical"] + "/"):
                        logical = root / link["logical"]
                        require(logical.is_symlink() and logical.resolve() == Path(link["target"]), "Pinned existing source producer link changed")
                        boundary = Path(link["target"])
                        f = boundary / source["path"][len(link["logical"]) + 1:]
                        break
                no_links(f, boundary)
                raw = f.read_bytes()
            return same_bytes(raw, row)

        def check_all():
            for row in reused:
                same_bytes(bz.read(prefix + row["path"]), row)
            for row in missing:
                read_input(row)
            require(args.plan.read_bytes() == plan_raw and all(stamp(Path(p)) == s for p, s in stamps.items()), "Input archive/plan changed")

        check_all()
        if args.inspect:
            print(json.dumps({"status": "STATIC_INPUTS_VERIFIED_NO_OUTPUT", "payload_files": len(missing), "payload_bytes": plan["supplement_payload_bytes"], "base_reused": len(reused), "plan_sha256": sha(plan_raw)}))
            return 0
        input_paths = {base, runtime, args.plan.absolute()}
        output = checked_output_path(args.output, root=root, inputs=input_paths)
        receipt = checked_output_path(args.receipt, root=root, inputs=input_paths)
        require(output != receipt, "Distinct output and receipt required")
        public_rows = lambda rows: [{k: r[k] for k in ("path", "bytes", "sha256")} for r in rows]
        manifest = {"schema": "cirven.source35.source-build-supplement.v1", "status": "STATIC_SOURCE_LOCK_LOCAL_FILE_INPUTS_PACKAGED_NO_NPM_BUILD", "plan_sha256": sha(plan_raw), "logical_prefix": prefix, "base_source_archive": {**plan["base_source_archive"], "path": base.name}, "files": public_rows(missing), "base_reused": public_rows(reused), "checks": plan["checks"], "Root_authorized_exceptions": plan["Root_authorized_exceptions"], "WASM_provenance": "Existing manifest-pinned input; not rebuilt in this round", "registry_dependency_scope": plan["qualification"], "first_stage_complete": False}
        manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        generated = name(plan["generated_manifest_member"])
        require(generated.startswith(prefix) and generated not in base_index and generated not in {prefix + r["path"] for r in missing}, "Generated manifest collides")
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as oz:
            for member, raw in [(prefix + r["path"], read_input(r)) for r in missing] + [(generated, manifest_raw)]:
                info = zipfile.ZipInfo(member, (2026, 10, 24, 0, 0, 0))
                info.create_system, info.external_attr, info.compress_type = 3, 0o100644 << 16, zipfile.ZIP_DEFLATED
                oz.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        with output.open("r+b") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        check_all()
    with zipfile.ZipFile(output) as oz:
        actual = archive_index(oz)
        require(set(actual) == {prefix + r["path"] for r in missing} | {generated}, "Output membership differs")
        for row in missing:
            same_bytes(oz.read(prefix + row["path"]), row)
        require(oz.read(generated) == manifest_raw, "Generated manifest differs")
    result = {"status": "PASS_SOURCE_BUILD_SUPPLEMENT_BYTES_MEMBERS_CRC_AND_INPUTS_UNCHANGED_NO_NPM_BUILD", "plan_sha256": sha(plan_raw), "archive": {"path": str(output), "bytes": output.stat().st_size, "sha256": sha(output.read_bytes())}, "payload_files": len(missing), "payload_bytes": plan["supplement_payload_bytes"], "base_reused_files": len(reused), "generated_manifest_member": generated, "generated_manifest_sha256": sha(manifest_raw), "all_payload_before_after_SHA": True, "all_selected_base_members_before_after_SHA": True, "all_output_members_SHA_and_CRC": True, "existing_archive_full_SHA_recomputed": False, "input_archive_identity_and_embedded_manifest_unchanged": True, "runtime_or_dependency_tree_copy": False, "npm_network_build_actions": 0, "first_stage_complete": False}
    with receipt.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        print("Source supplement not completed; preserve any new output for review: " + str(error), file=sys.stderr)
        raise SystemExit(1)
