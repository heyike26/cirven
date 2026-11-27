"""Original CLI pre-auth integration; no account entitlement or paid inference.

The empty-account resolver below is explicit test configuration. Product account
resolution correctly refuses missing OAuth before dispatch. It does not replace
any original CLI, HTTP API, model/SDK kernel or process owner. Only local config
and original auth-missing outcomes are exercised here.
"""
import asyncio
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.adapters.opencode import OpenCodeBridge
from packages.adapters.opencode_account import SubscriptionProviderProfile, authorization_epoch
from packages.adapters.opencode_planning import OpenCodePlanningTransport, PLANNING_PERMISSIONS
from packages.adapters.opencode_profile import CLI_SHA
from packages.adapters.subscription_runtime import (is_subscription, native_provider_config,
    subscription_budget_summary, subscription_environment)
from packages.agent_sessions import AgentSessions
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.local_workspaces import LocalWorkspaces
from packages.domain.store import Store, Conflict
from packages.domain.tasks import canonical, digest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'var/opencode-ui-offline-guard/opencode'


@pytest.fixture
def subscription(tmp_path):
    account = tmp_path / 'owned-account-data'
    account.mkdir()
    return SubscriptionProviderProfile(profile_id='own-subscription', revision=1, sha256='a'*64,
        executable=CLI, executable_sha256=CLI_SHA[os.name], model_id='gpt-5.4',
        account_ref={'id':'own-account','revision':1,'sha256':'b'*64}, account_data_home=account,
        # PreAuthConfiguration deliberately substitutes product account admission
        # to inspect the original CLI's empty-auth failure. This local metadata
        # generation is not OAuth authentication and creates no auth store.
        authorization_epoch=authorization_epoch(account,establish=True))


def test_environment_shares_only_explicit_native_data_and_has_no_api_key_fallback(subscription, tmp_path):
    home=tmp_path/'execution-home';home.mkdir()
    base=OpenCodeBridge._environment(home,home/'config/opencode/opencode.json','fixture-password')
    base.update(OPENAI_API_KEY='fixture-never-forward',CHATGPT_ACCESS_TOKEN='fixture-never-forward',
                OPENCODE_CONFIG_CONTENT='fixture-never-forward')
    old=copy.deepcopy(base)
    actual=subscription_environment(base,subscription)
    assert base==old
    assert actual['XDG_DATA_HOME']==str(subscription.account_data_home)
    assert actual['OPENCODE_DISABLE_DEFAULT_PLUGINS']=='false'
    for key in ('HOME','USERPROFILE','XDG_CONFIG_HOME','XDG_CACHE_HOME','OPENCODE_CONFIG'):
        assert actual[key]==base[key]
    for key in ('OPENAI_API_KEY','CHATGPT_ACCESS_TOKEN','OPENCODE_CONFIG_CONTENT'):
        assert key not in actual
    assert not (subscription.account_data_home/'opencode/auth.json').exists()


@pytest.mark.parametrize('bad',[
    {'api_key':'would-be-paid-fallback'}, {'provider_id':'eeas-api'},
    {'protocol':'OPENAI_COMPATIBLE_CHAT_COMPLETIONS'}, {'provider_url':'https://api.openai.com/v1'},
    {'account_ref':{'id':'bad','revision':1,'sha256':'bad'}}, {'account_data_home':None},
])
def test_forged_or_api_profile_cannot_select_native_route(subscription, tmp_path, bad):
    home=tmp_path/'private';home.mkdir()
    with pytest.raises(Conflict):
        subscription_environment(OpenCodeBridge._environment(home,home/'opencode.json','private'),replace(subscription,**bad))


def test_native_account_symlink_or_home_sharing_is_refused(subscription,tmp_path):
    home=tmp_path/'home';home.mkdir()
    env=OpenCodeBridge._environment(home,home/'opencode.json','private')
    with pytest.raises(Conflict,match='SHARE_ONLY'):
        subscription_environment(env,replace(subscription,account_data_home=home))
    linked=tmp_path/'linked';linked.symlink_to(subscription.account_data_home,target_is_directory=True)
    with pytest.raises(Conflict,match='LINK_OR_REPARSE'):
        subscription_environment(env,replace(subscription,account_data_home=linked))


