"""Real SQLite and sealed files; crash-window injection, no model/CLI."""
from copy import deepcopy
import hashlib

import pytest

from packages.agent_sessions import AgentSessions
from packages.agent_sessions import event_journal
from packages.domain.store import Store, Conflict
from packages.domain.tasks import digest, now


@pytest.fixture
def journal(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    store = Store(data / "eeas.sqlite3")
    pid = store.create_project("Event crash recovery", "test")["id"]
    ident = "a" * 32
    service = AgentSessions(store, data, provider_registry=None, workspaces=None, capabilities=None)
    manifest = {"id": ident, "project_id": pid, "request": {}}
    ref = {"id": ident, "revision": 1, "sha256": digest(manifest)}
    state = {"generation": 4, "last_event_seq": 0, "owner_status": "BLOCKED",
             "active_operation_id": None, "pending_permissions": [{"generation": 3}],
             "process_identity": {"pid": 321, "created": 123.0},
             "controller_identity": None, "load_error": "FileExistsError"}
    service._write(service._path(pid, ident, "manifest.json"), manifest)
    service._write(service._path(pid, ident, "state.json"), state)
    old = {"status": "UNKNOWN_OUTCOME", "generation": 3, "raw_text": "keep manual edits"}
    service._write(service._path(pid, ident, "operations", "old.json"), old)
    def repair(**changes):
        return service.repair_event_journal(store, data, pid, ident,
            session_ref=ref, expected_generation=changes.pop("generation", 4), **changes)
    def event(seq):
        value = {"seq": seq, "type": "ORIGINAL_EVENT", "payload": {"test": True},
                 "created_at": now(), "original_native_replay": False}
        service._write(service._path(pid, ident, "events", f"{seq:010d}.json"), value)
        return value
    return service, pid, ident, state, repair, event


def tree_hash(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*.json")}


def test_real_tail_adoption_only_changes_observation_cursor_and_is_idempotent(journal):
    service, pid, ident, state, repair, event = journal
    first, second = event(1), event(2)
    root = service._path(pid, ident)
    before = tree_hash(root)
    result = repair(expected_cursor=0)
    assert result["from_seq"] == 0 and result["to_seq"] == 2
    assert result["cursor_published"] and result["native_replay"] is False
    after = service._state(pid, ident)
    assert {k: after[k] for k in state if k != "last_event_seq"} == {
        k: state[k] for k in state if k != "last_event_seq"}
    for path, sha in before.items():
        if path != "state.json":
            assert tree_hash(root)[path] == sha
    assert service.events(pid, ident)["events"] == [first, second]
    assert len(list((root / "event-cursor-recoveries").glob("*.json"))) == 1
    assert repair(expected_cursor=2)["to_seq"] == 2
    assert service._event(pid, ident, "NEW_OBSERVATION", {}) == 3
    assert service._read(root / "events/0000000001.json") == first


@pytest.mark.parametrize("bad", ["seal", "seq", "replay", "timestamp", "schema"])
def test_invalid_tail_fails_closed_without_rewriting_any_record(journal, bad):
    service, pid, ident, state, repair, event = journal
    value = event(1)
    path = service._path(pid, ident, "events/0000000001.json")
    if bad == "seal":
        path.write_bytes(path.read_bytes().replace(b'ORIGINAL_EVENT', b'ORIGINAL_FAULT'))
    else:
        if bad == "seq": value["seq"] = 2
        if bad == "replay": value["original_native_replay"] = True
        if bad == "timestamp": value["created_at"] = "yesterday"
        if bad == "schema": value["unexpected"] = True
        service._write(path, value, replace=True)
    before = tree_hash(service._path(pid, ident))
    with pytest.raises(Conflict): repair()
    assert tree_hash(service._path(pid, ident)) == before
    assert service._state(pid, ident) == state


def test_bounds_and_stale_maintenance_guards(journal, monkeypatch):
    service, pid, ident, state, repair, event = journal
    event(1); event(2)
    with pytest.raises(Conflict): repair(generation=3)
    with pytest.raises(Conflict): repair(expected_cursor=1)
    monkeypatch.setattr(event_journal, "MAX_TAIL_EVENTS", 1)
    with pytest.raises(Conflict, match="event bound"): repair()
    assert service._state(pid, ident) == state
    monkeypatch.setattr(event_journal, "MAX_TAIL_EVENTS", 200)
    monkeypatch.setattr(event_journal, "MAX_TAIL_BYTES", 1)
    with pytest.raises(Conflict, match="byte bound"): repair()
    assert service._state(pid, ident) == state


def test_publication_failure_then_real_append_recovers_without_overwrite(journal, monkeypatch):
    service, pid, ident, state, repair, event = journal
    original_write = service._write
    failed = False
    def crash(path, value, **kwargs):
        nonlocal failed
        if path.name == "state.json" and not failed:
            failed = True
            raise OSError("Injected event committed before cursor publication")
        return original_write(path, value, **kwargs)
    monkeypatch.setattr(service, "_write", crash)
    with pytest.raises(OSError): service._event(pid, ident, "ORIGINAL_EVENT", {"kept": True})
    path = service._path(pid, ident, "events/0000000001.json")
    retained = path.read_bytes()
    assert service._state(pid, ident) == state
    assert service._event(pid, ident, "FOLLOWING_OBSERVATION", {}) == 2
    assert path.read_bytes() == retained
    assert service.events(pid, ident)["last_event_seq"] == 2


def test_failed_maintenance_publication_can_reuse_prepared_evidence(journal, monkeypatch):
    service, pid, ident, state, repair, event = journal
    event(1)
    original_write = AgentSessions._write
    def crash(path, value, **kwargs):
        if path.name == "state.json": raise OSError("Injected failed publication")
        return original_write(path, value, **kwargs)
    monkeypatch.setattr(AgentSessions, "_write", staticmethod(crash))
    with pytest.raises(OSError): repair()
    prepared = list(service._path(pid, ident, "event-cursor-recoveries").glob("*.json"))
    assert len(prepared) == 1 and service._state(pid, ident) == state
    raw = prepared[0].read_bytes()
    monkeypatch.setattr(AgentSessions, "_write", staticmethod(original_write))
    assert repair()["to_seq"] == 1
    assert prepared[0].read_bytes() == raw
