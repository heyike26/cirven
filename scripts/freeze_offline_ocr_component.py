"""Freeze explicitly acquired OCR wheels/models and check both target dependency graphs."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
from email.parser import BytesParser
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys
import threading
import urllib.request
import zipfile

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import compatible_tags, cpython_tags, sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.domain.local_workspaces import canonical, digest, sha
METADATA_LOCKS = {}


def wheel(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        member = next(n for n in names if n.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(member))
        record = next(n for n in names if n.endswith(".dist-info/RECORD"))
        checked = 0
        for name, checksum, size in csv.reader(StringIO(archive.read(record).decode())):
            if not checksum:
                continue
            raw = archive.read(name)
            algorithm, expected = checksum.split("=", 1)
            if algorithm != "sha256" or base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode() != expected:
                raise ValueError("Wheel RECORD mismatch: " + name)
            if size and len(raw) != int(size):
                raise ValueError("Wheel RECORD length mismatch: " + name)
            checked += 1
        return {"name": canonicalize_name(metadata["Name"]), "version": metadata["Version"], "path": str(path.resolve()),
                "filename": path.name, "sha256": sha(path.read_bytes()), "byte_length": path.stat().st_size,
                "requires_dist": metadata.get_all("Requires-Dist", []), "requires_python": metadata.get("Requires-Python"),
                "license_metadata": metadata.get("License-Expression") or metadata.get("License"), "record_members_verified": checked}


def public_source(value: dict, component: Path) -> dict:
    if value["name"] == "antlr4-python3-runtime":
        source = component / "originals/antlr4-python3-runtime-4.9.3.tar.gz"
        if sha(source.read_bytes()) != "f224469b4168294902bb1efa80a8bf7855f24c99aef99cbefc1bcd3cce77881b":
            raise ValueError("Derived ANTLR wheel original changed")
        return {"kind": "DERIVED_PURE_PYTHON_WHEEL", "source_url": "https://pypi.org/pypi/antlr4-python3-runtime/4.9.3/json",
                "source_sha256": sha(source.read_bytes()), "source_filename": source.name,
                "recipe": ["python", "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-index", "--wheel-dir", "wheels/common", "originals/" + source.name]}
    metadata_path = component / "originals" / (value["name"] + "-" + value["version"] + "-pypi.json")
    url = "https://pypi.org/pypi/" + value["name"] + "/" + value["version"] + "/json"
    with METADATA_LOCKS.setdefault(str(metadata_path), threading.Lock()):
        if metadata_path.exists():
            raw = metadata_path.read_bytes()
        else:
            with urllib.request.urlopen(url, timeout=40) as stream:
                raw = stream.read()
            with metadata_path.open("xb") as stream:
                stream.write(raw)
    metadata = json.loads(raw)
    original = next(v for v in metadata["urls"] if v["filename"] == value["filename"])
    if value["sha256"] != original["digests"]["sha256"] or value["byte_length"] != original["size"]:
        raise ValueError("Official PyPI receipt mismatch: " + value["filename"])
    return {"kind": "PYPI_ORIGINAL_WHEEL", "url": original["url"], "metadata_url": url,
            "metadata_sha256": sha(raw), "license_expression": metadata["info"].get("license_expression"),
            "classifiers": [c for c in metadata["info"].get("classifiers", []) if c.startswith("License ::")]}


def closure(values: list[dict], platform: str) -> dict:
    index = {v["name"]: v for v in values}
    if len(index) != len(values):
        raise ValueError("Ambiguous wheel versions in closure")
    env = {"implementation_name": "cpython", "implementation_version": "3.10.22", "os_name": "nt" if platform == "windows" else "posix",
           "platform_machine": "AMD64" if platform == "windows" else "x86_64", "platform_python_implementation": "CPython",
           "platform_release": "", "platform_system": "Windows" if platform == "windows" else "Linux", "platform_version": "",
           "python_full_version": "3.10.22", "python_version": "3.10", "sys_platform": "win32" if platform == "windows" else "linux", "extra": ""}
    edges = []
    tags = (set(cpython_tags((3, 10), abis=["cp310"], platforms=["win_amd64"])) |
            set(compatible_tags((3, 10), interpreter="cp310", platforms=["win_amd64"]))) if platform == "windows" else set(sys_tags())
    for value in values:
        if value["requires_python"] and "3.10.22" not in SpecifierSet(value["requires_python"]):
            raise ValueError("Wheel does not support the target Python: " + value["filename"])
        if not parse_wheel_filename(value["filename"])[3] & tags:
            raise ValueError("Wheel tag does not support the target: " + value["filename"])
        for text in value["requires_dist"]:
            dependency = Requirement(text)
            if dependency.marker and not dependency.marker.evaluate(env):
                continue
            name = canonicalize_name(dependency.name)
            if name not in index or index[name]["version"] not in dependency.specifier:
                raise ValueError("Missing target dependency: " + value["name"] + " -> " + text)
            edges.append({"from": value["name"], "requires": text, "resolved": name + "==" + index[name]["version"]})
    return {"platform": platform, "target": "CPython3.10 x64", "marker_environment": env, "status": "PASS", "dependency_edges": edges,
            "native_install_import": "NOT_RUN" if platform == "windows" else "SEPARATE_ACTUAL_INSTALL_RECEIPT"}


def package_files(value: dict) -> dict:
    with zipfile.ZipFile(value["path"]) as archive:
        return {name: sha(archive.read(name)) for name in archive.namelist()
                if ".dist-info/" not in name and "/models/" not in name and
                (name.endswith((".py", ".yaml", ".so", ".dll", ".pyd")) or ".so." in name)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", type=Path, default=ROOT / "toolchain/ocr")
    args = parser.parse_args()
    component = args.component.resolve()
    linux_paths = sorted((component / "wheels/linux310").glob("*.whl"))
    reused_paths = [Path(p) for p in json.loads((component / "windows-reused-wheels.json").read_bytes())]
    windows_paths = [*reused_paths, *sorted((component / "wheels/windows310").glob("*.whl"))]
    paths = sorted({p.resolve() for p in linux_paths + windows_paths})
    values = [wheel(path) for path in paths]
    with ThreadPoolExecutor(max_workers=5) as pool:
        sources = list(pool.map(lambda value: public_source(value, component), values))
    for value, source in zip(values, sources):
        value["source"] = source
    lookup = {v["path"]: v for v in values}
    linux = [lookup[str(p.resolve())] for p in linux_paths]
    windows = [lookup[str(p.resolve())] for p in windows_paths]
    qualification = {p: closure(v, p) for p, v in [("linux", linux), ("windows", windows)]}
    # Preserve all supplied license/notices as inert files, including native
    # runtime vendored licenses; metadata alone is not a full license text.
    licenses = []
    for value in values:
        with zipfile.ZipFile(value["path"]) as archive:
            for name in archive.namelist():
                if name.endswith("/") or not any(word in name.lower() for word in ["license", "copying", "thirdpartynotice"]):
                    continue
                raw = archive.read(name)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError("Unexpectedly large license member")
                destination = component / "licenses" / value["filename"] / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() and destination.read_bytes() != raw:
                    raise ValueError("Existing license record changed")
                if not destination.exists():
                    destination.write_bytes(raw)
                licenses.append({"wheel": value["filename"], "member": name, "path": str(destination.relative_to(component)), "sha256": sha(raw)})
    model_dir = component / "models"
    model_dir.mkdir(exist_ok=True)
    rapid = next(v for v in linux if v["name"] == "rapidocr")
    model_members = {"det": "PP-OCRv6_det_small.onnx", "rec": "PP-OCRv6_rec_small.onnx", "cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx"}
    expected = {"det": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f", "rec": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884", "cls": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"}
    models = {}
    with zipfile.ZipFile(rapid["path"]) as archive:
        for role, filename in model_members.items():
            member = "rapidocr/models/" + filename
            raw = archive.read(member)
            if sha(raw) != expected[role]:
                raise ValueError("Official model SHA mismatch")
            path = model_dir / filename
            if path.exists() and path.read_bytes() != raw:
                raise ValueError("Existing model original changed")
            if not path.exists():
                path.write_bytes(raw)
            models[role] = {"path": str(path.relative_to(component)), "sha256": sha(raw), "byte_length": len(raw),
                            "source_wheel_sha256": rapid["sha256"], "source_member": member, "license": "Apache-2.0", "upstream": "PaddleOCR/Baidu"}
    primary = {v["name"]: v["version"] for v in linux}
    extra = {v["name"]: v["version"] for v in windows if v["name"] not in primary}
    if any(v["version"] != primary[v["name"]] for v in windows if v["name"] in primary):
        raise ValueError("Platform-independent runtime pins diverged")
    files = {platform: {v["name"]: package_files(v) for v in selected}
             for platform, selected in [("linux", linux), ("windows", windows)]}
    model_config = {"schema_version": "eeas.offline-ocr-models.v1", "engine": {"name": "rapidocr", "version": "3.9.2", "wheel_sha256": rapid["sha256"], "provider": "CPUExecutionProvider"},
                    "packages": primary, "platform_packages": {"windows": extra}, "models": models, "engine_files": files}
    (component / "MODELS.json").write_text(canonical({"payload": model_config, "sha256": digest(model_config)}) + "\n", encoding="utf-8")
    for platform, selected in [("linux310", linux), ("windows310", windows)]:
        lines = [v["name"] + "==" + v["version"] + " --hash=sha256:" + v["sha256"] for v in selected]
        (component / ("requirements-" + platform + ".lock")).write_text("\n".join(sorted(lines)) + "\n", encoding="utf-8")
    source = {"schema_version": "eeas.offline-ocr-component.v1", "retrieved_at": datetime.now(timezone.utc).isoformat(), "source_kind": "public",
              "wheels": values, "licenses": licenses, "models": models, "qualification": qualification,
              "model_manifest_sha256": sha((component / "MODELS.json").read_bytes()), "shared_windows_wheels_copied": False,
              "closure_scope": "EXACT_WHEELS_WITH_EXPLICIT_SHARED_PATHS;NOT_A_SELF_CONTAINED_DESKTOP_RELEASE"}
    additional = component / "originals/additional-license-sources.json"
    if additional.is_file():
        source["additional_license_receipts"] = json.loads(additional.read_bytes())
        for value in source["additional_license_receipts"]["sources"]:
            if sha((component / "originals" / value["filename"]).read_bytes()) != value["sha256"]:
                raise ValueError("Additional original license receipt changed")
    (component / "SOURCE.json").write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"wheels": len(values), "linux": len(linux), "windows": len(windows), "model_bytes": sum(v["byte_length"] for v in models.values()), "licenses": len(licenses), "target_graphs": "PASS"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
