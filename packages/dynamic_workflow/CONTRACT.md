# Dynamic Workflow headless core v1

This package uses Python 3.10 standard library SQLite only. It can reuse an EEAS `Store.connect()` or own a standalone database. It does not dispatch, schedule, prompt, poll, install a tool, or change an OpenCode permission. The original OpenCode loop/todo/task remains the execution owner; existing planning proposals, taskbooks and harnesses remain their own versioned objects. This package adds a versioned plan/evidence association, not a replacement planning engine or fixed engineering template.

User outcome: a main model can revise a task dependency chain when evidence changes, while explicit user corrections remain protected. Existing tasks, sources and manual verification are traceable; incomplete requirements stay visible; an execution return or model claim cannot masquerade as acceptance. A read-only `ready_node_ids` projection is a candidate for an explicit next action and grants no authority.

## Public interface

```python
store = DynamicWorkflowStore(existing_store_or_sqlite_path)
store.create(project_id, plan, *, operation_id, workflow_id=None, actor="MODEL")
store.get(project_id, workflow_id, revision=None)
store.list(project_id, limit=100)
store.replan(project_id, workflow_id, plan, *, expected_ref, operation_id,
             actor="MODEL", reason)
store.answer(project_id, workflow_id, question_id, answer, *, expected_ref,
             operation_id, actor="USER")
store.set_locks(project_id, workflow_id, node_id, fields, *, expected_ref,
                operation_id, actor="USER")
store.attach_execution(project_id, workflow_id, node_id, actual_observation, *,
                       plan_ref, operation_id)
store.record_claim(project_id, workflow_id, node_id, text, *, plan_ref,
                   operation_id, source_refs=None)
store.record_validation(project_id, workflow_id, node_id, criterion_id, outcome, *,
                        plan_ref, evidence_refs, operation_id, actor)
store.operation(project_id, workflow_id_or_none, operation_id)
```

`WorkflowInvalidInput`, `WorkflowConflict`, `WorkflowNotFound` and `WorkflowIntegrityError` are exported. The adapter should map invalid input to 422, stale CAS/idempotency/ownership conflicts to 409, and missing records to 404. A missing operation is **UNOBSERVED**, not proof of non-execution. After an unknown write result the client reads the original operation; it must not create a new operation ID or reapply automatically.

IDs are opaque ASCII letters/digits/underscore/hyphen, 1–100 characters. `Ref={id,revision:int>=1,sha256:lower64}`; external source refs may additionally carry `kind` and `project_id`. A supplied project on a source must match. Workflow refs passed as expected_ref/plan_ref are the exact three fields returned by the core.

With an existing application Store, project existence is checked by its `project()` method. Standalone mode isolates records by supplied project_id but has no project registry. **References are provenance, not authorization.** Root/API/plugin adapters must authenticate the project, genuine user actor and actual evidence/operation references; never expose an untrusted model field that can impersonate USER/VERIFIER. The core does not independently verify a file, model assertion, test process or remote session merely because it receives a checksum.

## Plan

```json
{
  "title": "Task name",
  "goal": "User objective",
  "external_refs": [{"id":"existing_harness","revision":1,"sha256":"..."}],
  "questions": [{"id":"environment","text":"Missing constraint?","blocking":true,"source_refs":[]}],
  "nodes": [{
    "id":"investigate", "title":"Investigate", "description":"Work to do",
    "depends_on":[], "acceptance":[{"id":"criterion","text":"Observable requirement","method":"How to verify"}],
    "source_refs":[], "question_ids":["environment"],
    "agent_policy": {
      "model":{"mode":"MANUAL","value":"admitted_catalog_route_id"},
      "depth":{"mode":"AUTO","value":null}
    }
  }]
}
```

`title`, `goal`, `nodes` are required. Questions/external_refs and each optional node field have empty defaults. Nodes max512, questions max512, acceptance/source refs max128 each, complete version max2MiB. Empty acceptance is allowed during discovery but projects NEEDS_ACCEPTANCE. Dependencies are exact node IDs; unknown/self/cyclic/duplicate IDs fail before persistence. Input ordering is canonicalized and never invents an edge. No `status`, `PASSED` or arbitrary runtime fields are accepted in a plan.

Model/depth are independent. AUTO requires null; MANUAL requires a nonempty opaque string. Capabilities and supported variants are enumerated by the pinned runtime/catalog, never hardcoded here. AUTO means original-main-model selection from admitted runtime capabilities, not a new model call. A manual pin is a request; it does not prove an alias, model or variant was applied.

A model replan cannot alter/remove an existing manual model/depth pin, create a user pin, change an explicitly locked field, or delete a locked/pinned node. The USER actor may explicitly correct a plan/pin with a reason. Only USER can alter node locks or confirm a question. Lockable fields are title/description/depends_on/acceptance/source_refs/question_ids/agent_policy. User-unlocked fields remain model-editable.

