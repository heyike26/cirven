"""Explicit provider selection for the original OpenCode runtime, without account discovery."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION, binary_hash
from packages.adapters.provider_transport_policy import EnterpriseTransportPolicy, MaterialRef, TransportPolicyError
from packages.domain.store import Conflict
from packages.domain.tasks import digest


REASONING_EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh')


def declared_reasoning_efforts(value):
    """User-declared wire values only; never infer model/provider capability."""
    if (type(value) is not list or len(value) > len(REASONING_EFFORTS) or
            any(type(item) is not str or item not in REASONING_EFFORTS for item in value) or
            len(set(value)) != len(value)):
        raise Conflict('Reasoning effort support must be an explicit unique supported-value list')
    return tuple(item for item in REASONING_EFFORTS if item in value)


def transport_summary(configuration, ca_origin=None):
    """Display reviewed metadata; do not expose private refs, values or PEM."""
    proxy=configuration['proxy'];ca=configuration['ca']
    return {'policy_ref':{'id':configuration['id'],'revision':configuration['revision'],
                         'sha256':digest(configuration)},
            'auth_mode':configuration['auth_mode'],
            'timeout_seconds':configuration['timeout_seconds'],'tls_verification':'SYSTEM_TRUST_PLUS_EXPLICIT_CA',
            'trust_environment':False,'follow_redirects':False,
            'ca':{'kind':ca['kind'],'sha256':ca['source_ref']['sha256'],
                  'byte_length':ca.get('byte_length'),'file_source_ref':ca_origin} if ca else None,
            'proxy':{'url':proxy['url'],'auth_configured':proxy['auth_ref'] is not None} if proxy else None,
            'custom_headers':[{'name':h['name'],'private':h['secret_ref'] is not None} for h in configuration['headers']],
            'model_access':'NOT_VALIDATED'}


@dataclass(frozen=True)
class ProviderProfile:
    profile_id: str
    revision: int
    sha256: str
    executable: Path
    executable_sha256: str
    provider_id: str
    provider_url: str
    model_id: str
    secret_ref: str | None
    transport_kind: str
    api_key: str | None = field(repr=False)
    credential_sha256: str | None = field(repr=False)
    supports_images: bool = False
    transport_policy: EnterpriseTransportPolicy | None = field(default=None,repr=False,compare=False)
    transport_secrets: tuple[str,...] = field(default=(),repr=False,compare=False)
    auth_mode: str = 'API_KEY'
    ca_origin: dict | None = field(default=None,repr=False,compare=False)
    reasoning_efforts: tuple[str,...] = ()

    @property
    def fingerprint(self):
        value={"profile_sha256": self.sha256, "credential_sha256": self.credential_sha256}
        if self.transport_policy is not None:
            try:value['transport_fingerprint']=self.transport_policy.resolve(self.transport_policy.reference).fingerprint
            except TransportPolicyError as error:raise Conflict(error.code) from None
        return digest(value)

    @property
    def reference(self):
        return {"id": self.profile_id, "revision": self.revision, "sha256": self.sha256}

    def public(self):
        result={"profile_ref": self.reference, "provider_id": self.provider_id, "model": self.model_id,
                "protocol": "OPENAI_COMPATIBLE_CHAT_COMPLETIONS", "transport_kind": self.transport_kind,
                "secret_ref": self.secret_ref, "credential_present": bool(self.api_key),
                "cli_sha256": self.executable_sha256, "version": VERSION, "source_commit": COMMIT,
                "commercial_cost": "UNKNOWN", "company_qualification": "NOT_VALIDATED",
                "native_windows": "NOT_VALIDATED", "reasoning_efforts": list(self.reasoning_efforts),
                "reasoning_qualification": "DECLARED_NOT_OBSERVED" if self.reasoning_efforts else "NOT_DECLARED"}
        if self.transport_policy is not None:
            result.update(auth_mode=self.auth_mode,
                transport=transport_summary(self.transport_policy.public_configuration(),self.ca_origin))
        return result


class OpenCodeProviderRegistry:
    """The application supplies versioned configurations and its private secret resolver.

    No environment key, developer login, home directory, global native auth file or
    automatically discovered model is consulted. Configurations are JSON values;
    persistence and UI ownership stay with the caller.
    """

    def __init__(self, configuration_resolver, secret_resolver, ca_origin_resolver=None, account_resolver=None,
                 runtime_path_resolver=None):
        self.configuration_resolver = configuration_resolver
        self.secret_resolver = secret_resolver
        self.ca_origin_resolver=ca_origin_resolver or getattr(getattr(configuration_resolver,'__self__',None),'check_ca_origin',None)
        self.account_resolver=account_resolver or getattr(getattr(configuration_resolver,'__self__',None),'subscription_binding',None)
        self.runtime_path_resolver=runtime_path_resolver or getattr(
            getattr(configuration_resolver,'__self__',None),'resolve_runtime_path',None)

    def resolve(self, reference):
        return self._resolve(reference,verify_executable=True)

    def resolve_metadata(self,reference):
        """Recheck configured credentials/profile for an already verified live CLI."""
        return self._resolve(reference,verify_executable=False)

    def current_fingerprint(self,reference):
        """Recheck exact config/materials without rereading the large CLI binary."""
        return self._resolve(reference,verify_executable=False).fingerprint

    configuration_fingerprint=current_fingerprint

    def _resolve(self, reference, *, verify_executable):
        if (not isinstance(reference, dict) or set(reference) != {"id", "revision", "sha256"} or
                not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(reference["id"])) or
                type(reference["revision"]) is not int or reference["revision"] < 1 or
                not re.fullmatch(r"[0-9a-f]{64}", str(reference["sha256"]))):
            raise Conflict("An exact provider profile id, revision and SHA256 are required")
        value = self.configuration_resolver(reference["id"], reference["revision"])
        if not isinstance(value, dict): raise Conflict("Selected provider configuration is unavailable")
        # Reject NaN and credential fields instead of silently inheriting an API key.
        try: json.dumps(value, allow_nan=False)
        except (TypeError, ValueError): raise Conflict("Provider configuration is not finite JSON") from None
        if value.get('schema')=='eeas.opencode-subscription-provider.v1':
            from packages.adapters.opencode_account import resolve_subscription_profile
            return resolve_subscription_profile(value,reference,self.account_resolver,verify_executable=verify_executable,
                runtime_path_resolver=self.runtime_path_resolver)
        keys = {"schema", "id", "revision", "version", "source_commit", "executable", "executable_sha256",
                "provider_id", "protocol", "provider_url", "model_id", "secret_ref", "transport_kind"}
        modern=value.get('schema')=='eeas.opencode-provider.v2'
        if modern:keys=keys|{'auth_mode','transport_policy','transport_policy_ref','ca_origin'}
        if (not keys <= set(value) or set(value) - keys - {'supports_images','reasoning_efforts'} or
                value["schema"] not in {"eeas.opencode-provider.v1","eeas.opencode-provider.v2"}):
            raise Conflict("Provider configuration fields are invalid")
        if type(value.get('supports_images',False)) is not bool:raise Conflict('Image input support must be explicitly declared')
        reasoning_efforts = declared_reasoning_efforts(value.get('reasoning_efforts', []))
        if (value["id"] != reference["id"] or value["revision"] != reference["revision"] or
                digest(value) != reference["sha256"]):
            raise Conflict("Selected provider profile changed")
        if value["version"] != VERSION or value["source_commit"] != COMMIT:
            raise Conflict("Provider profile requires the approved original OpenCode version")
        if value["protocol"] != "OPENAI_COMPATIBLE_CHAT_COMPLETIONS":
            raise Conflict("Provider protocol has not been implemented")
        if value["transport_kind"] not in {"MOCK_WIRE", "COMPANY_CONFIGURED"}:
            raise Conflict("Provider must explicitly declare Mock or user-configured company transport")
        if not re.fullmatch(r"eeas-[A-Za-z0-9_-]{1,80}", str(value["provider_id"])):
            raise Conflict("Custom provider identity is invalid")
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", str(value["model_id"])):
            raise Conflict("Explicit model identity is invalid")
        try:
            endpoint = urlsplit(value["provider_url"])
            if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username or
                    endpoint.password or endpoint.query or endpoint.fragment or
                    not endpoint.path.endswith("/v1") or any(ord(c) <= 32 for c in value["provider_url"])):
                raise ValueError()
            if value["transport_kind"] == "MOCK_WIRE" and not (
                    endpoint.scheme == "http" and endpoint.hostname == "127.0.0.1" and endpoint.port):
                raise ValueError()
        except (TypeError, ValueError): raise Conflict("Selected provider endpoint is invalid") from None
        path = Path(self.runtime_path_resolver(value['executable']) if callable(self.runtime_path_resolver)
                    else value['executable'])
        import os
        approved = CLI_SHA.get(os.name)
        if (not path.is_absolute() or ".." in path.parts or path.is_symlink() or
                any(p.is_symlink() for p in path.parents) or not path.is_file() or
                value["executable_sha256"] != approved or (verify_executable and binary_hash(path) != approved)):
            raise Conflict("Original CLI path or bytes differ from the approved source manifest")
        secret_ref = value["secret_ref"]
        auth_mode=value.get('auth_mode','API_KEY')
        if auth_mode not in {'API_KEY','NONE'} or (auth_mode=='NONE' and secret_ref is not None):
            raise Conflict('Explicit provider auth mode is invalid')
        key=None;policy=None;transport_secrets=()
        if auth_mode=='API_KEY':
            if not isinstance(secret_ref, str) or not re.fullmatch(r"secret:[A-Za-z0-9_.-]{1,100}", secret_ref):
                raise Conflict("An explicit private secret reference is required")
            try:key = self.secret_resolver(secret_ref)
            except Exception:raise Conflict('Selected provider credential is unavailable') from None
            if not isinstance(key, str) or not 1 <= len(key) <= 8192 or any(ord(c) < 32 for c in key):
                raise Conflict("Selected provider credential is unavailable")
        if modern:
            def read_material(ref):
                if not re.fullmatch(r'secret:[a-z0-9-]{1,100}',ref.id) or ref.revision!=1:
                    raise TransportPolicyError('INVALID_PRIVATE_MATERIAL_REF')
                text=self.secret_resolver(ref.id)
                if not isinstance(text,str):raise TransportPolicyError('INVALID_PRIVATE_MATERIAL')
                return text.encode('utf-8')
            try:
                policy_ref=MaterialRef.from_public(value['transport_policy_ref'])
                origin=value['ca_origin']
                if origin is not None and not callable(self.ca_origin_resolver):
                    raise TransportPolicyError('CA_ORIGIN_REVALIDATION_UNAVAILABLE')
                policy=EnterpriseTransportPolicy.from_public_configuration(value['transport_policy'],
                    expected_ref=policy_ref,read_secret=read_material,read_ca_bytes=read_material,
                    validate_ca_file=(lambda descriptor:self.ca_origin_resolver(origin,descriptor)) if origin else None)
                if policy.auth_mode!=auth_mode or (policy.api_key_ref.id if policy.api_key_ref else None)!=secret_ref:
                    raise TransportPolicyError('TRANSPORT_PROFILE_AUTH_MISMATCH')
                private=[]
                if key is not None:private.append(key)
                for header in policy.headers:
                    if header.secret_ref:private.append(read_material(header.secret_ref).decode('utf-8'))
                if policy.proxy and policy.proxy.auth_ref:
                    raw=read_material(policy.proxy.auth_ref).decode('utf-8');private.append(raw)
                    auth=json.loads(raw);private.extend((auth['username'],auth['password']))
                transport_secrets=tuple(dict.fromkeys(private))
                if origin is not None:
                    from packages.domain.local_workspaces import FileSource
                    ref=FileSource.model_validate(origin)
                    if policy.ca is None or policy.ca.public()['kind']!='PEM_FILE' or ref.path!=policy.ca.relative_path or ref.sha256!=policy.ca.source_ref.sha256:
                        raise TransportPolicyError('CA_ORIGIN_REF_MISMATCH')
                    if not callable(self.ca_origin_resolver):raise TransportPolicyError('CA_ORIGIN_REVALIDATION_UNAVAILABLE')
                    self.ca_origin_resolver(origin,policy.ca.public())
            except TransportPolicyError as error:raise Conflict(error.code) from None
            except (ValueError,TypeError,KeyError):raise Conflict('Provider transport configuration is invalid') from None
        return ProviderProfile(value["id"], value["revision"], reference["sha256"], path, approved,
            value["provider_id"], value["provider_url"], value["model_id"], secret_ref,
            value["transport_kind"], key, digest(key) if key is not None else None,value.get('supports_images',False),
            policy,transport_secrets,auth_mode,value.get('ca_origin'),reasoning_efforts)
