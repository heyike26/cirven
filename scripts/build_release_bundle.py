"""Build a versioned DEVELOPMENT_PREVIEW from actual tracked source and exact wheels."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from bundle_support import (MANIFEST, SCHEMA, MAX_MEMBER, assert_no_links, canonical_json,
                            digest_file, read_json, safe_name, validate_bundle, validate_names)
from install_offline import verify
from offline_test_support import verify_test_kit
from install_pptx_dependencies import selected_bundle

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = {"apps", "packages", "scripts", "tests", ".claude", "contracts", "milestones",
           "state", "docs", "profiles", "handoff", "specs"}
ROOT_FILES = {"AGENTS.md", "CLAUDE.md", "ROUTER.md", "README.md", "pyproject.toml",
              "requirements.lock", "requirements-core.lock", "requirements-runtime.lock",
              "install-windows.cmd", "start-windows.cmd", "stop-windows.cmd", "verify-windows.cmd"}
REQUIRED = {"apps/api/main.py", "apps/web/index.html", "packages/domain/store.py",
            "scripts/install_offline.py", "scripts/validate_bundle.py", "scripts/bundle_support.py",
            "scripts/portable_acceptance.py", "scripts/snapshot_data.py", "profiles/stack.lock.json"}


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout


def source_files(root):
    names = git(root, "ls-files", "-z").decode("utf-8").split("\0")
    result = {name: root / name for name in names if name and
              (name.split("/")[0] in FOLDERS or name in ROOT_FILES)}
    if not REQUIRED <= set(result):
        raise ValueError("Commit/stage the release implementation first; missing required source: " + str(sorted(REQUIRED - set(result))))
    return result


def office_materials(root, target, runtime_versions):
    """Exact approved members, both small source-test wheel closures, no caches.

    Only the host target is installed. Supplemental platform bytes are supply
    material for the original two-platform source tests, never native qualification.
    """
    if not (root / 'packages/domain/pptx_import.py').is_file():
        return {}, {}, None
    paths, metadata = {}, {'versions': {}, 'manifests': {}}
    expected = {name.replace('_', '-').lower(): version for name, version in runtime_versions.items()}
    _, _, selection = selected_bundle(root, target)
    if set(selection['targets']) - {'linux-py310', 'windows-py310'}:
        raise ValueError('Unsupported PPTX office dependency target')
    for platform in sorted(selection['targets']):
        directory, manifest, _ = selected_bundle(root, platform)
        relative = safe_name(selection['targets'][platform]['root'])
        names = {'manifest.json', 'requirements.lock'}
        versions = {}
        for package in manifest['packages']:
            name = package['name'].replace('_', '-').lower()
            if name in expected and expected[name] != package['version']:
                raise ValueError('Office/runtime pinned version conflict: ' + name)
            versions[name] = package['version']
            names.add('wheels/' + package['wheel']['filename'])
            names.update(item['path'] for item in package['licenses'])
            names.add(package['wheel_metadata']['path'])
        for name in names:
            name = safe_name(name); path = directory / name; assert_no_links(path)
            paths[relative + '/' + name] = path
        metadata['versions'][platform] = versions
        metadata['manifests'][relative + '/manifest.json'] = digest_file(directory / 'manifest.json')
    paths['vendor/office-pptx/selection.json'] = root / 'vendor/office-pptx/selection.json'
    metadata.update({'selected_target': target, 'source_manifest': selection['component_source'],
                     'source_manifest_sha256': selection['component_source_sha256'],
                     'supplemental_platform_scope': 'Original source-test materials; only matching host target is installed',
                     'native_windows': 'NOT_RUN'})
    return paths, metadata, selection


def wheel_licenses(directory, prefix, namespace="release/licenses", include_bundled_cli=True):
    additions, packages = {}, []
    for wheel in sorted((directory / "wheels").glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            metadata = [info for info in archive.infolist() if info.filename.endswith(".dist-info/METADATA")]
            if len(metadata) != 1 or metadata[0].file_size > 2 * 1024 * 1024:
                raise ValueError("Wheel metadata is missing or oversized")
            text = archive.read(metadata[0])
            details = BytesParser().parsebytes(text, headersonly=True)
            name, version = details["Name"], details["Version"]
            if not name or not version:
                raise ValueError("Wheel name/version is missing")
            directory_name = metadata[0].filename.split("/")[0]
            base = f"{namespace}/{directory_name}/"
            additions[base + "METADATA.txt"] = text
            license_paths = []
            for info in archive.infolist():
                relative = info.filename
                if (not info.is_dir() and relative.startswith(directory_name + "/") and
                        ("/licenses/" in relative or relative.rsplit("/", 1)[-1].upper().startswith(("LICENSE", "COPYING", "NOTICE")))):
                    safe_name(relative)
                    if info.file_size > 2 * 1024 * 1024:
                        raise ValueError("Wheel license is oversized")
                    output = base + relative.split("/", 1)[1]
                    additions[output] = archive.read(info)
                    license_paths.append(output)
            packages.append({"name": name, "version": version, "wheel": prefix + "/wheels/" + wheel.name,
                             "wheel_sha256": digest_file(wheel), "source": f"https://pypi.org/project/{name}/{version}/",
                             "license_declared": details.get("License-Expression") or details.get("License"),
                             "license_classifiers": [x for x in details.get_all("Classifier", []) if x.startswith("License")],
                             "license_material": license_paths, "metadata": base + "METADATA.txt",
                             "redistribution_review": "NOT_DETERMINED" if name.lower() == "claude-agent-sdk" else "DECLARED_LICENSE_MATERIAL_INCLUDED"})
    inventory = {"packages": packages, "scope": "runtime" if include_bundled_cli else "optional-test-only"}
    if include_bundled_cli:
        inventory["bundled_cli"] = {
                        "component": "Claude Code inside the official claude-agent-sdk wheel",
                        "sdk_license_does_not_certify_cli_license": True,
                        "terms_source": "https://github.com/anthropics/claude-code/blob/main/LICENSE.md",
                        "public_license_text": "docs/third-party/claude-code-LICENSE.md",
                        "public_license_provenance": "docs/third-party/claude-code-license-source.json",
                        "license_status": "COMMERCIAL_TERMS_REVIEW_REQUIRED",
                        "installation": "Bundled native executable; no npm downloads on deployment",
                        "scope": "Development preview; company distribution/use qualification remains open"}
    additions[namespace + "/packages.json"] = canonical_json(inventory)
    return additions


def build(root: Path, target: str, wheel_root: Path, evidence: list[str], output: Path | None = None,
          evidence_root: Path | None = None, test_root: Path | None = None) -> dict:
    if target not in {"windows-py310", "linux-py310"}:
        raise ValueError("Unsupported release target")
    root, wheel_root = root.resolve(), wheel_root.resolve()
    evidence_root = (evidence_root or root).resolve()
    selection = read_json((root / "profiles/offline-dependencies.json").read_bytes())
    relative = safe_name(selection["targets"][target])
    directory = wheel_root / relative
    dependency_manifest = verify(directory, target)
    assert_no_links(directory)
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    status = git(root, "status", "--porcelain=v1", "-z").decode()
    paths = source_files(root)
    for path in directory.rglob("*"):
        assert_no_links(path)
        if path.is_file():
            paths[relative + "/" + path.relative_to(directory).as_posix()] = path
    selected = []
    for name in evidence:
        name = safe_name(name)
        if not name.startswith("reports/"):
            raise ValueError("Evidence must be an original report under reports/")
        source = evidence_root / name
        assert_no_links(source)
        if not source.is_dir() or not (source / "freeze.json").is_file() or not (source / "result.json").is_file():
            raise ValueError("Evidence requires original freeze.json and result.json")
        freeze = read_json((source / "freeze.json").read_bytes())
        report = {"path": name, "git_base": freeze.get("git_base"),
                         "freeze_sha256": digest_file(source / "freeze.json"),
                         "result": read_json((source / "result.json").read_bytes()),
                         "excluded_runtime_files": [],
                         "scope": "Original historical slice; use its frozen source/command/acceptance, not final-source PASS"}
        selected.append(report)
        for path in source.rglob("*"):
            assert_no_links(path)
            if path.is_file():
                local = path.relative_to(source)
                lowered = [part.casefold() for part in local.parts]
                if (any(part in {"original-data", "restored-data", "empty-user-home", "empty-claude-config"} for part in lowered)
                        or path.name.casefold() == "local-access.token"
                        or path.name.casefold().endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm"))):
                    report["excluded_runtime_files"].append({"path": local.as_posix(),
                        "reason": "Runtime data/credentials are not release evidence; retain original metadata and raw test logs"})
                    continue
                paths[name + "/" + local.as_posix()] = path
    if not selected:
        raise ValueError("At least one original evidence report is required")
    additions = wheel_licenses(directory, relative)
    office_paths, office_metadata, office_selection = office_materials(root, target, dependency_manifest['runtime'])
    paths.update(office_paths)
    if office_selection:
        for platform, selected_office in office_selection['targets'].items():
            office_relative = safe_name(selected_office['root'])
            additions.update(wheel_licenses(root / office_relative, office_relative,
                'release/licenses/office/' + platform, False))
        additions['release/office-component.json'] = canonical_json(office_metadata)
    test_manifest = None
    test_relative = None
    test_selection = root / "profiles/offline-test-dependencies.json"
    if test_selection.is_file():
        test_relative = safe_name(read_json(test_selection.read_bytes())["targets"][target])
        test_source = (test_root or root) / test_relative
        test_manifest = verify_test_kit(test_source, target)
        for path in test_source.rglob("*"):
            assert_no_links(path)
            if path.is_file():
                paths[test_relative + "/" + path.relative_to(test_source).as_posix()] = path
        additions.update(wheel_licenses(test_source, test_relative, "release/licenses/tests", False))
        for path in (test_source / "licenses").rglob("*"):
            if path.is_file():
                additions["release/licenses/test-artifacts/" + path.relative_to(test_source / "licenses").as_posix()] = path.read_bytes()
        paths.pop("profiles/offline-test-dependencies.json", None)
        additions["profiles/offline-test-dependencies.json"] = canonical_json({
            "schema_version": "eeas.offline-test-selection.v1", "targets": {target: test_relative},
            "optional": True, "install_entry": "scripts/install_test_dependencies.py", "native_windows": "BLOCKED"})
    elif (root / "scripts/install_test_dependencies.py").is_file():
        raise ValueError("Prepare the optional test-kit selection before packaging the real application")
    # Overrides only release-specific selection: the other OS wheelhouse is deliberately absent.
    additions["profiles/offline-dependencies.json"] = canonical_json({"schema_version": "eeas.offline-selection.v1",
        "targets": {target: relative}, "release_status": "DEVELOPMENT_PREVIEW", "native_windows": "NOT_VALIDATED"})
    paths.pop("profiles/offline-dependencies.json", None)
    # A package cannot contain its own final archive digest. Replace any old
    # checkout delivery pointer with this package's actual source/target scope;
    # recipients obtain the archive digest from the independent trusted record.
    paths.pop("state/DELIVERY.json", None)
    additions["state/DELIVERY.json"] = canonical_json({
        "schema_version": "eeas.packaged-delivery.v1", "source_commit": commit,
        "target": target, "release_status": "DEVELOPMENT_PREVIEW",
        "manifest": MANIFEST, "combination_lock": "release/combination-lock.json",
        "evidence_index": "release/evidence-index.json",
        "archive_digest": "Use the separately trusted external delivery record or .zip.sha256; no self-referential digest",
        "native_windows": "BLOCKED", "live_model": "BLOCKED", "os_isolation": "NOT_VALIDATED"})
    additions["release/evidence-index.json"] = canonical_json({"reports": selected,
        "final_source_validation": "Use the separately recorded post-integration acceptance; historical results do not certify changed code"})
    additions["release/prerequisites.json"] = canonical_json({"target": target, "architecture": "x86_64", "python": "3.10",
        "python_distribution": "Preinstall company-approved CPython3.10 x86_64; Python itself is not bundled",
        "runtime_libraries": "Native host/runtime prerequisite must be checked by portable_acceptance.py on the target",
        "npm": "NOT_REQUIRED", "model": "NOT_CONFIGURED without separate company endpoint/credentials/compatibility evidence",
        "browser": "Human UI uses approved local browser; optional pytest/Playwright/headless-shell kit is bundled and installed separately; no install-time downloads",
        "license": "Official wheel license texts included; bundled Claude Code commercial terms qualification remains open"})
    additions["release/README.md"] = ("# EEAS DEVELOPMENT_PREVIEW\n\n"
        "目标：" + target + "，Python3.10 x86_64。Windows原生/真实模型/OS隔离门禁单独保留，WSL结果不能替代。\n\n"
        "本包源提交与目标见 state/DELIVERY.json、BUNDLE_MANIFEST.json；包的SHA256来自外部可信交付记录，包内不引用旧交付。\n\n"
        "先将交付记录中的SHA256交给包外可信的 scripts/validate_bundle.py：\n"
        "`py -3.10 validate_bundle.py PACKAGE.zip --sha256 DIGEST --destination 新目录`\n"
        "校验器依赖同目录 bundle_support.py；这两个文件须来自可信交付副本。\n\n"
        "在解包目录执行 `py -3.10 scripts/portable_acceptance.py --output 新的验收目录`，"
        "它创建新的.venv、仅使用随包wheel、执行原创API/SQLite/SDK基础验收与快照恢复；不调用模型。\n"
        "然后 `start-windows.cmd`、`stop-windows.cmd`；诊断 `.venv\\Scripts\\python.exe -m apps.api diagnose`。\n"
        "停机备份：`.venv\\Scripts\\python.exe scripts/snapshot_data.py backup --data var --output 新备份.zip`。\n"
        "恢复：`... snapshot_data.py restore --archive 备份.zip --sha256 单独保管的摘要 --destination 新数据目录`。\n"
        "设置 EEAS_DATA_DIR 指向新恢复目录；不要用旧代码打开含新迁移的数据库，回滚使用匹配代码与验证过的快照。\n\n"
        "可选离线测试另建环境与固定浏览器cache：`py -3.10 scripts/install_test_dependencies.py`。\n"
        "安装器不联网、不修改应用.venv；然后执行 `.offline-probe\\test-env\\run-tests.cmd`，"
        "或 PowerShell `& .\\.offline-probe\\test-env\\run-tests.ps1`。已有目标必须换新目录，"
        "使用 `--destination` 和 `--browser-cache` 指定。Linux按安装回执 EEAS_TEST_ENV.json 设置环境变量后执行其Python的 `-m pytest`。\n"
        "随包只含固定revision的headless-shell；人工界面使用公司批准的浏览器，视频功能因未带FFmpeg不验收。\n\n"
        "备份包含项目原始资料，应仅在获准的内网位置保管；个人凭据、访问token和开发缓存不打包。\n").encode("utf-8")
    validate_names([*paths, *additions, MANIFEST])
    source_hashes = {}
    for name, path in paths.items():
        assert_no_links(path)
        if not path.is_file() or path.stat().st_size > MAX_MEMBER:
            raise ValueError("Missing/oversized source or asset: " + name)
        source_hashes[name] = digest_file(path)
    locks = {name: source_hashes[name] for name in source_hashes
             if name.endswith(".lock") or name == "profiles/stack.lock.json" or name == relative + "/manifest.json"
             or test_relative and name == test_relative + "/manifest.json"}
    if office_selection:
        locks.update(office_metadata['manifests'])
        locks['vendor/office-pptx/selection.json'] = source_hashes['vendor/office-pptx/selection.json']
    source_only = {name: digest for name, digest in source_hashes.items() if not name.startswith(("vendor/", "reports/"))}
    combination = {"schema_version": "eeas.release-combination.v1", "source_commit": commit,
                   "source_snapshot_sha256": hashlib.sha256(canonical_json(source_only)).hexdigest(),
                   "target": target, "python": "3.10", "dependency_locks_sha256": locks,
                   "runtime_versions": dependency_manifest["runtime"],
                   "optional_test_versions": test_manifest["versions"] if test_manifest else {},
                   "office_component": office_metadata,
                   "headless_shell_revision": test_manifest["browser"]["revision"] if test_manifest else None,
                   "stack_lock_note": "Hash binds actual file; historical design eeas_commit field is not the delivered source commit"}
    additions["release/combination-lock.json"] = canonical_json(combination)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = output or root / "artifacts" / f"eeas-preview-{target}-{stamp}.zip"
    validator_directory = output.with_suffix(".verifier")
    assert_no_links(output)
    assert_no_links(validator_directory)
    if os.path.lexists(output) or os.path.lexists(output.with_suffix(".zip.sha256")) or os.path.lexists(validator_directory):
        raise ValueError("Output exists; historical packages are immutable")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": SCHEMA, "release_status": "DEVELOPMENT_PREVIEW", "target": target,
                "created_at": datetime.now(timezone.utc).isoformat(), "source_commit": commit,
                "source_snapshot_sha256": combination["source_snapshot_sha256"],
                "source_worktree_dirty": bool(status), "source_git_status": status.split("\0")[:-1],
                "gates": {"native_windows": "BLOCKED", "live_agent": "BLOCKED", "os_isolation": "NOT_VALIDATED",
                          "bundled_cli_license": "NOT_DETERMINED", "intranet": "NOT_VALIDATED"}, "files": {}}
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(paths.items()):
            # One frozen byte sequence supplies both ZIP and manifest; later compare live files.
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != source_hashes[name]:
                raise ValueError("Source changed while packaging: " + name)
            archive.writestr(name, data)
            manifest["files"][name] = {"sha256": digest, "bytes": len(data)}
        for name, data in sorted(additions.items()):
            archive.writestr(name, data)
            manifest["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        if git(root, "rev-parse", "HEAD").decode().strip() != commit or git(root, "status", "--porcelain=v1", "-z").decode() != status:
            raise ValueError("Git source changed during packaging")
        if any(digest_file(path) != source_hashes[name] for name, path in paths.items()):
            raise ValueError("Source changed during packaging")
        archive.writestr(MANIFEST, canonical_json(manifest))
    digest = digest_file(output)
    validate_bundle(output, digest)
    with output.with_suffix(".zip.sha256").open("x", encoding="utf-8") as stream:
        stream.write(digest + "  " + output.name + "\n")
    # Ship separately: recipients can validate without executing/importing ZIP content.
    validator_directory.mkdir(mode=0o700, exist_ok=False)
    validators = {}
    for filename in ("validate_bundle.py", "bundle_support.py"):
        data = (root / "scripts" / filename).read_bytes()
        path = validator_directory / filename
        with path.open("xb") as stream:
            stream.write(data)
        validators[filename] = {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}
    with (validator_directory / "VALIDATOR_FILES.json").open("xb") as stream:
        stream.write(canonical_json({"source_commit": commit, "files": validators,
                                     "trust": "Confirm these hashes from the trusted delivery record before use"}))
    return {"path": str(output), "sha256": digest, "bytes": output.stat().st_size,
            "source_commit": commit, "source_snapshot_sha256": manifest["source_snapshot_sha256"],
            "release_status": manifest["release_status"], "target": target,
            "wheel_count": len(list((directory / "wheels").glob("*.whl"))), "evidence": selected,
            "standalone_validators": validators,
            "optional_test_wheels": len(test_manifest["versions"]) if test_manifest else 0,
            "office_wheels": sum(len(v) for v in office_metadata.get('versions', {}).values()) if office_selection else 0,
            "headless_shell_revision": test_manifest["browser"]["revision"] if test_manifest else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["windows-py310", "linux-py310"], default="windows-py310")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--wheel-root", type=Path, help="Read-only dependency repository root")
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--test-root", type=Path, help="Read-only optional test-kit repository root")
    parser.add_argument("--evidence", action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = build(args.root, args.target, args.wheel_root or args.root, args.evidence,
                       args.output, args.evidence_root, args.test_root)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"Release build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
