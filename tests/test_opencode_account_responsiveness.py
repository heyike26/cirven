"""Account cold preparation must leave real localhost status requests responsive.

The bounded gate represents slow disk/antivirus scheduling. Archive checking,
installation, original CLI startup and provider HTTP are not replaced.
"""
import asyncio
import os
import secrets
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from apps.api.config import Settings
from apps.api.main import create_app
from packages.adapters.opencode_account import OpenCodeAccount
from packages.adapters.opencode_profile import CLI_SHA, binary_hash
from packages.domain.provider_settings import ProviderSettings
from packages.domain.store import Conflict, Store
from test_opencode_account import CLI, ROOT, check, evidence, observe_native, scene


async def thread_started(event):
    deadline = time.monotonic() + 5
    while not event.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(.005)
    check(event.is_set(), 'Preparation did not reach the bounded slow-disk gate')


def test_actual_cold_archive_and_single_owner_leave_tcp_status_and_heartbeat_responsive(tmp_path, monkeypatch):
    async def run():
        data = tmp_path / 'responsive-data'; data.mkdir()
        host_token = secrets.token_hex(32)
        monkeypatch.setenv('EEAS_DESKTOP_HOST_TOKEN', host_token)
        monkeypatch.setenv('EEAS_DESKTOP_HOST_PID', str(os.getpid()))
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        settings = Settings(data, port=port, worker_enabled=False)
        app = create_app(settings)
        account = app.state.subscription_account
        providers = app.state.provider_settings
        original_cli = providers._cli
        started, release = threading.Event(), threading.Event()
        preparation_threads = []
        def slow_real_cli():
            preparation_threads.append(threading.get_ident())
            started.set()
            check(release.wait(4), 'Status requests did not release slow preparation in time')
            return original_cli()
        monkeypatch.setattr(providers, '_cli', slow_real_cli)
        observed = observe_native(account)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
            log_level='error', access_log=False))
        serving = asyncio.create_task(server.serve())
        beat_times = []
        beating = True
        async def heartbeat():
            while beating:
                beat_times.append(time.monotonic())
                await asyncio.sleep(.01)
        beat = asyncio.create_task(heartbeat())
        pending = []
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                if serving.done(): await serving
                await asyncio.sleep(.01)
            check(server.started)
            async with httpx.AsyncClient(base_url=settings.origin, trust_env=False, timeout=90) as client:
                boot = await client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
                check(boot.status_code == 200)
                client.headers.update({'Origin': settings.origin, 'X-EEAS-Desktop-Host': host_token})
                pending = [asyncio.create_task(client.get('/api/subscription/account')) for _ in range(2)]
                await thread_started(started)
                before = len(beat_times)
                observed_at = time.monotonic()
                responses = await asyncio.wait_for(asyncio.gather(*(
                    client.get(path) for path in ('/health', '/api/desktop-host/collaboration/get',
                        '/api/desktop-host/context/status'))), 1.5)
                elapsed = time.monotonic() - observed_at
                await asyncio.sleep(.06)
                check(all(response.status_code == 200 for response in responses))
                check(not release.is_set() and not any(task.done() for task in pending),
                    'Status requests ran only after preparation finished')
                check(len(beat_times) - before >= 3, 'API event loop heartbeat stopped during preparation')
                gated_ticks = len(beat_times) - before
                check(preparation_threads == [preparation_threads[0]])
                check(preparation_threads[0] != threading.get_ident(), 'Large preparation ran on the API loop')
                release.set()
                replies = await asyncio.wait_for(asyncio.gather(*pending), 90)
                check(all(reply.status_code == 200 and reply.json()['status'] == 'SIGNED_OUT' for reply in replies))
                check(len(preparation_threads) == 1, 'Concurrent status reads created another account owner')
                process = account.active['account']['process']
                installed = providers.path('runtime', '1.19.0', 'opencode')
                check(binary_hash(installed) == CLI_SHA['posix'])
                check(all(item['method'] == 'GET' for item in observed))
                evidence({'tmp': tmp_path}, 'actual-cold-preparation-responsive', {
                    'status_routes': [response.request.url.path for response in responses],
                    'status_http_codes': [response.status_code for response in responses],
                    'status_elapsed_seconds_while_preparation_gated': elapsed,
                    'heartbeat_ticks_while_preparation_gated': gated_ticks,
                    'preparation_on_separate_thread': True, 'concurrent_account_reads': 2,
                    'actual_archive_installations': 1, 'original_cli_process_count': 1,
                    'installed_original_cli_sha256': binary_hash(installed),
                    'native_requests': observed, 'oauth_authorization_dispatched': False})
        finally:
            release.set()
            for task in pending:
                if not task.done(): task.cancel()
            if pending: await asyncio.gather(*pending, return_exceptions=True)
            beating = False
            await beat
            server.should_exit = True
            await asyncio.wait_for(serving, 20)
        check(account.active == {} and process.returncode is not None)
    asyncio.run(run())


