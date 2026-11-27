"""Restoration must resolve owned account/CLI identities without touching the source.

All credentials and executables here are synthetic. No OAuth or model requests.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from packages.adapters import opencode_account as account
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.domain.provider_settings import ProviderSettings
from packages.domain.store import Conflict, InvalidInput
from packages.domain.tasks import digest
from scripts import native_user_state as recovery


def wrapped(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'value': value, 'sha256': digest(value)}), encoding='utf8')


@pytest.fixture
def restored(tmp_path, monkeypatch):
    source = tmp_path / 'old original data'
    native = source / 'subscription-account/native-data'
    native.mkdir(parents=True)
    (native / 'opaque.fixture').write_bytes(b'SYNTHETIC NEVER PARSED AS OAUTH')
    cli = source / 'provider-settings/runtime/fixture-cli'
    cli.parent.mkdir(parents=True)
    cli.write_bytes(b'SYNTHETIC NONEXECUTABLE ORIGINAL CLI')
    monkeypatch.setitem(account.CLI_SHA, os.name, hashlib.sha256(cli.read_bytes()).hexdigest())
    info = native.stat()
    binding = {'schema': 'eeas.native-subscription-account.v1', 'id': 'account-fixture',
        'revision': 1, 'version': account.VERSION, 'source_commit': account.COMMIT,
        'plugin_sha256': account.PLUGIN_SHA256, 'account_data_home': str(native),
        'directory_identity': [info.st_dev, info.st_ino]}
    reference = {'id': binding['id'], 'revision': 1, 'sha256': digest(binding)}
    wrapped(source / 'subscription-account/binding.json', binding)
    profile = {'schema': account.SCHEMA, 'id': 'fixture', 'revision': 1,
        'version': account.VERSION, 'source_commit': account.COMMIT,
        'executable': str(cli), 'executable_sha256': account.CLI_SHA[os.name],
        'provider_id': 'openai', 'protocol': account.PROTOCOL, 'auth_mode': account.AUTH_MODE,
        'model_id': 'fixture-model', 'account_ref': reference,
        'account_data_home': str(native), 'supports_images': False}
    p = source / 'provider-settings/profiles/fixture/0000000001.json'
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({'configuration': profile, 'sha256': digest(profile)}), encoding='utf8')
    stage = tmp_path / 'copy partial'
    destination = tmp_path / 'restored data'
    shutil.copytree(source, stage)
    proof = {'schema_version': 'eeas.restore-provenance.v1',
        'allowed_source_data_dirs': [str(source)], 'snapshot_sha256': '1' * 64,
        'manifest_sha256': '1' * 64, 'snapshot_id': 'original-fixture-snapshot',
        'target_data_dir': str(destination),
        'directory_bindings': recovery._restored_directory_bindings(stage, [str(source)])}
    (stage / 'restore-provenance.json').write_text(json.dumps(proof), encoding='utf8')
    stage.rename(destination)
    # The source path is now unavailable. Falling back to it must never work.
    source.rename(tmp_path / 'held original unavailable')
    pins = {str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in destination.rglob('*') if p.is_file()}
    providers = ProviderSettings(None, destination, tmp_path)
    return {'data': destination, 'source': source, 'binding': binding, 'reference': reference,
        'profile': profile, 'providers': providers, 'pins': pins, 'proof': proof}


def test_restored_account_and_profile_use_copy_without_editing_original_records(restored, monkeypatch):
    data = restored['data']
    seen = []
    original_private = account._private_directory

    def private(path):
        seen.append(Path(path))
        assert not Path(path).is_relative_to(restored['source'])
        return original_private(path)

    monkeypatch.setattr(account, '_private_directory', private)
    projected = account.read_account_binding(data, restored['reference'])
    assert projected['account_data_home'] == str(data / 'subscription-account/native-data')
    assert account.OpenCodeAccount(restored['providers'], data)._binding()[1] == restored['reference']
    config = restored['profile']
    ref = {'id': config['id'], 'revision': 1, 'sha256': digest(config)}
    profile = account.resolve_subscription_profile(config, ref,
        restored['providers'].subscription_binding, require_auth=False,
        runtime_path_resolver=restored['providers'].resolve_runtime_path)
    assert profile.executable == data / 'provider-settings/runtime/fixture-cli'
    assert profile.account_data_home == data / 'subscription-account/native-data'
    assert restored['providers'].get('fixture')['profile_ref'] == ref
    assert seen
    assert {str(p.relative_to(data)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in data.rglob('*') if p.is_file()} == restored['pins']


@pytest.mark.parametrize('damage', ['missing', 'reference', 'saved_identity', 'new_identity',
    'target_root', 'source_root', 'snapshot', 'replacement'])
def test_restore_does_not_weaken_account_replacement_and_scope_guards(restored, damage):
    data = restored['data']
    marker = data / 'restore-provenance.json'
    proof = restored['proof']
    identity = proof['directory_bindings']['subscription-account/native-data']
    if damage == 'missing':
        marker.unlink()
    elif damage == 'replacement':
        native = data / 'subscription-account/native-data'
        native.rename(native.with_name('held original native-data'))
        native.mkdir()
    else:
        if damage == 'reference': identity['reference']['revision'] += 1
        elif damage == 'saved_identity': identity['saved_directory_identity'] = [0, 0]
        elif damage == 'new_identity': identity['restored_directory_identity'] = [0, 0]
        elif damage == 'target_root': proof['target_data_dir'] = str(data.parent / 'other data')
        elif damage == 'source_root': proof['allowed_source_data_dirs'] = [str(data.parent / 'other source')]
        elif damage == 'snapshot': proof['snapshot_sha256'] = 'not-a-snapshot-sha'
        marker.write_text(json.dumps(proof), encoding='utf8')
    with pytest.raises(Conflict):
        account.read_account_binding(data, restored['reference'])


def test_generic_api_provider_also_resolves_only_restored_cli_bytes(restored):
    config = {k: restored['profile'][k] for k in ['id', 'revision', 'version', 'source_commit',
        'executable', 'executable_sha256', 'model_id']}
    config.update(schema='eeas.opencode-provider.v1', provider_id='eeas-local',
        protocol='OPENAI_COMPATIBLE_CHAT_COMPLETIONS', provider_url='http://127.0.0.1:9/v1',
        secret_ref='secret:fixture', transport_kind='MOCK_WIRE')
    provider = OpenCodeProviderRegistry(lambda *_: config, lambda _: 'SYNTHETIC PRIVATE KEY',
        runtime_path_resolver=restored['providers'].resolve_runtime_path)
    ref = {'id': config['id'], 'revision': 1, 'sha256': digest(config)}
    actual = provider.resolve(ref)
    assert actual.executable == restored['data'] / 'provider-settings/runtime/fixture-cli'
    assert actual.reference == ref
    actual.executable.write_bytes(b'CHANGED SYNTHETIC CLI')
    with pytest.raises(Conflict): provider.resolve(ref)


def test_foreign_runtime_cannot_escape_a_restored_data_root(restored):
    foreign = restored['data'].parent / 'foreign fixed-cli'
    foreign.write_bytes(b'SYNTHETIC NONEXECUTABLE ORIGINAL CLI')
    with pytest.raises(InvalidInput):
        restored['providers'].resolve_runtime_path(str(foreign))


def test_restore_records_do_not_accept_a_binding_pointing_outside_the_snapshot(restored):
    data = restored['data']
    value = dict(restored['binding'], account_data_home=str(data.parent / 'foreign account'))
    wrapped(data / 'subscription-account/binding.json', value)
    with pytest.raises(recovery.RecoveryError, match='outside'):
        recovery._restored_directory_bindings(data, [str(restored['source'])])
