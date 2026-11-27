"""Real fixed CLI OAuth control; no browser, real token endpoint or paid inference.

Opaque credentials below are explicit owned-store guard fixtures, never a claim
of actual ChatGPT login/entitlement. Native HTTP, PKCE generation and process
lifecycle use the original unmodified CLI.
"""
import asyncio
import json
import os
from pathlib import Path
import time

import pytest

from packages.adapters.opencode_account import (AUTH_MODE, PROTOCOL, OpenCodeAccount,
    SubscriptionProviderProfile, read_account_binding, require_owned_oauth)
from packages.adapters.opencode_profile import CLI_SHA, binary_hash
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.domain.provider_settings import ProviderSettings
from packages.domain.store import Conflict, Store
from packages.domain.tasks import digest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'var/opencode-ui-offline-guard/opencode'


def check(condition, message='Actual account qualification assertion failed'):
    # Also active under python -O.
    if not condition: raise AssertionError(message)


def evidence(scene, name, value):
    directory = os.environ.get('EEAS_ACCOUNT_EVIDENCE_DIR')
    if not directory: return
    path = Path(directory) / (name + '-' + scene['tmp'].name + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**value, 'real_account_login': 'NOT_RUN', 'native_windows': 'NOT_RUN',
        'paid_model_calls': 0, 'original_cli_sha256': CLI_SHA['posix']}, indent=2))


@pytest.fixture
def scene(tmp_path, monkeypatch):
    check(binary_hash(CLI) == CLI_SHA['posix'])
    data = tmp_path / 'data'; data.mkdir()
    providers = ProviderSettings(Store(data / 'store.sqlite3'), data, ROOT)
    # Select the actual hash-verified binary; do not fake its process or HTTP.
    monkeypatch.setattr(providers, '_cli', lambda: CLI)
    return {'providers': providers, 'tmp': tmp_path, 'data': data}


def fixture_owned_oauth(account, *, kind='oauth', other_provider=False):
    binding, reference = account._binding()
    parent = Path(binding['account_data_home']) / 'opencode'; parent.mkdir(exist_ok=True)
    value = {'openai': {'type': 'oauth', 'access': 'opaque-owned-fixture-not-real-access',
        'refresh': 'opaque-owned-fixture-not-real-refresh', 'expires': int(time.time() * 1000) + 3600000}}
    if kind == 'api': value['openai'] = {'type': 'api', 'key': 'owned-fixture-not-a-real-api-key'}
    if other_provider: value['unapproved'] = {'type': 'api', 'key': 'unused-owned-fixture'}
    path = parent / 'auth.json'
    path.write_text(json.dumps(value)); path.chmod(0o600)
    return binding, reference, path


def observe_native(account):
    observed = []
    original = account._native
    async def observe(method, path, body=None, **options):
        observed.append({'method': method, 'path': path})
        return await original(method, path, body, **options)
    account._native = observe
    return observed


def test_actual_original_cli_loads_browser_methods_in_empty_private_account(scene, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'ambient-key-must-not-be-used')
    monkeypatch.setenv('OPENCODE_AUTH_CONTENT', 'ambient-native-auth-must-not-be-used')
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        observed = observe_native(account)
        try:
            result = await account.get()
            methods = await account._native('GET', '/provider/auth')
            check(result['status'] == 'SIGNED_OUT' and result['model_access'] == 'NOT_VALIDATED')
            check(any(item['label'] == 'ChatGPT Pro/Plus (browser)' and item['type'] == 'oauth' for item in methods['openai']))
            check(all(item['method'] == 'GET' for item in observed))
            check(not (account.root / 'native-data/opencode/auth.json').exists())
            process = account.active['account']['process']
            check(process.returncode is None)
            evidence(scene, 'actual-empty-original', {'state': result, 'native_requests': observed,
                'oauth_methods': methods['openai'], 'process_alive': True})
        finally:
            check((await account.close())['status'] == 'CLOSED')
        check(process.returncode is not None)
    asyncio.run(run())


