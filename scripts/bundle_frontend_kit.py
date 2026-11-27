"""Build/validate an offline frontend development kit; never run its contents."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import unicodedata
import zipfile

if __package__:
    from .experience_catalog import CatalogError, canonical, inspect_file, load_lessons, portable_relative, safe_source, strict_json, utc_now
else:
    from experience_catalog import CatalogError, canonical, inspect_file, load_lessons, portable_relative, safe_source, strict_json, utc_now

SCHEMA = "eeas.frontend-development-kit.v1"
MANIFEST = "FRONTEND_KIT_MANIFEST.json"
README = "FRONTEND_KIT_README.md"
NEW_SKILLS = (
    "animation-vocabulary", "baseline-ui", "break-ui", "design-motion-principles", "emil-design-eng",
    "find-animation-opportunities", "fixing-accessibility", "fixing-motion-performance", "gsap-timeline",
    "motion-design", "review-animations",
)
EXISTING_SKILLS = ("frontend-design", "impeccable", "webapp-testing")
OWNED_SKILL = "eeas-development-lessons"
SKILLS = NEW_SKILLS + EXISTING_SKILLS + (OWNED_SKILL,)
TREES = ("apps/web", "toolchain/ui-fonts/noto-serif-sc") + tuple(".claude/skills/" + name for name in SKILLS)
FILES = (
    "experience/README.md", "experience/lessons.jsonl", "scripts/experience_catalog.py", "tests/test_experience_catalog.py",
    "scripts/bundle_frontend_kit.py", "tests/test_bundle_frontend_kit.py", "scripts/record_slice.py",
    "tests/test_glass_peer_browser.py", "tests/test_glass_accessibility_peer.py", "tests/test_glass_lab_browser.py",
    "tests/test_navigation_autohide_browser.py", "tests/test_web.py", "tests/conftest.py", "profiles/development-skills.json",
    "tests/test_navigation_intent_race_browser.py", "tests/test_frontend_contract_migrations.py",
    "tests/test_ui_independent_crosscheck.py", "tests/test_task_multiline_browser.py",
    "reports/E07-independent-task-layout/microcases_schema_sync.py",
)
ESSENTIAL_ASSETS = (
    "apps/web/index.html", "apps/web/assets/app.js", "apps/web/assets/app.css", "apps/web/assets/glass-shell.css",
    "apps/web/assets/glass-components.css", "apps/web/assets/glass-motion.js", "apps/web/assets/navigation-shell.js",
    "apps/web/assets/glass-lab/index.html", "apps/web/assets/glass-lab/lab.js", "apps/web/assets/glass-lab/lab.css",
    "apps/web/assets/fonts/README.md", "apps/web/assets/fonts/sources.json", "apps/web/assets/fonts/manifest.json",
    "apps/web/assets/fonts/font-source-manifest-noto-serif-sc.json", "apps/web/assets/fonts/LICENSE-noto-serif-sc.txt",
    "apps/web/assets/fonts/generation/requirements.txt", "apps/web/assets/fonts/generation/tools-manifest.json",
    "toolchain/ui-fonts/noto-serif-sc/README.md", "toolchain/ui-fonts/noto-serif-sc/build.py",
    "toolchain/ui-fonts/noto-serif-sc/export_metadata.py", "toolchain/ui-fonts/noto-serif-sc/sources.json",
    "toolchain/ui-fonts/noto-serif-sc/manifest.json", "toolchain/ui-fonts/noto-serif-sc/upstream/OFL.txt",
    ".claude/skills/impeccable/scripts/bin/linux-x64/impeccable",
    ".claude/skills/impeccable/scripts/bin/windows-x64/impeccable.exe",
    ".claude/skills/impeccable/scripts/VERSION",
    ".claude/skills/impeccable/scripts/bin/OFFLINE_ENGINE_MANIFEST.json",
)
OPTIONAL_FILES = ("PRODUCT.md", "DESIGN.md", "docs/FRONTEND_EFFECTS.md", "docs/DESIGN_SKILL_ROLES.md")
REPORTS = (
    "E07-glass-design-research", "E07-glass-independent-first", "E07-glass-independent-fixed1",
    "E07-top-navigation-independent-first", "E07-top-navigation-three-fixes", "E07-editorial-navigation20-first",
    "E07-navigation-four-fixes", "E07-tablet-window-link-fix", "E07-navigation-reading-intent10",
    "E07-open-silver-browser92", "E07-offline-studio-fonts-build", "E07-offline-font-generation-tools",
    "E07-offline-font-generation-tools-recovery", "E07-offline-studio-serif",
    "E07-open-silver-browser92-fixed", "E07-navigation-race-and-topbar-before",
    "E07-navigation-restoration-final13", "E07-multiline-submit-migration", "E10-experience-frontend-tools20",
)
MAX_MEMBERS = 20000
MAX_FILE_BYTES = 96 * 1024 * 1024
MAX_TOTAL_BYTES = 320 * 1024 * 1024
MAX_RATIO = 200
SHA = re.compile(r"[0-9a-f]{64}\Z")
FORBIDDEN = {".git", ".venv", "node_modules", "__pycache__", ".eeas-sdk-state", ".cache"}

README_TEXT = """# EEAS 前端离线开发 kit

