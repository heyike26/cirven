"""A bounded HTTP forwarder. The original CLI alone chooses model/tool iterations."""
import asyncio
import hashlib
import json
import math
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler

from packages.adapters.model_transport import open_async_model_transport
from packages.adapters.model_relay_lifecycle import ModelRelayLifecycle, WireCloseUnconfirmed

from packages.domain.tasks import canonical, digest


class ProviderWire:
    def __init__(self, profile, *, admit, complete, max_output_tokens, allowed_tool_names=None, absolute_deadline=None,include_input_bytes=False):
        if type(include_input_bytes) is not bool:raise ValueError("Input byte accounting must be explicit")
        self.include_input_bytes=include_input_bytes
        self.profile, self.admit, self.complete = profile, admit, complete
        self.max_output_tokens = max_output_tokens
        self.allowed_tool_names = None if allowed_tool_names is None else frozenset(allowed_tool_names)
        self.absolute_deadline = absolute_deadline
        if absolute_deadline is not None and (type(absolute_deadline) not in (int, float) or not math.isfinite(absolute_deadline)):
            raise ValueError('Invalid ordinary absolute deadline')
        self.key = secrets.token_hex(24)
        # closed seals admission; only close_confirmed proves every owned
        # request thread (including its complete callback) actually stopped.
        self.closed = False
        self._lock = threading.RLock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def reply(self, status, reason):
                raw = canonical({"error": {"message": reason, "code": reason}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
                self.close_connection = True

            def do_GET(self):
                self.reply(403, "SESSION_PROVIDER_ROUTE_DENIED")

            def do_POST(self):
                if self.path != "/v1/chat/completions" or self.headers.get("Authorization") != "Bearer " + owner.key:
                    self.reply(403, "SESSION_PROVIDER_ROUTE_DENIED")
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 4 * 1024 * 1024:
                        raise ValueError()
                    body = json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    if body.get("model") != profile.model_id or body.get("stream") is not True:
                        raise ValueError()
                    tokens = body.get("max_tokens", body.get("max_completion_tokens"))
                    if type(tokens) is not int or not 1 <= tokens <= owner.max_output_tokens:
                        raise ValueError()
                    if owner.allowed_tool_names is not None:
                        names = [item['function']['name'] for item in body.get('tools', [])]
                        if (not names or len(names) != len(set(names)) or
                                not set(names) <= owner.allowed_tool_names):
                            raise ValueError('Ordinary provider tool declaration differs')
                    with owner._lock:
                        if owner.closed or owner.absolute_deadline is not None and time.time() >= owner.absolute_deadline:
                            raise ValueError('Provider owner closed or absolute deadline expired')
                        # This is the admission boundary. A previously admitted
                        # callback may finish after sealing, and is still owned.
                    receipt = owner.admit(digest(body),input_bytes=size) if owner.include_input_bytes else owner.admit(digest(body))
                except Exception:
                    self.reply(403, "SESSION_PROVIDER_SCOPE_OR_BUDGET_DENIED")
                    return
                observation = {"request_sha256": digest(body), "receipt_id": receipt, "upstream": False}
                try:
                    if owner.closed:
                        observation['upstream'] = False
                        raise ValueError('Wire sealed before forwarding')
                    remaining = 60 if owner.absolute_deadline is None else min(60, owner.absolute_deadline-time.time())
                    if remaining <= 0:
                        observation['upstream'] = False
                        raise ValueError('Ordinary absolute deadline expired before forwarding')
                    # This is the existing request handler's own loop, not an
                    # additional agent or an idle background thread. Cancellation
                    # reaches HTTPX while waiting for headers or the next chunk.
                    owner._io.run(self.forward, body, remaining, observation)
                except asyncio.CancelledError:
                    observation.update(error_type='UPSTREAM_CANCELLED_LOCAL_CLOSE',
                        local_cancel_requested=True, remote_outcome='UNKNOWN')
                    self.close_connection = True
                except Exception as error:
                    observation["error_type"] = type(error).__name__
                    self.close_connection = True
                finally:
                    owner.complete(observation)

            async def forward(self, body, remaining, observation):
                async with open_async_model_transport(profile,timeout_cap_seconds=remaining) as client:
                    with owner._lock:
                        if owner.closed:
                            raise ValueError('Wire sealed before forwarding')
                        observation['upstream'] = True
                    async with client.stream('POST', profile.provider_url+'/chat/completions', json=body) as response:
                        observation['http_status'] = response.status_code
                        if 300 <= response.status_code < 400:
                            self.reply(502, 'SESSION_PROVIDER_REDIRECT_DENIED')
                            return
                        self.send_response(response.status_code)
                        self.send_header('Content-Type', response.headers.get('content-type','application/octet-stream'))
                        self.send_header('Connection','close')
                        self.end_headers()
                        self.close_connection = True
                        sha, size = hashlib.sha256(), 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if (size > 4*1024*1024 or owner.absolute_deadline is not None and
                                    time.time() >= owner.absolute_deadline):
                                raise ValueError('Provider response exceeded its size or absolute time bound')
                            sha.update(chunk)
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        observation.update(response_sha256=sha.hexdigest(),response_bytes=size)

        self._io = ModelRelayLifecycle(Handler, lock=self._lock)
        self.server, self.thread = self._io.server, self._io.thread

    @property
    def base_url(self):
        return "http://127.0.0.1:" + str(self.server.server_port) + "/v1"

    def start(self):
        with self._lock:
            if self.closed:
                raise RuntimeError('WIRE_SEALED')
            self._io.start()
        return self

    @property
    def close_confirmed(self):
        return self._io.close_confirmed

    @property
    def _connections(self):
        return self._io.connections

    @property
    def owned_threads(self):
        return self._io.owned_threads

    @property
    def owned_tasks(self):
        return self._io.owned_tasks

    def close(self, *, timeout_seconds=5):
        self._io.validate_timeout(timeout_seconds)
        with self._lock:
            self.closed = True
        self._io.close(timeout_seconds=timeout_seconds)
