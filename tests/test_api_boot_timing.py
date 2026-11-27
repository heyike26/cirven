import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from apps.api import boot_timing as timing


def records(output):
    return [json.loads(line) for line in output.splitlines()
            if line.startswith('{"schema":"eeas.startup-timing.v1"')]


@pytest.fixture(autouse=True)
def reset_components(monkeypatch):
    monkeypatch.setattr(timing, "_STARTS", {})


@pytest.mark.parametrize("flag", [None, "0", "true"])
def test_default_silent_without_clock_or_new_io(monkeypatch, capsys, flag):
    if flag is None:
        monkeypatch.delenv("EEAS_STARTUP_TIMING", raising=False)
    else:
        monkeypatch.setenv("EEAS_STARTUP_TIMING", flag)
    monkeypatch.setattr(timing, "_now", lambda: pytest.fail("disabled measurements read clock"))
    with timing.startup_phase("API_ENTRY", "CREATE_APP"):
        result = 17
    timing.startup_entered("API_ENTRY", "SERVER_RUN")
    assert result == 17
    assert capsys.readouterr() == ("", "")


def test_phase_pairs_measure_actual_boundary_without_body_or_paths(monkeypatch, capsys):
    monkeypatch.setenv("EEAS_STARTUP_TIMING", "1")
    ticks = iter([1_000_000, 4_500_000, 6_000_000])
    monkeypatch.setattr(timing.time, "perf_counter_ns", lambda: next(ticks))
    with timing.startup_phase("API_ENTRY", "CREATE_APP"):
        secret = "not-a-real-token-unrelated-private-path"
    timing.startup_entered("API_ENTRY", "SERVER_RUN")
    output = capsys.readouterr()
    assert not output.out and secret not in output.err
    rows = records(output.err)
    assert [r["status"] for r in rows] == ["ENTERED", "COMPLETED", "ENTERED"]
    assert rows[1]["duration_ms"] == 3.5 and rows[1]["elapsed_ms"] == 3.5
    assert rows[2]["phase"] == "SERVER_RUN" and "duration_ms" not in rows[2]
    assert rows[2]["elapsed_ms"] == 5.0
    assert all(r["pid"] == os.getpid() for r in rows)
    assert set(rows[0]) == {"schema", "event", "component", "pid", "phase", "status",
                            "started_monotonic_ns", "elapsed_ms"}


@pytest.mark.parametrize("failure, status", [(RuntimeError("private-test-text"), "FAILED"),
                                            (asyncio.CancelledError(), "CANCELLED"),
                                            (KeyboardInterrupt(), "CANCELLED")])
def test_failure_identity_and_cancellation_are_not_hidden(monkeypatch, capsys, failure, status):
    monkeypatch.setenv("EEAS_STARTUP_TIMING", "1")
    with pytest.raises(type(failure)) as caught:
        with timing.startup_phase("API_APP", "STORE_INIT"):
            raise failure
    assert caught.value is failure
    output = capsys.readouterr()
    assert "private-test-text" not in output.err and output.out == ""
    assert [r["status"] for r in records(output.err)] == ["ENTERED", status]


def test_broken_diagnostic_stream_and_clock_do_not_change_business(monkeypatch):
    monkeypatch.setenv("EEAS_STARTUP_TIMING", "1")
    class BrokenStream(io.StringIO):
        def write(self, value):
            raise OSError("unavailable-log-sink")
    monkeypatch.setattr(timing.sys, "stderr", BrokenStream())
    with timing.startup_phase("API_APP", "STORE_INIT"):
        result = 23
    failure = ValueError("original-business-failure")
    with pytest.raises(ValueError) as caught:
        with timing.startup_phase("API_APP", "STORE_INIT"):
            raise failure
    assert result == 23 and caught.value is failure
    monkeypatch.setattr(timing.time, "perf_counter_ns", lambda: (_ for _ in ()).throw(OSError()))
    with timing.startup_phase("API_APP", "STORE_INIT"):
        result += 1
    assert result == 24


@pytest.mark.parametrize("component, phase", [("private-component", "C:/private-file"),
                                               ([], "CREATE_APP"), ("API_ENTRY", [])])
def test_unknown_labels_cannot_become_metadata_channel(monkeypatch, capsys, component, phase):
    monkeypatch.setenv("EEAS_STARTUP_TIMING", "1")
    with timing.startup_phase(component, phase):
        pass
    assert capsys.readouterr() == ("", "")


def test_actual_entry_imports_measured_and_help_keeps_stdout_contract(tmp_path):
    env = {**os.environ, "EEAS_STARTUP_TIMING": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "EEAS_DATA_DIR": str(tmp_path / "not-created-by-help")}
    result = subprocess.run([sys.executable, "-m", "apps.api", "--help"],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "EEAS local service management" in result.stdout
    assert not (tmp_path / "not-created-by-help").exists()
    rows = records(result.stderr)
    assert [(r["phase"], r["status"]) for r in rows] == [
        ("CLIENT_SERVER_IMPORTS", "ENTERED"), ("CLIENT_SERVER_IMPORTS", "COMPLETED"),
        ("APPLICATION_IMPORT", "ENTERED"), ("APPLICATION_IMPORT", "COMPLETED")]
    assert all(r["pid"] != os.getpid() and r["component"] == "API_ENTRY" for r in rows)


def test_actual_app_storage_skills_lifespan_and_health_with_no_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EEAS_STARTUP_TIMING", "1")
    from fastapi.testclient import TestClient
    from apps.api.config import Settings
    from apps.api.main import create_app
    app = create_app(Settings(data_dir=tmp_path, worker_enabled=False))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get("/health")
        assert response.status_code == 200
    rows = records(capsys.readouterr().err)
    assert [(r["phase"], r["status"]) for r in rows] == [
        ("STORE_INIT", "ENTERED"), ("STORE_INIT", "COMPLETED"),
        ("BUNDLED_SKILL_VERIFY", "ENTERED"), ("BUNDLED_SKILL_VERIFY", "COMPLETED"),
        ("LIFESPAN_START", "ENTERED"), ("LIFESPAN_START", "COMPLETED")]
    assert all(r["component"] == "API_APP" for r in rows)
