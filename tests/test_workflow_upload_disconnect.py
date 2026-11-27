import json

import pytest
from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api.main import create_app


PATH = "/api/stage-workflows/template"


async def invoke(app, client, path, messages, *, method="POST", headers=(), on_disconnect=None):
    """Use the production ASGI stack with a deterministic receive sequence."""
    url = client.base_url
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"host", url.netloc),
            (b"origin", str(url).rstrip("/").encode()),
            (b"cookie", ("eeas_session=" + client.cookies["eeas_session"]).encode()),
            (b"x-eeas-csrf", client.headers["x-eeas-csrf"].encode()),
            *headers,
        ],
        "client": ("127.0.0.1", 12000),
        "server": (url.host, url.port),
    }
    sequence = iter(messages)
    sent = []

    async def receive():
        message = next(sequence, {"type": "http.disconnect"})
        if on_disconnect is not None and message["type"] == "http.disconnect":
            on_disconnect()
        return message

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def template_count(app):
    with app.state.store.connect() as db:
        return db.execute("SELECT count(*) FROM stage_workflow_templates").fetchone()[0]


@pytest.mark.parametrize("partial_body", [b"", b"--template\r\nContent-Disposition: form-data;"])
def test_interrupted_stage_template_never_reaches_parser_or_wakes_observers(client, monkeypatch, partial_body):
    app = client.app
    watched = []
    monkeypatch.setattr(app.state.global_supervisor, "wake", watched.append)
    messages = ([{"type": "http.request", "body": partial_body, "more_body": True}] if partial_body else [])
    messages.append({"type": "http.disconnect"})
    closer = app.state.owner_shutdown
    admitted = []

    def observe():
        admitted.append(closer.inspect()["pending_request_count"])

    async def disconnect():
        return await invoke(app, client, PATH, messages,
                            headers=[(b"content-type", b"multipart/form-data; boundary=template")],
                            on_disconnect=observe)

    assert client.portal.call(disconnect) == []
    assert admitted and all(count == 1 for count in admitted)
    assert closer.inspect()["pending_request_count"] == 0
    assert not watched
    assert template_count(app) == 0
    assert client.get("/health").status_code == 200


def test_cancelled_renderer_asset_request_does_not_report_internal_failure(client):
    async def cancel_asset():
        return await invoke(client.app, client, "/agent-shell/assets/workflow-font.woff2",
                            [{"type": "http.disconnect"}], method="GET")

    assert client.portal.call(cancel_asset) == []
    assert client.get("/health").status_code == 200


def test_a_complete_template_keeps_its_receipt_if_the_client_leaves(client, monkeypatch):
    project = client.post("/api/projects", json={"name": "模板断开验证"}).json()["id"]
    conversation = client.post("/api/conversations", json={
        "project_id": project, "title": "模板操作", "operation_id": "template-disconnect-test",
    }).json()
    owner = conversation["conversation_ref"]["id"]
    # Build an ordinary multipart upload using the same HTTP client as the smoke.
    request = client.build_request("POST", PATH, data={"project_id": project, "conversation_id": owner},
                                   files={"file": ("requirements.md", b"# Requirements", "text/markdown")})
    body = request.read()
    chunk = len(body) // 2
    watched = []
    monkeypatch.setattr(client.app.state.global_supervisor, "wake", watched.append)
    messages = [{"type": "http.request", "body": body[:chunk], "more_body": True},
                {"type": "http.request", "body": body[chunk:], "more_body": False},
                {"type": "http.disconnect"}]

    async def upload():
        return await invoke(client.app, client, PATH, messages,
                            headers=[(b"content-type", request.headers["content-type"].encode())])

    sent = client.portal.call(upload)
    assert sent[0]["status"] == 200
    receipt = json.loads(b"".join(m.get("body", b"") for m in sent))
    assert receipt["name"] == "requirements.md"
    assert template_count(client.app) == 1
    assert watched == ["OWNER_CHANGED"]
    assert client.app.state.owner_shutdown.inspect()["pending_request_count"] == 0


def test_an_app_that_returns_early_without_a_disconnect_still_reports_an_error(tmp_path):
    settings = Settings(tmp_path)
    app = create_app(settings)

    async def incomplete_app(scope, receive, send):
        return

    app.mount("/incomplete", incomplete_app)
    with TestClient(app, base_url=settings.origin) as client:
        with pytest.raises(RuntimeError, match="No response returned"):
            client.get("/incomplete/check")