def test_actual_browser_pkce_url_is_ephemeral_and_repeated_login_never_posts_again(scene):
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        observed = observe_native(account)
        try:
            result = await account.login('actual-browser-pkce')
            check(result['status'] == 'WAITING_USER' and result['login_url'].startswith('https://auth.openai.com/oauth/authorize?'))
            target = account.login_target(result['attempt_id'])
            check(target['login_url'] == result['login_url'])
            replay = await account.login('actual-browser-pkce')
            check('login_url' not in replay and replay['attempt_id'] == result['attempt_id'])
            check(sum(item['path'].endswith('/authorize') for item in observed) == 1)
            for path in account.root.rglob('*.json'):
                check('https://auth.openai.com/oauth/authorize?' not in path.read_text())
            cancelled = await account.cancel(result['attempt_id'])
            check(cancelled['status'] == 'SIGNED_OUT' and account.active == {})
            with pytest.raises(Conflict): account.login_target(result['attempt_id'])
            evidence(scene, 'actual-pkce-idempotency', {'native_requests': observed, 'cancel_status': cancelled['status'],
                'persisted_login_url': False, 'callback_or_token_exchange': False})
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())


def test_actual_callback_is_one_owned_task_and_cancel_stops_native_wait(scene):
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        observed = observe_native(account)
        try:
            begun = await account.login('actual-waiting-callback')
            ident = begun['attempt_id']
            first = await account.complete(ident)
            check(first['status'] == 'WAITING_USER' and first['phase'] == 'COMPLETING')
            second = await account.complete(ident)
            check(second['phase'] == 'COMPLETING' and len(account.tasks) == 1)
            deadline = time.monotonic() + 10
            while not any(item['path'].endswith('/callback') for item in observed) and time.monotonic() < deadline:
                await asyncio.sleep(.01)
            check(sum(item['path'].endswith('/callback') for item in observed) == 1)
            state = await account.get()
            check(state['phase'] == 'COMPLETING' and state['status'] == 'WAITING_USER')
            for _ in range(3):
                target = account.login_target(ident)
                check(target['login_url'] == begun['login_url'])
            check(sum(item['path'].endswith('/authorize') for item in observed) == 1)
            check(sum(item['path'].endswith('/callback') for item in observed) == 1)
            check('login_url' not in state)
            for path in account.root.rglob('*.json'):
                check('https://auth.openai.com/oauth/authorize?' not in path.read_text())
            process = account.active['account']['process']
            cancelled = await account.cancel(ident)
            await asyncio.gather(*account.tasks.values(), return_exceptions=True)
            check(cancelled['status'] == 'SIGNED_OUT' and process.returncode is not None)
            check(account._attempt(ident)['status'] == 'CANCELLED')
            check(account._state()['status'] == 'SIGNED_OUT')
            check(account._login_target is None)
            with pytest.raises(Conflict): account.login_target(ident)
            check(sum(item['path'].endswith('/callback') for item in observed) == 1)
            evidence(scene, 'actual-callback-cancel', {'native_requests': observed, 'callback_count': 1,
                'reopen_while_completing_count': 3, 'reopen_preserves_original_url': True,
                'persisted_login_url': False, 'terminal_reopen_rejected': True,
                'process_stopped': True, 'attempt_status': account._attempt(ident)['status']})
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())