这是前端代码、字体原件/转换工具、指定上游 Skills 与有证据绑定经验的复用快照。
它不是完整 EEAS 软件发行物，不代表十个公开项目全功能审计完成，也不签发内网、
Windows 原生、公司 DeepSeek/API/MCP、模型 Vision 或用户审美资格。原 FAIL 和 UNKNOWN 保留。

在含中文与空格的解包目录启动独立布局 Lab（仅需 Python 3.10，本机，无 CDN）：

```powershell
Set-Location 'D:\\内网开发\\EEAS 前端 kit'
py -3.10 -m http.server 8088 --bind 127.0.0.1 --directory apps/web
```

浏览 http://127.0.0.1:8088/assets/glass-lab/，按 Ctrl+C 停止。
Lab 是布局演示；主 index.html 的项目/任务/审批/真实窗口需要完整 EEAS 后端。
本 kit 不带 runtime SDK CLI、运行 wheels、Playwright 浏览器或完整后端。
原 20 场景和新增导航竞态、布局/提交迁移测试及 fixture/helper 是可移入同版本完整 EEAS 的回归材料，不能在本 kit 中
假称完整运行；真实测试还需正式 runtime/test vendor 闭包与其离线安装器。

字体 WOFF2、原 TTF/OFL、FontTools/Brotli 的 Python 3.10 Linux/Windows wheels、
原许可和固定来源清单随包。复用已有字体无需安装任何工具。生成工具仅为开发用途。
按 apps/web/assets/fonts/README.md 的 --no-index --require-hashes 命令使用新环境；
acquire 命令是显式在线获取工具，内网不要执行。字体覆盖范围按原 manifest，不是全 CJK/emoji。

.claude/skills 中保留 11 新技能、frontend-design/impeccable/webapp-testing 及项目经验 Skill；
保留原文、上游 commit/许可，不把 MIT、Apache 或 OFL 互相等同。
按当前任务仅加载相关一个主 Skill；不自动注册产品 SDK 插件，也不自动安装 npm/GSAP。
上游指导中的在线链接、安装步骤和云能力不因随包而获得内网资格。
Linux 的解包工具若不恢复 ZIP 可执行位，需显式 chmod u+x
.claude/skills/impeccable/scripts/bin/linux-x64/impeccable；不得让 launcher 改用联网下载。

FRONTEND_KIT_MANIFEST.json 绑定实际成员 SHA/字节、Git HEAD、实际 dirty 状态及来源检查。
dirty 工作内容有独立成员 SHA，不以 HEAD 冒充实际字节。经验 catalog 是机器元数据索引；
精选 lesson 是人工证据总结，不声称逐个理解所有历史问题。原报告元数据 SHA 为外部引用，
源码 snapshot 不重复装包。可选所选 raw 必须经秘密审查；路径排除不能识别任意秘密。
只有构建时传入 --catalog 才携带机器索引；manifest 的 catalog_included 记录实际情况。

