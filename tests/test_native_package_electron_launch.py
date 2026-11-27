"""Real Linux child outcomes exercise launcher policy; no Windows GUI claim."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sys
import types

import pytest

from scripts import native_package_bootstrap as bootstrap


@pytest.fixture
def launch_bundle(tmp_path):
    root = tmp_path/'EEAS startup original child'
    executable = root/'runtime/electron/electron.exe'
    application = root/'apps/desktop-host'
    executable.parent.mkdir(parents=True)
    application.mkdir(parents=True)
    # An original POSIX exec wrapper runs a real separate Python process. This
    # deliberately does not emulate or qualify Electron/Win32 window behavior.
    executable.write_text('#!/bin/sh\nfixture_script="$1/fixture_main.py"\nshift\nexec '+shlex.quote(sys.executable)+' -I -B "$fixture_script" "$@"\n')
    executable.chmod(0o700)
    (application/'fixture_main.py').write_text(
        'import json,os,pathlib,sys\n'
        'p=pathlib.Path(__file__).with_name("fixture-config.json")\n'
        'v=json.loads(p.read_text())\n'
        'print(json.dumps({"pid":os.getpid(),"node_flag_present":any(k.upper()=="ELECTRON_RUN_AS_NODE" for k in os.environ),"fixture_marker":os.environ.get("EEAS_ORIGINAL_FIXTURE_MARKER"),"arguments":sys.argv[1:],"profile":{k:os.environ[k] for k in ("HOME","USERPROFILE","APPDATA","LOCALAPPDATA","HOMEDRIVE","HOMEPATH") if k in os.environ},"private":{k:os.environ.get(k) for k in ("EEAS_DATA_DIR","TEMP","XDG_CONFIG_HOME","XDG_DATA_HOME","PATHEXT")},"secret_present":"OPENAI_API_KEY" in os.environ}))\n'
        'sys.stderr.write("Original Main child stderr fixture\\n")\n'
        'raise SystemExit(v["exit_code"])\n')
    config = application/'fixture-config.json'
    config.write_text(json.dumps({'exit_code':0}))
    manifest = {'entrypoints':{'electron':'runtime/electron/electron.exe','main':'apps/desktop-host/main.cjs'}}
    return root,manifest,config


def test_real_child_receives_gui_environment_and_log_preserves_original_streams(launch_bundle, capsys):
    root,manifest,_config = launch_bundle
    original = {'ELECTRON_RUN_AS_NODE':'1','electron_run_as_node':'1','EEAS_ORIGINAL_FIXTURE_MARKER':'unchanged'}
    assert bootstrap.launch_electron(root,manifest,original,bootstrap.StartupProgress(enabled=False)) == 0
    assert original['ELECTRON_RUN_AS_NODE'] == '1'  # Caller environment is not mutated.
    logs = list((root/'user-data/logs').glob('main-process-*.log'))
    assert len(logs) == 1
    lines = logs[0].read_text().splitlines()
    metadata = next(json.loads(line) for line in lines if line.startswith('{'))
    assert metadata['pid'] != os.getpid() and metadata['node_flag_present'] is False
    assert metadata['fixture_marker'] == 'unchanged'
    assert 'Original Main child stderr fixture' in lines
    captured = capsys.readouterr()
    assert captured.out == '' and captured.err == ''


def test_background_launch_passes_only_explicit_main_flag_to_a_real_child(launch_bundle):
    root, manifest, _config = launch_bundle
    if bootstrap.launch_electron(root, manifest, {'ELECTRON_RUN_AS_NODE': '1'},
                                 bootstrap.StartupProgress(enabled=False), background=True) != 0:
        raise AssertionError('Background child did not exit zero')
    log = next((root / 'user-data/logs').glob('main-process-*.log'))
    metadata = next(json.loads(line) for line in log.read_text().splitlines() if line.startswith('{'))
    if metadata['arguments'] != ['--eeas-background'] or metadata['node_flag_present']:
        raise AssertionError('Background intent did not reach the GUI child exactly')


@pytest.mark.parametrize('mode', ['--api', '--verify', '--diagnose', '--diagnose-child'])
def test_background_flag_cannot_silence_an_unrelated_child_or_verifier(mode):
    with pytest.raises(SystemExit) as caught:
        bootstrap.main([mode, '--background'])
    if caught.value.code != 2:
        raise AssertionError('Invalid background mode must fail before a launch')


@pytest.mark.parametrize('code',[1,17,130])
def test_real_nonzero_main_is_not_silent_or_mislabelled_as_user_cancel(launch_bundle, code):
    root,manifest,config = launch_bundle
    config.write_text(json.dumps({'exit_code':code}))
    with pytest.raises(bootstrap.PackageError, match='(?:退出码|exit code) '+str(code)) as caught:
        bootstrap.launch_electron(root,manifest,{},bootstrap.StartupProgress(enabled=False))
    # An arbitrary child's130 is a failure, not evidence of our own cancellation.
    assert not isinstance(caught.value,bootstrap.StartupCancelled)
    log = next((root/'user-data/logs').glob('main-process-*.log'))
    assert str(log) in str(caught.value)
    assert 'Original Main child stderr fixture' in log.read_text()


def test_our_user_cancellation_stops_before_real_child_spawn_or_failure_log(launch_bundle):
    root,manifest,_config = launch_bundle
    progress = bootstrap.StartupProgress(enabled=True)
    progress.cancelled = True
    try:
        with pytest.raises(bootstrap.StartupCancelled):
            bootstrap.launch_electron(root,manifest,{},progress)
    finally:
        progress.close()
    assert not (root/'user-data').exists()


def test_normal_controlled_main_exit_does_not_write_startup_failure(launch_bundle):
    root,manifest,_config = launch_bundle
    assert bootstrap.launch_electron(root,manifest,{},bootstrap.StartupProgress(enabled=False)) == 0
    assert not list((root/'user-data/logs').glob('startup-error-*.json'))


def test_actual_main_wrapper_reports_nonzero_exception_via_existing_visible_failure_route(launch_bundle, monkeypatch):
    root,manifest,config = launch_bundle
    config.write_text(json.dumps({'exit_code':23}))
    monkeypatch.setattr(bootstrap,'__file__',str(root/'scripts/native_package_bootstrap.py'))
    # The actual real child above produces the failure; only dispatch around the
    # Windows-only target guard is adapted so main's failure-report path runs.
    def checked(_root,options,child_mode,implicit_mcp,progress):
        assert options.electron and not child_mode
        return bootstrap.launch_electron(root,manifest,{'ELECTRON_RUN_AS_NODE':'1'},progress)
    monkeypatch.setattr(bootstrap,'_main_checked',checked)
    with pytest.raises(bootstrap.PackageError,match='(?:退出码|exit code) 23'):
        bootstrap.main(['--electron'])
    receipt = next((root/'user-data/logs').glob('startup-error-*.json'))
    value = json.loads(receipt.read_text())
    assert value['status'] == 'STARTUP_FAILED'
    assert value['error']['type'] == 'PackageError'
    assert 'exit code 23' in value['error']['message'] or '退出码 23' in value['error']['message']
    assert 'launch_electron' in value['error']['traceback']


def profile_bundle(launch_bundle):
    root, manifest, _config = launch_bundle
    for key, name in [('python', 'runtime/python/python.exe'),
                      ('component_manifest', 'component.json'), ('readonly_manifest', 'readonly.json')]:
        manifest['entrypoints'][key] = name
        item = root / name; item.parent.mkdir(parents=True, exist_ok=True); item.write_text('{}')
    (root / bootstrap.MANIFEST).write_text('{}')
    return root, manifest


def host_environment():
    return {'USERPROFILE': r'C:\Users\Original', 'APPDATA': r'C:\Users\Original\AppData\Roaming',
            'LOCALAPPDATA': r'C:\Users\Original\AppData\Local', 'HOME': r'C:\Users\Original',
            'HOMEDRIVE': 'C:', 'HOMEPATH': r'\Users\Original', 'PATHEXT': '.COM;.EXE;.BAT;.CMD',
            'OPENAI_API_KEY': 'DUMMY_NOT_A_CREDENTIAL', 'ELECTRON_RUN_AS_NODE': '1'}


def test_actual_gui_dispatch_keeps_early_host_profile_while_real_child_has_private_app_data(launch_bundle):
    root, manifest = profile_bundle(launch_bundle)
    original = host_environment(); inherited = dict(original)
    # Adapt only the target-platform gate and manifest verification. The real
    # dispatcher, environment helpers, log writer and separate child all run.
    class TargetPath(type(root)):
        @property
        def drive(self): return 'D:'
    namespace = dict(bootstrap._main_checked.__globals__)
    namespace.update(os=types.SimpleNamespace(name='nt', environ=inherited),
                     sys=types.SimpleNamespace(version_info=(3, 10)), verify_package=lambda *a, **k: manifest)
    checked = types.FunctionType(bootstrap._main_checked.__code__, namespace)
    options = types.SimpleNamespace(verify=False, diagnose=False, diagnose_child=False,
                                   args=[], api=False, mcp=None, module=None, script=None, background=False)
    assert checked(TargetPath(root), options, False, False, bootstrap.StartupProgress(enabled=False)) == 0
    log = next((root/'user-data/logs').glob('main-process-*.log'))
    value = next(json.loads(line) for line in log.read_text().splitlines() if line.startswith('{'))
    assert value['profile'] == {key: original[key] for key in bootstrap.GUI_PROFILE_KEYS}
    assert value['private']['EEAS_DATA_DIR'] == str(root/'user-data')
    assert value['private']['TEMP'] == str(root/'user-data/tmp')
    assert value['private']['XDG_CONFIG_HOME'] == str(root/'user-data/config')
    assert value['private']['PATHEXT'] == original['PATHEXT']
    assert not value['secret_present'] and not value['node_flag_present']
    assert original == host_environment()


def test_wsl_optional_home_is_dropped_without_replacing_the_native_required_profile(launch_bundle):
    root, manifest = profile_bundle(launch_bundle)
    inherited = host_environment(); inherited['HOME'] = '/home/wsl-user'
    captured = bootstrap.capture_gui_profile(root, inherited)
    assert captured['filtered_optional_keys'] == ['HOME']
    env = bootstrap.gui_runtime_environment(root, manifest, inherited=inherited)
    assert 'HOME' not in env and inherited['HOME'] == '/home/wsl-user'
    assert not any(key.upper() == 'ELECTRON_RUN_AS_NODE' for key in env)
    assert all(env[key] == inherited[key] for key in ('USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'HOMEDRIVE', 'HOMEPATH'))


def test_absent_optional_profile_values_stay_absent_and_private_api_does_not_inherit_gui_drive(launch_bundle):
    root, manifest = profile_bundle(launch_bundle)
    inherited = {key: value for key, value in host_environment().items()
                 if key not in ('HOME', 'HOMEDRIVE', 'HOMEPATH')}
    gui = bootstrap.gui_runtime_environment(root, manifest, inherited=inherited)
    assert all(key not in gui for key in ('HOME', 'HOMEDRIVE', 'HOMEPATH'))
    api = bootstrap.runtime_environment(root, manifest, inherited=host_environment())
    assert api['HOME'] == api['USERPROFILE'] == str(root/'user-data/home')
    assert api['APPDATA'] == str(root/'user-data/appdata')
    assert api['LOCALAPPDATA'] == str(root/'user-data/local-appdata')
    assert 'HOMEDRIVE' not in api and 'HOMEPATH' not in api  # This fixture is POSIX.


@pytest.mark.parametrize('key', ['USERPROFILE', 'APPDATA', 'LOCALAPPDATA'])
def test_missing_or_non_native_required_profile_is_refused_before_private_directories(launch_bundle, key):
    root, manifest = profile_bundle(launch_bundle)
    for value in (None, '', '/home/not-a-windows-profile', r'C:relative'):
        inherited = host_environment()
        if value is None: inherited.pop(key)
        else: inherited[key] = value
        with pytest.raises(bootstrap.PackageError, match='GUI host profile'):
            bootstrap.gui_runtime_environment(root, manifest, inherited=inherited)
        assert not (root/'user-data').exists()


def test_already_privatized_entry_is_not_misrepresented_as_host_profile():
    root = r'D:\Cirven\Install'
    for private in (root+r'\user-data', r'D:\Independent Private Data'):
        inherited = host_environment(); inherited['EEAS_DATA_DIR'] = private
        inherited['APPDATA'] = private+r'\appdata'
        with pytest.raises(bootstrap.PackageError, match='GUI host profile'):
            bootstrap.capture_gui_profile(root, inherited)
    assert bootstrap.capture_gui_profile(root, {**host_environment(), 'USERPROFILE': r'D:\Real OS Profile'})['profile']['USERPROFILE'] == r'D:\Real OS Profile'


def test_actual_sdk_environment_keeps_execution_home_and_does_not_reinherit_gui_or_keys(tmp_path, monkeypatch):
    from packages.adapters.opencode import OpenCodeBridge
    for key, value in host_environment().items(): monkeypatch.setenv(key, value)
    home = tmp_path/'owned SDK execution'; home.mkdir()
    actual = OpenCodeBridge._environment(home, home/'config/opencode.json', 'DUMMY_PRIVATE_PASSWORD')
    assert actual['HOME'] == actual['USERPROFILE'] == str(home)
    assert actual['APPDATA'] == str(home/'roaming') and actual['LOCALAPPDATA'] == str(home/'local')
    assert actual['XDG_CONFIG_HOME'] == str(home/'config') and actual['XDG_DATA_HOME'] == str(home/'data')
    assert all(key not in actual for key in ('HOMEDRIVE', 'HOMEPATH', 'OPENAI_API_KEY', 'ELECTRON_RUN_AS_NODE'))
