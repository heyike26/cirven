"""Actual local API/Store/record adapters; no model or native OS is qualified."""
from pathlib import Path
from types import SimpleNamespace
import asyncio
import json
import threading

from fastapi.testclient import TestClient
import pytest

from apps.api.config import Settings
from apps.api.main import create_app
from apps.api.supervisor import ApplicationTaskCollector
from packages.agent_sessions.service import AgentSessions
from packages.domain.store import Store
from packages.domain.tasks import canonical, digest, now
from packages.domain.conversation_threads import ConversationThreads, source_identity


def session_record(service, pid, ident, operation, status, *, generation=1, waiting=False):
    manifest = {"id": ident, "project_id": pid, "request": {}, "title": "DO_NOT_EXPORT_TRANSCRIPT"}
    service._write(service._path(pid, ident, "manifest.json"), manifest)
    service._write(service._path(pid, ident, "state.json"), {
        "generation": generation, "active_operation_id": operation,
        "pending_questions": [{"DO_NOT_EXPORT": "private text"}] if waiting else [],
        "pending_permissions": [], "observation_at": now(), "messages": [{"text": "PRIVATE_TEXT"}]})
    service._write(service._path(pid, ident, "operations", digest(operation) + ".json"), {
        "id": operation, "kind": "MESSAGE", "status": status,
        "operation_ref": {"id": operation, "request_sha256": "a" * 64},
        "request": {"generation": generation, "raw_text": "NEVER_EXPORT_PROMPT"}})
    service.active[ident] = {"process": SimpleNamespace(returncode=None)}


def test_collector_three_independent_sessions_preserve_wait_return_unknown_without_model_calls(tmp_path):
    store = Store(tmp_path / "state.sqlite"); store.migrate()
    pid = store.create_project("test", "")['id']
    sessions = AgentSessions(store, tmp_path, provider_registry=None, workspaces=None, capabilities=None)
    session_record(sessions, pid, "a" * 32, "running-a", "ACCEPTED", waiting=True)
    session_record(sessions, pid, "b" * 32, "returned-b", "RETURNED")
    session_record(sessions, pid, "c" * 32, "lost-c", "ACCEPTED", generation=2, waiting=True)
    sessions.active.pop("c" * 32)
    sessions.inspect = lambda *_args: pytest.fail("Collector must not invoke runtime inspection or a model")
    collector = ApplicationTaskCollector(SimpleNamespace(state=SimpleNamespace(store=store, agent_sessions=sessions)))
    result = collector.collect_sync()
    tasks = result["observations"]
    assert result["unavailable_codes"] == []
    indexed = {item["key"]["operation_id"]: item for item in tasks}
    assert indexed["running-a"]["wait_reason"] == "USER_INPUT"
    assert indexed["returned-b"]["execution_returned"] is True
    assert indexed["returned-b"]["validation_status"] == "NOT_RUN"
    assert indexed["lost-c"]["source_status"] == "UNKNOWN_OUTCOME"
    assert indexed["lost-c"]["wait_reason"] is None
    assert indexed["lost-c"]["key"]["generation"] == 2
    assert len(tasks) == 3
    raw = json.dumps(tasks)
    for private in ("PRIVATE_TEXT", "private text", "DO_NOT_EXPORT", "NEVER_EXPORT_PROMPT"):
        assert private not in raw
    assert collector.coverage["model_calls"] == 0


