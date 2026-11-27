import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import stat
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from bundle_support import (MANIFEST, SCHEMA, canonical_json, digest_file, extract_bundle,
                            validate_bundle, validate_extracted)
from build_release_bundle import REQUIRED, build
from snapshot_data import backup, restore
from packages.domain.store import Store
from packages.runner.worker import WorkerLock
from install_offline import verify


def test_native_opaque_transcript_restores_without_entire_private_cli_state(tmp_path):
    data = tmp_path / 'original-native-data'
    Store(data / 'eeas.sqlite3')
    transcript = data / 'agent-transcripts' / 'original-execution' / 'original-session.jsonl'
    transcript.parent.mkdir(parents=True)
    original = b'{"type":"user","message":{"content":"original public fixture"}}\n'
    transcript.write_bytes(original)
    state = data / 'agent-workspaces' / 'original-execution' / '.eeas-sdk-state'
    state.mkdir(parents=True)
    for name in ('arbitrary-cache.json', '.claude.json', 'settings.json', '.credentials.json'):
        (state / name).write_text('original private-state sentinel; never restored')
    public_artifact = state.parent / 'original-output.txt'
    public_artifact.write_text('original visible artifact')
    receipt = backup(data, tmp_path / 'opaque-native-snapshot.zip')
    target = tmp_path / '中文 原生恢复'
    restore(Path(receipt['path']), target, receipt['sha256'])
    assert (target / transcript.relative_to(data)).read_bytes() == original
    assert (target / public_artifact.relative_to(data)).read_text() == 'original visible artifact'
    assert not (target / state.relative_to(data)).exists()


def archive_fixture(path, files=None, *, extra=None, omit=None, metadata=None, compression=zipfile.ZIP_STORED):
    files = files or {"apps/api/original.py": b"original fixture = 1\n"}
    manifest = {"schema_version": SCHEMA, "release_status": "DEVELOPMENT_PREVIEW",
                "source_commit": "a" * 40, "target": "windows-py310",
                "files": {name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for name, raw in files.items()}}
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, raw in files.items():
            if name == omit:
                continue
            if metadata:
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = metadata << 16
                archive.writestr(entry, raw)
            else:
                archive.writestr(name, raw)
        if extra:
            archive.writestr(extra, b"unlisted original fixture")
        archive.writestr(MANIFEST, canonical_json(manifest))
    return digest_file(path)


def test_safe_unpack_hash_check_and_fresh_destination(tmp_path):
    archive = tmp_path / "original.zip"
    digest = archive_fixture(archive)
    destination = tmp_path / "中文 移机"
    result = extract_bundle(archive, destination, digest)
    assert result["release_status"] == "DEVELOPMENT_PREVIEW"
    assert validate_extracted(destination) == result
    before = (destination / "apps/api/original.py").read_bytes()
    with pytest.raises(ValueError, match="exists"):
        extract_bundle(archive, destination, digest)
    assert (destination / "apps/api/original.py").read_bytes() == before
    with pytest.raises(ValueError, match="Bundle SHA256"):
        validate_bundle(archive, "0" * 64)
    (destination / "apps/api/original.py").write_text("changed")
    with pytest.raises(ValueError, match="SHA256"):
        validate_extracted(destination)


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "C:/drive.txt", "apps\\escape.txt",
                                 "apps/NUL.txt", "apps/COM¹.txt", "apps/odd. ", "apps/file:stream", ".env", ".claude/credentials.json"])
def test_malicious_windows_or_private_path_never_extracts(tmp_path, name):
    archive = tmp_path / "attack.zip"
    digest = archive_fixture(archive, {name: b"original malicious fixture"})
    with pytest.raises(ValueError):
        extract_bundle(archive, tmp_path / "destination", digest)
    assert not (tmp_path / "destination").exists()
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.parametrize("files", [{"apps/File.py": b"x", "apps/file.py": b"y"},
                                   {"Apps/a.py": b"x", "apps/b.py": b"y"},
                                   {"apps/file": b"x", "apps/file/child": b"y"}])
def test_windows_case_or_file_directory_conflict_rejected(tmp_path, files):
    archive = tmp_path / "collision.zip"
    digest = archive_fixture(archive, files)
    with pytest.raises(ValueError, match="collision"):
        validate_bundle(archive, digest)


