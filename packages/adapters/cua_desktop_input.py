"""One frozen Cua SDK controller; no model loop or unrestricted MCP passthrough.

Construction/import never creates a native driver/session. The trusted host
explicitly calls open_session after its GUI qualification. All SDK lifecycle
and awaits use one dedicated thread/loop. Root sync control guard includes its
workspace guard and remains held throughout each actual mutation.
"""
from __future__ import annotations
import asyncio
import base64
import concurrent.futures
import ctypes as ct
from dataclasses import fields, is_dataclass
import enum
import hashlib
import importlib.metadata
import inspect
from io import BytesIO
import json
import math
import os
from pathlib import Path, PureWindowsPath
import queue
import re
import sys
import threading
import time
import uuid

from packages.adapters.windows_desktop_region import surface_reference, _sha


class CuaInputError(ValueError):
    pass


def _copy(value, *, limit=4 * 1024 * 1024):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > limit:
        raise CuaInputError('CLOSED_JSON_BUDGET_EXCEEDED')
    return json.loads(raw)


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise CuaInputError(name)
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9_-]{1,120}', value):
        raise CuaInputError('OPAQUE_OPERATION_OR_SESSION_ID_REQUIRED')
    return value


def _json_result(value):
    if is_dataclass(value):
        names = [f.name for f in fields(value)]
        if names:
            return {name: _json_result(getattr(value, name)) for name in names}
        # UniFFI 0.35.2 records declare a custom __init__, with no annotated
        # dataclass fields. fields(record) == () must not erase real evidence.
        if type(value).__module__ not in {'cua_driver._native', 'cua_driver._native_contract'}:
            raise CuaInputError('UNKNOWN_EMPTY_DATACLASS_RECORD')
        names = list(inspect.signature(type(value)).parameters)
        if any(name.startswith('_') or not hasattr(value, name) for name in names):
            raise CuaInputError('SDK_GENERATED_RECORD_CONTRACT_CHANGED')
        return {'sdk_type': type(value).__qualname__,
                **{name: _json_result(getattr(value, name)) for name in names}}
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, (list, tuple)):
        return [_json_result(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_result(v) for k, v in value.items()}
    if value is None or type(value) in (str, bool, int, float):
        return value
    raise CuaInputError('SDK_RESULT_NOT_STRUCTURED')


def _checked_reference_file(value, *, boundary=None):
    if not isinstance(value, dict) or set(value) != {'path', 'sha256'}:
        raise CuaInputError('EXACT_PRIVATE_FILE_REFERENCE_REQUIRED')
    path = Path(value['path'])
    if (not path.is_absolute() or not isinstance(value['sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', value['sha256'])
            or any(p.is_symlink() for p in (path, *path.parents))
            or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024
            or (boundary is not None and not path.resolve().is_relative_to(boundary.resolve()))
            or hashlib.sha256(path.read_bytes()).hexdigest() != value['sha256']):
        raise CuaInputError('PRIVATE_POLICY_OR_RUNTIME_SOURCE_CHANGED')
    return path


def _readonly_manifest(path):
    """Require a real native tool ceiling, not only a Python role flag."""
    import yaml
    class UniqueLoader(yaml.SafeLoader):
        pass
    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise CuaInputError('READONLY_POLICY_DUPLICATE_KEY')
            result[key] = loader.construct_object(value_node, deep=deep)
        return result
    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        value = yaml.load(Path(path).read_bytes(), Loader=UniqueLoader)
    except (ValueError, yaml.YAMLError, TypeError):
        raise CuaInputError('READONLY_ORIGINAL_POLICY_INVALID') from None
    if (not isinstance(value, dict) or set(value) not in (
            {'version', 'expires_after', 'idle_timeout', 'allow'},
            {'version', 'expires_after', 'idle_timeout', 'allow', 'resources'})
            or type(value.get('version')) is not int or value['version'] != 3
            or not isinstance(value.get('expires_after'), str) or not value['expires_after']
            or not isinstance(value.get('idle_timeout'), str) or not value['idle_timeout']
            or not isinstance(value.get('allow'), dict)
            or set(value['allow']) != {'tools'}
            or value['allow'].get('tools') != ['get_window_state']):
        raise CuaInputError('READONLY_POLICY_MUST_ALLOW_ONLY_GET_WINDOW_STATE')
    resources = value.get('resources')
    if 'resources' in value:
        if (not isinstance(resources, dict) or set(resources) != {'apps', 'desktop'}
                or not isinstance(resources['desktop'], dict) or set(resources['desktop']) != {'display'}
                or resources['desktop']['display'] is not False or not isinstance(resources['apps'], list)
                or len(resources['apps']) != 1):
            raise CuaInputError('READONLY_POLICY_SINGLE_APP_NO_DISPLAY_FILES_OR_NETWORK_REQUIRED')
        app = resources['apps'][0]
        if (not isinstance(app, dict) or set(app) != {'executable', 'windows'} or app['windows'] != 'all'
                or not isinstance(app['executable'], str) or not app['executable']
                or not (Path(app['executable']).is_absolute() or PureWindowsPath(app['executable']).is_absolute())
                or '..' in app['executable'].replace('\\', '/').split('/')
                or any(ord(c) < 32 for c in app['executable'])
                or any(c in app['executable'].removeprefix('\\\\?\\') for c in '*?')):
            raise CuaInputError('READONLY_POLICY_EXACT_ABSOLUTE_APP_EXECUTABLE_REQUIRED')
    return path


def _task_manifest(path):
    """Frozen app-only input ceiling; per-HWND authority remains a second gate."""
    import yaml
    raw = Path(path).read_bytes()
    if len(raw) > 65536:
        raise CuaInputError('TASK_POLICY_BYTE_BUDGET_EXCEEDED')
    class UniqueLoader(yaml.SafeLoader):
        pass
    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise CuaInputError('TASK_POLICY_DUPLICATE_OR_NONSTRING_KEY')
            result[key] = loader.construct_object(value_node, deep=deep)
        return result
    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        value = yaml.load(raw, Loader=UniqueLoader)
    except (ValueError, TypeError, yaml.YAMLError):
        raise CuaInputError('TASK_ORIGINAL_POLICY_INVALID') from None
    if (not isinstance(value, dict) or set(value) != {'version', 'expires_after', 'idle_timeout', 'allow', 'resources'}
            or type(value['version']) is not int or value['version'] != 3
            or not all(isinstance(value[k], str) and re.fullmatch(r'[1-9][0-9]{0,3}[sm]', value[k]) for k in ('expires_after', 'idle_timeout'))
            or not isinstance(value['allow'], dict) or set(value['allow']) != {'tools'}):
        raise CuaInputError('TASK_POLICY_BOUNDED_APP_ONLY_REQUIRED')
    tools = value['allow']['tools']
    if any(int(value[key][:-1]) * (60 if value[key][-1] == 'm' else 1) > 600
            for key in ('expires_after', 'idle_timeout')):
        raise CuaInputError('TASK_POLICY_TTL_EXCEEDS_BOUNDED_COMMAND_CEILING')
    if (not isinstance(tools, list) or not tools or not all(isinstance(tool, str) for tool in tools)
            or len(set(tools)) != len(tools) or 'get_window_state' not in tools
            or not set(tools) <= {'get_window_state', 'click', 'type_text', 'press_key'}
            or not set(tools) & {'click', 'type_text', 'press_key'}):
        raise CuaInputError('TASK_POLICY_EXACT_DECLARED_TOOLS_REQUIRED')
    resources = value['resources']
    if (not isinstance(resources, dict) or set(resources) != {'apps', 'desktop'}
            or not isinstance(resources['desktop'], dict) or set(resources['desktop']) != {'display'}
            or resources['desktop']['display'] is not False or not isinstance(resources['apps'], list)
            or len(resources['apps']) != 1):
        raise CuaInputError('TASK_POLICY_SINGLE_APP_NO_DISPLAY_FILES_OR_NETWORK_REQUIRED')
    app = resources['apps'][0]
    if (not isinstance(app, dict) or set(app) != {'executable', 'windows'} or app['windows'] != 'all'
            or not isinstance(app['executable'], str)
            or not (Path(app['executable']).is_absolute() or PureWindowsPath(app['executable']).is_absolute())
            or '..' in app['executable'].replace('\\', '/').split('/')
            or any(ord(c) < 32 for c in app['executable'])
            or any(c in app['executable'].removeprefix('\\\\?\\') for c in '*?')):
        raise CuaInputError('TASK_POLICY_EXACT_ABSOLUTE_APP_EXECUTABLE_REQUIRED')
    return path


class NativeCuaSessionFactory:
    """Frozen 0.35.2 SDK factory; actual create happens only in worker open()."""
    _owner_lock = threading.Lock()
    _owner = None

    def __init__(self, *, runtime_root, component_manifest_ref, capability_manifest_ref,
                 readonly_capability_manifest_ref=None):
        self.root = Path(runtime_root)
        if not self.root.is_absolute():
            raise CuaInputError('ABSOLUTE_SELECTED_RUNTIME_REQUIRED')
        self.manifest_ref = _copy(component_manifest_ref)
        self.policy_ref = _copy(capability_manifest_ref)
        self.readonly_policy_ref = (_copy(readonly_capability_manifest_ref)
            if readonly_capability_manifest_ref is not None else None)
        self.driver = self.sdk = None
        self.token = uuid.uuid4().hex
        self.worker_thread = None

    def probe(self):
        if (os.name != 'nt' or self.root.drive.upper() not in {'D:', 'E:'}
                or ct.sizeof(ct.c_void_p) != 8 or sys.version_info[:2] != (3, 10)):
            raise CuaInputError('WINDOWS_PY310_X64_SELECTED_D_OR_E_RUNTIME_REQUIRED')
        path = _checked_reference_file(self.manifest_ref, boundary=self.root)
        policy = _checked_reference_file(self.policy_ref, boundary=self.root)
        manifest = json.loads(path.read_bytes())
        components = [w for w in manifest.get('wheels', []) if w.get('name') == 'cua-driver']
        if len(components) != 1 or components[0].get('version') != '0.35.2':
            raise CuaInputError('FROZEN_CUA_DRIVER_0_35_2_REQUIRED')
        dist = importlib.metadata.distribution('cua-driver')
        if dist.version != '0.35.2':
            raise CuaInputError('INSTALLED_CUA_VERSION_CHANGED')
        for member, expected in components[0]['engine_files'].items():
            target = Path(dist.locate_file(member))
            if (not target.resolve().is_relative_to(self.root.resolve())
                    or any(p.is_symlink() for p in (target, *target.parents))
                    or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected):
                raise CuaInputError('INSTALLED_CUA_ENGINE_CHANGED_OR_OUTSIDE_RUNTIME')
        import cua_driver
        if (cua_driver.__version__ != '0.35.2' or not Path(cua_driver.__file__).resolve().is_relative_to(self.root.resolve())):
            raise CuaInputError('IMPORTED_CUA_VERSION_CHANGED')
        return cua_driver, policy

    def _same_worker(self):
        current = threading.get_ident()
        if self.worker_thread is not None and self.worker_thread != current:
            raise CuaInputError('SDK_CONTROLLER_THREAD_CHANGED')
        self.worker_thread = current

    async def open(self, binding):
        self._same_worker()
        sdk, compatibility_policy = self.probe()
        role = binding.get('runtime_role', 'TASK_INPUT')
        if role not in {'TASK_INPUT', 'READONLY_OBSERVATION'}:
            raise CuaInputError('DECLARED_CUA_RUNTIME_ROLE_REQUIRED')
        policy = compatibility_policy
        if role == 'READONLY_OBSERVATION':
            selected_ref = binding.get('readonly_policy_ref', self.readonly_policy_ref)
            if selected_ref is None:
                raise CuaInputError('EXPLICIT_READONLY_NATIVE_POLICY_REFERENCE_REQUIRED')
            policy = _checked_reference_file(selected_ref, boundary=self.root)
            _readonly_manifest(policy)
        elif 'task_policy_ref' in binding:
            policy = _checked_reference_file(binding['task_policy_ref'], boundary=self.root)
            _task_manifest(policy)
        if self.driver is None:
            with self._owner_lock:
                if self.__class__._owner is not None:
                    raise CuaInputError('ONE_CUA_CONTROLLER_PER_PROCESS_REQUIRED')
                self.__class__._owner = self.token
            try:
                # The generated SDK explicitly supports callback loop injection.
                from cua_driver import _native
                _native.uniffi_set_event_loop(asyncio.get_running_loop())
                class DenyAdditionalRisk(sdk.DriverAuthorizationHost):
                    async def authorize(self, request):
                        # Callback is never permission to bypass Root's guard.
                        # This first slice does not wait on UI/API/Root RLocks.
                        return sdk.DriverAuthorizationDecision(action=sdk.DriverAuthorizationAction.DENY,
                                                               request_digest=request.request_digest)
                auth = sdk.RuntimeAuthorizationOptions(
                    allowed_modes=[sdk.SessionPermissionMode.BOUNDED],
                    compatibility_mode=sdk.SessionPermissionMode.BOUNDED,
                    compatibility_capability_manifest_path=str(compatibility_policy),
                    compatibility_bounded_manifest_path=str(compatibility_policy), unrestricted_acknowledged=False,
                    max_session_ttl_seconds=3600, max_idle_ttl_seconds=3600)
                options = sdk.ConfiguredDriverOptions(claude_code_compatibility=False, authorization=auth)
                self.driver = sdk.CuaDriver.create_configured_with_authorization_host(options, DenyAdditionalRisk())
                self.sdk = sdk
            except BaseException:
                with self._owner_lock:
                    if self.__class__._owner == self.token:
                        self.__class__._owner = None
                raise
        opts = sdk.TrustedSessionOptions(public_session=binding['session_label'], mode=sdk.SessionPermissionMode.BOUNDED,
            ttl_seconds=binding['budget']['ttl_seconds'], idle_ttl_seconds=binding['budget']['ttl_seconds'],
            capability_manifest_path=str(policy), bounded_manifest_path=str(policy))
        return _NativeSession(sdk, sdk.create_trusted_session(self.driver, opts),
            readonly=role == 'READONLY_OBSERVATION')

    async def shutdown(self):
        self._same_worker()
        try:
            if self.driver is not None:
                await self.driver.shutdown()
        finally:
            self.driver = None
            with self._owner_lock:
                if self.__class__._owner == self.token:
                    self.__class__._owner = None


class _NativeSession:
    """Exact released typed mutation API; capture keeps canonical extra fields."""
    def __init__(self, sdk, session, *, readonly=False):
        self.sdk, self.session = sdk, session
        if type(readonly) is not bool:
            raise CuaInputError('EXPLICIT_NATIVE_READONLY_ROLE_REQUIRED')
        self.readonly = readonly

    async def call_tool(self, name, args):
        if name not in {'get_window_state', 'get_desktop_state'}:
            raise CuaInputError('NO_UNRESTRICTED_SDK_TOOL_PASSTHROUGH')
        if self.readonly and name != 'get_window_state':
            raise CuaInputError('READONLY_OBSERVATION_WINDOW_ONLY_NO_DISPLAY')
        return await self.session.call_tool(name, args)

    async def action(self, name, args):
        if self.readonly:
            raise CuaInputError('READONLY_OBSERVATION_CANNOT_MUTATE')
        sdk = self.sdk
        target = args['target']
        typed = None
        if name == 'click' or 'delivery_mode' not in args:
            typed = sdk.ActionTarget.WINDOW(target['pid'], target['window_id']) if target['kind'] == 'window' else sdk.ActionTarget.DESKTOP('primary')
        if name == 'click':
            mode = sdk.InputDeliveryMode.BACKGROUND if args['delivery_mode'] == 'background' else sdk.InputDeliveryMode.FOREGROUND
            position = (sdk.ClickPosition.ELEMENT(args['element_token']) if 'element_token' in args else
                sdk.ClickPosition.CAPTURED_COORDINATES(args['x'], args['y'], args['capture_id']))
            value = sdk.ClickInput(target=typed,
                position=position,
                delivery_mode=mode, session=None, button=None, count=1)
            actual = await self.session.click(value)
            return {'is_error': actual.error is not None or actual.effect == sdk.ActionEffect.REFUSED,
                    'error_code': actual.error.code if actual.error is not None else None,
                    'action': _json_result(actual), 'images': []}
        if name == 'type_text':
            if 'delivery_mode' in args:
                if target['kind'] != 'window' or args['delivery_mode'] not in {'background', 'foreground'}:
                    raise CuaInputError('EXACT_WINDOW_EXPLICIT_DELIVERY_REQUIRED')
                return await self.session.call_tool('type_text', json.dumps({'pid': target['pid'],
                    'window_id': target['window_id'], 'text': args['text'], 'delivery_mode': args['delivery_mode']}))
            return await self.session.type_text(sdk.TypeTextInput(text=args['text'], target=typed, scope=None, session=None))
        if name == 'press_key':
            if 'delivery_mode' in args:
                if target['kind'] != 'window' or args['delivery_mode'] not in {'background', 'foreground'}:
                    raise CuaInputError('EXACT_WINDOW_EXPLICIT_DELIVERY_REQUIRED')
                return await self.session.call_tool('press_key', json.dumps({'pid': target['pid'],
                    'window_id': target['window_id'], 'key': args['key'], 'delivery_mode': args['delivery_mode']}))
            return await self.session.press_key(sdk.PressKeyInput(key=args['key'], target=typed, scope=None, session=None, modifiers=None))
        raise CuaInputError('UNDECLARED_SDK_ACTION')

    def close(self):
        self.session.close()


class _SDKWorker:
    """Serial bounded I/O jobs, not an Agent planner/LLM loop."""
    def __init__(self):
        self.jobs = queue.Queue(maxsize=64)
        self.closed = False
        self.thread = threading.Thread(target=self._run, name='EEAS-Cua-SDK-owned-IO', daemon=True)
        self.thread.start()

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while True:
                item = self.jobs.get()
                if item is None:
                    break
                fn, future, _tag = item
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    future.set_result(loop.run_until_complete(fn()))
                except BaseException as error:
                    future.set_exception(error)
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def submit(self, fn, *, mutation_tag=None):
        if self.closed:
            raise CuaInputError('SDK_WORKER_CLOSED')
        if threading.get_ident() == self.thread.ident:
            raise CuaInputError('SDK_WORKER_REENTRANT_DISPATCH_REJECTED')
        future = concurrent.futures.Future()
        try:
            self.jobs.put_nowait((fn, future, mutation_tag))
        except queue.Full:
            raise CuaInputError('SDK_INPUT_QUEUE_BUDGET_EXCEEDED') from None
        # SDK awaits carry an explicit timeout. No pretend cancellation of
        # already sent OS input is performed by the calling FastAPI thread.
        return future

    def call(self, fn, *, mutation_tag=None):
        return self.submit(fn, mutation_tag=mutation_tag).result()

    def revoke_mutations(self, tag):
        count = 0
        with self.jobs.mutex:
            keep = []
            while self.jobs.queue:
                item = self.jobs.queue.popleft()
                if item is not None and item[2] == tag:
                    item[1].set_exception(CuaInputError('QUEUED_MUTATION_REVOKED_BEFORE_SDK_DISPATCH'))
                    count += 1
                else:
                    keep.append(item)
            self.jobs.queue.extend(keep)
            self.jobs.not_full.notify_all()
        return count

    def close(self):
        self.closed = True
        self.jobs.put(None)
        self.thread.join(timeout=10)
        return not self.thread.is_alive()


class CuaDesktopInput:
    def __init__(self, *, data_dir, session_factory, target_authority, observation_guard,
                 control_guard, frame_validator=None, platform=None, clock=None):
        if not all(callable(x) for x in [target_authority, observation_guard, control_guard]):
            raise CuaInputError('EXACT_TARGET_AND_BOTH_SCOPE_GUARDS_REQUIRED')
        self.platform = os.name if platform is None else platform
        self.data = Path(data_dir)
        if (not self.data.is_absolute() or any(p.is_symlink() for p in (self.data, *self.data.parents))
                or (self.platform == 'nt' and self.data.drive.upper() not in {'D:', 'E:'})):
            raise CuaInputError('SELECTED_PRIVATE_D_OR_E_DATA_DIRECTORY_REQUIRED')
        self.factory = session_factory
        if not callable(getattr(session_factory, 'open', None)) or not callable(getattr(session_factory, 'shutdown', None)):
            raise CuaInputError('ORIGINAL_SDK_SESSION_FACTORY_REQUIRED')
        self.target_authority = target_authority
        self.observation_guard, self.control_guard = observation_guard, control_guard
        self.frame_validator = frame_validator
        self.clock = time.time if clock is None else clock
        self.worker = _SDKWorker()
        self.records, self.frames, self.operations = {}, {}, {}
        self.revoked = set()
        self.queue_lock = threading.Lock()
        self.closed = False

    @staticmethod
    def _binding(value):
        expected = {'surface_ref', 'execution_ref', 'session_label', 'budget'}
        if not isinstance(value, dict):
            raise CuaInputError('IMMUTABLE_SESSION_BINDING_REQUIRED')
        role = value.get('runtime_role', 'TASK_INPUT')
        if role == 'READONLY_OBSERVATION':
            expected |= {'runtime_role', 'workspace_session_ref', 'observation_run_ref'}
            if 'readonly_policy_ref' in value:
                expected |= {'readonly_policy_ref'}
        elif role == 'TASK_INPUT':
            if 'runtime_role' in value:
                expected |= {'runtime_role', 'workspace_session_ref'}
            if 'task_policy_ref' in value:
                expected |= {'task_policy_ref'}
        else:
            raise CuaInputError('DECLARED_CUA_RUNTIME_ROLE_REQUIRED')
        if set(value) != expected:
            raise CuaInputError('IMMUTABLE_SESSION_BINDING_REQUIRED')
        value = _copy(value, limit=65536)
        value['surface_ref'] = surface_reference(value['surface_ref'])
        if 'workspace_session_ref' in value:
            value['workspace_session_ref'] = surface_reference(value['workspace_session_ref'])
        _identifier(value['session_label'])
        execution = value['execution_ref']
        if role == 'READONLY_OBSERVATION':
            if execution is not None:
                raise CuaInputError('READONLY_OBSERVATION_HAS_NO_MODEL_EXECUTION')
            value['observation_run_ref'] = surface_reference(value['observation_run_ref'])
            if 'readonly_policy_ref' in value:
                _checked_reference_file(value['readonly_policy_ref'])
        elif (not isinstance(execution, dict) or not {'project_id', 'execution_id', 'request_sha256'} <= set(execution)
                or set(execution) - {'project_id', 'execution_id', 'request_sha256', 'task_ref'}):
            raise CuaInputError('EXACT_ORIGINAL_EXECUTION_REFERENCE_REQUIRED')
        if role == 'TASK_INPUT':
            if 'task_policy_ref' in value:
                _checked_reference_file(value['task_policy_ref'])
            _identifier(execution['project_id']); _identifier(execution['execution_id'])
            if not isinstance(execution['request_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', execution['request_sha256']):
                raise CuaInputError('EXACT_ORIGINAL_EXECUTION_REQUEST_HASH_REQUIRED')
            if execution.get('task_ref') is not None:
                surface_reference(execution['task_ref'])
        budget = value['budget']
        if not isinstance(budget, dict) or set(budget) != {'max_actions', 'timeout_ms', 'max_frame_age_ms', 'ttl_seconds', 'allow_foreground'}:
            raise CuaInputError('IMMUTABLE_CUA_OPERATION_BUDGET_REQUIRED')
        if type(budget['allow_foreground']) is not bool:
            raise CuaInputError('EXPLICIT_FROZEN_FOREGROUND_PERMISSION_REQUIRED')
        if role == 'READONLY_OBSERVATION' and budget['allow_foreground'] is not False:
            raise CuaInputError('READONLY_OBSERVATION_CANNOT_AUTHORIZE_FOREGROUND_INPUT')
        action_bounds = (0, 0) if role == 'READONLY_OBSERVATION' else (1, 1000)
        for name, lo, hi in [('max_actions', *action_bounds), ('timeout_ms', 100, 15000), ('max_frame_age_ms', 100, 30000), ('ttl_seconds', 1, 3600)]:
            _integer(budget[name], lo, hi, 'CUA_BUDGET_INVALID_' + name)
        return value

    def _record(self, ref):
        if self.closed:
            raise CuaInputError('ADAPTER_CLOSED')
        ref = surface_reference(ref)
        record = self.records.get(ref['id'])
        if record is None or record['ref'] != ref:
            raise CuaInputError('EXACT_CUA_SESSION_UNKNOWN')
        if record['closed'] or record['fault']:
            raise CuaInputError('CUA_SESSION_CLOSED_OR_OUTCOME_UNKNOWN')
        if self.clock() >= record['expires_at']:
            record['native'].close(); record['closed'] = True
            raise CuaInputError('CUA_SESSION_EXPIRED')
        return record

    def _target(self, target_ref, surface_ref, input_epoch):
        ref = surface_reference(target_ref)
        _integer(input_epoch, 0, 1 << 63, 'EXACT_INPUT_EPOCH_REQUIRED')
        actual = _copy(self.target_authority(ref, surface_ref, input_epoch), limit=65536)
        if (not isinstance(actual, dict) or actual.get('kind') not in {'WINDOW', 'DESKTOP'}
                or actual.get('self_target') is not False
                or not isinstance(actual.get('identity_sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', actual['identity_sha256'])):
            raise CuaInputError('HOST_ATTESTED_NON_SELF_TARGET_REQUIRED')
        if actual['kind'] == 'WINDOW':
            _integer(actual.get('pid'), 1, (1 << 32) - 1, 'EXACT_WINDOW_PID_REQUIRED')
            _integer(actual.get('window_id'), 1, (1 << 64) - 1, 'EXACT_NATIVE_WINDOW_ID_REQUIRED')
            _integer(actual.get('process_creation'), 1, (1 << 64) - 1, 'EXACT_WINDOW_PROCESS_CREATION_REQUIRED')
        elif actual.get('display_id') != 'primary':
            raise CuaInputError('WINDOWS_PRIMARY_DISPLAY_ONLY')
        bounds = actual.get('bounds')
        if (not isinstance(bounds, dict) or set(bounds) != {'x', 'y', 'width', 'height'}
                or any(type(v) is not int for v in bounds.values()) or min(bounds['width'], bounds['height']) < 1):
            raise CuaInputError('ACTUAL_TARGET_PHYSICAL_BOUNDS_REQUIRED')
        return actual

    def _write(self, relative, raw):
        self.data.mkdir(parents=True, exist_ok=True)
        if any(p.is_symlink() for p in (self.data, *self.data.parents)):
            raise CuaInputError('PRIVATE_RUNTIME_DATA_LINK_CHANGED')
        directory = self.data / relative.split('/')[0]
        directory.mkdir(exist_ok=True)
        if directory.is_symlink():
            raise CuaInputError('PRIVATE_RUNTIME_ARTIFACT_LINK_FORBIDDEN')
        path = self.data / relative
        with path.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        return {'path': relative, 'sha256': hashlib.sha256(raw).hexdigest(), 'byte_length': len(raw)}

    async def _bounded(self, record, awaitable):
        try:
            return await asyncio.wait_for(awaitable, record['binding']['budget']['timeout_ms'] / 1000)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            record['fault'] = True
            record['native'].close()
            raise CuaInputError('SDK_OPERATION_OUTCOME_UNKNOWN_SESSION_REVOKED') from None

    @staticmethod
    def _pixel_proof(value, raw, target):
        if value is None or type(value) is bool:
            return None
        value = _copy(value, limit=65536)
        expected = {'status', 'image_sha256', 'target_identity_sha256', 'target_bounds',
                    'evidence_ref', 'capture_method', 'self_exclusion_verified'}
        if (not isinstance(value, dict) or set(value) != expected
                or value['status'] != 'VERIFIED_TARGET_PIXELS'
                or value['image_sha256'] != hashlib.sha256(raw).hexdigest()
                or value['target_identity_sha256'] != target['identity_sha256']
                or value['target_bounds'] != target['bounds']
                or value['self_exclusion_verified'] is not True
                or value['capture_method'] not in {'WINDOWS_GRAPHICS_CAPTURE', 'QUALIFIED_PRINT_WINDOW',
                    'QUALIFIED_DISPLAY_CAPTURE', 'QUALIFIED_EXACT_FRAME'}):
            raise CuaInputError('EXACT_FRAME_PIXEL_SOURCE_PROOF_REQUIRED')
        value['evidence_ref'] = surface_reference(value['evidence_ref'])
        return value

    def open_session(self, binding):
        binding = self._binding(binding)
        if self.closed:
            raise CuaInputError('ADAPTER_CLOSED')
        async def run():
            if self.closed:
                raise CuaInputError('ADAPTER_CLOSED')
            if any(r['binding']['session_label'] == binding['session_label'] for r in self.records.values()):
                raise CuaInputError('NATIVE_SESSION_LABEL_ALREADY_USED_NO_LIFETIME_ADOPTION')
            native = await self.factory.open(binding)
            identifier = uuid.uuid4().hex
            ref = {'id': identifier, 'revision': 1, 'sha256': _sha({'id': identifier, 'binding': binding})}
            self.records[identifier] = {'ref': ref, 'binding': binding, 'native': native, 'closed': False,
                'fault': False, 'expires_at': self.clock() + binding['budget']['ttl_seconds'], 'actions': 0,
                'runtime_role': binding.get('runtime_role', 'TASK_INPUT')}
            return {'session_ref': ref, 'status': 'SESSION_OPENED_NATIVE_ROUTES_UNQUALIFIED',
                'runtime_role': binding.get('runtime_role', 'TASK_INPUT'), 'surface_ref': binding['surface_ref'],
                'workspace_session_ref': binding.get('workspace_session_ref'),
                'observation_run_ref': binding.get('observation_run_ref'),
                'binding_sha256': _sha(binding), 'live_input_qualification': 'NOT_RUN_UNTIL_ROOT_GUI_TEST', 'sdk_version': '0.35.2',
                'transport_kind': 'ORIGINAL_CUA_SDK' if isinstance(self.factory, NativeCuaSessionFactory) else 'INJECTED_TEST_PROVIDER'}
        return self.worker.call(run)

    def capture(self, session_ref, target_ref, surface_ref, input_epoch):
        session_ref, target_ref, surface_ref = map(surface_reference, [session_ref, target_ref, surface_ref])
        async def run():
            record = self._record(session_ref)
            if surface_ref != record['binding']['surface_ref']:
                raise CuaInputError('CAPTURE_DIFFERS_FROM_FROZEN_SURFACE')
            with self.observation_guard(surface_ref, input_epoch):
                target = self._target(target_ref, surface_ref, input_epoch)
                arguments = {'include_screenshot': True, 'include_accessibility_tree': True, 'full_output': True,
                    'max_image_dimension': 0, 'timeout_ms': record['binding']['budget']['timeout_ms']}
                if target['kind'] == 'WINDOW':
                    name = 'get_window_state'; arguments.update(pid=target['pid'], window_id=target['window_id'])
                else:
                    if record['runtime_role'] == 'READONLY_OBSERVATION':
                        raise CuaInputError('READONLY_OBSERVATION_WINDOW_ONLY_NO_DISPLAY')
                    if self.frame_validator is None:
                        raise CuaInputError('DESKTOP_FRAME_SELF_EXCLUSION_VALIDATOR_REQUIRED')
                    name = 'get_desktop_state'; arguments = {'max_image_dimension': 0}
                result = await self._bounded(record, record['native'].call_tool(name, json.dumps(arguments)))
                structured = _json_result(result)
                if structured.get('is_error') is not False:
                    raise CuaInputError('SDK_CAPTURE_ERROR_' + str(structured.get('error_code') or 'UNKNOWN'))
                if self._target(target_ref, surface_ref, input_epoch) != target:
                    raise CuaInputError('CAPTURE_TARGET_CHANGED')
                images = structured.get('images')
                if not isinstance(images, list) or len(images) != 1 or images[0].get('mime_type') != 'image/png':
                    raise CuaInputError('ONE_ORIGINAL_PNG_CAPTURE_REQUIRED')
                encoded = images[0].get('data_base64')
                if not isinstance(encoded, str) or len(encoded) > 24 * 1024 * 1024:
                    raise CuaInputError('CAPTURE_BYTE_BUDGET_EXCEEDED')
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except ValueError:
                    raise CuaInputError('INVALID_CAPTURE_BASE64') from None
                if len(raw) > 16 * 1024 * 1024 or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise CuaInputError('INVALID_OR_OVERSIZED_ORIGINAL_PNG')
                from PIL import Image
                with Image.open(BytesIO(raw)) as image:
                    width, height = image.size
                    if min(width, height) < 1 or width * height > 16_000_000:
                        raise CuaInputError('CAPTURE_PIXEL_BUDGET_EXCEEDED')
                    image.verify()
                metadata_json = structured.get('structured_json')
                if (not isinstance(metadata_json, str) or len(metadata_json.encode()) > 2 * 1024 * 1024):
                    raise CuaInputError('STRUCTURED_CAPTURE_METADATA_BUDGET_EXCEEDED')
                details = json.loads(metadata_json)
                if not isinstance(details, dict):
                    raise CuaInputError('STRUCTURED_CAPTURE_METADATA_REQUIRED')
                capture_id = details.get('capture_id')
                if capture_id is not None and (not isinstance(capture_id, str) or not 1 <= len(capture_id) <= 512 or not capture_id.strip()):
                    raise CuaInputError('BOUNDED_ORIGINAL_SDK_CAPTURE_ID_REQUIRED')
                if target['kind'] == 'WINDOW' and (details.get('pid') != target['pid'] or details.get('window_id') != target['window_id']):
                    raise CuaInputError('SDK_CAPTURE_TARGET_METADATA_MISMATCH')
                # The released SDK emits tokens only as top-level elements in
                # one snapshot. Never parse a UI label/value as token metadata.
                snapshot = details.get('snapshot_id')
                elements = details.get('elements', [])
                tokens = set()
                if isinstance(snapshot, str) and 1 <= len(snapshot) <= 512 and isinstance(elements, list) and len(elements) <= 10000:
                    for element in elements:
                        if not isinstance(element, dict): continue
                        token = element.get('element_token'); index = element.get('element_index')
                        if (type(index) is int and index >= 0 and isinstance(token, str)
                                and token == snapshot + ':' + str(index) and len(token) <= 1024
                                and element.get('enabled') is True):
                            tokens.add(token)
                validation = self.frame_validator(raw, _copy(target), _copy(details)) if self.frame_validator else None
                pixel_proof = self._pixel_proof(validation, raw, target)
                if target['kind'] == 'DESKTOP' and pixel_proof is None:
                    raise CuaInputError('DESKTOP_FRAME_SELF_EXCLUSION_NOT_VERIFIED')
                identifier = uuid.uuid4().hex
                artifact = self._write(identifier + '/frame.png', raw)
                metadata_ref = self._write(identifier + '/sdk-capture.json', metadata_json.encode())
                public = {'session_ref': session_ref, 'target_ref': target_ref, 'surface_ref': surface_ref,
                    'input_epoch': input_epoch, 'captured_at': self.clock(), 'image_ref': artifact,
                    'width': width, 'height': height, 'capture_id': capture_id,
                    'snapshot_id': snapshot if tokens else None, 'element_tokens': sorted(tokens),
                    'sdk_metadata_ref': metadata_ref,
                    'status': 'OBSERVATION_NOT_ACCEPTED_ENGINEERING_FACT',
                    'runtime_role': record['runtime_role'],
                    'pixel_source': 'VERIFIED_TARGET_PIXELS' if pixel_proof else 'UNKNOWN',
                    'pixel_source_proof': pixel_proof,
                    'visual_coordinate_input_authorized': record['runtime_role'] == 'TASK_INPUT' and pixel_proof is not None,
                    'source': 'ORIGINAL_CUA_DRIVER_0_35_2' if isinstance(self.factory, NativeCuaSessionFactory) else 'INJECTED_TEST_PROVIDER',
                    'self_exclusion': 'VALIDATED_BY_HOST' if pixel_proof else 'PIXEL_SOURCE_UNKNOWN',
                    'coverage': {'degraded': structured.get('degraded') is True, 'truncated': details.get('truncated') is True,
                        'pixel_source': 'VERIFIED_TARGET_PIXELS' if pixel_proof else 'UNKNOWN', 'whole_page_seen': False}}
                ref = {'id': identifier, 'revision': 1, 'sha256': _sha(public)}
                self.frames[identifier] = {'ref': ref, 'public': public, 'target': target, 'used': False}
                self._write(identifier + '/observation.json', json.dumps(public, ensure_ascii=False, sort_keys=True).encode())
                return {**public, 'frame_ref': ref}
        return self.worker.call(run)

    def act(self, session_ref, target_ref, frame_ref, action, *, lease_ref, surface_ref,
            input_epoch, control_epoch, operation_id):
        session_ref, target_ref, frame_ref, lease_ref, surface_ref = map(surface_reference,
            [session_ref, target_ref, frame_ref, lease_ref, surface_ref])
        operation_id = _identifier(operation_id)
        action = _copy(action, limit=65536)
        if not isinstance(action, dict) or action.get('kind') not in {'CLICK', 'TYPE_TEXT', 'PRESS_KEY'}:
            raise CuaInputError('DECLARED_SUPPORTED_ACTION_REQUIRED')
        claim = {'session_ref': session_ref, 'target_ref': target_ref, 'frame_ref': frame_ref, 'action': action,
            'lease_ref': lease_ref, 'surface_ref': surface_ref, 'input_epoch': input_epoch, 'control_epoch': control_epoch}
        request_sha = _sha(claim)
        async def run():
            key = (session_ref['id'], operation_id)
            if key in self.operations:
                prior = self.operations[key]
                if prior['request_sha256'] != request_sha:
                    raise CuaInputError('ACTION_OPERATION_IDEMPOTENCY_CONFLICT')
                return {**_copy(prior['result']), 'replayed': True}
            record = self._record(session_ref)
            if record['runtime_role'] != 'TASK_INPUT':
                raise CuaInputError('READONLY_OBSERVATION_CANNOT_MUTATE')
            with self.queue_lock:
                if _sha(record['binding']['execution_ref']) in self.revoked:
                    raise CuaInputError('ORIGINAL_EXECUTION_INPUT_QUEUE_REVOKED')
            if surface_ref != record['binding']['surface_ref']:
                raise CuaInputError('ACTION_DIFFERS_FROM_FROZEN_SURFACE')
            # Root control guard itself nests the Root workspace guard, using
            # one RLock. Both cover this entire SDK await on this worker thread.
            with self.control_guard(lease_ref, surface_ref, input_epoch, control_epoch) as control:
                if not isinstance(control, dict) or control.get('execution_ref') != record['binding']['execution_ref']:
                    raise CuaInputError('ACTION_DIFFERS_FROM_FROZEN_ORIGINAL_EXECUTION')
                target = self._target(target_ref, surface_ref, input_epoch)
                frame = self.frames.get(frame_ref['id'])
                if (not frame or frame['ref'] != frame_ref or frame['public']['session_ref'] != session_ref
                        or frame['public']['target_ref'] != target_ref or frame['public']['surface_ref'] != surface_ref
                        or frame['public']['input_epoch'] != input_epoch or frame['target'] != target):
                    raise CuaInputError('EXACT_CURRENT_SESSION_TARGET_FRAME_REQUIRED')
                if frame['used'] or self.clock() - frame['public']['captured_at'] > record['binding']['budget']['max_frame_age_ms'] / 1000:
                    raise CuaInputError('FRAME_CONSUMED_OR_EXPIRED_REOBSERVE_REQUIRED')
                if record.get('input_blocked_unknown'):
                    raise CuaInputError('SDK_ACTION_OUTCOME_UNKNOWN_NO_ADDITIONAL_INPUT_REOBSERVE_REQUIRED')
                path = self.data / frame['public']['image_ref']['path']
                if (not path.is_file() or any(p.is_symlink() for p in (path, *path.parents))
                        or hashlib.sha256(path.read_bytes()).hexdigest() != frame['public']['image_ref']['sha256']):
                    raise CuaInputError('OBSERVATION_FRAME_ARTIFACT_CHANGED')
                if record['actions'] >= record['binding']['budget']['max_actions']:
                    raise CuaInputError('FIXED_NATIVE_ACTION_BUDGET_EXCEEDED')
                native_target = {'kind': 'window', 'pid': target['pid'], 'window_id': target['window_id']} if target['kind'] == 'WINDOW' else {'kind': 'desktop', 'display_id': 'primary'}
                kind = action['kind']
                if kind == 'CLICK':
                    element_click = set(action) == {'kind', 'element_token', 'delivery_mode'}
                    if not element_click and (set(action) != {'kind', 'x', 'y', 'delivery_mode'} or any(type(action[k]) not in (int, float) or not math.isfinite(action[k]) for k in ['x', 'y'])):
                        raise CuaInputError('EXACT_CAPTURE_PIXEL_CLICK_REQUIRED')
                    mode = action['delivery_mode']
                    if mode not in {'background', 'foreground'} or (mode == 'foreground' and not record['binding']['budget']['allow_foreground']):
                        raise CuaInputError('EXPLICIT_FROZEN_FOREGROUND_PERMISSION_REQUIRED')
                    if target['kind'] == 'DESKTOP' and mode != 'foreground':
                        raise CuaInputError('SDK_DESKTOP_REQUIRES_EXPLICIT_FOREGROUND_NO_FALLBACK')
                    if element_click:
                        token = action['element_token']
                        if (target['kind'] != 'WINDOW' or not isinstance(token, str)
                                or token not in frame['public'].get('element_tokens', [])):
                            raise CuaInputError('ELEMENT_TOKEN_NOT_FROM_EXACT_ENABLED_SDK_FRAME')
                        metadata_ref = frame['public']['sdk_metadata_ref']
                        metadata_path = self.data / metadata_ref['path']
                        if (not metadata_path.is_file() or any(p.is_symlink() for p in (metadata_path, *metadata_path.parents))
                                or hashlib.sha256(metadata_path.read_bytes()).hexdigest() != metadata_ref['sha256']):
                            raise CuaInputError('ELEMENT_FRAME_METADATA_ARTIFACT_CHANGED')
                        name = 'click'; arguments = {'target': native_target, 'element_token': token, 'delivery_mode': mode}
                    elif not 0 <= action['x'] < frame['public']['width'] or not 0 <= action['y'] < frame['public']['height']:
                        raise CuaInputError('CLICK_OUTSIDE_SOURCE_CAPTURE')
                    if not element_click:
                        capture_id = frame['public']['capture_id']
                        if not isinstance(capture_id, str) or not capture_id:
                            raise CuaInputError('SDK_CAPTURE_ID_REQUIRED_NO_UNBOUND_COORDINATE_FALLBACK')
                        if frame['public']['pixel_source'] != 'VERIFIED_TARGET_PIXELS' or frame['public']['pixel_source_proof'] is None:
                            raise CuaInputError('UNKNOWN_PIXEL_SOURCE_CANNOT_AUTHORIZE_COORDINATE_INPUT')
                        name = 'click'; arguments = {'target': native_target, 'x': action['x'], 'y': action['y'],
                            'capture_id': capture_id, 'delivery_mode': mode}
                elif kind == 'TYPE_TEXT':
                    if set(action) not in ({'kind', 'text'}, {'kind', 'text', 'delivery_mode'}) or not isinstance(action['text'], str) or not 1 <= len(action['text']) <= 16384:
                        raise CuaInputError('BOUNDED_EXACT_TEXT_ACTION_REQUIRED')
                    if target['kind'] != 'WINDOW':
                        raise CuaInputError('DESKTOP_KEYBOARD_ROUTE_NOT_QUALIFIED')
                    name = 'type_text'; arguments = {'target': native_target, 'text': action['text']}
                else:
                    if set(action) not in ({'kind', 'key'}, {'kind', 'key', 'delivery_mode'}) or action['key'] not in {'ENTER', 'TAB', 'ESC', 'BACKSPACE', 'DELETE', 'LEFT', 'RIGHT', 'UP', 'DOWN', 'HOME', 'END'}:
                        raise CuaInputError('DECLARED_SINGLE_KEY_REQUIRED')
                    if target['kind'] != 'WINDOW':
                        raise CuaInputError('DESKTOP_KEYBOARD_ROUTE_NOT_QUALIFIED')
                    name = 'press_key'; arguments = {'target': native_target, 'key': action['key']}
                if kind in {'TYPE_TEXT', 'PRESS_KEY'} and 'delivery_mode' in action:
                    mode = action['delivery_mode']
                    if mode not in {'background', 'foreground'} or (mode == 'foreground' and not record['binding']['budget']['allow_foreground']):
                        raise CuaInputError('EXPLICIT_FROZEN_FOREGROUND_PERMISSION_REQUIRED')
                    arguments['delivery_mode'] = mode
                frame['used'] = True; record['actions'] += 1
                try:
                    result = await self._bounded(record, record['native'].action(name, arguments))
                    actual = _json_result(result)
                    _copy(actual)
                    target_after = self._target(target_ref, surface_ref, input_epoch)
                    effect = (actual.get('action') or {}).get('effect') if isinstance(actual.get('action'), dict) else None
                    status = ('REFUSED_OR_OUTCOME_UNKNOWN_REOBSERVATION_REQUIRED'
                        if effect in {'UNVERIFIABLE', 'REFUSED'} else
                        'RETURNED_REOBSERVATION_REQUIRED' if actual.get('is_error') is False and target_after == target else
                        'REFUSED_OR_OUTCOME_UNKNOWN_REOBSERVATION_REQUIRED')
                    if status != 'RETURNED_REOBSERVATION_REQUIRED' or effect != 'CONFIRMED':
                        record['input_blocked_unknown'] = True
                    raw = json.dumps(actual, ensure_ascii=False, sort_keys=True).encode()
                    artifact = self._write(uuid.uuid4().hex + '/action-result.json', raw)
                    response = {'operation_id': operation_id, 'session_ref': session_ref, 'frame_ref': frame_ref,
                        'status': status, 'raw_artifact_ref': artifact, 'sdk_is_error': actual.get('is_error'),
                        'sdk_error_code': actual.get('error_code'), 'action': actual.get('action'),
                        'sdk_effect': effect, 'goal_verification': 'NOT_VERIFIED',
                        'additional_input_blocked': record.get('input_blocked_unknown') is True,
                        'task_completed': False, 'reobserve_required': True, 'replayed': False}
                except Exception as error:
                    record['fault'] = True
                    record['native'].close()
                    response = {'operation_id': operation_id, 'session_ref': session_ref, 'frame_ref': frame_ref,
                        'status': 'OUTCOME_UNKNOWN_SESSION_REVOKED', 'error_type': type(error).__name__,
                        'task_completed': False, 'reobserve_required': True, 'replayed': False}
                self.operations[key] = {'request_sha256': request_sha, 'result': response}
                return _copy(response)
        record = self.records.get(session_ref['id'])
        if record is None or record['ref'] != session_ref:
            raise CuaInputError('EXACT_CUA_SESSION_UNKNOWN')
        if record['runtime_role'] != 'TASK_INPUT':
            raise CuaInputError('READONLY_OBSERVATION_CANNOT_MUTATE')
        tag = _sha(record['binding']['execution_ref'])
        with self.queue_lock:
            if self.closed:
                raise CuaInputError('ADAPTER_CLOSED')
            if tag in self.revoked:
                raise CuaInputError('ORIGINAL_EXECUTION_INPUT_QUEUE_REVOKED')
            future = self.worker.submit(run, mutation_tag=tag)
        return future.result()

    def revoke_queued(self, lease):
        """Root takeover hook; queued old execution jobs fail before dispatch."""
        if lease is None:
            return {'status': 'DRAINED', 'future_admission_revoked': True, 'discarded_count': 0,
                    'already_sent_os_input_retracted': False}
        value = _copy(lease, limit=65536)
        if not isinstance(value, dict) or not isinstance(value.get('execution_ref'), dict):
            raise CuaInputError('EXACT_REVOKED_CONTROL_LEASE_REQUIRED')
        tag = _sha(value['execution_ref'])
        with self.queue_lock:
            self.revoked.add(tag)
            count = self.worker.revoke_mutations(tag)
        return {'status': 'DRAINED', 'future_admission_revoked': True, 'discarded_count': count,
            'already_sent_os_input_retracted': False}

    def close_session(self, session_ref):
        session_ref = surface_reference(session_ref)
        async def run():
            record = self.records.get(session_ref['id'])
            if record is None or record['ref'] != session_ref:
                raise CuaInputError('EXACT_CUA_SESSION_UNKNOWN')
            if not record['closed']:
                record['native'].close(); record['closed'] = True
            return {'session_ref': session_ref, 'status': 'CLOSED', 'already_sent_os_input_retracted': False}
        return self.worker.call(run)

    def shutdown(self):
        if self.closed:
            return {'status': 'CLOSED' if not self.worker.thread.is_alive() else 'SDK_WORKER_CLOSE_UNKNOWN'}
        self.closed = True
        async def run():
            for record in self.records.values():
                if not record['closed']:
                    record['native'].close(); record['closed'] = True
            await self.factory.shutdown()
        self.worker.call(run)
        return {'status': 'CLOSED' if self.worker.close() else 'SDK_WORKER_CLOSE_UNKNOWN'}
