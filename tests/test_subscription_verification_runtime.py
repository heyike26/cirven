"""Actual fixed CLI account observation, private fake OAuth metadata only.

No real authorization, token, model service or paid inference. Positive response
proofs remain synthetic-message UNIT tests; this qualifies the legacy dispatch
epoch bridge and no-verification-on-login behavior only.
"""
import asyncio
import time

import pytest

from test_opencode_account import scene, fixture_owned_oauth, observe_native, check
from packages.adapters.opencode_account import OpenCodeAccount, authorization_epoch
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.domain.store import Conflict
from packages.domain.local_workspaces import LocalWorkspaces
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.tasks import digest
from packages.agent_sessions import AgentSessions


def test_real_native_old_connected_store_cannot_complete_synthetic_pending_reconnect(scene):
    """Actual original GET with unusable private fake OAuth; no OAuth/model POST."""
    account = OpenCodeAccount(scene['providers'], scene['data'])
    binding, reference, _fixture = fixture_owned_oauth(account)
    saved = scene['providers'].save_subscription('pending-reconnect-save', 'pending-reconnect',
        'gpt-5.4', reference, False)
    account._save(profile=saved)
    observed = observe_native(account)

    async def run():
        try:
            check((await account.get())['status'] == 'AUTHENTICATED')
            # Fixture models an already-started login awaiting its original
            # callback, while the previous unusable store still says connected.
            attempt = {'id': 'synthetic_pending_reconnect', 'status': 'WAITING_USER',
                       'deadline': time.time()+60, 'method': 'browser'}
            account._save_attempt(attempt)
            account._save(status='WAITING_USER', attempt_id=attempt['id'],
                          authorization_epoch='f'*32, reconnect_required=True)
            account._login_target = {'attempt_id': attempt['id']}
            waiting = await account.get()
            check(waiting['status'] == 'WAITING_USER' and waiting['model_access'] == 'NOT_VALIDATED')
            attempt['status'] = 'UNKNOWN_OUTCOME'; account._save_attempt(attempt)
            account._save(status='UNKNOWN_OUTCOME')
            check((await account.get())['status'] == 'UNKNOWN_OUTCOME')
            attempt['status'] = 'CANCELLED'; account._save_attempt(attempt)
            account._save(status='SIGNED_OUT')
            restored = await account.get()
            check(restored['status'] == 'AUTHENTICATED' and restored['phase'] == 'REAUTH_REQUIRED')
            check(restored['model_access'] == 'NOT_VALIDATED')
            check(all(item['method'] == 'GET' for item in observed))
        finally:
            check((await account.close())['status'] == 'CLOSED')
    asyncio.run(run())


def test_existing_private_dispatch_initializes_epoch_before_account_panel_without_claiming_inference(scene):
    account=OpenCodeAccount(scene['providers'],scene['data'])
    _binding,reference,_fixture_auth=fixture_owned_oauth(account)
    saved=scene['providers'].save_subscription('legacy-before-panel','legacy-subscription','gpt-5.4',reference,False)
    registry=OpenCodeProviderRegistry(scene['providers'].configuration,scene['providers'].secret)
    check(authorization_epoch(_binding['account_data_home']) is None)
    # The real production resolver performs its original auth-type/path guard,
    # then freezes a local generation; no account GET or model POST is needed.
    selected=registry.resolve(saved['profile_ref'])
    epoch=authorization_epoch(_binding['account_data_home'])
    check(selected.authorization_epoch==epoch and len(epoch)==32)
    check(selected.public()['model_access']=='NOT_VALIDATED')
    observed=observe_native(account)
    async def run():
        try:
            result=await account.get()
            check(result['status']=='AUTHENTICATED' and result['model_access']=='NOT_VALIDATED')
            check(authorization_epoch(_binding['account_data_home'])==epoch)
            check(all(item['method']=='GET' for item in observed))
        finally:
            check((await account.close())['status']=='CLOSED')
    asyncio.run(run())


def test_missing_owned_oauth_dispatch_never_establishes_authorization_generation(scene):
    account=OpenCodeAccount(scene['providers'],scene['data'])
    binding,reference=account._binding()
    saved=scene['providers'].save_subscription('missing-oauth-save','missing-oauth','gpt-5.4',reference,False)
    registry=OpenCodeProviderRegistry(scene['providers'].configuration,scene['providers'].secret)
    with pytest.raises(Conflict):registry.resolve(saved['profile_ref'])
    check(authorization_epoch(binding['account_data_home']) is None)


