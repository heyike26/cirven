"""Private companion capability management, reusing existing native owners."""
from fastapi import Depends, Header, Query

from packages.domain.native_capabilities import (CapabilityEnableInput, MCPConfigureInput,
    MCPProbeInput, NativeCapabilities, SkillImportInput)
from packages.domain.questions import Id
from packages.domain.native_tools import ToolOwnerScope
from packages.domain.native_capabilities import _json
from packages.domain.tasks import canonical
from packages.domain.store import Conflict
from packages.extensions.service import ExtensionRegistry


def register_native_capability_routes(app, store, data_dir, *, authority, service=None):
    if getattr(app.state, "native_capabilities", None) is not None:
        return app.state.native_capabilities
    value = service or NativeCapabilities(store, data_dir,
        provider_settings=getattr(app.state, "provider_settings", None),
        transport_reader=lambda: getattr(app.state, "desktop_transport", None),
        extension_registry=ExtensionRegistry(store, data_dir),
        planning_toolkit=getattr(app.state, "planning_toolkit", None))
    app.state.native_capabilities = value

    def host(x_eeas_desktop_host: str | None = Header(None)):
        authority.check_host(x_eeas_desktop_host, require_native=False)

    root = "/api/desktop-host/capabilities"

    @app.get(root + "/get", dependencies=[Depends(host)])
    def get(network_mode: str = Query(default="AS_CONFIGURED")):
        return value.get(network_mode=network_mode)

    @app.get(root + "/tools", dependencies=[Depends(host)])
    async def tools(scope: str | None = Query(default=None, max_length=1024)):
        result = value.tool_catalog()
        if scope is not None:
            from pydantic import ValidationError
            try:
                selected = ToolOwnerScope.model_validate(_json(scope)).model_dump()
            except (ValueError, TypeError, ValidationError):
                raise Conflict('An exact tool owner scope is required') from None
            sessions = getattr(app.state, 'agent_sessions', None)
            if sessions is None:
                raise Conflict('Original session tool observation is unavailable')
            result.update(await sessions.tool_catalog(selected))
            if value.native_binding_snapshot(validate_mcp_ids=set())['sha256'] != result['capability_binding_sha256']:
                raise Conflict('Capability configuration changed during tool observation')
        if len(canonical(result).encode('utf-8')) > 524288:
            raise Conflict('Tool catalogue exceeds its bounded response')
        return result

    @app.get(root + "/mcp/{ident}", dependencies=[Depends(host)])
    def read_mcp(ident: Id, revision: int = Query(ge=1),
                 sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        return value.mcp_content({'id': ident, 'revision': revision, 'sha256': sha256})

    @app.get(root + '/operations/{operation_id}', dependencies=[Depends(host)])
    def operation(operation_id: Id):
        return value.operation(operation_id)

    @app.post(root + "/skills/import", dependencies=[Depends(host)])
    def import_skill(body: SkillImportInput):
        return value.import_skill(body)

    @app.get(root + "/skills/{ident}", dependencies=[Depends(host)])
    def read_skill(ident: Id):
        return value.skill_content(ident)

    @app.post(root + "/skills/{ident}/enabled", dependencies=[Depends(host)])
    def skill_enabled(ident: Id, body: CapabilityEnableInput):
        return value.set_enabled("skills", ident, body)

    @app.post(root + "/mcp/configure", dependencies=[Depends(host)])
    def configure_mcp(body: MCPConfigureInput):
        return value.configure_mcp(body)

    @app.post(root + "/mcp/{ident}/enabled", dependencies=[Depends(host)])
    def mcp_enabled(ident: Id, body: CapabilityEnableInput):
        return value.set_enabled("mcp", ident, body)

    @app.post(root + "/mcp/{ident}/probe", dependencies=[Depends(host)])
    async def probe_mcp(ident: Id, body: MCPProbeInput):
        return await value.probe_mcp(ident, body)

    @app.post(root + "/executions/{execution_id}/probe", dependencies=[Depends(host)])
    async def probe_execution(execution_id: Id, project_id: Id, network_mode: str = "AS_CONFIGURED"):
        return await value.probe_execution(project_id, execution_id, network_mode=network_mode)

    return value
