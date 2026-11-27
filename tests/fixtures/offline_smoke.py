import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

from fastapi.testclient import TestClient
from claude_agent_sdk import ClaudeAgentOptions

from apps.api.config import Settings
from apps.api.main import create_app
from packages.domain.store import Store

if sys.version_info[:2] != (3, 10):
    raise RuntimeError("Unexpected Python version")
try:
    socket.getaddrinfo("pypi.org", 443)
except RuntimeError:
    pass
else:
    raise RuntimeError("Offline network guard was not active")

with tempfile.TemporaryDirectory(prefix="eeas-offline-original-") as directory:
    settings = Settings(Path(directory))
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        bootstrap = client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"}).json()
        client.headers.update({"Origin": settings.origin, "X-EEAS-CSRF": bootstrap["csrf_token"]})
        response = client.post("/api/projects", json={"name": "original-offline-fixture"})
        if response.status_code != 201:
            raise RuntimeError("Offline API project creation failed")
        project = response.json()
        doc = client.post(f"/api/projects/{project['id']}/documents",
            files={"file": ("original.md", b"REQ: original file\nACCEPT: expected contents\n")}).json()
        extracted = client.post(f"/api/projects/{project['id']}/planning/extract",
            json={"document_id": doc["id"], "revision": 1}).json()
        if extracted["mode"] != "mock" or extracted["model_verified"]:
            raise RuntimeError("Offline extraction incorrectly reported live verification")
        body = extracted["proposal"]
        body["confirmed"] = True
        req = client.post(f"/api/projects/{project['id']}/requirements", json=body)
        if req.status_code != 201:
            raise RuntimeError("Offline requirements persistence failed")
    if Store(Path(directory) / "eeas.sqlite3").project(project["id"]) != project:
        raise RuntimeError("Offline database reopen failed")
    if ClaudeAgentOptions(allowed_tools=["Read"]).allowed_tools != ["Read"]:
        raise RuntimeError("SDK import/options failed")
print(json.dumps({"status": "PASS", "python": sys.version.split()[0],
                  "sdk": importlib.metadata.version("claude-agent-sdk"),
                  "environment": "Windows" if os.name == "nt" else "WSL2/Linux",
                  "windows_native": "LOCAL_SMOKE_PASS" if os.name == "nt" else "NOT_VALIDATED",
                  "runtime": "mock", "source": "synthetic", "live_model": "NOT_CONNECTED"}))
