"""Back up SQLite and supported assets after stopping the application.

Portable snapshots preserve Claude SDK execution history and omit its private
runtime directory. For continued Claude sessions, use MAINTAIN_CIRVEN.cmd to
create and restore a private backup as the same Windows user on this computer.
"""
from __future__ import annotations
import argparse
from contextlib import closing, ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import zipfile
import hashlib
import uuid

# Embedded Windows Python uses a fixed ._pth and omits the executing script's
# directory. Resolve the packaged helpers beside this file for the normal CLI.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from native_state_protocols import KERNEL_DATABASES, kernel_binary_pin, restore_kernel_mode
    from bundle_support import (MANIFEST, assert_no_links, canonical_json, digest_file,
                                extract_bundle, read_json, safe_name, validate_bundle, validate_names)
finally:
    sys.path.pop(0)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "eeas.data-snapshot.v1"
CLAUDE_SDK_CONTINUATION = "REQUIRES_PRIVATE_BACKUP_WITH_ORIGINAL_TRANSCRIPT"
ASSET_DIRS = {"workspaces", "verification", "runner-journal", "receipts", "agent-workspaces",
              "extensions", "extension-journal", "agent-transcripts"}
EXCLUDED = {"local-access.token", "worker.lock", "server.stdout.txt", "eeas.sqlite3-wal", "eeas.sqlite3-shm",
            "credentials.json", ".credentials.json", "settings.local.json", ".env", ".eeas-sdk-state"}


class QuiescentLock:
    """Same OS lock region as WorkerLock, held throughout the filesystem snapshot."""
    def __init__(self, path):
        self.path, self.stream = path, None

    def __enter__(self):
        assert_no_links(self.path)
        self.stream = self.path.open("a+b")
        self.stream.seek(0)
        if not self.stream.read(1):
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise ValueError("Stop the active worker before backup") from exc
        return self

    def __exit__(self, *args):
        if os.name == "nt":
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()


def checked_database(connection):
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("SQLite foreign key check failed")
    return [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]


def inspect_quiescent(connection):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "stage_workflow_operations" in tables and connection.execute(
            "SELECT 1 FROM stage_workflow_operations WHERE completed=0 LIMIT 1").fetchone():
        raise ValueError("Running or unconfirmed workflow stage blocks a consistent snapshot")
    if "task_runs" in tables:
        if connection.execute("SELECT 1 FROM task_runs WHERE execution_status IN ('RUNNING','CANCEL_REQUESTED','UNKNOWN_OUTCOME') OR verification_status='RUNNING' LIMIT 1").fetchone():
            raise ValueError("Running/unknown execution or verification blocks a consistent snapshot")
    if "agent_executions" in tables and connection.execute("SELECT 1 FROM agent_executions WHERE execution_status IN ('RUNNING','UNKNOWN_OUTCOME','CANCEL_REQUESTED') OR verification_status='RUNNING' LIMIT 1").fetchone():
        raise ValueError("Running/unknown native execution blocks a consistent snapshot")
    if "runner_jobs" in tables and connection.execute("SELECT 1 FROM runner_jobs WHERE status IN ('STARTING','RUNNING') LIMIT 1").fetchone():
        raise ValueError("Independent runner must be finished before snapshot")


def workflow_binaries(data):
    result = {}
    directory = data / 'workflow-kernels' / 'versions'
    assert_no_links(directory)
    if directory.is_dir():
        for name in ('kernel','kernel.exe'):
            for path in directory.glob('*/*/'+name):
                relative = path.relative_to(data).as_posix()
                pin = kernel_binary_pin(relative)
                if pin:
                    assert_no_links(path)
                    if not path.is_file() or digest_file(path) != pin:
                        raise ValueError("Managed workflow kernel differs from its content address")
                    result[relative] = path
    return result


