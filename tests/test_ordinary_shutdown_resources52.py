"""Actual ordinary TCP/worker lifetimes; no CLI, model or Windows qualification."""
import asyncio
from types import SimpleNamespace
import threading

from apps.api.owner_shutdown import OwnerShutdown
from packages.adapters.opencode_native_message import NativeMessageBridge


def outcome(receipt):
    return next(row for row in receipt['outcomes'] if row['owner_kind'] == 'NATIVE_MESSAGES')


def test_shutdown_retains_real_bridge_handles_after_its_registry_is_removed(tmp_path):
    async def scenario():
        async def callback(_route, value):
            return value

        bridge = NativeMessageBridge(callback, asyncio.get_running_loop())
        worker_ids = await bridge.run_sync(lambda: (threading.get_ident(),))
        retained = bridge.owned_threads
        assert any(t.ident == worker_ids[0] and t.is_alive() for t in retained)
        ordinary = SimpleNamespace(owned_bridges=(bridge,), tasks={})
        calls = []

        async def close():
            calls.append('close')
            await asyncio.to_thread(bridge.close)
            ordinary.owned_bridges = ()

        native = SimpleNamespace(ordinary=ordinary, close=close)
        owner = OwnerShutdown(SimpleNamespace(state=SimpleNamespace(native_messages=native)), tmp_path)
        try:
            receipt = await owner.close()
            row = outcome(receipt)
            assert receipt['status'] == row['status'] == 'CLOSED', receipt
            assert row['captured_thread_count'] >= 2
            assert row['stopped_thread_count'] == row['captured_thread_count']
            assert row['pending_thread_count'] == row['pending_task_count'] == 0
            assert ordinary.owned_bridges == () and not any(t.is_alive() for t in retained)
            assert await owner.close() == receipt and calls == ['close']
        finally:
            if not bridge.close_confirmed:
                await asyncio.to_thread(bridge.close)

    asyncio.run(scenario())


def test_cancelled_awaiter_does_not_hide_running_callback_or_authorize_closed(tmp_path):
    async def scenario():
        async def callback(_route, value):
            return value

        bridge = NativeMessageBridge(callback, asyncio.get_running_loop())
        entered, release = threading.Event(), threading.Event()

        def held():
            entered.set()
            if not release.wait(15):
                raise TimeoutError('Owned test callback was not released')

        task = asyncio.create_task(bridge.run_sync(held))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert any(not future.done() for future in bridge.owned_tasks)
        ordinary = SimpleNamespace(owned_bridges=(bridge,), tasks={})
        calls = []

        async def close():
            calls.append('close')
            await asyncio.to_thread(bridge.close)

        native = SimpleNamespace(ordinary=ordinary, close=close)
        owner = OwnerShutdown(SimpleNamespace(state=SimpleNamespace(native_messages=native)), tmp_path)
        try:
            receipt = await owner.close()
            row = outcome(receipt)
            assert receipt['status'] == row['status'] == 'CLOSE_UNKNOWN', receipt
            assert row['pending_task_count'] >= 1 and row['pending_thread_count'] >= 1
            assert not bridge.close_confirmed and ordinary.owned_bridges == (bridge,)
            release.set()
            await asyncio.to_thread(bridge.close)
            assert bridge.close_confirmed and not any(t.is_alive() for t in bridge.owned_threads)
            assert await owner.close() == receipt and calls == ['close']
        finally:
            release.set()
            if not bridge.close_confirmed:
                await asyncio.to_thread(bridge.close)

    asyncio.run(scenario())