def test_native_provider_preserves_original_responses_provider_and_exact_model(subscription):
    config=native_provider_config(subscription,1024)
    assert config=={'provider':{'openai':{'models':{'gpt-5.4':{'limit':{'context':131072,'output':1024}}}}}}
    raw=canonical(config)
    assert 'apiKey' not in raw and 'baseURL' not in raw and 'npm' not in raw
    summary=subscription_budget_summary({'max_calls':3,'timeout_seconds':60,'max_output_tokens':1024})
    assert summary['rpc_attempts'] is None and summary['rpc_max_calls']==3
    assert summary['hard_rpc_cap']=='NOT_QUALIFIED' and summary['rpc_cap_enforcement']=='NATIVE_DIRECT_UNMETERED'
    assert summary['output_token_cap']=='NOT_QUALIFIED_NATIVE_CODEX_PLUGIN'
    assert summary['provider_receipts']==[]


def test_session_and_planning_config_keep_native_tool_policies_without_relay(subscription):
    owner=AgentSessions.__new__(AgentSessions)
    request={'command_allowlist':[],'mcp_tool_allowlist':[],'max_calls':2,'max_output_tokens':1024}
    binding={'value':{'native_config_fragment':{'mcp':{},'skills':{}}}}
    config=owner._config({'request':request},subscription,binding,None)
    assert config['model']==config['small_model']=='openai/gpt-5.4'
    assert config['plugin']==[] and config['provider']==native_provider_config(subscription,1024)['provider']
    assert config['agent']['build']['steps']==2 and config['permission']['*']=='deny'
    assert config['permission']['question']=='allow' and config['permission']['task']['explore']=='ask'
    planning=OpenCodePlanningTransport.__new__(OpenCodePlanningTransport)
    cfg=planning._planning_config(subscription,{'budget':{'max_output_tokens':1024}},None)
    assert cfg['provider']==native_provider_config(subscription,1024)['provider']
    assert cfg['model']==cfg['small_model']=='openai/gpt-5.4'
    assert cfg['agent']['eeas-planning-proposal']['steps']==1 and cfg['permission']==PLANNING_PERMISSIONS


class PreAuthConfiguration:
    """An immutable credential-free input fixture, not a product account gate."""
    def __init__(self,profile):
        self.profile=profile
        self.raw={'fixture':'CREDENTIAL_FREE_NATIVE_PROVIDER_CONFIGURATION_NOT_ACCOUNT_AUTHORITY'}
    def resolve(self,ref):
        if ref!=self.profile.reference:raise Conflict('Changed pre-auth input fixture')
        return self.profile
    def configuration_resolver(self,*_):return copy.deepcopy(self.raw)
    def current_fingerprint(self,ref):return self.resolve(ref).fingerprint