def freeze_kernel_databases(data, temporary, locks):
    # The primary DB writer lock is already held and unresolved operations
    # were rejected. Acquire auxiliary writer locks in a stable order, then
    # use SQLite's backup API so uncheckpointed WAL pages are included.
    copies = {}
    for name in KERNEL_DATABASES:
        path = data / name
        assert_no_links(path)
        if not path.exists():
            continue
        guard = locks.enter_context(closing(sqlite3.connect(path.as_uri()+"?mode=rw",uri=True,timeout=2)))
        guard.execute("BEGIN IMMEDIATE")
        saved = temporary / path.name
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=2)) as reader, closing(sqlite3.connect(saved)) as dest:
            reader.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError("Workflow database integrity check failed")
        copies[name] = saved
    return copies


def asset_files(data):
    result = {}
    for directory in sorted(ASSET_DIRS):
        folder = data / directory
        if not folder.exists():
            continue
        assert_no_links(folder)
        for path in folder.rglob("*"):
            assert_no_links(path)
            if path.is_file():
                name = path.relative_to(data).as_posix()
                safe_name(name, private=False)
                if any(part.casefold() in EXCLUDED or part.casefold().startswith(".env") or
                       part.casefold().endswith((".key", ".pem")) for part in path.relative_to(data).parts):
                    continue
                result[name] = path
    result.update(workflow_binaries(data))
    validate_names(result, private=False)
    return result