def test_repeated_cancel_settles_preparation_before_close_or_another_owner(scene, monkeypatch):
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def slow_checked_cli():
            calls.append(threading.get_ident()); started.set()
            try:
                check(release.wait(4), 'Cancellation test did not release preparation')
                check(binary_hash(CLI) == CLI_SHA['posix'])
                return CLI
            finally: finished.set()
        monkeypatch.setattr(scene['providers'], '_cli', slow_checked_cli)
        waiting = asyncio.create_task(account.get())
        second = closing = None
        try:
            await thread_started(started)
            waiting.cancel(); await asyncio.sleep(.02)
            waiting.cancel(); await asyncio.sleep(.02)
            check(not waiting.done() and account._startup_lock.locked())
            second = asyncio.create_task(account.get())
            closing = asyncio.create_task(account.close())
            await asyncio.sleep(.04)
            check(not closing.done() and not second.done())
            check(account.active == {} and not finished.is_set())
            release.set()
            with pytest.raises(asyncio.CancelledError): await waiting
            with pytest.raises(Conflict): await second
            check((await closing)['status'] == 'CLOSED')
            check(finished.is_set() and len(calls) == 1 and account.active == {})
            check(not account._startup_lock.locked())
            check(not list(account.root.glob('controllers/*/native-stdout.bin')))
            evidence(scene, 'cancelled-preparation-close-fence', {
                'repeated_cancellation_count': 2, 'preparation_settled_before_close': True,
                'startup_lock_retained_until_preparation_settled': True,
                'late_original_process_spawned': False, 'second_owner_spawned': False,
                'close_status': 'CLOSED', 'native_requests': []})
        finally:
            release.set()
            await asyncio.gather(waiting, *([second] if second else []), return_exceptions=True)
            check((await account.close())['status'] == 'CLOSED')
            if closing: await closing
    asyncio.run(run())


def test_actual_integrity_failure_still_rejects_before_original_process_start(tmp_path):
    async def run():
        data = tmp_path / 'integrity-data'; data.mkdir()
        providers = ProviderSettings(Store(data / 'store.sqlite3'), data, ROOT)
        target = providers.path('runtime', '1.19.0', 'opencode')
        target.parent.mkdir(parents=True)
        target.write_bytes(b'corrupted-original-runtime-integrity-fixture')
        account = OpenCodeAccount(providers, data)
        try:
            with pytest.raises(Conflict, match='Installed OpenCode bytes changed'):
                await account.get()
            check(account.active == {} and not (account.root / 'controllers').exists())
            check(target.read_bytes() == b'corrupted-original-runtime-integrity-fixture')
            evidence({'tmp': tmp_path}, 'threaded-integrity-failure', {
                'actual_binary_integrity_guard_rejected': True,
                'original_process_spawned': False, 'fallback_or_repair_attempted': False})
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())
