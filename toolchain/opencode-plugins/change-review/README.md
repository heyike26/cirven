# Cirven change review observer

Independent, dependency-free server plugin for fixed OpenCode **1.19.0**,
commit `733b2c627e5aec39cb33cc22373bdce33053ef2c`. It observes original
`edit`, `write`, and the exact supported `apply_patch` subset. It grants no
permission, changes no tool arguments/results, starts no agent or model, runs no
command, and uses no Git. The App alone may install it for a new explicitly
authorized code session. Existing read-only sessions stay read-only.

## Admission and activation

The trusted App creates an ordinary private directory outside the workspace,
including `ledger/`, and writes the following ordinary UTF-8 JSON. The only
configuration entry is environment `EEAS_CHANGE_REVIEW_CONFIG` pointing to that
absolute file. No project config, prompt, tool argument, network location or
ambient credential is consulted.

```json
{
  "schema_version": 1,
  "scope": {
    "project_id": "project-id",
    "session_ref": {"id":"owner-id","revision":1,"sha256":"64-lowercase-hex"},
    "generation": 1,
    "workspace_ref": {"id":"workspace-id","revision":1,"binding_sha256":"64-lowercase-hex"},
    "workspace_root": "D:\\approved\\project",
    "root_identity": {"dev":"device-number","ino":"inode-number"},
    "allowed_write_paths": ["src/", "README.md"],
    "ledger_root": "D:\\private-appdata\\code-session-generation-1\\ledger",
    "budget_root": "D:\\private-appdata\\code-session\\budget"
  },
  "scope_sha256": "SHA256(canonical(scope))",
  "native_session_id": null
}
```

`canonical` is recursive, sorted-key compact JSON, exported from `files.mjs`.
Config is bounded to 512 KiB and at most 128 explicit path grants.
The displayed hash placeholders are explanatory and are rejected as inputs.
`allowed_write_paths` retains the App's semantics: list item `*` explicitly grants
the entire root; a trailing `/` grants a directory subtree; other items grant one
exact file. String `*` is also accepted for callers already using that form.
`.` / `..`, absolute grants and aliases are rejected. Root identity uses Node
BigInt `stat.dev/ino` decimal strings. The App must establish cross-language
Windows identity equivalence; Linux checks do not establish it.

Append this bundle's **absolute file URI** `index.mjs` to the supported original
plugin config. The export is `{id:'eeas.change-review',server}`. Verify the exact
manifest and loader admission; original `--pure` mode disables plugins and must
only be changed by the App's audited branch. Do not enable arbitrary plugins or
run online installation to make this bundle load.

Loading validates the directory/root/scope and writes immutable `ready.json`:
`{schema:'eeas.change-review.ready.v1',scope_sha256,plugin_id,native_session_id}`.
Null native ID is **DISARMED**. After creating the original session, the App
atomically rewrites only `native_session_id` to the exact new native ID. Each
hook rereads the file. Once non-null, changing scope/generation/native ID requires
a new plugin instance and private ledger. `ready.json` records first load and is
not rewritten on bind. Reusing an identical ready receipt is allowed, but it
does not prove a tool ran. Backend admission must also pin bundle source hashes.

## Immutable file protocol

Call key is `sha256(canonical({native_session_id,call_id}))`. The plugin creates
`calls/<callkey>/` exclusively; it never overwrites an earlier call's snapshots.

- `before.json`: `schema:'eeas.change-review.before.v1'`, plugin `{id,version}`,
  `scope_sha256`, `project_id`, `session_ref`, `generation`, `workspace_ref`,
  `native_session_id`, `call_id`, `tool`, `args_sha256`,
  `status:'BEFORE_CAPTURED'`, `reason_codes`, `started_at`,
  `files:[{path,before,parents:[{path,dev,ino}]}]`, `before_sha256`.
  The latter hashes canonical JSON **without** its own field. Parent identities
  come from the actual directory chain. The approval reader must constrain them
  to the exact root/file ancestors, not treat arbitrary ledger paths as reads.
  This is the backend's exact call/path/baseline input for its preapproval drift
  guard; missing/partial/concurrent evidence must not grant a write.
- `result.json`: `schema:'eeas.change-review.record.v1'`, the same exact scope
  and call fields, `args_sha256`, `status`, `reason_codes`,
  `tool_returned_successfully`, `started_at`, `finished_at`,
  `files:[{path,status,reason_codes,before,after}]`, `record_sha256`.
  This hash similarly excludes its own field. Status is `COMPLETE`, `PARTIAL`,
  `FAILED`, `UNKNOWN`, or `REJECTED` and describes **observer evidence**, not a
  grant, operation success, cancellation, semantic validation or rollback.
- A snapshot is `{state:'UTF8'|'ABSENT',sha256,bytes,line_count,blob,stat}`.
  UTF8 content retains every byte, BOM and line ending in
  `blobs/<sha256>.utf8`; stat is `{dev,ino,size,mtime_ns,ctime_ns}` decimal
  strings. ABSENT has null hash/blob/stat, zero bytes/lines. An unavailable side
  is **null**, never ABSENT. An empty present file has the SHA of empty bytes;
  backend may project the empty side's public content hash consistently.
