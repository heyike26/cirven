"""Same-origin subscription control; native OAuth remains inside fixed OpenCode."""
from typing import Literal

from fastapi import Depends, Header
from pydantic import Field

from packages.adapters.opencode_account import OpenCodeAccount
from packages.domain.questions import Id, RequestModel


class SubscriptionLogin(RequestModel):
    operation_id: Id
    method: Literal['browser'] = 'browser'


class SubscriptionComplete(RequestModel):
    pass


class SubscriptionProfileInput(RequestModel):
    operation_id: Id
    id: Id
    model_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,200}$')


def register_subscription_routes(app, store, data_dir, source_root=None, *, authority=None, service=None):
    existing = getattr(app.state, 'subscription_account', None)
    if existing is not None:
        return existing
    value = service or OpenCodeAccount(app.state.provider_settings, data_dir)
    app.state.subscription_account = value
    app.state.provider_settings.subscription_account = value

    @app.get('/api/subscription/account')
    async def account():
        return await value.get()

    @app.post('/api/subscription/login')
    async def login(body: SubscriptionLogin):
        return await value.login(body.operation_id, body.method)

    @app.post('/api/subscription/attempts/{attempt_id}/complete')
    async def complete(attempt_id: Id, body: SubscriptionComplete):
        return await value.complete(attempt_id)

    @app.post('/api/subscription/attempts/{attempt_id}/cancel')
    async def cancel(attempt_id: Id, body: SubscriptionComplete):
        return await value.cancel(attempt_id)

    @app.post('/api/subscription/profiles', status_code=201)
    async def profile(body: SubscriptionProfileInput):
        return await value.save_profile(body.operation_id, body.id, body.model_id)

    @app.get('/api/subscription/profile-operations/{operation_id}')
    def profile_operation(operation_id: Id):
        return value.profile_operation(operation_id)

    if authority is not None:
        def host(x_eeas_desktop_host: str | None = Header(None)):
            authority.check_host(x_eeas_desktop_host, require_native=False)

        @app.get('/api/desktop-host/subscription/login-target', dependencies=[Depends(host)])
        def login_target(attempt_id: Id):
            return value.login_target(attempt_id)

    return value