用随包标准库校验器验证，无解包、模型调用或内容执行：

```powershell
py -3.10 scripts/bundle_frontend_kit.py validate --archive 'D:\\内网开发\\frontend-kit.zip' --expected-sha256 <包外可信SHA>
py -3.10 tests/test_experience_catalog.py
py -3.10 tests/test_bundle_frontend_kit.py
```

校验只能证明字节/结构绑定，包外可信 SHA 与独立源审查仍必需。
失败后的部分 ZIP 留存用于诊断，必须用新名称重做，不能覆盖或称为成功制品。
最终软件看完整发行物的 BUNDLE_MANIFEST.json / 包外 state/DELIVERY.json；不硬编码历史包。
"""


def kit_path(relative):
    path = portable_relative(relative)
    if any(re.search(r'[<>"|?*\x01-\x1f]', part) or part.lower() in FORBIDDEN or
           part.lower().startswith(".env") or part.lower().endswith(".token") for part in path.parts):
        raise CatalogError("Private/cache/nonportable member is forbidden")
    return path


def alias(relative):
    kit_path(relative)
    return unicodedata.normalize("NFC", relative).casefold()


def git_command(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=15)
    return result.stdout


def source_identity(root):
    head = git_command(root, "rev-parse", "HEAD").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise CatalogError("A real source Git commit is required")
    status = git_command(root, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
    return {"git_head": head, "repository_dirty": bool(status), "status_sha256": hashlib.sha256(status).hexdigest(),
            "status_scope": "actual repository status; paths/values not printed", "artifact_scope": "DEVELOPMENT_PREVIEW_FRONTEND_KIT_ONLY"}


def selected_files(root):
    selected = set(FILES + ESSENTIAL_ASSETS)
    missing_optional = []
    for relative in OPTIONAL_FILES:
        if safe_source(root, relative).exists():
            selected.add(relative)
        else:
            missing_optional.append(relative)
    for relative in TREES:
        directory = safe_source(root, relative)
        if not directory.is_dir():
            raise CatalogError("Required frontend tree missing: " + relative)
        stack = [directory]
        while stack:
            parent = stack.pop()
            for child in sorted(parent.iterdir()):
                rel = child.relative_to(root).as_posix()
                kit_path(rel)
                if child.is_symlink():
                    raise CatalogError("Symlink in selected tree: " + rel)
                if child.is_dir():
                    stack.append(child)
                elif child.is_file():
                    selected.add(rel)
                else:
                    raise CatalogError("Non-regular frontend member: " + rel)
                if len(selected) > MAX_MEMBERS:
                    raise CatalogError("Frontend member count exceeds limit")
    return selected, missing_optional


def member_metadata(root, relative):
    kit_path(relative)
    metadata, _ = inspect_file(safe_source(root, relative), limit=0)
    if metadata["byte_length"] > MAX_FILE_BYTES:
        raise CatalogError("Frontend member exceeds size limit: " + relative)
    executable = bool(safe_source(root, relative).stat().st_mode & 0o111) or relative == (
        ".claude/skills/impeccable/scripts/bin/linux-x64/impeccable")
    return {"path": relative, "sha256": metadata["sha256"], "byte_length": metadata["byte_length"],
            "archive_mode": stat.S_IFREG | (0o755 if executable else 0o644)}


def json_member(root, relative):
    metadata, raw = inspect_file(safe_source(root, relative), limit=1024 * 1024)
    if metadata["metadata_truncated"]:
        raise CatalogError("Source metadata exceeds 1 MiB")
    return strict_json(raw)


def checked_pin(members, relative, sha256, byte_length=None):
    kit_path(relative)
    actual = members.get(relative)
    if not SHA.fullmatch(str(sha256)) or actual is None or actual["sha256"] != sha256:
        raise CatalogError("Pinned original missing/changed: " + relative)
    if byte_length is not None and (type(byte_length) is not int or actual["byte_length"] != byte_length):
        raise CatalogError("Pinned original byte length differs: " + relative)


def check_sources(root, members):
    skills = []
    profile = json_member(root, "profiles/development-skills.json")
    if not isinstance(profile, list):
        raise CatalogError("Development skills profile must be a list")
    for name in NEW_SKILLS:
        base = ".claude/skills/" + name
        source = json_member(root, base + "/UPSTREAM_SOURCE.json")
        if not isinstance(source, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("commit"))) or not isinstance(source.get("files"), dict):
            raise CatalogError("New skill has no fixed upstream source: " + name)
        if not source["files"] or "UPSTREAM-LICENSE.txt" not in source["files"]:
            raise CatalogError("New skill original license pin missing: " + name)
        for relative, sha in source["files"].items():
            checked_pin(members, base + "/" + str(kit_path(relative)), sha)
        if base + "/SKILL.md" not in members:
            raise CatalogError("Skill entrypoint missing")
        skills.append({"name": name, "source_metadata_path": base + "/UPSTREAM_SOURCE.json", "commit": source["commit"],
                       "license_declared": source.get("license_spdx", "UNKNOWN"), "original_pins_checked": len(source["files"]),
                       "source_authentication": "NOT_INFERRED"})
    for name in EXISTING_SKILLS:
        candidates = [row for row in profile if isinstance(row, dict) and row.get("name") == name]
        if len(candidates) != 1 or not isinstance(candidates[0].get("files_sha256"), dict) or not re.fullmatch(
                r"[0-9a-f]{40}", str(candidates[0].get("commit"))):
            raise CatalogError("Existing skill exact source record missing: " + name)
        source, base = candidates[0], ".claude/skills/" + name
        for relative, sha in source["files_sha256"].items():
            checked_pin(members, base + "/" + str(kit_path(relative)), sha)
        if "SKILL.md" not in source["files_sha256"] or not any("LICENSE" in relative.upper() for relative in source["files_sha256"]):
            raise CatalogError("Existing skill original license missing")
        skills.append({"name": name, "source_metadata_path": "profiles/development-skills.json", "commit": source.get("commit"),
                       "license_declared": source.get("license", "UNKNOWN"), "original_pins_checked": len(source["files_sha256"]),
                       "source_authentication": "NOT_INFERRED"})
    font_checks = 0
    for base in ("apps/web/assets/fonts", "toolchain/ui-fonts/noto-serif-sc"):
        source = json_member(root, base + "/sources.json")
        if not isinstance(source, dict) or not isinstance(source.get("files"), list) or not source["files"] or not re.fullmatch(
                r"[0-9a-f]{40}", str(source.get("commit"))):
            raise CatalogError("Original font source list missing")
        for row in source["files"]:
            checked_pin(members, base + "/" + str(kit_path(row["path"])), row["sha256"], row["byte_length"])
            font_checks += 1
    font_manifest = json_member(root, "apps/web/assets/fonts/manifest.json")
    if not isinstance(font_manifest, dict) or not isinstance(font_manifest.get("fonts"), list) or not font_manifest["fonts"]:
        raise CatalogError("Runtime font bindings missing")
    for row in font_manifest["fonts"]:
        checked_pin(members, "apps/web/assets/fonts/" + str(kit_path(row["original_path"])), row["original_sha256"])
        checked_pin(members, "apps/web/assets/fonts/" + str(kit_path(row["woff2_path"])), row["woff2_sha256"], row["woff2_bytes"])
    serif = json_member(root, "toolchain/ui-fonts/noto-serif-sc/manifest.json")
    checked_pin(members, serif["original_path"], serif["original_sha256"], serif["original_bytes"])
    checked_pin(members, serif["runtime_path"], serif["woff2_sha256"], serif["woff2_bytes"])
    tools_base = "apps/web/assets/fonts/generation"
    tools = json_member(root, tools_base + "/tools-manifest.json")
    if not isinstance(tools, dict) or tools.get("schema") != "eeas.offline-font-tools.v1" or not isinstance(tools.get("files"), list):
        raise CatalogError("Font generation tool manifest missing")
    wheels = []
    for row in tools["files"]:
        path = tools_base + "/" + str(kit_path(row["path"]))
        checked_pin(members, path, row["sha256"], row["byte_length"])
        if not row.get("licenses"):
            raise CatalogError("Font generation wheel original license missing")
        for license_row in row["licenses"]:
            checked_pin(members, tools_base + "/" + str(kit_path(license_row["path"])), license_row["sha256"])
        wheels.append(path)
    required_wheels = {tools_base + "/wheels/" + name for name in (
        "fonttools-4.65.0-py3-none-any.whl", "brotli-1.2.0-cp310-cp310-win_amd64.whl",
        "brotli-1.2.0-cp310-cp310-manylinux2014_x86_64.manylinux_2_17_x86_64.whl")}
    if set(wheels) != required_wheels:
        raise CatalogError("Exact Python 3.10 font generation wheel closure differs")
    engine_base = ".claude/skills/impeccable/scripts"
    engine = json_member(root, engine_base + "/bin/OFFLINE_ENGINE_MANIFEST.json")
    _, version_raw = inspect_file(safe_source(root, engine_base + "/VERSION"), limit=100)
    version = version_raw.decode("ascii").strip()
    if not isinstance(engine, dict) or engine.get("schema") != "eeas.skill-offline-engine.v1" or not isinstance(
            engine.get("files"), list) or not re.fullmatch(r"[0-9a-f]{40}", str(engine.get("commit"))) or (
            engine.get("release_tag") != "engine-v" + version):
        raise CatalogError("Impeccable offline engine source/version manifest differs")
    engine_paths = set()
    for row in engine["files"]:
        checked_pin(members, row["path"], row["sha256"], row["byte_length"])
        if not row["path"].startswith(engine_base + "/bin/") or row["path"] in engine_paths:
            raise CatalogError("Engine manifest path scope/duplicate differs")
        engine_paths.add(row["path"])
    for relative in (engine_base + "/bin/linux-x64/impeccable", engine_base + "/bin/windows-x64/impeccable.exe"):
        if not {relative, relative + ".sha256"} <= engine_paths:
            raise CatalogError("Engine binary/official SHA sidecar missing")
        _, raw = inspect_file(safe_source(root, relative + ".sha256"), limit=4096)
        match = re.match(rb"([0-9a-f]{64})(?:\s|$)", raw)
        if not match or match.group(1).decode() != members[relative]["sha256"]:
            raise CatalogError("Engine official SHA sidecar differs")
    if not any("LICENSE" in PurePosixPath(path).name.upper() for path in engine_paths):
        raise CatalogError("Engine original license missing")
    return {"skills": skills, "project_owned_skill": OWNED_SKILL, "original_font_pins_checked": font_checks,
            "font_generation_wheels": wheels,
            "offline_skill_engine": {"release_tag": engine["release_tag"], "commit": engine["commit"],
                                     "files_pinned": len(engine_paths), "execution_by_builder": "NOT_RUN", "native_windows": "NOT_RUN"},
            "qualification": "HASH_BINDING_ONLY_NOT_INSTALL_OR_NATIVE_GENERATION"}


def report_references(root):
    refs = []
    for name in REPORTS:
        relative = "reports/" + name
        directory = safe_source(root, relative)
        record = {"path": relative, "status": "METADATA_REFERENCES_ONLY", "raw_artifacts_copied": False,
                  "snapshots_traversed": False, "depth_limit": 3, "metadata_selection": "JSON/README/stdout/stderr only; other artifacts external",
                  "metadata": []}
        if not directory.exists():
            record["status"] = "NOT_PRESENT"
        else:
            stack = [(directory, 0)]
            while stack:
                parent, depth = stack.pop()
                for child in sorted(parent.iterdir()):
                    rel = child.relative_to(root).as_posix()
                    kit_path(rel)
                    if child.is_symlink():
                        raise CatalogError("Symlink report reference")
                    if child.is_dir():
                        if "snapshot" in child.name.lower() or depth >= 3:
                            continue
                        stack.append((child, depth + 1))
                    elif child.is_file() and (child.suffix.lower() == ".json" or child.name.lower().startswith("readme") or
                                             re.search(r"(?:stdout|stderr).*(?:\.txt|\.log)$", child.name.lower())):
                        record["metadata"].append(member_metadata(root, rel))
                        if len(record["metadata"]) > 4000:
                            raise CatalogError("Report metadata reference count exceeds limit")
        refs.append(record)
    return refs


def allowed_member(relative, evidence_paths=()):
    kit_path(relative)
    return relative in FILES + OPTIONAL_FILES + (README, "experience/catalog.json") or relative in evidence_paths or any(
        relative.startswith(tree + "/") for tree in TREES)


def build_frontend_kit(repo_root, output, catalog_path=None, include_lesson_evidence=False):
    root = Path(repo_root).resolve()
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Output already exists")
    if any(parent.is_symlink() for parent in (output.parent, *output.parent.parents)):
        raise CatalogError("Output parents cannot be symlinks")
    before = source_identity(root)
    selected, missing_optional = selected_files(root)
    lessons = load_lessons(root, "experience/lessons.jsonl")
    evidence = {row["path"]: row["sha256"] for lesson in lessons for row in lesson["evidence"]}
    copied = set()
    if include_lesson_evidence:
        for relative in evidence:
            parts = kit_path(relative).parts
            if not relative.startswith("reports/") or any("snapshot" in part.lower() for part in parts):
                raise CatalogError("Selected lesson raw must be report evidence, not a source snapshot")
            copied.add(relative)
        selected.update(copied)
    if len(selected) + 3 > MAX_MEMBERS:
        raise CatalogError("Frontend member count exceeds limit")
    if output.resolve() in {safe_source(root, path).resolve() for path in selected} or any(
            output.resolve().is_relative_to(safe_source(root, tree).resolve()) for tree in TREES):
        raise CatalogError("Output cannot be inside selected source trees")
    members, aliases = {}, set()
    for relative in sorted(selected):
        key = alias(relative)
        if key in aliases:
            raise CatalogError("Windows case/Unicode alias")
        aliases.add(key)
        members[relative] = member_metadata(root, relative)
        if relative in copied and members[relative]["sha256"] != evidence[relative]:
            raise CatalogError("Selected lesson evidence changed")
    sources = check_sources(root, members)
    generated = {README: README_TEXT.encode("utf-8")}
    if catalog_path is not None:
        metadata, raw = inspect_file(Path(catalog_path), limit=16 * 1024 * 1024)
        catalog = strict_json(raw) if not metadata["metadata_truncated"] else None
        if not isinstance(catalog, dict) or catalog.get("schema_version") != "eeas.experience-catalog.v1":
            raise CatalogError("Experience catalog schema/size invalid")
        generated["experience/catalog.json"] = raw
    for relative, raw in generated.items():
        if alias(relative) in aliases:
            raise CatalogError("Generated member collides with selected source")
        aliases.add(alias(relative))
        members[relative] = {"path": relative, "sha256": hashlib.sha256(raw).hexdigest(), "byte_length": len(raw),
                             "archive_mode": stat.S_IFREG | 0o644, "generated": True}
    if sum(row["byte_length"] for row in members.values()) > MAX_TOTAL_BYTES:
        raise CatalogError("Frontend kit total expanded size exceeds limit")
    refs = report_references(root)
    manifest = {"schema_version": SCHEMA, "created_at": utc_now(), "source_before": before,
                "members": list(members.values()), "source_checks": sources, "skills_selected": list(SKILLS),
                "fixed_allowlist": {"trees": list(TREES), "files": list(FILES), "optional_files": list(OPTIONAL_FILES)},
                "missing_optional_files": missing_optional, "report_references": refs,
                "lesson_records": len(lessons), "lesson_evidence_references": evidence,
                "copied_lesson_evidence": sorted(copied), "source_snapshots_copied": False,
                "catalog_included": catalog_path is not None,
                "browser_probes": {"declared_original_nodes": 20, "collection_or_execution_by_builder": False,
                                   "whole_backend_and_test_vendor_required": True},
                "qualification": {"assessment": "CONTENT_BOUND_FRONTEND_DEVELOPMENT_KIT_ONLY", "native_windows": "NOT_RUN",
                                  "company_api_model_mcp_vision": "UNKNOWN", "public_ten_project_campaign": "NOT_COMPLETE",
                                  "aesthetics": "UNCONFIRMED"}}
    with output.open("xb") as destination:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative, metadata in members.items():
                if relative in generated:
                    raw = generated[relative]
                else:
                    actual, raw = inspect_file(safe_source(root, relative), limit=MAX_FILE_BYTES)
                    if (actual["sha256"], actual["byte_length"]) != (metadata["sha256"], metadata["byte_length"]):
                        raise CatalogError("Source changed during kit build: " + relative)
                info = zipfile.ZipInfo(relative)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = metadata["archive_mode"] << 16
                archive.writestr(info, raw)
            after = source_identity(root)
            if before["git_head"] != after["git_head"]:
                raise CatalogError("Source commit changed during kit build")
            for relative, metadata in members.items():
                if relative not in generated:
                    actual, _ = inspect_file(safe_source(root, relative), limit=0)
                    if (actual["sha256"], actual["byte_length"]) != (metadata["sha256"], metadata["byte_length"]):
                        raise CatalogError("Source changed after kit copy: " + relative)
            manifest["source_after"] = after
            manifest["global_status_changed_during_build"] = before["status_sha256"] != after["status_sha256"]
            archive.writestr(MANIFEST, canonical(manifest))
    return manifest


def validate_frontend_kit(archive_path, expected_sha256=None):
    digest, _ = inspect_file(Path(archive_path), limit=0)
    if digest["byte_length"] > MAX_TOTAL_BYTES:
        raise CatalogError("Archive size limit")
    if expected_sha256 is not None and (not SHA.fullmatch(expected_sha256) or digest["sha256"] != expected_sha256):
        raise CatalogError("External archive SHA differs")
    with zipfile.ZipFile(archive_path) as archive:
        infos, names, total = archive.infolist(), {}, 0
        if not 1 <= len(infos) <= MAX_MEMBERS:
            raise CatalogError("Archive member count limit")
        for info in infos:
            key = alias(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if key in names or info.is_dir() or mode not in (0, stat.S_IFREG) or info.flag_bits & 1:
                raise CatalogError("Duplicate/alias/special/encrypted ZIP member")
            names[key] = info
            total += info.file_size
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or info.file_size > MAX_FILE_BYTES or (
                    info.file_size > max(1, info.compress_size) * MAX_RATIO) or total > MAX_TOTAL_BYTES:
                raise CatalogError("Unsupported ZIP compression/size/ratio")
        if MANIFEST.casefold() not in names or names[MANIFEST.casefold()].filename != MANIFEST:
            raise CatalogError("Frontend manifest missing")
        manifest_info = names[MANIFEST.casefold()]
        if manifest_info.file_size > 16 * 1024 * 1024:
            raise CatalogError("Manifest size limit")
        manifest = strict_json(archive.read(manifest_info))
        if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA or not isinstance(manifest.get("members"), list):
            raise CatalogError("Frontend manifest schema invalid")
        expected, evidence = {}, manifest.get("copied_lesson_evidence", [])
        bindings = manifest.get("lesson_evidence_references", {})
        if not isinstance(evidence, list) or not isinstance(bindings, dict):
            raise CatalogError("Lesson evidence manifest invalid")
        for relative in evidence:
            if not isinstance(relative, str) or not relative.startswith("reports/") or any(
                    "snapshot" in part.lower() for part in kit_path(relative).parts) or relative not in bindings:
                raise CatalogError("Copied evidence is not bound report raw")
        for row in manifest["members"]:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str) or not SHA.fullmatch(str(row.get("sha256"))) or (
                    type(row.get("byte_length")) is not int or row["byte_length"] < 0 or
                    type(row.get("archive_mode")) is not int or row["archive_mode"] not in (stat.S_IFREG | 0o644, stat.S_IFREG | 0o755)):
                raise CatalogError("Member manifest invalid")
            path, key = row["path"], alias(row["path"])
            if key in expected or not allowed_member(path, evidence):
                raise CatalogError("Member not in fixed frontend allowlist")
            if path in evidence and row["sha256"] != bindings[path]:
                raise CatalogError("Copied lesson raw binding differs")
            expected[key] = row
        required = set(FILES + ESSENTIAL_ASSETS) | {README} | {".claude/skills/" + name + "/SKILL.md" for name in SKILLS}
        required |= {".claude/skills/" + name + "/UPSTREAM_SOURCE.json" for name in NEW_SKILLS}
        required |= {".claude/skills/" + name + "/UPSTREAM-LICENSE.txt" for name in NEW_SKILLS}
        if not required <= {row["path"] for row in expected.values()} or manifest.get("skills_selected") != list(SKILLS):
            raise CatalogError("Required frontend/skill/test materials missing")
        for key in ("source_before", "source_after"):
            source = manifest.get(key)
            if not isinstance(source, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("git_head"))) or (
                    type(source.get("repository_dirty")) is not bool or not SHA.fullmatch(str(source.get("status_sha256")))):
                raise CatalogError("Actual source identity missing")
        if manifest["source_before"]["git_head"] != manifest["source_after"]["git_head"]:
            raise CatalogError("Source commit changed")
        if set(names) != set(expected) | {MANIFEST.casefold()}:
            raise CatalogError("Missing or unlisted ZIP member")
        for key, row in expected.items():
            info = names[key]
            if info.filename != row["path"] or info.file_size != row["byte_length"]:
                raise CatalogError("Member name/byte length differs")
            if (info.external_attr >> 16) & 0xffff != row["archive_mode"]:
                raise CatalogError("Member archive mode differs")
            digest_member, length = hashlib.sha256(), 0
            with archive.open(info) as stream:
                while block := stream.read(65536):
                    digest_member.update(block); length += len(block)
                    if length > info.file_size:
                        raise CatalogError("Expanded member exceeds declared size")
            if length != row["byte_length"] or digest_member.hexdigest() != row["sha256"]:
                raise CatalogError("Member SHA differs")
    after, _ = inspect_file(Path(archive_path), limit=0)
    if (digest["sha256"], digest["byte_length"]) != (after["sha256"], after["byte_length"]):
        raise CatalogError("Archive changed during verification")
    return {"schema_version": SCHEMA, "archive_sha256": digest["sha256"], "members_checked": len(expected),
            "status": "HASH_AND_STRUCTURE_VALID_ONLY", "qualification": "NOT_INFERRED"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    build = actions.add_parser("build")
    build.add_argument("--repo-root", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--catalog")
    build.add_argument("--include-lesson-evidence", action="store_true")
    validate = actions.add_parser("validate")
    validate.add_argument("--archive", required=True)
    validate.add_argument("--expected-sha256")
    args = parser.parse_args(argv)
    try:
        if args.action == "build":
            manifest = build_frontend_kit(args.repo_root, args.output, args.catalog, args.include_lesson_evidence)
            result = {"output": args.output, "members": len(manifest["members"]), "source": manifest["source_before"],
                      "scope": manifest["qualification"]["assessment"]}
        else:
            result = validate_frontend_kit(args.archive, args.expected_sha256)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (CatalogError, OSError, ValueError, KeyError, TypeError, RecursionError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "ERROR", "reason": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