def test_unlisted_missing_symlink_and_zip_bomb_rejected(tmp_path):
    archive = tmp_path / "unlisted.zip"
    digest = archive_fixture(archive, extra="apps/extra.py")
    with pytest.raises(ValueError, match="Unlisted"):
        validate_bundle(archive, digest)
    archive = tmp_path / "missing.zip"
    digest = archive_fixture(archive, omit="apps/api/original.py")
    with pytest.raises(ValueError, match="missing"):
        validate_bundle(archive, digest)
    archive = tmp_path / "symlink.zip"
    digest = archive_fixture(archive, metadata=stat.S_IFLNK | 0o777)
    with pytest.raises(ValueError, match="symlink"):
        validate_bundle(archive, digest)
    archive = tmp_path / "bomb.zip"
    digest = archive_fixture(archive, {"apps/large.txt": b"A" * (2 * 1024 * 1024)}, compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(ValueError, match="ratio"):
        validate_bundle(archive, digest)


def test_member_tamper_even_when_new_external_digest_is_supplied(tmp_path):
    archive = tmp_path / "member.zip"
    archive_fixture(archive)
    with zipfile.ZipFile(archive) as source:
        manifest = source.read(MANIFEST)
    with zipfile.ZipFile(archive, "w") as changed:
        changed.writestr("apps/api/original.py", b"tampered fixture = 1\n")
        changed.writestr(MANIFEST, manifest)
    with pytest.raises(ValueError, match="SHA256"):
        validate_bundle(archive, digest_file(archive))


def test_linked_destination_parent_rejected(tmp_path):
    archive = tmp_path / "original.zip"
    digest = archive_fixture(archive)
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        extract_bundle(archive, link / "new", digest)
    assert not (real / "new").exists()


def minimal_repository(tmp_path):
    root, wheels = tmp_path / "source", tmp_path / "dependency-source"
    root.mkdir()
    for name in REQUIRED:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if name.endswith(".json") else "# original release fixture\n")
    selected = "vendor/windows-py310/original"
    path = wheels / selected
    (path / "wheels").mkdir(parents=True)
    wheel = path / "wheels/original-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("original-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: original\nVersion: 1.0\nLicense: MIT\n")
        archive.writestr("original-1.0.dist-info/licenses/", b"")
        archive.writestr("original-1.0.dist-info/licenses/LICENSE", "Original fixture license text, not third-party material")
    for name in ("requirements-core.lock", "requirements-runtime.lock"):
        (path / name).write_text("original==1.0\n")
    manifest = {"target": "windows-py310", "python": "3.10", "core": {"original": "1.0"}, "runtime": {"original": "1.0"},
                "files_sha256": {p.relative_to(path).as_posix(): digest_file(p) for p in path.rglob("*") if p.is_file()}}
    (path / "manifest.json").write_bytes(canonical_json(manifest))
    (root / "profiles/offline-dependencies.json").write_bytes(canonical_json({"targets": {"windows-py310": selected}}))
    (root / ".claude/skills/original").mkdir(parents=True)
    (root / ".claude/skills/original/SKILL.md").write_text("Original fixture Skill")
    evidence = "reports/original/one"
    report = root / evidence
    report.mkdir(parents=True)
    (report / "freeze.json").write_bytes(canonical_json({"git_base": "b" * 40, "command": ["original-test"]}))
    (report / "result.json").write_bytes(canonical_json({"status": "PASS", "exit_code": 0}))
    (report / "stdout.txt").write_text("original verification output\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Original Fixture", "-c", "user.email=original@localhost", "commit", "-qm", "original fixture"], cwd=root, check=True)
    return root, wheels, evidence


