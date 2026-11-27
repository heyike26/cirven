"""Download exact runtime wheels for Windows/Python3.10 and WSL self-verification."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from email.parser import BytesParser
import zipfile

from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
CORE = ["fastapi", "uvicorn", "httpx", "python-multipart", "colorama", "psutil", "Pillow", "pypdf"]


def distribution(requirement, target):
    try:
        return metadata.distribution(requirement.name)
    except metadata.PackageNotFoundError:
        if target != "windows-py310":
            raise
        with tempfile.TemporaryDirectory(prefix="eeas-win-metadata-") as directory:
            # The marker was already evaluated for the target. pip otherwise evaluates it on WSL.
            download_spec = requirement.name + str(requirement.specifier)
            subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
                            "--platform", "win_amd64", "--python-version", "3.10", "--implementation", "cp",
                            "--abi", "cp310", "--dest", directory, download_spec], check=True)
            paths = list(Path(directory).glob("*.whl"))
            if len(paths) != 1:
                raise ValueError("Expected one platform-specific dependency wheel")
            with zipfile.ZipFile(paths[0]) as archive:
                names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
                info = BytesParser().parsebytes(archive.read(names[0]))
            return SimpleNamespace(version=info["Version"], requires=info.get_all("Requires-Dist", []))


def closure(roots, target):
    pending = [(name, frozenset()) for name in roots]
    visited, versions = set(), {}
    while pending:
        spec, extras = pending.pop()
        parent = Requirement(spec)
        name = parent.name.lower().replace("_", "-")
        if (name, extras) in visited:
            if not parent.specifier.contains(versions[name]):
                raise ValueError(f"Dependency constraint conflicts with {parent}")
            continue
        visited.add((name, extras))
        dist = distribution(parent, target)
        if not parent.specifier.contains(dist.version):
            raise ValueError(f"Installed dependency violates {parent}")
        versions[name] = dist.version
        for text in dist.requires or []:
            requirement = Requirement(text)
            applies = requirement.marker is None
            windows = target == "windows-py310"
            for extra in {"", *extras}:
                environment = {**default_environment(), "sys_platform": "win32" if windows else "linux",
                               "platform_system": "Windows" if windows else "Linux",
                               "os_name": "nt" if windows else "posix", "extra": extra,
                               "python_version": "3.10", "python_full_version": "3.10.22"}
                if requirement.marker and requirement.marker.evaluate(environment):
                    applies = True
            if applies:
                pending.append((str(requirement), frozenset(requirement.extras)))
    return versions


def prepare(target, core, runtime):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = ROOT / "vendor" / target / stamp
    wheels = directory / "wheels"
    wheels.mkdir(parents=True, exist_ok=False)
    for name, versions in (("requirements-core.lock", core), ("requirements-runtime.lock", runtime)):
        (directory / name).write_text("".join(f"{key}=={value}\n" for key, value in sorted(versions.items())))
    command = [sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
               "--dest", str(wheels), "-r", str(directory / "requirements-runtime.lock")]
    if target == "windows-py310":
        command += ["--platform", "win_amd64", "--python-version", "3.10", "--implementation", "cp", "--abi", "cp310"]
    subprocess.run(command, check=True)
    files = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(directory.rglob("*")) if p.is_file()}
    manifest = {"schema_version": "eeas.offline-dependencies.v1", "target": target,
                "python": "3.10", "architecture": "x86_64", "includes_agent_sdk": True,
                "source": "PyPI exact-version binary wheels; no maintainer build scripts",
                "core": core, "runtime": runtime, "files_sha256": files,
                "verification": "DOWNLOADED_NOT_NATIVE_VALIDATED",
                "live_model": "NOT_CONNECTED"}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Dependencies: {directory.relative_to(ROOT)}")
    return str(directory.relative_to(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["windows-py310", "linux-py310", "both"], default="both")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 10):
        parser.error("Resolve dependencies under the selected Python3.10 environment")
    targets = ["linux-py310", "windows-py310"] if args.target == "both" else [args.target]
    selected = {}
    for target in targets:
        core = closure(CORE, target)
        runtime = closure([*CORE, "claude-agent-sdk", "pip"], target)
        selected[target] = prepare(target, core, runtime)
        if target == "windows-py310":
            # Deployment locks apply to future Windows runs; old snapshots are preserved.
            (ROOT / "requirements-core.lock").write_text("".join(f"{k}=={v}\n" for k, v in sorted(core.items())))
            (ROOT / "requirements-runtime.lock").write_text("".join(f"{k}=={v}\n" for k, v in sorted(runtime.items())))
    # Reuse only completed, hash-verified packages from a partial previous preparation.
    from install_offline import verify
    for target in ("linux-py310", "windows-py310"):
        if target in selected:
            continue
        for manifest in sorted((ROOT / "vendor" / target).glob("*/manifest.json"), reverse=True):
            try:
                verify(manifest.parent, target)
            except (OSError, ValueError):
                continue
            selected[target] = str(manifest.parent.relative_to(ROOT))
            break
    (ROOT / "profiles/offline-dependencies.json").write_text(json.dumps({
        "schema_version": "eeas.offline-selection.v1", "targets": selected,
        "release_status": "DEVELOPMENT_ONLY", "native_windows": "NOT_VALIDATED"}, indent=2) + "\n")


if __name__ == "__main__":
    main()
