'use strict';
// Generated from desktop authority; do not edit independently.
// SHA256 apps/agent-shell/src/original-work-requirement-sources.ts 0911e42c476101fee60e6c9a466e0641bcc40881df43f1f2e875f1168e88c8ed
// SHA256 apps/agent-shell/src/work-source-editor.ts 324158759cfcc14e5aea9c1b46cd0bce84dab14e0c312c52fea104a46b75695e
const { canonical, hash, workRequirementScope } = require('./work-case-wire.cjs');
const api = Object.freeze({ request() { throw Error('WORK_SOURCE_TRANSPORT_REQUIRED'); } });
const sourceSame = (a, b) => canonical(a) === canonical(b);
const sourceSha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v), key = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,40}$/.test(v), count = (v, max = 512) => Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) <= max, text = (v, max = 4000) => typeof v === 'string' && Array.from(v).length <= max && !v.includes('\0');
function check(v) { if (!v)
    throw Error('WORK_SOURCE_CHANGED'); }
function exact(v, names) { return !!v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === names.length && Object.keys(v).every(k => names.includes(k)); }
function ids(v) { return Array.isArray(v) && v.length <= 100 && v.every(key) && new Set(v).size === v.length; }
function ref(v) { return v && id(v.id) && count(v.revision, 2147483647) && v.revision > 0 && sourceSha(v.sha256); }
function scope(v) { return v && exact(v, ['session_ref', 'generation', 'workspace_ref', 'root_operation_id']) && v.session_ref && id(v.session_ref.id) && count(v.session_ref.revision, 2147483647) && v.session_ref.revision > 0 && sourceSha(v.session_ref.sha256) && v.workspace_ref && sourceSha(v.workspace_ref.id) && sourceSha(v.workspace_ref.binding_sha256) && count(v.workspace_ref.revision, 2147483647) && v.workspace_ref.revision > 0 && count(v.generation, Number.MAX_SAFE_INTEGER) && v.generation > 0 && id(v.root_operation_id); }
function sourceQuery(v) { const r = v; return exact(r, ['session_ref', 'generation', 'workspace_ref', 'root_operation_id', 'root_sha256', 'source_project_id', 'requirement_ref', 'requirement_ids', 'acceptance_ids']) && scope({ session_ref: r.session_ref, generation: r.generation, workspace_ref: r.workspace_ref, root_operation_id: r.root_operation_id }) && sourceSha(r.root_sha256) && id(r.source_project_id) && ref(r.requirement_ref) && ids(r.requirement_ids) && r.requirement_ids.length > 0 && ids(r.acceptance_ids); }
function sourceDecision(v) { const r = v; if (!exact(r, ['session_ref', 'generation', 'workspace_ref', 'root_operation_id', 'root_sha256', 'source_project_id', 'requirement_ref', 'requirement_ids', 'acceptance_ids', 'operation_id', 'expected_revision', 'action', 'review_sha256']))
    return false; const { operation_id, expected_revision, action, review_sha256, ...q } = r; return sourceQuery(q) && id(operation_id) && count(expected_revision) && (action === 'ALLOW' ? sourceSha(review_sha256) : action === 'REVOKE' && review_sha256 === null); }