def test_task_titles_use_verified_public_navigation_metadata_without_changing_execution_identity(tmp_path):
    store = Store(tmp_path / 'state.sqlite'); store.migrate()
    pid = store.create_project('设计文档项目', '')['id']
    sessions = AgentSessions(store, tmp_path, provider_registry=None, workspaces=None, capabilities=None)
    sid = 'e' * 32
    session_record(sessions, pid, sid, 'returned-one', 'RETURNED')
    sessions.inspect = lambda *_: pytest.fail('Display labels cannot inspect or start an owner')
    collector = ApplicationTaskCollector(SimpleNamespace(state=SimpleNamespace(store=store, agent_sessions=sessions)))
    before = collector.collect_sync()['observations'][0]
    assert before['label'] == '设计文档项目' and sid[:8] not in before['label']
    reference = sessions._manifest(pid, sid)[1]
    source = {'kind': 'original', 'project_id': pid, 'session_ref': reference, 'workspace_ref': None}
    alias, identity = source_identity(source)
    with store.connect() as connection:
        ConversationThreads(store).enroll(connection, {'id': alias, 'kind': 'original',
            'project_id': pid, 'title': '首版交付文档 😀', 'source': source}, created_at=now())
    titled = collector.collect_sync()['observations'][0]
    assert titled['label'] == '首版交付文档 😀'
    assert {k: v for k, v in before.items() if k not in {'label', 'observed_at', 'display_context'}} == {
        k: v for k, v in titled.items() if k not in {'label', 'observed_at', 'display_context'}}
    assert before['display_context'] == {'project_label': '设计文档项目'}
    assert titled['display_context']['project_label'] == '设计文档项目'
    assert titled['display_context']['conversation_created_at']
    # A rename is display-only. Prompt/manifest text is never consulted.
    with store.connect() as connection:
        connection.execute('UPDATE conversation_threads SET display_title=? WHERE conversation_id=?',
                           ('重命名后的文档', alias))
    assert collector.collect_sync()['observations'][0]['label'] == '重命名后的文档'
    for unsafe in ['password=NEVER_EXPORT', 'bad\nname']:
        with store.connect() as connection:
            connection.execute('UPDATE conversation_threads SET display_title=? WHERE conversation_id=?', (unsafe, alias))
        assert collector.collect_sync()['observations'][0]['label'] == '设计文档项目'
    with store.connect() as connection:
        connection.execute('UPDATE conversation_threads SET display_title=? WHERE conversation_id=?', ('SAFE_BUT_WRONG_SOURCE', alias))
        connection.execute('UPDATE conversation_source_aliases SET source_sha256=? WHERE alias_id=?', ('0' * 64, alias))
    result = collector.collect_sync()
    assert result['observations'][0]['label'] == '设计文档项目'
    assert result['observations'][0]['source_status'] == 'RETURNED'
    assert 'NEVER_EXPORT_PROMPT' not in json.dumps(result) and 'DO_NOT_EXPORT_TRANSCRIPT' not in json.dumps(result)
    assert collector.coverage['model_calls'] == 0
    malformed = {'kind': 'unknown-source-kind', 'project_id': pid}
    with store.connect() as connection:
        connection.execute('UPDATE conversation_source_aliases SET source_json=?,source_sha256=? WHERE alias_id=?',
                           (canonical(malformed), digest(malformed), alias))
    recovered = collector.collect_sync()
    assert recovered['observations'][0]['label'] == '设计文档项目'
    assert recovered['observations'][0]['source_status'] == 'RETURNED'


