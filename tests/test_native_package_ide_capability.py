"""Actual copying/refresh/archive/bytes, not Windows or runnable-runtime proof."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile

import pytest

from scripts import package_native_companion as builder
from scripts.native_package_bootstrap import PackageError, read_manifest, verify_package


def check(value, message='packaged IDE capability requirement failed'):
    if not value:
        raise AssertionError(message)


def write(path, raw=b'original structural fixture, NOT A WINDOWS EXECUTABLE\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def write_change_review_bundle(source):
    """Independent second bundle: actual frozen six-member source, tiny files."""
    repository = Path(__file__).resolve().parents[1]
    prefix = 'toolchain/opencode-plugins/change-review'
    manifest = json.loads((repository/prefix/'manifest.json').read_bytes())
    for name in ['manifest.json', *[item['path'] for item in manifest['files']]]:
        write(source/prefix/name, (repository/prefix/name).read_bytes())


def write_native_review_bundle(source):
    """Separate required page-context bundle; no npm/runtime substitution."""
    repository = Path(__file__).resolve().parents[1]
    prefix = 'toolchain/opencode-plugins/native-review-context'
    manifest = json.loads((repository/prefix/'manifest.json').read_bytes())
    for name in ['manifest.json', *[item['path'] for item in manifest['files']]]:
        write(source/prefix/name, (repository/prefix/name).read_bytes())


def write_native_message_bundle(source):
    repository = Path(__file__).resolve().parents[1]
    for prefix in ('toolchain/opencode-plugins/dynamic-workflow',
                   'toolchain/opencode-plugins/native-message-context',
                   'toolchain/opencode-plugins/model-authority'):
        manifest = json.loads((repository/prefix/'manifest.json').read_bytes())
        for name in ['manifest.json', *[item['path'] for item in manifest['files']]]:
            write(source/prefix/name, (repository/prefix/name).read_bytes())


@pytest.fixture
def scene(tmp_path):
    source, runtime = tmp_path/'source', tmp_path/'runtime'
    repository = Path(__file__).resolve().parents[1]
    for name in builder.SOURCE_TREES:
        if name == 'toolchain/opencode-plugins/model-authority':
            continue  # Its exact real member set is copied below; no placeholder.
        write(source/name/'original.txt')
    # The four interface files are actual current production bytes, not mock
    # classes or name-only stand-ins. The runtime is explicitly structural.
    for name in {*builder.IDE_COMPANION_FILES, *builder.SOURCE_EXTRAS}:
        write(source/name, (repository/name).read_bytes())
    plugin = source/'toolchain/opencode-plugins/dynamic-workflow'
    raw = (plugin/'original.txt').read_bytes()
    write(plugin/'manifest.json', json.dumps({'id':'eeas.dynamic-workflow', 'files':[
        {'path':'original.txt', 'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest()}]}).encode())
    write_change_review_bundle(source)
    write_native_review_bundle(source)
    write_native_message_bundle(source)
    jobs = {name:source/name for name in builder.IDE_COMPANION_FILES}
    for name in builder.ENTRYPOINTS.values():
        if name not in jobs:
            jobs[name] = write(runtime/name)
    write(runtime/'runtime/site-packages/original.py', b'# preserve this original dependency\n')
    jobs['runtime/site-packages/original.py'] = runtime/'runtime/site-packages/original.py'
    support = runtime/'runtime/opencode-support'
    executable = write(support/'rg.exe')
    archive = write(support/'ripgrep-15.1.0-x86_64-pc-windows-msvc.zip')
    write(support/'SUPPORT_MANIFEST.json', json.dumps({'schema':'eeas.opencode-support.v1',
        'platform':'windows-x64', 'version':'15.1.0',
        'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(), 'files':[
            {'path':'rg.exe', 'bytes':executable.stat().st_size,
             'sha256':hashlib.sha256(executable.read_bytes()).hexdigest()}]}).encode())
    return source, runtime, {'jobs':jobs, 'runtime_audit':{'scope':'STRUCTURAL_FILES_ONLY_NOT_WINDOWS'},
        'source_root':str(source), 'runtime_root':str(runtime)}


def marker(value):
    check(value['ide_companion'] == {'protocol':'eeas.ide-companion.v1', 'background_bootstrap':True,
        'discovery_relative_path':'user-data/ide-companion/discovery.json',
        'transport':'MAIN_OWNED_LOOPBACK_HTTP', 'external_agent_owner':'DESKTOP_EEAS_ONLY'})
    check(value['state'] == 'DEVELOPMENT_PREVIEW')
    check(value['qualification']['model_access'] == 'NOT_CONFIGURED')
    check(not any(key in value['ide_companion'] for key in ('ready','pass','qualified','windows')))


def fingerprint(path):
    stat = path.stat()
    return stat.st_ino, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


def test_actual_fresh_copy_and_archive_bind_all_four_interfaces_without_claiming_runtime_ready(scene, tmp_path):
    source, _runtime, plan = scene
    output = tmp_path/'fresh-preview'
    manifest = builder.copy_plan(plan, output)
    marker(manifest)
    check(verify_package(output) == manifest)
    check(manifest['qualification']['windows_cold_boot'] == 'NOT_RUN')
    inventory = {row['path']:row for row in manifest['files']}
    for name in builder.IDE_COMPANION_FILES:
        raw = (source/name).read_bytes()
        check((output/name).read_bytes() == raw)
        check(inventory[name]['sha256'] == hashlib.sha256(raw).hexdigest())
        check(inventory[name]['bytes'] == len(raw) and inventory[name]['startup_critical'] is True)
    # Load the real copied bridge with its copied protocol dependency. This
    # catches a missing transitive JSON file without starting the structural
    # Windows runtime, a user database, or an actual native application.
    repository = Path(__file__).resolve().parents[1]
    loaded = subprocess.run([
        str(repository/'.node-tools/node_modules/node/bin/node'), '-e',
        "const b=require(process.argv[1]);process.stdout.write(JSON.stringify({protocol:b.PROTOCOL,methods:b.METHODS}));",
        str(output/'apps/desktop-host/ide-companion-bridge.cjs'),
    ], check=True, capture_output=True, text=True, timeout=10)
    protocol = json.loads((output/'plugins/vscode-companion/protocol.json').read_bytes())
    check(json.loads(loaded.stdout) == {'protocol':protocol['protocol'], 'methods':protocol['methods']})
    archive = tmp_path/'actual-structural-package.zip'
    builder.archive_payload(output, archive, manifest)
    with zipfile.ZipFile(archive) as actual:
        marker(json.loads(actual.read('PACKAGE_MANIFEST.json')))
        check(all(actual.read(name) == (source/name).read_bytes() for name in builder.IDE_COMPANION_FILES))


@pytest.mark.parametrize('fault', ['missing_subscription', 'extra_member', 'rebound_same_bytes'])
def test_model_authority_package_pins_refuse_partial_or_rebound_reviewed_members(scene, tmp_path, fault):
    from packages.adapters.opencode_model_authority import PLUGIN_PATH, PLUGIN_FILES
    source, _runtime, _plan = scene
    jobs = {PLUGIN_PATH+'/'+name:source/PLUGIN_PATH/name for name in (*PLUGIN_FILES,'manifest.json')}
    check(set(builder.model_authority_job_pins(jobs)) == set(jobs))
    member = PLUGIN_PATH+'/subscription-http.mjs'
    if fault == 'missing_subscription':
        jobs.pop(member)
    elif fault == 'extra_member':
        jobs[PLUGIN_PATH+'/unreviewed.mjs'] = jobs[member]
    else:
        jobs[member] = write(tmp_path/'rebound.mjs', jobs[member].read_bytes())
    with pytest.raises(PackageError, match='Original model authority selected closure'):
        builder.model_authority_job_pins(jobs)


def test_old_manifest_without_marker_still_reads_and_refresh_adds_real_bridge_without_touching_private_or_runtime(scene, tmp_path):
    source, runtime, plan = scene
    plan = {**plan, 'jobs':{name:path for name,path in plan['jobs'].items()
        if name not in builder.IDE_COMPANION_FILES[:2]}}
    output = tmp_path/'old-preview'
    old = builder.copy_plan(plan, output)
    check('ide_companion' not in old and read_manifest(output) == old and verify_package(output) == old)
    original_manifest = (output/'PACKAGE_MANIFEST.json').read_bytes()
    private = write(output/'user-data/untouched-draft.txt', '保留未发送的真实私稿'.encode())
    original_private = fingerprint(private)
    dependencies = {row['path']:fingerprint(output/row['path']) for row in old['files']
        if row['path'].startswith('runtime/')}
    refresh_plan = builder.make_refresh_plan(source, runtime)
    check(set(builder.IDE_COMPANION_FILES) <= refresh_plan['jobs'].keys())
    refreshed = builder.refresh_source(refresh_plan, output)
    marker(refreshed)
    check(verify_package(output) == refreshed)
    check(refreshed['qualification']['windows_cold_boot'] == 'NOT_RUN_AFTER_SOURCE_REFRESH')
    check(fingerprint(private) == original_private)
    check(all(fingerprint(output/name) == before for name,before in dependencies.items()))
    rows = {row['path']:row for row in refreshed['files']}
    check(all(rows[row['path']] == row for row in old['files'] if row['path'].startswith('runtime/')))
    check(not any(name.startswith('user-data/') for name in rows))
    history = output/refreshed['source_refresh']['history_relative_path']
    check((history/'PACKAGE_MANIFEST.before.json').read_bytes() == original_manifest)
    check(all((output/name).read_bytes() == (source/name).read_bytes() for name in builder.IDE_COMPANION_FILES))


def test_partial_new_interface_is_not_advertised_or_given_a_success_manifest(scene, tmp_path):
    _source, _runtime, plan = scene
    jobs = dict(plan['jobs']);jobs.pop('apps/desktop-host/ide-api-adapter.cjs')
    output = tmp_path/'incomplete-preview'
    with pytest.raises(PackageError, match='IDE companion interface payload is incomplete'):
        builder.copy_plan({**plan,'jobs':jobs}, output)
    check(not (output/'PACKAGE_MANIFEST.json').exists())
    check((output/'PACKAGE_FAILED.json').is_file())


def test_incomplete_refresh_refuses_before_mutation_or_deleting_existing_user_state(scene, tmp_path):
    source, runtime, plan = scene
    output = tmp_path/'preserve-preview'
    old = builder.copy_plan(plan, output)
    private = write(output/'user-data/current.txt', b'keep this draft')
    before = {row['path']:fingerprint(output/row['path']) for row in old['files']}
    manifest_before = (output/'PACKAGE_MANIFEST.json').read_bytes()
    refresh_plan = builder.make_refresh_plan(source,runtime)
    refresh_plan['jobs'].pop('apps/desktop-host/main.cjs')
    with pytest.raises(PackageError, match='IDE companion interface payload is incomplete'):
        builder.refresh_source(refresh_plan,output)
    check((output/'PACKAGE_MANIFEST.json').read_bytes() == manifest_before)
    check(not (output/'package-history').exists())
    check(private.read_bytes() == b'keep this draft')
    check(all(fingerprint(output/name) == expected for name,expected in before.items()))
