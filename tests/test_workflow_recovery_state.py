"""Workflow recovery uses the real stage dispatcher and SQLite WAL checkpoints."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
from threading import Event
import zipfile

import pytest

from packages.domain.store import Store
from packages.dynamic_workflow import kernel_settings, stage_workspace
from scripts import native_state_protocols as protocol
from scripts import native_user_state as private
from test_stage_workflow_runs import define, node, scope, start

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import snapshot_data as portable


def test_active_real_stage_blocks_both_backups(client, tmp_path):
    owner = scope(client)
    define(client, owner, [node('A')])
    run = start(client, owner)
    store = client.app.state.stage_workflows
    entered, release = Event(), Event()

    def waiting(ctx):
        entered.set()
        assert release.wait(15)
        return {'questions': ['Specify the environment']}

    store.executor = waiting
    archive = tmp_path / 'during-stage.zip'
    with ThreadPoolExecutor(max_workers=1) as pool:
        stage = pool.submit(store.action, owner['project_id'], owner['conversation_id'], run['id'],
                            'owned-stage', run['revision'], 'A', 'execute', {})
        try:
            assert entered.wait(10)
            with pytest.raises(private.RecoveryError, match='stage is executing'):
                private._no_live_workflow(store.data_dir)
            with pytest.raises(ValueError, match='unconfirmed workflow stage'):
                portable.backup(store.data_dir, archive)
            assert not archive.exists()
        finally:
            release.set()
        assert stage.result(timeout=10)['stages'][0]['status'] == 'needs_input'
    private._no_live_workflow(store.data_dir)
    assert portable.backup(store.data_dir, archive)['status'] == 'PASS'


def test_workflow_wal_and_version_bytes_restore_with_cache_and_secret_excluded(tmp_path):
    data = tmp_path / 'source'
    Store(data / 'eeas.sqlite3')
    sample = b'local recovery fixture\n'
    digest = hashlib.sha256(sample).hexdigest()
    installer = tmp_path / 'original-kernel'
    installer.write_bytes(sample)
    kernel_settings.install(data, 'claude-code', 0, str(installer), digest, 'local-1')
    with stage_workspace.ledger(data):
        pass
    temp_home = data / 'workflow-kernels/sessions/unfinished/home'
    temp_home.mkdir(parents=True)
    for name in ('.credentials.json', 'settings.json', '.env'):
        (temp_home / name).write_bytes(b'synthetic transient')
    config, ledger = [data / key for key in protocol.KERNEL_DATABASES]
    with closing(sqlite3.connect(config)) as settings, closing(sqlite3.connect(ledger)) as blobs:
        for db in [settings, blobs]:
            assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
            db.execute('PRAGMA wal_autocheckpoint=0')
        settings.execute("UPDATE versions SET version='wal-updated' WHERE digest=?", (digest,))
        blobs.execute('INSERT INTO blobs VALUES(?,?)', (digest, sample))
        settings.commit()
        blobs.commit()
        assert all(Path(str(path)+'-wal').stat().st_size > 0 for path in (config, ledger))
        archive = tmp_path / 'workflow.zip'
        settings.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            portable.backup(data, archive)
        settings.rollback()
        assert not archive.exists()
        saved = portable.backup(data, archive)
    target = tmp_path / '新用户数据'
    portable.restore(archive, target, saved['sha256'])
    installed = kernel_settings.select_binary(target, 'claude-code', digest)
    assert installed.read_bytes() == sample
    if os.name != 'nt':
        assert installed.stat().st_mode & 0o777 == 0o700
    with kernel_settings.database(target) as db:
        assert db.execute('SELECT version FROM versions WHERE digest=?', (digest,)).fetchone()[0] == 'wal-updated'
    with stage_workspace.ledger(target) as db:
        assert db.execute('SELECT content FROM blobs WHERE sha=?', (digest,)).fetchone()[0] == sample
    assert not (target / temp_home.relative_to(data)).exists()
    with zipfile.ZipFile(archive) as bundled:
        assert not any(name.endswith(('-wal','-shm')) for name in bundled.namelist())


def test_managed_binary_permission_is_bound_to_exact_platform_path_and_hash(tmp_path):
    raw = b'fixed sample'
    digest = hashlib.sha256(raw).hexdigest()
    prefix = f'workflow-kernels/versions/opencode/{digest}/'
    regular = tmp_path / 'neutral'
    regular.write_bytes(raw)
    regular.chmod(0o600)
    assert protocol.kernel_binary_pin(prefix + 'kernel.exe') == digest
    for bad in ('workflow-kernels/sessions/test/home/.credentials.json', prefix+'other.exe',
                prefix+'../kernel', 'workspaces/'+prefix+'kernel', prefix.replace(digest,'bad')+'kernel'):
        assert not protocol.kernel_private_member(bad)
        protocol.restore_kernel_mode(regular, bad, digest)
    protocol.restore_kernel_mode(regular, prefix+'kernel.exe', digest)
    if os.name != 'nt':
        assert regular.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match='content address'):
        protocol.restore_kernel_mode(regular, prefix+'kernel', '0'*64)
    cache = 'workflow-kernels/sessions/test/home/.credentials.json'
    assert private._excluded(cache)
    assert not private._excluded(cache, cache_exclusion_policy=private.SHADER_CACHE_EXCLUSION_POLICY)
    assert not private._excluded('workspaces/project/workflow-kernels/sessions/test/note.txt')


def test_older_snapshot_with_missing_workflow_dependencies_is_identified(tmp_path):
    with closing(sqlite3.connect(':memory:')) as main:
        main.execute('CREATE TABLE stage_workflow_runs(id INTEGER)')
        main.execute('INSERT INTO stage_workflow_runs VALUES(1)')
        with pytest.raises(ValueError, match='missing the workflow kernel settings'):
            portable.check_workflow_snapshot(main, set())
        main.execute('CREATE TABLE stage_workflow_attempts(body TEXT)')
        main.execute('INSERT INTO stage_workflow_attempts VALUES(?)', ('{"verified_evidence":[{"id":"file"}]}',))
        with pytest.raises(ValueError, match='missing workflow artifact bytes'):
            portable.check_workflow_snapshot(main, {protocol.KERNEL_DATABASES[0]})
        config = tmp_path / 'settings.sqlite3'
        with closing(sqlite3.connect(config)) as db:
            db.execute('CREATE TABLE versions(engine,digest)')
            db.execute('INSERT INTO versions VALUES(?,?)', ('claude-code', '1'*64))
            db.commit()
        with pytest.raises(ValueError, match='missing a registered'):
            portable.check_workflow_snapshot(main, set(protocol.KERNEL_DATABASES), config)


def test_unpublished_zip_is_discarded_if_changed_during_capture(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    Store(data / 'eeas.sqlite3')
    document = data / 'workspaces' / 'original.txt'
    document.parent.mkdir()
    document.write_text('first revision')
    archive = tmp_path / 'changed.zip'
    original = portable.asset_files
    calls = 0

    def changing_assets(root):
        nonlocal calls
        calls += 1
        if calls == 2:
            document.write_text('another revision')
        return original(root)

    monkeypatch.setattr(portable, 'asset_files', changing_assets)
    with pytest.raises(ValueError, match='assets changed'):
        portable.backup(data, archive)
    assert not archive.exists() and not archive.with_suffix('.zip.sha256').exists()
    assert document.read_text() == 'another revision'
