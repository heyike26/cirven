"""Real policy files and fault counters; no Windows, SDK driver, GUI or input.

SDK/Win32 counters below test guards and argument plumbing only. Root performs
the separate actual Windows process-image/resource-authorization acceptance.
"""
import asyncio
import copy
import ctypes as ct
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from packages.adapters.cua_desktop_input import (CuaDesktopInput, CuaInputError,
    NativeCuaSessionFactory, _readonly_manifest)
from packages.adapters.desktop_current_context import (CurrentContextError,
    DWORD, Win32CurrentWindowSource)
from packages.adapters.windows_desktop_region import _sha
from test_native_collaboration import BIND, BUDGET, TOKEN, host


def write_policy(path, *, resources=True):
    value = {'version': 3, 'expires_after': '5m', 'idle_timeout': '5m',
        'allow': {'tools': ['get_window_state']}}
    if resources:
        value['resources'] = {'apps': [{'executable': r'C:\Windows\System32\notepad.exe', 'windows': 'all'}],
            'desktop': {'display': False}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False))
    return value, {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def readonly_binding():
    ref = lambda ident: {'id': ident, 'revision': 1, 'sha256': 'a' * 64}
    return {'surface_ref': ref('surface'), 'workspace_session_ref': ref('workspace'),
        'observation_run_ref': ref('readonly-run'), 'execution_ref': None,
        'runtime_role': 'READONLY_OBSERVATION', 'session_label': 'readonly-policy', 'budget': copy.deepcopy(BUDGET)}


def test_actual_policy_file_only_allows_one_exact_app_and_no_display(tmp_path):
    value, ref = write_policy(tmp_path / 'actual-readonly.yaml')
    assert _readonly_manifest(Path(ref['path'])) == Path(ref['path'])
    bound = CuaDesktopInput._binding({**readonly_binding(), 'readonly_policy_ref': ref})
    assert bound['readonly_policy_ref'] == ref and bound['execution_ref'] is None
    assert bound['budget']['max_actions'] == 0 and not bound['budget']['allow_foreground']
    assert value['resources']['desktop']['display'] is False
    wrong = {**ref, 'sha256': 'f' * 64}
    with pytest.raises(CuaInputError):
        CuaDesktopInput._binding({**readonly_binding(), 'readonly_policy_ref': wrong})
    with pytest.raises(CuaInputError):
        CuaDesktopInput._binding({**bound, 'runtime_role': 'TASK_INPUT'})
    Path(ref['path']).write_text('allow: {tools: [click]}\n')
    with pytest.raises(CuaInputError):
        CuaDesktopInput._binding(bound)


@pytest.mark.parametrize('change', [
    lambda p: p['allow']['tools'].append('click'),
    lambda p: p['allow']['tools'].append('get_desktop_state'),
    lambda p: p['resources']['desktop'].update(display=True),
    lambda p: p['resources']['desktop'].update(display=0),
    lambda p: p['resources'].update(files={'read': [{'dir': 'D:/'}]}),
    lambda p: p['resources'].update(browser={'origins': ['https://example.invalid']}),
    lambda p: p['resources'].update(network='all'),
    lambda p: p['resources']['apps'][0].update(launch=True),
    lambda p: p['resources']['apps'][0].update(terminate='driver_launched'),
    lambda p: p['resources']['apps'][0].update(executable='notepad.exe'),
    lambda p: p['resources']['apps'][0].update(executable=r'C:\Windows\*.exe'),
    lambda p: p['resources']['apps'][0].update(executable=r'C:\Windows\notepad?.exe'),
    lambda p: p['resources']['apps'].append({'executable': 'D:/other.exe', 'windows': 'all'}),
    lambda p: p['allow'].update(rules=[{'tool': 'type_text'}]),
    lambda p: p.update(permission_mode='unrestricted'),
])
def test_broad_or_unknown_readonly_resource_policy_is_rejected(tmp_path, change):
    path = tmp_path / 'refused.yaml'; value, _ref = write_policy(path)
    change(value); path.write_text(yaml.safe_dump(value))
    with pytest.raises(CuaInputError):
        _readonly_manifest(path)


def test_host_receipt_binds_the_entire_readonly_file_reference(tmp_path):
    authority, _native = host(tmp_path / 'actual-host-records')
    authority.bind(BIND)
    _value, policy = write_policy(tmp_path / 'root-created-policy.yaml')
    binding = authority.observation_session_binding(session_label='readonly-actual-file', budget=BUDGET,
        readonly_policy_ref=policy)
    assert binding['readonly_policy_ref'] == policy
    assert authority.observation_bindings[binding['observation_run_ref']['id']] == binding
    changed = copy.deepcopy(binding)
    changed['readonly_policy_ref']['sha256'] = 'f' * 64
    assert _sha(changed) != _sha(binding)
    receipt = {'runtime_role': 'READONLY_OBSERVATION', 'workspace_session_ref': binding['workspace_session_ref'],
        'surface_ref': binding['surface_ref'], 'session_ref': {'id': 'opened', 'revision': 1,
            'sha256': _sha({'id': 'opened', 'binding': binding})},
        'observation_run_ref': binding['observation_run_ref'], 'binding_sha256': _sha(binding)}
    forged = {**receipt, 'binding_sha256': _sha(changed), 'session_ref': {**receipt['session_ref'],
        'sha256': _sha({'id': 'opened', 'binding': changed})}}
    from packages.domain.store import Conflict
    with pytest.raises(Conflict):
        authority.set_observation_session(forged)
    authority.set_observation_session(receipt)
    assert authority.binding_reader()['sdk_session_ref'] == receipt['session_ref']
    assert authority.get()['input']['enabled'] is False
    authority.close()


class SDKOptionsCounter:
    SessionPermissionMode = SimpleNamespace(BOUNDED='BOUNDED')
    def __init__(self):
        self.options = []
    @staticmethod
    def TrustedSessionOptions(**kwargs):
        return SimpleNamespace(**kwargs)
    def create_trusted_session(self, driver, options):
        self.options.append(options)
        return object()


def factory_counter(root, default_ref):
    sdk = SDKOptionsCounter()
    factory = NativeCuaSessionFactory(runtime_root=root,
        component_manifest_ref={'path': str(root / 'unused-component.json'), 'sha256': 'a' * 64},
        capability_manifest_ref=default_ref, readonly_capability_manifest_ref=default_ref)
    # These counters test only file validation/TrustedSessionOptions selection.
    # No native probe/driver or original SDK call can run in this seam.
    factory.probe = lambda: (sdk, Path(default_ref['path']))
    factory.driver = object(); factory.sdk = sdk
    return factory, sdk


def test_factory_selects_actual_root_created_policy_and_bounded_readonly_session(tmp_path):
    runtime = tmp_path / 'selected-runtime'; _, default = write_policy(runtime / 'default.yaml', resources=False)
    _, target = write_policy(runtime / 'target-readonly.yaml')
    factory, sdk = factory_counter(runtime, default)
    native = asyncio.run(factory.open({**readonly_binding(), 'readonly_policy_ref': target}))
    assert native.readonly
    assert sdk.options[0].capability_manifest_path == target['path']
    assert sdk.options[0].bounded_manifest_path == target['path'] and sdk.options[0].mode == 'BOUNDED'
    legacy = asyncio.run(factory.open(readonly_binding()))
    assert legacy.readonly and sdk.options[1].capability_manifest_path == default['path']


def test_factory_rejects_policy_outside_selected_runtime_or_changed_hash_before_session(tmp_path):
    runtime = tmp_path / 'selected-runtime'; _, default = write_policy(runtime / 'default.yaml', resources=False)
    _, outside = write_policy(tmp_path / 'outside.yaml')
    factory, sdk = factory_counter(runtime, default)
    with pytest.raises(CuaInputError):
        asyncio.run(factory.open({**readonly_binding(), 'readonly_policy_ref': outside}))
    assert sdk.options == []
    _, target = write_policy(runtime / 'target.yaml')
    Path(target['path']).write_text('changed original bytes')
    with pytest.raises(CuaInputError):
        asyncio.run(factory.open({**readonly_binding(), 'readonly_policy_ref': target}))
    assert sdk.options == []


class ProcessImageCounter:
    def __init__(self):
        self.opened, self.closed, self.queried = [], [], []
        self.changed_process = self.query_failed = False
        self.executable = r'C:\Windows\System32\notepad.exe'
        self.kernel = SimpleNamespace(OpenProcess=self.open, CloseHandle=self.close)
    def open(self, access, inherit, pid):
        self.opened.append((access, inherit, pid)); return 321
    def close(self, handle):
        self.closed.append(handle); return 1
    def process(self, handle):
        return {'pid': 44, 'creation': 900 + int(self.changed_process), 'session': 1, 'exit_code': 259}
    def _bind(self, library, name, returns, args):
        assert name == 'QueryFullProcessImageNameW' and library is self.kernel and len(args) == 4
        def query(handle, flags, buffer, count):
            self.queried.append((handle, flags))
            if self.query_failed:
                return 0
            buffer.value = self.executable
            ct.cast(count, ct.POINTER(DWORD)).contents.value = len(buffer.value.encode('utf-16-le')) // 2
            return 1
        return query


def source_counter(*, changed_window=False):
    source = Win32CurrentWindowSource.__new__(Win32CurrentWindowSource)
    source.api = ProcessImageCounter()
    metadata = {'status': 'WINDOW_OBSERVED', 'window_id': 123, 'pid': 44, 'process_creation': 900,
        'os_session': 1, 'identity_sha256': 'a' * 64, 'title': 'A title that is not executable metadata'}
    calls = []
    def inspect(window_id):
        calls.append(window_id)
        return {**metadata, 'identity_sha256': 'b' * 64 if changed_window and len(calls) > 1 else 'a' * 64}
    source.inspect = inspect
    return source, metadata, calls


def test_process_image_query_uses_limited_handle_and_rechecks_identity_without_title_adoption():
    source, metadata, calls = source_counter()
    assert source.process_executable(metadata) == r'C:\Windows\System32\notepad.exe'
    assert calls == [123, 123] and source.api.opened == [(0x1000, False, 44)]
    assert source.api.queried == [(321, 0)] and source.api.closed == [321]


def test_process_image_query_preserves_actual_unicode_executable_utf16_length():
    source, metadata, _calls = source_counter()
    source.api.executable = 'D:\\用户资料\\原生🙂编辑器.exe'
    assert source.process_executable(metadata) == source.api.executable
    assert source.api.closed == [321]


@pytest.mark.parametrize('mode', ['window-reused', 'process-reused', 'query-refused', 'relative-path', 'stale-input'])
def test_process_image_identity_or_query_failure_never_returns_a_resource(mode):
    source, metadata, _calls = source_counter(changed_window=mode == 'window-reused')
    source.api.changed_process = mode == 'process-reused'
    source.api.query_failed = mode == 'query-refused'
    if mode == 'relative-path':
        source.api.executable = 'notepad.exe'
    if mode == 'stale-input':
        metadata = {**metadata, 'identity_sha256': 'b' * 64}
    with pytest.raises(CurrentContextError):
        source.process_executable(metadata)
    assert source.api.closed == ([] if mode == 'stale-input' else [321])