def test_builder_binds_actual_commit_licenses_original_evidence_and_target_selection(tmp_path):
    root, wheels, evidence = minimal_repository(tmp_path)
    (root / 'state').mkdir()
    (root / 'state/DELIVERY.json').write_text('{"bundle":"old unrelated artifact.zip","sha256":"old-fixture-digest"}')
    subprocess.run(['git', 'add', 'state/DELIVERY.json'], cwd=root, check=True)
    subprocess.run(['git', '-c', 'user.name=Original Fixture', '-c', 'user.email=original@localhost',
                    'commit', '-qm', 'original historical delivery fixture'], cwd=root, check=True)
    result = build(root, "windows-py310", wheels, [evidence], tmp_path / "preview.zip")
    manifest = validate_bundle(Path(result["path"]), result["sha256"])
    assert result["wheel_count"] == 1
    assert manifest["source_worktree_dirty"] is False
    assert ".claude/skills/original/SKILL.md" in manifest["files"]
    for name, record in result["standalone_validators"].items():
        assert digest_file(Path(record["path"])) == record["sha256"]
        assert Path(record["path"]).read_bytes() == (root / "scripts" / name).read_bytes()
    with zipfile.ZipFile(result["path"]) as archive:
        lock = json.loads(archive.read("release/combination-lock.json"))
        assert lock["source_commit"] == result["source_commit"]
        assert lock["source_snapshot_sha256"] == result["source_snapshot_sha256"]
        delivery = json.loads(archive.read('state/DELIVERY.json'))
        assert delivery['source_commit'] == result['source_commit']
        assert delivery['target'] == 'windows-py310'
        assert delivery['manifest'] == MANIFEST
        assert 'old unrelated artifact' not in json.dumps(delivery)
        assert 'sha256' not in delivery
        packages = json.loads(archive.read("release/licenses/packages.json"))
        assert packages["packages"][0]["license_material"]
        assert packages["bundled_cli"]["sdk_license_does_not_certify_cli_license"]
        index = json.loads(archive.read("release/evidence-index.json"))
        assert index["reports"][0]["git_base"] == "b" * 40
        assert set(json.loads(archive.read("profiles/offline-dependencies.json"))["targets"]) == {"windows-py310"}
    with pytest.raises(ValueError, match="Output exists"):
        build(root, "windows-py310", wheels, [evidence], Path(result["path"]))


def test_installer_rejects_direct_download_and_option_locks_before_environment_creation(tmp_path):
    root, wheels, _ = minimal_repository(tmp_path)
    directory = wheels / "vendor/windows-py310/original"
    manifest = json.loads((directory / "manifest.json").read_bytes())
    lock = directory / "requirements-runtime.lock"
    for text in ("original @ https://public.invalid/package.whl\n", "--index-url https://public.invalid\n", "original==*\n"):
        lock.write_text(text)
        manifest["files_sha256"][lock.name] = digest_file(lock)
        (directory / "manifest.json").write_bytes(canonical_json(manifest))
        with pytest.raises(ValueError, match="exact pins"):
            verify(directory, "windows-py310")


def test_original_logs_survive_without_runtime_access_tokens_or_private_sqlite(tmp_path):
    root, wheels, evidence = minimal_repository(tmp_path)
    private = root / evidence / "portable/platform/original-data"
    private.mkdir(parents=True)
    (private / "local-access.token").write_text("synthetic-credential-fixture-never-shipped")
    (private / "eeas.sqlite3").write_bytes(b"original private-database fixture")
    result = build(root, "windows-py310", wheels, [evidence], tmp_path / "preview.zip")
    with zipfile.ZipFile(result["path"]) as archive:
        assert evidence + "/stdout.txt" in archive.namelist()
        assert not any("local-access.token" in name or name.endswith(".sqlite3") for name in archive.namelist())
        excluded = json.loads(archive.read("release/evidence-index.json"))["reports"][0]["excluded_runtime_files"]
        assert len(excluded) == 2


def test_quiescent_backup_restores_db_assets_without_token_and_preserves_multisource_provenance(tmp_path):
    data = tmp_path / "原创 数据"
    store = Store(data / "eeas.sqlite3")
    project = store.create_project("original backup", "synthetic")
    raw = "原创资料\n".encode()
    document = store.save_document(project["id"], "original.md", raw, "synthetic")
    (data / "local-access.token").write_text("original-private-token-fixture")
    (data / "workspaces/original").mkdir(parents=True)
    (data / "workspaces/original/candidate.txt").write_text("original artifact\n")
    receipt = backup(data, tmp_path / "snapshot.zip")
    with zipfile.ZipFile(receipt["path"]) as archive:
        assert "local-access.token" not in archive.namelist()
    restored = tmp_path / "restore1"
    assert restore(Path(receipt["path"]), restored, receipt["sha256"])["status"] == "PASS"
    assert Store(restored / "eeas.sqlite3").project(project["id"]) == project
    with Store(restored / "eeas.sqlite3").connect() as connection:
        assert bytes(connection.execute("SELECT raw_bytes FROM document_versions WHERE document_id=?", (document["id"],)).fetchone()[0]) == raw
    assert (restored / "workspaces/original/candidate.txt").read_text() == "original artifact\n"
    assert not (restored / "local-access.token").exists()
    provenance = json.loads((restored / "restore-provenance.json").read_text())
    assert provenance["allowed_source_data_dirs"] == [str(data.resolve())]
    assert provenance["manifest_sha256"] and provenance["snapshot_id"]
    second = backup(restored, tmp_path / "second.zip")
    final = tmp_path / "restore2"
    restore(Path(second["path"]), final, second["sha256"])
    roots = json.loads((final / "restore-provenance.json").read_text())["allowed_source_data_dirs"]
    assert roots == [str(restored.resolve()), str(data.resolve())]
    with pytest.raises(ValueError, match="exists"):
        restore(Path(receipt["path"]), restored, receipt["sha256"])