def test_actual_original_long_owner_missing_auth_has_no_relay_or_paid_fallback(subscription,tmp_path):
    assert CLI.is_file() and hashlib.sha256(CLI.read_bytes()).hexdigest()==CLI_SHA[os.name]
    registry=PreAuthConfiguration(subscription)
    subscription=replace(subscription,sha256=digest(registry.raw));registry.profile=subscription
    data=tmp_path/'data';data.mkdir()
    store=Store(data/'eeas.sqlite3');pid=store.create_project('Original native subscription pre-auth QA','fixture')['id']
    capabilities=NativeCapabilities(store,data);workspaces=LocalWorkspaces(store,data)
    owner=AgentSessions(store,data,provider_registry=registry,workspaces=workspaces,capabilities=capabilities)
    request={'operation_id':'open-owned-preauth','workspace_ref':None,'provider_profile_ref':subscription.reference,
             'capability_binding_sha256':capabilities.native_binding_snapshot()['sha256'],
             'title':'Original credential-free subscription probe','ttl_seconds':120,'max_calls':2,'max_output_tokens':1024}
    async def check():
        try:
            opened=await owner.open(pid,request);ident=opened['session_ref']['id']
            context=owner.active[ident]
            assert opened['owner_status']=='READY' and context['wire'] is None
            assert opened['model_calls_reserved'] is None and opened['native_turns_started']==0
            assert opened['budget_observation']['rpc_attempts'] is None
            config=json.loads((owner._path(pid,ident,'runtime/home/config/opencode/opencode.json')).read_bytes())
            assert config['model']=='openai/gpt-5.4' and 'apiKey' not in canonical(config) and 'baseURL' not in canonical(config)
            await owner.send(pid,ident,{'operation_id':'missing-auth-original-send','session_ref':opened['session_ref'],
                'generation':opened['generation'],'raw_text':'Reply with READY only.','timeout_seconds':30})
            deadline=asyncio.get_running_loop().time()+40
            while asyncio.get_running_loop().time()<deadline:
                view=await owner.inspect(pid,ident)
                if owner.operation(pid,ident,'missing-auth-original-send')['status'] in {'FAILED','RETURNED','UNKNOWN_OUTCOME'}:break
                await asyncio.sleep(.1)
            operation=owner.operation(pid,ident,'missing-auth-original-send')
            assert operation['status']=='FAILED',operation
            raw=canonical(view['messages'])
            assert any(marker in raw.lower()for marker in ('api key','apikey','authentication','credentials')),raw
            assert view['model_calls_reserved'] is None and view['native_turns_started']==1
            assert not (subscription.account_data_home/'opencode/auth.json').exists()
            target=tmp_path/'original-subscription-preauth-observation.json'
            target.write_text(canonical({'scope':'ACTUAL_ORIGINAL_CLI_EMPTY_ACCOUNT_NO_PAID_INFERENCE_NOT_PRODUCT_LOGIN',
                'session':view,'operation':operation,'API_key_supplied':False,'relay_started':False,
                'Windows':'NOT_RUN','entitlement':'NOT_VALIDATED'}))
            # A duplicate ID returns the original failed operation, not another POST.
            same=await owner.send(pid,ident,{'operation_id':'missing-auth-original-send','session_ref':opened['session_ref'],
                'generation':opened['generation'],'raw_text':'Reply with READY only.','timeout_seconds':30})
            assert same['status']=='FAILED' and owner.get(pid,ident)['native_turns_started']==1
        finally:await owner.close()
        assert owner.active=={}
    asyncio.run(check())


def test_actual_original_planning_missing_auth_preserves_source_and_reports_unmetered(subscription,scene):
    # Reuse original immutable local document/workspace fixture; no transport/kernel mock.
    from test_opencode_planning import scene as _fixture_documentation
    from packages.adapters.opencode_planning import source_receipt_sha256
    registry=PreAuthConfiguration(subscription)
    transport=OpenCodePlanningTransport(scene['service'],scene['tmp_path'],provider_registry=registry,
        workspace_authority=scene['authority'],source_validator=scene['source_validator'])
    ref={k:scene['bound'][k]for k in ('id','revision','binding_sha256')}
    async def check():
        try:
            result=await transport.propose({'project_id':scene['pid'],'operation_id':'original-subscription-planning-preauth',
                'prompt':'Return the structured result from the frozen evidence.','workspace_ref':ref,
                'source_receipt':scene['receipt'],'provider_profile_ref':subscription.reference,
                'budget':{'max_calls':2,'timeout_seconds':60,'max_output_tokens':1024},'output_schema':scene['schema']})
            assert result['status']=='FAILED',result
            assert result['usage']['rpc_attempts'] is None and result['usage']['hard_rpc_cap']=='NOT_QUALIFIED'
            assert result['usage']['provider_receipts']==[] and result['candidate_json'] is None
            assert result['model']=={'provider_id':'openai','model_id':'gpt-5.4'}
            raw=canonical(result['events']).lower()
            assert any(marker in raw for marker in ('api key','apikey','authentication','credentials')),raw
            assert scene['file'].read_bytes()==scene['original']
            (scene['tmp_path']/'original-subscription-planning-preauth-observation.json').write_text(canonical(result))
        finally:await transport.close()
        assert transport.active=={}
    asyncio.run(check())


# The existing fixture creates real durable documents and a real workspace only;
# importing it does not replace any original CLI, API or execution mechanism.
from test_opencode_planning import scene