def test_real_application_api_settings_conflict_checks_and_supervisor_lifecycle(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", port=8787, worker_enabled=False)
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8787") as client:
        bootstrap = client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
        assert bootstrap.status_code == 200
        client.headers.update({"Origin": "http://127.0.0.1:8787", "X-EEAS-CSRF": bootstrap.json()["csrf_token"]})
        initial = client.get("/api/supervisor").json()
        before = initial["settings"]
        assert before["notifications_enabled"] is False
        changed = client.post("/api/supervisor/settings", json={
            "enabled": True, "notifications_enabled": False, "interval_seconds": 17,
            "expected_revision": before["revision"]})
        assert changed.status_code == 200, changed.text
        assert changed.json()["interval_seconds"] == 17
        assert changed.json()["notifications_enabled"] is False
        conflict = client.post("/api/supervisor/settings", json={
            "enabled": False, "notifications_enabled": True, "interval_seconds": 5,
            "expected_revision": before["revision"]})
        assert conflict.status_code == 409
        rejected = client.post("/api/supervisor/settings", json={
            "enabled": True, "notifications_enabled": True, "interval_seconds": 1,
            "expected_revision": changed.json()["revision"]})
        assert rejected.status_code == 422
        checked = client.post("/api/supervisor/check")
        assert checked.status_code == 200, checked.text
        value = checked.json()
        assert value["settings"]["interval_seconds"] == 17
        assert value["model_calls"] == 0 and value["automatic_task_retry"] is False
        assert value["source_coverage"]["legacy_mock_worker_included"] is False
        assert value["tasks"] == []
    assert app.state.agent_sessions.closed is True
    # Reopening uses the same persisted settings, without any model call.
    another = create_app(settings)
    with TestClient(another, base_url="http://127.0.0.1:8787") as client:
        assert client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"}).status_code == 200
        assert client.get("/api/supervisor").json()["settings"]["interval_seconds"] == 17


def test_corrupt_historical_result_is_isolated_from_healthy_session_updates(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    pid = store.create_project("test", "")['id']
    sessions = AgentSessions(store, tmp_path, provider_registry=None, workspaces=None, capabilities=None)
    session_record(sessions, pid, "a" * 32, "healthy-a", "RETURNED")
    with store.connect() as connection:
        connection.execute("INSERT INTO planning_proposal_operations VALUES (?,?,?,?,?,?,?)",
                           (pid, "bad-historical-result", "a" * 64, "old-proposal", "FAILED", "{", "b" * 64))
    collector = ApplicationTaskCollector(SimpleNamespace(state=SimpleNamespace(store=store, agent_sessions=sessions)))
    result = collector.collect_sync()
    assert result["unavailable_codes"] == ["PLANNING_RESULT_INTEGRITY_FAILED"]
    assert len(result["observations"]) == 1
    assert result["observations"][0]["key"]["operation_id"] == "healthy-a"
    assert result["observations"][0]["source_status"] == "RETURNED"


def test_discovery_operation_and_underlying_execution_are_one_supervised_user_task(tmp_path):
    store = Store(tmp_path / "state.sqlite")
    pid = store.create_project("test", "")['id']
    operation = "discover-one"
    native_operation = "disc-" + digest([pid, operation])[:40]
    with store.connect() as connection:
        connection.execute("INSERT INTO discovery_operations VALUES (?,?,?,?,?,?,?,?)",
                           (pid, operation, "discovery-one", "REFINE", "a" * 64, "PROCESSING", None, None))
        connection.execute("""INSERT INTO agent_executions
            (id,project_id,operation_id,request_hash,runtime,workspace,execution_status,verification_status,
             session_id,model_profile,sdk_version,request,result,created_at,ended_at,cancel_requested)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("real-execution", pid, native_operation, "a" * 64, "opencode-planning-http", "private-runtime",
             "FAILED", "NOT_RUN", None, "configured-profile", "1.19.0", "{}", None, now(), now(), 0))
    collector = ApplicationTaskCollector(SimpleNamespace(state=SimpleNamespace(store=store)))
    result = collector.collect_sync()
    assert result["unavailable_codes"] == []
    assert len(result["observations"]) == 1
    assert result["observations"][0]["key"]["kind"] == "discovery"
    assert result["observations"][0]["source_status"] == "FAILED"


def test_source_reader_close_joins_actual_thread_after_the_poll_await_was_cancelled(tmp_path):
    started, release, ended = threading.Event(), threading.Event(), threading.Event()
    collector = ApplicationTaskCollector(SimpleNamespace(state=None))
    def blocking_read():
        started.set();release.wait(5);ended.set();return {"observations": [], "unavailable_codes": []}
    collector.collect_sync = blocking_read
    async def scenario():
        read = asyncio.create_task(collector())
        await asyncio.to_thread(started.wait, 3)
        read.cancel()
        with pytest.raises(asyncio.CancelledError):
            await read
        closing = asyncio.create_task(collector.close())
        await asyncio.sleep(.02)
        assert not closing.done(), "Cancelling the await must not qualify the still-running source thread as closed"
        release.set();await closing
        assert ended.is_set() and not collector._reads
    asyncio.run(scenario())
