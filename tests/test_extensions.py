import io
import json
from pathlib import Path
import sqlite3
import stat
import zipfile

import pytest
from conftest import ROOT
from packages.domain.store import Conflict, InvalidInput
from packages.domain.tasks import TaskService
from packages.extensions.schema import fixture_manifest
from packages.extensions.service import ExtensionRegistry
from test_documents import project


def archive(version, mutate=None):
    files = {"manifest.json": json.dumps(fixture_manifest(version)).encode(),
             "plugin.py": (ROOT / "packages/extensions/fixtures" / ("v" + str(version)) / "plugin.py").read_bytes(),
             "SKILL.md": b"# Original test skill\nDoes not grant any permission.\n"}
    if mutate:
        mutate(files)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, raw in files.items():
            output.writestr(name, raw)
    return stream.getvalue()


def registry(tmp_path):
    return ExtensionRegistry(TaskService(tmp_path / "eeas.sqlite3", ROOT), tmp_path)


@pytest.mark.parametrize('field', ['native_parameters', 'parameter_schema'])
@pytest.mark.parametrize('number', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_manifest_cannot_poison_registry_api(client, field, number):
    def malformed(files):
        manifest = fixture_manifest(1)
        manifest[field] = {'future_namespace': {'value': number}}
        files['manifest.json'] = json.dumps(manifest).encode()
    response = client.post('/api/extensions', files={'file': ('original-nonfinite.zip', archive(1, malformed), 'application/zip')})
    assert response.status_code == 422
    listing = client.get('/api/extensions')
    assert listing.status_code == 200
    assert listing.json()['extensions'] == []


def activate(reg, snapshot):
    review = reg.review(snapshot["id"])
    reg.approve(review["id"], review["action_hash"], review["action"]["permissions"])
    return review


def test_original_v1_v2_real_load_call_fixed_binding_permissions_and_rollback(client, tmp_path):
    reg = registry(tmp_path)
    pid = project(client)
    v1 = reg.import_snapshot(archive(1))
    assert v1["supported"] and not v1["loaded"] and not v1["qualified"]
    assert reg.import_snapshot(archive(1))["id"] == v1["id"]
    v1 = reg.qualify(v1["id"])
    assert v1["qualified"] and v1["loaded"]
    first = activate(reg, v1)
    frozen = reg.pin(pid, v1["id"])
    assert reg.invoke(frozen["lock"]["snapshot_id"], "original")['result'] == 'v1|original'
    v2 = reg.qualify(reg.import_snapshot(archive(2))["id"])
    with pytest.raises(Conflict, match="explicitly approved"):
        reg.invoke(v2["id"], "original")
    review = reg.review(v2["id"])
    assert review["action"]["new_permissions"] == ["text.length"]
    with pytest.raises(Conflict, match="permission"):
        reg.approve(review["id"], review["action_hash"], ["text.format"])
    reg.approve(review["id"], review["action_hash"], review["action"]["permissions"])
    changed = reg.invoke(v2["id"], "中文")
    assert changed["result"] == {"format": "v2", "text": "中文", "length": 2}
    assert reg.binding(pid) == frozen
    assert reg.invoke(frozen["lock"]["snapshot_id"], "original")['result'] == 'v1|original'
    activate(reg, v1)
    assert reg.describe(v1["id"])["channel"] == "STABLE"
    assert reg.describe(v2["id"])["channel"] == "CANDIDATE"
    # A delayed repeat of a consumed v2 approval must not undo the rollback.
    reg.approve(review["id"], review["action_hash"], review["action"]["permissions"])
    assert reg.describe(v1["id"])["channel"] == "STABLE"
    with reg.service.connect() as connection:
        evidence = connection.execute("SELECT payload FROM extension_evidence WHERE snapshot_id=?", (v2["id"],)).fetchone()
        proof = json.loads(evidence[0])
        assert proof["actual_call"]["result"]["format"] == "v2"
        assert all(run["receipt"]["exit_code"] == 0 for run in proof["runs"])


def test_tampered_missing_unloaded_and_same_version_changed_are_not_available(tmp_path):
    reg = registry(tmp_path)
    v1 = reg.qualify(reg.import_snapshot(archive(1))["id"])
    review = reg.review(v1["id"])
    with pytest.raises(Conflict, match="cannot change bytes"):
        reg.import_snapshot(archive(1, lambda files: files.update({"plugin.py": b"raise RuntimeError('changed')"})))
    row = reg._row(v1["id"])
    Path(row["directory"], "plugin.py").unlink()
    actual = reg.describe(v1["id"])
    assert actual["integrity"] == "INTEGRITY_ERROR" and not actual["loaded"] and not actual["qualified"]
    with pytest.raises(Conflict):
        reg.approve(review["id"], review["action_hash"], review["action"]["permissions"])
    with reg.service.connect() as connection, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE extension_snapshots SET version='3'")


def test_unknown_native_namespace_is_preserved_but_never_executes(tmp_path):
    reg = registry(tmp_path)
    def untrusted(files):
        m = fixture_manifest(2)
        m.update(provider="other.sdk.plugin", adapter="sdk-native", native_parameters={"future_feature": {"option": 123}})
        files["manifest.json"] = json.dumps(m).encode()
        files["plugin.py"] = b"from pathlib import Path; Path('BAD').write_text('should not execute')"
    candidate = reg.import_snapshot(archive(2, untrusted))
    assert candidate["manifest"]["native_parameters"]["future_feature"]["option"] == 123
    assert not candidate["supported"] and not candidate["loaded"] and not candidate["qualified"]
    with pytest.raises(InvalidInput, match="cannot execute"):
        reg.qualify(candidate["id"])
    assert not (tmp_path / "extension-journal").exists()


@pytest.mark.parametrize("name", ["../escaped.py", "C:/escaped.py", "CON.txt", "extra|file.txt", "plugin.PY", "huge.txt"])
def test_archive_paths_aliases_and_zip_bomb_are_rejected_before_registration(tmp_path, name):
    reg = registry(tmp_path)
    def corrupt(files):
        files[name] = b"x" * (70000 if name == "huge.txt" else 3)
    with pytest.raises(InvalidInput):
        reg.import_snapshot(archive(1, corrupt))
    assert not (tmp_path / "extensions").exists()


def test_archive_symlink_and_changed_review_digest_are_rejected(tmp_path):
    reg = registry(tmp_path)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        info = zipfile.ZipInfo("plugin.py")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "/etc/passwd")
    with pytest.raises(InvalidInput, match="symlink"):
        reg.import_snapshot(output.getvalue())
    v1 = reg.qualify(reg.import_snapshot(archive(1))["id"])
    review = reg.review(v1["id"])
    with pytest.raises(Conflict, match="exact snapshot"):
        reg.approve(review["id"], "0" * 64, review["action"]["permissions"])
