import json
from pathlib import Path
import shutil
import sys

import pytest
from conftest import ROOT
from packages.domain.paths import resolve_data_path
from packages.domain.store import InvalidInput
from packages.domain.tasks import TaskService
from packages.extensions.service import ExtensionRegistry
from packages.runner.fixture import TrustedFixtureRunner
from packages.runner.worker import Worker
from packages.verifier.service import Verifier
from test_documents import project
from test_extension_tasks import import_and_activate
from test_tasks import inputs
from test_worker import submitted, until

sys.path.insert(0, str(ROOT / 'scripts'))
from snapshot_data import backup, restore


def marker(root, origins):
    (root / 'restore-provenance.json').write_text(json.dumps({
        'schema_version': 'eeas.restore-provenance.v1', 'allowed_source_data_dirs': origins,
        'snapshot_id': 'original-test-restore', 'manifest_sha256': '0' * 64}))


def test_cross_os_prefix_mapping_stays_inside_root_and_rejects_escape(tmp_path):
    marker(tmp_path, ['/original/Linux data', 'C:\\Original\\Windows Data'])
    assert resolve_data_path('/original/Linux data/verification/run/tests.json', tmp_path) == tmp_path / 'verification/run/tests.json'
    assert resolve_data_path('c:\\original\\windows data\\verification\\run\\tests.json', tmp_path) == tmp_path / 'verification/run/tests.json'
    assert resolve_data_path('workspaces/run/candidate.txt', tmp_path) == tmp_path / 'workspaces/run/candidate.txt'
    for raw in ('/original/Linux data/../outside', '/original/Linux database/secret', '/etc/passwd', 'C:/elsewhere/password'):
        with pytest.raises(InvalidInput):
            resolve_data_path(raw, tmp_path)
    outside = tmp_path.parent / 'outside-original-file'
    outside.write_text('not accessible')
    (tmp_path / 'linked').symlink_to(outside)
    with pytest.raises(InvalidInput, match='escaped|symlink'):
        resolve_data_path('/original/Linux data/linked', tmp_path)


@pytest.mark.parametrize('relocation', ['move', 'snapshot'])
def test_actual_results_baselines_runner_and_plugin_survive_move_without_db_rewrite(client, tmp_path, relocation):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    plugin = import_and_activate(client, 1)
    client.post(f'/api/projects/{pid}/extension-binding', json={'snapshot_id': plugin})
    first_task, first = submitted(client, pid, body, 'before-move-verified')
    second_task, second = submitted(client, pid, body, 'before-move-unverified')
    original = TaskService(tmp_path / 'eeas.sqlite3', ROOT)
    worker = Worker(original, tmp_path)
    assert worker.start()
    try:
        until(original, pid, first['id'], {'RETURNED'})
        until(original, pid, second['id'], {'RETURNED'})
    finally:
        worker.stop()
    proof = Verifier(original, tmp_path).verify(pid, first['id'])
    with original.connect() as connection:
        before = [tuple(row) for row in connection.execute('SELECT baseline_path,expected_path FROM verification_baselines ORDER BY run_id')]
        frozen = [row[0] for row in connection.execute('SELECT frozen_inputs FROM task_runs ORDER BY id')]
    moved = tmp_path.parent / (tmp_path.name + '-' + relocation + '-中文 移机')
    if relocation == 'move':
        shutil.move(str(tmp_path), moved)
        marker(moved, [str(tmp_path)])
    else:
        archive = tmp_path.parent / (tmp_path.name + '-' + relocation + '-original.zip')
        snapshot = backup(tmp_path, archive)
        restore(archive, moved, snapshot['sha256'])
        shutil.rmtree(tmp_path)
    restored = TaskService(moved / 'eeas.sqlite3', ROOT)
    verifier = Verifier(restored, moved)
    assert verifier.evidence(pid, proof['payload']['evidence_id'])['association_status'] == 'CURRENT'
    assert verifier.verify(pid, second['id'])['payload']['result'] == 'PASSED'
    assert TrustedFixtureRunner(restored, moved).inspect(restored.run(pid, first['id']))['state'] == 'FINISHED'
    registry = ExtensionRegistry(restored, moved)
    assert registry.describe(plugin)['qualified']
    assert registry.invoke(plugin, 'moved')['result'] == 'v1|moved'
    with restored.connect() as connection:
        after = [tuple(row) for row in connection.execute('SELECT baseline_path,expected_path FROM verification_baselines ORDER BY run_id')]
        assert before == after
        assert frozen == [row[0] for row in connection.execute('SELECT frozen_inputs FROM task_runs ORDER BY id')]
    # Remove the relocation authority: old absolute records may not silently read elsewhere.
    (moved / 'restore-provenance.json').unlink()
    assert verifier.evidence(pid, proof['payload']['evidence_id'])['association_status'] == 'STALE_EVIDENCE'
    assert not registry.describe(plugin)['qualified']
    with pytest.raises(InvalidInput):
        resolve_data_path(before[0][0], moved)