def test_real_idle_owner_keeps_immutable_manifest_and_exact_controls_after_authorization_rotation(scene):
    """Original idle CLI/control qualification; no subscription inference.

    The private fake OAuth fixture is deliberately not a usable account token.
    No message POST is admitted, so no external token or model call occurs.
    """
    account=OpenCodeAccount(scene['providers'],scene['data'])
    binding,reference,_private_fixture_auth=fixture_owned_oauth(account)
    saved=scene['providers'].save_subscription('idle-owner-save','idle-owner','gpt-5.4',reference,False)
    store=scene['providers'].store
    pid=store.create_project('Original idle authorization migration','No model prompt')['id']
    registry=OpenCodeProviderRegistry(scene['providers'].configuration,scene['providers'].secret)
    capabilities=NativeCapabilities(store,scene['data'])
    owner=AgentSessions(store,scene['data'],provider_registry=registry,
        workspaces=LocalWorkspaces(store,scene['data']),capabilities=capabilities)
    observed=[]
    original=owner._http
    async def track(context,method,path,*args,**kwargs):
        observed.append({'method':method,'path':path})
        return await original(context,method,path,*args,**kwargs)
    owner._http=track

    async def run():
        try:
            opened=await owner.open(pid,{'operation_id':'open-idle-owner','workspace_ref':None,
                'provider_profile_ref':saved['profile_ref'],
                'capability_binding_sha256':capabilities.native_binding_snapshot()['sha256'],
                'title':'Original idle owner only','ttl_seconds':120,'max_calls':1,'max_output_tokens':256})
            sid=opened['session_ref']['id'];native=opened['original_session_id']
            manifest,_=owner._manifest(pid,sid)
            check(manifest['provider_fingerprint']==digest({'profile_sha256':saved['profile_ref']['sha256'],
                'account_ref':reference}),'Old immutable manifest fingerprint must remain compatible')
            frozen=owner.active[sid]['profile'].authorization_epoch
            check(frozen==authorization_epoch(binding['account_data_home']))
            account._save(authorization_epoch='f'*32)
            check(frozen!='f'*32)
            control={'session_ref':opened['session_ref'],'generation':opened['generation']}
            with pytest.raises(Conflict,match='authorization changed'):
                await owner.send(pid,sid,{**control,'operation_id':'refused-old-authorization',
                    'raw_text':'NEVER_DISPATCH_THIS_TEST_PROMPT','agent':'plan'})
            check(not any(item['path'].endswith(('/prompt_async','/message')) and item['method']=='POST'
                          for item in observed))
            # Stale authorization cannot prevent stopping our own exact owner.
            # This is SESSION-level idle abort; exact active-op race tests live
            # in the separate original-session control regression suite.
            stopped=await owner.interrupt(pid,sid,{**control,'operation_id':'stop-old-owned-idle'})
            check(stopped['status']=='RETURNED' and stopped['original_abort_acknowledged'] is True)
            check(stopped['original_message_stop_confirmed'] is False)
            check((await owner.inspect(pid,sid))['original_session_id']==native)
            disconnected=await owner.controlled_disconnect(pid,sid,{**control,'operation_id':'disconnect-old-owned-idle'})
            check(disconnected['last_control_operation']['status']=='RETURNED')
            check(sid not in owner.active)
            loaded=await owner.load(pid,sid,{'operation_id':'explicitly-load-new-authorization',
                'session_ref':opened['session_ref']})
            fresh=loaded['session']
            check(fresh['original_session_id']==native and fresh['generation']==opened['generation']+1)
            check(owner.active[sid]['profile'].authorization_epoch=='f'*32)
            check(not any(item['path'].endswith(('/prompt_async','/message')) and item['method']=='POST'
                          for item in observed))
            check(account._public()['model_access']=='NOT_VALIDATED')
        finally:
            await owner.close()
            check(owner.closed and not owner.active)
            check((await account.close())['status']=='CLOSED')
    asyncio.run(run())
