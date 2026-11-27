"""Actual selected-manifest startup gate; structural files, no native launch."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts import native_package_bootstrap as bootstrap
from scripts import package_native_companion as builder

from test_native_package_ide_capability import scene


def options(expected):
    return SimpleNamespace(verify=True, diagnose=False, expected_manifest_sha256=expected)


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_exact_selected_program_verifies_without_starting_a_business_owner(scene, tmp_path, capsys):
    _source, _runtime, plan = scene
    output = tmp_path / 'program'
    builder.copy_plan(plan, output)
    expected = checksum(output / 'PACKAGE_MANIFEST.json')
    progress = SimpleNamespace(enabled=False)
    assert bootstrap._main_checked(output, options(expected), False, False, progress) == 0
    assert json.loads(capsys.readouterr().out)['qualification'] == 'FILES_ONLY'
    assert not (output / 'user-data').exists()


@pytest.mark.parametrize('expected', ['a' * 64, 'not-a-sha', True])
def test_different_or_invalid_selected_manifest_is_refused_before_payload_or_start(tmp_path, monkeypatch, expected):
    path = tmp_path / 'PACKAGE_MANIFEST.json'
    path.write_text('{}')

    def must_not_verify(*_args, **_kwargs):
        pytest.fail('Different selected program must stop before payload/startup')

    monkeypatch.setattr(bootstrap, 'verify_package', must_not_verify)
    with pytest.raises(bootstrap.PackageError, match='Selected program version changed'):
        bootstrap._main_checked(tmp_path, options(expected), False, False, SimpleNamespace(enabled=False))
    assert not (tmp_path / 'user-data').exists()


def test_manifest_changed_during_actual_verification_never_reaches_start(scene, tmp_path, monkeypatch):
    _source, _runtime, plan = scene
    output = tmp_path / 'program'
    builder.copy_plan(plan, output)
    path = output / 'PACKAGE_MANIFEST.json'
    expected = checksum(path)
    actual_verify = bootstrap.verify_package

    def verify_then_change(*args, **kwargs):
        result = actual_verify(*args, **kwargs)
        value = json.loads(path.read_text())
        value['different_release'] = True
        path.write_text(json.dumps(value))
        return result

    monkeypatch.setattr(bootstrap, 'verify_package', verify_then_change)
    with pytest.raises(bootstrap.PackageError, match='during startup verification'):
        bootstrap._main_checked(output, options(expected), False, False, SimpleNamespace(enabled=False))
    assert not (output / 'user-data').exists()


def test_selected_maintenance_interpreter_must_belong_to_target(tmp_path):
    roots = [tmp_path / 'current', tmp_path / 'older retained version']
    interpreters = [root / 'runtime/python/pythonw.exe' for root in roots]
    for interpreter in interpreters:
        interpreter.parent.mkdir(parents=True)
        interpreter.touch()
    assert bootstrap.selected_installation_root(roots[1], interpreters[1]) == roots[1]
    with pytest.raises(bootstrap.PackageError, match='does not own'):
        bootstrap.selected_installation_root(roots[1], interpreters[0])