- Metadata never includes tool arguments, prompt or tool output; `args_sha256`
  binds them without copying them. Snapshot blobs intentionally contain only
  approved workspace file bytes. Store them in the user's private app data, not
  the workspace, public response, package or general logging stream.

The backend must join the exact native session + callID to **one** original
completed tool part, verify owner/generation/workspace/config/bundle and all
hashes, and only then expose a complete pair as `FULL_FILE`. The official hook
does not contain messageID: none is fabricated. Duplicate/ambiguous original
part IDs are not resolved by guessing. A ready receipt or after hook alone is
insufficient. No current disk/buffer data may fill historical holes.

## Proof and failure boundaries

- `edit`: exact argument transformation plus original `metadata.filediff.patch`
  must agree with both full captured sides; fixed upstream's indentation trim
  is handled. Fuzzy edits, unsupported line transforms or formatter differences
  stay PARTIAL. `write`: an ABSENT creation must equal intended content exactly.
  Existing-file write stays PARTIAL unless a same-call official permission diff
  also attests the captured base; upstream write has no old-content result.
- `apply_patch`: bounded Add/Update/Delete/Move targets are all enumerated and
  grant-checked **before any target read**. Exact, unique context and original
  per-file metadata patches must agree. Moves require an absent destination;
  unsupported/fuzzy syntax is not silently interpreted. Both move paths and
  deletion's ABSENT side remain explicit.
- Stable descriptor reads check file identity/size/mtime/ctime before and after,
  ordinary ancestor identities and root identity. Links, junction/alias paths,
  hardlinked files, invalid UTF-8, NUL/control binary content, invalid paths and
  oversized data are rejected. A changed ancestor or atomic file replacement
  is PARTIAL. All captures remain under the original trusted scope.
- Per-file side <=256 KiB; all before sides per call <=256 KiB; before+after
  total <=512 KiB; <=20 patch operations and <=128 retained calls per ledger.
  Capacity is a recording gap, not permission to delete history or repeat a task.
  Claims and immutable conflict markers mark overlapping paths PARTIAL even
  across observer instances. No background polling/watcher/model runs.
- An original error event records FAILED. Idle/dispose before success records
  UNKNOWN. A process crash leaves before-only evidence; restart never rereads a
  replacement before or replays a call, and can record UNKNOWN. Late hooks and
  repeat call IDs never rewrite a result. Private stale claims fail closed;
  the App must reconcile them or use a fresh authorized generation.

## Resource budget and retention

Private ledger logical file bytes are capped at **96 MiB**, with **1 MiB** kept
for bounded capacity/failure receipts. A call must reserve **1 MiB + 64 KiB**
before creating its call directory or reading any target: 512 KiB total blobs,
256 KiB before metadata, 256 KiB result metadata, and bounded claim/conflict/
reservation overhead. Permanent, exclusive `reservations/0000.json` slots make
the data budget at most **89** captures; this budget can be reached before the
absolute 128-call limit. Reservation records never shrink after failure,
UNKNOWN or restart. No unbounded mutable counter or background GC runs.
`budget_root` is shared across generations of the same exact
`{project_id,session_ref,workspace_ref}`; its canonical SHA is
`budget_identity_sha256`. It is an ordinary precreated directory, outside and
not overlapping the workspace or ledger. Each generation keeps its own immutable
ledger/ready/scope; it never relabels old work as new. Reservations are keyed by
`sha256(canonical({scope_sha256,call_key}))`, so a new generation cannot reset
the shared quota or reuse another generation's reservation.

Startup performs a bounded ordinary-file **stat-only** audit of the current
ledger and shared budget (<=8192 files, <=1024 directories, depth <=5), not blob
content or old generations' blob trees. Permanent reservations account for all
producer writes; the App also bounds its immutable admission index. Subsequent
allocation verifies the fixed budget header and <=89 small reservation slots.
Unknown or drifted budgets refuse new capture. Full budget records one bounded
`capacity.json` with `CAPACITY_REACHED`; it creates no partial new call or blob
set, does not abort/retry the original tool, and never evicts prior records.
The receipt is a timestamped gap fact, not a permanent instruction to block a
future capture if the temporary active limit later clears.

At most **8** calls across the shared budget retain capture state, using
exclusive `active/` leases. `claims/` and `conflicts/` also live in `budget_root`
for cross-generation overlap detection. For one instance, retained state is
<=2 MiB before buffers and <=4 MiB UTF-8 argument payload, plus bounded strings/JSON/temporary
read buffers. These are retained-payload bounds, not a measured whole-runtime
RSS limit. Full arguments are validated before cloning; unknown/oversized input
is not copied into a ledger. `args_sha256` may be null for rejected input that
could not be safely hashed under those bounds. Filesystem allocation overhead
is distinct from logical bytes; no measured disk savings are claimed.

