"""Real HTTPX/HTTP/TLS/proxy qualification; only gateway protocol is a fixture."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import select
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from packages.adapters.provider_transport_policy import (
    CABytes, CheckedCAFile, EnterpriseTransportPolicy, HeaderSpec, MaterialRef,
    MAX_CA_BYTES, ProxySpec, TransportPolicyError,
)


def check(condition, message="qualification check failed"):
    if not condition:
        raise AssertionError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def ref(name, data):
    return MaterialRef(name, 1, digest(data))


def evidence(name, **facts):
    print("EVIDENCE_JSON " + json.dumps({"case": name, **facts}, sort_keys=True))


class Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.do_POST()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.observed.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if self.path == "/slow":
            time.sleep(0.35)
        if self.path == "/redirect":
            self.send_response(307)
            self.send_header("Location", "/target")
            data = b"redirect-not-followed"
        elif self.path == "/sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            data = b'data: {"fixture":true}\n\ndata: [DONE]\n\n'
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            data = b'{"fixture_gateway":true}'
            if self.path == "/set-cookie":
                self.send_header("Set-Cookie", "session=fixture-server-cookie")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ForwardProxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.do_POST()

    def do_POST(self):
        parsed = urlsplit(self.path)
        self.server.observed.append({"method": self.command, "target": self.path,
                                     "auth": self.headers.get("Proxy-Authorization")})
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in {"host", "proxy-authorization", "connection", "content-length"}}
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
        try:
            connection.request(self.command, parsed.path or "/", body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            self.send_response(response.status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            connection.close()

    def do_CONNECT(self):
        self.server.observed.append({"method": "CONNECT", "target": self.path,
                                     "auth": self.headers.get("Proxy-Authorization")})
        host, port = self.path.rsplit(":", 1)
        remote = socket.create_connection((host, int(port)), timeout=2)
        try:
            self.send_response(200, "Connection Established")
            self.end_headers()
            self.wfile.flush()
            sockets = [self.connection, remote]
            while True:
                ready, _, _ = select.select(sockets, [], [], 2)
                if not ready:
                    break
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination = remote if source is self.connection else self.connection
                    destination.sendall(data)
        finally:
            self.close_connection = True
            remote.close()


@contextmanager
def running_server(handler=Gateway, tls=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    server.observed = []
    if tls is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls["leaf_path"], tls["key_path"])
        server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    scheme = "https" if tls is not None else "http"
    host = "localhost" if tls is not None else "127.0.0.1"
    server.url = f"{scheme}://{host}:{server.server_port}"
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        check(not worker.is_alive(), "fixture listener did not close")


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    directory = tmp_path_factory.mktemp("provider-transport-real-tls")
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EEAS original QA CA")])
    now = datetime.now(timezone.utc)
    root = (x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name)
            .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(root_key, hashes.SHA256()))
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(root_name).public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(root_key, hashes.SHA256()))
    root_pem = root.public_bytes(serialization.Encoding.PEM)
    leaf_pem = leaf.public_bytes(serialization.Encoding.PEM)
    (directory / "root.pem").write_bytes(root_pem)
    (directory / "leaf.pem").write_bytes(leaf_pem)
    (directory / "leaf.key").write_bytes(leaf_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (directory / "leaf.key").chmod(0o600)
    return {"root": root_pem, "root_path": directory / "root.pem",
            "leaf_path": directory / "leaf.pem", "key_path": directory / "leaf.key"}


def none(**kwargs):
    return EnterpriseTransportPolicy.admit(policy_id="qa-policy", revision=1, auth_mode="NONE", **kwargs)


def lower_headers(request):
    return {k.lower(): v for k, v in request["headers"].items()}


def test_real_no_auth_api_key_and_sse_are_explicit(monkeypatch):
    fixture_key = b"original-fixture-key-not-company"
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key-must-never-load")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key-must-never-load")
    with running_server() as gateway:
        policy = none()
        with policy.open_client(policy.reference) as client:
            response = client.request("POST", gateway.url + "/none", json={"fixture": True})
            check(response.status_code == 200)
        check("authorization" not in lower_headers(gateway.observed[0]))
        check("cookie" not in lower_headers(gateway.observed[0]))
        check(lower_headers(gateway.observed[0])["content-type"] == "application/json")
        private_ref = ref("fixture-key", fixture_key)
        keyed = EnterpriseTransportPolicy.admit(policy_id="qa-keyed", revision=1, auth_mode="API_KEY",
                                                api_key_ref=private_ref, read_secret=lambda _: fixture_key)
        with keyed.open_client(keyed.reference) as client:
            with client.stream("POST", gateway.url + "/sse", json={"stream": True}) as response:
                check(response.status_code == 200 and b"[DONE]" in response.read())
        check(lower_headers(gateway.observed[1])["authorization"] == "Bearer " + fixture_key.decode())
        evidence("explicit-none-bearer-sse", requests=len(gateway.observed), noauth_absent=True,
                 bearer_sha256=digest(lower_headers(gateway.observed[1])["authorization"].encode()), sse_read=True)


def test_real_custom_auth_header_private_and_public_are_separate():
    fixture_auth = b"Basic ZmFrZS1maXh0dXJlOm9ubHk="
    policy = none(headers=(HeaderSpec("Authorization", secret_ref=ref("custom-auth", fixture_auth)),
                           HeaderSpec("X-Tenant", public_value="original-qa")),
                  read_secret=lambda _: fixture_auth)
    representation = repr(policy) + repr(policy.resolve()) + json.dumps(policy.public_configuration())
    check(fixture_auth.decode() not in representation)
    with running_server() as gateway, policy.open_client(policy.reference) as client:
        response = client.request("POST", gateway.url + "/custom", json={})
        check(response.status_code == 200)
        headers = lower_headers(gateway.observed[0])
        check(headers["authorization"] == fixture_auth.decode() and headers["x-tenant"] == "original-qa")
        evidence("custom-private-auth", requests=1, public_secrets_absent=True,
                 auth_header_sha256=digest(fixture_auth), tenant="original-qa")


def test_ambient_proxy_and_server_cookie_do_not_expand_policy(monkeypatch):
    with running_server() as gateway, running_server(ForwardProxy) as ambient_proxy:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.setenv(name, ambient_proxy.url)
        monkeypatch.setenv("NO_PROXY", "")
        policy = none()
        with policy.open_client(policy.reference) as client:
            check(client.request("GET", gateway.url + "/set-cookie").status_code == 200)
            check(client.request("GET", gateway.url + "/cookie-check").status_code == 200)
        check(len(ambient_proxy.observed) == 0 and len(gateway.observed) == 2)
        check("cookie" not in lower_headers(gateway.observed[1]))
        evidence("no-ambient-proxy-cookie", proxy_requests=0, gateway_requests=2, inherited_cookie=False)


def test_real_explicit_proxy_private_auth_forwarding():
    proxy_secret = b'{"username":"fixture-proxy-user","password":"fixture-proxy-password"}'
    with running_server() as gateway, running_server(ForwardProxy) as proxy:
        policy = none(proxy=ProxySpec(proxy.url, ref("proxy-auth", proxy_secret)),
                      read_secret=lambda _: proxy_secret)
        safe = repr(policy) + repr(policy.resolve()) + json.dumps(policy.public_configuration())
        check("fixture-proxy-user" not in safe and "fixture-proxy-password" not in safe)
        with policy.open_client(policy.reference) as client:
            check(client.request("POST", gateway.url + "/forward", json={"proxy": True}).status_code == 200)
        check(len(proxy.observed) == len(gateway.observed) == 1)
        check(proxy.observed[0]["auth"].startswith("Basic "))
        check("proxy-authorization" not in lower_headers(gateway.observed[0]))
        evidence("explicit-proxy-forward", proxy_requests=1, gateway_requests=1,
                 proxy_auth_sha256=digest(proxy.observed[0]["auth"].encode()), credential_in_public=False)


def test_real_tls_user_ca_plus_hostname_and_default_trust_checks(certificates, monkeypatch):
    ca_ref = ref("tls-root", certificates["root"])
    admitted = none(ca=CABytes(certificates["root"], ca_ref))
    monkeypatch.setenv("SSL_CERT_FILE", str(certificates["root_path"]))
    monkeypatch.setenv("SSL_CERT_DIR", str(certificates["root_path"].parent))
    with running_server(tls=certificates) as gateway:
        with admitted.open_client(admitted.reference) as client:
            check(client.request("POST", gateway.url + "/trusted", json={}).status_code == 200)
            with pytest.raises(TransportPolicyError, match="TRANSPORT_CONNECTERROR"):
                client.request("GET", gateway.url.replace("localhost", "127.0.0.1") + "/wrong-host")
        defaults = none()
        with defaults.open_client(defaults.reference) as client:
            with pytest.raises(TransportPolicyError, match="TRANSPORT_CONNECTERROR"):
                client.request("GET", gateway.url + "/untrusted")
        check(len(gateway.observed) == 1)
        evidence("real-tls-ca-hostname", successful_requests=1, wrong_hostname_rejected=True,
                 ambient_ca_not_trusted=True, ca_sha256=ca_ref.sha256)


def test_real_https_connect_proxy_preserves_tls_verification(certificates):
    proxy_secret = b'{"username":"connect-user","password":"connect-secret"}'
    with running_server(tls=certificates) as gateway, running_server(ForwardProxy) as proxy:
        policy = none(ca=CABytes(certificates["root"], ref("connect-ca", certificates["root"])),
                      proxy=ProxySpec(proxy.url, ref("connect-proxy", proxy_secret)),
                      read_secret=lambda _: proxy_secret)
        with policy.open_client(policy.reference) as client:
            check(client.request("GET", gateway.url + "/tls-via-proxy").status_code == 200)
        untrusted = none(proxy=ProxySpec(proxy.url))
        with untrusted.open_client(untrusted.reference) as client:
            with pytest.raises(TransportPolicyError, match="TRANSPORT_CONNECTERROR"):
                client.request("GET", gateway.url + "/not-verified")
        check(len(gateway.observed) == 1 and len(proxy.observed) == 2)
        check(all(r["method"] == "CONNECT" for r in proxy.observed))
        evidence("real-https-connect", tunnel_requests=2, verified_gateway_requests=1,
                 bad_trust_rejected=True, proxy_auth_sha256=digest(proxy.observed[0]["auth"].encode()))


def test_real_https_proxy_itself_is_verified(certificates):
    with running_server() as gateway, running_server(ForwardProxy, tls=certificates) as proxy:
        policy = none(ca=CABytes(certificates["root"], ref("https-proxy-ca", certificates["root"])),
                      proxy=ProxySpec(proxy.url))
        with policy.open_client(policy.reference) as client:
            check(client.request("POST", gateway.url + "/verified-proxy", json={}).status_code == 200)
        untrusted = none(proxy=ProxySpec(proxy.url))
        with untrusted.open_client(untrusted.reference) as client:
            with pytest.raises(TransportPolicyError, match="TRANSPORT_CONNECTERROR"):
                client.request("GET", gateway.url + "/bad-proxy-trust")
        check(len(proxy.observed) == len(gateway.observed) == 1)
        evidence("real-tls-proxy", verified_proxy_requests=1, gateway_requests=1,
                 untrusted_proxy_rejected=True)


def test_checked_ca_file_rechecked_inside_open_client(certificates, tmp_path):
    ca_path = tmp_path / "公司 CA.pem"
    ca_path.write_bytes(certificates["root"])
    source = CheckedCAFile(tmp_path, ca_path.name, ref("selected-ca-file", certificates["root"]))
    policy = none(ca=source)
    with running_server(tls=certificates) as gateway, policy.open_client(policy.reference) as client:
        check(client.request("GET", gateway.url + "/before-change").status_code == 200)
        changed = certificates["root"] + b"\n"
        ca_path.write_bytes(changed)
        with pytest.raises(TransportPolicyError, match="CA_SOURCE_DRIFT"):
            client.request("GET", gateway.url + "/must-not-dispatch")
        check(len(gateway.observed) == 1 and ca_path.read_bytes() == changed)
        evidence("ca-file-current-source-guard", outgoing_requests=1, changed_file_preserved=True,
                 old_sha256=source.source_ref.sha256, current_sha256=digest(changed))


def test_ca_reader_boundaries_bad_pem_size_hash_link_and_private_key(certificates, tmp_path):
    data = certificates["root"]
    ca_ref = ref("bounded-ca", data)
    for relative in ("../root.pem", "/root.pem", "x/../root.pem", "x//root.pem", "C:/root.pem", "x\\root.pem"):
        with pytest.raises(TransportPolicyError, match="INVALID_CA_RELATIVE_PATH"):
            CheckedCAFile(tmp_path, relative, ca_ref)
    root = tmp_path / "root.pem"
    root.write_bytes(data)
    linked = tmp_path / "link.pem"
    linked.symlink_to(root)
    with pytest.raises(TransportPolicyError, match="CA_FILE_LINK"):
        none(ca=CheckedCAFile(tmp_path, "link.pem", ca_ref))
    hardlinked = tmp_path / "hard.pem"
    hardlinked.hardlink_to(root)
    with pytest.raises(TransportPolicyError, match="CA_NOT_SINGLE_REGULAR_FILE"):
        none(ca=CheckedCAFile(tmp_path, "hard.pem", ca_ref))
    hardlinked.unlink()
    with pytest.raises(TransportPolicyError, match="CA_SOURCE_DRIFT"):
        none(ca=CheckedCAFile(tmp_path, "root.pem", ref("wrong-hash", b"wrong")))
    with pytest.raises(TransportPolicyError, match="INVALID_CA_SIZE"):
        CABytes(b"x" * (MAX_CA_BYTES + 1), ref("too-large", b"x" * (MAX_CA_BYTES + 1)))
    root.write_bytes(b"x" * (MAX_CA_BYTES + 1))
    with pytest.raises(TransportPolicyError, match="INVALID_CA_SIZE"):
        none(ca=CheckedCAFile(tmp_path, "root.pem", ca_ref))
    for bad in (b"not pem", b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----",
                data + certificates["key_path"].read_bytes()):
        with pytest.raises(TransportPolicyError, match="INVALID_CA_PEM"):
            CABytes(bad, ref("bad-pem", bad))
    evidence("ca-bounds-and-material", traversal_rejected=6, symlink_rejected=True,
             hardlink_rejected=True, size_checked=True, sha_checked=True, private_key_rejected=True)


def test_policy_canonical_reference_restart_restore_and_ca_source_drift(certificates):
    fixture_secret = b"fixture-restart-secret"
    ca_value = [certificates["root"]]
    policy = none(ca=CABytes(ca_value[0], ref("restart-ca", ca_value[0])),
                  headers=(HeaderSpec("X-Auth", secret_ref=ref("restart-auth", fixture_secret)),
                           HeaderSpec("X-Tenant", public_value="fixture")),
                  read_secret=lambda _: fixture_secret)
    public = json.loads(json.dumps(policy.public_configuration()))
    restored = EnterpriseTransportPolicy.from_public_configuration(
        public, expected_ref=policy.reference, read_secret=lambda _: fixture_secret,
        read_ca_bytes=lambda _: ca_value[0])
    check(restored.reference == policy.reference and restored.resolve().fingerprint == policy.resolve().fingerprint)
    public["timeout_seconds"] = 30.0
    with pytest.raises(TransportPolicyError, match="POLICY_REF_MISMATCH"):
        EnterpriseTransportPolicy.from_public_configuration(public, expected_ref=policy.reference)
    with running_server(tls=certificates) as gateway, restored.open_client(restored.reference) as client:
        check(client.request("GET", gateway.url + "/restored").status_code == 200)
        ca_value[0] += b"\n"
        with pytest.raises(TransportPolicyError, match="CA_SOURCE_DRIFT"):
            client.request("GET", gateway.url + "/changed-callback")
        check(len(gateway.observed) == 1)
    evidence("exact-policy-restore", canonical_ref=policy.reference.public(), restart_read_checked=True,
             mutation_rejected=True, ca_callback_drift_rejected=True)


def test_secret_source_drift_or_unavailable_never_dispatches():
    current = [b"original-fixture-secret"]
    key_ref = ref("live-source", current[0])
    keyed = EnterpriseTransportPolicy.admit(policy_id="live-key", revision=1, auth_mode="API_KEY",
                                            api_key_ref=key_ref, read_secret=lambda _: current[0])
    with running_server() as gateway, keyed.open_client(keyed.reference) as client:
        check(client.request("GET", gateway.url + "/before").status_code == 200)
        current[0] = b"changed-fixture-secret"
        with pytest.raises(TransportPolicyError, match="SECRET_SOURCE_DRIFT") as error:
            client.request("GET", gateway.url + "/must-not-send")
        check(current[0].decode() not in str(error.value) and len(gateway.observed) == 1)
    def unavailable(_):
        raise ValueError("private-loader-error-must-not-leak")
    with pytest.raises(TransportPolicyError, match="SECRET_SOURCE_UNAVAILABLE") as error:
        EnterpriseTransportPolicy.admit(policy_id="missing", revision=1, auth_mode="API_KEY",
                                        api_key_ref=key_ref, read_secret=unavailable)
    check("private-loader-error" not in str(error.value))
    evidence("secret-version-guard", requests=1, source_drift_rejected=True, private_error_redacted=True)


@pytest.mark.parametrize("name", ["Host", "Connection", "Content-Length", "ContentLength",
                                  "Transfer-Encoding", "TransferEncoding", "Origin", "Sec-Fetch-Site",
                                  "SecAnything", "TE", "Trailer", "Upgrade", "Expect", "Proxy-Connection",
                                  "Proxy-Authorization", "Content-Type", "Content-Encoding"])
def test_wire_framing_headers_are_not_user_overrides(name):
    with pytest.raises(TransportPolicyError, match="FORBIDDEN_HEADER"):
        HeaderSpec(name, public_value="fixture")


def test_no_crlf_literal_auth_duplicate_or_invalid_auth_mode():
    for name in ("Authorization", "X-API-Key", "X-Company-Auth", "Cookie", "X-Access-Token"):
        with pytest.raises(TransportPolicyError, match="SENSITIVE_HEADER_REQUIRES_SECRET_REF"):
            HeaderSpec(name, public_value="must-be-private")
    for name in ("Bad\r\nInjected", "Invalid Name", "Bad:Name"):
        with pytest.raises(TransportPolicyError, match="INVALID_HEADER_NAME"):
            HeaderSpec(name, public_value="fixture")
    for value in ("x\r\nInjected: value", "x\x00", "值", " leading", "trailing ", " "):
        with pytest.raises(TransportPolicyError, match="INVALID_HEADER_VALUE"):
            HeaderSpec("X-Description", public_value=value)
        with pytest.raises(TransportPolicyError, match="INVALID_HEADER_VALUE"):
            none(headers=(HeaderSpec("X-Auth", secret_ref=ref("crlf", value.encode())),),
                 read_secret=lambda _: value.encode())
    with pytest.raises(TransportPolicyError, match="DUPLICATE_HEADER"):
        none(headers=(HeaderSpec("X-Tenant", public_value="one"), HeaderSpec("x-tenant", public_value="two")))
    with pytest.raises(TransportPolicyError, match="AUTH_MODE_KEY_MISMATCH"):
        none(api_key_ref=ref("unwanted-key", b"key"))
    with pytest.raises(TransportPolicyError, match="AUTH_MODE_KEY_MISMATCH"):
        EnterpriseTransportPolicy.admit(policy_id="no-dummy", revision=1, auth_mode="API_KEY")
    secret = b"fixture"
    with pytest.raises(TransportPolicyError, match="AUTHORIZATION_COLLISION"):
        EnterpriseTransportPolicy.admit(policy_id="duplicate-auth", revision=1, auth_mode="API_KEY",
            api_key_ref=ref("key", secret), headers=(HeaderSpec("Authorization", secret_ref=ref("header", secret)),),
            read_secret=lambda _: secret)
    evidence("admission-safety", literal_auth_rejected=5, crlf_rejected=True, duplicate_rejected=True,
             explicit_none_key_rejected=True, missing_key_rejected=True)


def test_proxy_admission_never_puts_credentials_in_public_url():
    for url in ("http://user:password@localhost:8080", "socks5://localhost:8080", "http://localhost/path",
                "http://localhost/?token=secret", "http://localhost/#secret", "http://localhost:99999",
                "http://localhost\r\nHost:else"):
        with pytest.raises(TransportPolicyError, match="INVALID_PROXY_URL") as error:
            ProxySpec(url)
        check("password" not in str(error.value) and "token=secret" not in str(error.value))
    bad = b'{"username":"user:must-not-have-colon","password":"fixture"}'
    with pytest.raises(TransportPolicyError, match="INVALID_PROXY_AUTH"):
        none(proxy=ProxySpec("http://localhost:8080", ref("bad-proxy-auth", bad)), read_secret=lambda _: bad)
    duplicate = b'{"username":"one","username":"two","password":"fixture"}'
    with pytest.raises(TransportPolicyError, match="INVALID_PROXY_AUTH"):
        none(proxy=ProxySpec("http://localhost:8080", ref("duplicate-proxy", duplicate)),
             read_secret=lambda _: duplicate)


def test_real_redirect_timeout_no_retry_and_request_override_guards():
    policy = none(timeout_seconds=0.1)
    with running_server() as gateway, policy.open_client(policy.reference) as client:
        redirect = client.request("GET", gateway.url + "/redirect")
        check(redirect.status_code == 307 and len(gateway.observed) == 1)
        with pytest.raises(TransportPolicyError, match="TRANSPORT_READTIMEOUT"):
            client.request("POST", gateway.url + "/slow", json={})
        check(len(gateway.observed) == 2)
        for kwargs in ({"verify": False}, {"timeout": 999}, {"follow_redirects": True},
                       {"auth": ("user", "secret")}, {"cookies": {"session": "secret"}}, {"extensions": {}}):
            with pytest.raises(TransportPolicyError, match="REQUEST_POLICY_OVERRIDE"):
                client.request("GET", gateway.url + "/not-dispatched", **kwargs)
        for headers in ({"Host": "else"}, {"Authorization": "secret"}, {"Sec-Other": "x"}):
            with pytest.raises(TransportPolicyError):
                client.request("GET", gateway.url + "/not-dispatched", headers=headers)
        with pytest.raises(TransportPolicyError, match="INVALID_REQUEST_URL"):
            client.request("GET", gateway.url.replace("http://", "http://user:secret@"))
        check(len(gateway.observed) == 2)
        evidence("real-timeout-redirect", requests=2, retried=False, redirect_followed=False,
                 timeout_code="TRANSPORT_READTIMEOUT", overrides_dispatched=0)
    for timeout in (0, True, float("inf"), float("nan"), 120.1):
        with pytest.raises(TransportPolicyError, match="INVALID_TIMEOUT"):
            none(timeout_seconds=timeout)


def test_exact_expected_ref_and_managed_headers_guard_live_requests():
    policy = none(headers=(HeaderSpec("X-Tenant", public_value="fixed"),))
    wrong = MaterialRef(policy.reference.id, policy.reference.revision, "0" * 64)
    with pytest.raises(TransportPolicyError, match="POLICY_REF_MISMATCH"):
        policy.open_client(wrong)
    public = policy.public_configuration()
    public["headers"][0]["public_value"] = "changed-copy"
    check(policy.public_configuration()["headers"][0]["public_value"] == "fixed")
    with running_server() as gateway, policy.open_client(policy.reference) as client:
        with pytest.raises(TransportPolicyError, match="REQUEST_HEADER_OVERRIDE"):
            client.request("GET", gateway.url + "/not-dispatched", headers={"X-Tenant": "changed"})
        check(client.request("GET", gateway.url + "/allowed", headers={"X-Correlation-Id": "fixture-1"}).status_code == 200)
        check(lower_headers(gateway.observed[0])["x-tenant"] == "fixed")
        check(lower_headers(gateway.observed[0])["x-correlation-id"] == "fixture-1")
        evidence("immutable-public-and-managed-headers", requests=1, ref_exact=True,
                 detached_public_copy=True, fixed_header_preserved=True)


def test_trusted_timeout_cap_only_shortens_real_request_without_changing_ref():
    policy = none(timeout_seconds=60)
    expected_ref = policy.reference
    with running_server() as gateway, policy.open_client(expected_ref, timeout_cap_seconds=0.1) as client:
        check(client.effective_timeout_seconds == 0.1 and policy.reference == expected_ref)
        started = time.monotonic()
        with pytest.raises(TransportPolicyError, match="TRANSPORT_READTIMEOUT"):
            client.request("GET", gateway.url + "/slow")
        check(time.monotonic() - started < 0.3 and len(gateway.observed) == 1)
        with pytest.raises(TransportPolicyError, match="REQUEST_POLICY_OVERRIDE"):
            client.request("GET", gateway.url + "/not-dispatched", timeout=60)
        evidence("trusted-shorter-timeout", policy_timeout_seconds=60, effective_timeout_seconds=0.1,
                 requests=1, policy_ref_unchanged=True)
    with policy.open_client(expected_ref, timeout_cap_seconds=90) as client:
        check(client.effective_timeout_seconds == 60)


def test_trusted_timeout_cap_rejects_invalid_nonfinite_and_boolean():
    policy = none()
    for cap in (0, -1, True, "1", float("nan"), float("inf")):
        with pytest.raises(TransportPolicyError, match="INVALID_TIMEOUT_CAP"):
            policy.open_client(policy.reference, timeout_cap_seconds=cap)