def test_actual_failed_native_callback_clears_the_reopen_target(scene):
    import httpx
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        observed = observe_native(account)
        try:
            begun = await account.login('actual-native-callback-failure')
            ident = begun['attempt_id']
            await account.complete(ident)
            check(account.login_target(ident)['login_url'] == begun['login_url'])
            deadline = time.monotonic() + 10
            while not any(item['path'].endswith('/callback') for item in observed) and time.monotonic() < deadline:
                await asyncio.sleep(.01)
            check(sum(item['path'].endswith('/callback') for item in observed) == 1)
            # Fail the actual original PKCE waiter through its own local cancel
            # endpoint. No OpenAI token/model request or fake callback response.
            async with httpx.AsyncClient(trust_env=False) as client:
                reply = await client.get('http://127.0.0.1:1455/cancel')
                check(reply.status_code == 200)
            outcomes = await asyncio.wait_for(asyncio.gather(*account.tasks.values(), return_exceptions=True), 10)
            check(len(outcomes) == 1 and isinstance(outcomes[0], Conflict))
            check(account._attempt(ident)['status'] == 'UNKNOWN_OUTCOME')
            check(account._login_target is None)
            with pytest.raises(Conflict): account.login_target(ident)
            check(sum(item['path'].endswith('/authorize') for item in observed) == 1)
            check(sum(item['path'].endswith('/callback') for item in observed) == 1)
            check(not (account.root / 'native-data/opencode/auth.json').exists())
            evidence(scene, 'actual-callback-failed-reopen', {'native_requests': observed,
                'attempt_status': account._attempt(ident)['status'], 'failure_from_original_local_waiter': True,
                'memory_login_url_cleared': True, 'terminal_reopen_rejected': True,
                'credential_file_created': False})
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())


def test_completing_reopen_checks_deadline_and_close_clears_the_original_url(scene):
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        observed = observe_native(account)
        try:
            begun = await account.login('actual-deadline-close-target')
            ident = begun['attempt_id']
            await account.complete(ident)
            check(account.login_target(ident)['login_url'] == begun['login_url'])
            # An expired persisted deadline must deny even the retained, valid
            # in-memory URL. This is an attempt-metadata fixture, not a clock or
            # original-runtime replacement.
            attempt = account._attempt(ident)
            attempt['deadline'] = time.time() - 1
            account._save_attempt(attempt)
            with pytest.raises(Conflict): account.login_target(ident)
            check(account._login_target is None)
            check((await account.close())['status'] == 'CLOSED')
            with pytest.raises(Conflict): account.login_target(ident)
            check(sum(item['path'].endswith('/authorize') for item in observed) == 1)
            check(sum(item['path'].endswith('/callback') for item in observed) <= 1)
            evidence(scene, 'actual-expired-close-reopen', {'native_requests': observed,
                'expired_reopen_rejected': True, 'closed_reopen_rejected': True,
                'memory_login_url_cleared': True})
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())


def test_actual_restart_restores_metadata_get_only_without_oauth_replay(scene):
    async def run():
        first = OpenCodeAccount(scene['providers'], scene['data'])
        original = await first.login('actual-restart-pending')
        check((await first.close())['status'] == 'CLOSED')
        account = OpenCodeAccount(scene['providers'], scene['data'])
        observed = observe_native(account)
        try:
            result = await account.get()
            check(result['attempt_id'] == original['attempt_id'] and result['status'] == 'UNKNOWN_OUTCOME')
            check('login_url' not in result and all(item['method'] == 'GET' for item in observed))
            with pytest.raises(Conflict): account.login_target(original['attempt_id'])
            await account.login('actual-restart-pending')
            check(all(item['method'] == 'GET' for item in observed))
            evidence(scene, 'actual-restart-get-only', {'state': result, 'native_requests': observed})
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())


