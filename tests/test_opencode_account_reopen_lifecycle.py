"""Live original OAuth URL cleanup; no real account login or model request."""
import asyncio
import time

import pytest

from packages.adapters.opencode_account import OpenCodeAccount
from packages.domain.store import Conflict
from test_opencode_account import check, evidence, fixture_owned_oauth, observe_native, scene


async def start_waiting_callback(account, operation):
    observed = observe_native(account)
    begun = await account.login(operation)
    ident = begun['attempt_id']
    await account.complete(ident)
    deadline = time.monotonic() + 10
    while not any(item['path'].endswith('/callback') for item in observed) and time.monotonic() < deadline:
        await asyncio.sleep(.01)
    check(sum(item['path'].endswith('/callback') for item in observed) == 1)
    await asyncio.sleep(.2)
    check(not account.tasks[ident].done())
    check(account.login_target(ident)['login_url'] == begun['login_url'])
    return ident, observed


def test_actual_close_with_live_unexpired_callback_clears_its_reopen_url(scene):
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        try:
            ident, observed = await start_waiting_callback(account, 'actual-live-target-close')
            check(account._attempt(ident)['deadline'] > time.time())
            check(account._login_target is not None)
            process = account.active['account']['process']
            check((await account.close())['status'] == 'CLOSED')
            check(account._login_target is None and process.returncode is not None)
            check(account.tasks[ident].done())
            with pytest.raises(Conflict): account.login_target(ident)
            check(sum(item['path'].endswith('/authorize') for item in observed) == 1)
            check(sum(item['path'].endswith('/callback') for item in observed) == 1)
            evidence(scene, 'actual-live-target-close', {'native_requests': observed,
                'url_live_immediately_before_close': True, 'unexpired_before_close': True,
                'memory_login_url_cleared': True, 'original_process_stopped': True,
                'callback_task_done': True, 'closed_reopen_rejected': True})
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())


def test_actual_authenticated_type_observation_cannot_complete_pending_reopen(scene):
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        try:
            ident, observed = await start_waiting_callback(account, 'actual-typed-auth-target-clear')
            # Only the owned credential entry is an explicit opaque type
            # fixture. Catalog disposal/observation still use the original CLI.
            # This does not assert a successful OAuth exchange or Pro access.
            fixture_owned_oauth(account)
            check(await account._native('POST', '/instance/dispose') is True)
            check(account._login_target is not None and not account.tasks[ident].done())
            state = await account.get()
            check(state['status'] == 'WAITING_USER' and state['phase'] == 'COMPLETING')
            check(state['authentication_observed_at'] and state['model_access'] == 'NOT_VALIDATED')
            check('login_url' not in state and account.login_target(ident)['login_url'])
            check(account._attempt(ident)['status'] == 'COMPLETING' and not account.tasks[ident].done())
            # A credential in the private store is independent of this callback's
            # result. Only the exact pending attempt may clear its live target.
            repeated = await account.complete(ident)
            check(repeated['phase'] == 'COMPLETING' and len(account.tasks) == 1)
            cancelled = await account.cancel(ident)
            check(account._attempt(ident)['status'] == 'CANCELLED' and account.tasks[ident].done())
            check(account._login_target is None and 'login_url' not in cancelled)
            with pytest.raises(Conflict): account.login_target(ident)
            check(sum(item['path'].endswith('/authorize') for item in observed) == 1)
            check(sum(item['path'].endswith('/callback') for item in observed) == 1)
            evidence(scene, 'actual-auth-type-pending-target', {'native_requests': observed,
                'credential_source': 'OWNED_OPAQUE_TYPE_FIXTURE_NOT_REAL_LOGIN',
                'catalog_observed_while_original_callback_pending': True, 'target_cleared_by_cancellation': True,
                'model_access': state['model_access'], 'actual_OAuth_success': 'NOT_RUN'})
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())
