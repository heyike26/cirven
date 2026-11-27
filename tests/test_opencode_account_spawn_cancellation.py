"""A real original process must remain owned after repeated waiter cancellation."""
import asyncio

import pytest

from packages.adapters.opencode_account import OpenCodeAccount
from test_opencode_account import check, evidence, scene


def test_repeated_cancel_during_real_late_spawn_cannot_report_false_closed(scene, monkeypatch):
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        actual_spawn = asyncio.create_subprocess_exec
        spawned, release = asyncio.Event(), asyncio.Event()
        processes, spawn_tasks = [], []
        async def delayed_actual_spawn(*args, **options):
            # Execute the unchanged original CLI. Only delivery of its actual
            # Process to the caller is gated, exposing the cancellation race.
            process = await actual_spawn(*args, **options)
            processes.append(process)
            spawn_tasks.append(asyncio.current_task())
            spawned.set()
            await release.wait()
            return process
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed_actual_spawn)
        waiting = asyncio.create_task(account.get())
        closing = None
        try:
            await asyncio.wait_for(spawned.wait(), 10)
            check(len(processes) == 1 and processes[0].returncode is None)
            waiting.cancel(); await asyncio.sleep(.02)
            waiting.cancel(); await asyncio.sleep(.02)
            waiter_pending, startup_locked = not waiting.done(), account._startup_lock.locked()
            closing = asyncio.create_task(account.close())
            await asyncio.sleep(.04)
            close_waited = not closing.done()
            release.set()
            with pytest.raises(asyncio.CancelledError): await waiting
            closed = await closing
            evidence(scene, 'actual-late-spawn-repeated-cancel', {
                'original_cli_process_count': len(processes), 'real_spawn_delayed_return': True,
                'waiter_cancellation_count': 2, 'waiter_pending_until_actual_spawn_return': waiter_pending,
                'startup_lock_held_until_process_observed': startup_locked,
                'close_waited_for_original_process': close_waited, 'close_status': closed['status'],
                'original_process_returncode_at_close': processes[0].returncode,
                'original_spawn_task_done': all(task.done() for task in spawn_tasks),
                'oauth_or_model_requests': 0})
            check(waiter_pending and startup_locked,
                'Repeated cancellation abandoned a still-running original spawn')
            check(close_waited, 'Close completed before the exact original Process was recorded')
            check(closed['status'] == 'CLOSED' and account.active == {})
            check(len(processes) == 1 and processes[0].returncode is not None,
                'CLOSED receipt left the real original process alive')
            check(all(task.done() for task in spawn_tasks))
            check(not account._startup_lock.locked())
        finally:
            release.set()
            await asyncio.gather(waiting, *spawn_tasks, return_exceptions=True)
            await account.close()
            if closing: await closing
            # On the deliberately run pre-fix negative, cleanup uses only this
            # test's retained actual Process; never a user PID or global kill.
            for process in processes:
                if process.returncode is None:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), 10)
            check(all(process.returncode is not None for process in processes))
            evidence(scene, 'actual-late-spawn-test-owned-cleanup', {
                'all_retained_original_processes_stopped': True,
                'retained_original_process_returncodes': [process.returncode for process in processes],
                'cleanup_scope': 'ONLY_TEST_RETAINED_PROCESS_OBJECTS'})
    asyncio.run(run())
