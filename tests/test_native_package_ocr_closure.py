"""Actual original file/wheel fixtures; no OCR inference or Windows qualification."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from scripts import package_native_companion as builder
from scripts.native_package_bootstrap import PackageError, verify_package
from test_native_package_ide_capability import (
    write_change_review_bundle, write_native_message_bundle, write_native_review_bundle,
)


def file_bytes(path, raw=b'original fixture\n'):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(raw)
    return path


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def save_ocr(path, files):
    payload = {'schema_version':'eeas.offline-ocr-models.v1','engine_files':{'windows':{'certifi':files}},
        'packages':{'certifi':'1.0'},'fixture':'STRUCTURAL_NOT_ACTUAL_ENGINE'}
    file_bytes(path,canonical({'payload':payload,'sha256':hashlib.sha256(canonical(payload)).hexdigest()}))


@pytest.fixture
def component(tmp_path):
    source,runtime = tmp_path/'source',tmp_path/'selected-runtime'
    for name in builder.SOURCE_TREES:
        if not name.startswith('toolchain/opencode-plugins/'):
            file_bytes(source/name/'original.txt')
    write_change_review_bundle(source)
    write_native_review_bundle(source)
    write_native_message_bundle(source)
    for name in (*builder.SOURCE_EXTRAS,'apps/desktop-host/package.json','apps/agent-shell/package.json'):
        file_bytes(source/name)
    file_bytes(source/builder.ENTRYPOINTS['main'])
    site = runtime/'runtime/site-packages'
    main = file_bytes(site/'certifi/__init__.py',b'# original certifi fixture\n')
    first = file_bytes(site/'certifi/tests/__init__.py',b'')
    second = file_bytes(site/'certifi/tests/test_certify.py',b'# original wheel test fixture\n')
    pinned = {p.relative_to(site).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (main,first,second)}
    save_ocr(source/'toolchain/ocr/MODELS.json',pinned)
    # Real77-package metadata/RECORD/wheel fixtures satisfy the actual existing
    # audit code. Executable bytes are declared fixtures, never Windows proof.
    distributions = []
    for index,name in enumerate(['certifi']+[f'original-fixture-{n}' for n in range(76)]):
        metadata = file_bytes(site/(name+'-1.0.dist-info/METADATA'),('Name: '+name+'\nVersion: 1.0\n').encode())
        members = [metadata]+([main,first,second] if name == 'certifi' else [])
        record = []
        for path in members:
            raw = path.read_bytes();encoded = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
            record.append(path.relative_to(site).as_posix()+',sha256='+encoded+','+str(len(raw)))
        file_bytes(metadata.with_name('RECORD'),('\n'.join(record)+'\n').encode())
        filename = name.replace('-','_')+'-1.0-py3-none-any.whl'
        wheel = runtime/'toolchain/runtime-wheels'/filename
        wheel.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(wheel,'x') as archive:
            for path in members:archive.writestr(path.relative_to(site).as_posix(),path.read_bytes())
        distributions.append({'name':name,'version':'1.0','filename':filename,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'bytes':wheel.stat().st_size})
    file_bytes(runtime/'dependency-closure-windows310.json',json.dumps({'target':'windows-x64-py310','packages':distributions}).encode())
    file_bytes(runtime/'requirements-focused-native-windows310.lock')
    executables = {}
    for key in ('python','electron'):
        path = file_bytes(runtime/builder.ENTRYPOINTS[key],b'ORIGINAL STRUCTURAL EXECUTABLE FIXTURE NOT WINDOWS\n')
        executables[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    file_bytes(runtime/'runtime/opencode/opencode.exe')
    component_manifest = file_bytes(runtime/builder.ENTRYPOINTS['component_manifest'],b'{"wheels":[]}')
    receipt = {'records':[{'component':name,'executable_sha256':digest} for name,digest in executables.items()],
        'component_manifest_sha256':hashlib.sha256(component_manifest.read_bytes()).hexdigest()}
    file_bytes(runtime/'RUNTIME_RECEIPT.json',json.dumps(receipt).encode())
    file_bytes(runtime/builder.ENTRYPOINTS['readonly_manifest'])
    support = runtime/'runtime/opencode-support'
    executable = file_bytes(support/'rg.exe',b'original support fixture')
    archive = file_bytes(support/'ripgrep-15.1.0-x86_64-pc-windows-msvc.zip',b'original support archive fixture')
    file_bytes(support/'SUPPORT_MANIFEST.json',json.dumps({'schema':'eeas.opencode-support.v1','platform':'windows-x64',
        'version':'15.1.0','archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
        'files':[{'path':'rg.exe','bytes':executable.stat().st_size,'sha256':hashlib.sha256(executable.read_bytes()).hexdigest()}]}).encode())
    for name in ('toolchain/components/windows-capture-2.0.1','toolchain/ocr/models','toolchain/ocr/licenses',
            'toolchain/fonts','toolchain/ui-fonts/noto-serif-sc'):
        file_bytes(runtime/name/'original.txt')
    file_bytes(source/'toolchain/components/opencode-v1.19.0/manifest.json',b'{"files":[]}')
    file_bytes(source/'packages/tests/should-still-be-pruned.py')
    return source,runtime,pinned


def legacy_package(component, output):
    source,runtime,pinned = component
    plan = builder.make_plan(source,runtime)
    # Reproduce the omitted installed wheel tests while keeping the reviewed plugins intact.
    plan.pop('ocr_runtime')
    plan['jobs'] = {name:path for name,path in plan['jobs'].items()
        if not (name.startswith('runtime/site-packages/') and '/tests/' in name)}
    return builder.copy_plan(plan,output)


def test_new_package_includes_all_bound_wheel_tests_but_prunes_development_tests(component, tmp_path):
    source,runtime,pinned = component
    plan = builder.make_plan(source,runtime)
    assert 'tests' in builder.EXCLUDE_DIRS
    assert 'packages/tests/should-still-be-pruned.py' not in plan['jobs']
    assert set('runtime/site-packages/'+name for name in pinned) <= plan['jobs'].keys()
    output = tmp_path/'new-package'
    manifest = builder.copy_plan(plan,output)
    assert verify_package(output) == manifest
    inventory = {row['path']:row for row in manifest['files']}
    for name,digest in pinned.items():
        target = 'runtime/site-packages/'+name
        assert inventory[target]['sha256'] == digest
        assert hashlib.sha256((output/target).read_bytes()).hexdigest() == digest
    assert manifest['runtime_audit']['ocr_engine_closure']['member_count'] == len(pinned)


def test_non_ocr_dependency_record_tests_survive_plan_copy_archive_and_relocation(component, tmp_path):
    source,runtime,_ = component
    site = runtime/'runtime/site-packages'
    member = file_bytes(site/'original_fixture_0/tests/test_original.py',b'# registered non-OCR wheel original\n')
    unregistered = file_bytes(member.with_name('developer-only.py'),b'# not a RECORD member\n')
    record = site/'original-fixture-0-1.0.dist-info/RECORD'
    raw = member.read_bytes()
    encoded = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
    with record.open('a') as stream:
        stream.write(f'{member.relative_to(site).as_posix()},sha256={encoded},{len(raw)}\n')
    plan = builder.make_plan(source,runtime)
    target = 'runtime/site-packages/'+member.relative_to(site).as_posix()
    assert target in plan['jobs']
    assert 'runtime/site-packages/'+unregistered.relative_to(site).as_posix() not in plan['jobs']
    assert plan['runtime_audit']['installed_runtime_closure']['distribution_count'] == 77
    output = tmp_path/'new-registered-closure'
    manifest = builder.copy_plan(plan,output)
    archive = tmp_path/'registered-closure.zip'
    builder.archive_payload(output,archive,manifest)
    moved = tmp_path/'moved-registered-closure'
    shutil.move(output,moved)
    assert verify_package(moved) == manifest
    with zipfile.ZipFile(archive) as handle:
        assert handle.read(target) == raw
        assert not any('developer-only.py' in name for name in handle.namelist())
    assert builder.check_record(moved/'runtime/site-packages',moved/'runtime/site-packages/original-fixture-0-1.0.dist-info') == 2


@pytest.mark.parametrize('fault',['changed_after_plan','escaped_record'])
def test_dependency_record_boundary_refuses_changed_or_escaped_members(component,tmp_path,fault):
    source,runtime,_ = component
    site = runtime/'runtime/site-packages'
    if fault == 'escaped_record':
        outside = file_bytes(runtime/'outside.py',b'outside selected site\n')
        encoded = base64.urlsafe_b64encode(hashlib.sha256(outside.read_bytes()).digest()).decode().rstrip('=')
        with (site/'original-fixture-0-1.0.dist-info/RECORD').open('a') as stream:
            stream.write(f'../../outside.py,sha256={encoded},{outside.stat().st_size}\n')
        with pytest.raises(PackageError,match='escapes selected site-packages'):
            builder.make_plan(source,runtime)
    else:
        plan = builder.make_plan(source,runtime)
        member = site/'original-fixture-0-1.0.dist-info/METADATA'
        member.write_bytes(member.read_bytes()+b'Changed: after audit\n')
        with pytest.raises(PackageError,match='Pinned dependency RECORD source changed'):
            builder.copy_plan(plan,tmp_path/'refused-changed-record')


def test_explicit_refresh_repairs_omitted_pinned_files_preserves_old_bindings_and_audit(component, tmp_path):
    source,runtime,pinned = component
    output = tmp_path/'old-preview'
    old = legacy_package(component,output)
    old_manifest = (output/'PACKAGE_MANIFEST.json').read_bytes()
    original_models = (output/'toolchain/ocr/MODELS.json').read_bytes()
    existing = output/'runtime/site-packages/certifi/__init__.py'
    identity = (existing.stat().st_ino,existing.stat().st_mtime_ns,existing.read_bytes())
    refreshed = builder.refresh_source(builder.make_refresh_plan(source,runtime,repair_ocr_runtime=True),output)
    assert verify_package(output) == refreshed
    report = refreshed['source_refresh']['ocr_runtime_repair']
    assert len(report['added_files']) == 2 and report['verified_existing_members'] == 1
    assert report['existing_runtime_bytes'] == 'NEVER_OVERWRITTEN'
    assert report['windows_ocr_inference'] == 'NOT_RUN'
    assert (existing.stat().st_ino,existing.stat().st_mtime_ns,existing.read_bytes()) == identity
    assert (output/'toolchain/ocr/MODELS.json').read_bytes() == original_models
    new_rows = {row['path']:row for row in refreshed['files']}
    assert all(new_rows[row['path']] == row for row in old['files'] if row['path'].startswith('runtime/'))
    history = output/refreshed['source_refresh']['history_relative_path']
    assert (history/'PACKAGE_MANIFEST.before.json').read_bytes() == old_manifest
    assert json.loads((history/'OCR_RUNTIME_REPAIR.json').read_bytes()) == report
    archive = tmp_path/'repaired.zip'
    builder.archive_payload(output,archive,refreshed)
    with zipfile.ZipFile(archive) as package:
        assert all(package.read('runtime/site-packages/'+name) == (runtime/'runtime/site-packages'/name).read_bytes() for name in pinned)


@pytest.mark.parametrize('registered',[False,True])
def test_different_existing_runtime_bytes_are_never_overwritten(component, tmp_path, registered):
    source,runtime,_pinned = component
    output = tmp_path/'different-preview'
    if registered:builder.copy_plan(builder.make_plan(source,runtime),output)
    else:legacy_package(component,output)
    path = output/'runtime/site-packages/certifi/tests/test_certify.py'
    file_bytes(path,b'UNAPPROVED EXISTING RUNTIME BYTES\n')
    before = (output/'PACKAGE_MANIFEST.json').read_bytes()
    with pytest.raises(PackageError,match='refusing overwrite'):
        builder.refresh_source(builder.make_refresh_plan(source,runtime,repair_ocr_runtime=True),output)
    assert path.read_bytes() == b'UNAPPROVED EXISTING RUNTIME BYTES\n'
    assert (output/'PACKAGE_MANIFEST.json').read_bytes() == before
    assert not (output/'package-history').exists()


def test_missing_registered_member_can_be_restored_without_changing_its_binding(component, tmp_path):
    source,runtime,_pinned = component
    output = tmp_path/'registered-missing'
    old = builder.copy_plan(builder.make_plan(source,runtime),output)
    name = 'runtime/site-packages/certifi/tests/test_certify.py'
    (output/name).unlink()
    updated = builder.refresh_source(builder.make_refresh_plan(source,runtime,repair_ocr_runtime=True),output)
    assert updated['source_refresh']['ocr_runtime_repair']['restored_files'] == [name]
    assert next(row for row in old['files'] if row['path']==name) == next(row for row in updated['files'] if row['path']==name)
    assert verify_package(output) == updated


def test_identical_unregistered_member_is_bound_without_rewriting_it(component, tmp_path):
    source,runtime,_pinned = component
    output = tmp_path/'matching-unregistered'
    legacy_package(component,output)
    name = 'runtime/site-packages/certifi/tests/test_certify.py'
    path = file_bytes(output/name,(runtime/name).read_bytes())
    before = (path.stat().st_ino,path.stat().st_mtime_ns)
    updated = builder.refresh_source(builder.make_refresh_plan(source,runtime,repair_ocr_runtime=True),output)
    assert name in updated['source_refresh']['ocr_runtime_repair']['registered_identical_files']
    assert (path.stat().st_ino,path.stat().st_mtime_ns) == before
    assert verify_package(output) == updated


def test_normal_refresh_does_not_silently_repair_runtime(component, tmp_path):
    source,runtime,_pinned = component
    output = tmp_path/'explicit-required'
    legacy_package(component,output)
    updated = builder.refresh_source(builder.make_refresh_plan(source,runtime),output)
    assert 'ocr_runtime_repair' not in updated['source_refresh']
    assert not (output/'runtime/site-packages/certifi/tests/__init__.py').exists()


def test_modified_ocr_manifest_binding_is_refused_before_any_mutation(component, tmp_path):
    source,runtime,pinned = component
    output = tmp_path/'old-ocr-binding'
    legacy_package(component,output)
    before = (output/'PACKAGE_MANIFEST.json').read_bytes()
    save_ocr(source/'toolchain/ocr/MODELS.json',{key:value for key,value in pinned.items() if '/tests/' not in key})
    with pytest.raises(PackageError,match='unchanged existing OCR manifest'):
        builder.refresh_source(builder.make_refresh_plan(source,runtime,repair_ocr_runtime=True),output)
    assert (output/'PACKAGE_MANIFEST.json').read_bytes() == before
    assert not (output/'package-history').exists()


@pytest.mark.parametrize('fault',['engine-drift','engine-missing','manifest-digest','escaping-member'])
def test_pinned_source_drift_and_unsafe_manifest_never_get_repaired(component, fault):
    source,runtime,pinned = component
    manifest = source/'toolchain/ocr/MODELS.json'
    if fault == 'engine-drift':file_bytes(runtime/'runtime/site-packages/certifi/tests/test_certify.py',b'changed selected bytes')
    if fault == 'engine-missing':(runtime/'runtime/site-packages/certifi/tests/test_certify.py').unlink()
    if fault == 'manifest-digest':manifest.write_bytes(manifest.read_bytes().replace(b'"sha256":',b'"invalid":',1))
    if fault == 'escaping-member':save_ocr(manifest,{'../../outside.py':'0'*64})
    with pytest.raises(PackageError):builder.ocr_runtime_inventory(manifest,runtime)


def test_source_change_after_planning_fails_copy_against_original_ocr_pin(component, tmp_path):
    source,runtime,_pinned = component
    plan = builder.make_plan(source,runtime)
    file_bytes(runtime/'runtime/site-packages/certifi/tests/test_certify.py',b'changed after plan')
    output = tmp_path/'failed-copy'
    with pytest.raises(PackageError,match='Pinned OCR engine source changed'):
        builder.copy_plan(plan,output)
    assert (output/'PACKAGE_FAILED.json').exists()
    assert not (output/'PACKAGE_MANIFEST.json').exists()


def test_concurrently_appearing_runtime_file_is_not_overwritten(component, tmp_path, monkeypatch):
    source,runtime,_pinned = component
    output = tmp_path/'race-preview'
    legacy_package(component,output)
    before = (output/'PACKAGE_MANIFEST.json').read_bytes()
    plan = builder.make_refresh_plan(source,runtime,repair_ocr_runtime=True)
    member = 'runtime/site-packages/certifi/tests/test_certify.py'
    original_copy = shutil.copyfile
    def concurrent_file(source,destination,*args,**kwargs):
        value = original_copy(source,destination,*args,**kwargs)
        if '/incoming/'+member in Path(destination).as_posix():file_bytes(output/member,b'concurrent human bytes')
        return value
    monkeypatch.setattr(builder.shutil,'copyfile',concurrent_file)
    with pytest.raises(FileExistsError):builder.refresh_source(plan,output)
    assert (output/member).read_bytes() == b'concurrent human bytes'
    assert (output/'PACKAGE_MANIFEST.json').read_bytes() == before
    assert list((output/'package-history').glob('*/REFRESH_FAILED.json'))


def test_repair_flag_cannot_be_silently_used_for_copy_or_audit(component, tmp_path):
    source,runtime,_pinned = component
    for extra in ([],['--audit-only']):
        with pytest.raises(PackageError,match='requires explicit --refresh-source'):
            builder.main(['--source-root',str(source),'--runtime-root',str(runtime),'--output',str(tmp_path/'not-created'),
                '--repair-ocr-runtime',*extra])
    assert not (tmp_path/'not-created').exists()
