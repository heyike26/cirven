"""Offline missing/tampered tooling never enters original auto-download startup."""
import json
import os
from pathlib import Path

import pytest

from packages.adapters.opencode_support import support_environment


def test_linux_keeps_existing_environment_without_windows_tooling():
    env = {'PATH': '/usr/bin', 'OPENCODE_DISABLE_AUTOUPDATE': 'true'}
    assert support_environment(env, platform='posix', root='/missing') == env


def test_missing_tooling_refuses_instead_of_leaving_an_auto_download_path(tmp_path):
    env = {'PATH': 'ambient'}
    with pytest.raises(ValueError, match='tooling is missing'):
        support_environment(env, platform='nt', root=tmp_path)
    assert env == {'PATH': 'ambient'}


def test_changed_executable_refuses_before_environment_is_exposed(tmp_path):
    (tmp_path/'SUPPORT_MANIFEST.json').write_text(json.dumps({
        'schema':'eeas.opencode-support.v1', 'version':'15.1.0', 'platform':'windows-x64'}))
    (tmp_path/'rg.exe').write_bytes(b'changed executable')
    env = {'PATH':'ambient'}
    with pytest.raises(ValueError, match='ripgrep bytes differ'):
        support_environment(env, platform='nt', root=tmp_path)
    assert env == {'PATH': 'ambient'}


def test_unreviewed_version_and_linked_directory_are_rejected(tmp_path):
    (tmp_path/'SUPPORT_MANIFEST.json').write_text(json.dumps({
        'schema':'eeas.opencode-support.v1','version':'other','platform':'windows-x64'}))
    with pytest.raises(ValueError, match='version differs'):
        support_environment({},platform='nt',root=tmp_path)
    link=tmp_path/'linked';link.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ValueError, match='Absolute frozen'):
        support_environment({},platform='nt',root=link)


def test_actual_downloaded_frozen_binary_selects_first_without_a_model_call():
    root=Path(os.environ.get('EEAS_TEST_OPENCODE_SUPPORT', 'runtime/opencode-support'))
    if not root.is_dir(): pytest.skip('Separately qualified Windows distribution unavailable')
    result=support_environment({'PATH':'ambient'},platform='nt',root=root)
    assert result['PATH'].startswith(str(root))
    assert result['PATH'].endswith('ambient')


@pytest.fixture
def actual_frozen_windows_support():
    root = Path(os.environ.get('EEAS_TEST_OPENCODE_SUPPORT', 'runtime/opencode-support'))
    if not root.is_dir():
        pytest.skip('Separately qualified Windows distribution unavailable')
    return root


@pytest.mark.parametrize('explicit', ['.EXE;.CMD', ' .COM;.EXE;.BAT;.CMD;.PS1 '])
def test_windows_pathext_preserves_explicit_nonempty_value(
        monkeypatch, actual_frozen_windows_support, explicit):
    monkeypatch.setenv('PATHEXT', '.HOST')
    env = {'PATH': 'ambient', 'PATHEXT': explicit}
    result = support_environment(env, platform='nt', root=actual_frozen_windows_support)
    assert result is env
    assert result['PATHEXT'] == explicit


@pytest.mark.parametrize('incoming', [None, '', ' \t'])
def test_windows_pathext_inherits_only_host_extension_variable(
        monkeypatch, actual_frozen_windows_support, incoming):
    host = ' .COM;.EXE;.BAT;.CMD;.CUSTOM '
    monkeypatch.setenv('PATHEXT', host)
    monkeypatch.setenv('SOURCE38_UNRELATED_ENV', 'MUST_NOT_INHERIT')
    env = {'PATH': 'ambient', 'OPENCODE_CLIENT': 'eeas'}
    if incoming is not None:
        env['PATHEXT'] = incoming
    result = support_environment(env, platform='nt', root=actual_frozen_windows_support)
    assert result['PATHEXT'] == host
    assert set(result) == {'PATH', 'OPENCODE_CLIENT', 'PATHEXT', 'PYTHONUTF8'}
    assert result['PYTHONUTF8'] == '1'
    assert result['OPENCODE_CLIENT'] == 'eeas'


@pytest.mark.parametrize('host', [None, '', ' \t'])
def test_windows_pathext_uses_minimal_fallback_when_host_missing_or_blank(
        monkeypatch, actual_frozen_windows_support, host):
    if host is None:
        monkeypatch.delenv('PATHEXT', raising=False)
    else:
        monkeypatch.setenv('PATHEXT', host)
    result = support_environment({'PATH': 'ambient', 'PATHEXT': ' \t'},
        platform='nt', root=actual_frozen_windows_support)
    assert result['PATHEXT'] == '.COM;.EXE;.BAT;.CMD'


def test_posix_never_adds_pathext_or_changes_explicit_value(monkeypatch):
    monkeypatch.setenv('PATHEXT', '.HOST')
    monkeypatch.setenv('PYTHONUTF8', '1')
    for env in ({'PATH': '/usr/bin'}, {'PATH': '/usr/bin', 'PATHEXT': ''},
                {'PATH': '/usr/bin', 'PYTHONUTF8': '0'},
                {'PATH': '/usr/bin', 'PYTHONUTF8': '1'},
                {'PATH': '/usr/bin', 'PYTHONUTF8': ''}):
        before = dict(env)
        assert support_environment(env, platform='posix', root='/missing') is env
        assert env == before


def test_windows_pythonutf8_defaults_without_inheriting_host_environment(
        monkeypatch, actual_frozen_windows_support):
    monkeypatch.setenv('PYTHONUTF8', '0')
    monkeypatch.setenv('SOURCE38_UNRELATED_ENV', 'MUST_NOT_INHERIT')
    env = {'PATH': 'ambient', 'PATHEXT': '.EXE;.CMD'}
    result = support_environment(env, platform='nt', root=actual_frozen_windows_support)
    assert result is env
    assert result['PYTHONUTF8'] == '1'
    assert result['PATHEXT'] == '.EXE;.CMD'
    assert set(result) == {'PATH', 'PATHEXT', 'PYTHONUTF8'}


@pytest.mark.parametrize('explicit', ['0', '1', ''])
def test_windows_pythonutf8_preserves_every_explicit_caller_value(
        monkeypatch, actual_frozen_windows_support, explicit):
    monkeypatch.setenv('PYTHONUTF8', '1' if explicit == '0' else '0')
    env = {'PATH': 'ambient', 'PATHEXT': '.EXE', 'PYTHONUTF8': explicit}
    result = support_environment(env, platform='nt', root=actual_frozen_windows_support)
    assert result is env
    assert result['PYTHONUTF8'] == explicit


def test_invalid_windows_rg_does_not_add_default_utf8_or_mutate_environment(tmp_path):
    (tmp_path/'SUPPORT_MANIFEST.json').write_text(json.dumps({
        'schema': 'eeas.opencode-support.v1', 'version': '15.1.0', 'platform': 'windows-x64'}))
    # Correct length reaches the real digest check rather than only its size gate.
    (tmp_path/'rg.exe').write_bytes(b'\0' * 4266496)
    env = {'PATH': 'ambient', 'PATHEXT': ''}
    before = dict(env)
    with pytest.raises(ValueError, match='ripgrep bytes differ'):
        support_environment(env, platform='nt', root=tmp_path)
    assert env == before