Answers bind the exact question definition checksum. Replanning unchanged question definitions preserves answers; changed wording/sources/blocking or removed questions do not carry the answer into the new version. The old answer remains in immutable history. An unassigned blocking question applies globally; assigned questions affect their referenced nodes and dependency descendants.

## Versions, CAS and influence

Create/replan/answer/set_locks each save an immutable version and exact operation receipt in one SQLite transaction. Replan/answer/locks require the exact current expected_ref; stale revision or checksum changes nothing. Operations are idempotent by project+operation_id with canonical request hash; same request returns its original immutable receipt even after the head advanced, different input conflicts. Create without workflow_id derives a stable ID from project+operation_id, so an uncertain creation can be inspected by project and operation alone.

Responses are flat: version fields + `workflow_ref`, `operation_id`, `request_sha256`, `operation_status:"COMPLETED"`, `replayed`, `automatic_execution:false`, `read_only_execution:true`. `operation()` returns the original committed receipt, not the current head. `get()` additionally exposes current_revision/is_current_revision, actual_records, node_projection, ready_node_ids, coverage and dispatch_performed:false.

Each version records change.actor/reason/base_ref/changed_node_ids/affected_node_ids/removed_node_ids. Local changes include node definition, relevant questions/answers, goal and external_refs. Affected nodes include transitive successors over old and new DAGs. No affected node is automatically restarted. Evidence is associated by a transitive node-basis checksum: unrelated unchanged nodes keep CURRENT_BASIS evidence; changed nodes/descendants retain OLD_BASIS records for explicit review. The older plan remains readable by revision. This is conservative impact analysis, not semantic proof that two different requirements are equivalent.

## Recorded execution and validation

```json
{
  "owner":{"project_id":"p","session_id":"original_root_or_child","operation_id":"existing_operation","generation":4},
  "status":"RETURNED", "observed_at":"2026-10-15T08:00:00+00:00",
  "source_ref":{"id":"actual_operation_ref","revision":1,"sha256":"..."},
  "coverage":"EXACT_OPERATION",
  "routing":{
    "model":{"status":"OBSERVED","effective":"provider/model","evidence_level":"ASSISTANT_MESSAGE","evidence_refs":[{"id":"actual_assistant_message","revision":1,"sha256":"..."}]},
    "depth":{"status":"UNSUPPORTED","effective":null,"evidence_level":"NONE","evidence_refs":[]}
  }
}
```

Generation is an integer 0..<2^48; owner project must match. attach_execution accepts only an already observed original operation record. Runtime status is RUNNING/RETURNED/FAILED/CANCEL_REQUESTED/CANCELLED/TIMED_OUT/UNKNOWN_OUTCOME/UNKNOWN/IDLE. It does not turn a request to cancel into stopped, idle into done, or returned into verified. It never starts/adopts a process or follows a child link automatically.

Routing fields are independently OBSERVED/UNOBSERVED/UNSUPPORTED. A non-null effective value requires status OBSERVED, ASSISTANT_MESSAGE evidence level and at least one exact evidence ref admitted by the caller. SESSION_METADATA/TASK_METADATA_SELECTION may be retained as unobserved selection evidence, never effective inference proof. A missing routing field is UNOBSERVED. Requested node policy stays separate from actual routing, including unsupported depth; the core does not assert that the two strings map to the same registered route.

record_claim is MODEL_CLAIM only. record_validation requires actor USER or VERIFIER, an existing criterion, explicit PASSED/FAILED and nonempty exact evidence refs. Caller must admit the verifier and evidence. Latest criterion records for an unchanged basis are considered; all criteria, dependency validation and blocking confirmations are needed to project VALIDATED_BY_RECORDED_EVIDENCE. This is a report of admitted recorded evidence, not independent execution or semantic verification by this module.

Records are immutable and contain exact plan_ref/node_basis/owner/source refs; cross-session links coexist without reparenting actual sessions or inventing delegation/hand-off edges. The graph renderer must continue drawing actual delegation only from the existing collaboration evidence projection. Older late observations do not replace a newer timestamped returned observation. Node projection keeps execution_status and validation_status separate, so retained validation and a later running observation can both be shown.

V1 bounds each observation to128KiB and exposes at most2048 records; exceeding the read window fails explicitly while preserving original records. It does not silently truncate coverage. Scope is EXPLICIT_LINKED_RECORDS_ONLY, not all tasks or child-message history. The adapter should offer archival/pagination before scaling past this first-slice bound.

## Qualification boundary

Tests use real standalone/shared SQLite, separate CAS writers, restart, unknown-operation recovery, atomic failure rollback, manual pins/locks, question revision isolation, changed-source descendant impact, unaffected verification reuse, execution/claim/verification separation, route evidence levels and a512-node dependency chain. No Windows, paid/company model, original-loop runtime policy application, plugin-install compatibility or user efficiency improvement is qualified by this headless package alone.
