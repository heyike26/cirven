import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from bundle_support import canonical_json, digest_file
from offline_test_support import browser_members, extract_browser, verify_test_kit
from build_release_bundle import build
from test_release_bundle import minimal_repository


def original_kit(directory, target="windows-py310"):
    (directory / "wheels").mkdir(parents=True)
    (directory / "browser").mkdir()
    with zipfile.ZipFile(directory / "wheels/original-1.0-py3-none-any.whl", "w") as output:
        output.writestr("original-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: original\nVersion: 1.0\nLicense: MIT\n")
        output.writestr("original-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        output.writestr("original-1.0.dist-info/licenses/LICENSE", "Original synthetic test licence, no third-party content")
    (directory / "requirements-test.lock").write_text("original==1.0\n")
    top = "chrome-headless-shell-win64" if target == "windows-py310" else "chrome-headless-shell-linux64"
    filename = "chrome-headless-shell.exe" if target == "windows-py310" else "chrome-headless-shell"
    archive = directory / "browser/headless-shell.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(top + "/" + filename, b"original browser fixture, never executed")
        output.writestr(top + "/LICENSE.txt", b"original synthetic notice, not a third-party licence")
    manifest = {"schema_version": "eeas.offline-tests.v1", "target": target, "python": "3.10", "architecture": "x86_64",
                "versions": {"original": "1.0"}, "browser": {"kind": "chromium-headless-shell", "revision": "1243",
                    "archive": "browser/headless-shell.zip", "files": browser_members(archive)},
                "files_sha256": {p.relative_to(directory).as_posix(): digest_file(p) for p in directory.rglob("*") if p.is_file()}}
    (directory / "manifest.json").write_bytes(canonical_json(manifest))
    return manifest


def rewritten(directory, manifest):
    manifest["files_sha256"] = {p.relative_to(directory).as_posix(): digest_file(p) for p in directory.rglob("*") if p.is_file() and p.name != "manifest.json"}
    (directory / "manifest.json").write_bytes(canonical_json(manifest))


def test_correct_registry_layout_no_cache_overwrite(tmp_path):
    directory = tmp_path / "original"
    manifest = original_kit(directory)
    assert verify_test_kit(directory, "windows-py310") == manifest
    cache = tmp_path / "中文 浏览器 cache"
    extract_browser(directory, cache, manifest)
    exe = cache / "chromium_headless_shell-1243/chrome-headless-shell-win64/chrome-headless-shell.exe"
    assert exe.read_bytes() == b"original browser fixture, never executed"
    with pytest.raises(ValueError, match="exists"):
        extract_browser(directory, cache, manifest)


@pytest.mark.parametrize("line", ["original @ https://external.invalid/test.whl\n", "--index-url https://external.invalid\n", "original==*\n"])
def test_install_lock_cannot_download_urls_or_accept_external_options(tmp_path, line):
    directory = tmp_path / "original"
    manifest = original_kit(directory)
    (directory / "requirements-test.lock").write_text(line)
    rewritten(directory, manifest)
    with pytest.raises(ValueError, match="exact pins"):
        verify_test_kit(directory, "windows-py310")


@pytest.mark.parametrize("names,mode", [(["../outside"], 0), (["chrome-headless-shell-win64/link"], stat.S_IFLNK | 0o777),
                                       (["chrome-headless-shell-win64/File", "chrome-headless-shell-win64/file"], 0)])
def test_unsafe_browser_artifact_cannot_create_cache(tmp_path, names, mode):
    directory = tmp_path / "original"
    manifest = original_kit(directory)
    archive = directory / "browser/headless-shell.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for name in names:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = mode << 16
            output.writestr(info, b"original malicious fixture")
    rewritten(directory, manifest)
    with pytest.raises(ValueError):
        verify_test_kit(directory, "windows-py310")
    with pytest.raises(ValueError):
        extract_browser(directory, tmp_path / "cache", manifest)
    assert not (tmp_path / "cache").exists()


def test_browser_bomb_and_unlisted_nested_manifest_rejected(tmp_path):
    directory = tmp_path / "original"
    manifest = original_kit(directory)
    (directory / "browser/manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="Unlisted"):
        verify_test_kit(directory, "windows-py310")
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("chrome-headless-shell-win64/large", b"A" * (2 * 1024 * 1024))
    with pytest.raises(ValueError, match="ratio"):
        browser_members(archive)


@pytest.mark.parametrize("tag", ["cp310-cp310-manylinux_2_28_x86_64", "cp311-cp311-win_amd64"])
def test_windows_kit_rejects_linux_and_wrong_python_wheel(tmp_path, tag):
    directory = tmp_path / "original"
    manifest = original_kit(directory)
    with zipfile.ZipFile(directory / "wheels/original-1.0-py3-none-any.whl", "w") as output:
        output.writestr("original-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: original\nVersion: 1.0\n")
        output.writestr("original-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: " + tag + "\n")
    rewritten(directory, manifest)
    with pytest.raises(ValueError, match="incompatible"):
        verify_test_kit(directory, "windows-py310")


def test_runtime_license_inventory_not_overwritten_by_optional_test_kit(tmp_path):
    root, wheels, evidence = minimal_repository(tmp_path)
    relative = "vendor/test-windows-py310/original"
    directory = wheels / relative
    manifest = original_kit(directory)
    # Actual wheel-shaped original licence fixture; no installation.
    (directory / "wheels/original-1.0-py3-none-any.whl").unlink()
    wheel = directory / "wheels/original_test-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as output:
        output.writestr("original-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: original-test\nVersion: 1.0\nLicense: MIT\n")
        output.writestr("original-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        output.writestr("original-1.0.dist-info/licenses/LICENSE", "Original test licence fixture")
    (directory / "requirements-test.lock").write_text("original-test==1.0\n")
    manifest["versions"] = {"original-test": "1.0"}
    rewritten(directory, manifest)
    (root / "profiles/offline-test-dependencies.json").write_bytes(canonical_json({"targets": {"windows-py310": relative}}))
    result = build(root, "windows-py310", wheels, [evidence], tmp_path / "preview.zip", test_root=wheels)
    with zipfile.ZipFile(result["path"]) as archive:
        runtime = json.loads(archive.read("release/licenses/packages.json"))
        test = json.loads(archive.read("release/licenses/tests/packages.json"))
        assert runtime["packages"][0]["name"] == "original"
        assert test["packages"][0]["name"] == "original-test"
        assert "bundled_cli" in runtime and "bundled_cli" not in test
        assert relative + "/browser/headless-shell.zip" in archive.namelist()


def test_prepared_public_wheel_tags_and_actual_windows_kit_in_bundle(tmp_path):
    selected_path = ROOT / "profiles/offline-test-dependencies.json"
    if not selected_path.is_file():
        pytest.skip("Prepare public optional artifacts first")
    selection = json.loads(selected_path.read_bytes())
    for target, relative in selection["targets"].items():
        if not (ROOT / relative / "browser/headless-shell.zip").is_file():
            pytest.skip("Public artifact payload is not present")
        manifest = verify_test_kit(ROOT / relative, target)
        assert manifest["versions"]["playwright"] == "1.63.0"
        assert manifest["versions"]["pytest"] == "9.1.1"
        assert manifest["browser"]["revision"] == "1243"
    root, runtime, evidence = minimal_repository(tmp_path)
    relative = selection["targets"]["windows-py310"]
    (root / "profiles/offline-test-dependencies.json").write_bytes(canonical_json(selection))
    # Original minimal source/runtime fixture carrying actual public Windows test
    # artifacts; this verifies packaging, never claims Windows execution.
    result = build(root, "windows-py310", runtime, [evidence], tmp_path / "public-kit.zip", test_root=ROOT)
    assert result["optional_test_wheels"] == 12
    assert result["headless_shell_revision"] == "1243"
    with zipfile.ZipFile(result["path"]) as archive:
        combined = json.loads(archive.read("release/combination-lock.json"))
        assert relative + "/manifest.json" in combined["dependency_locks_sha256"]
        assert combined["optional_test_versions"]["pytest"] == "9.1.1"
        assert relative + "/browser/headless-shell.zip" in archive.namelist()
        assert not any(name.startswith(relative + "/system-libs/") for name in archive.namelist())
        packages = json.loads(archive.read("release/licenses/tests/packages.json"))
        assert len(packages["packages"]) == 12
        assert "bundled_cli" not in packages


def test_real_pinned_platform_kits_and_fresh_offline_test_environment(tmp_path):
    selected_path = ROOT / "profiles/offline-test-dependencies.json"
    if not selected_path.is_file():
        pytest.skip("Run preparation first; optional target artifacts are not present")
    selection = json.loads(selected_path.read_bytes())
    for target, relative in selection["targets"].items():
        if not (ROOT / relative / "browser/headless-shell.zip").is_file():
            pytest.skip("Target artifact bytes must be locally present for this qualification")
        manifest = verify_test_kit(ROOT / relative, target)
        assert manifest["versions"]["playwright"] == "1.63.0"
        assert manifest["versions"]["pytest"] == "9.1.1"
        assert manifest["browser"]["revision"] == "1243"
    if os.name == "nt":
        pytest.skip("The current receipt qualifies WSL/Linux only; run native Windows installation separately")
    runtime_root = Path(os.environ.get("EEAS_PUBLIC_WHEEL_ROOT", str(ROOT)))
    runtime = json.loads((ROOT / "profiles/offline-dependencies.json").read_bytes())["targets"]["linux-py310"]
    if not (runtime_root / runtime / "wheels").is_dir():
        pytest.skip("Prepared production runtime bytes are required")
    envdir = tmp_path / "中文 测试 environment"
    cache = tmp_path / "中文 浏览器 cache"
    evidence = Path(os.environ.get("EEAS_EVIDENCE_DIR", str(tmp_path)))
    command = [sys.executable, "-B", str(ROOT / "scripts/install_test_dependencies.py"), "--root", str(ROOT),
               "--runtime-root", str(runtime_root), "--test-root", str(ROOT), "--destination", str(envdir), "--browser-cache", str(cache)]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "tests/fixtures/offline_guard"), str(ROOT)]),
           "PIP_NO_INDEX": "1", "PIP_INDEX_URL": "http://127.0.0.1:1/unavailable", "PYTHONDONTWRITEBYTECODE": "1"}
    with (evidence / "test-install-stdout.txt").open("xb") as stdout, (evidence / "test-install-stderr.txt").open("xb") as stderr:
        completed = subprocess.run(command, env=env, stdout=stdout, stderr=stderr, timeout=180, check=False)
    assert completed.returncode == 0, (evidence / "test-install-stderr.txt").read_text()
    receipt = json.loads((envdir / "EEAS_TEST_ENV.json").read_bytes())
    env.update(receipt["environment_variables"])
    smoke = "import pytest; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); page=b.new_page(); page.set_content('<title>原创离线测试</title>'); assert page.title()=='原创离线测试'; b.close(); p.stop(); print('pytest '+pytest.__version__+' / pinned Playwright default headless: PASS')"
    with (evidence / "test-smoke-stdout.txt").open("xb") as stdout, (evidence / "test-smoke-stderr.txt").open("xb") as stderr:
        completed = subprocess.run([str(envdir / "bin/python"), "-c", smoke], env=env, stdout=stdout, stderr=stderr, timeout=45, check=False)
    assert completed.returncode == 0, (evidence / "test-smoke-stderr.txt").read_text()
    with (evidence / "test-gui-stdout.txt").open("xb") as stdout, (evidence / "test-gui-stderr.txt").open("xb") as stderr:
        completed = subprocess.run([str(envdir / "bin/python"), "-m", "pytest", "tests/test_web.py::test_real_browser_create_refresh_and_process_restart", "-q"],
                                   cwd=ROOT, env=env, stdout=stdout, stderr=stderr, timeout=90, check=False)
    assert completed.returncode == 0, (evidence / "test-gui-stdout.txt").read_text() + (evidence / "test-gui-stderr.txt").read_text()
