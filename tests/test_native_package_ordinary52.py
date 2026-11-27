"""Real plugin bytes/copy/refresh and shared dependency closure; no runtime claim."""
import hashlib
import pytest

from scripts import package_native_companion as builder
from scripts.native_package_bootstrap import PackageError, verify_package
from test_native_package_ide_capability import scene, write

PREFIX = 'toolchain/opencode-plugins/native-message-context/'
MEMBERS = {PREFIX + name for name in ('index.mjs', 'package.json', 'README.md', 'manifest.json')}
VENDOR = 'toolchain/opencode-plugins/dynamic-workflow/vendor/zod/index.js'


def test_actual_copy_refresh_has_required_reader_and_shared_vendor_once(scene, tmp_path):
    source, runtime, structural = scene
    write(source / PREFIX / 'tests' / 'private-test.mjs', b'not production')
    plan = builder.make_refresh_plan(source, runtime)
    assert {name for name in plan['jobs'] if name.startswith(PREFIX)} == MEMBERS
    pins = builder.native_message_job_pins(plan['jobs'])
    assert MEMBERS <= pins.keys() and VENDOR in pins
    output = tmp_path / 'output'
    first = builder.copy_plan({**structural, 'jobs': {**structural['jobs'], **plan['jobs']}}, output)
    private = write(output / 'user-data' / 'unchanged-draft.txt', b'keep this draft')
    runtime_before = [row for row in first['files'] if row['path'].startswith('runtime/')]
    final = builder.refresh_source(builder.make_refresh_plan(source, runtime), output)
    assert verify_package(output) == final
    assert [row for row in final['files'] if row['path'].startswith('runtime/')] == runtime_before
    assert private.read_bytes() == b'keep this draft'
    rows = {row['path']: row for row in final['files']}
    for name, pin in pins.items():
        assert rows[name]['sha256'] == pin['sha256']
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == pin['sha256']


@pytest.mark.parametrize('fault', ['missing-reader', 'reader-tamper', 'alias', 'vendor-missing', 'vendor-tamper'])
def test_reader_or_shared_dependency_damage_prevents_package(scene, fault):
    source, runtime, _structural = scene
    if fault == 'missing-reader':
        (source / PREFIX / 'index.mjs').unlink()
    elif fault == 'reader-tamper':
        (source / PREFIX / 'index.mjs').write_bytes(b'changed reader')
    elif fault == 'alias':
        write(source / PREFIX / 'INDEX.MJS', b'Windows alias')
    elif fault == 'vendor-missing':
        (source / VENDOR).unlink()
    else:
        (source / VENDOR).write_bytes(b'changed dependency')
    with pytest.raises(PackageError):
        builder.make_refresh_plan(source, runtime)


def test_partial_shared_selection_cannot_write_a_success_manifest(scene, tmp_path):
    source, runtime, structural = scene
    plan = builder.make_refresh_plan(source, runtime)
    jobs = {**structural['jobs'], **plan['jobs']}
    jobs.pop(VENDOR)
    with pytest.raises(PackageError):
        builder.copy_plan({**structural, 'jobs': jobs}, tmp_path / 'partial')
    assert not (tmp_path / 'partial' / 'PACKAGE_MANIFEST.json').exists()
