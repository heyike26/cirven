"""Original external HTTP transport only; the unmodified OpenCode CLI executes its real loop."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MARKER = "EEAS_ORIGINAL_TASK_REVIEW_WIRE_RESULT"
KEY = "eeas-original-fixture-not-company"
MODEL = "original-task-wire-v1"


class OriginalWire:
    def __init__(self, behavior="success"):
        self.behavior = behavior
        self.requests = []
        self.received, self.release = threading.Event(), threading.Event()
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 512 * 1024:
                    self.send_error(413); return
                raw = self.rfile.read(length)
                body = json.loads(raw)
                with owner.lock:
                    owner.requests.append({"path": self.path, "body": body,
                        "fixture_auth_matches": self.headers.get("Authorization") == "Bearer " + KEY})
                    count = len(owner.requests)
                owner.received.set()
                if count > 1:
                    self.send_error(429, "Original one-RPC fixture exhausted"); return
                if (self.path != "/v1/chat/completions" or body.get("model") != MODEL or
                        not owner.requests[-1]["fixture_auth_matches"] or body.get("tools") or body.get("stream") is not True):
                    self.send_error(400); return
                if owner.behavior == "hold":
                    owner.release.wait(20)
                if owner.behavior == "error":
                    payload = b'{"error":{"message":"Original non-retryable model transport error","type":"invalid_request_error"}}'
                    self.send_response(400); self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
                    return
                chunks = [
                    {"id": "chatcmpl-original-task", "object": "chat.completion.chunk", "created": 0, "model": MODEL,
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": MARKER + (" " + KEY if owner.behavior == "echo_key" else "")}, "finish_reason": None}]},
                    {"id": "chatcmpl-original-task", "object": "chat.completion.chunk", "created": 0, "model": MODEL,
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}},
                ]
                payload = ("".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers(); self.wfile.write(payload); self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError): pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self): self.thread.start(); return self

    def close(self):
        self.release.set(); self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)

    @property
    def base_url(self): return "http://127.0.0.1:" + str(self.server.server_port) + "/v1"
