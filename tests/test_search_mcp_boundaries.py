"""Real loopback MCP: reject compression early and keep parser logs useful/safe."""
import asyncio
from contextlib import contextmanager
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
import socket
import threading

import httpx2
import pytest

from packages.adapters.search_mcp import call_search, resolve_search_profile


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    original = socket.socket.connect
    def connect(sock, address):
        if isinstance(address, tuple):
            assert address[0] in {"127.0.0.1", "::1"}, "Only original loopback MCP is allowed"
        return original(sock, address)
    monkeypatch.setattr(socket.socket, "connect", connect)


@contextmanager
def upstream(*, encoding=None, malformed=False, padding=0, pause_call=False):
    state = {"requests": [], "sizes": []}
    release = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *_): pass
        def reply(self, raw, *, encoded=False):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if encoded:
                for value in encoding:
                    self.send_header("Content-Encoding", value)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append({"message": request, "encoding": self.headers.get("Accept-Encoding")})
            if "id" not in request:
                self.send_response(202); self.send_header("Content-Length", "0"); self.end_headers(); return
            method = request["method"]
            if method == "initialize":
                result = {"protocolVersion": request["params"]["protocolVersion"], "capabilities": {"tools": {}},
                          "serverInfo": {"name": "original-bounded-search", "version": "1"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "approved_search", "inputSchema": {"type": "object"}}]}
            elif method == "tools/call":
                if pause_call:
                    release.wait(3)
                payload = {"results": [{"url": "https://original.example.invalid/lead", "title": "线索", "snippet": "无全文读取"}]}
                result = {"content": [{"type": "text", "text": json.dumps(payload) + " " * padding}]}
            else:
                raise AssertionError(method)
            raw = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
            if malformed and method == "tools/call":
                # Echo only this test client's synthetic credentials, exactly as
                # the installed parser's ValidationError used to expose them.
                raw = (self.headers["Authorization"] + " " + self.headers.get("X-API-Key", "")).encode()
            encoded = method == "tools/call" and encoding is not None
            wire = gzip.compress(raw) if encoded and encoding == ["gzip"] else raw
            state["sizes"].append({"method": method, "raw": len(wire), "decoded": len(raw)})
            try:
                self.reply(wire, encoded=encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass  # A deliberately cancelled client closes the real socket.
        def do_GET(self):
            self.send_response(405); self.send_header("Content-Length", "0"); self.end_headers()
        def do_DELETE(self):
            self.send_response(202); self.send_header("Content-Length", "0"); self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state["url"] = f"http://127.0.0.1:{server.server_port}/mcp"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        release.set()
        server.shutdown(); server.server_close(); thread.join(2)
        assert not thread.is_alive()


def profile(tmp_path, state, name="one", limit=1024):
    path = tmp_path / (name + "-approved-search.json")
    secrets = ("ORIGINAL_" + name + "_TOKEN", "ORIGINAL_" + name + "_API_KEY")
    path.write_text(json.dumps({"schema_version": "eeas.search-mcp.v1", "capability": "search_only",
        "transport": {"type": "http", "url": state["url"],
                      "headers": {"Authorization": "Bearer " + secrets[0], "X-API-Key": secrets[1]}},
        "upstream_tool_name": "approved_search", "limits": {"max_result_bytes": limit, "timeout_seconds": 3}}))
    return resolve_search_profile({"EEAS_SEARCH_MCP_CONFIG_PATH": str(path)}), secrets


def leaves(exception):
    if hasattr(exception, "exceptions"):
        return [leaf for child in exception.exceptions for leaf in leaves(child)]
    return [exception]


@pytest.mark.parametrize("encoding", [["gzip"], ["deflate"], ["br"], ["gzip, identity"], ["identity", "gzip"], ["future-codec"]])
def test_nonidentity_is_refused_before_any_payload_read(tmp_path, monkeypatch, encoding):
    reads = []
    original = httpx2.Response.aread
    async def measured(response):
        raw = await original(response)
        reads.append(len(raw))
        return raw
    monkeypatch.setattr(httpx2.Response, "aread", measured)
    with upstream(encoding=encoding, padding=32768) as state:
        selected, _ = profile(tmp_path, state)
        with pytest.raises(Exception) as caught:
            asyncio.run(call_search(selected, {"query": "原创线索"}))
    assert any(str(error) == "SEARCH_MCP_CONTENT_ENCODING_UNSUPPORTED" for error in leaves(caught.value))
    assert all(size < selected.limits["max_result_bytes"] * 4 + 8192 for size in reads)
    assert all(request["encoding"] == "identity" for request in state["requests"])


@pytest.mark.parametrize("encoding", [None, ["identity"], ["Identity"]])
def test_uncompressed_response_still_searches_without_reading_source(tmp_path, encoding):
    with upstream(encoding=encoding) as state:
        selected, _ = profile(tmp_path, state)
        result = asyncio.run(call_search(selected, {"query": "原创线索"}))
    assert result["kind"] == "SEARCH_LEAD" and result["original_document_read"] is False
    assert [request["message"]["method"] for request in state["requests"]].count("tools/call") == 1
    assert all(request["encoding"] == "identity" for request in state["requests"])


def test_plain_overlimit_stays_rejected_by_the_raw_stream_bound(tmp_path):
    with upstream(padding=32768) as state:
        selected, _ = profile(tmp_path, state)
        with pytest.raises(Exception) as caught:
            asyncio.run(call_search(selected, {"query": "原创线索"}))
    assert any(str(error) == "SEARCH_MCP_TRANSPORT_LIMIT" for error in leaves(caught.value))


def test_concurrent_clients_and_every_handler_keep_parser_reason_without_credentials(tmp_path, caplog):
    logger = logging.getLogger("mcp.client.streamable_http")
    streams = [io.StringIO(), io.StringIO()]
    handlers = [logging.StreamHandler(stream) for stream in streams]
    for index, handler in enumerate(handlers):
        handler.setFormatter(logging.Formatter(f"handler{index} %(levelname)s %(message)s"))
        logger.addHandler(handler)
    try:
        with upstream(malformed=True) as first, upstream(malformed=True) as second:
            one, secrets1 = profile(tmp_path, first, "one")
            two, secrets2 = profile(tmp_path, second, "two")
            async def run():
                return await asyncio.gather(call_search(one, {"query": "一"}), call_search(two, {"query": "二"}), return_exceptions=True)
            with caplog.at_level(logging.ERROR):
                outcomes = asyncio.run(run())
        assert all(isinstance(outcome, Exception) for outcome in outcomes)
        logs = [caplog.text, *(stream.getvalue() for stream in streams)]
        for output in logs:
            assert "Error parsing JSON response" in output and "ValidationError" in output
            assert output.count("[REDACTED]") >= 2  # Both original parser errors omit their input.
            assert not any(secret in output for secret in (*secrets1, *secrets2))
        records = [record for record in caplog.records if record.name == logger.name and "Error parsing JSON" in record.getMessage()]
        assert len(records) == 2 and all(record.exc_info is None for record in records)
        assert all(record.levelno == logging.ERROR for record in records)

        # The filter does not mute or rewrite a different MCP client after this
        # operation has ended, including its ordinary exception information.
        caplog.clear()
        with caplog.at_level(logging.ERROR):
            try:
                raise ValueError("original unrelated failure")
            except ValueError:
                logger.exception("outside selected search %s", secrets1[0])
        assert secrets1[0] in caplog.text and "original unrelated failure" in caplog.text
        assert caplog.records[-1].exc_info is not None
    finally:
        for handler in handlers:
            logger.removeHandler(handler)


def test_selected_client_does_not_change_loggers_or_levels_for_parallel_other_client(tmp_path, caplog):
    logger = logging.getLogger("mcp.client.streamable_http")
    level, disabled, handlers = logger.level, logger.disabled, tuple(logger.handlers)
    with upstream(malformed=True) as state:
        selected, secrets = profile(tmp_path, state)
        async def normal_client():
            # This sibling task did not inherit the selected operation's scope.
            await asyncio.sleep(0)
            logger.error("original unrelated sibling %s", secrets[0])
        async def run():
            return await asyncio.gather(call_search(selected, {"query": "原创"}), normal_client(), return_exceptions=True)
        with caplog.at_level(logging.ERROR):
            outcomes = asyncio.run(run())
    assert isinstance(outcomes[0], Exception)
    assert "original unrelated sibling " + secrets[0] in caplog.text
    parsing = [record for record in caplog.records if "Error parsing JSON response" in record.getMessage()]
    assert len(parsing) == 1 and secrets[0] not in parsing[0].exc_text
    assert (logger.level, logger.disabled, tuple(logger.handlers)) == (level, disabled, handlers)


@pytest.mark.parametrize("cancel", [False, True])
def test_credential_log_scope_is_reset_in_same_task_after_failure_or_cancel(tmp_path, caplog, cancel):
    logger = logging.getLogger("mcp.client.streamable_http")
    with upstream(malformed=True, pause_call=cancel) as state:
        selected, secrets = profile(tmp_path, state)
        async def run():
            if cancel:
                task = asyncio.current_task()
                async def canceller():
                    for _ in range(200):
                        if any(item["message"]["method"] == "tools/call" for item in state["requests"]):
                            task.cancel(); return
                        await asyncio.sleep(0.01)
                    raise AssertionError("Real MCP call was never observed")
                controller = asyncio.create_task(canceller())
            try:
                await call_search(selected, {"query": "原创"})
            except asyncio.CancelledError:
                assert cancel
            except Exception:
                assert not cancel
            else:
                raise AssertionError("Malformed/cancelled MCP call must fail")
            if cancel:
                await controller
            # This is the original task which directly awaited call_search.
            logger.error("scope ended %s", secrets[0])
        with caplog.at_level(logging.ERROR):
            asyncio.run(run())
    assert "scope ended " + secrets[0] in caplog.text


def test_invalid_json_escaped_header_credentials_are_redacted_in_byte_repr(tmp_path, caplog):
    from dataclasses import replace
    with upstream(malformed=True) as state:
        selected, _ = profile(tmp_path, state)
        token = "ORIGINAL_quote'\\backslash_TOKEN"
        key = 'ORIGINAL_double"\\slash_KEY'
        selected = replace(selected, headers={"Authorization": "Bearer " + token, "X-API-Key": key})
        with caplog.at_level(logging.ERROR), pytest.raises(Exception):
            asyncio.run(call_search(selected, {"query": "原创"}))
    for secret in (token, key):
        assert secret not in caplog.text and repr(secret)[1:-1] not in caplog.text
    assert "ORIGINAL_quote" not in caplog.text and "slash_KEY" not in caplog.text
    assert "ValidationError" in caplog.text and "[REDACTED]" in caplog.text
