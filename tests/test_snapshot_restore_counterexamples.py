"""Actual small original snapshot bytes; no upstream/company/native qualification."""
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

import pytest

from conftest import ROOT
from packages.domain.store import Store

sys.path.insert(0, str(ROOT / "scripts"))
from bundle_support import MANIFEST, canonical_json, digest_file
from snapshot_data import SCHEMA, backup, restore


def test_actual_snapshot_bytes_schema_and_migration_guards_preserve_originals(tmp_path):
    evidence = Path(os.environ.get("EEAS_EVIDENCE_DIR", tmp_path)) / "actual-snapshot-counterexamples"
    evidence.mkdir(exist_ok=False)
    data = tmp_path / "original snapshot data"
    store = Store(data / "eeas.sqlite3")
    project = store.create_project("Original snapshot counterexample", "synthetic")
    raw = "原创恢复材料\n尾端保留\n".encode("utf-8")
    document = store.save_document(project["id"], "original.txt", raw, "synthetic")
    artifact = data / "workspaces/original/candidate.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"original immutable output\n")
    original = evidence / "original-snapshot.zip"
    receipt = backup(data, original)
    original_hash = digest_file(original)
    assert receipt["sha256"] == original_hash
    with zipfile.ZipFile(original) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members[MANIFEST])
    assert manifest["schema_version"] == SCHEMA
    assert all(digest_file(ROOT / "packages/domain/migrations" / name) == sha
               for name, sha in manifest["migrations_sha256"].items())
    (evidence / "original-manifest.json").write_bytes(members[MANIFEST])
    facts = {"source_kind": "synthetic", "runtime_kind": "none", "real_model_calls": 0,
             "source_sha256": {name: digest_file(ROOT / name) for name in
                               ("scripts/snapshot_data.py", "scripts/bundle_support.py", "packages/domain/store.py")},
             "snapshot_receipt": receipt, "original_snapshot_sha256": original_hash, "rejections": []}

    def reject(name, archive, expected):
        destination = tmp_path / name
        with pytest.raises(ValueError) as actual:
            restore(archive, destination, expected)
        assert not destination.exists()
        facts["rejections"].append({"name": name, "actual_archive_sha256": digest_file(archive),
                                    "supplied_sha256": expected, "actual_exception": str(actual.value),
                                    "destination_created": destination.exists()})

    reject("wrong-external-sha", original, "0" * 64)
    for name in ("tampered-member", "wrong-schema", "changed-migration-code"):
        changed = dict(members)
        metadata = json.loads(members[MANIFEST])
        if name == "tampered-member":
            changed["workspaces/original/candidate.txt"] += b"original tamper fixture\n"
        elif name == "wrong-schema":
            metadata["schema_version"] = "eeas.original-unknown-snapshot.v999"
            changed[MANIFEST] = canonical_json(metadata)
        else:
            first = next(iter(metadata["migrations_sha256"]))
            metadata["migrations_sha256"][first] = "0" * 64
            changed[MANIFEST] = canonical_json(metadata)
        modified = evidence / (name + ".zip")
        with zipfile.ZipFile(modified, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for member, body in changed.items():
                archive.writestr(member, body)
        reject(name, modified, digest_file(modified))

    destination = tmp_path / "fresh correct restore"
    facts["actual_restore_receipt"] = restore(original, destination, original_hash)
    restored = Store(destination / "eeas.sqlite3")
    assert restored.project(project["id"]) == project
    with restored.connect() as connection:
        actual_raw = bytes(connection.execute("SELECT raw_bytes FROM document_versions WHERE document_id=?",
                                             (document["id"],)).fetchone()[0])
    assert actual_raw == raw
    assert (destination / artifact.relative_to(data)).read_bytes() == artifact.read_bytes()
    assert digest_file(original) == original_hash
    facts["restored_raw_sha256"] = hashlib.sha256(actual_raw).hexdigest()
    facts["original_snapshot_after_sha256"] = digest_file(original)
    facts["qualification"] = {"company_model": "NOT_RUN", "windows_native": "BLOCKED", "os_isolation": "NOT_QUALIFIED"}
    with (evidence / "actual-facts.json").open("x", encoding="utf-8") as stream:
        json.dump(facts, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
