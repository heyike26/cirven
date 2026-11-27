"""Small original file fixtures exercise copying/relocation, never Windows/GUI."""
import base64
import asyncio
import copy
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from scripts.native_package_bootstrap import (PackageError, activate_python, private_paths,
    _relative_path, diagnostic_exception, diagnose_mcp, python_module_command, python_script_command, runtime_environment, verify_package)
from scripts.package_native_companion import (ENTRYPOINTS, INSTRUCTION_FILES, archive_payload,
    check_record, copy_plan, disk_target, files_below, refresh_source)


@pytest.fixture
def bundle(tmp_path):
    source = tmp_path / 'tiny-original-structural-files'; source.mkdir()
    names = list(ENTRYPOINTS.values()) + ['scripts/native_package_bootstrap.py', 'packages/runner/supervisor.py']
    names += ['runtime/site-packages/fixture.pth', 'runtime/site-packages/original_subpath/value.py']
    jobs = {}
    for name in names:
        path = source / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('original file fixture, not a Windows executable\n' if not name.endswith('.pth') else 'original_subpath\n')
        jobs[name] = path
    output = tmp_path / 'portable-A'
    manifest = copy_plan({'jobs':jobs, 'runtime_audit':{'qualification':'TEST_STRUCTURE_ONLY_NOT_RUNTIME'},
        'source_root':str(source), 'runtime_root':str(source)}, output)
    return output, manifest, source


def test_actual_files_copy_hashes_relative_python_paths_and_relocation_without_original_location(bundle, tmp_path):
    root, manifest, source = bundle
    assert verify_package(root)['state'] == 'DEVELOPMENT_PREVIEW'
    pth = (root / 'runtime/python/python310._pth').read_text()
    assert '../site-packages' in pth and '../..' in pth and 'import site' not in pth
    shutil.rmtree(source)
    moved = tmp_path / '移动目录🙂' / 'portable-B'; moved.parent.mkdir()
    root.rename(moved)
    actual = verify_package(moved)
    assert actual == manifest
    env = runtime_environment(moved, actual, inherited={'OPENAI_API_KEY':'ambient', 'HOME':'/unrelated/home',
        'EEAS_OPENCODE_SUPPORT_ROOT':'/old/development/runtime'})
    assert 'OPENAI_API_KEY' not in env and 'EEAS_OPENCODE_SUPPORT_ROOT' not in env and env['HOME'].startswith(str(moved))
    descriptor = json.loads(Path(env['EEAS_COLLABORATION_RUNTIME_DESCRIPTOR']).read_bytes())
    assert descriptor['runtime_root'] == str(moved) and str(root) not in json.dumps(descriptor)
    assert all(Path(descriptor[key]['path']).is_relative_to(moved) for key in ('component_manifest_ref','readonly_capability_manifest_ref'))
    assert hashlib.sha256(Path(env['EEAS_COLLABORATION_RUNTIME_DESCRIPTOR']).read_bytes()).hexdigest() == env['EEAS_COLLABORATION_RUNTIME_DESCRIPTOR_SHA256']
    assert env['PIP_NO_INDEX'] == '1' and actual['qualification']['model_access'] == 'NOT_CONFIGURED'


def test_private_descriptor_is_exact_and_never_reuses_changed_bytes(bundle):
    root, manifest, _source = bundle
    env = runtime_environment(root, manifest, inherited={})
    path = Path(env['EEAS_COLLABORATION_RUNTIME_DESCRIPTOR'])
    path.write_text('original mutation')
    with pytest.raises(PackageError):
        runtime_environment(root, manifest, inherited={})


def test_all_child_entry_commands_apply_the_same_bootstrap_without_development_interpreter(bundle):
    root, manifest, _source = bundle
    for module in ('apps.api','packages.desktop.mcp_server','packages.planning.mcp_server','packages.planning.discovery_mcp_server'):
        command = python_module_command(root,module,['--original','value'])
        assert command[:3] == [str(root / 'runtime/python/python.exe'),'-B',str(root / 'scripts/native_package_bootstrap.py')]
        assert command[3:] == ['--module',module,'--','--original','value']
    script = python_script_command(root,'packages/runner/supervisor.py',['job.json'])
    assert script[:3] == [str(root / 'runtime/python/python.exe'),'-B',str(root / 'scripts/native_package_bootstrap.py')]
    assert script[3:] == ['--script','packages/runner/supervisor.py','--','job.json']
    with pytest.raises(PackageError):
        python_module_command(root,'unknown.ambient.module')
    with pytest.raises(PackageError):
        python_script_command(root,'../outside.py')


