"""Install only bundled, hash-checked dependencies into a fresh environment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import venv

try:
    from bundle_support import assert_no_links, digest_file, safe_name, validate_names
except ModuleNotFoundError:
    from scripts.bundle_support import assert_no_links, digest_file, safe_name, validate_names

ROOT = Path(__file__).resolve().parents[1]


def verify(directory: Path, target: str) -> dict:
    assert_no_links(directory)
    directory = directory.resolve()
    assert_no_links(directory / "manifest.json")
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest["target"] != target or manifest["python"] != "3.10":
        raise ValueError("Dependency platform/Python version mismatch")
    if not manifest.get("files_sha256"):
        raise ValueError("Dependency manifest is empty")
    try:
        validate_names(manifest["files_sha256"])
    except ValueError as exc:
        raise ValueError("Dependency path escapes or is unsafe") from exc
    for name, expected in manifest["files_sha256"].items():
        path = directory / name
        assert_no_links(path)
        if path.is_symlink() or not path.resolve().is_relative_to(directory):
            raise ValueError("Dependency path escapes its bundle")
        if not path.is_file() or digest_file(path) != expected:
            raise ValueError(f"Dependency hash mismatch: {name}")
    expected_wheels = {name for name in manifest["files_sha256"] if name.startswith("wheels/")}
    actual_wheels = {p.relative_to(directory).as_posix() for p in (directory / "wheels").glob("*")}
    if not expected_wheels or actual_wheels != expected_wheels:
        raise ValueError("Unlisted or missing dependency wheels")
    for group in ("core", "runtime"):
        if group not in manifest:
            continue
        lock = directory / f"requirements-{group}.lock"
        if lock.name not in manifest["files_sha256"]:
            raise ValueError("Missing hash-checked dependency lock")
        pins = {}
        for line in lock.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)", line)
            if not match:
                raise ValueError("Offline lock must contain exact pins only; URLs/options are forbidden")
            name = re.sub(r"[-_.]+", "-", match[1]).lower()
            if name in pins:
                raise ValueError("Duplicate dependency pin")
            pins[name] = match[2]
        expected_pins = {re.sub(r"[-_.]+", "-", name).lower(): version for name, version in manifest[group].items()}
        if pins != expected_pins:
            raise ValueError("Dependency lock and manifest versions disagree")
    return manifest


def install(directory: Path, destination: Path, include_agent: bool = True,
            office_directory: Path | None = None) -> Path:
    if sys.version_info[:2] != (3, 10):
        raise ValueError("Use Python3.10 explicitly (Windows: py -3.10)")
    target = "windows-py310" if os.name == "nt" else "linux-py310"
    runtime = verify(directory, target)
    if office_directory is not None:
        try:
            from install_pptx_dependencies import verify_bundle
        except ModuleNotFoundError:
            from scripts.install_pptx_dependencies import verify_bundle
        office = verify_bundle(office_directory, target)
        versions = {re.sub(r'[-_.]+', '-', n).lower(): v
                    for n, v in runtime['runtime' if include_agent else 'core'].items()}
        for package in office['packages']:
            name = re.sub(r'[-_.]+', '-', package['name']).lower()
            if name in versions and versions[name] != package['version']:
                raise ValueError('PPTX/runtime version conflict before environment creation: ' + name)
    assert_no_links(destination)
    if os.path.lexists(destination):
        raise ValueError("Destination exists; select a fresh environment instead of replacing a running version")
    destination.mkdir(parents=True, exist_ok=False)
    # Managed/minimal Python may omit ensurepip. Bootstrap from the bundled pip wheel.
    # Relocatable standalone Python must retain its base-library link on POSIX.
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(destination)
    executable = destination / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    lock = directory / ("requirements-runtime.lock" if include_agent else "requirements-core.lock")
    env = {**os.environ, "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
           "PIP_CONFIG_FILE": os.devnull}
    pip_wheels = list((directory / "wheels").glob("pip-*.whl"))
    if len(pip_wheels) != 1:
        raise ValueError("Expected one bundled pip wheel for offline bootstrap")
    bootstrap = "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('pip',run_name='__main__')"
    subprocess.run([str(executable), "-c", bootstrap, str(pip_wheels[0]), "install", "--isolated",
                    "--no-index", "--no-cache-dir", "--ignore-installed", "--only-binary=:all:",
                    "--find-links", str(directory / "wheels"), f"pip=={json.loads((directory / 'manifest.json').read_text())['runtime']['pip']}"],
                   env=env, check=True)
    subprocess.run([str(executable), "-m", "pip", "install", "--isolated", "--no-index",
                    "--no-cache-dir", "--only-binary=:all:", "--find-links", str(directory / "wheels"),
                    "-r", str(lock)], env=env, check=True)
    if office_directory is not None:
        subprocess.run([str(executable), '-m', 'pip', '--isolated', 'install', '--no-index',
                        '--no-deps', '--no-cache-dir', '--require-hashes', '--only-binary=:all:',
                        '--find-links', str(office_directory / 'wheels'),
                        '-r', str(office_directory / 'requirements.lock')], env=env, check=True)
    subprocess.run([str(executable), "-m", "pip", "check"], env=env, check=True)
    print("Offline environment ready:", executable)
    return executable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--destination", type=Path, default=ROOT / ".venv")
    parser.add_argument("--core-only", action="store_true")
    args = parser.parse_args()
    try:
        target = "windows-py310" if os.name == "nt" else "linux-py310"
        wheelhouse = args.wheelhouse
        if wheelhouse is None:
            selected = json.loads((ROOT / "profiles/offline-dependencies.json").read_text())
            wheelhouse = ROOT / selected["targets"][target]
        office = None
        if (ROOT / 'packages/domain/pptx_import.py').is_file():
            try:
                from install_pptx_dependencies import selected_bundle
            except ModuleNotFoundError:
                from scripts.install_pptx_dependencies import selected_bundle
            office, _, _ = selected_bundle(ROOT, target)
        install(wheelhouse.absolute(), args.destination.absolute(), not args.core_only, office)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exception:
        print(f"Offline installation failed: {exception}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
