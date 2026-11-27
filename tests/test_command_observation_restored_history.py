"""Relocate immutable observer files without commands, SDK, API or LOAD.

The production observer hooks receive an explicitly simulated tool return.
Only the observer's metadata producer runs in Node; no shell child is started.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from packages.adapters import opencode_command_observation as reader
from packages.domain.tasks import digest
from test_opencode_command_observation import command_scene, observation, NODE, PLUGIN


def capture_unexecuted_fixture(scene):
    script = ("import {createObserver} from " + json.dumps((PLUGIN / 'index.mjs').as_uri()) + ";"
        "let text='';for await(const x of process.stdin)text+=x;const config=JSON.parse(text);"
        "const fs=await import('node:fs');const c=JSON.parse(fs.readFileSync(config,'utf8'));"
        "const hooks=createObserver({directory:c.scope.workspace_root,configPath:config});"
        "const input={tool:'bash',sessionID:c.native_session_id,callID:'restore_fixture'};"
        "const args={command:'EXPLICIT_UNEXECUTED_HISTORY_FIXTURE',workdir:c.scope.workspace_root};"
        "const result={output:'unexecuted tool-return fixture\\n',metadata:{exit:7,truncated:false}};"
        "await hooks['tool.execute.before'](input,{args});"
        "await hooks['shell.env']({cwd:c.scope.workspace_root,...input},{env:{}});"
        "await hooks['tool.execute.after']({...input,args},result);await hooks.dispose();"
        "process.stdout.write(JSON.stringify({input,args,result}));")
    result = subprocess.run([str(NODE), '--input-type=module', '-e', script],
        input=json.dumps(scene['admission']['config_path']), encoding='utf8', capture_output=True,
        timeout=30, env={**os.environ, 'TMPDIR': str(scene['tmp'])})
    assert result.returncode == 0, result.stderr
    return observation(scene, json.loads(result.stdout))


@pytest.fixture
def restored_history(command_scene):
    scene = command_scene
    observed = capture_unexecuted_fixture(scene)
    baseline = reader.read_history(scene['admission'], observed, data_dir=scene['data'])
    assert not baseline[1] and baseline[0][0]['exit_code'] == 7
    destination = scene['tmp'] / 'independent restored data'
    destination.mkdir()
    # Only the small observer tree is needed; no SQLite, runtime or large copy.
    shutil.copytree(scene['runtime'], destination / 'runtime')
    (destination / 'restore-provenance.json').write_text(json.dumps({
        'schema_version': 'eeas.restore-provenance.v1', 'allowed_source_data_dirs': [str(scene['data'])],
        'target_data_dir': str(destination), 'snapshot_sha256': 'a' * 64, 'manifest_sha256': 'a' * 64,
        'snapshot_id': 'explicit-owned-test-restore', 'directory_bindings': {}}), encoding='utf8')
    admission = scene['admission']
    config_relative = Path(admission['config_path']).relative_to(scene['data'])
    ledger_relative = Path(admission['scope']['ledger_root']).relative_to(scene['data'])
    return {**scene, 'observed': observed, 'baseline': baseline, 'restored': destination,
        'config': destination / config_relative, 'ledger': destination / ledger_relative,
        'original_config_raw': Path(admission['config_path']).read_bytes(),
        'original_admission': deepcopy(admission)}


def deny_original_or_executor(scene, monkeypatch):
    old = scene['data']
    original_regular, original_directory = reader._regular, reader._directory
    def current_only(path, *args):
        assert not Path(path).is_relative_to(old), 'Reader touched the original installation'
        return original_regular(path, *args)
    def current_directory(path):
        assert not Path(path).is_relative_to(old), 'Reader inspected the original installation'
        return original_directory(path)
    monkeypatch.setattr(reader, '_regular', current_only)
    monkeypatch.setattr(reader, '_directory', current_directory)
    for name in ('prepare', 'bind', 'ready', 'record_admission'):
        monkeypatch.setattr(reader, name, lambda *a, **k: pytest.fail('History may not activate an observer'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('History may not start an executor'))


@pytest.mark.parametrize('old_exists', [True, False])
def test_restored_copy_reads_its_own_history_with_original_present_or_deleted(restored_history, monkeypatch, old_exists):
    s = restored_history
    if not old_exists:
        assert s['data'].is_relative_to(s['tmp'])
        shutil.rmtree(s['data'])
    deny_original_or_executor(s, monkeypatch)
    config_before = s['config'].read_bytes()
    rows, why = reader.read_history(s['admission'], s['observed'], data_dir=s['restored'])
    assert (rows, why) == s['baseline']
    assert s['config'].read_bytes() == config_before == s['original_config_raw']
    assert s['admission'] == s['original_admission']
    assert digest(s['admission']['scope']) == s['admission']['scope_sha256']


@pytest.mark.parametrize('damage', ['session', 'generation', 'workspace', 'no_provenance',
    'wrong_source_root', 'wrong_target_root', 'wrong_snapshot_sha', 'config_sha', 'blob_sha', 'reparse'])
def test_bad_scope_provenance_or_copied_bytes_never_fall_back_to_old_source(restored_history, monkeypatch, damage):
    s = restored_history
    observed = deepcopy(s['observed'])
    marker = s['restored'] / 'restore-provenance.json'
    proof = json.loads(marker.read_bytes())
    if damage == 'session': observed['session_ref']['sha256'] = 'f' * 64
    elif damage == 'generation': observed['generation'] = 2
    elif damage == 'workspace': observed['workspace_ref']['binding_sha256'] = 'f' * 64
    elif damage == 'no_provenance': marker.unlink()
    elif damage == 'wrong_source_root': proof['allowed_source_data_dirs'] = [str(s['tmp'] / 'other source')]
    elif damage == 'wrong_target_root': proof['target_data_dir'] = str(s['tmp'] / 'other target')
    elif damage == 'wrong_snapshot_sha': proof['snapshot_sha256'] = 'b' * 64
    elif damage == 'config_sha': s['config'].write_bytes(s['config'].read_bytes() + b' ')
    elif damage == 'blob_sha':
        blob = next((s['ledger'] / 'blobs').glob('*.utf8'))
        blob.write_bytes(blob.read_bytes() + b'tampered')
    elif damage == 'reparse':
        original = s['ledger'].with_name('retained-ledger')
        s['ledger'].rename(original)
        s['ledger'].symlink_to(original, target_is_directory=True)
    if damage in {'wrong_source_root', 'wrong_target_root', 'wrong_snapshot_sha'}:
        marker.write_text(json.dumps(proof), encoding='utf8')
    deny_original_or_executor(s, monkeypatch)
    rows, why = reader.read_history(s['admission'], observed, data_dir=s['restored'])
    assert not rows and why
    assert s['admission'] == s['original_admission']


def test_recomputed_scope_cannot_authorize_path_traversal(restored_history, monkeypatch):
    s = restored_history
    admission = deepcopy(s['admission'])
    admission['scope']['ledger_root'] = str(s['data'] / '../outside/ledger')
    admission['scope_sha256'] = digest(admission['scope'])
    deny_original_or_executor(s, monkeypatch)
    rows, why = reader.read_history(admission, s['observed'], data_dir=s['restored'])
    assert not rows and why == ['COMMAND_SCOPE_UNAVAILABLE']
