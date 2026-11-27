"""Verify and install the optional, fixed PPTX closure offline into an existing venv.

No environment is created/replaced, downloads and dependency resolution are disabled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import unicodedata
import zipfile


def _member_name(name: str) -> str:
    if (not name or name.startswith('/') or '\\' in name or ':' in name
            or len(name) > 512 or any(ord(c) < 32 for c in name)):
        raise ValueError('Unsafe PPTX dependency member name')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    if any(not p or p in {'.', '..'} or p.endswith((' ', '.')) or p.split('.')[0].upper() in reserved
           or any(c in '<>"|?*' for c in p) for p in name.split('/')):
        raise ValueError('Unsafe Windows PPTX dependency member name')
    return unicodedata.normalize('NFC', name).casefold()


def _pinned_file(root: Path, entry: dict) -> bytes:
    _member_name(entry['path'])
    path = root / entry['path']
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('Symlink PPTX license/metadata path')
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError('PPTX license/metadata escapes root')
    raw = path.read_bytes()
    if len(raw) > 1024 * 1024 or len(raw) != entry['byte_length'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('PPTX original license/metadata integrity failure')
    return raw


def verify_bundle(bundle: Path, target: str) -> dict:
    root = bundle.resolve(strict=True)
    if bundle.is_symlink() or not root.is_dir():
        raise ValueError("Unsafe PPTX dependency root")
    for path in (root / "manifest.json", root / "requirements.lock", root / "wheels"):
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("PPTX dependency path escapes its root")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "eeas.office-pptx-dependencies.v1" or manifest.get("target") != target or manifest.get("python") != "3.10":
        raise ValueError("PPTX dependency target/schema mismatch")
    packages = manifest.get("packages")
    if not isinstance(packages, list) or not 1 <= len(packages) <= 32:
        raise ValueError("PPTX dependency count limit")
    expected, names, files = [], set(), set()
    for item in packages:
        name, version, wheel = item["name"], item["version"], item["wheel"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) or not re.fullmatch(r"[0-9][A-Za-z0-9_.+-]*", version):
            raise ValueError("Invalid PPTX pinned package")
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        if normalized in names:
            raise ValueError("Duplicate PPTX pinned package")
        names.add(normalized)
        filename, sha = wheel["filename"], wheel["sha256"]
        _member_name(filename)
        if (PurePosixPath(filename).name != filename or "\\" in filename or not filename.endswith(".whl")
                or not re.fullmatch("[0-9a-f]{64}", sha) or filename.casefold() in files):
            raise ValueError("Unsafe/aliased PPTX wheel name/hash")
        files.add(filename.casefold())
        path = root / "wheels" / filename
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("PPTX wheel path escapes its root")
        raw = path.read_bytes()
        if not 0 < len(raw) <= 32 * 1024 * 1024 or len(raw) != wheel["byte_length"] or hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError("PPTX wheel integrity failure")
        with zipfile.ZipFile(path) as archive:
            aliases, prefixes, members, total = set(), {}, {}, 0
            infos = archive.infolist()
            if len(infos) > 4096:
                raise ValueError("PPTX wheel member count limit")
            for info in infos:
                member = info.filename.rstrip("/")
                canonical = _member_name(member)
                if canonical in aliases:
                    raise ValueError("Unsafe/aliased PPTX wheel member")
                aliases.add(canonical)
                members[canonical] = (member, info.is_dir())
                components = member.split('/')
                for index in range(1, len(components) + 1):
                    prefix = '/'.join(components[:index]); key = _member_name(prefix)
                    if key in prefixes and prefixes[key] != prefix:
                        raise ValueError('Windows directory alias in PPTX wheel')
                    prefixes[key] = prefix
                if stat.S_IFMT(info.external_attr >> 16) not in {0, stat.S_IFDIR, stat.S_IFREG} or info.flag_bits & 1:
                    raise ValueError("Unsafe PPTX wheel member type")
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise ValueError("Unsupported PPTX wheel compression")
                total += info.file_size
                if total > 96 * 1024 * 1024 or info.file_size > 32 * 1024 * 1024:
                    raise ValueError("PPTX wheel expansion limit")
                if info.file_size > 1024 * 1024 and info.file_size > max(1, info.compress_size) * 300:
                    raise ValueError("PPTX wheel compression ratio limit")
            for member, _ in members.values():
                for parent in PurePosixPath(member).parents:
                    if str(parent) == '.':
                        continue
                    existing = members.get(_member_name(str(parent)))
                    if existing and not existing[1]:
                        raise ValueError('PPTX wheel file/directory conflict')
            tag_members = [i for i in infos if i.filename.endswith(".dist-info/WHEEL")]
            if len(tag_members) != 1 or tag_members[0].file_size > 16384:
                raise ValueError("Invalid PPTX wheel platform metadata")
            tags = [line[5:] for line in archive.read(tag_members[0]).decode("utf-8").splitlines() if line.startswith("Tag: ")]
            pure = "py3-none-any" in tags
            platform_ok = any(t.startswith("cp310-cp310-") and
                ((target == "windows-py310" and t.endswith("win_amd64")) or
                 (target == "linux-py310" and "manylinux" in t and t.endswith("x86_64"))) for t in tags)
            if not pure and not platform_ok:
                raise ValueError("PPTX wheel platform does not match Python 3.10 target")
            if archive.testzip() is not None:
                raise ValueError("PPTX wheel CRC failure")
            originals = item['licenses'] + [item['wheel_metadata']]
            if not item['licenses'] or len(originals) > 32:
                raise ValueError('Missing/excessive PPTX original licenses')
            for original in originals:
                stored = _pinned_file(root, original)
                if archive.read(original['wheel_member']) != stored:
                    raise ValueError('PPTX license/metadata differs from original wheel member')
        expected.append(name + "==" + version + " --hash=sha256:" + sha)
    lock = (root / "requirements.lock").read_bytes()
    # Only our generated pins are accepted: no URL, editable file, extra index or option.
    if lock != ("\n".join(expected) + "\n").encode("utf-8") or hashlib.sha256(lock).hexdigest() != manifest["requirements_sha256"]:
        raise ValueError("PPTX requirements lock integrity/closed-pins failure")
    if {path.name.casefold() for path in (root / 'wheels').iterdir()} != files:
        raise ValueError('Unlisted or missing PPTX wheelhouse entries')
    return manifest


def selected_bundle(source_root: Path, target: str, payload_root: Path | None = None) -> tuple[Path, dict, dict]:
    """Bind the public dependency manifest to the program's frozen selection/source."""
    selection_path = source_root / 'vendor/office-pptx/selection.json'
    if selection_path.is_symlink():
        raise ValueError('Unsafe PPTX selection')
    selection = json.loads(selection_path.read_text(encoding='utf-8'))
    if selection.get('schema') != 'eeas.office-pptx-selection.v1':
        raise ValueError('Invalid PPTX selection schema')
    source_name = selection['component_source']; _member_name(source_name)
    source = source_root / source_name
    if source.is_symlink() or not source.resolve(strict=True).is_relative_to(source_root.resolve()):
        raise ValueError('Unsafe PPTX component source path')
    if hashlib.sha256(source.read_bytes()).hexdigest() != selection['component_source_sha256']:
        raise ValueError('PPTX component source selection integrity failure')
    chosen = selection['targets'][target]; relative = chosen['root']; _member_name(relative)
    path = (payload_root or source_root) / relative
    if path.is_symlink() or not path.resolve(strict=True).is_relative_to((payload_root or source_root).resolve()):
        raise ValueError('Unsafe PPTX selected payload path')
    if hashlib.sha256((path / 'manifest.json').read_bytes()).hexdigest() != chosen['manifest_sha256']:
        raise ValueError('PPTX selected manifest integrity failure')
    manifest = verify_bundle(path, target)
    if (manifest['requirements_sha256'] != chosen['requirements_sha256']
            or len(manifest['packages']) != chosen['wheels']
            or sum(p['wheel']['byte_length'] for p in manifest['packages']) != chosen['wheel_bytes']):
        raise ValueError('PPTX selection and actual closure disagree')
    return path, manifest, selection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--target", choices=("linux-py310", "windows-py310"), required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    manifest = verify_bundle(args.bundle, args.target)
    print(json.dumps({"status": "VERIFIED", "target": args.target, "wheels": len(manifest["packages"]), "native_windows": "NOT_RUN"}))
    if args.verify_only:
        return 0
    _, selected, _ = selected_bundle(Path(__file__).resolve().parents[1], args.target)
    if selected != manifest:
        raise ValueError('Install bundle does not match the program selected PPTX closure')
    actual_target = "windows-py310" if sys.platform == "win32" else "linux-py310" if sys.platform.startswith("linux") else None
    if sys.version_info[:2] != (3, 10) or actual_target != args.target or sys.prefix == sys.base_prefix:
        raise ValueError("Install requires matching Python 3.10 in an existing isolated venv")
    root = args.bundle.resolve()
    command = [sys.executable, "-m", "pip", "--isolated", "install", "--no-index", "--no-deps",
               "--require-hashes", "--find-links", str(root / "wheels"), "-r", str(root / "requirements.lock")]
    result = subprocess.run(command, check=False)
    if result.returncode:
        return result.returncode
    return subprocess.run([sys.executable, "-m", "pip", "--isolated", "check"], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
