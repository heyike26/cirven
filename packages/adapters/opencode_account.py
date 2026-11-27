"""Application-owned ChatGPT subscription account; original OpenCode owns OAuth.

No token/cache discovery, token parsing, refresh implementation or model loop.
Only XDG_DATA_HOME is shared with subscription execution owners. Concurrent
cross-process native auth writes are not claimed to be serialized here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

import httpx

from packages.adapters.opencode import OpenCodeBridge
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION, binary_hash
from packages.adapters.opencode_subscription import NATIVE_METHODS, PLUGIN_SHA256
from packages.domain.paths import resolve_data_path
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, digest

PROTOCOL = "NATIVE_CODEX_SUBSCRIPTION"
AUTH_MODE = "OAUTH_SUBSCRIPTION"
SCHEMA = "eeas.opencode-subscription-provider.v1"
DESTINATION = "https://chatgpt.com/backend-api/codex"
_ACCOUNT_METADATA_LOCK = threading.RLock()


def authorization_epoch(account_data_home, *, establish=False):
    """Local authorization generation only; never read the native auth store."""
    path=Path(account_data_home).parent/'state.json'
    with _ACCOUNT_METADATA_LOCK:
        state=_read(path) if path.exists() else None
        value=state.get('authorization_epoch') if state else None
        if value is not None and not re.fullmatch('[a-f0-9]{32}',str(value)):
            raise Conflict('Subscription authorization generation metadata changed')
        if value is None and establish:
            value=uuid.uuid4().hex
            state=state or {'status':'SIGNED_OUT','attempt_id':None,'model_access':'NOT_VALIDATED',
                            'models':[],'authentication_observed_at':None}
            _write(path,{**state,'authorization_epoch':value},replace=True)
        return value


def _verification_scope(profile):
    return {'account_ref':profile.account_ref,'profile_ref':profile.reference,
            'model_id':profile.model_id,'authorization_epoch':profile.authorization_epoch}


def read_subscription_access(profile):
    """Latest account-wide access observation, never a credential/entitlement."""
    with _ACCOUNT_METADATA_LOCK:
        if not profile.authorization_epoch or authorization_epoch(profile.account_data_home) != profile.authorization_epoch:
            return None
        path = Path(profile.account_data_home).parent / 'access-observation.json'
        if not path.exists():
            return None
        value = _read(path)
        if value.get('scope') != {'account_ref': profile.account_ref, 'authorization_epoch': profile.authorization_epoch}:
            return None
        if (set(value) != {'schema', 'scope', 'status', 'completed_ms', 'evidence_sha256', 'operation_id'}
                or value['schema'] != 'eeas.subscription-access-observation.v1'
                or value['status'] not in {'AVAILABLE', 'REAUTH_REQUIRED'}
                or type(value['completed_ms']) not in {int, float} or not math.isfinite(value['completed_ms'])
                or value['completed_ms'] <= 0
                or not re.fullmatch('[a-f0-9]{64}', str(value['evidence_sha256']))
                or not re.fullmatch('[A-Za-z0-9_-]{1,100}', str(value['operation_id']))):
            raise Conflict('Subscription access observation changed')
        return value


def _record_subscription_access(profile, evidence, *, available):
    # Called under the metadata lock after the original response is validated.
    if authorization_epoch(profile.account_data_home) != profile.authorization_epoch:
        raise Conflict('Subscription authorization changed after response')
    stamp = max(item['completed'] for item in evidence['assistants'])
    wanted = {'schema': 'eeas.subscription-access-observation.v1',
              'scope': {'account_ref': profile.account_ref, 'authorization_epoch': profile.authorization_epoch},
              'status': 'AVAILABLE' if available else 'REAUTH_REQUIRED', 'completed_ms': stamp,
              'evidence_sha256': digest(evidence), 'operation_id': evidence['source']['operation_id']}
    prior = read_subscription_access(profile)
    # Late callbacks cannot rewind a newer native outcome; a tie stays conservative.
    if prior and (prior['completed_ms'] > stamp or prior == wanted
                  or prior['completed_ms'] == stamp and prior['status'] == 'REAUTH_REQUIRED'):
        return prior
    _write(Path(profile.account_data_home).parent / 'access-observation.json', wanted, replace=True)
    return wanted


def subscription_reauth_required(profile):
    with _ACCOUNT_METADATA_LOCK:
        state = _read(Path(profile.account_data_home).parent / 'state.json')
        if state.get('authorization_epoch') != profile.authorization_epoch:
            raise Conflict('Subscription authorization changed')
        access = read_subscription_access(profile)
        return state.get('reconnect_required') is True or bool(access and access['status'] == 'REAUTH_REQUIRED')


def record_subscription_rejection(profile, source, operation, messages):
    """Persist a correlated original 401; keep secrets and response bodies out."""
    from packages.agent_sessions.outcomes import original_auth_rejection
    from packages.adapters.subscription_runtime import _profile
    _profile(profile)
    proof = original_auth_rejection(operation, messages)
    if proof is None:
        return {'status': 'NOT_RECORDED'}
    keys = {'kind', 'project_id', 'object_ref', 'operation_id', 'request_sha256', 'generation',
            'original_session_id', 'status', 'idle_observed', 'cancel_requested',
            'native_user_messages', 'native_assistant_ids', 'deadline_ms'}
    if (proof['provider_id'] != profile.provider_id or proof['model_id'] != profile.model_id
            or set(source) != keys or source.get('kind') != 'ORIGINAL_SESSION'
            or source.get('operation_id') != operation.get('id')
            or source.get('request_sha256') != operation.get('request_sha256')
            or source.get('status') != 'FAILED' or source.get('idle_observed') is not True
            or source.get('cancel_requested') is not False
            or source.get('original_session_id') != proof['original_session_id']
            or source.get('native_user_messages') != proof['native_user_messages']
            or source.get('native_assistant_ids') != proof['native_assistant_ids']
            or source.get('object_ref') != operation.get('session_ref')
            or source.get('generation') != operation.get('generation')):
        raise Conflict('Subscription rejection does not match its original operation')
    evidence = {'scope': _verification_scope(profile), 'source': source,
                'code': proof['code'], 'http_status': 401,
                'assistants': [{'id': ident, 'completed': proof['completed_ms']} for ident in proof['native_assistant_ids']]}
    with _ACCOUNT_METADATA_LOCK:
        read_account_binding(Path(profile.account_data_home).parent.parent, profile.account_ref)
        observed = _record_subscription_access(profile, evidence, available=False)
    return {'status': 'SAVED', 'model_access': 'NOT_VALIDATED', 'access_status': observed['status']}


def read_subscription_verification(profile):
    """One exact bounded pointer/receipt, no history scan, auth read or RPC."""
    with _ACCOUNT_METADATA_LOCK:
        if not profile.authorization_epoch or authorization_epoch(profile.account_data_home)!=profile.authorization_epoch:
            return None
        root=Path(profile.account_data_home).parent
        read_account_binding(root.parent,profile.account_ref)
        scope=_verification_scope(profile)
        pointer=root/'model-verifications'/digest(scope)/'latest.json'
        if not pointer.exists():return None
        selected=_read(pointer)
        if set(selected)!={'scope','receipt_id','receipt_sha256'} or selected['scope']!=scope:
            raise Conflict('Subscription verification pointer scope changed')
        ident=selected['receipt_id']
        if not re.fullmatch('[a-f0-9]{64}',str(ident)):raise Conflict('Subscription verification receipt identity changed')
        receipt=_read(pointer.parent/'operations'/(ident+'.json'))
        if digest(receipt)!=selected['receipt_sha256'] or receipt.get('scope')!=scope:
            raise Conflict('Subscription verification receipt integrity changed')
        evidence=receipt.get('evidence',{})
        if (receipt.get('schema')!='eeas.subscription-model-verification.v1'
                or any(evidence.get(key)!=value for key,value in scope.items())
                or evidence.get('model_access')!='VERIFIED'
                or evidence.get('evidence_kind')!='ORIGINAL_MODEL_RESPONSE_OBSERVED'):
            raise Conflict('Subscription verification evidence scope changed')
        return receipt


def record_subscription_response(profile, source, messages):
    """Persist only the original adapter's qualified, already-completed response.

    This internal callback is not an API and cannot authorize execution. Failures
    are separate observation failures; callers must retain the original outcome.
    """
    from packages.adapters.subscription_runtime import subscription_response_evidence
    evidence=subscription_response_evidence(profile,source,messages)
    if evidence is None:return {'status':'NOT_RECORDED'}
    with _ACCOUNT_METADATA_LOCK:
        root=Path(profile.account_data_home).parent
        read_account_binding(root.parent,profile.account_ref)
        if authorization_epoch(profile.account_data_home)!=profile.authorization_epoch:
            raise Conflict('Subscription authorization generation changed after dispatch')
        scope=_verification_scope(profile)
        ident=digest({key:source[key] for key in ('kind','project_id','object_ref','operation_id','request_sha256','generation')})
        directory=root/'model-verifications'/digest(scope)
        path=directory/'operations'/(ident+'.json')
        existing=path.exists()
        if existing:
            receipt=_read(path)
            if receipt.get('scope')!=scope or receipt.get('evidence')!=evidence:
                raise Conflict('Subscription verification operation binds another response')
        else:
            receipt={'schema':'eeas.subscription-model-verification.v1','scope':scope,'evidence':evidence,
                     'observed_at':time.time()}
            _write(path,receipt)
        pointer=directory/'latest.json'
        wanted={'scope':scope,'receipt_id':ident,'receipt_sha256':digest(receipt)}
        # A replay repairs a missing pointer but never rewinds a newer observed
        # operation. Exact duplicates do not create more filesystem writes.
        if not pointer.exists() or not existing:
            _write(pointer,wanted,replace=True)
        elif _read(pointer).get('scope')!=scope:
            raise Conflict('Subscription verification pointer scope changed')
        _record_subscription_access(profile, evidence, available=True)
        return {'status':'SAVED','receipt_ref':{'id':ident,'revision':1,'sha256':digest(receipt)},
                'model_access':'VERIFIED','evidence_kind':'ORIGINAL_MODEL_RESPONSE_OBSERVED'}


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise InvalidInput("Subscription identity is invalid")
    return value


def _private_directory(path):
    path = Path(path)
    if not path.is_absolute() or any(_linked(p) for p in (path, *path.parents)) or not path.is_dir():
        raise Conflict("Subscription account directory is unavailable or changed")
    return path


def _linked(path):
    try:
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def _read(path):
    if any(_linked(p) for p in (path, *path.parents)) or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise Conflict("Subscription account metadata is unavailable")
    try:
        record = json.loads(path.read_bytes())
        if set(record) != {"value", "sha256"} or digest(record["value"]) != record["sha256"]:
            raise ValueError()
        return record["value"]
    except (ValueError, TypeError, KeyError):
        raise Conflict("Subscription account metadata integrity failed") from None


def _write(path, value, *, replace=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(_linked(p) for p in (path, *path.parents)):
        raise Conflict("Subscription metadata cannot traverse a symbolic link")
    raw = canonical({"value": value, "sha256": digest(value)}).encode()
    target = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp") if replace else path
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if replace:
            os.replace(target, path)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _restored_account_identity(data_dir, directory, value, reference):
    """Only an explicit restore may bind an unchanged account to a new inode."""
    try:
        data = _private_directory(Path(data_dir).resolve())
        marker = data / 'restore-provenance.json'
        if (_linked(marker) or not marker.is_file() or marker.stat().st_size > 65536
                or marker.stat().st_nlink != 1):
            return False
        provenance = json.loads(marker.read_bytes())
        if (provenance.get('schema_version') != 'eeas.restore-provenance.v1'
                or Path(provenance['target_data_dir']) != data
                or not re.fullmatch('[a-f0-9]{64}', provenance['snapshot_sha256'])
                or provenance['manifest_sha256'] != provenance['snapshot_sha256']):
            return False
        proof = provenance['directory_bindings']['subscription-account/native-data']
        if (set(proof) != {'reference', 'saved_account_data_home', 'saved_directory_identity',
                         'restored_directory_identity'}
                or proof['reference'] != reference
                or proof['saved_account_data_home'] != value['account_data_home']
                or proof['saved_directory_identity'] != value['directory_identity']
                or resolve_data_path(value['account_data_home'], data) != directory):
            return False
        actual = directory.stat()
        return (type(proof['restored_directory_identity']) is list
                and all(type(n) is int for n in proof['restored_directory_identity'])
                and proof['restored_directory_identity'] == [actual.st_dev, actual.st_ino])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, InvalidInput):
        return False


def read_account_binding(data_dir, reference):
    root = resolve_data_path("subscription-account", Path(data_dir))
    value = _read(root / "binding.json")
    expected = {"id": value["id"], "revision": value["revision"], "sha256": digest(value)}
    if reference != expected or value.get("schema") != "eeas.native-subscription-account.v1":
        raise Conflict("Subscription account binding changed")
    if (value.get("version") != VERSION or value.get("source_commit") != COMMIT or
            value.get("plugin_sha256") != PLUGIN_SHA256):
        raise Conflict("Subscription account requires the fixed original runtime")
    directory = _private_directory(root / "native-data")
    actual = directory.stat()
    if (str(directory) != value.get("account_data_home") or
            [actual.st_dev, actual.st_ino] != value.get("directory_identity")):
        if not _restored_account_identity(data_dir, directory, value, reference):
            raise Conflict("Subscription account directory identity changed")
        # Runtime projection only. The saved binding/reference and credentials
        # remain byte-for-byte intact, including their historical identities.
        return {**value, 'account_data_home': str(directory),
                'directory_identity': [actual.st_dev, actual.st_ino]}
    return value


def require_owned_oauth(account_data_home):
    """Inspect only our bound private native store; never return credential values.

    The original runtime still owns every write and refresh. This guard prevents
    this application's dispatch from silently consuming a native API-key entry;
    it is not protection against a concurrently malicious local administrator.
    """
    directory = _private_directory(account_data_home)
    parent = _private_directory(directory / "opencode")
    path = parent / "auth.json"
    if (_linked(path) or not path.is_file() or path.stat().st_size > 1024 * 1024 or
            path.stat().st_nlink != 1):
        raise Conflict("OWNED_SUBSCRIPTION_OAUTH_REQUIRED")
    try:
        before = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
            getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read(1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        final = path.lstat()
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if (len(raw) > 1024 * 1024 or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                len({identity(before), identity(opened), identity(after), identity(final)}) != 1 or
                _linked(path) or _linked(parent) or _linked(directory)):
            raise ValueError()
        value = json.loads(raw)
        auth = value.get("openai") if isinstance(value, dict) else None
        if (set(value) != {"openai"} or not isinstance(auth, dict) or auth.get("type") != "oauth" or
                not isinstance(auth.get("refresh"), str) or not auth["refresh"] or
                not isinstance(auth.get("access"), str) or type(auth.get("expires")) is not int or auth["expires"] < 0):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, OSError):
        raise Conflict("OWNED_SUBSCRIPTION_OAUTH_REQUIRED") from None
    return {"auth_mode": AUTH_MODE, "native_auth_type": "oauth", "api_key_fallback": False}


@dataclass(frozen=True)
class SubscriptionProviderProfile:
    profile_id: str
    revision: int
    sha256: str
    executable: Path
    executable_sha256: str
    model_id: str
    account_ref: dict
    account_data_home: Path = field(repr=False)
    supports_images: bool = False
    provider_id: str = "openai"
    protocol: str = PROTOCOL
    auth_mode: str = AUTH_MODE
    transport_kind: str = "NATIVE_SUBSCRIPTION"
    provider_url: str = DESTINATION
    api_key: None = field(default=None, repr=False)
    credential_sha256: None = field(default=None, repr=False)
    secret_ref: None = None
    transport_policy: None = field(default=None, repr=False)
    transport_secrets: tuple = field(default=(), repr=False)
    authorization_epoch: str | None = field(default=None, repr=False, compare=False)

    @property
    def reference(self):
        return {"id": self.profile_id, "revision": self.revision, "sha256": self.sha256}

    @property
    def fingerprint(self):
        # Native token refresh must not invalidate an immutable execution scope.
        # Keep immutable configuration identity compatible with old manifests;
        # authorization generation is a separate dispatch/response basis.
        return digest({"profile_sha256": self.sha256, "account_ref": self.account_ref})

    def public(self):
        return {"profile_ref": self.reference, "provider_id": self.provider_id, "model": self.model_id,
                "model_id": self.model_id, "provider_url": self.provider_url, "protocol": self.protocol, "auth_mode": self.auth_mode,
                "auth_kind": self.auth_mode, "account_ref": self.account_ref,
                "transport_kind": self.transport_kind, "credential_present": False,
                "version": VERSION, "source_commit": COMMIT, "cli_sha256": self.executable_sha256,
                "supports_images": self.supports_images, "model_access": "NOT_VALIDATED",
                "commercial_cost": "UNKNOWN", "api_key_fallback": False,
                "native_auth_write_serialization": "NOT_QUALIFIED"}


def resolve_subscription_profile(value, reference, account_resolver, *, verify_executable=True,
                                 require_auth=True, runtime_path_resolver=None):
    keys = {"schema", "id", "revision", "version", "source_commit", "executable", "executable_sha256",
            "provider_id", "protocol", "auth_mode", "model_id", "account_ref", "account_data_home", "supports_images"}
    if (set(value) != keys or value["schema"] != SCHEMA or value["protocol"] != PROTOCOL or
            value["auth_mode"] != AUTH_MODE or value["provider_id"] != "openai" or
            value["version"] != VERSION or value["source_commit"] != COMMIT or
            value["id"] != reference["id"] or value["revision"] != reference["revision"] or
            digest(value) != reference["sha256"] or type(value["supports_images"]) is not bool):
        raise Conflict("Subscription provider configuration fields or revision changed")
    if not isinstance(value["model_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value["model_id"]):
        raise Conflict("Subscription model identity is invalid")
    if not callable(account_resolver):
        raise Conflict("Subscription account binding authority is unavailable")
    binding = account_resolver(value["account_ref"])
    directory = _private_directory(Path(binding['account_data_home']))
    if resolve_data_path(value['account_data_home'], directory.parent.parent) != directory:
        raise Conflict("Subscription profile cannot replace its owned auth directory")
    if require_auth: require_owned_oauth(directory)
    if callable(runtime_path_resolver):
        executable = Path(runtime_path_resolver(value['executable']))
    elif (directory.parent.parent / 'restore-provenance.json').exists():
        executable = resolve_data_path(value['executable'], directory.parent.parent)
    else:
        executable = Path(value['executable'])
    expected = CLI_SHA[os.name]
    if (not executable.is_absolute() or any(p.is_symlink() for p in (executable, *executable.parents)) or
            not executable.is_file() or value["executable_sha256"] != expected or
            (verify_executable and binary_hash(executable) != expected)):
        raise Conflict("Original subscription CLI bytes differ from the approved runtime")
    return SubscriptionProviderProfile(value["id"], value["revision"], reference["sha256"], executable,
        expected, value["model_id"], value["account_ref"], directory, value["supports_images"],
        authorization_epoch=authorization_epoch(directory,establish=require_auth))


class OpenCodeAccount:
    """One persistent account data home, one current owned native auth controller."""
    def __init__(self, providers, data_dir, *, startup_timeout=90, login_timeout=300):
        self.providers, self.data_dir = providers, Path(data_dir).absolute()
        self.root = resolve_data_path("subscription-account", self.data_dir)
        self.startup_timeout, self.login_timeout = startup_timeout, login_timeout
        self.active = {}
        self._startup_lock = asyncio.Lock()
        self._login_target = None
        self.closing = False
        self._close_task = None
        self.tasks = {}

    def _state(self):
        path = self.root / "state.json"
        return _read(path) if path.exists() else {"status": "SIGNED_OUT", "attempt_id": None,
            "model_access": "NOT_VALIDATED", "models": [], "authentication_observed_at": None}

    def _save(self, **changes):
        with _ACCOUNT_METADATA_LOCK:
            value = {**self._state(), **changes}
            _write(self.root / "state.json", value, replace=True)
            return value

    def _binding(self):
        path = self.root / "binding.json"
        if path.exists():
            value = _read(path)
            reference = {"id": value["id"], "revision": value["revision"], "sha256": digest(value)}
            return read_account_binding(self.data_dir, reference), reference
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if any(_linked(p) for p in (self.root, *self.root.parents)):
            raise Conflict("Subscription storage cannot traverse a symbolic link")
        directory = self.root / "native-data"
        directory.mkdir(mode=0o700, exist_ok=False)
        stat = directory.stat()
        value = {"schema": "eeas.native-subscription-account.v1", "id": "account-" + uuid.uuid4().hex,
            "revision": 1, "version": VERSION, "source_commit": COMMIT, "plugin_sha256": PLUGIN_SHA256,
            "account_data_home": str(directory), "directory_identity": [stat.st_dev, stat.st_ino]}
        _write(path, value)
        return value, {"id": value["id"], "revision": 1, "sha256": digest(value)}

    async def _native(self, method, path, body=None, *, timeout=10):
        allowed = {("GET", "/provider/auth"), ("GET", "/provider"),
            ("POST", "/provider/openai/oauth/authorize"), ("POST", "/provider/openai/oauth/callback"),
            ("POST", "/instance/dispose")}
        if (method, path) not in allowed:
            raise Conflict("Subscription request is outside the native account scope")
        context = self.active.get("account")
        if not context or not context.get("process") or context["process"].returncode is not None or self.closing:
            raise Conflict("Owned subscription controller is unavailable")
        response = await context["client"].request(method, path, json=body, timeout=timeout)
        if (self.closing or self.active.get("account") is not context or context["process"].returncode is not None):
            raise Conflict("Native subscription response arrived after owner cancellation")
        if response.status_code != 200 or len(response.content) > 2 * 1024 * 1024:
            raise Conflict("Native subscription request was rejected: HTTP " + str(response.status_code))
        return response.json()

    def _prepare_owner(self):
        # The fixed archive and executable are large. Their complete integrity
        # checks, extraction and durable writes must not stall the API loop.
        binding, _reference = self._binding()
        executable = self.providers._cli()
        runtime = self.root / "controllers" / uuid.uuid4().hex
        home = runtime / "home"; (home / "tmp").mkdir(parents=True, mode=0o700)
        cwd = runtime / "project"; cwd.mkdir()
        config = home / "config" / "opencode" / "opencode.json"; config.parent.mkdir(parents=True)
        config.write_text(canonical({"autoupdate": False, "share": "disabled", "snapshot": False,
            "formatter": False, "lsp": False, "plugin": [], "permission": {"*": "deny"},
            "enabled_providers": ["openai"]}), encoding="utf-8"); config.chmod(0o600)
        password = secrets.token_hex(24)
        env = OpenCodeBridge._environment(home, config, password)
        env.update(XDG_DATA_HOME=binding["account_data_home"], OPENCODE_DISABLE_DEFAULT_PLUGINS="false")
        from packages.adapters.opencode_startup_guard import reject_unreviewed_project_origins
        reject_unreviewed_project_origins(cwd)
        return executable, runtime, cwd, env, password

    async def _prepared_owner(self):
        preparation = asyncio.create_task(asyncio.to_thread(self._prepare_owner))
        try:
            return await asyncio.shield(preparation)
        except asyncio.CancelledError:
            # A cancelled HTTP waiter cannot cancel an already writing thread.
            # Settle it before releasing the startup lock or reporting close;
            # never spawn a process from its late result. Repeated cancellation
            # must not leave preparation running behind a second owner.
            while not preparation.done():
                try:
                    await asyncio.shield(preparation)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not preparation.cancelled(): preparation.exception()
            raise

    async def _ensure_owner(self):
        if self.closing:
            raise Conflict("Subscription account owner is closing")
        async with self._startup_lock:
            if self.closing:
                raise Conflict("Subscription account owner is closing")
            context = self.active.get("account")
            if context and context["process"].returncode is None:
                return
            if context:
                await context["client"].aclose()
                for stream in context["logs"]: stream.close()
                self.active.pop("account", None)
            executable, runtime, cwd, env, password = await self._prepared_owner()
            if self.closing:
                raise Conflict("Subscription account owner is closing")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
            logs = [open(runtime / name, "xb") for name in ("native-stdout.bin", "native-stderr.bin")]
            context = {"logs": logs, "runtime": runtime}
            self.active["account"] = context
            try:
                spawn = asyncio.create_task(asyncio.create_subprocess_exec(str(executable), "--pure", "serve",
                    "--hostname", "127.0.0.1", "--port", str(port), cwd=cwd, env=env,
                    stdout=logs[0], stderr=logs[1]))
                try: context["process"] = await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    # Record the actual process before propagating even repeated
                    # waiter cancellation. Otherwise close could see only logs
                    # and report CLOSED while this owned spawn returns later.
                    while not spawn.done():
                        try:
                            await asyncio.shield(spawn)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not spawn.cancelled(): context["process"] = spawn.result()
                    raise
                context["client"] = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                    auth=("eeas", password), params={"directory": str(cwd)}, trust_env=False,
                    follow_redirects=False, timeout=10)
                deadline = time.monotonic() + self.startup_timeout
                while time.monotonic() < deadline:
                    if self.closing or context["process"].returncode is not None:
                        raise Conflict("Subscription controller stopped before readiness")
                    try:
                        response = await context["client"].get("/global/health", timeout=.5)
                        if response.status_code == 200:
                            if response.json() != {"healthy": True, "version": VERSION}:
                                raise Conflict("Subscription original runtime version changed")
                            return
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(.05)
                raise Conflict("Subscription controller readiness timed out")
            except BaseException:
                await self._stop_context(context)
                raise

    @staticmethod
    async def _stop_context(context):
        process = context.get("process")
        stopped = process is None
        if process is not None:
            if process.returncode is None:
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    process.kill()
                    try: await asyncio.wait_for(process.wait(), 5)
                    except asyncio.TimeoutError: return False
                except ProcessLookupError:
                    await process.wait()
            stopped = process.returncode is not None
        if stopped:
            if context.get("client"): await context["client"].aclose()
            for stream in context.get("logs", []): stream.close()
        return stopped

    @staticmethod
    def _catalog(reply):
        if not isinstance(reply, dict) or not isinstance(reply.get("connected"), list):
            raise Conflict("Original provider observation is invalid")
        providers = reply.get("all", [])
        if not isinstance(providers, list): raise Conflict("Original provider catalog is invalid")
        selected = [item for item in providers if item.get("id") == "openai"]
        models = []
        if len(selected) > 1: raise Conflict("Original subscription provider is ambiguous")
        if selected:
            values = selected[0].get("models", {})
            if not isinstance(values, dict) or len(values) > 1000: raise Conflict("Original model catalog exceeds scope")
            for ident, item in sorted(values.items()):
                if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", ident) or not isinstance(item, dict): continue
                name = item.get("name", ident)
                if not isinstance(name, str) or len(name) > 300: name = ident
                capabilities = item.get("capabilities", {})
                supports = capabilities.get("input", {}).get("image", False) is True if isinstance(capabilities, dict) else False
                models.append({"id": ident, "name": name, "supports_images": supports})
        return "openai" in reply["connected"], models

    def _public(self, state=None):
        state = state or self._state()
        # Derived observation never edits immutable provider/save receipts.
        state={**state,'model_access':'NOT_VALIDATED','model_verification':None}
        if state.get('status')=='AUTHENTICATED' and state.get('profile'):
            try:
                public=state['profile'];ref=public['profile_ref']
                configuration=self.providers.configuration(ref['id'],ref['revision'])
                profile=resolve_subscription_profile(configuration,ref,self.providers.subscription_binding,
                    verify_executable=False,require_auth=False,
                    runtime_path_resolver=self.providers.resolve_runtime_path)
                if public['model_id']!=profile.model_id or public['account_ref']!=profile.account_ref:
                    raise Conflict('Selected subscription profile observation changed')
                access=read_subscription_access(profile)
                receipt=read_subscription_verification(profile)
                if state.get('reconnect_required') is True or access and access['status']=='REAUTH_REQUIRED':
                    state.update(phase='REAUTH_REQUIRED')
                elif receipt:
                    state.update(model_access='VERIFIED',model_verification=receipt)
            except (Conflict,InvalidInput,NotFound,OSError,ValueError,KeyError,TypeError):
                state['verification_observation_status']='UNAVAILABLE'
        return {**state, "api_key_fallback": False, "catalog_semantics": "ORIGINAL_CLI_CATALOG_NOT_ENTITLEMENT",
            "native_auth_write_serialization": "NOT_QUALIFIED", "native_windows": "NOT_VALIDATED",
            "automatic_retry": False, "native_status": "CLOSING" if self.closing else
                "RUNNING" if self.active.get("account", {}).get("process") and
                self.active["account"]["process"].returncode is None else "STOPPED"}

    async def get(self):
        if self.closing: return self._public()
        # GET may start a private native observer, never OAuth or inference POST.
        await self._ensure_owner()
        connected, models = self._catalog(await self._native("GET", "/provider"))
        if connected:
            binding, _ref = self._binding()
            require_owned_oauth(Path(binding["account_data_home"]))
        state = self._state()
        if connected and not state.get('authorization_epoch'):
            # Upgrading an existing connected private store establishes only a
            # local generation, never inference or real-world account identity.
            authorization_epoch(Path(binding['account_data_home']),establish=True)
            state=self._state()
        attempt_id = state.get("attempt_id")
        pending = self._attempt(attempt_id) if attempt_id else None
        if pending and pending["status"] in {"STARTING", "WAITING_USER", "COMPLETING"}:
            callback = self.tasks.get(attempt_id)
            live = (self._login_target and self._login_target["attempt_id"] == attempt_id or
                pending["status"] == "COMPLETING" and callback is not None and not callback.done())
            status = "WAITING_USER" if live and pending["deadline"] > time.time() else "UNKNOWN_OUTCOME"
        elif state.get('reconnect_required') and state['status']=='UNKNOWN_OUTCOME':
            status = 'UNKNOWN_OUTCOME'
        elif connected:
            status = "AUTHENTICATED"
        elif state["status"] == "UNKNOWN_OUTCOME": status = "UNKNOWN_OUTCOME"
        else: status = "SIGNED_OUT"
        if status != "WAITING_USER": self._clear_login_target(attempt_id)
        return self._public(self._save(status=status, models=models, phase=pending["status"] if pending else None,
            authentication_observed_at=time.time() if connected else None))

    def _attempt(self, ident):
        _identity(ident)
        path = self.root / "attempts" / (ident + ".json")
        if not path.exists(): raise NotFound("Subscription login attempt has no recorded outcome")
        return _read(path)

    def _save_attempt(self, attempt):
        _write(self.root / "attempts" / (attempt["id"] + ".json"), attempt, replace=True)

    @staticmethod
    def _login_url(reply):
        if not isinstance(reply, dict) or set(reply) != {"url", "method", "instructions"} or reply["method"] != "auto":
            raise Conflict("Original subscription authorization response is invalid")
        url = urlsplit(reply["url"]); query = parse_qs(url.query)
        if (url.scheme != "https" or url.netloc != "auth.openai.com" or url.path != "/oauth/authorize" or url.fragment or
                query.get("client_id") != ["app_EMoamEEZ73f0CkXaXp7hrann"] or
                query.get("redirect_uri") != ["http://localhost:1455/auth/callback"] or
                query.get("response_type") != ["code"] or query.get("code_challenge_method") != ["S256"] or
                query.get("scope") != ["openid profile email offline_access"] or
                any(key in query for key in ("access_token", "refresh_token", "id_token", "id_token_hint")) or
                any(len(query.get(key, [])) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", query[key][0])
                    for key in ("state", "code_challenge"))):
            raise Conflict("Original subscription login destination differs from fixed PKCE source")
        return reply["url"]

    async def login(self, operation_id, method="browser"):
        _identity(operation_id)
        if method != "browser": raise InvalidInput("Only the original browser subscription login is enabled")
        if self.closing: raise Conflict("Subscription account is closing")
        path = self.root / "operations" / (digest(operation_id) + ".json")
        if path.exists():
            record = _read(path)
            if record["request_sha256"] != digest({"method": method}): raise Conflict("Subscription operation input changed")
            if record['attempt_id'] != self._state().get('attempt_id'):
                raise Conflict('Historical subscription login cannot replace the current attempt; inspect account state')
            return self._public()  # A repeated POST cannot restart authorization or reveal a new URL.
        state = self._state()
        if state["status"] in {"WAITING_USER", "UNKNOWN_OUTCOME"}:
            raise Conflict("An account has a pending attempt; inspect it without starting another")
        ident = uuid.uuid4().hex
        _write(path, {"operation_id": operation_id, "request_sha256": digest({"method": method}), "attempt_id": ident})
        attempt = {"id": ident, "operation_id": operation_id, "method": method, "status": "STARTING",
                   "deadline": time.time() + self.login_timeout, "method_index": None, "methods_sha256": None}
        self._save_attempt(attempt); self._save(status="WAITING_USER", attempt_id=ident,
            authorization_epoch=uuid.uuid4().hex,model_access='NOT_VALIDATED',
            reconnect_required=state['status']=='AUTHENTICATED' or state.get('reconnect_required') is True)
        try:
            await self._ensure_owner()
            methods = await self._native("GET", "/provider/auth")
            selected = methods.get("openai", []) if isinstance(methods, dict) else []
            indexes = [index for index, item in enumerate(selected)
                       if item.get("type") == "oauth" and item.get("label") == NATIVE_METHODS["browser"]]
            if len(indexes) != 1: raise Conflict("Fixed native ChatGPT browser authentication method is unavailable")
            attempt.update(method_index=indexes[0], methods_sha256=digest(selected))
            self._save_attempt(attempt)
            if self.closing or self._attempt(ident)["status"] != "STARTING":
                raise Conflict("Subscription authorization was cancelled before dispatch")
            reply = await self._native("POST", "/provider/openai/oauth/authorize", {"method": indexes[0]}, timeout=15)
            url = self._login_url(reply)
            if self.closing or self._attempt(ident)["status"] != "STARTING":
                raise Conflict("Authorization completed after cancellation")
            attempt["status"] = "WAITING_USER"; self._save_attempt(attempt)
            self._login_target = {"attempt_id": ident, "login_url": url}
            return {**self._public(), "login_url": url}
        except BaseException:
            self._clear_login_target(ident)
            if self._attempt(ident)["status"] in {"STARTING", "WAITING_USER"}:
                attempt["status"] = "UNKNOWN_OUTCOME"; self._save_attempt(attempt)
                self._save(status="UNKNOWN_OUTCOME")
            raise

    def _clear_login_target(self, attempt_id):
        if self._login_target and self._login_target["attempt_id"] == attempt_id:
            self._login_target = None

    def login_target(self, attempt_id):
        state = self._state(); attempt = self._attempt(attempt_id)
        callback = self.tasks.get(attempt_id)
        waiting = attempt["status"] == "WAITING_USER"
        completing = attempt["status"] == "COMPLETING" and callback is not None and not callback.done()
        if (self.closing or state.get("attempt_id") != attempt_id or state["status"] != "WAITING_USER" or
                not (waiting or completing) or
                attempt["deadline"] <= time.time() or not self._login_target or
                self._login_target["attempt_id"] != attempt_id):
            self._clear_login_target(attempt_id)
            raise Conflict("Subscription login target is no longer current; no authorization was replayed")
        return {"attempt_id": attempt_id, "status": "WAITING_USER", "login_url": self._login_target["login_url"]}

    async def complete(self, attempt_id):
        attempt = self._attempt(attempt_id)
        if attempt["status"] == "AUTHENTICATED": return await self.get()
        if attempt["status"] == "COMPLETING": return self._public()
        self.login_target(attempt_id)
        attempt["status"] = "COMPLETING"; self._save_attempt(attempt)
        self._save(phase="COMPLETING")
        # Retain only this attempt's in-memory original URL while its one
        # callback is pending, so reopening the browser never reauthorizes.
        # HTTP cancellation cannot cancel/replay the native callback. The owned
        # task is observable and participates in application aggregate shutdown.
        task = asyncio.create_task(self._complete(attempt))
        self.tasks[attempt_id] = task
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return self._public()

    async def _complete(self, attempt):
        attempt_id = attempt["id"]
        try:
            methods = await self._native("GET", "/provider/auth")
            selected = methods.get("openai", []) if isinstance(methods, dict) else []
            if digest(selected) != attempt["methods_sha256"]:
                raise Conflict("Native authentication methods changed during login")
            reply = await self._native("POST", "/provider/openai/oauth/callback", {"method": attempt["method_index"]},
                timeout=max(.01, attempt["deadline"] - time.time()))
            if reply is not True or self.closing or self._attempt(attempt_id)["status"] != "COMPLETING":
                raise Conflict("Native subscription callback did not establish this current attempt")
            binding, _reference = self._binding()
            require_owned_oauth(Path(binding['account_data_home']))
            if self.closing or self._state().get('attempt_id') != attempt_id or self._attempt(attempt_id)['status'] != 'COMPLETING':
                raise Conflict('Subscription completion was cancelled before catalog refresh')
            # The fixed native provider catalog is instance-cached. Reset only
            # our private auth instance after explicit successful completion;
            # account GET/restart never sends this mutation.
            if await self._native('POST', '/instance/dispose') is not True:
                raise Conflict('Original subscription provider catalog was not refreshed')
            connected, models = self._catalog(await self._native('GET', '/provider'))
            if not connected: raise Conflict("Native subscription provider is not connected")
            if self.closing or self._state().get('attempt_id') != attempt_id or self._attempt(attempt_id)['status'] != 'COMPLETING':
                raise Conflict('Subscription completion was cancelled before publication')
            attempt["status"] = "AUTHENTICATED"; self._save_attempt(attempt)
            return self._public(self._save(status='AUTHENTICATED', phase="AUTHENTICATED", models=models,
                authentication_observed_at=time.time(), reconnect_required=False))
        except BaseException:
            if self._attempt(attempt_id)["status"] == "COMPLETING":
                attempt["status"] = "UNKNOWN_OUTCOME"; self._save_attempt(attempt)
                self._save(status="UNKNOWN_OUTCOME", phase="UNKNOWN_OUTCOME")
            raise
        finally:
            self._clear_login_target(attempt_id)

    async def cancel(self, attempt_id):
        attempt = self._attempt(attempt_id)
        if self._state().get("attempt_id") != attempt_id: raise Conflict("Only the current login attempt may be cancelled")
        if attempt["status"] == "AUTHENTICATED": raise Conflict("Cancelling an attempt does not log out an authenticated account")
        if attempt["status"] == "CANCELLED": return self._public()
        attempt["status"] = "CANCEL_REQUESTED"; self._save_attempt(attempt); self._login_target = None
        async with self._startup_lock:
            context = self.active.get("account")
            stopped = await self._stop_context(context) if context else True
            if stopped: self.active.pop("account", None)
        attempt["status"] = "CANCELLED" if stopped else "UNKNOWN_OUTCOME"; self._save_attempt(attempt)
        return self._public(self._save(status="SIGNED_OUT" if stopped else "UNKNOWN_OUTCOME"))

    async def save_profile(self, operation_id, ident, model_id):
        _identity(operation_id); _identity(ident)
        path = self.root / "profile-operations" / (digest(operation_id) + ".json")
        request = {"id": ident, "model_id": model_id}
        if path.exists():
            record = _read(path)
            if record["request_sha256"] != digest(request): raise Conflict("Subscription profile operation input changed")
            inspected = self.profile_operation(operation_id)
            if inspected['status']!='COMPLETED': raise Conflict("Subscription profile operation is incomplete; inspect, do not replay")
            return inspected['result']
        state = await self.get()
        models = {item["id"]: item for item in state["models"]}
        if state["status"] != "AUTHENTICATED" or model_id not in models:
            raise Conflict("Select a model actually listed by the connected original subscription provider")
        # A concurrent identical request may have committed while GET awaited
        # the original native provider. Inspect its exact journal, never save a
        # second revision or overwrite the first operation claim.
        if path.exists():
            record = _read(path)
            if record['request_sha256'] != digest(request): raise Conflict('Subscription profile operation input changed')
            inspected = self.profile_operation(operation_id)
            if inspected['status'] != 'COMPLETED': raise Conflict('Subscription profile operation is incomplete; inspect, do not replay')
            return inspected['result']
        _write(path, {"request_sha256": digest(request), "result": None})
        _binding, reference = self._binding()
        result = self.providers.save_subscription(operation_id, ident, model_id, reference, models[model_id]["supports_images"])
        _write(path, {"request_sha256": digest(request), "result": result}, replace=True)
        self._save(profile=result)
        return result

    def profile_operation(self, operation_id):
        _identity(operation_id)
        path = self.root / 'profile-operations' / (digest(operation_id) + '.json')
        if not path.exists():
            return {'operation_id':operation_id, 'status':'NO_RECORD', 'result':None,
                'execution_outcome':'NOT_PROVEN', 'automatic_retry':False}
        record = _read(path)
        if record.get('result'):
            ref = record['result']['profile_ref']
            actual = self.providers.get(ref['id'],ref['revision'])
            if actual != record['result']: raise Conflict('Subscription profile operation result changed')
            return {'operation_id':operation_id, 'status':'COMPLETED', 'result':actual, 'automatic_retry':False}
        actual = self.providers.subscription_operation(operation_id)
        if actual['status']=='COMPLETED':
            if digest(actual['public_request']) != record['request_sha256']:
                raise Conflict('Subscription account/provider operation binding changed')
            return {'operation_id':operation_id, 'status':'COMPLETED', 'result':actual['result'], 'automatic_retry':False}
        return {'operation_id':operation_id, 'status':'INCOMPLETE', 'result':None, 'automatic_retry':False}

    async def _close(self):
        self.closing = True; self._login_target = None
        async with self._startup_lock:
            outcomes = []
            for ident, context in list(self.active.items()):
                stopped = await self._stop_context(context)
                outcomes.append({"owner_kind": "SUBSCRIPTION_AUTH", "status": "CLOSED" if stopped else "CLOSE_UNKNOWN"})
                if stopped: self.active.pop(ident, None)
            if self.tasks:
                done, pending = await asyncio.wait(list(self.tasks.values()), timeout=2)
                if pending: outcomes.append({"owner_kind": "SUBSCRIPTION_CALLBACK", "status": "CLOSE_UNKNOWN"})
            return {"status": "CLOSE_UNKNOWN" if any(row["status"] != "CLOSED" for row in outcomes) else "CLOSED",
                    "outcomes": outcomes, "credential_revoked": False}

    async def close(self):
        if self._close_task is None:
            self.closing = True
            self._close_task = asyncio.create_task(self._close())
        return await asyncio.shield(self._close_task)
