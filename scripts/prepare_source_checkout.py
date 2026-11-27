"""Materialize the pinned public inputs used by a Cirven source checkout."""
from __future__ import annotations

import base64
import hashlib
import json
import posixpath
import shutil
import tarfile
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
GHOSTTY_URL = "https://registry.npmjs.org/ghostty-web/-/ghostty-web-0.3.0.tgz"
GHOSTTY_INTEGRITY = (
    "sha512-SAdSHWYF20GMZUB0n8kh1N6Z4ljMnuUqT8iTB2n5FAPswEV10MejEpLlhW/"
    "769GL5+BQa1NYwEg9y/XCckV5+A=="
)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def download(url, destination, integrity):
    print(f"Downloading {url}", flush=True)
    algorithm, checksum = integrity.split("-", 1)
    digest = hashlib.new(algorithm)
    with urlopen(Request(url, headers={"User-Agent": "Cirven source checkout"}), timeout=90) as response:
        with destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
    actual = digest.hexdigest() if algorithm == "sha256" else base64.b64encode(digest.digest()).decode()
    if actual != checksum:
        raise ValueError(f"Archive checksum mismatch: {url}")


def extract_entry(archive, member, output, checksum=None, size=None):
    if not member.isfile() or (size is not None and member.size != size):
        raise ValueError(f"Unexpected archive member: {member.name}")
    with archive.extractfile(member) as source:
        data = source.read()
    if checksum and hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError(f"Member checksum mismatch: {member.name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.is_file() or output.read_bytes() != data:
        output.write_bytes(data)


def extract_npm_package(archive_path, destination):
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            if member.isdir():
                continue
            path = PurePosixPath(member.name)
            if len(path.parts) < 2 or path.parts[0] != "package" or ".." in path.parts or "\\" in member.name:
                raise ValueError(f"Unexpected npm archive path: {member.name}")
            extract_entry(archive, member, destination.joinpath(*path.parts[1:]))


def prepare_opencode(scratch):
    manifest_file = ROOT / "toolchain/source-inputs/opencode.json"
    manifest = load(manifest_file)
    commit = manifest["commit"]
    destination = ROOT / "staging-native-ui"
    archive_path = scratch / "opencode.tgz"
    download(
        f"https://codeload.github.com/anomalyco/opencode/tar.gz/{commit}",
        archive_path, f"sha256-{manifest['archive_sha256']}",
    )
    prefix = f"opencode-{commit}"
    pending = {posixpath.join(prefix, item["path"]): item for item in manifest["files"]}
    client = "packages/app/vendor/opencode-ai-client-1.17.13-v2.tgz"
    if f"{prefix}/{client}" not in pending:
        pending[f"{prefix}/{client}"] = {"path": client, "sha256": None, "bytes": None}
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            if item := pending.pop(member.name, None):
                extract_entry(archive, member, destination / "upstream" / item["path"], item["sha256"], item["bytes"])
    if pending:
        raise ValueError(f"Missing upstream inputs: {', '.join(pending)}")
    shutil.copyfile(manifest_file, destination / "source-manifest.json")



def prepare_fonts(scratch):
    from fontTools.ttLib import TTFont

    root = ROOT / "apps/web/assets/fonts"
    originals = {Path(item["path"]).name: item for item in load(root / "sources.json")["files"]}
    faces = load(root / "manifest.json")["fonts"]
    for face in faces:
        original = originals[Path(face["original_path"]).name]
        prepare_font(scratch, root / face["woff2_path"], face["woff2_sha256"], original["url"], original["sha256"], TTFont)

    serif = load(root / "font-source-manifest-noto-serif-sc.json")
    prepare_font(scratch, root / serif["woff2_filename"], serif["woff2_sha256"],
                 serif["official_ttf_url"], serif["original_ttf_sha256"], TTFont)


def prepare_font(scratch, output, expected, url, source_sha256, font_type):
    if output.is_file() and hashlib.sha256(output.read_bytes()).hexdigest() == expected:
        return
    source = scratch / "font-original.ttf"
    result = scratch / "font-converted.woff2"
    download(url, source, f"sha256-{source_sha256}")
    with font_type(source, recalcTimestamp=False) as font:
        font.flavor = "woff2"
        font.save(result)
    if hashlib.sha256(result.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Generated font checksum mismatch: {output.name}")
    shutil.copyfile(result, output)


def main():
    with TemporaryDirectory(prefix="cirven-dependencies-") as directory:
        scratch = Path(directory)
        prepare_fonts(scratch)
        prepare_opencode(scratch)
        ghostty = scratch / "ghostty.tgz"
        download(GHOSTTY_URL, ghostty, GHOSTTY_INTEGRITY)
        extract_npm_package(ghostty, ROOT / "staging-native-ui/ghostty/upstream")

        diagram = ROOT / "packages/adapters/diagram_layout_assets"
        elk = load(diagram / "UPSTREAM.json")
        elk_worker = load(diagram / "TASK_WORKER_UPSTREAM.json")
        archive_path = scratch / "elk.tgz"
        download(elk["tarball_url"], archive_path, elk["registry_integrity"])
        with tarfile.open(archive_path, "r:gz") as archive:
            for name, metadata in (("elk.bundled.js", elk), ("elk-worker.min.js", elk_worker)):
                spec = metadata["files"][name]
                extract_entry(archive, archive.getmember(f"package/lib/{name}"), diagram / name, spec["sha256"], spec["bytes"])

        desktop = ROOT / "apps/desktop-host/vendor"
        marked = load(desktop / "marked-provenance.json")
        archive_path = scratch / "marked.tgz"
        download(marked["registry"], archive_path, marked["registry_integrity"])
        with tarfile.open(archive_path, "r:gz") as archive:
            member = archive.getmember(f"package/{marked['source']}")
            extract_entry(archive, member, desktop / "marked.cjs", marked["source_sha256"])

        vendor = ROOT / "toolchain/opencode-plugins/dynamic-workflow/vendor"
        zod = load(vendor / "ZOD_SOURCE.json")
        archive_path = scratch / "zod.tgz"
        download(zod["source_url"], archive_path, zod["registry_integrity"])
        extract_npm_package(archive_path, vendor / "zod")


if __name__ == "__main__":
    main()
