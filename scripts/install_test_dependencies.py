"""Install optional bundled tests into a fresh environment/cache, with no downloads."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True
from bundle_support import assert_no_links, canonical_json, digest_file, safe_name
from install_offline import install
from offline_test_support import extract_browser, verify_test_kit

ROOT = Path(__file__).resolve().parents[1]


def install_tests(root, runtime_root, test_root, destination, browser_cache):
    if sys.version_info[:2] != (3, 10):
        raise ValueError("Select Python3.10 explicitly")
    target = "windows-py310" if os.name == "nt" else "linux-py310"
    selected = json.loads((root / "profiles/offline-test-dependencies.json").read_text(encoding="utf-8"))
    test_relative = safe_name(selected["targets"][target])
    directory = test_root / test_relative
    manifest = verify_test_kit(directory, target)
    for path in (destination, browser_cache):
        assert_no_links(path)
        if os.path.lexists(path):
            raise ValueError("Test environment/cache exists; choose fresh paths")
    runtime = json.loads((root / "profiles/offline-dependencies.json").read_text(encoding="utf-8"))
    executable = install(runtime_root / safe_name(runtime["targets"][target]), destination)
    env = {**os.environ, "PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": os.devnull, "PIP_DISABLE_PIP_VERSION_CHECK": "1",
           "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    subprocess.run([str(executable), "-m", "pip", "install", "--isolated", "--no-index", "--no-cache-dir", "--only-binary=:all:",
                    "--find-links", str(directory / "wheels"), "-r", str(directory / "requirements-test.lock")], env=env, check=True)
    subprocess.run([str(executable), "-m", "pip", "check"], env=env, check=True)
    query = "import importlib.metadata,json,pathlib; d=importlib.metadata.distribution('playwright'); print(json.dumps({'version':d.version,'registry':str(d.locate_file('playwright/driver/package/browsers.json'))}))"
    actual = json.loads(subprocess.check_output([str(executable), "-c", query], env=env))
    if actual["version"] != manifest["versions"]["playwright"] or digest_file(Path(actual["registry"])) != manifest["browser"]["registry_sha256"]:
        raise ValueError("Installed Playwright registry differs from the pinned browser")
    extract_browser(directory, browser_cache, manifest)
    browser_env = {"PLAYWRIGHT_BROWSERS_PATH": str(browser_cache.absolute()), "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if target == "linux-py310":
        browser_env["LD_LIBRARY_PATH"] = str((directory / "system-libs").absolute())
    receipt = {"schema_version": "eeas.offline-test-install.v1", "target": target, "python": "3.10",
               "environment": str(destination), "executable": str(executable), "browser_cache": str(browser_cache),
               "test_manifest_sha256": digest_file(directory / "manifest.json"), "versions": manifest["versions"],
               "browser_revision": manifest["browser"]["revision"], "environment_variables": browser_env,
               "native_windows": "BLOCKED" if os.name != "nt" else "NOT_QUALIFIED_BY_INSTALL", "video": manifest["video"]}
    with (destination / "EEAS_TEST_ENV.json").open("xb") as stream:
        stream.write(canonical_json(receipt))
    if os.name == "nt":
        cmd = ["@echo off", "chcp 65001 >nul", "setlocal", *[f'set "{key}={value}"' for key, value in browser_env.items()],
               'set "PYTHONUTF8=1"', f'cd /d "{root.absolute()}"', f'"{executable.absolute()}" -m pytest %*', "exit /b %errorlevel%"]
        (destination / "run-tests.cmd").write_text("\r\n".join(cmd) + "\r\n", encoding="utf-8")
        ps = [*[f"$env:{key} = '{value.replace(chr(39), chr(39) * 2)}'" for key, value in browser_env.items()],
              "$env:PYTHONUTF8 = '1'", f"Set-Location -LiteralPath '{str(root.absolute()).replace(chr(39), chr(39) * 2)}'",
              f"& '{str(executable.absolute()).replace(chr(39), chr(39) * 2)}' -m pytest @args", "exit $LASTEXITCODE"]
        (destination / "run-tests.ps1").write_text("\n".join(ps) + "\n", encoding="utf-8-sig")
    print(json.dumps(receipt, ensure_ascii=False))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--test-root", type=Path)
    parser.add_argument("--destination", type=Path, default=ROOT / ".offline-probe/test-env")
    parser.add_argument("--browser-cache", type=Path, default=ROOT / ".offline-probe/test-browsers")
    args = parser.parse_args()
    try:
        install_tests(args.root, args.runtime_root or args.root, args.test_root or args.root, args.destination.absolute(), args.browser_cache.absolute())
        return 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print("Offline test installation failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