Private data must not be externally edited to bypass these reservations; an
unexpected ledger is rejected on re-open. The App owns **cross-session** quota
and user-visible retain/export/delete management. This observer does not silently
delete failed/UNKNOWN evidence, source files, user edits or older generations,
and does not create another generation when full.

**This observer provides no atomic write isolation and cannot prevent original
tools from overwriting external saves.** Fixed edit/write can read a base before
waiting for permission and have no cross-process FileTime CAS. A temporary human
save completely overwritten between hooks may leave no observable final trace.
COMPLETE means full captured sides and matching stated tool evidence, not proof
that no such race occurred. The App's preapproval drift guard is separate and
still has a non-atomic cross-process window; never advertise automatic protection
or infer authorship for mixed content. Detected mismatch/concurrency is PARTIAL.
The plugin never changes permissions or silently denies/rewrites the original
tool to disguise this limitation. Backend grants remain authoritative.

## Tests and source qualification

Use fixed existing Node 22 with temp/cache on D:

```sh
EEAS_CHANGE_REVIEW_TEST_ROOT=./tmp/change-review/tmp \
EEAS_CHANGE_REVIEW_UPSTREAM_ROOT=./tmp/change-review/upstream \
NODE_COMPILE_CACHE=./tmp/change-review/node-cache \
node --test toolchain/opencode-plugins/change-review/tests/*.test.mjs
```

Tests use actual ordinary files and official-shaped hooks. The source-shape test
checks exact originals extracted from the pinned archive (it explicitly skips if
the source fixture is absent). Source archive SHA:
`ab433f7a5fc9e54c90da18bd3457298009f426424805f043aa32c03543b4a192`.
Hook definitions: `packages/plugin/src/index.ts:266–284`; invocation order:
`packages/opencode/src/session/tools.ts:106–125`; original tools:
`packages/opencode/src/tool/{edit,write,apply_patch}.ts`.
[Official plugin hooks](https://opencode.ai/docs/plugins/) describe the supported
extension surface; current docs do not replace pinned-source qualification.
No upstream implementation or dependency is copied into this plugin. This is
local Node/filesystem qualification, not Windows, native editor, actual original
loader/CLI or real model qualification. App/runtime integration is a separate
explicit test and remains NOT_RUN for this standalone slice.


### 原 OpenCode 1.19.0 搜索默认值兼容（QA28）

该固定版本的 `glob` / `grep` 把可省略的 `path`（以及 grep 的 `include`）直接放入权限元数据。省略参数会令原权限列表的 JsonValue 编码失败，表现为 `GET /permission` HTTP 400，用户无法查看待确认权限。

在已绑定且仍有效的同一原会话中，`tool.execute.before` 将省略的搜索目录明确为其原有效工作目录，将省略的 grep 文件过滤明确为空串（原 ripgrep 的无过滤分支）。显式路径、过滤、模式和非法参数均原样交给原工具；不改读写权限、不授予许可、不发模型请求、不扫描文件。未绑定、其他会话、关闭或漂移的作用域不处理。仅在本插件已装载的工作目录写入会话生效；不声称所有原会话均已修复。实际原生升级与完整任务验收须另有回执。


### Child file-tool observations (source implementation, qualification pending)

Observer 0.1.1 also captures a child tool when the existing original private SDK
proves its parent chain reaches the bound native root, with the same workspace.
The injected SDK client is the one provided by the fixed 1.19.0 plugin interface:
https://github.com/anomalyco/opencode/blob/733b2c627e5aec39cb33cc22373bdce33053ef2c/packages/plugin/src/index.ts
The legacy SDK session.get contract accepts path.id and forwards a GET only:
https://github.com/anomalyco/opencode/blob/733b2c627e5aec39cb33cc22373bdce33053ef2c/packages/sdk/js/src/gen/sdk.gen.ts
No model-provided URL, parent label or title can supply this proof. Each lookup
is limited to twelve ancestors and a shared two-second deadline, with at most
eight concurrent lookups. Missing, cyclic, foreign-directory or changed lineage
leaves no qualified write capture. GET failure has no model/tool fallback.

Only after obtaining the existing permanent call reservation does the observer
publish ledger/children/<digest>.json (at most 8 KiB). The before/after record pins
that immutable lineage ref. Child and root calls share the original 96 MiB
budget, eight active capture slots, path claims and conflict records. The SDK
permission prompt stays native; this observer grants no write or permission.
An idle event closes only that native session's calls, preserving other child
calls still in progress. Root binding and scope.json remain unchanged.

The App checks the exact child tool message and native request, original root
scope and lineage ref before positive path approval, then retains the original
file-version/parent-handle checks. Once-only approval remains a pre-write drift
guard: it does not make the original tool write atomic. Historical child entries
need completed child tool observations, matching before/after record hashes and
the same lineage proof. Partial history remains partial. Native child write,
Windows filesystem, IDE diff and whole-task qualification have not run for this
source change; the implementation and authored tests do not constitute a pass.
