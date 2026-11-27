# Original OpenCode session owner

This owner starts one unmodified pinned OpenCode serve and uses its original HTTP/session/SSE APIs. The original loop selects all model iterations, tools, Plan/build agents and task children. The local provider relay only forwards the explicitly configured model with persistent RPC admission; it does not implement an agent loop.

## Public API and command line

Start the existing localhost API with `.venv/bin/python -m apps.api serve`. Configure the explicit model profile through the existing provider settings UI/API first. No ambient credentials or paid fallback are used. An empty credential cannot start a qualified original session.

For a simple command line, save the exact `profile_ref` returned by the provider settings API to `provider-ref.json`, then run this Python script with the same existing environment. The selected project/workspace can be omitted for a pure text conversation; the backend creates its actual internal readonly runtime and does not invent a LocalWorkspaces/FileSource authorization.

```python
import json, uuid, httpx
from pathlib import Path
with httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False, timeout=90) as api:
    bootstrap = api.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
    bootstrap.raise_for_status()
    api.headers.update({"Origin": str(api.base_url).rstrip("/"),
                        "X-EEAS-CSRF": bootstrap.json()["csrf_token"]})
    configured = api.get("/api/agent-sessions/configuration").json()
    opened = api.post("/api/agent-sessions/open", json={
        "operation_id": str(uuid.uuid4()), "project_id": None, "workspace_ref": None,
        "provider_profile_ref": json.loads(Path("provider-ref.json").read_text()),
        "capability_binding_sha256": configured["capability_binding_sha256"]})
    opened.raise_for_status()
    session = opened.json()
    route = "/api/projects/" + session["project_id"] + "/agent-sessions/" + session["session_ref"]["id"]
    operation_id = str(uuid.uuid4())
    sent = api.post(route + "/messages", json={
        "operation_id": operation_id, "session_ref": session["session_ref"],
        "generation": session["generation"], "agent": "plan",
        "raw_text": "Ask the original question tool to clarify the acceptance criteria."})
    sent.raise_for_status()
    print(json.dumps(sent.json(), ensure_ascii=False, indent=2))
    print(json.dumps(api.get(route).json(), ensure_ascii=False, indent=2))
```

Poll GET `route` and GET `route + /operations/{operation_id}`. Messages retain original info/parts/message IDs; root todo and original child session references are observations, not EEAS-invented tasks. The snapshot covers the last 200 root messages, root todo, child metadata, pending original questions/permissions. It does not claim complete child message history or upstream SSE replay. Local event cursors are persisted by GET `route + /events?after=0&limit=100`.

Reply to a pending question through POST `route + /questions/{id}/reply` with a new operation ID, exact session_ref/generation/request_ref and `answers: [["the actual label"]]`. Reject it using `/questions/{id}/reject`. Reply to original permissions through `/permissions/{id}/reply` with `reply: once|always|reject`, strictly from the returned allowed_choices. The original always field may contain `*` or wider command patterns; the owner refuses that expansion for an exact command or explore-child grant.

POST `/interrupt` with the exact control reference requests the original abort. Its acknowledgement does not prove prior external effects were rolled back. POST `/disconnect` revokes only the exact generation and created subprocess; the response includes last_control_operation. Reconnect after controller restart only by POST `/load` with operation_id and the immutable session_ref. The same original database/session is loaded, generation changes and no unknown POST is replayed. Persisted model-call allowances do not reset on load.

## Skills, MCP and command admission

M4 import/configuration must finish before opening a session, and the exact capability binding hash is admitted. Saved/probed records do not prove native load. Skill discovery uses the actual original `/api/skill` with `location[directory]`, bounded observation of the asynchronously initialized config plugin, and an exact name/source-path match. Original skill/MCP completed tool parts separately establish invocation, while qualified remains false.

Opening enabled local MCP requires explicit `allowed_effects: ["PROCESS_START"]`; remote MCP requires REMOTE_MCP_CONNECT. Exact permitted registry names are supplied in mcp_tool_allowlist, and original tool execution still asks for user permission. The actual fixture demonstrates original Skill plus official Python MCP startup/read of a genuine local file. PROCESS_START admits the selected executable's declared effects; it does not sandbox its filesystem or network. STRICT_OFFLINE refuses MCP until such isolation is qualified.

The pinned original naming rule preserves ASCII letters, digits, underscores and hyphens. `fixture-local` and `fixture_local` are distinct server IDs and their actual native tool calls are qualified separately. Owner admission conservatively rejects enabled server prefixes separated by an underscore, for example `fixture` and `fixture_local`: different server/tool combinations can then share one compound name, and the original public status API does not provide an unflattened catalog to resolve ownership. This refusal checks potential namespace ambiguity before startup, rather than claiming that every overlapping pair's advertised tools actually collide. Disabled saved sources neither block this admission nor receive invocation credit. The original name rule is mirrored; names are not rewritten to grant different tools.

Exact commands require a genuine selected WorkspaceRef, BACKGROUND_COMMAND and literal command_allowlist entries without permission globs. They execute only after an actual original permission reply. Native edit/write tools remain denied; reviewed file mutations use the existing DirectActions/CAS path. A literal shell command can itself have effects; this is explicit user admission rather than an OS containment claim.

## Startup boundary and limitations

Original V2 config ancestry is unconditional despite the legacy disable-project flag. The shared startup guard rejects actual `.opencode`, opencode.json/jsonc and Git metadata on lexical and resolved ancestors, including dangling links. Existing Git workspaces need a separately reviewed adapter; this slice does not claim general repository support. Private HOME/XDG config origins and config-byte drift are checked before reuse. Actual original CLI positive control starts a real plugin child; owner negative control rejects that source before process creation.

These are file admission checks, not an OS sandbox. Concurrent filesystem changes between inspection and original import remain a TOCTOU limitation. Original model RPC count/output tokens and owner lifetime are bounded; command costs, commercial USD costs and external side effects are not globally bounded. Unknown outcomes require inspection and explicit recovery and never trigger automatic resubmission. Model semantic correctness, company credentials/models and Windows owner runtime are separately unqualified by the Linux external-model-fixture tests.

Pinned primary source: [V2 config discovery](https://github.com/anomalyco/opencode/blob/733b2c627e5aec39cb33cc22373bdce33053ef2c/packages/core/src/config.ts), [external plugin import](https://github.com/anomalyco/opencode/blob/733b2c627e5aec39cb33cc22373bdce33053ef2c/packages/core/src/config/plugin/external.ts), [Skill discovery](https://github.com/anomalyco/opencode/blob/733b2c627e5aec39cb33cc22373bdce33053ef2c/packages/core/src/config/plugin/skill.ts).
