# EEAS Dynamic Workflow — OpenCode plugin 0.2.0

One independently removable, self-contained **server plugin** for fixed OpenCode
1.19.0 / `733b2c627e5aec39cb33cc22373bdce33053ef2c`. It uses the official V1
`config`, `tool.execute.before`, `event`, `dispose` and custom-tool interfaces.
It does not patch OpenCode, introduce another model loop, install an npm package at startup,
enable experimental background subagents, or bundle Electron.

The same plugin provides a headless plan/actual graph, per-node catalog model and
reasoning-variant routing, independently manual/AUTO model and depth policy, and
authenticated local lifecycle wake hints. `eeas_workflow_graph` is a real
read-only native tool. The original main model can inspect it and select a named
node/route alias through the original `task`; this is neither a replacement task
tool nor an autonomous scheduler. For a selected existing workflow, an explicitly admitted `eeas_workflow_replan`
native tool can propose a MODEL replan through the host's versioned Core API.
`eeas_workflow_plan_operation` only reads the original write operation. Neither
tool answers as the user, changes human locks, verifies acceptance or executes
a task. Initial workflow creation stays host-owned.

## Install offline / use without EEAS UI

Copy this complete directory, including `manifest.json`, to a versioned local
path. Verify the supplied manifest SHA and each source SHA before importing code.
Create a private config and policy using the published JSON protocol (or EEAS's
`prepare_workflow_plugin` helper). Add this exact V1 declaration to an explicit
OpenCode JSON configuration:

```json
{"plugin":[["file:///absolute/dynamic-workflow/index.mjs",{
  "configPath":"/absolute/owner/plugin-config.json",
  "configSha256":"sha256-of-exact-config-bytes"
}]]}
```

Windows uses a proper `file:///D:/.../index.mjs` URI and native absolute paths in
options. Do not put the module in an auto-scanned `.opencode/plugins` directory.
Do not use the unrelated V2 `plugins`/`setup` shape. `--pure`/`OPENCODE_PURE=true`
disables this official V1 loader; the admitting host must remove that flag only
for this exact reviewed package while retaining origin/permission guards.

`manage.mjs configure /absolute/request.json` activates, disables or changes an
explicit existing **JSON** config through a local lock and expected-byte-hash
CAS. Request fields: `configPath`, `expectedConfigSha256`, `bundleRoot`,
`manifestSha256`, `pluginConfigPath`, `pluginConfigSha256`, optionally
`disable:true` or explicit `replaceEntry` for an old installed file URI. Existing
unrelated plugins/agents/permissions are preserved. JSONC editing is unsupported.
Restart the original instance after any installation/removal/version upgrade.
Importing management code does nothing. Uninstall removes only this exact tuple;
it never deletes user files, history, state, another plugin or the original CLI.

Without a sink, the module works independently: inspect the read-only tool or
the local projection JSON, and build any graph client against its protocol.
With an EEAS host, the fixed loopback sink requires a dedicated owner-scoped
`EEAS_WORKFLOW_EVENT_TOKEN` environment value of 32–256 safe characters. No
provider credentials appear in plugin config or graph. Network hints contain
only bounded session/status/reference metadata, never prompt/tool/error bodies.
The host must validate active owner/generation/native-session association and
re-read original state; a wake hint grants no execution authority.

## Routing, evidence, limits

The admitted catalog enumerates provider/model/variant combinations; no fixed
brand/model names or reasoning levels are invented. Alias creation alone is
`SELECTED_NOT_YET_OBSERVED`. Only actual assistant-message fields constitute
observed routing evidence; wire fixtures separately qualify request fields.
`session.idle` remains idle, not task success or acceptance. Parent lineage,
actual task delegation with call IDs, and planned dependencies have distinct
edge kinds. There are no fabricated handoff or file-conflict edges.

The host's CAS policy file is read at each owned alias's next native task
boundary. Model and depth MANUAL pins filter independently. AUTO choices stay
with the original main model and admitted catalog. Missing combinations fail
before dispatch. In-flight child model changes are unsupported. New plan nodes
need a safe-point reconnect to register aliases; the projection explicitly says
`RECONNECT_REQUIRED`. Resuming an existing child via this plugin alias's
`task_id` requires a future exact-owner route check and is currently rejected;
unrelated original task/continuation behavior is unchanged.

This first routing slice creates explicitly read-only aliases, preserving the
original build/plan/explore agents and task tool. It does not infer permission
inheritance from an agent name. Write-capable aliases require separate host
admission. Limits: 64 compiled nodes, 16 catalog routes, 8 variants/route,
256 aliases, 256 observed sessions, 512 edges/dispatches, 2048 wake identities.
Large plans should compile the relevant horizon and reconnect explicitly.

Projection is event-observed, not historical replay or a complete subtree.
Restart archives the previous exact projection without adopting stale execution
facts or replaying tasks. The local output uses lock/CAS and preserves foreign
bytes on conflict. Orphan locks fail closed; this module never steals a lock.

No existing community plugin source is copied. Upstream official interfaces and
candidate/license decisions are recorded in D qualification research. This
new private package is `UNLICENSED` until the project owner chooses publication
terms. Node/runtime-fixture tests do not qualify Windows, ChatGPT Pro, company
gateways or commercial cost limits. Dependencies/providers remain host-owned.

## MODEL replan capability (0.2.0)

Set `model_replan:true` only with an existing `plan_ref` and admitted local sink.
The tool uses the complete MIT Zod 4.1.8 npm original vendored at `vendor/zod`,
matching the fixed OpenCode workspace catalog, to supply actual typed plugin
arguments. No fake JSON-schema object, global install or runtime download is used.
The registry tarball SHA512, original package/version/LICENSE and all recursive
source hashes accompany this bundle.

`EEAS_WORKFLOW_PLAN_TOKEN` is independent of the event token and grants only
MODEL replan of this exact project/workflow/owner generation. The endpoint is
derived from the admitted loopback sink origin: a single POST to
`/api/dynamic-workflow/plugin-plan`, then only GET
`/api/dynamic-workflow/plugin-plan/operations/{operation_id}` for an uncertain
result. Redirects, arbitrary URLs, actor impersonation and automatic POST retry
are unavailable. A pending UNKNOWN blocks a new operation until the old one is
checked. A 404 lookup remains UNKNOWN.

A local durable intent precedes the POST. Same ID/different bytes is a conflict;
repeating an uncertain ID only looks up the original operation. On success the
plugin verifies exact operation ID, project, workflow reference and the host's
`plugin_request_sha256` of the complete packet, then re-reads the synchronized
policy. COMMITTED is a plan-write result, never execution completion or
acceptance. Local receipt/policy failure preserves that known result and reports
the independent persistence/synchronization problem. New aliases still require
a safe-point reconnect.
