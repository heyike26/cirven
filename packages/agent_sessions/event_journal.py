"""Recover committed event tails without replaying native commands.

The SQLite writer lock serializes callers, but cannot commit two JSON files
atomically. An event may survive a failed publication of the state cursor.
Callers must hold that existing writer lock throughout recovery/publication.
"""
from datetime import datetime
import hashlib

from packages.domain.store import Conflict
from packages.domain.tasks import digest

MAX_TAIL_EVENTS = 200
MAX_TAIL_BYTES = 16 * 1024 * 1024


def reconcile_tail(root, state, scope, *, read, write):
    cursor = state.get("last_event_seq")
    if type(cursor) is not int or cursor < 0:
        raise Conflict("Session event cursor is invalid")
    entries, size = [], 0
    for seq in range(cursor + 1, cursor + MAX_TAIL_EVENTS + 1):
        path = root / "events" / f"{seq:010d}.json"
        if not path.exists():
            break
        if not path.is_file() or path.is_symlink():
            raise Conflict("Session event tail is not a regular record")
        size += path.stat().st_size
        if size > MAX_TAIL_BYTES:
            raise Conflict("Session event tail exceeds its recovery byte bound")
        event = read(path)
        if (not isinstance(event, dict) or set(event) != {
                "seq", "type", "payload", "created_at", "original_native_replay"}
                or type(event["seq"]) is not int or event["seq"] != seq
                or not isinstance(event["type"], str) or not event["type"]
                or not isinstance(event["payload"], dict)
                or event["original_native_replay"] is not False):
            raise Conflict("Session event tail scope or schema is invalid")
        try:
            stamp = datetime.fromisoformat(event["created_at"])
            if stamp.tzinfo is None:
                raise ValueError()
        except (TypeError, ValueError):
            raise Conflict("Session event tail timestamp is invalid") from None
        entries.append({"seq": seq, "event_sha256": digest(event),
                        "record_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    if not entries:
        return state, None
    if (root / "events" / f"{entries[-1]['seq'] + 1:010d}.json").exists():
        raise Conflict("Session event tail exceeds its recovery event bound")
    # Immutable prepared evidence precedes publication. Its existence alone is
    # not a claim that state.json was successfully replaced.
    prepared = {"schema": "eeas.session-event-cursor-recovery.v1", "scope": scope,
                "from_seq": cursor, "to_seq": entries[-1]["seq"], "events": entries,
                "native_replay": False}
    key = digest(prepared)
    path = root / "event-cursor-recoveries" / (key + ".json")
    if path.exists():
        if read(path) != prepared:
            raise Conflict("Session event recovery record differs")
    else:
        write(path, prepared)
    updated = {**state, "last_event_seq": entries[-1]["seq"],
               "last_event_cursor_recovery": {"id": key, "sha256": digest(prepared)}}
    return updated, prepared
