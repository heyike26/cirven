"""Ordinary-source plan/copy/verify checks; not Windows or real-runtime proof."""
from pathlib import Path
import hashlib
import json
import shutil

import pytest

from scripts import package_native_companion as builder
from scripts.native_package_bootstrap import PackageError, verify_package
from test_native_package_ide_capability import scene, write
from test_native_package_ocr_closure import component

PREFIX = 'toolchain/opencode-plugins/change-review/'
EXPECTED = {PREFIX+name for name in ('index.mjs','files.mjs','intent.mjs','budget.mjs','lineage.mjs','package.json','README.md','manifest.json')}


def check(value, message='Change Review package closure requirement failed'):
    if not value:
        raise AssertionError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_closure(manifest, output, source):
    rows = {row['path']:row for row in manifest['files']}
    check({name for name in rows if name.startswith(PREFIX)} == EXPECTED)
    for name in EXPECTED:
        check((output/name).read_bytes() == (source/name).read_bytes())
        check(rows[name]['sha256'] == digest(source/name))
        check(rows[name]['bytes'] == (source/name).stat().st_size)
        check(rows[name]['startup_critical'] is True)


def test_actual_source_refresh_plan_copy_verify_and_manifest_keep_exact_current_members(scene, tmp_path):
    source, runtime, structural = scene
    write(source/PREFIX/'tests/own-test.mjs', b'not a runtime dependency')
    write(source/PREFIX/'private-ledger.json', b'not a production closure member')
    plan = builder.make_refresh_plan(source, runtime)
    check({name for name in plan['jobs'] if name.startswith(PREFIX)} == EXPECTED)
    check(not any(name.startswith('runtime/python/') for name in plan['jobs']))
    output = tmp_path/'preview'
    manifest = builder.copy_plan({**structural,'jobs':{**structural['jobs'],**plan['jobs']}}, output)
    check(verify_package(output) == manifest)
    check_closure(manifest, output, source)
    old_runtime = {row['path']:row for row in manifest['files'] if row['path'].startswith('runtime/')}
    write(output/'user-data/private.txt', b'unchanged own draft')
    refreshed = builder.refresh_source(builder.make_refresh_plan(source,runtime), output)
    check(verify_package(output) == refreshed)
    check_closure(refreshed,output,source)
    check({row['path']:row for row in refreshed['files'] if row['path'].startswith('runtime/')} == old_runtime)
    check((output/'user-data/private.txt').read_bytes() == b'unchanged own draft')


def test_actual_full_make_plan_keeps_new_plugin_and_existing_record_ocr_closures(component, tmp_path):
    source,runtime,original_ocr = component
    plan = builder.make_plan(source,runtime)
    check({name for name in plan['jobs'] if name.startswith(PREFIX)} == EXPECTED)
    check(set('runtime/site-packages/'+name for name in original_ocr) <= plan['jobs'].keys())
    check(plan['runtime_audit']['installed_runtime_closure']['distribution_count'] == 77)
    output = tmp_path/'full-structural-preview'
    manifest = builder.copy_plan(plan,output)
    check(verify_package(output) == manifest)
    check_closure(manifest,output,source)


def test_fresh_make_plan_refuses_missing_required_tree_even_with_valid_77_runtime(component):
    source,runtime,_ = component
    shutil.rmtree(source/PREFIX)
    with pytest.raises(PackageError,match='Change Review production directory'):
        builder.make_plan(source,runtime)


@pytest.mark.parametrize('fault', ['tree','manifest','member','hash','manifest_alias','path_escape',
    'file_link','ancestor_link','hardlink','windows_alias'])
def test_missing_changed_or_aliased_plugin_refuses_real_plan(scene, tmp_path, fault):
    source,runtime,_ = scene
    root = source/PREFIX
    manifest = root/'manifest.json'
    if fault == 'tree':shutil.rmtree(root)
    elif fault == 'manifest':manifest.unlink()
    elif fault == 'member':(root/'budget.mjs').unlink()
    elif fault == 'hash':(root/'index.mjs').write_bytes(b'changed code')
    elif fault in {'manifest_alias','path_escape'}:
        value=json.loads(manifest.read_bytes())
        value['files'][0]['path'] = value['files'][1]['path'].upper() if fault=='manifest_alias' else '../outside.mjs'
        manifest.write_text(json.dumps(value),encoding='utf-8')
    elif fault == 'file_link':
        outside=tmp_path/'same-bytes.mjs';(root/'index.mjs').rename(outside);(root/'index.mjs').symlink_to(outside)
    elif fault == 'ancestor_link':
        outside=tmp_path/'same-byte-plugin';root.rename(outside);root.symlink_to(outside,target_is_directory=True)
    elif fault == 'hardlink':
        outside=tmp_path/'hardlink.mjs';outside.hardlink_to(root/'index.mjs')
    elif fault == 'windows_alias':write(root/'INDEX.MJS', b'case alias must not be selected')
    with pytest.raises(PackageError):builder.make_refresh_plan(source,runtime)


def test_plan_to_copy_drift_and_partial_manual_job_selection_cannot_get_success_manifest(scene, tmp_path):
    source,runtime,structural = scene
    plan = builder.make_refresh_plan(source,runtime)
    jobs={**structural['jobs'],**plan['jobs']}
    incomplete=dict(jobs);incomplete.pop(PREFIX+'budget.mjs')
    first=tmp_path/'incomplete'
    with pytest.raises(PackageError,match='Change Review selected payload'):
        builder.copy_plan({**structural,'jobs':incomplete},first)
    check(not (first/'PACKAGE_MANIFEST.json').exists())
    check((first/'PACKAGE_FAILED.json').exists())
    (source/PREFIX/'index.mjs').write_bytes(b'after-plan drift')
    second=tmp_path/'drifted'
    with pytest.raises(PackageError,match='Change Review closure bytes'):
        builder.copy_plan({**structural,'jobs':jobs},second)
    check(not (second/'PACKAGE_MANIFEST.json').exists())


def test_refresh_source_drift_refuses_before_package_history_or_private_mutation(scene, tmp_path):
    source,runtime,structural=scene
    plan=builder.make_refresh_plan(source,runtime)
    output=tmp_path/'stopped-preview'
    builder.copy_plan({**structural,'jobs':{**structural['jobs'],**plan['jobs']}},output)
    private=write(output/'user-data/keep.txt',b'keep')
    before=(output/'PACKAGE_MANIFEST.json').read_bytes()
    (source/PREFIX/'intent.mjs').write_bytes(b'changed after refresh planning')
    with pytest.raises(PackageError,match='Change Review closure bytes'):
        builder.refresh_source(plan,output)
    check((output/'PACKAGE_MANIFEST.json').read_bytes()==before)
    check(private.read_bytes()==b'keep')
    check(not (output/'package-history').exists())
