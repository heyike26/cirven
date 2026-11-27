"""Real SQLite/restart/CAS and asyncio lifecycle; collector never owns execution."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
import time

import pytest
from pydantic import ValidationError

from packages.agent_sessions.supervisor import (
    GlobalSupervisor, TaskObservation, SupervisorConflict, SupervisorIntegrityError, SupervisorInvalidInput,
)


def check(value, message="supervisor acceptance failed"):
    if not value: raise AssertionError(message)


def observation(name="task", status="RUNNING", epoch=1700000000, **extra):
    return {"key": {"kind": "original-session", "project_id": "project", "object_id": name,
                   "operation_id": "op_" + name, "generation": 2},
            "label": "独立工程会话 " + name, "source_status": status,
            "observed_at": datetime.fromtimestamp(epoch, timezone.utc).isoformat(), **extra}


class Collector:
    def __init__(self, values=()): self.values, self.calls, self.failure = list(values), 0, None
    async def __call__(self):
        self.calls += 1
        if self.failure: raise self.failure
        return deepcopy(self.values)


def test_strict_safe_observation_and_settings_cas_are_persistent(tmp_path):
    collect = Collector()
    supervisor = GlobalSupervisor(tmp_path, collect)
    first = supervisor.settings()
    check(first["enabled"] and not first["notifications_enabled"] and first["interval_seconds"] == 30)
    saved = supervisor.update_settings(enabled=False, interval_seconds=5, notifications_enabled=False, expected_revision=1)
    check(saved["revision"] == 2 and not saved["enabled"] and not saved["notifications_enabled"])
    reopened = GlobalSupervisor(tmp_path, collect)
    check(reopened.settings() == saved)
    check(reopened.update_settings(enabled=False, interval_seconds=5, expected_revision=2) == saved)
    with pytest.raises(SupervisorConflict): supervisor.update_settings(enabled=True, interval_seconds=5, expected_revision=1)
    for bad in [True, 4, 3601, "30"]:
        with pytest.raises(SupervisorInvalidInput): supervisor.update_settings(enabled=True, interval_seconds=bad, expected_revision=2)
    with pytest.raises(SupervisorInvalidInput): supervisor.update_settings(enabled=1, interval_seconds=30, expected_revision=2)
    for extra in [{"prompt": "never stored"}, {"label": "api_key=must-not-persist"}, {"label": "bad\nlabel"},
                  {"observed_at": "2026-01-01T00:00:00"}, {"execution_returned": "true"},
                  {"source_ref": {"id": "a", "revision": 1, "sha256": "a" * 64, "data_url": "secret"}}]:
        with pytest.raises((ValidationError, ValueError)): TaskObservation.model_validate({**observation(), **extra})


def test_settings_cas_serializes_real_independent_sqlite_writers(tmp_path):
    a, b = GlobalSupervisor(tmp_path, Collector()), GlobalSupervisor(tmp_path, Collector())
    def write(owner, interval):
        try: return owner.update_settings(enabled=True, interval_seconds=interval, expected_revision=1)
        except SupervisorConflict: return "CONFLICT"
    with ThreadPoolExecutor(2) as pool: results = list(pool.map(lambda args: write(*args), [(a, 7), (b, 11)]))
    check(sum(value == "CONFLICT" for value in results) == 1)
    check(a.settings()["revision"] == 2 and a.settings() == b.settings())


def test_independent_task_outcomes_and_validation_never_become_one_global_success(tmp_path):
    async def run():
        collect = Collector([observation("a", "RETURNED", execution_returned=True, validation_status="NOT_RUN"),
                             observation("b", "RUNNING", validation_status="PASSED"),
                             observation("c", "RUNNING", wait_reason="USER_INPUT"), observation("d", "CANCEL_REQUESTED")])
        supervisor = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000000)
        snapshot = await supervisor.poll_once()
        tasks = {v["key"]["object_id"]: v for v in snapshot["tasks"]}
        check(tasks["a"]["classification"] == "EXECUTION_RETURNED" and tasks["a"]["validation_status"] == "NOT_RUN")
        check(tasks["b"]["classification"] == "RUNNING" and tasks["b"]["validation_status"] == "PASSED")
        check(tasks["c"]["classification"] == "WAITING_USER" and tasks["d"]["classification"] == "CANCEL_REQUESTED")
        check(not next(v for v in snapshot["alerts"] if v["key"]["object_id"] == "d")["terminal"])
        check(snapshot["coverage"] == "ONLY_COLLECTED_EXISTING_TASKS")
        await supervisor.close()
    asyncio.run(run())


def test_repeat_polls_do_not_duplicate_alerts_or_change_notifications_and_ack_is_exact(tmp_path):
    async def run():
        collect, changes = Collector([observation()]), []
        supervisor = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000000, on_change=lambda value: changes.append(value))
        first = await supervisor.poll_once()
        alert = first["alerts"][0]
        check(alert["alert_ref"] == {key: alert[key] for key in ("id", "revision", "sha256")})
        collect.values[0]["observed_at"] = datetime.fromtimestamp(1700000001, timezone.utc).isoformat()
        second = await supervisor.poll_once()
        check(second["alerts_total"] == 1 and len(changes) == 1)
        with pytest.raises(SupervisorConflict): supervisor.ack({**alert["alert_ref"], "sha256": "b" * 64})
        ack = supervisor.ack(alert["alert_ref"])
        check(ack["acknowledged"] and supervisor.ack(alert["alert_ref"]) == ack)
        await supervisor.poll_once()
        restored = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000002).snapshot()
        check(restored["alerts"][0]["acknowledged"] and restored["alerts_total"] == 1)
        await supervisor.close()
    asyncio.run(run())


def test_restart_marks_only_old_active_observations_needs_check_until_collect_confirms(tmp_path):
    async def run():
        collect = Collector([observation("running"), observation("returned", "RETURNED", execution_returned=True)])
        original = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000000)
        await original.poll_once()
        await original.close()
        reopened = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000001)
        tasks = {item["key"]["object_id"]: item for item in reopened.snapshot()["tasks"]}
        check(tasks["running"]["classification"] == "NEEDS_CHECK" and tasks["running"]["last_classification"] == "RUNNING")
        check(tasks["running"]["observation_status"] == "NEEDS_CHECK_AFTER_RESTART")
        check(tasks["returned"]["classification"] == "EXECUTION_RETURNED")
        current = await reopened.poll_once()
        check(next(item for item in current["tasks"] if item["key"]["object_id"] == "running")["classification"] == "RUNNING")
        check(current["alerts_total"] == 2, "restart and reobservation do not duplicate historical state alerts")
        await reopened.close()
    asyncio.run(run())


def test_collect_failure_is_observer_unavailable_not_every_task_failed_and_deduplicates(tmp_path):
    async def run():
        now = [1700000000]
        collect = Collector([observation()])
        supervisor = GlobalSupervisor(tmp_path, collect, clock=lambda: now[0])
        await supervisor.poll_once()
        collect.failure = RuntimeError("secret credential=never-persist")
        now[0] += 500
        failed = await supervisor.poll_once()
        repeated = await supervisor.poll_once()
        check(failed["observer"]["status"] == "OBSERVATION_UNAVAILABLE" and repeated["alerts_total"] == 2)
        check(repeated["tasks"][0]["classification"] == "RUNNING" and repeated["tasks"][0]["observation_status"] == "STALE")
        check("never-persist" not in json.dumps(repeated))
        collect.failure = None
        collect.values = []
        recovered = await supervisor.poll_once()
        check(recovered["observer"]["status"] == "READY" and recovered["tasks"][0]["classification"] == "RUNNING")
        await supervisor.close()
    asyncio.run(run())


def test_stale_is_not_timeout_but_explicit_upstream_timeout_is(tmp_path):
    async def run():
        collect = Collector([observation("old", fresh_for_seconds=5),
            observation("timeout", "ERROR", actual_error_code="MODEL_TIMEOUT"), observation("lost", "DISCONNECTED")])
        supervisor = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000010)
        tasks = {value["key"]["object_id"]: value for value in (await supervisor.poll_once())["tasks"]}
        check(tasks["old"]["observation_status"] == "STALE" and tasks["old"]["classification"] == "RUNNING")
        check(tasks["timeout"]["classification"] == "TIMED_OUT" and tasks["lost"]["classification"] == "NEEDS_CHECK")
        await supervisor.close()
    asyncio.run(run())


def test_partial_source_failure_keeps_two_healthy_updates_and_recovers_without_poll_chatter(tmp_path):
    async def run():
        now, changes = [1700000000], []
        result = {"observations": [observation("a"), observation("b"), observation("lost")], "unavailable_codes": []}
        async def collect(): return deepcopy(result)
        supervisor = GlobalSupervisor(tmp_path, collect, clock=lambda: now[0], on_change=lambda value: changes.append(value))
        await supervisor.poll_once()
        now[0] += 500
        result.update(observations=[observation("a", "RETURNED", epoch=now[0], execution_returned=True),
                                   observation("b", "RUNNING", epoch=now[0], wait_reason="USER_INPUT")],
                      unavailable_codes=["DISCOVERY_RESULT_INTEGRITY_FAILED", "SOURCE_UNAVAILABLE"])
        partial = await supervisor.poll_once()
        tasks = {value["key"]["object_id"]: value for value in partial["tasks"]}
        check(partial["observer"]["status"] == "PARTIAL")
        check(partial["observer"]["unavailable_codes"] == ["DISCOVERY_RESULT_INTEGRITY_FAILED", "SOURCE_UNAVAILABLE"])
        check(tasks["a"]["classification"] == "EXECUTION_RETURNED" and tasks["b"]["classification"] == "WAITING_USER")
        check(tasks["lost"]["classification"] == "RUNNING" and tasks["lost"]["observation_status"] == "STALE")
        check(sum(value["kind"] == "OBSERVER_STATE_CHANGED" for value in partial["alerts"]) == 1)
        result["unavailable_codes"].reverse()
        repeated = await supervisor.poll_once()
        check(repeated["alerts_total"] == partial["alerts_total"] and len(changes) == 2)
        result["unavailable_codes"] = []
        recovered = await supervisor.poll_once()
        check(recovered["observer"]["status"] == "READY" and recovered["observer"]["unavailable_codes"] == [])
        check(recovered["alerts_total"] == partial["alerts_total"] and len(changes) == 3)
        await supervisor.close()
        reopened = GlobalSupervisor(tmp_path, collect, clock=lambda: now[0])
        check(next(value for value in reopened.snapshot()["tasks"] if value["key"]["object_id"] == "a")["classification"] == "EXECUTION_RETURNED")
        await reopened.close()
    asyncio.run(run())


def test_unknown_source_codes_are_not_persisted_and_invalid_partial_batch_is_atomic(tmp_path):
    async def run():
        result = {"observations": [observation()], "unavailable_codes": []}
        async def collect(): return deepcopy(result)
        supervisor = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000000)
        await supervisor.poll_once()
        result.update(observations=[observation(status="FAILED")], unavailable_codes=["api_key=secret-not-stored"])
        snapshot = await supervisor.poll_once()
        check(snapshot["tasks"][0]["classification"] == "RUNNING")
        check(snapshot["observer"]["status"] == "OBSERVATION_UNAVAILABLE" and snapshot["observer"]["unavailable_codes"] == [])
        check("secret-not-stored" not in json.dumps(snapshot))
        await supervisor.close()
    asyncio.run(run())


def test_invalid_collect_batch_is_atomic_and_does_not_partially_change_tasks(tmp_path):
    async def run():
        collect = Collector([observation()])
        supervisor = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000000)
        await supervisor.poll_once()
        collect.values = [observation(status="FAILED"), {**observation("invalid"), "prompt": "forbidden"}]
        snapshot = await supervisor.poll_once()
        check(snapshot["tasks"][0]["classification"] == "RUNNING" and len(snapshot["tasks"]) == 1)
        check(snapshot["observer"]["status"] == "OBSERVATION_UNAVAILABLE")
        await supervisor.close()
    asyncio.run(run())


def test_notification_failure_preserves_known_returned_outcome(tmp_path):
    async def run():
        def broken(_snapshot): raise RuntimeError("UI destroyed")
        supervisor = GlobalSupervisor(tmp_path, Collector([observation(status="RETURNED", execution_returned=True)]),
                                      clock=lambda: 1700000000, on_change=broken)
        snapshot = await supervisor.poll_once()
        check(snapshot["observer"]["status"] == "READY" and snapshot["observer"]["notification_status"] == "UNAVAILABLE")
        check(snapshot["tasks"][0]["classification"] == "EXECUTION_RETURNED" and snapshot["alerts_total"] == 1)
        await supervisor.close()
    asyncio.run(run())


def test_sqlite_checksums_fail_closed_without_overwriting_corruption(tmp_path):
    supervisor = GlobalSupervisor(tmp_path, Collector())
    with sqlite3.connect(supervisor.path) as connection:
        connection.execute("UPDATE records SET payload=? WHERE kind='settings'", ('{"enabled":false}',))
    with pytest.raises(SupervisorIntegrityError): supervisor.settings()
    with pytest.raises(SupervisorIntegrityError): supervisor.update_settings(enabled=True, interval_seconds=30, expected_revision=1)
    with sqlite3.connect(supervisor.path) as connection:
        check(connection.execute("SELECT payload FROM records WHERE kind='settings'").fetchone()[0] == '{"enabled":false}')


def test_actual_async_start_disable_interval_wake_periodic_poll_and_close(tmp_path):
    async def run():
        count = 0
        changed = asyncio.Event()
        async def collect():
            nonlocal count
            count += 1
            changed.set()
            return []
        supervisor = GlobalSupervisor(tmp_path, collect)
        supervisor.update_settings(enabled=False, interval_seconds=30, expected_revision=1)
        await supervisor.start()
        await supervisor.start()
        await asyncio.sleep(.03)
        check(count == 0)
        supervisor.update_settings(enabled=True, interval_seconds=30, expected_revision=2)
        await asyncio.wait_for(changed.wait(), .7)
        check(count == 1)
        changed.clear()
        supervisor.update_settings(enabled=True, interval_seconds=5, expected_revision=3)
        await asyncio.wait_for(changed.wait(), .7)
        check(count == 2, "changing interval wakes the current long wait")
        changed.clear()
        began = time.monotonic()
        await asyncio.wait_for(changed.wait(), 6)
        check(count == 3 and time.monotonic() - began >= 4.8, "actual minimum interval timer must poll")
        supervisor.update_settings(enabled=False, interval_seconds=5, expected_revision=4)
        await asyncio.sleep(.03)
        before = count
        await supervisor.close()
        await supervisor.close()
        await asyncio.sleep(.02)
        check(count == before and supervisor.snapshot()["observer"]["status"] == "CLOSED")
        check(not [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()])
        with pytest.raises(SupervisorConflict): await supervisor.start()
    asyncio.run(run())


def test_close_cancels_inflight_background_and_manual_collect_without_leaving_tasks(tmp_path):
    async def run():
        entered = asyncio.Event()
        cancelled = 0
        async def collect():
            nonlocal cancelled
            entered.set()
            try: await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled += 1
                raise
        supervisor = GlobalSupervisor(tmp_path, collect)
        manual = asyncio.create_task(supervisor.poll_once())
        await entered.wait()
        await supervisor.start()
        await asyncio.sleep(.01)
        await supervisor.close()
        check(manual.cancelled() and cancelled == 1)
        check(not [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()])
    asyncio.run(run())


async def until(predicate, timeout=1.5):
    async def wait():
        while not predicate(): await asyncio.sleep(.005)
    await asyncio.wait_for(wait(), timeout)


def test_owner_wake_is_only_a_hint_and_debounces_without_changing_owner_state(tmp_path):
    async def run():
        collect = Collector([observation("actual")])
        supervisor = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000000)
        before = supervisor.change_sequence()
        check(supervisor.wake("OWNER_CHANGED", observation("untrusted")["key"]) == "QUEUED")
        check(supervisor.wake("SOURCE_LOST") == "COALESCED")
        check(collect.calls == 0 and supervisor.change_sequence() == before and not supervisor.snapshot()["tasks"])
        for reason in ["", "owner changed", "PASSWORD=hidden", "A" * 65, 123]:
            with pytest.raises(SupervisorInvalidInput): supervisor.wake(reason)
        for key in [{**observation()["key"], "source_status": "RETURNED"}, {"kind": "task"}, "task"]:
            with pytest.raises(SupervisorInvalidInput): supervisor.wake("OWNER_CHANGED", key)
        await supervisor.start()
        await until(lambda: collect.calls == 1 and bool(supervisor.snapshot()["tasks"]))
        check(supervisor.snapshot()["tasks"][0]["key"]["object_id"] == "actual")
        collect.values = [observation("actual", "RETURNED", execution_returned=True)]
        began = time.monotonic()
        check(supervisor.wake("OWNER_CHANGED") == "QUEUED")
        for _ in range(2000): check(supervisor.wake("OWNER_CHANGED") == "COALESCED")
        await until(lambda: collect.calls == 2)
        check(.075 <= time.monotonic() - began < 1.5, "fixed short debounce must not wait for 30s fallback")
        await asyncio.sleep(.15)
        snapshot = supervisor.snapshot()
        check(collect.calls == 2 and snapshot["tasks"][0]["classification"] == "EXECUTION_RETURNED")
        check(snapshot["tasks"][0]["validation_status"] == "NOT_OBSERVED")
        check(not snapshot["settings"]["notifications_enabled"] and snapshot["alerts_total"] == 2)
        await supervisor.close()
    asyncio.run(run())


def test_real_cross_thread_storm_during_collect_is_one_follow_up_and_never_overlaps(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls, active, maximum = 0, 0, 0
        async def collect():
            nonlocal calls, active, maximum
            calls += 1
            active += 1
            maximum = max(maximum, active)
            try:
                if calls == 1:
                    entered.set()
                    await release.wait()
                await asyncio.sleep(.01)
                return [observation("owned", "RETURNED" if calls > 1 else "RUNNING")]
            finally: active -= 1
        supervisor = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000000)
        await supervisor.start()
        await entered.wait()
        def storm():
            with ThreadPoolExecutor(8) as pool:
                return list(pool.map(lambda _: supervisor.wake("OWNER_CHANGED"), range(4000)))
        answers = await asyncio.to_thread(storm)
        check(answers.count("QUEUED") == 1 and answers.count("COALESCED") == 3999)
        release.set()
        await until(lambda: calls == 2 and active == 0)
        await asyncio.sleep(.15)
        check(calls == 2 and maximum == 1 and not supervisor._signal_scheduled)
        await supervisor.close()
    asyncio.run(run())


def test_fixed_debounce_is_not_postponed_by_a_continuous_event_stream(tmp_path):
    async def run():
        starts, sent = [], 0
        async def collect():
            starts.append((time.monotonic(), sent))
            return []
        supervisor = GlobalSupervisor(tmp_path, collect)
        await supervisor.start()
        await until(lambda: len(starts) == 1)
        began = time.monotonic()
        for _ in range(24):
            sent += 1
            supervisor.wake("OWNER_CHANGED")
            await asyncio.sleep(.01)
            if len(starts) >= 2: break
        # Measure the collector entry, not the SQLite fsync before this coroutine
        # resumes. A trailing debounce would wait until all 24 hints had stopped.
        check(len(starts) >= 2 and 0 < starts[1][1] < 24 and starts[1][0] - began >= .075,
              "ongoing hints must not reset a trailing debounce indefinitely")
        await supervisor.close()
    asyncio.run(run())


def test_disable_drops_pending_hints_and_reenable_rereads_current_owner(tmp_path):
    async def run():
        collect = Collector([observation("owned")])
        supervisor = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000000)
        await supervisor.start()
        await until(lambda: collect.calls == 1)
        supervisor.wake("OWNER_CHANGED")
        saved = supervisor.update_settings(enabled=False, interval_seconds=30, expected_revision=1)
        for _ in range(100): check(supervisor.wake("OWNER_CHANGED") == "DISABLED")
        await asyncio.sleep(.16)
        check(collect.calls == 1 and supervisor.snapshot()["observer"]["status"] == "DISABLED")
        collect.values = [observation("owned", "RETURNED", execution_returned=True)]
        supervisor.update_settings(enabled=True, interval_seconds=30, expected_revision=saved["revision"])
        await until(lambda: collect.calls == 2)
        check(supervisor.snapshot()["tasks"][0]["classification"] == "EXECUTION_RETURNED")
        await supervisor.close()
    asyncio.run(run())


def test_queued_automatic_poll_checks_disable_after_real_manual_lock_wait(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def collect():
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return []
        supervisor = GlobalSupervisor(tmp_path, collect)
        manual = asyncio.create_task(supervisor.poll_once())
        await entered.wait()
        await supervisor.start()
        await asyncio.sleep(.02)
        supervisor.update_settings(enabled=False, interval_seconds=30, expected_revision=1)
        release.set()
        await manual
        await asyncio.sleep(.15)
        check(calls == 1, "an automatic waiter cannot begin a collector after disable committed")
        await supervisor.close()
    asyncio.run(run())


def test_source_loss_and_recovery_wakes_reread_healthy_sources_without_invented_failures(tmp_path):
    async def run():
        owner = {"observations": [observation("missing"), observation("healthy")], "unavailable_codes": []}
        calls = 0
        async def collect():
            nonlocal calls
            calls += 1
            return deepcopy(owner)
        supervisor = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000000)
        await supervisor.start()
        await until(lambda: calls == 1)
        owner.update(observations=[observation("healthy", "RETURNED", execution_returned=True)],
                     unavailable_codes=["ORIGINAL_SESSION_OBSERVATION_UNAVAILABLE"])
        supervisor.wake("SOURCE_LOST", observation("missing")["key"])
        await until(lambda: calls == 2)
        snapshot = supervisor.snapshot()
        tasks = {item["key"]["object_id"]: item for item in snapshot["tasks"]}
        check(snapshot["observer"]["status"] == "PARTIAL" and tasks["missing"]["classification"] == "RUNNING")
        check(tasks["healthy"]["classification"] == "EXECUTION_RETURNED")
        owner.update(observations=[observation("missing", "WAITING_USER", wait_reason="PERMISSION")], unavailable_codes=[])
        supervisor.wake("OWNER_CHANGED")
        await until(lambda: calls == 3)
        check(supervisor.snapshot()["observer"]["status"] == "READY")
        check(next(t for t in supervisor.snapshot()["tasks"] if t["key"]["object_id"] == "missing")["classification"] == "WAITING_USER")
        await supervisor.close()
    asyncio.run(run())


def test_change_cursor_broadcasts_commits_and_idempotent_writes_do_not_manufacture_changes(tmp_path):
    async def run():
        collect = Collector([observation()])
        supervisor = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000000)
        cursor = supervisor.change_sequence()
        waiters = [asyncio.create_task(supervisor.wait_for_change(cursor, 1)) for _ in range(4)]
        await asyncio.sleep(0)
        supervisor.update_settings(enabled=False, interval_seconds=30, expected_revision=1)
        changed = await asyncio.gather(*waiters)
        check(len(set(changed)) == 1 and changed[0] > cursor and collect.calls == 0)
        cursor = changed[0]
        supervisor.update_settings(enabled=False, interval_seconds=30, expected_revision=2)
        check(supervisor.change_sequence() == cursor)
        snapshot = await supervisor.poll_once()  # explicit local read remains possible while disabled
        check(snapshot["change_sequence"] > cursor and collect.calls == 1)
        cursor = snapshot["change_sequence"]
        waiter = asyncio.create_task(supervisor.wait_for_change(cursor, 1))
        await asyncio.sleep(0)
        ref = snapshot["alerts"][0]["alert_ref"]
        supervisor.ack(ref)
        check(await waiter > cursor)
        cursor = supervisor.change_sequence()
        supervisor.ack(ref)
        check(supervisor.change_sequence() == cursor)
        began = time.monotonic()
        check(await supervisor.wait_for_change(cursor, .05) == cursor and time.monotonic() - began >= .04)
        check(await supervisor.wait_for_change(cursor + 100, 1) == cursor)
        check(not supervisor._change_waiters and collect.calls == 1)
        for after, timeout in [(True, 1), (-1, 1), (0, True), (0, 0), (0, 26), (0, float("inf")), (0, float("nan"))]:
            with pytest.raises(SupervisorInvalidInput): await supervisor.wait_for_change(after, timeout)
        await supervisor.close()
    asyncio.run(run())


def test_restart_cursor_increases_without_replaying_dirty_state_and_wait_cancellation_cleans_up(tmp_path):
    async def run():
        collect = Collector([observation()])
        first = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000000)
        await first.poll_once()
        first.wake("OWNER_CHANGED")
        cursor = first.change_sequence()
        await first.close()
        second = GlobalSupervisor(tmp_path, collect, clock=lambda: 1700000000)
        check(second.change_sequence() > cursor and second._dirty_deadline is None)
        check(second.snapshot()["tasks"][0]["observation_status"] == "NEEDS_CHECK_AFTER_RESTART")
        check(await second.wait_for_change(cursor, 1) == second.change_sequence() and collect.calls == 1)
        waiter = asyncio.create_task(second.wait_for_change(second.change_sequence(), 25))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError): await waiter
        check(not second._change_waiters)
        await second.start()
        await until(lambda: collect.calls == 2)
        check(second.snapshot()["tasks"][0]["classification"] == "RUNNING")
        await second.close()
    asyncio.run(run())


def test_close_cancels_active_event_poll_releases_all_readers_and_rejects_late_hints(tmp_path):
    async def run():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        calls = 0
        async def collect():
            nonlocal calls
            calls += 1
            if calls == 1: return []
            entered.set()
            try: await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        supervisor = GlobalSupervisor(tmp_path, collect)
        await supervisor.start()
        await until(lambda: calls == 1)
        supervisor.wake("OWNER_CHANGED")
        await entered.wait()
        cursor = supervisor.change_sequence()
        readers = [asyncio.create_task(supervisor.wait_for_change(cursor, 25)) for _ in range(3)]
        await asyncio.sleep(0)
        await supervisor.close()
        check(cancelled.is_set() and all(value > cursor for value in await asyncio.gather(*readers)))
        check(supervisor.wake("OWNER_CHANGED") == "CLOSED")
        cursor = supervisor.change_sequence()
        await supervisor.close()
        check(supervisor.change_sequence() == cursor and not supervisor._change_waiters)
        check(not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()])
    asyncio.run(run())


def test_corrupt_change_metadata_fails_closed_but_still_joins_collector_on_close(tmp_path):
    async def run():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def collect():
            entered.set()
            try: await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        supervisor = GlobalSupervisor(tmp_path, collect)
        await supervisor.start()
        await entered.wait()
        with sqlite3.connect(supervisor.path) as connection:
            connection.execute("UPDATE records SET payload=? WHERE kind='change'", ('{"sequence":-1}',))
        with pytest.raises(SupervisorIntegrityError): await supervisor.close()
        check(cancelled.is_set() and supervisor._worker is None)
        with sqlite3.connect(supervisor.path) as connection:
            check(connection.execute("SELECT payload FROM records WHERE kind='change'").fetchone()[0] == '{"sequence":-1}')
        check(not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()])
    asyncio.run(run())
