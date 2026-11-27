"""Internal storage policy; authored for the later concentrated debug phase."""
from pathlib import Path

import pytest

from scripts import package_native_companion as builder


def test_internal_output_validation_does_not_create_directories(tmp_path, monkeypatch):
    root = tmp_path / 'internal' / '.development'
    monkeypatch.setattr(builder, 'INTERNAL_ARTIFACT_ROOT', root)
    target = root / 'package' / 'new-preview'
    assert builder.disk_target(target) == target
    assert not root.exists()


@pytest.mark.parametrize('path', ['/mnt/d/new-package', '/mnt/e/new-package',
                                  'D:\\new-package', 'd:new-package',
                                  '\\\\server\\share\\new-package', '/tmp/new-package'])
def test_external_windows_and_unowned_outputs_are_rejected(path):
    with pytest.raises(builder.PackageError):
        builder.disk_target(path)


def test_internal_root_itself_and_parent_escape_are_rejected(tmp_path, monkeypatch):
    root = tmp_path / 'internal' / '.development'
    monkeypatch.setattr(builder, 'INTERNAL_ARTIFACT_ROOT', root)
    for path in (root, root / '..' / 'outside'):
        with pytest.raises(builder.PackageError):
            builder.disk_target(path)
    assert not root.exists()


def test_symlinked_internal_output_or_storage_root_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / 'internal'; root.mkdir()
    destination = tmp_path / 'retained'; destination.mkdir()
    link = root / 'link'; link.symlink_to(destination, target_is_directory=True)
    monkeypatch.setattr(builder, 'INTERNAL_ARTIFACT_ROOT', root)
    with pytest.raises(builder.PackageError):
        builder.disk_target(link / 'new-preview')
    monkeypatch.setattr(builder, 'INTERNAL_ARTIFACT_ROOT', link)
    with pytest.raises(builder.PackageError):
        builder.disk_target(link / 'new-preview')
    assert list(destination.iterdir()) == []


def test_invalid_archive_fails_before_plan_or_package_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, 'INTERNAL_ARTIFACT_ROOT', tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid archive must be rejected before reading runtime or copying payload')
    monkeypatch.setattr(builder, 'make_plan', forbidden)
    monkeypatch.setattr(builder, 'copy_plan', forbidden)
    with pytest.raises(builder.PackageError):
        builder.main(['--runtime-root', '/mnt/d/existing-readonly-runtime',
                      '--output', str(tmp_path / 'new-preview'), '--archive', '/mnt/d/new.zip'])
    assert not (tmp_path / 'new-preview').exists()


def test_existing_archive_is_preserved_before_planning(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, 'INTERNAL_ARTIFACT_ROOT', tmp_path)
    archive = tmp_path / 'retained.zip'; archive.write_bytes(b'original unique archive')
    def forbidden(*args, **kwargs):
        pytest.fail('Existing archive must not trigger a package copy')
    monkeypatch.setattr(builder, 'make_plan', forbidden)
    with pytest.raises(builder.PackageError):
        builder.main(['--runtime-root', '/mnt/d/existing-readonly-runtime',
                      '--output', str(tmp_path / 'new-preview'), '--archive', str(archive)])
    assert archive.read_bytes() == b'original unique archive'


def test_missing_output_never_chooses_an_ambient_drive(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Output must be explicit before planning')
    monkeypatch.setattr(builder, 'make_plan', forbidden)
    with pytest.raises(builder.PackageError, match='explicit internal'):
        builder.main(['--runtime-root', '/mnt/d/existing-readonly-runtime'])


def test_readonly_runtime_audit_remains_available_without_output(monkeypatch, capsys):
    seen = []
    def audit(path):
        seen.append(Path(path))
        return {'wheel_jobs': [], 'qualification': 'STRUCTURAL_STUB_ONLY'}
    monkeypatch.setattr(builder, 'audit_selected_runtime', audit)
    assert builder.main(['--runtime-root', '/mnt/d/existing-readonly-runtime', '--audit-only']) == 0
    assert seen == [Path('/mnt/d/existing-readonly-runtime')]
    assert 'STRUCTURAL_STUB_ONLY' in capsys.readouterr().out
