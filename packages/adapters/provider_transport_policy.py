"""Explicit, immutable HTTP transport admission for enterprise model gateways.

This module owns no provider registry, model loop, retries or environment secrets.
The caller persists the public configuration and supplies its existing private
material reader.  An exact policy reference and all selected materials are checked
again before *each* request, including requests through an already open client.
"""

from __future__ import annotations

from contextlib import contextmanager, asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json
import math
import ntpath
import os
from pathlib import Path, PurePosixPath
import re
import ssl
import stat
from typing import Callable, Iterator, Literal, Mapping
from urllib.parse import urlsplit

import httpx


MAX_CA_BYTES = 128 * 1024
MAX_SECRET_BYTES = 32 * 1024
MAX_HEADERS = 32
MAX_HEADER_VALUE_BYTES = 8192
MAX_TIMEOUT_SECONDS = 120.0
_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_BLOCKED_HEADERS = frozenset({
    "host", "connection", "content-length", "contentlength", "transfer-encoding",
    "transferencoding", "origin", "te", "trailer", "upgrade", "expect",
    "proxy-connection", "proxy-authorization", "content-encoding", "content-type",
})
_SENSITIVE_HEADERS = frozenset({
    "authorization", "cookie", "set-cookie", "api-key", "apikey", "x-api-key",
    "x-auth-token", "x-access-token", "x-csrf-token",
})