def test_worker_lock_and_unknown_execution_block_backup(client, tmp_path):
    lock = WorkerLock(tmp_path / "worker.lock")
    assert lock.acquire()
    try:
        with pytest.raises(ValueError, match="Stop the active worker"):
            backup(tmp_path, tmp_path / "active.zip")
    finally:
        lock.release()
    from test_documents import project
    from test_tasks import inputs
    from test_worker import submitted
    pid = project(client)
    _, _, body = inputs(client, pid)
    _, run = submitted(client, pid, body, "unknown-backup")
    with sqlite3.connect(tmp_path / "eeas.sqlite3") as connection:
        connection.execute("UPDATE task_runs SET execution_status='UNKNOWN_OUTCOME' WHERE id=?", (run["id"],))
    with pytest.raises(ValueError, match="unknown execution"):
        backup(tmp_path, tmp_path / "unknown.zip")
    assert not (tmp_path / "unknown.zip").exists()


def test_restore_rejects_older_migration_code_without_creating_destination(tmp_path):
    data = tmp_path / "data"
    Store(data / "eeas.sqlite3")
    receipt = backup(data, tmp_path / "snapshot.zip")
    with pytest.raises(ValueError, match="incompatible"):
        restore(Path(receipt["path"]), tmp_path / "restore", receipt["sha256"], tmp_path / "older-code")
    assert not (tmp_path / "restore").exists()


def test_actual_legacy_schema_forward_migration_and_snapshot_rollback(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    older_code = tmp_path / "older-code"
    older_migrations = older_code / "packages/domain/migrations"
    older_migrations.mkdir(parents=True)
    old_versions = []
    with sqlite3.connect(legacy / "eeas.sqlite3") as connection:
        connection.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)")
        # Actual checked-in migrations, predating runner-recovery fields.
        for path in sorted((ROOT / "packages/domain/migrations").glob("*.sql")):
            if path.name[:3] > "009":
                continue
            connection.executescript(path.read_text())
            connection.execute("INSERT INTO schema_migrations VALUES (?)", (path.name,))
            shutil.copyfile(path, older_migrations / path.name)
            old_versions.append(path.name)
        connection.execute("INSERT INTO projects VALUES ('original-legacy','Original Legacy','synthetic','2026-01-01T00:00:00Z')")
    baseline = backup(legacy, tmp_path / "legacy-snapshot.zip", older_code)
    upgraded = tmp_path / "upgraded"
    restore(Path(baseline["path"]), upgraded, baseline["sha256"])
    current = Store(upgraded / "eeas.sqlite3")
    current.create_project("original post-upgrade", "synthetic")
    with current.connect() as connection:
        versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert len(versions) > len(old_versions)
    receipt = backup(upgraded, tmp_path / "new-schema.zip")
    with pytest.raises(ValueError, match="incompatible"):
        restore(Path(receipt["path"]), tmp_path / "forbidden-downgrade", receipt["sha256"], older_code)
    rollback = tmp_path / "matching-old-code-rollback"
    restore(Path(baseline["path"]), rollback, baseline["sha256"], older_code)
    with sqlite3.connect(rollback / "eeas.sqlite3") as connection:
        assert connection.execute("SELECT name FROM projects").fetchall() == [("Original Legacy",)]
        assert [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")] == old_versions
    assert digest_file(Path(baseline["path"])) == baseline["sha256"]
