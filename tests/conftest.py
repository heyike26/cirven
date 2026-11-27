import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.config import Settings
from apps.api.main import create_app

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def migrate_through(tmp_path, monkeypatch):
    """Run the real migrator through a historical release before opening the current schema."""
    from packages.domain import store

    def apply(database, version, last_name):
        sources = sorted((ROOT / "packages/domain/migrations").glob("*.sql"))[:version]
        assert len(sources) == version and sources[-1].name == last_name
        folder = tmp_path / f"schema-{version}" / "migrations"
        folder.mkdir(parents=True)
        for source in sources:
            shutil.copyfile(source, folder / source.name)
        with monkeypatch.context() as patch:
            patch.setattr(store, "__file__", str(folder.parent / "store.py"))
            historical = store.Store(database)
            with historical.connect() as db:
                applied = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
            assert applied == [source.name for source in sources]
    return apply


@pytest.fixture
def client(tmp_path):
    settings = Settings(tmp_path, port=8765)
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        response = client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
        client.headers.update({"Origin": settings.origin, "X-EEAS-CSRF": response.json()["csrf_token"]})
        yield client


@pytest.fixture
def claude_review_client(monkeypatch, request):
    monkeypatch.setenv("EEAS_NATIVE_RUNTIME", "claude-agent-sdk")
    return request.getfixturevalue("client")


class LiveServer:
    def __init__(self, data_dir):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.data_dir = data_dir
        self.origin = f"http://127.0.0.1:{self.port}"
        self.env = {**os.environ, "EEAS_DATA_DIR": str(data_dir), "EEAS_PORT": str(self.port),
                    "EEAS_HOST": "127.0.0.1"}
        self.process = None

    def start(self):
        self.process = subprocess.Popen([sys.executable, "-m", "apps.api", "serve"], cwd=ROOT,
                                         env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with httpx.Client(trust_env=False) as client:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("server exited during startup")
                try:
                    response = client.get(self.origin + "/health", timeout=0.1)
                    if response.status_code == 200:
                        return self
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
        raise RuntimeError("server startup timeout")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)


@pytest.fixture
def live_server(tmp_path):
    server = LiveServer(tmp_path)
    try:
        yield server.start()
    finally:
        server.stop()
