"""Real stopped-file backup/restore; no Windows/account/startup qualification."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from scripts import native_user_state as recovery
from scripts import native_state_protocols as protocols

ROOT = Path(__file__).resolve().parents[1]


def pin(path):
    return {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def program(root, *, data=False):
    files = {'runtime/electron/version': b'42.3.3',
             'runtime/opencode/opencode.exe': b'original structural CLI, never executed',
             'packages/domain/migrations/001_original.sql': b'original migration contract'}
    rows = []
    for name, raw in files.items():
        rows.append({'path': name, **pin(write(root / name, raw))})
    write(root / 'PACKAGE_MANIFEST.json', json.dumps({
        'schema': 'eeas.native-companion-package.v1', 'files': rows,
    }).encode())
    if data:
        private = root / 'user-data'
        private.mkdir()
        connection = sqlite3.connect(private / 'eeas.sqlite3')
        try:
            connection.executescript("CREATE TABLE schema_migrations(version TEXT);"
                "INSERT INTO schema_migrations VALUES('001_original.sql');"
                "CREATE TABLE operations(id TEXT,status TEXT);"
                "INSERT INTO operations VALUES('original','UNKNOWN_OUTCOME');")
        finally:
            connection.close()
        write(private / 'appdata/original-draft', '未发送原稿 😀'.encode())
    return root


@pytest.mark.parametrize('relative', [
    'native-capabilities/experience-provenance/skills/original/0000000001.json',
    'native-capabilities/experience-controls/exact/head.json',
])
def test_legacy_program_cannot_restore_negative_authority_state_it_does_not_understand(tmp_path, relative):
    source = program(tmp_path / 'source', data=True)
    payload = b'{"status":"PAUSED","operation_id":"original-unknown","automatic_replay":false}'
    write(source / 'user-data' / relative, payload)
    saved = recovery.backup(source, tmp_path / 'snapshot')
    target = program(tmp_path / 'older-compatible-database-only')
    source_hash = pin(source / 'user-data/eeas.sqlite3')
    with pytest.raises(recovery.RecoveryError, match='state protocol'):
        recovery.restore(tmp_path / 'snapshot', target, saved['manifest_sha256'])
    assert not (target / 'user-data').exists()
    assert not list(target.glob('user-data.partial-*'))
    assert pin(source / 'user-data/eeas.sqlite3') == source_hash
    assert (source / 'user-data' / relative).read_bytes() == payload


def test_ordinary_old_snapshot_keeps_its_existing_compatible_restore_route(tmp_path):
    source = program(tmp_path / 'source', data=True)
    saved = recovery.backup(source, tmp_path / 'snapshot')
    target = program(tmp_path / 'older-program')
    result = recovery.restore(tmp_path / 'snapshot', target, saved['manifest_sha256'])
    assert result['status'] == 'RESTORE_COMPLETE'
    assert result['model_or_agent_started'] is False
    assert (target / 'user-data/appdata/original-draft').read_bytes() == '未发送原稿 😀'.encode()
    assert pin(target / 'user-data/eeas.sqlite3') == pin(source / 'user-data/eeas.sqlite3')


def supported_program(root):
    program(root)
    path = root / 'PACKAGE_MANIFEST.json'
    manifest = json.loads(path.read_text())
    for name in sorted({name for names in protocols.READERS.values() for name in names}):
        manifest['files'].append({'path': name, **pin(write(root / name, (ROOT / name).read_bytes()))})
    manifest['durable_state_protocols'] = protocols.metadata(manifest['files'])
    path.write_text(json.dumps(manifest))
    return root


def authority_snapshot(tmp_path):
    source = program(tmp_path / 'source', data=True)
    name = 'native-capabilities/experience-controls/exact/head.json'
    raw = b'{"state":"PAUSED","original_operation":"unknown-1","replay":false}'
    write(source / 'user-data' / name, raw)
    saved = recovery.backup(source, tmp_path / 'snapshot')
    return source, tmp_path / 'snapshot', saved, name, raw


def resign_snapshot(root, mutate):
    path = root / recovery.MANIFEST
    value = json.loads(path.read_text())
    mutate(value)
    path.write_text(json.dumps(value))
    digest = pin(path)['sha256']
    (root / (recovery.MANIFEST + '.sha256')).write_text(digest)
    return digest


def test_supported_target_preflight_is_readonly_then_restores_exact_original_state(tmp_path):
    source, snapshot, saved, name, raw = authority_snapshot(tmp_path)
    target = supported_program(tmp_path / 'compatible-older-release')
    report = recovery.check_target(snapshot, target, saved['manifest_sha256'])
    assert report['status'] == 'RESTORE_TARGET_COMPATIBLE'
    assert report['required_state_protocols'] == sorted([protocols.SOURCE, protocols.PAUSE])
    assert not report['user_data_written'] and not report['model_or_agent_started']
    assert not (target / 'user-data').exists() and not list(target.glob('*.partial-*'))
    result = recovery.restore(snapshot, target, saved['manifest_sha256'],
                              expected_program_sha256=report['program_manifest_sha256'])
    assert result['status'] == 'RESTORE_COMPLETE' and not result['model_or_agent_started']
    assert (target / 'user-data' / name).read_bytes() == raw
    assert (source / 'user-data' / name).read_bytes() == raw
    assert pin(target / 'user-data/eeas.sqlite3') == pin(source / 'user-data/eeas.sqlite3')


def test_changed_but_still_compatible_program_cannot_replace_the_explicitly_checked_version(tmp_path):
    _source, snapshot, saved, _name, _raw = authority_snapshot(tmp_path)
    target = supported_program(tmp_path / 'target')
    report = recovery.check_target(snapshot, target, saved['manifest_sha256'])
    path = target / 'PACKAGE_MANIFEST.json'
    value = json.loads(path.read_text())
    value['different_compatible_release'] = True
    path.write_text(json.dumps(value))
    with pytest.raises(recovery.RecoveryError, match='explicitly checked version'):
        recovery.restore(snapshot, target, saved['manifest_sha256'],
                         expected_program_sha256=report['program_manifest_sha256'])
    assert not (target / 'user-data').exists() and not list(target.glob('user-data.partial-*'))


@pytest.mark.parametrize('damage', ['missing_reader', 'modified_reader', 'unbound_pin', 'unsupported_version'])
def test_target_protocol_claim_requires_complete_actual_manifest_bound_readers(tmp_path, damage):
    _source, snapshot, saved, _name, _raw = authority_snapshot(tmp_path)
    target = supported_program(tmp_path / 'target')
    path = target / 'PACKAGE_MANIFEST.json'
    manifest = json.loads(path.read_text())
    reader = protocols.READERS[protocols.PAUSE][0]
    if damage == 'missing_reader':
        del manifest['durable_state_protocols']['protocols'][protocols.PAUSE]['readers'][reader]
    elif damage == 'modified_reader':
        (target / reader).write_bytes(b'# changed after program admission\n')
    elif damage == 'unbound_pin':
        manifest['durable_state_protocols']['protocols'][protocols.PAUSE]['readers'][reader] = 'a' * 64
    else:
        manifest['durable_state_protocols']['schema'] = 'future-unknown'
    path.write_text(json.dumps(manifest))
    with pytest.raises(recovery.RecoveryError, match='state protocol'):
        recovery.restore(snapshot, target, saved['manifest_sha256'])
    assert not (target / 'user-data').exists() and not list(target.glob('*.partial-*'))


def test_legacy_backup_without_requirement_field_still_derives_negative_authority(tmp_path):
    _source, snapshot, _saved, _name, _raw = authority_snapshot(tmp_path)
    digest = resign_snapshot(snapshot, lambda v: v.pop('required_state_protocols'))
    value, _ = recovery.verify(snapshot, digest)
    assert 'required_state_protocols' not in value
    with pytest.raises(recovery.RecoveryError, match='state protocol'):
        recovery.restore(snapshot, program(tmp_path / 'legacy-target'), digest)
    target = supported_program(tmp_path / 'supported-target')
    assert recovery.restore(snapshot, target, digest)['status'] == 'RESTORE_COMPLETE'


def test_removing_declared_requirement_does_not_override_snapshot_members(tmp_path):
    _source, snapshot, _saved, _name, _raw = authority_snapshot(tmp_path)
    digest = resign_snapshot(snapshot, lambda v: v.update(required_state_protocols=[]))
    with pytest.raises(recovery.RecoveryError, match='state protocol requirements'):
        recovery.verify(snapshot, digest)


@pytest.mark.parametrize('change', ['reader', 'manifest'])
def test_reader_or_program_changed_during_copy_cannot_publish_a_restore(tmp_path, monkeypatch, change):
    _source, snapshot, saved, _name, _raw = authority_snapshot(tmp_path)
    target = supported_program(tmp_path / 'target')
    actual_copy = recovery._copy

    def copy_then_change_reader(*args):
        actual_copy(*args)
        if change == 'reader':
            (target / protocols.READERS[protocols.PAUSE][0]).write_bytes(b'# concurrent change\n')
        else:
            path = target / 'PACKAGE_MANIFEST.json'
            value = json.loads(path.read_text())
            value['concurrent_change'] = True
            path.write_text(json.dumps(value))

    monkeypatch.setattr(recovery, '_copy', copy_then_change_reader)
    with pytest.raises(recovery.RecoveryError, match='reader bytes differ|Target program changed'):
        recovery.restore(snapshot, target, saved['manifest_sha256'])
    assert not (target / 'user-data').exists()
    assert len(list(target.glob('user-data.partial-*'))) == 1


def test_target_manifest_change_during_initial_compatibility_scan_is_not_admitted(tmp_path, monkeypatch):
    _source, snapshot, saved, _name, _raw = authority_snapshot(tmp_path)
    target = supported_program(tmp_path / 'target')
    actual_installation = recovery._installation

    def inspect_then_change(path):
        result = actual_installation(path)
        manifest_path = target / 'PACKAGE_MANIFEST.json'
        value = json.loads(manifest_path.read_text())
        value['concurrent_change'] = True
        manifest_path.write_text(json.dumps(value))
        return result

    monkeypatch.setattr(recovery, '_installation', inspect_then_change)
    with pytest.raises(recovery.RecoveryError, match='Target program changed during compatibility check'):
        recovery.check_target(snapshot, target, saved['manifest_sha256'])
    assert not (target / 'user-data').exists() and not list(target.glob('*.partial-*'))


def test_detection_is_bounded_to_durable_authority_locations():
    file = {'kind': 'file'}
    assert protocols.requirements({'project/native-capabilities/experience-controls/head.json': file}) == []
    assert protocols.requirements({'native-capabilities/experience-controls': {'kind': 'directory'}}) == []
    assert protocols.requirements({'NATIVE-CAPABILITIES/EXPERIENCE-CONTROLS/a/head.json': file}) == sorted([protocols.SOURCE, protocols.PAUSE])
    assert protocols.requirements({'agent-sessions/project/session/experience-candidates/source-policy/ref/head.json': file}) == [protocols.SOURCE]
    assert protocols.requirements({'agent-sessions/project/session/experience-candidates/source-authority/ref.json': file}) == [protocols.SOURCE]


def test_packager_never_declares_a_partly_missing_reader_group():
    with pytest.raises(protocols.StateProtocolError, match='Incomplete packaged state protocol'):
        protocols.metadata([{'path': protocols.READERS[protocols.SOURCE][0], 'sha256': 'a' * 64}])
