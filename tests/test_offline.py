import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("offline", Path(__file__).parents[1] / "scripts/install_offline.py")
offline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offline)


def make_manifest(path):
    wheel = path / "wheels/original.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"original fixture for integrity checks; not installed")
    manifest = {"target": "linux-py310", "python": "3.10",
                "files_sha256": {"wheels/original.whl": hashlib.sha256(wheel.read_bytes()).hexdigest()}}
    (path / "manifest.json").write_text(json.dumps(manifest))
    return wheel, manifest


def test_hash_changes_and_unlisted_wheels_rejected_before_install(tmp_path):
    wheel, manifest = make_manifest(tmp_path)
    assert offline.verify(tmp_path, "linux-py310") == manifest
    wheel.write_bytes(b"tampered fixture")
    with pytest.raises(ValueError, match="hash mismatch"):
        offline.verify(tmp_path, "linux-py310")
    wheel.write_bytes(b"original fixture for integrity checks; not installed")
    (tmp_path / "wheels/extra.whl").write_bytes(b"extra")
    with pytest.raises(ValueError, match="Unlisted"):
        offline.verify(tmp_path, "linux-py310")


def test_wrong_target_and_manifest_escape_are_rejected(tmp_path):
    wheel, manifest = make_manifest(tmp_path)
    with pytest.raises(ValueError, match="mismatch"):
        offline.verify(tmp_path, "windows-py310")
    manifest["files_sha256"]["../../escape"] = "0" * 64
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="escapes"):
        offline.verify(tmp_path, "linux-py310")