def test_owned_guard_rejects_api_other_provider_and_symbolic_link_without_leaking_values(scene):
    account = OpenCodeAccount(scene['providers'], scene['data'])
    binding, ref, path = fixture_owned_oauth(account)
    check(require_owned_oauth(Path(binding['account_data_home']))['native_auth_type'] == 'oauth')
    for options in ({'kind': 'api'}, {'other_provider': True}):
        fixture_owned_oauth(account, **options)
        with pytest.raises(Conflict) as caught: require_owned_oauth(Path(binding['account_data_home']))
        check('fixture' not in str(caught.value) and 'owned-' not in str(caught.value))
    fixture_owned_oauth(account)
    saved = path.with_name('auth-fixture-original.json'); path.rename(saved); path.symlink_to(saved.name)
    with pytest.raises(Conflict): require_owned_oauth(Path(binding['account_data_home']))
    path.unlink(); saved.rename(path)
    evidence(scene, 'owned-auth-guard', {'api_type_rejected': True, 'other_provider_rejected': True,
        'symlink_rejected': True, 'credential_values_recorded': False})


def test_actual_catalog_selection_saves_real_subscription_profile_and_stable_scope(scene):
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        binding, ref, path = fixture_owned_oauth(account)
        try:
            state = await account.get()
            check(state['status'] == 'AUTHENTICATED' and state['models'])
            selected = state['models'][0]['id']
            result = await account.save_profile('actual-profile-save', 'codex-subscription', selected)
            registry = OpenCodeProviderRegistry(scene['providers'].configuration, scene['providers'].secret)
            profile = registry.resolve(result['profile_ref'])
            check(isinstance(profile, SubscriptionProviderProfile))
            check(profile.auth_mode == AUTH_MODE and profile.protocol == PROTOCOL and profile.api_key is None)
            check(profile.account_data_home == Path(binding['account_data_home']))
            check(result in scene['providers'].list())
            check(result['model_access'] == 'NOT_VALIDATED' and 'account_data_home' not in result)
            fingerprint = profile.fingerprint
            current = json.loads(path.read_bytes())
            current['openai']['access'] = 'owned-fixture-token-refreshed-without-scope-change'
            path.write_text(json.dumps(current))
            check(registry.resolve(result['profile_ref']).fingerprint == fingerprint)
            replay = await account.save_profile('actual-profile-save', 'codex-subscription', selected)
            check(replay == result and replay['profile_ref']['revision'] == 1)
            fixture_owned_oauth(account, kind='api')
            with pytest.raises(Conflict): registry.resolve(result['profile_ref'])
            check(scene['providers'].get('codex-subscription')['profile_ref'] == result['profile_ref'])
            evidence(scene, 'actual-catalog-profile', {'profile': result, 'same_scope_after_fixture_rotation': True,
                'api_type_dispatch_rejected': True, 'credentials': 'OWNED_OPAQUE_FIXTURE_NOT_REAL_LOGIN'})
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())


def test_account_directory_replacement_invalidates_profile_authority(scene):
    account = OpenCodeAccount(scene['providers'], scene['data'])
    binding, ref, path = fixture_owned_oauth(account)
    directory = Path(binding['account_data_home'])
    directory.rename(directory.with_name('native-data-old-fixture')); directory.mkdir()
    with pytest.raises(Conflict): read_account_binding(scene['data'], ref)


def test_rejected_model_and_changed_operation_do_not_create_extra_profile(scene):
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        fixture_owned_oauth(account)
        try:
            state = await account.get()
            with pytest.raises(Conflict): await account.save_profile('bad-catalog-model', 'codex', 'model-never-in-original-catalog')
            check(not scene['providers'].list())
            model = state['models'][0]['id']
            saved = await account.save_profile('one-profile-op', 'codex', model)
            with pytest.raises(Conflict): await account.save_profile('one-profile-op', 'codex', model + '-changed')
            check(scene['providers'].get('codex')['profile_ref'] == saved['profile_ref'])
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())


def test_close_is_shared_and_blocks_new_native_owner_or_authorization(scene):
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        await account.get()
        process = account.active['account']['process']
        one, two = await asyncio.gather(account.close(), account.close())
        check(one == two and one['status'] == 'CLOSED' and process.returncode is not None)
        with pytest.raises(Conflict): await account.login('cannot-login-after-close')
        check((await account.get())['native_status'] == 'CLOSING' and account.active == {})
    asyncio.run(run())