def check_workflow_snapshot(primary, members, settings=None):
    tables = {row[0] for row in primary.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if ('stage_workflow_runs' in tables and primary.execute('SELECT 1 FROM stage_workflow_runs LIMIT 1').fetchone()
            and KERNEL_DATABASES[0] not in members):
        raise ValueError("Snapshot is missing the workflow kernel settings; preserve the original data and back up again")
    if ('stage_workflow_attempts' in tables and KERNEL_DATABASES[1] not in members
            and primary.execute("SELECT 1 FROM stage_workflow_attempts WHERE json_array_length(body,'$.verified_evidence')>0 LIMIT 1").fetchone()):
        raise ValueError("Snapshot is missing workflow artifact bytes and execution receipts")
    if settings is not None:
        with closing(sqlite3.connect(settings.as_uri()+"?mode=ro&immutable=1",uri=True)) as config:
            if config.execute("PRAGMA integrity_check").fetchall() != [('ok',)]:
                raise ValueError("Workflow settings integrity check failed")
            for engine, checksum in config.execute("SELECT engine,digest FROM versions"):
                base = f"workflow-kernels/versions/{engine}/{checksum}/kernel"
                if kernel_binary_pin(base)!=checksum or not {base,base+".exe"}.intersection(members):
                    raise ValueError("Snapshot is missing a registered workflow kernel binary")


def check_workflow_archive(archive, members):
    # A legacy archive can carry approved stage rows while omitting their
    # payload database. Identify this before writing anything at the restore target.
    with tempfile.TemporaryDirectory(prefix='eeas-workflow-read-') as tmp, zipfile.ZipFile(archive) as source:
        temp = Path(tmp)
        for name in ('eeas.sqlite3', KERNEL_DATABASES[0]):
            if name in members:
                with source.open(name) as inp, (temp/Path(name).name).open('xb') as out:
                    shutil.copyfileobj(inp,out,1024*1024)
        settings = temp/'settings.sqlite3'
        with closing(sqlite3.connect((temp/'eeas.sqlite3').as_uri()+"?mode=ro&immutable=1",uri=True)) as primary:
            check_workflow_snapshot(primary,members,settings if settings.exists() else None)


def _write_snapshot(data: Path, output: Path, source_root: Path):
    assert_no_links(data)
    assert_no_links(output)
    data, source_root = data.resolve(), source_root.resolve()
    database = data / "eeas.sqlite3"
    assert_no_links(database)
    if not database.is_file():
        raise ValueError("Missing database")
    output.parent.mkdir(parents=True, exist_ok=True)
    with QuiescentLock(data / "worker.lock"), tempfile.TemporaryDirectory(prefix="eeas-snapshot-") as temporary, ExitStack() as locks:
        # Acquire writer exclusion separately: SQLite backup reads a consistent source
        # snapshot while writes and claims are prevented, including API with worker disabled.
        locker = sqlite3.connect(database, timeout=2)
        reader = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
        saved = Path(temporary) / "eeas.sqlite3"
        destination = sqlite3.connect(saved)
        try:
            locker.execute("BEGIN IMMEDIATE")
            versions = checked_database(reader)
            inspect_quiescent(reader)
            kernel_dbs = freeze_kernel_databases(data, Path(temporary), locks)
            paths = asset_files(data)
            original_hashes = {name: digest_file(path) for name, path in paths.items()}
            reader.backup(destination)
            checked_database(destination)
            destination.close()
            roots = [str(data)]
            previous = None
            provenance = data / "restore-provenance.json"
            if provenance.is_file():
                assert_no_links(provenance)
                previous = read_json(provenance.read_bytes())
                if previous.get("schema_version") != "eeas.restore-provenance.v1":
                    raise ValueError("Invalid previous restore provenance")
                roots.extend(previous.get("allowed_source_data_dirs", []))
            migrations = {}
            for version in versions:
                safe_name(version)
                path = source_root / "packages/domain/migrations" / version
                assert_no_links(path)
                if not path.is_file():
                    raise ValueError("Source cannot interpret the applied database migration")
                migrations[version] = digest_file(path)
            manifest = {"schema_version": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
                        "snapshot_id": str(uuid.uuid4()), "allowed_source_data_dirs": list(dict.fromkeys(roots)),
                        "previous_restore_provenance": previous, "migrations_sha256": migrations,
                        "schema_migrations": versions, "files": {},
                        "scope": "Quiescent SQLite and declared runtime assets; credentials/access token/cache excluded",
                        "claude_sdk_continuation": CLAUDE_SDK_CONTINUATION}
            paths.update(kernel_dbs)
            paths["eeas.sqlite3"] = saved
            check_workflow_snapshot(reader,paths.keys(),kernel_dbs.get(KERNEL_DATABASES[0]))
            with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, path in sorted(paths.items()):
                    raw = path.read_bytes()
                    digest = hashlib.sha256(raw).hexdigest()
                    if name in original_hashes and digest != original_hashes[name]:
                        raise ValueError("Asset changed during backup")
                    archive.writestr(name, raw, compress_type=zipfile.ZIP_STORED if name in kernel_dbs or kernel_binary_pin(name) else zipfile.ZIP_DEFLATED)
                    manifest["files"][name] = {"sha256": digest, "bytes": len(raw)}
                if set(asset_files(data)) != set(original_hashes) or any(digest_file(path) != original_hashes[name] for name, path in asset_files(data).items()):
                    raise ValueError("Runtime assets changed during backup")
                if set(kernel_dbs) != {name for name in KERNEL_DATABASES if (data/name).exists()}:
                    raise ValueError("Workflow database inventory changed during backup")
                archive.writestr(MANIFEST, canonical_json(manifest))
        finally:
            destination.close()
            reader.close()
            locker.rollback()
            locker.close()
    digest = digest_file(output)
    validate_bundle(output, digest, expected_schema=SCHEMA, private=False)
    return {"sha256": digest, "schema_migrations": versions, "status": "PASS",
            "claude_sdk_continuation": CLAUDE_SDK_CONTINUATION}


def backup(data: Path, output: Path, source_root: Path = ROOT):
    sidecar = output.with_suffix(".zip.sha256")
    assert_no_links(output)
    assert_no_links(sidecar)
    if os.path.lexists(output) or os.path.lexists(sidecar) or output == sidecar:
        raise ValueError("Snapshot output exists; old backups are immutable")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Validate the complete archive before exposing its name. If a concurrent
    # publisher wins, leave its bytes alone. On Windows rename is exclusive.
    with tempfile.TemporaryDirectory(prefix=".eeas-snapshot-", dir=output.parent) as temporary:
        scratch = Path(temporary)
        archive = scratch / "snapshot.zip"
        result = _write_snapshot(data, archive, source_root)
        checksum = scratch / "checksum"
        checksum.write_text(result["sha256"] + "  " + output.name + "\n", encoding="utf-8")
        archive.chmod(0o600)
        checksum.chmod(0o600)
        identity = archive.stat()
        publish = os.rename if os.name == 'nt' else os.link
        publish(archive, output)
        try:
            publish(checksum, sidecar)
        except OSError:
            # Remove only the archive this operation just published.
            if output.exists() and os.path.samestat(identity, output.lstat()):
                output.unlink()
            raise
    return {**result, "path": str(output)}


def restore(archive: Path, destination: Path, expected_sha256: str, source_root: Path = ROOT):
    destination = destination.absolute()
    manifest = validate_bundle(archive, expected_sha256, expected_schema=SCHEMA, private=False)
    versions = manifest.get("schema_migrations")
    migrations = manifest.get("migrations_sha256", {})
    if not isinstance(versions, list) or not versions or set(versions) != set(migrations) or "eeas.sqlite3" not in manifest["files"]:
        raise ValueError("Invalid database snapshot metadata")
    roots = manifest.get("allowed_source_data_dirs")
    if not isinstance(roots, list) or not roots or any(not isinstance(root, str) or not root or "\x00" in root for root in roots):
        raise ValueError("Invalid data-root provenance")
    for version, expected in migrations.items():
        safe_name(version)
        path = source_root / "packages/domain/migrations" / version
        assert_no_links(path)
        if not path.is_file() or digest_file(path) != expected:
            raise ValueError("Restore code is incompatible with snapshot migrations; no reverse migration")
    for name in manifest["files"]:
        pin = kernel_binary_pin(name)
        if pin and manifest["files"][name]["sha256"] != pin:
            raise ValueError("Managed workflow kernel differs from its content address")
        if name != "eeas.sqlite3" and name not in KERNEL_DATABASES and not pin and name.split("/")[0] not in ASSET_DIRS:
            raise ValueError("Unexpected/private snapshot file")
        if any(part.casefold() in EXCLUDED or part.casefold().startswith(".env") or part.casefold().endswith((".pem", ".key")) for part in name.split("/")):
            raise ValueError("Unexpected/private snapshot file")
    if '057_stage_workflows.sql' in migrations or KERNEL_DATABASES[0] in manifest['files']:
        check_workflow_archive(archive,manifest['files'].keys())
    extract_bundle(archive, destination, expected_sha256, expected_schema=SCHEMA, private=False)
    for name, entry in manifest["files"].items():
        restore_kernel_mode(destination/name,name,entry["sha256"])
    connection = sqlite3.connect((destination / "eeas.sqlite3").as_uri() + "?mode=ro", uri=True)
    try:
        if checked_database(connection) != versions:
            raise ValueError("Snapshot database/manifest migration mismatch")
        inspect_quiescent(connection)
    finally:
        connection.close()
    provenance = {"schema_version": "eeas.restore-provenance.v1", "snapshot_sha256": expected_sha256,
                  "allowed_source_data_dirs": manifest["allowed_source_data_dirs"],
                  "snapshot_id": manifest["snapshot_id"], "manifest_sha256": hashlib.sha256(canonical_json(manifest)).hexdigest(),
                  "restored_data_root": str(destination.resolve()),
                  "restored_at": datetime.now(timezone.utc).isoformat(), "immutable_records_rewritten": False,
                  "note": "Historical absolute paths require provenance-aware file resolution; original requests/receipts remain unchanged"}
    with (destination / "restore-provenance.json").open("xb") as stream:
        stream.write(canonical_json(provenance))
    (destination / "restore-provenance.json").chmod(0o600)
    return {"status": "PASS", "destination": str(destination), "schema_migrations": versions,
            "claude_sdk_continuation": CLAUDE_SDK_CONTINUATION,
            "access_token": "REGENERATE_ON_START", "schema_downgrade": "FORBIDDEN"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("backup")
    create.add_argument("--data", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    load = commands.add_parser("restore")
    load.add_argument("--archive", type=Path, required=True)
    load.add_argument("--sha256", required=True)
    load.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = backup(args.data, args.output) if args.action == "backup" else restore(args.archive, args.destination.absolute(), args.sha256)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        print(f"Snapshot operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