function flags(v) { check(v && v.metadata_only === true && v.model_calls === 0 && v.commands_started === 0 && v.source_files_written === 0 && v.permission_granted === false && v.source_workspace_authorized === false && v.original_project_changed === false && v.original_task_status_changed === false && v.automatic_adoption === false && v.whole_task_acceptance === 'NOT_ASSESSED' && v.authority === 'READONLY_SELECTED_REQUIREMENTS_FOR_ONE_ORIGINAL_ROOT'); }
function selection(v) {
    check(v && Array.isArray(v.requirements) && v.requirements.length > 0 && v.requirements.length <= 100 && new Set(v.requirements.map(r => r.id)).size === v.requirements.length && Array.isArray(v.acceptance) && v.acceptance.length <= 100 && new Set(v.acceptance.map(r => r.id)).size === v.acceptance.length);
    for (const r of v.requirements) {
        check(key(r.id) && text(r.text) && r.text.trim() && Array.isArray(r.source_refs) && r.source_refs.length <= 20);
        for (const s of r.source_refs)
            check(id(s.document_id) && count(s.revision, 2147483647) && s.revision > 0 && sourceSha(s.sha256) && count(s.line_start, Number.MAX_SAFE_INTEGER) && s.line_start > 0 && count(s.line_end, Number.MAX_SAFE_INTEGER) && s.line_end >= s.line_start);
    }
    for (const r of v.acceptance)
        check(key(r.id) && text(r.text) && r.text.trim() && ids(r.requirement_ids) && r.requirement_ids.length > 0 && r.requirement_ids.every(k => v.requirements.some(r => r.id === k)));
}
async function validateSourceReceipt(v, s, root, r) {
    check(v && new TextEncoder().encode(JSON.stringify(v)).length <= 32768);
    flags(v);
    check(v.kind === 'ORIGINAL_WORK_REQUIREMENT_SOURCE_OPERATION' && v.version === 1 && v.project_id === s.project_id && sourceSame(v.session_ref, s.session_ref) && v.root_operation_id === root && sourceSha(v.root_sha256) && v.source_project_id !== s.project_id && id(v.source_project_id) && ref(v.requirement_ref) && ids(v.requirement_ids) && v.requirement_ids.length > 0 && ids(v.acceptance_ids) && id(v.operation_id) && sourceSha(v.request_sha256) && count(v.revision) && v.revision > 0 && text(v.recorded_at, 100));
    check(v.grant_id === await hash({ schema: 'eeas.original-work-requirement-source.v1', project_id: s.project_id, session_id: s.session_ref.id, root_operation_id: root, source_project_id: v.source_project_id, requirement_id: v.requirement_ref.id }));
    check(v.action === 'ALLOW' ? v.state === 'ACTIVE' && sourceSha(v.review_sha256) && sourceSha(v.selection_sha256) : v.action === 'REVOKE' && v.state === 'REVOKED' && v.review_sha256 === null && v.selection_sha256 === null);
    if (r) {
        check(sourceDecision(r) && v.request_sha256 === await hash(r) && v.revision === r.expected_revision + 1);
        for (const k of ['root_sha256', 'source_project_id', 'requirement_ref', 'requirement_ids', 'acceptance_ids', 'operation_id', 'action', 'review_sha256'])
            check(sourceSame(v[k], r[k]));
    }
    return structuredClone(v);
}
async function sourceAuthorityRef(v) { return { source_project_id: v.source_project_id, grant_id: v.grant_id, revision: v.revision, receipt_sha256: await hash(v) }; }
async function validateSourceReview(v, s, q) {
    flags(v);
    check(sourceQuery(q) && v.kind === 'ORIGINAL_WORK_REQUIREMENT_SOURCE_REVIEW' && v.version === 1 && v.read_only === true && v.grant_recorded === false && v.query_sha256 === await hash(q) && text(v.title, 200) && ['manual', 'mock', 'live_suggestion'].includes(v.origin));
    const b = v.basis, { session_ref, generation, workspace_ref, root_operation_id } = q;
    check(b && b.schema === 'eeas.original-work-requirement-source-review.v1' && b.project_id === s.project_id && sourceSame(b.scope, { session_ref, generation, workspace_ref, root_operation_id }) && b.root_sha256 === q.root_sha256 && b.source_project_id === q.source_project_id && q.source_project_id !== s.project_id && sourceSame(b.requirement_ref, q.requirement_ref) && sourceSame(b.requirement_ids, q.requirement_ids) && sourceSame(b.acceptance_ids, q.acceptance_ids) && count(b.expected_revision) && b.authority === v.authority && v.review_sha256 === await hash(b));
    selection(v.selected);
    check(sourceSame(v.selected.requirements.map(r => r.id), [...q.requirement_ids].sort()) && sourceSame(v.selected.acceptance.map(r => r.id), [...q.acceptance_ids].sort()) && b.selection_sha256 === await hash(v.selected));
    check(Array.isArray(b.source_statuses) && b.source_statuses.length === q.requirement_ids.length && new Set(b.source_statuses.map(r => r.id)).size === b.source_statuses.length && b.source_statuses.every(r => q.requirement_ids.includes(r.id) && ['CURRENT', 'UNSOURCED'].includes(r.status)));
    const grant = await hash({ schema: 'eeas.original-work-requirement-source.v1', project_id: s.project_id, session_id: s.session_ref.id, root_operation_id, source_project_id: q.source_project_id, requirement_id: q.requirement_ref.id });
    check(b.grant_id === grant);
    if (v.current_grant) {
        await validateSourceReceipt(v.current_grant, s, root_operation_id);
        check(v.current_grant.grant_id === grant && v.current_grant.revision === b.expected_revision && v.current_grant.root_sha256 === q.root_sha256);
    }
    else
        check(b.expected_revision === 0);
    return structuredClone(v);
}
class OriginalWorkRequirementSourcesApi {
    client;
    constructor(client = api) {
        this.client = client;
    }
    path(s) { return '/api/projects/' + encodeURIComponent(s.project_id) + '/agent-sessions/' + encodeURIComponent(s.session_ref.id) + '/work-requirement-sources'; }
    async catalog(s, root, pid, selected = null, offset = 0, signal) {
        check(id(pid) && pid !== s.project_id && count(offset, 1000000) && (!selected || ref(selected)));
        const request = { ...workRequirementScope(s, root), source_project_id: pid, catalog_offset: offset, selected_requirement_ref: selected }, v = await this.client.request(this.path(s) + '/catalog', 'POST', request, signal, false);
        check(new TextEncoder().encode(JSON.stringify(v)).length <= 1048576);
        flags(v);
        check(v.kind === 'ORIGINAL_WORK_REQUIREMENT_SOURCE_CATALOG' && v.version === 1 && v.read_only === true && v.grant_recorded === false && v.project_id === s.project_id && sourceSame(v.scope, workRequirementScope(s, root)) && v.source_project_id === pid && sourceSha(v.root_sha256) && v.query_sha256 === await hash(request));
        for (const page of [v.catalog, v.grants])
            check(page && page.offset === offset && page.limit === 8 && Array.isArray(page.entries) && page.entries.length <= 8 && (page.next_offset === null || page.entries.length === 8 && page.next_offset === offset + 8));
        check(v.catalog.scope === 'EXPLICIT_OTHER_PROJECT_READONLY_PREVIEW' && v.grants.scope === 'THIS_ORIGINAL_ROOT_ALL_SOURCE_PROJECTS');
        for (const row of v.catalog.entries)
            check(ref(row.ref) && (row.title === null || text(row.title, 200)) && ['READY', 'NOT_CONFIRMED', 'UNAVAILABLE'].includes(row.reason) && row.eligible === (row.reason === 'READY') && (row.reason === 'UNAVAILABLE' ? row.origin === null && row.confirmed === null : ['manual', 'mock', 'live_suggestion'].includes(row.origin ?? '') && row.confirmed === row.eligible));
        for (const row of v.grants.entries) {
            await validateSourceReceipt(row, s, root);
            check(row.root_sha256 === v.root_sha256);
        }
        if (v.selected) {
            check(selected && sourceSame(v.selected.ref, selected) && count(v.selected.current_revision, 2147483647) && v.selected.current_revision >= selected.revision);
            selection(v.selected.payload);
            check(text(v.selected.payload.title, 200) && typeof v.selected.payload.confirmed === 'boolean' && v.selected.payload.schema_version === 'eeas.requirements.v1' && ['manual', 'mock', 'live_suggestion'].includes(v.selected.payload.origin));
            const rows = v.selected.sources, items = v.selected.payload.requirements;
            check(Array.isArray(rows) && rows.length === items.length && new Set(rows.map(r => r.id)).size === rows.length && rows.every(r => items.some(i => i.id === r.id) && ['CURRENT', 'UNSOURCED', 'UNAVAILABLE', 'SUPERSEDED'].includes(r.status)));
        }
        else
            check(selected === null);
        return structuredClone(v);
    }
    async review(s, q, signal) { check(sourceQuery(q) && sourceSame({ session_ref: q.session_ref, generation: q.generation, workspace_ref: q.workspace_ref, root_operation_id: q.root_operation_id }, workRequirementScope(s, q.root_operation_id))); const v = await this.client.request(this.path(s) + '/review', 'POST', q, signal, false); check(new TextEncoder().encode(JSON.stringify(v)).length <= 1048576); return validateSourceReview(v, s, q); }
    async command(s, r, signal) { check(sourceDecision(r) && sourceSame({ session_ref: r.session_ref, generation: r.generation, workspace_ref: r.workspace_ref, root_operation_id: r.root_operation_id }, workRequirementScope(s, r.root_operation_id))); return validateSourceReceipt(await this.client.request(this.path(s) + '/decisions', 'POST', r, signal, false), s, r.root_operation_id, r); }
    async operation(s, r, signal) { check(sourceDecision(r) && r.generation <= s.generation); const q = { ...workRequirementScope(s, r.root_operation_id), operation_id: r.operation_id, request_sha256: await hash(r) }; return validateSourceReceipt(await this.client.request(this.path(s) + '/operations/inspect', 'POST', q, signal, false), s, r.root_operation_id, r); }
}
module.exports = { canonical, hash, workRequirementScope, sourceSame, sourceSha, sourceQuery, sourceDecision, validateSourceReceipt, sourceAuthorityRef, validateSourceReview, OriginalWorkRequirementSourcesApi };
