"""Actual HTTPX/TLS/proxy and shared policy, not paid inference qualification."""
import asyncio
from types import SimpleNamespace

import pytest

from packages.adapters.model_transport import open_async_model_transport
from packages.adapters.provider_transport_policy import CABytes, ProxySpec, TransportPolicyError
from test_provider_transport_policy import certificates, running_server, ForwardProxy, none, ref, lower_headers
from test_model_transport_relays import profile


def test_async_tls_explicit_ca_connect_proxy_and_hostname_validation(certificates,monkeypatch):
    ca=CABytes(certificates['root'],ref('async-root',certificates['root']))
    monkeypatch.setenv('SSL_CERT_FILE',str(certificates['root_path']))
    with running_server(tls=certificates) as gateway,running_server(ForwardProxy) as proxy:
        policy=none(ca=ca,proxy=ProxySpec(proxy.url))
        async def run():
            async with policy.open_async_client(policy.reference,timeout_cap_seconds=2) as client:
                assert (await client.request('POST',gateway.url+'/async-tls',json={})).status_code==200
                with pytest.raises(TransportPolicyError,match='TRANSPORT_CONNECTERROR'):
                    await client.request('GET',gateway.url.replace('localhost','127.0.0.1')+'/wrong-host')
            defaults=none()
            async with defaults.open_async_client(defaults.reference) as client:
                with pytest.raises(TransportPolicyError,match='TRANSPORT_CONNECTERROR'):
                    await client.request('GET',gateway.url+'/ambient-ca-denied')
        asyncio.run(run())
        assert len(gateway.observed)==1
        assert proxy.observed


@pytest.mark.parametrize('mode',['NONE','BEARER','CUSTOM'])
def test_async_shared_policy_auth_drift_and_request_overrides_are_checked_before_dispatch(mode):
    with running_server() as gateway:
        selected,materials,key=profile(gateway.url,mode)
        async def run():
            async with open_async_model_transport(selected,timeout_cap_seconds=2) as client:
                assert client.effective_timeout_seconds==2
                async with client.stream('POST',gateway.url+'/async-stream',json={'selected':True}) as response:
                    assert response.status_code==200
                    assert await response.aread()
                for override in [{'timeout':100},{'follow_redirects':True},{'extensions':{}},
                                 {'headers':{'Authorization':'unselected'}}]:
                    with pytest.raises(TransportPolicyError):
                        await client.request('POST',gateway.url+'/override',json={},**override)
                if mode!='NONE':
                    materials[key]=b'changed-private-material'
                    with pytest.raises(TransportPolicyError,match='SECRET_SOURCE_DRIFT'):
                        await client.request('GET',gateway.url+'/drift')
        asyncio.run(run());assert len(gateway.observed)==1
        headers=lower_headers(gateway.observed[0])
        assert headers.get('authorization')==('Bearer qa-explicit-only-key' if mode=='BEARER' else None)
        assert headers.get('x-corporate-auth')==('qa-explicit-only-key' if mode=='CUSTOM' else None)