class TransportPolicyError(ValueError):
    """A value-free error code, safe to place in a public receipt."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise TransportPolicyError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class MaterialRef:
    id: str
    revision: int
    sha256: str

    def __post_init__(self) -> None:
        _require(isinstance(self.id, str) and 0 < len(self.id) <= 160 and
                 all(ord(c) >= 32 and ord(c) != 127 for c in self.id), "INVALID_REF_ID")
        _require(type(self.revision) is int and self.revision > 0, "INVALID_REF_REVISION")
        _require(isinstance(self.sha256, str) and bool(_SHA.fullmatch(self.sha256)),
                 "INVALID_REF_SHA256")

    def public(self) -> dict:
        return {"id": self.id, "revision": self.revision, "sha256": self.sha256}

    @classmethod
    def from_public(cls, value: Mapping) -> MaterialRef:
        _require(isinstance(value, Mapping) and set(value) == {"id", "revision", "sha256"},
                 "INVALID_REF_FIELDS")
        return cls(value["id"], value["revision"], value["sha256"])


def _header_name(name: str) -> str:
    _require(isinstance(name, str) and bool(_TOKEN.fullmatch(name)), "INVALID_HEADER_NAME")
    lowered = name.lower()
    _require(lowered not in _BLOCKED_HEADERS and not lowered.startswith("sec"),
             "FORBIDDEN_HEADER")
    return lowered


def _header_value(value: str) -> str:
    _require(isinstance(value, str) and len(value) > 0 and
             all(32 <= ord(c) <= 126 for c in value) and value == value.strip(" "),
             "INVALID_HEADER_VALUE")
    _require(len(value.encode("ascii")) <= MAX_HEADER_VALUE_BYTES, "HEADER_VALUE_TOO_LARGE")
    return value


def _sensitive(name: str) -> bool:
    # Explicit secret_ref is also supported for enterprise-specific names.
    return (name in _SENSITIVE_HEADERS or any(part in name for part in
            ("token", "secret", "password", "credential", "auth", "key")))


@dataclass(frozen=True)
class HeaderSpec:
    name: str
    public_value: str | None = None
    secret_ref: MaterialRef | None = None

    def __post_init__(self) -> None:
        name = _header_name(self.name)
        object.__setattr__(self, "name", name)
        _require((self.public_value is None) != (self.secret_ref is None),
                 "HEADER_REQUIRES_ONE_VALUE_SOURCE")
        _require(self.secret_ref is None or isinstance(self.secret_ref, MaterialRef),
                 "INVALID_HEADER_SECRET_REF")
        if self.public_value is not None:
            _require(not _sensitive(name), "SENSITIVE_HEADER_REQUIRES_SECRET_REF")
            _header_value(self.public_value)

    def public(self) -> dict:
        return {"name": self.name, "public_value": self.public_value,
                "secret_ref": self.secret_ref.public() if self.secret_ref else None}


def _proxy_url(value: str) -> str:
    _require(isinstance(value, str) and 0 < len(value) <= 2048 and
             all(33 <= ord(c) <= 126 for c in value), "INVALID_PROXY_URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = parsed.hostname
    except ValueError:
        raise TransportPolicyError("INVALID_PROXY_URL") from None
    _require(parsed.scheme in {"http", "https"} and bool(host) and
             parsed.username is None and parsed.password is None and
             parsed.path in {"", "/"} and not parsed.query and not parsed.fragment and
             "%" not in parsed.netloc and "\\" not in parsed.netloc and
             (port is None or 1 <= port <= 65535), "INVALID_PROXY_URL")
    return value.rstrip("/")


@dataclass(frozen=True)
class ProxySpec:
    url: str
    auth_ref: MaterialRef | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", _proxy_url(self.url))
        _require(self.auth_ref is None or isinstance(self.auth_ref, MaterialRef),
                 "INVALID_PROXY_AUTH_REF")

    def public(self) -> dict:
        return {"url": self.url, "auth_ref": self.auth_ref.public() if self.auth_ref else None}


def _pem(pem: bytes) -> bytes:
    _require(type(pem) is bytes and 0 < len(pem) <= MAX_CA_BYTES, "INVALID_CA_SIZE")
    try:
        text = pem.decode("ascii")
    except UnicodeDecodeError:
        raise TransportPolicyError("INVALID_CA_PEM") from None
    # A CA input contains certificates only, never private keys or arbitrary text.
    remainder = re.sub(r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+"
                       r"-----END CERTIFICATE-----", "", text)
    _require(not remainder.strip() and "-----BEGIN CERTIFICATE-----" in text,
             "INVALID_CA_PEM")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        context.load_verify_locations(cadata=text)
    except (ssl.SSLError, ValueError):
        raise TransportPolicyError("INVALID_CA_PEM") from None
    return pem


@dataclass(frozen=True)
class CABytes:
    pem: bytes = field(repr=False)
    source_ref: MaterialRef
    _read_source: Callable[[MaterialRef], bytes] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require(isinstance(self.source_ref, MaterialRef), "INVALID_CA_SOURCE_REF")
        _pem(self.pem)
        _require(_sha(self.pem) == self.source_ref.sha256, "CA_SOURCE_DRIFT")

    def read(self) -> bytes:
        if self._read_source is not None:
            try:
                current = self._read_source(self.source_ref)
            except Exception:
                raise TransportPolicyError("CA_SOURCE_UNAVAILABLE") from None
            _require(type(current) is bytes and _sha(current) == self.source_ref.sha256,
                     "CA_SOURCE_DRIFT")
        return self.pem

    def public(self) -> dict:
        return {"kind": "PEM_BYTES", "source_ref": self.source_ref.public(),
                "byte_length": len(self.pem)}


@dataclass(frozen=True)
class CheckedCAFile:
    root: Path
    relative_path: str
    source_ref: MaterialRef
    _validate_source: Callable[[], None] | None = field(default=None,repr=False,compare=False)

    def __post_init__(self) -> None:
        root = Path(self.root)
        _require(root.is_absolute(), "CA_ROOT_MUST_BE_ABSOLUTE")
        _require(isinstance(self.source_ref, MaterialRef), "INVALID_CA_SOURCE_REF")
        relative = self.relative_path
        _require(isinstance(relative, str) and len(relative) <= 1024 and
                 "\\" not in relative and not ntpath.splitdrive(relative)[0] and
                 not relative.startswith("/") and not any(ord(c) < 32 for c in relative),
                 "INVALID_CA_RELATIVE_PATH")
        parts = relative.split("/")
        _require(bool(relative) and all(p not in {"", ".", ".."} and ":" not in p for p in parts),
                 "INVALID_CA_RELATIVE_PATH")
        object.__setattr__(self, "root", root)

    def read(self) -> bytes:
        """Open only the selected regular file; POSIX uses anchored no-follow handles."""
        descriptors: list[int] = []
        try:
            if self._validate_source is not None:
                try:self._validate_source()
                except Exception:raise TransportPolicyError('CA_SOURCE_DRIFT') from None
            _require(self.root.resolve(strict=True) == self.root and self.root.is_dir(),
                     "CA_ROOT_LINK_OR_MISSING")
            path = self.root
            for part in PurePosixPath(self.relative_path).parts:
                path = path / part
                _require(not stat.S_ISLNK(path.lstat().st_mode), "CA_FILE_LINK")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            if os.name == "posix" and os.open in os.supports_dir_fd:
                directory = os.open(self.root, flags | os.O_DIRECTORY | os.O_NOFOLLOW)
                descriptors.append(directory)
                parts = PurePosixPath(self.relative_path).parts
                for part in parts[:-1]:
                    directory = os.open(part, flags | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=directory)
                    descriptors.append(directory)
                descriptor = os.open(parts[-1], flags | os.O_NOFOLLOW, dir_fd=directory)
            else:
                descriptor = os.open(path, flags)
            descriptors.append(descriptor)
            before = os.fstat(descriptor)
            _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "CA_NOT_SINGLE_REGULAR_FILE")
            _require(0 < before.st_size <= MAX_CA_BYTES, "INVALID_CA_SIZE")
            with os.fdopen(os.dup(descriptor), "rb") as source:
                data = source.read(MAX_CA_BYTES + 1)
            after = os.fstat(descriptor)
            _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                     (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "CA_SOURCE_DRIFT")
            _require(path.resolve(strict=True) == path and
                     (path.stat().st_dev, path.stat().st_ino) == (after.st_dev, after.st_ino),
                     "CA_SOURCE_DRIFT")
            _require(_sha(data) == self.source_ref.sha256, "CA_SOURCE_DRIFT")
            if self._validate_source is not None:
                try:self._validate_source()
                except Exception:raise TransportPolicyError('CA_SOURCE_DRIFT') from None
            return _pem(data)
        except OSError:
            raise TransportPolicyError("CA_SOURCE_UNAVAILABLE") from None
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def public(self) -> dict:
        return {"kind": "PEM_FILE", "root": str(self.root), "relative_path": self.relative_path,
                "source_ref": self.source_ref.public()}


def _system_tls_context(ca: bytes | None) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # Explicit OpenSSL defaults, NOT env-selected cafile/capath. HTTPX trust_env=False
    # alone would not suppress SSL_CERT_FILE inside ssl.create_default_context().
    defaults = ssl.get_default_verify_paths()
    cafile = defaults.openssl_cafile
    capath = defaults.openssl_capath
    cafile = cafile if cafile and Path(cafile).is_file() else None
    capath = capath if capath and Path(capath).is_dir() else None
    if cafile or capath:
        context.load_verify_locations(cafile=cafile, capath=capath)
    if os.name == "nt":
        purpose = ssl.Purpose.SERVER_AUTH.oid
        certificates = []
        for store in ("CA", "ROOT"):
            for certificate, encoding, trust in ssl.enum_certificates(store):
                if encoding == "x509_asn" and (trust is True or purpose in trust):
                    certificates.append(ssl.DER_cert_to_PEM_cert(certificate))
        if certificates:
            context.load_verify_locations(cadata="\n".join(certificates))
    if ca is not None:
        context.load_verify_locations(cadata=_pem(ca).decode("ascii"))
    _require(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname,
             "TLS_VERIFICATION_REQUIRED")
    return context


@dataclass(frozen=True)
class ResolvedTransportMaterial:
    """Private runtime material. Do not serialize; public facts are hashes only."""

    fingerprint: str
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    proxy_auth: tuple[str, str] | None = field(repr=False)
    ca_pem: bytes | None = field(repr=False)


SecretReader = Callable[[MaterialRef], bytes]


@dataclass(frozen=True)
class EnterpriseTransportPolicy:
    policy_id: str
    revision: int
    auth_mode: Literal["API_KEY", "NONE"]
    api_key_ref: MaterialRef | None = None
    headers: tuple[HeaderSpec, ...] = ()
    ca: CABytes | CheckedCAFile | None = field(default=None, repr=False)
    proxy: ProxySpec | None = None
    timeout_seconds: float = 60.0
    _read_secret: SecretReader | None = field(default=None, repr=False, compare=False)

    @classmethod
    def admit(cls, *, policy_id: str, revision: int, auth_mode: Literal["API_KEY", "NONE"],
              api_key_ref: MaterialRef | None = None, headers: tuple[HeaderSpec, ...] = (),
              ca: CABytes | CheckedCAFile | None = None, proxy: ProxySpec | None = None,
              timeout_seconds: float = 60.0, read_secret: SecretReader | None = None
              ) -> EnterpriseTransportPolicy:
        return cls(policy_id, revision, auth_mode, api_key_ref, headers, ca, proxy,
                   timeout_seconds, read_secret)

    def __post_init__(self) -> None:
        MaterialRef(self.policy_id, self.revision, "0" * 64)
        _require(self.auth_mode in {"API_KEY", "NONE"}, "INVALID_AUTH_MODE")
        _require((self.auth_mode == "API_KEY") == (self.api_key_ref is not None),
                 "AUTH_MODE_KEY_MISMATCH")
        _require(self.api_key_ref is None or isinstance(self.api_key_ref, MaterialRef),
                 "INVALID_API_KEY_REF")
        _require(isinstance(self.headers, (list, tuple)) and len(self.headers) <= MAX_HEADERS and
                 all(isinstance(h, HeaderSpec) for h in self.headers), "INVALID_HEADERS")
        object.__setattr__(self, "headers", tuple(sorted(self.headers, key=lambda h: h.name)))
        names = [h.name for h in self.headers]
        _require(len(names) == len(set(names)), "DUPLICATE_HEADER")
        _require(self.auth_mode != "API_KEY" or "authorization" not in names,
                 "AUTHORIZATION_COLLISION")
        _require(self.ca is None or isinstance(self.ca, (CABytes, CheckedCAFile)), "INVALID_CA_SOURCE")
        _require(self.proxy is None or isinstance(self.proxy, ProxySpec), "INVALID_PROXY")
        _require(type(self.timeout_seconds) in (float, int) and math.isfinite(self.timeout_seconds) and
                 0.1 <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS, "INVALID_TIMEOUT")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        # Admission is an actual read/hash/TLS parse, not a saved/probed == loaded claim.
        self.resolve()

    def public_configuration(self) -> dict:
        return {"schema": 1, "id": self.policy_id, "revision": self.revision,
                "auth_mode": self.auth_mode,
                "api_key_ref": self.api_key_ref.public() if self.api_key_ref else None,
                "headers": [h.public() for h in self.headers],
                "ca": self.ca.public() if self.ca else None,
                "proxy": self.proxy.public() if self.proxy else None,
                "timeout_seconds": self.timeout_seconds,
                "tls_verification": "SYSTEM_TRUST_PLUS_EXPLICIT_CA",
                "trust_environment": False, "follow_redirects": False}

    @property
    def reference(self) -> MaterialRef:
        return MaterialRef(self.policy_id, self.revision, _sha(_canonical(self.public_configuration())))

    def _secret(self, ref: MaterialRef) -> bytes:
        _require(callable(self._read_secret), "SECRET_READER_REQUIRED")
        try:
            data = self._read_secret(ref)
        except Exception:
            raise TransportPolicyError("SECRET_SOURCE_UNAVAILABLE") from None
        _require(type(data) is bytes and 0 < len(data) <= MAX_SECRET_BYTES, "INVALID_SECRET_SIZE")
        _require(_sha(data) == ref.sha256, "SECRET_SOURCE_DRIFT")
        return data

    def resolve(self, expected_ref: MaterialRef | None = None) -> ResolvedTransportMaterial:
        if expected_ref is not None:
            _require(isinstance(expected_ref, MaterialRef) and self.reference == expected_ref,
                     "POLICY_REF_MISMATCH")
        headers: list[tuple[str, str]] = []
        private_hashes: list[dict] = []

        def text_secret(ref: MaterialRef) -> str:
            data = self._secret(ref)
            private_hashes.append({"ref": ref.public(), "sha256": _sha(data)})
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                raise TransportPolicyError("INVALID_SECRET_ENCODING") from None

        if self.api_key_ref:
            headers.append(("authorization", "Bearer " + _header_value(text_secret(self.api_key_ref))))
        for header in self.headers:
            value = text_secret(header.secret_ref) if header.secret_ref else header.public_value
            headers.append((header.name, _header_value(value)))
        proxy_auth = None
        if self.proxy and self.proxy.auth_ref:
            def unique_fields(pairs):
                result = {}
                for key, value in pairs:
                    _require(key not in result, "INVALID_PROXY_AUTH")
                    result[key] = value
                return result

            try:
                auth = json.loads(text_secret(self.proxy.auth_ref), object_pairs_hook=unique_fields)
            except json.JSONDecodeError:
                raise TransportPolicyError("INVALID_PROXY_AUTH") from None
            _require(isinstance(auth, dict) and set(auth) == {"username", "password"},
                     "INVALID_PROXY_AUTH")
            username, password = auth["username"], auth["password"]
            _header_value(username)
            _header_value(password)
            _require(":" not in username, "INVALID_PROXY_AUTH")
            proxy_auth = (username, password)
        ca_pem = self.ca.read() if self.ca else None
        if ca_pem is not None:
            private_hashes.append({"ca_ref": self.ca.source_ref.public(), "sha256": _sha(ca_pem)})
        fingerprint = _sha(_canonical({"policy_ref": self.reference.public(), "materials": private_hashes}))
        return ResolvedTransportMaterial(fingerprint, tuple(headers), proxy_auth, ca_pem)

    @classmethod
    def from_public_configuration(cls, value: Mapping, *, expected_ref: MaterialRef,
                                  read_secret: SecretReader | None = None,
                                  read_ca_bytes: SecretReader | None = None,
                                  validate_ca_file: Callable[[Mapping], None] | None = None) -> EnterpriseTransportPolicy:
        """Restore exact reviewed configuration, never infer credentials from the host."""
        fields = {"schema", "id", "revision", "auth_mode", "api_key_ref", "headers", "ca", "proxy",
                  "timeout_seconds", "tls_verification", "trust_environment", "follow_redirects"}
        _require(isinstance(value, Mapping) and set(value) == fields and value["schema"] == 1 and
                 value["tls_verification"] == "SYSTEM_TRUST_PLUS_EXPLICIT_CA" and
                 value["trust_environment"] is False and value["follow_redirects"] is False,
                 "INVALID_POLICY_CONFIGURATION")
        _require(MaterialRef(value["id"], value["revision"], _sha(_canonical(value))) == expected_ref,
                 "POLICY_REF_MISMATCH")
        headers = []
        _require(isinstance(value["headers"], list), "INVALID_HEADERS")
        for header in value["headers"]:
            _require(isinstance(header, Mapping) and set(header) == {"name", "public_value", "secret_ref"},
                     "INVALID_HEADER_FIELDS")
            headers.append(HeaderSpec(header["name"], header["public_value"],
                                      MaterialRef.from_public(header["secret_ref"]) if header["secret_ref"] else None))
        ca = None
        if value["ca"] is not None:
            source = value["ca"]
            _require(isinstance(source, Mapping), "INVALID_CA_SOURCE")
            if source.get("kind") == "PEM_BYTES":
                _require(set(source) == {"kind", "source_ref", "byte_length"} and callable(read_ca_bytes),
                         "CA_BYTES_READER_REQUIRED")
                ref = MaterialRef.from_public(source["source_ref"])
                try:
                    data = read_ca_bytes(ref)
                except Exception:
                    raise TransportPolicyError("CA_SOURCE_UNAVAILABLE") from None
                _require(type(data) is bytes and len(data) == source["byte_length"], "CA_SOURCE_DRIFT")
                ca = CABytes(data, ref, read_ca_bytes)
            elif source.get("kind") == "PEM_FILE":
                _require(set(source) == {"kind", "root", "relative_path", "source_ref"}, "INVALID_CA_FIELDS")
                ca_descriptor = dict(source)
                ca = CheckedCAFile(Path(source["root"]), source["relative_path"],
                                   MaterialRef.from_public(source["source_ref"]),
                                   (lambda descriptor=ca_descriptor:validate_ca_file(descriptor)) if validate_ca_file else None)
            else:
                raise TransportPolicyError("INVALID_CA_SOURCE")
        proxy = None
        if value["proxy"] is not None:
            source = value["proxy"]
            _require(isinstance(source, Mapping) and set(source) == {"url", "auth_ref"}, "INVALID_PROXY_FIELDS")
            proxy = ProxySpec(source["url"], MaterialRef.from_public(source["auth_ref"]) if source["auth_ref"] else None)
        policy = cls.admit(policy_id=value["id"], revision=value["revision"], auth_mode=value["auth_mode"],
                           api_key_ref=MaterialRef.from_public(value["api_key_ref"]) if value["api_key_ref"] else None,
                           headers=tuple(headers), ca=ca, proxy=proxy, timeout_seconds=value["timeout_seconds"],
                           read_secret=read_secret)
        policy.resolve(expected_ref)
        return policy

    def open_client(self, expected_ref: MaterialRef, *, timeout_cap_seconds: float | None = None
                    ) -> GuardedTransportClient:
        return GuardedTransportClient(self, expected_ref, timeout_cap_seconds=timeout_cap_seconds)

    def open_async_client(self, expected_ref: MaterialRef, *, timeout_cap_seconds: float | None = None
                          ) -> GuardedAsyncTransportClient:
        return GuardedAsyncTransportClient(self, expected_ref, timeout_cap_seconds=timeout_cap_seconds)


class GuardedTransportClient:
    """A thin synchronous HTTPX lifecycle/dispatch guard, without retry logic."""
    _client_type = httpx.Client

    def __init__(self, policy: EnterpriseTransportPolicy, expected_ref: MaterialRef, *,
                 timeout_cap_seconds: float | None = None) -> None:
        if timeout_cap_seconds is not None:
            _require(type(timeout_cap_seconds) in (int, float) and math.isfinite(timeout_cap_seconds) and
                     timeout_cap_seconds > 0, "INVALID_TIMEOUT_CAP")
        self._effective_timeout_seconds = min(policy.timeout_seconds, timeout_cap_seconds) if timeout_cap_seconds is not None else policy.timeout_seconds
        material = policy.resolve(expected_ref)
        self._policy, self._expected_ref, self._fingerprint = policy, expected_ref, material.fingerprint
        context = _system_tls_context(material.ca_pem)
        proxy = None
        if policy.proxy:
            # HTTP CONNECT still uses client.verify for the target TLS handshake.
            # proxy.ssl_context applies only when the proxy connection itself is TLS.
            proxy_context = context if urlsplit(policy.proxy.url).scheme == "https" else None
            proxy = httpx.Proxy(policy.proxy.url, auth=material.proxy_auth, ssl_context=proxy_context)
        self._client = self._client_type(headers=dict(material.headers), verify=context, proxy=proxy,
                                    trust_env=False, follow_redirects=False,
                                    timeout=httpx.Timeout(self._effective_timeout_seconds))

    @property
    def effective_timeout_seconds(self) -> float:
        return self._effective_timeout_seconds

    def __enter__(self) -> GuardedTransportClient:
        self._client.__enter__()
        return self

    def __exit__(self, *args) -> None:
        self._client.__exit__(*args)

    def close(self) -> None:
        self._client.close()

    def _dispatch(self, url: str | httpx.URL, kwargs: dict) -> None:
        material = self._policy.resolve(self._expected_ref)
        _require(material.fingerprint == self._fingerprint, "RESOLVED_MATERIAL_DRIFT")
        _require(not any(k in kwargs for k in {"auth", "cookies", "verify", "proxy", "timeout",
                                               "follow_redirects", "extensions"}), "REQUEST_POLICY_OVERRIDE")
        try:
            parsed = urlsplit(str(url))
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname and parsed.username is None
                     and parsed.password is None and not parsed.fragment)
        except ValueError:
            valid = False
        _require(bool(valid), "INVALID_REQUEST_URL")
        extra = kwargs.get("headers")
        if extra is not None:
            _require(isinstance(extra, Mapping) and len(extra) <= MAX_HEADERS, "INVALID_HEADERS")
            managed = {name for name, _ in material.headers}
            seen = set()
            for name, value in extra.items():
                lowered = _header_name(name)
                _require(lowered not in managed and lowered not in seen and not _sensitive(lowered),
                         "REQUEST_HEADER_OVERRIDE")
                _header_value(value)
                seen.add(lowered)
        # A server-set cookie is not a newly granted private credential source.
        self._client.cookies.clear()

    def request(self, method: str, url: str | httpx.URL, **kwargs) -> httpx.Response:
        self._dispatch(url, kwargs)
        try:
            return self._client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise TransportPolicyError("TRANSPORT_" + type(error).__name__.upper()) from None

    @contextmanager
    def stream(self, method: str, url: str | httpx.URL, **kwargs) -> Iterator[httpx.Response]:
        self._dispatch(url, kwargs)
        try:
            with self._client.stream(method, url, **kwargs) as response:
                yield response
        except httpx.HTTPError as error:
            raise TransportPolicyError("TRANSPORT_" + type(error).__name__.upper()) from None


class GuardedAsyncTransportClient(GuardedTransportClient):
    """Same admission/dispatch policy, with cancellable HTTPX socket reads."""
    _client_type = httpx.AsyncClient

    def __enter__(self):
        raise TypeError('Use async with for an async model transport')

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args):
        await self._client.__aexit__(*args)

    async def aclose(self):
        await self._client.aclose()

    async def close(self):
        await self.aclose()

    async def request(self, method, url, **kwargs):
        self._dispatch(url, kwargs)
        try:
            return await self._client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise TransportPolicyError("TRANSPORT_" + type(error).__name__.upper()) from None

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        self._dispatch(url, kwargs)
        try:
            async with self._client.stream(method, url, **kwargs) as response:
                yield response
        except httpx.HTTPError as error:
            raise TransportPolicyError("TRANSPORT_" + type(error).__name__.upper()) from None

