"""Real native connect interleavings may invalidate mutable caller or public scope."""
import asyncio
import json
import os
from unittest.mock import patch

from claude_agent_sdk import ClaudeSDKClient
import pytest

from test_claude_sessions import SessionWire, context
from test_documents import upload
from test_sdk_images import environment
from test_sdk_settings import settings, selected_environment


@pytest.mark.parametrize("mutation", ["prompt", "slash_prompt", "oversize_prompt", "settings_rotation", "document_revision"])
def test_complete_frozen_request_and_source_are_rechecked_after_connect(client, tmp_path, monkeypatch, mutation):
    environment(monkeypatch)
    path = settings(tmp_path) if mutation == "settings_rotation" else None
    if path is not None: selected_environment(monkeypatch, path)
    pid, bridge, request = context(client, tmp_path)
    document = None
    if mutation == "document_revision":
        document = upload(client, pid, b"Original public source before connect", "original.txt").json()
        request["document_refs"] = [{"document_id": document["id"], "revision": 1, "sha256": document["sha256"]}]
    wires, changed = [], []

    class ConnectMutationWire(SessionWire):
        async def connect(self):
            await super().connect()
            if mutation == "prompt": request["prompt"] = "Changed caller prompt after immutable acceptance"
            elif mutation == "slash_prompt": request["prompt"] = "/clear"
            elif mutation == "oversize_prompt": request["prompt"] = "x" * (256 * 1024 + 1)
            elif mutation == "settings_rotation":
                body = json.loads(path.read_bytes())
                body["env"]["ANTHROPIC_AUTH_TOKEN"] = "original-new-token-during-connect"
                rotated = tmp_path / "rotated-approved.json"
                rotated.write_text(json.dumps(body), encoding="utf-8")
                os.replace(rotated, path)
            else:
                response = client.put(f"/api/projects/{pid}/documents/{document['id']}",
                                      files={"file": ("replacement.txt", b"Actual new original revision")},
                                      data={"expected_revision": 1})
                assert response.status_code == 200
            changed.append(True)

    def installed(options):
        wire = ConnectMutationWire(options, 0.002)
        wires.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)

    original_request = json.loads(json.dumps(request))
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=installed):
        row = asyncio.run(bridge.submit(pid, "query-scope-interleaving", request))
    assert changed == [True] and len(wires) == 1 and wires[0].closed
    assert row["execution_status"] == "FAILED" and row["result"] is None
    assert not [message for message in wires[0].writes if message["type"] == "user"]
    assert json.loads(row["request"]) == original_request
