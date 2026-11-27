"""Actual plan/copy/refresh bytes; no Windows, installation or executable runtime."""
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

from scripts import package_native_companion as current
from scripts.native_package_bootstrap import PackageError, verify_package
from test_native_package_ide_capability import scene, write

# The exact ten public output paths missed by source18's generic exclusion.
NESTED_LICENSES = (
    '@kobalte/core/node_modules/solid-presence/LICENSE',
    '@pierre/diffs/node_modules/diff/LICENSE',
    '@shikijs/transformers/node_modules/@shikijs/core/LICENSE',
    '@shikijs/transformers/node_modules/@shikijs/types/LICENSE',
    'babel-plugin-jsx-dom-expressions/node_modules/@babel/helper-module-imports/LICENSE',
    'framer-motion/node_modules/motion-dom/LICENSE.md',
    'framer-motion/node_modules/motion-utils/LICENSE.md',
    'lru-cache/node_modules/yallist/LICENSE',
    'solid-transition-size/node_modules/@corvu/utils/LICENSE',
    'vite-plugin-solid/node_modules/babel-preset-solid/LICENSE',
)
PREFIX = 'apps/agent-shell/dist/licenses/dependencies/'


def builder():
    source = os.environ.get('EEAS_LICENSE_PACKAGER_SOURCE')
    if not source:
        return current
    spec = importlib.util.spec_from_file_location('frozen_before_license_packager', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(condition, message='distribution license closure requirement failed'):
    if not condition:
        raise AssertionError(message)


def test_exact_ten_nested_license_texts_enter_real_plan_copy_and_refresh(scene, tmp_path):
    source, runtime, structural = scene
    build = builder()
    expected = {}
    for index, name in enumerate(NESTED_LICENSES):
        raw = ('Original own license-text fixture %d; no dependency code.\n' % index).encode()
        expected[PREFIX + name] = raw
        write(source / (PREFIX + name), raw)
    plan = build.make_refresh_plan(source, runtime)
    check(set(expected) <= plan['jobs'].keys(), 'Actual ten nested distribution license paths were omitted')
    # This is the same real packager copier/manifest, with the pre-existing small
    # structural runtime fixture. It never executes any bundled program.
    output = tmp_path / 'new-preview'
    manifest = build.copy_plan({**structural, 'jobs': {**structural['jobs'], **plan['jobs']}}, output)
    check(verify_package(output) == manifest)
    inventory = {row['path']: row for row in manifest['files']}
    for name, raw in expected.items():
        check((output / name).read_bytes() == raw)
        check(inventory[name]['sha256'] == hashlib.sha256(raw).hexdigest())
    protected = write(output / 'user-data/private-draft.txt', b'own private fixture: preserve')
    changed = next(iter(expected))
    write(source / changed, b'Updated original license-text fixture.\n')
    refreshed = build.refresh_source(build.make_refresh_plan(source, runtime), output)
    check(verify_package(output) == refreshed)
    check((output / changed).read_bytes() == b'Updated original license-text fixture.\n')
    check(protected.read_bytes() == b'own private fixture: preserve')


def test_default_node_modules_and_nonlicense_payloads_stay_outside_plan(scene):
    source, runtime, _ = scene
    rejected = [
        'packages/node_modules/vendor/LICENSE',
        'packages/node_modules/vendor/execute.js',
        PREFIX + 'pkg/node_modules/vendor/LICENSE.exe',
        PREFIX + 'pkg/node_modules/vendor/LICENSE.md.js',
        PREFIX + 'pkg/node_modules/vendor/index.js',
        PREFIX + 'pkg/node_modules/vendor/.git/LICENSE',
        PREFIX + 'pkg/node_modules/vendor/tests/LICENSE',
        PREFIX + 'pkg/node_modules/LICENSE/execute.js',
    ]
    for name in rejected:
        write(source / name, b'original own rejected fixture')
    plan = current.make_refresh_plan(source, runtime)
    check(not (set(rejected) & plan['jobs'].keys()))
    # The generic source/runtime walker itself has no new exception.
    check(not any('node_modules' in path.parts for path in current.files_below(source)))
    write(runtime / 'node_modules/vendor/LICENSE', b'not a runtime override')
    check(not any('node_modules' in path.parts for path in current.files_below(runtime)))


@pytest.mark.parametrize('fault', ['file-link', 'directory-link', 'license-root-link'])
def test_nested_license_links_do_not_escape_or_grant_new_payloads(scene, tmp_path, fault):
    source, runtime, _ = scene
    root = source / PREFIX
    if fault == 'license-root-link':
        outside = tmp_path / 'outside-license-root'
        write(outside / 'pkg/node_modules/vendor/LICENSE', b'outside own fixture')
        root.parent.mkdir(parents=True, exist_ok=True)
        root.symlink_to(outside, target_is_directory=True)
    elif fault == 'directory-link':
        outside = tmp_path / 'outside-module'
        write(outside / 'LICENSE', b'outside own fixture')
        target = root / 'pkg/node_modules/vendor'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(outside, target_is_directory=True)
    else:
        outside = write(tmp_path / 'outside-license.txt', b'outside own fixture')
        target = root / 'pkg/node_modules/vendor/LICENSE'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(outside)
    with pytest.raises(PackageError):
        current.make_refresh_plan(source, runtime)


@pytest.mark.parametrize('raw', [b'binary\0payload', b'\xff\xfeinvalid UTF8', b'X' * (256 * 1024 + 1)])
def test_license_exception_rejects_nontext_or_oversized_members(scene, raw):
    source, runtime, _ = scene
    write(source / (PREFIX + 'pkg/node_modules/vendor/LICENSE'), raw)
    with pytest.raises(PackageError, match='license'):
        current.make_refresh_plan(source, runtime)


def test_even_the_explicit_license_root_does_not_allow_a_dist_symlink(scene, tmp_path):
    source, runtime, _ = scene
    dist = source / 'apps/agent-shell/dist'
    moved = tmp_path / 'ordinary-dist'
    dist.rename(moved)
    dist.symlink_to(moved, target_is_directory=True)
    with pytest.raises(PackageError, match='symlink'):
        current.make_refresh_plan(source, runtime)