def test_generated_start_keeps_B_before_bootstrap_and_real_startup_ignores_environment(bundle, tmp_path):
    from scripts.package_native_companion import package_launchers
    root, _manifest, _source = bundle
    launch = package_launchers()['START_EEAS.cmd'].decode('ascii').splitlines()
    assert launch[3] == 'start "" "%~dp0runtime\\python\\pythonw.exe" -B "%~dp0scripts\\native_package_bootstrap.py" --electron'
    flags = python_module_command(root, 'apps.api')[1:2]
    assert flags == ['-B']
    module_dir = tmp_path/'actual-child-module'; module_dir.mkdir()
    (module_dir/'original_cache_probe.py').write_text('answer = 42\n')
    script = ('import sys,json;sys.path.insert(0,sys.argv[1]);import original_cache_probe;'
              'print(json.dumps({"no_bytecode":sys.dont_write_bytecode,"ignore_environment":sys.flags.ignore_environment,'
              '"answer":original_cache_probe.answer}))')
    result = subprocess.run([sys.executable, *flags, '-E', '-c', script, str(module_dir)],
                            capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(result.stdout) == {'no_bytecode':True, 'ignore_environment':1, 'answer':42}
    assert not (module_dir/'__pycache__').exists()


def test_payload_tamper_absence_and_symlink_are_rejected_before_boot(bundle, tmp_path):
    root, manifest, _source = bundle
    path = root / ENTRYPOINTS['component_manifest']
    original = path.read_bytes(); path.write_bytes(b'X' * len(original))
    with pytest.raises(PackageError):
        verify_package(root,full=False)
    path.write_bytes(original); path.unlink()
    with pytest.raises(PackageError):
        verify_package(root)
    other = tmp_path / 'external'; other.write_bytes(original); path.symlink_to(other)
    with pytest.raises(PackageError):
        verify_package(root)


def test_canonical_boundary_optimization_preserves_all_hashes_and_fresh_ancestor_checks(bundle, monkeypatch):
    import scripts.native_package_bootstrap as bootstrap
    root,manifest,_source=bundle
    original_sha=bootstrap.sha;original_link=Path.is_symlink;hashes=[];links=[]
    def counted_hash(path, **kwargs):
        hashes.append(Path(path).relative_to(root).as_posix());return original_sha(path, **kwargs)
    def counted_link(path):
        links.append(path);return original_link(path)
    monkeypatch.setattr(bootstrap,'sha',counted_hash);monkeypatch.setattr(Path,'is_symlink',counted_link)
    assert verify_package(root)==manifest
    assert sorted(hashes)==sorted(item['path'] for item in manifest['files'])
    assert links.count(root)>=len(manifest['files'])
    assert links.count(root.parent)>=len(manifest['files'])
    assert all(root/item['path'] in links for item in manifest['files'])


def test_fresh_ancestor_change_is_rejected_during_verification(bundle, tmp_path, monkeypatch):
    import scripts.native_package_bootstrap as bootstrap
    root,_manifest,_source=bundle
    original=bootstrap.sha;triggered=False
    # Install a real ancestor link between consecutive member reads. It points
    # inside the original installation, so containment alone cannot catch it.
    def replace_next_ancestor(path, **kwargs):
        nonlocal triggered
        value=original(path, **kwargs)
        if not triggered:
            triggered=True
            moved=root/'runtime/site-packages.original';(root/'runtime/site-packages').rename(moved)
            (root/'runtime/site-packages').symlink_to(moved,target_is_directory=True)
        return value
    monkeypatch.setattr(bootstrap,'sha',replace_next_ancestor)
    with pytest.raises(PackageError):
        verify_package(root,workers=1)  # This control mutates between consecutive reads.


def test_fixed_canonical_root_rejects_root_redirect_even_when_target_resolves_with_it(bundle, tmp_path):
    root,_manifest,_source=bundle
    original=root
    moved=tmp_path/'original-package-redirected';root.rename(moved)
    root.symlink_to(moved,target_is_directory=True)
    with pytest.raises(PackageError):
        _relative_path(original,ENTRYPOINTS['python'],canonical_root=original)


def test_no_unregistered_or_absolute_pth_code_runs(bundle, monkeypatch):
    root, manifest, _source = bundle
    called = []
    monkeypatch.setattr('site.addsitedir', lambda path: called.append(path))
    activate_python(root,manifest)
    assert called == [str(root / 'runtime/site-packages')]
    added = root / 'runtime/site-packages/unregistered.pth'; added.write_text('import unwanted\n')
    with pytest.raises(PackageError):
        activate_python(root,manifest)
    added.unlink()
    path = root / 'runtime/site-packages/fixture.pth'; path.write_text('/external/developer/path\n')
    altered = copy.deepcopy(manifest)
    for item in altered['files']:
        if item['path'].endswith('fixture.pth'):
            item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(PackageError):
        activate_python(root,altered)
    assert len(called) == 1


def test_data_paths_cannot_escape_installation_or_use_ambient_profile(bundle, tmp_path):
    root, manifest, _source = bundle
    with pytest.raises(PackageError):
        private_paths(root,data_dir=tmp_path / 'outside-data')
    outside = tmp_path / 'outside'; outside.mkdir()
    link = root / 'escaped-data'; link.symlink_to(outside,target_is_directory=True)
    with pytest.raises(PackageError):
        private_paths(root,data_dir=link)
    env = runtime_environment(root,manifest,inherited={'EEAS_DESKTOP_HOST_TOKEN':'trusted-main', 'EEAS_DESKTOP_HOST_PID':'123'})
    assert env['EEAS_DESKTOP_HOST_TOKEN'] == 'trusted-main' and env['EEAS_DESKTOP_HOST_PID'] == '123'


def test_record_target_script_redirect_still_requires_exact_real_file_hash(tmp_path):
    site = tmp_path / 'actual-target'; distribution = site / 'original.dist-info'; distribution.mkdir(parents=True)
    script = site / 'bin/original'; script.parent.mkdir(); script.write_bytes(b'original copied script')
    expected = base64.urlsafe_b64encode(hashlib.sha256(script.read_bytes()).digest()).decode().rstrip('=')
    (distribution / 'RECORD').write_text('../../bin/original,sha256='+expected+','+str(script.stat().st_size)+'\n')
    assert check_record(site,distribution) == 1
    script.write_bytes(b'changed copied script')
    with pytest.raises(PackageError):
        check_record(site,distribution)


def test_interrupted_copy_is_preserved_and_not_overwritten(bundle):
    root, manifest, source = bundle
    with pytest.raises(PackageError):
        copy_plan({'jobs':{},'runtime_audit':{}},root)
    failed = root.parent / 'failed-package'
    with pytest.raises(FileNotFoundError):
        copy_plan({'jobs':{'missing.py':source / 'missing.py'},'runtime_audit':{}},failed)
    assert (failed / 'PACKAGE_FAILED.json').is_file() and not (failed / 'PACKAGE_MANIFEST.json').exists()


def test_development_caches_and_legacy_absolute_sitecustomize_are_excluded(tmp_path):
    for name in ('normal.py','node_modules/dev.js','.venv/tool.py','__pycache__/temp.pyc','sitecustomize.py'):
        path = tmp_path / name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text('original fixture')
    assert [p.relative_to(tmp_path).as_posix() for p in files_below(tmp_path)] == ['normal.py']


def test_large_output_location_cannot_escape_drive_through_parent_segments_or_links(tmp_path):
    with pytest.raises(PackageError):
        disk_target('/mnt/d/../../tmp/original-large-output-not-created')
    outside=tmp_path/'original-outside';outside.mkdir()
    link=tmp_path/'original-output-link';link.symlink_to(outside,target_is_directory=True)
    with pytest.raises(PackageError):
        disk_target(link/'never-created')


def test_archive_uses_hashed_members_and_excludes_local_data_and_history(bundle, tmp_path):
    root,manifest,_source=bundle
    private=root/'user-data/original-private-fixture.txt';private.parent.mkdir();private.write_text('never distributed')
    history=root/'package-history/original-history.txt';history.parent.mkdir();history.write_text('preserved outside deliverable')
    archive=tmp_path/'original-small-structural-archive.zip'
    archive_payload(root,archive,manifest)
    with zipfile.ZipFile(archive) as package:
        assert set(package.namelist()) == {'PACKAGE_MANIFEST.json',*(item['path'] for item in manifest['files'])}
        assert package.testzip() is None


def test_portable_instructions_are_actual_hashed_distributable_files_with_current_boundaries(bundle, tmp_path):
    root, manifest, _source = bundle
    assert manifest['instructions']['files'] == list(INSTRUCTION_FILES)
    files = {item['path']: item for item in manifest['files']}
    for name in INSTRUCTION_FILES:
        raw = (root / name).read_bytes()
        assert len(raw) == files[name]['bytes'] and hashlib.sha256(raw).hexdigest() == files[name]['sha256']
        assert files[name]['startup_critical'] is False  # inert documents; launcher still verifies every byte
    text = (root / 'README_FIRST.md').read_text('utf-8')
    assert 'START_EEAS.cmd' in text and 'D:\\EEAS\\EEAS 内网初版 20261015' in text
    assert 'NOT_VALIDATED' in text and '不是发布者签名' in text and '普适原生输入' in text
    # The listed bundled IDs correspond to the reviewed original manifest,
    # rather than the static planning-tool directory or invented skills.
    from packages.domain.bundled_capabilities import BUNDLED_SKILLS
    import re
    listed = re.findall(r'^- ([a-z0-9-]+)：', text, re.M)
    assert sorted(listed) == sorted(item[0] for item in BUNDLED_SKILLS)
    setup = (root / 'INTRANET_SETUP.md').read_text('utf-8')
    renderer_contract = (Path(__file__).resolve().parents[1] / 'apps/agent-shell/src/native-capabilities.ts').read_text('utf-8')
    timeouts = {name: int(value) for name, value in re.findall(
        r'export const MCP_TIMEOUT_(MIN|MAX|DEFAULT) = (\d+);', renderer_contract)}
    assert set(timeouts) == {'MIN', 'MAX', 'DEFAULT'}
    assert f"{timeouts['MIN']}–{timeouts['MAX']} 毫秒" in setup
    assert f"首次连接建议 {timeouts['DEFAULT']} 毫秒" in setup
    assert '冷启动握手曾超过 10 秒' in setup
    assert 'model ID' in setup and '/v1' in setup and '公司模型' in setup
    assert '不导入环境变量或凭证' in setup and '不调用任何工具' in setup
    assert 'OS 级网络沙箱' in setup and '不会自动替换模型或计费方式' in setup
    archive = tmp_path / 'documented-portable.zip'
    archive_payload(root, archive, manifest)
    with zipfile.ZipFile(archive) as package:
        assert all(package.read(name) == (root / name).read_bytes() for name in INSTRUCTION_FILES)
    path = root / INSTRUCTION_FILES[0]
    original = path.read_bytes()
    path.write_bytes(b'X' * len(original))
    with pytest.raises(PackageError):
        verify_package(root)


def test_instruction_refresh_preserves_historical_bytes_and_rejects_unregistered_collision(bundle):
    import scripts.package_native_companion as builder
    root, manifest, source = bundle
    # Simulate a previous valid preview whose generated instructions predate this version.
    doc = root / INSTRUCTION_FILES[0]
    previous = b'original historical portable instruction bytes\n'
    doc.write_bytes(previous)
    old = copy.deepcopy(manifest)
    item = next(value for value in old['files'] if value['path'] == doc.name)
    item.update(bytes=len(previous), sha256=hashlib.sha256(previous).hexdigest())
    (root / 'PACKAGE_MANIFEST.json').write_text(json.dumps(old))
    plan = {'jobs': {name: source / name for name in
        ('scripts/native_package_bootstrap.py', 'apps/desktop-host/main.cjs')}}
    refreshed = refresh_source(plan, root)
    history = root / refreshed['source_refresh']['history_relative_path']
    assert (history / 'payload.before' / doc.name).read_bytes() == previous
    assert (root / doc.name).read_bytes() == builder.package_instructions()[doc.name]
    assert verify_package(root) == refreshed
    # A user's unregistered same-name file is not silently replaced on upgrade.
    del old['instructions']
    old['files'] = [value for value in refreshed['files'] if value['path'] not in INSTRUCTION_FILES]
    (root / 'PACKAGE_MANIFEST.json').write_text(json.dumps(old))
    before_manifest = (root / 'PACKAGE_MANIFEST.json').read_bytes()
    with pytest.raises(PackageError):
        refresh_source(plan, root)
    assert (root / 'PACKAGE_MANIFEST.json').read_bytes() == before_manifest
    assert (root / doc.name).read_bytes() == builder.package_instructions()[doc.name]


def test_final_refresh_preserves_prior_sources_manifests_and_removes_stale_code(bundle):
    root, manifest, source = bundle
    stale = root / 'packages/runner/supervisor.py'
    original = stale.read_bytes()
    api = source / 'apps/api/original_new.py'; api.parent.mkdir(parents=True,exist_ok=True)
    api.write_text('actual new original source fixture\n')
    jobs = {name:source / name for name in ['scripts/native_package_bootstrap.py','apps/desktop-host/main.cjs']}
    jobs['apps/api/original_new.py'] = api
    refreshed = refresh_source({'jobs':jobs,'source_root':str(source)},root)
    assert not stale.exists() and (root / 'apps/api/original_new.py').read_bytes() == api.read_bytes()
    history = root / refreshed['source_refresh']['history_relative_path']
    assert json.loads((history / 'PACKAGE_MANIFEST.before.json').read_bytes()) == manifest
    assert (history / 'payload.before/packages/runner/supervisor.py').read_bytes() == original
    assert verify_package(root) == refreshed
    assert refreshed['qualification']['windows_cold_boot'] == 'NOT_RUN_AFTER_SOURCE_REFRESH'


def test_refresh_rejects_changed_prior_source_before_any_mutation(bundle):
    root, manifest, source = bundle
    (root / 'packages/runner/supervisor.py').write_text('external changed source')
    old_manifest = (root / 'PACKAGE_MANIFEST.json').read_bytes()
    with pytest.raises(PackageError):
        refresh_source({'jobs':{'scripts/native_package_bootstrap.py':source / 'scripts/native_package_bootstrap.py'}},root)
    assert (root / 'PACKAGE_MANIFEST.json').read_bytes() == old_manifest
    assert not (root / 'package-history').exists()


def test_fresh_real_child_processes_each_apply_reviewed_pth_after_directory_move(bundle, tmp_path):
    root, _manifest, source = bundle
    # Original tiny fixture code is executed by separate Linux interpreters.
    # This verifies per-process initialization, not Windows binary qualification.
    module = source / 'runtime/site-packages/original_child_initialization.py'
    module.write_text('import os\nINITIALIZED_PID=os.getpid()\n')
    (source / 'runtime/site-packages/fixture.pth').write_text('import original_child_initialization\noriginal_subpath\n')
    supervisor = source / 'packages/runner/supervisor.py'
    supervisor.write_text('import json,os,original_child_initialization\nprint(json.dumps({"pid":os.getpid(),"initialized_pid":original_child_initialization.INITIALIZED_PID}))\n')
    plan = {'jobs':{p.relative_to(source).as_posix():p for p in files_below(source)},'runtime_audit':{}}
    new = tmp_path / 'native-child-original'; copy_plan(plan,new)
    moved = tmp_path / '移动后的原始夹具';new.rename(moved);shutil.rmtree(source)
    script = Path(__file__).resolve().parents[1] / 'scripts/native_package_bootstrap.py'
    code = ('import importlib.util,sys,pathlib;spec=importlib.util.spec_from_file_location("original_bootstrap",sys.argv[1]);'
        'm=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);r=pathlib.Path(sys.argv[2]);'
        'v=m.verify_package(r);m.execute_python(r,v,script="packages/runner/supervisor.py")')
    results=[]
    for _ in range(2):
        child=subprocess.run([sys.executable,'-I','-B','-c',code,str(script),str(moved)],capture_output=True,text=True,check=True)
        value=json.loads(child.stdout);assert value['pid'] == value['initialized_pid'];results.append(value)
    assert results[0]['pid'] != results[1]['pid'] and all(value['pid'] != __import__('os').getpid() for value in results)


def test_diagnostic_preserves_complete_nested_exception_groups():
    from exceptiongroup import ExceptionGroup
    try:
        try:
            raise ValueError('original nested diagnostic failure')
        except ValueError as original:
            raise ExceptionGroup('original parent',[original,ExceptionGroup('original nested',[RuntimeError('second child')])])
    except ExceptionGroup as error:
        value=diagnostic_exception(error)
    assert value['exceptions'][0]['message']=='original nested diagnostic failure'
    assert 'raise ValueError' in value['exceptions'][0]['traceback']
    assert value['exceptions'][1]['exceptions'][0]['message']=='second child'


@pytest.fixture
def original_mcp_diagnostic(bundle, tmp_path, monkeypatch):
    root,manifest,_source=bundle
    repository=Path(__file__).resolve().parents[1]
    plugin=Path('toolchain/opencode-plugins/native-review-context')
    for name in ('index.mjs','package.json','README.md','manifest.json'):
        destination=root/plugin/name;destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes((repository/plugin/name).read_bytes())
    profile=root/'profiles/stack.lock.json';profile.parent.mkdir();profile.write_text('{}')
    script=tmp_path/'original_stdio_fixture.py'
    script.write_text('from mcp.server import MCPServer\nserver=MCPServer("original-package-diagnostic-fixture",version="1.0.0")\n'
        '@server.tool()\ndef original_readonly_fixture()->dict:\n    return {"original_fixture":True}\nserver.run(transport="stdio")\n')
    monkeypatch.setattr('scripts.native_package_bootstrap.python_module_command',lambda *_args:[sys.executable,'-B',str(script)])
    monkeypatch.setattr('scripts.native_package_bootstrap.runtime_environment',lambda *_args,**_kwargs:{})
    directory=tmp_path/'original-diagnostic-receipts';directory.mkdir()
    return root,manifest,directory,script


def test_real_official_mcp_initialize_fields_and_each_child_stderr_stage_persist(original_mcp_diagnostic):
    root,manifest,directory,_script=original_mcp_diagnostic
    results=asyncio.run(diagnose_mcp(root,manifest,directory,timeout_seconds=15))
    assert len(results)==4 and all(value['server_name']=='original-package-diagnostic-fixture' for value in results)
    assert all(value['tools']==['original_readonly_fixture'] and value['tool_calls']==0 for value in results)
    state=json.loads((directory/'phase.json').read_bytes())
    assert state['stage']=='ALL_MCP_CHILDREN_CLOSED' and state['status']=='PASSED'
    events=[json.loads(line) for line in (directory/'phases.jsonl').read_text().splitlines()]
    assert sum(value['stage']=='INITIALIZE' for value in events)==4
    assert len(list(directory.glob('*.initialize.json')))==4 and len(list(directory.glob('*.stderr.txt')))==4


def test_actual_child_process_failure_records_stderr_and_nested_phase(original_mcp_diagnostic):
    root,manifest,directory,script=original_mcp_diagnostic
    script.write_text('import sys\nsys.stderr.write("Original child stderr failure\\n")\nraise RuntimeError("original child process failed")\n')
    with pytest.raises(BaseException):
        asyncio.run(diagnose_mcp(root,manifest,directory,timeout_seconds=10))
    state=json.loads((directory/'phase.json').read_bytes())
    assert state['status']=='FAILED' and state['stage']=='INITIALIZE'
    assert state['error']['traceback'] and (directory/state['stderr']).is_file()
    assert 'Original child stderr failure' in (directory/state['stderr']).read_text()
    assert len(list(directory.glob('*.exception.json')))==1
