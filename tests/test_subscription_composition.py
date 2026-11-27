"""Real local API and original OAuth owner aggregate shutdown, without account login."""
import asyncio
import os
from pathlib import Path

from packages.domain.tasks import canonical


def test_capability_management_reports_mounted_control_separately_from_account_qualification(client):
    state = client.app.state.native_capabilities.get()
    assert state['provider_management']['subscription_oauth_in_native_companion'] == 'CONTROL_IMPLEMENTED_ACCOUNT_NOT_VALIDATED'
    assert state['provider_management']['subscription_account_route'] == '/api/subscription/account'
    assert state['silent_paid_fallback'] is False
    assert client.app.state.subscription_account.active == {}


def test_actual_pending_native_oauth_owner_is_observed_by_application_shutdown(client):
    state = client.get('/api/subscription/account')
    assert state.status_code == 200, state.text
    assert state.json()['status'] == 'SIGNED_OUT'
    assert state.json()['api_key_fallback'] is False
    app = client.app
    account = app.state.subscription_account
    process = account.active['account']['process']
    assert process.returncode is None
    # Original browser-method authorize constructs PKCE and its local callback
    # listener. This test opens no browser and contacts no OpenAI token/model API.
    login = client.post('/api/subscription/login', json={
        'operation_id': 'owned-subscription-shutdown-test', 'method': 'browser'})
    assert login.status_code == 200, login.text
    attempt = login.json()['attempt_id']
    assert login.json()['status'] == 'WAITING_USER'
    complete = client.post(f'/api/subscription/attempts/{attempt}/complete', json={})
    assert complete.status_code == 200, complete.text
    assert complete.json()['phase'] == 'COMPLETING'
    client.portal.call(asyncio.sleep, .2)
    assert not account.tasks[attempt].done()
    receipt = client.portal.call(app.state.owner_shutdown.close)
    own = next(row for row in receipt['outcomes'] if row['owner_kind'] == 'SUBSCRIPTION_ACCOUNT')
    assert receipt['status'] == 'CLOSED', canonical(receipt)
    assert own['captured_process_count'] == own['stopped_process_count'] == 1
    assert own['pending_process_count'] == own['pending_task_count'] == 0
    assert own['active_context_count'] == 0
    assert receipt['outcomes'][-1]['owner_kind'] == 'RUNTIME_SDK'
    assert process.returncode is not None and account.active == {}
    assert account.tasks[attempt].done()
    assert client.portal.call(app.state.owner_shutdown.close) == receipt
    assert client.post('/api/subscription/login', json={
        'operation_id': 'must-not-reopen-after-shutdown', 'method': 'browser'}).status_code == 409
    assert 'login_url' not in canonical(receipt)
    assert str(account.root) not in canonical(receipt)
    destination = os.environ.get('EEAS_SUBSCRIPTION_COMPOSITION_EVIDENCE')
    if destination:
        path = Path(destination); path.mkdir(parents=True, exist_ok=True)
        (path / 'aggregate-native-oauth-close.json').write_text(canonical({
            'receipt': receipt, 'original_auth_owner_returncode': process.returncode,
            'callback_task_done': True, 'browser_opened': False,
            'actual_pro_login': 'NOT_RUN', 'windows': 'NOT_RUN'}))
