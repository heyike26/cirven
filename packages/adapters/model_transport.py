"""One explicit provider HTTP policy shared by original OpenCode wire relays.

No new model loop or retry policy. Legacy selected Bearer profiles remain
compatible; v2 profiles resolve their exact persisted materials per dispatch.
"""
from contextlib import contextmanager, asynccontextmanager
import hashlib
import math

from packages.adapters.provider_transport_policy import EnterpriseTransportPolicy,MaterialRef
from packages.domain.store import Conflict


def _selected_policy(profile, timeout_cap_seconds):
    if (type(timeout_cap_seconds) not in (int,float) or not math.isfinite(timeout_cap_seconds)
            or timeout_cap_seconds<=0):
        raise Conflict('MODEL_TRANSPORT_DEADLINE_EXHAUSTED')
    policy=getattr(profile,'transport_policy',None)
    if policy is None:
        key=getattr(profile,'api_key',None)
        if not isinstance(key,str) or not key:
            raise Conflict('EXPLICIT_PROVIDER_TRANSPORT_AUTH_REQUIRED')
        raw=key.encode('utf-8')
        ref=MaterialRef(id='legacy-selected-api-key',revision=1,sha256=hashlib.sha256(raw).hexdigest())
        policy=EnterpriseTransportPolicy.admit(policy_id='legacy-explicit-bearer',revision=1,
            auth_mode='API_KEY',api_key_ref=ref,read_secret=lambda actual:raw if actual==ref else b'',
            timeout_seconds=min(120,max(.1,timeout_cap_seconds)))
    return policy


@contextmanager
def open_model_transport(profile, *, timeout_cap_seconds):
    policy = _selected_policy(profile, timeout_cap_seconds)
    with policy.open_client(policy.reference,timeout_cap_seconds=timeout_cap_seconds) as client:
        yield client


@asynccontextmanager
async def open_async_model_transport(profile, *, timeout_cap_seconds):
    policy = _selected_policy(profile, timeout_cap_seconds)
    async with policy.open_async_client(policy.reference,timeout_cap_seconds=timeout_cap_seconds) as client:
        yield client


def private_model_redactions(profile):
    values=[getattr(profile,'api_key',None),*getattr(profile,'transport_secrets',())]
    # None/empty strings must not match every public event or enter legacy
    # redaction's string replacement loop.
    return list(dict.fromkeys(value for value in values if isinstance(value,str) and value))
