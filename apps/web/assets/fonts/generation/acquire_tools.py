"""Developer-only: acquire an exact Python 3.10 WOFF2 build closure."""
from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from urllib.error import URLError
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent


def fetch(url: str, limit: int) -> bytes:
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in {"pypi.org", "files.pythonhosted.org"}:
        raise ValueError("unexpected package URL")
    for attempt in range(2):
        try:
            with urlopen(Request(url, headers={"User-Agent": "EEAS-font-tool-closure"}), timeout=30) as response:
                if urlsplit(response.url).hostname not in {"pypi.org", "files.pythonhosted.org"}:
                    raise ValueError("unexpected package host")
                raw = response.read(limit + 1)
            break
        except URLError as error:
            print(f"public acquisition attempt {attempt + 1} failed: {type(error.reason).__name__}", flush=True)
            if attempt == 1:
                # Public developer acquisition fallback; normal certificate validation
                # and no redirects remain enabled. Not an offline build dependency.
                print("retrying public package acquisition with curl TLS backend", flush=True)
                raw = subprocess.run(["curl", "--proto", "=https", "--fail", "--silent", "--show-error",
                                      "--max-time", "30", "--max-filesize", str(limit), url],
                                     check=True, stdout=subprocess.PIPE).stdout
    if len(raw) > limit:
        raise ValueError("tool download exceeds limit")
    return raw


def acquire() -> None:
    directory = ROOT / "wheels"
    directory.mkdir(exist_ok=True)
    records = []
    requirements = []
    for package, version in (("fonttools", "4.65.0"), ("Brotli", "1.2.0")):
        metadata_url = f"https://pypi.org/pypi/{package.lower()}/{version}/json"
        metadata = json.loads(fetch(metadata_url, 2 * 1024 * 1024))
        candidates = [item for item in metadata["urls"] if (
            package == "fonttools" and item["filename"].endswith("py3-none-any.whl")
            or package == "Brotli" and "cp310-cp310" in item["filename"] and (
                "win_amd64" in item["filename"] or "manylinux" in item["filename"] and "x86_64" in item["filename"]))]
        if len(candidates) != (1 if package == "fonttools" else 2):
            raise ValueError("unexpected offline build wheel selection")
        hashes = []
        for item in candidates:
            target = directory / item["filename"]
            raw = target.read_bytes() if target.exists() else fetch(item["url"], 8 * 1024 * 1024)
            digest = hashlib.sha256(raw).hexdigest()
            if digest != item["digests"]["sha256"] or len(raw) != item["size"]:
                raise ValueError("public package digest mismatch")
            if not target.exists():
                with target.open("xb") as handle:
                    handle.write(raw)
            license_directory = ROOT / "licenses" / item["filename"]
            with zipfile.ZipFile(target) as handle:
                license_members = [info for info in handle.infolist() if ".dist-info/" in info.filename
                                   and ("/licenses/" in info.filename or Path(info.filename).name.lower().startswith(("license", "copying", "notice")))
                                   and not info.is_dir()]
                if not license_members:
                    raise ValueError("build wheel lacks retained licence")
                licenses = []
                for index, info in enumerate(license_members):
                    data = handle.read(info)
                    destination = license_directory / f"{index}-{Path(info.filename).name}"
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        if destination.read_bytes() != data:
                            raise ValueError("existing original licence bytes differ")
                    else:
                        with destination.open("xb") as output:
                            output.write(data)
                    licenses.append({"original_member": info.filename, "path": destination.relative_to(ROOT).as_posix(),
                                     "sha256": hashlib.sha256(data).hexdigest()})
            records.append({"package": package, "version": version, "path": target.relative_to(ROOT).as_posix(),
                            "url": item["url"], "metadata_url": metadata_url,
                            "sha256": digest, "byte_length": len(raw), "licenses": licenses})
            hashes.append(f"--hash=sha256:{digest}")
            print(f"verified official wheel {target.name}: {len(raw)} bytes", flush=True)
        requirements.append(f"{package}=={version} " + " ".join(hashes))
    with (ROOT / "requirements.txt").open("x", encoding="utf-8") as handle:
        handle.write("\n".join(requirements) + "\n")
    with (ROOT / "tools-manifest.json").open("x", encoding="utf-8") as handle:
        json.dump({"schema": "eeas.offline-font-tools.v1", "targets": ["CPython 3.10 Linux x86_64", "CPython 3.10 Windows AMD64"],
                   "runtime_required": False, "files": records, "native_windows_generation": "NOT_RUN"}, handle, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    acquire()