def test_get_waiting_on_startup_lock_cannot_spawn_after_close_fence(scene):
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        await account._startup_lock.acquire()
        waiting = asyncio.create_task(account.get())
        await asyncio.sleep(0)
        close = asyncio.create_task(account.close())
        await asyncio.sleep(0)
        account._startup_lock.release()
        with pytest.raises(Conflict): await waiting
        check((await close)['status'] == 'CLOSED')
        check(account.active == {} and not (account.root / 'controllers').exists())
    asyncio.run(run())


def test_profile_commit_then_lost_account_ack_is_recoverable_by_exact_get_only(scene, monkeypatch):
    import packages.adapters.opencode_account as module
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        fixture_owned_oauth(account)
        try:
            state = await account.get(); model = state['models'][0]['id']
            original = module._write
            def fail_ack(path, value, **options):
                if path.parent.name == 'profile-operations' and value.get('result') is not None:
                    raise OSError('fixture lost account acknowledgement after durable profile')
                return original(path, value, **options)
            monkeypatch.setattr(module, '_write', fail_ack)
            with pytest.raises(OSError): await account.save_profile('lost-account-ack', 'recovered-codex', model)
            before = {str(p.relative_to(account.root)): p.read_bytes() for p in account.root.rglob('*.json')
                if 'native-data' not in p.parts}
            recovered = account.profile_operation('lost-account-ack')
            check(recovered['status'] == 'COMPLETED' and recovered['result']['profile_ref']['revision'] == 1)
            check(account._state().get('profile') is None)
            check(before == {str(p.relative_to(account.root)): p.read_bytes() for p in account.root.rglob('*.json')
                if 'native-data' not in p.parts})
            check(account.profile_operation('never-posted')['status'] == 'NO_RECORD')
            check(len(scene['providers'].list()) == 1)
            evidence(scene, 'lost-profile-ack-read-only', {'operation': recovered, 'read_did_not_write': True,
                'profile_revision': 1})
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())


def test_actual_original_instance_refresh_loads_oauth_catalog_after_empty_observation(scene):
    async def run():
        account = OpenCodeAccount(scene['providers'], scene['data'])
        observed = observe_native(account)
        try:
            empty = await account.get()
            check(empty['status'] == 'SIGNED_OUT')
            # Only the credential is a declared opaque fixture. Both catalogs
            # and the actual instance disposal are observed from original CLI.
            fixture_owned_oauth(account)
            check(await account._native('POST','/instance/dispose') is True)
            connected = await account.get()
            check(connected['status'] == 'AUTHENTICATED')
            ids = [item['id'] for item in connected['models']]
            check('gpt-5.4' in ids and 'gpt-5.5-pro' not in ids)
            check(len(connected['models']) < len(empty['models']))
            check(connected['model_access'] == 'NOT_VALIDATED')
            evidence(scene, 'actual-native-catalog-refresh', {'native_requests':observed,
                'unsigned_catalog_count':len(empty['models']),'oauth_catalog_count':len(ids),
                'credentials':'OWNED_OPAQUE_FIXTURE_NOT_REAL_LOGIN','entitlement':'NOT_VALIDATED'})
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())


def test_concurrent_same_profile_operation_persists_only_one_actual_revision(scene):
    async def run():
        account = OpenCodeAccount(scene['providers'],scene['data'])
        fixture_owned_oauth(account)
        try:
            state = await account.get(); model = state['models'][0]['id']
            first, second = await asyncio.gather(*(account.save_profile('same-concurrent-profile-op','concurrent-codex',model)
                for _ in range(2)))
            check(first == second and first['profile_ref']['revision'] == 1)
            files = list(scene['providers'].path('profiles','concurrent-codex').glob('*.json'))
            check(len(files) == 1)
            check(account.profile_operation('same-concurrent-profile-op')['result'] == first)
        finally: check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())
