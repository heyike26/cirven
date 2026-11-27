'use strict';
// Generated pure validators; edit the renderer authority, then run this source generator.
// SHA256 apps/agent-shell/src/original-work-requirements.ts 069eb91b1e7d58985943cea5f5a20a1f07f923ca84b1f9e3dd717c0ffba6bc85
// SHA256 apps/agent-shell/src/work-requirement-editor.ts 49879572089fffcbc5ae82e79fd7c63f5242dc996a208d739043200865bfddb7
const { createHash, webcrypto: crypto } = require('node:crypto');
const canonical = v => JSON.stringify(v, (_k, x) => x !== null && typeof x === 'object' && !Array.isArray(x) ? Object.fromEntries(Object.keys(x).sort().map(k => [k, x[k]])) : x);
const hash = v => createHash('sha256').update(canonical(v)).digest('hex');
const same = (a, b) => canonical(a) === canonical(b);
function check(value) { if (!value)
    throw Error('ORIGINAL_WORK_REQUIREMENTS_CHANGED'); }
const sha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v);
const key = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,40}$/.test(v);
const count = (v, max = 512) => Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) <= max;
const text = (v, max) => typeof v === 'string' && Array.from(v).length <= max && !v.includes('\0');
const reference = (v) => !!v && id(v.id) && count(v.revision, 2147483647) && v.revision > 0 && sha(v.sha256);
const keys = (v) => Array.isArray(v) && v.length <= 100 && v.every(key) && new Set(v).size === v.length;
const subset = (values, parent) => values.every(v => parent.includes(v));
function sources(rows, ids) { check(Array.isArray(rows) && rows.length === ids.length && new Set(rows.map(r => r.id)).size === rows.length && rows.every(row => ids.includes(row.id) && ['CURRENT', 'UNSOURCED', 'SUPERSEDED', 'UNAVAILABLE'].includes(row.status))); }
function validRequirementSourceAuthority(v) { const a = v; return !!a && typeof a === 'object' && !Array.isArray(a) && Object.keys(a).length === 4 && Object.keys(a).every(k => ['source_project_id', 'grant_id', 'revision', 'receipt_sha256'].includes(k)) && id(a.source_project_id) && sha(a.grant_id) && count(a.revision, 512) && a.revision > 0 && sha(a.receipt_sha256); }
function parseRequirementLink(value, session, root, rootSha) {
    check(value && value.kind === 'ORIGINAL_WORK_REQUIREMENT_OPERATION' && value.version === 1 && value.project_id === session.project_id && same(value.session_ref, session.session_ref) && value.root_operation_id === root && value.root_sha256 === rootSha && id(value.operation_id) && sha(value.request_sha256) && count(value.revision) && value.revision > 0 && reference(value.requirement_ref) && keys(value.requirement_ids) && keys(value.acceptance_ids) && text(value.recorded_at, 100));
    check(value.action === 'ASSOCIATE' ? value.requirement_ids.length > 0 && sha(value.selection_sha256) : value.action === 'REMOVE' && value.requirement_ids.length === 0 && value.acceptance_ids.length === 0 && value.selection_sha256 === null);
    check(value.source_authority === undefined || validRequirementSourceAuthority(value.source_authority) && value.source_authority.source_project_id !== session.project_id);
    check(value.metadata_only === true && value.model_calls === 0 && value.commands_started === 0 && value.source_files_written === 0 && value.permission_granted === false && value.original_task_status_changed === false && value.whole_task_acceptance === 'NOT_ASSESSED');
    return value;
}
async function validateView(value, session, root, request) {
    const { include_catalog, catalog_offset, selected_requirement_ref, detail_kind, detail_id, selected_source_authority, ...scope } = request;
    const options = { selected: selected_requirement_ref, sourceAuthority: selected_source_authority, detail: detail_kind === null ? undefined : { kind: detail_kind, id: detail_id } };
    check(value && new TextEncoder().encode(JSON.stringify(value)).length <= 1048576);
    check(value.kind === 'ORIGINAL_WORK_REQUIREMENTS' && value.version === 1 && value.project_id === session.project_id && same(value.scope, scope) && value.query_sha256 === await hash(request) && sha(value.root_sha256) && await hash(value.root_basis) === value.root_sha256 && text(value.label, 280) && text(value.root_status, 80) && count(value.revision));
    const basis = value.root_basis;
    check(basis.project_id === session.project_id && same(basis.session_ref, session.session_ref) && same(basis.workspace_ref, scope.workspace_ref) && basis.root_operation_id === root && count(basis.root_generation, Number.MAX_SAFE_INTEGER) && basis.root_generation >= 1 && basis.root_generation <= scope.generation && typeof basis.native_session_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(basis.native_session_id) && typeof basis.native_message_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(basis.native_message_id) && sha(basis.request_sha256));
    check(value.read_only === true && value.model_calls === 0 && value.commands_started === 0 && value.source_files_written === 0 && value.automatic_adoption === false && value.whole_task_acceptance === 'NOT_ASSESSED' && value.current_workspace_verified === false && value.target_environment_verified === false && value.requirement_source_scope === 'REGISTERED_DOCUMENT_VERSIONS_ONLY' && value.coverage === 'EXPLICIT_SELECTED_REQUIREMENTS_ONLY_NOT_TEST_CASE_COVERAGE_OR_TARGET_ACCEPTANCE');
    check(Array.isArray(value.links) && value.links.length <= 10 && new Set(value.links.map(r => r.requirement_ref?.id)).size === value.links.length);
    for (const link of value.links) {
        parseRequirementLink(link, session, root, value.root_sha256);
        check(link.action === 'ASSOCIATE' && link.revision <= value.revision);
    }
    check(Array.isArray(value.impacts) && value.impacts.length === value.links.length);
    const seen = new Set();
    for (const impact of value.impacts) {
        const link = value.links.find(row => row.operation_id === impact.operation_id);
        check(link && !seen.has(impact.operation_id));
        seen.add(impact.operation_id);
        check(same(impact.requirement_ref, link.requirement_ref) && same(impact.requirement_ids, link.requirement_ids) && same(impact.acceptance_ids, link.acceptance_ids) && impact.selection_sha256 === link.selection_sha256 && impact.automatic_adoption === false && (impact.title === null || text(impact.title, 200)) && ['CURRENT', 'SELECTED_UNCHANGED_NEWER_BUNDLE', 'NEEDS_REVIEW', 'UNAVAILABLE'].includes(impact.review_status) && typeof impact.current_bundle_confirmed === 'boolean' && typeof impact.requires_explicit_adoption === 'boolean');
        for (const [rows, parent] of [[impact.changed_requirement_ids, link.requirement_ids], [impact.changed_acceptance_ids, link.acceptance_ids], [impact.stale_source_requirement_ids, link.requirement_ids]])
            check(keys(rows) && subset(rows, parent));
        if (impact.review_status === 'UNAVAILABLE')
            check(impact.current_ref === null && impact.sources.length === 0 && impact.requires_explicit_adoption === true);
        else {
            check(reference(impact.current_ref) && impact.current_ref.id === link.requirement_ref.id && impact.current_ref.revision >= link.requirement_ref.revision);
            if (impact.current_ref.revision === link.requirement_ref.revision)
                check(same(impact.current_ref, link.requirement_ref));
            sources(impact.sources, link.requirement_ids);
            check(same([...impact.stale_source_requirement_ids].sort(), impact.sources.filter(row => ['SUPERSEDED', 'UNAVAILABLE'].includes(row.status)).map(row => row.id).sort()));
            const needs = impact.changed_requirement_ids.length || impact.changed_acceptance_ids.length || impact.stale_source_requirement_ids.length || !impact.current_bundle_confirmed;
            check(impact.review_status === (needs ? 'NEEDS_REVIEW' : same(impact.current_ref, link.requirement_ref) ? 'CURRENT' : 'SELECTED_UNCHANGED_NEWER_BUNDLE'));
            check(impact.requires_explicit_adoption === !same(impact.current_ref, link.requirement_ref));
        }
    }
    const catalog = value.catalog;
    check(catalog && catalog.scope === 'SAME_EXECUTION_PROJECT_ONLY' && catalog.included === request.include_catalog && catalog.offset === request.catalog_offset && catalog.limit === 8 && Array.isArray(catalog.entries) && catalog.entries.length <= 8 && catalog.entries.every(row => reference(row.ref) && (row.title === null || text(row.title, 200)) && ['READY', 'NOT_CONFIRMED', 'UNAVAILABLE'].includes(row.reason) && row.eligible === (row.reason === 'READY') && (row.reason === 'UNAVAILABLE' ? row.origin === null && row.confirmed === null : ['manual', 'mock', 'live_suggestion'].includes(row.origin ?? '') && row.confirmed === row.eligible)) && (catalog.next_offset === null || catalog.entries.length === 8 && catalog.next_offset === request.catalog_offset + 8));
    if (!catalog.included)
        check(catalog.entries.length === 0 && catalog.next_offset === null);
    check(value.selected_issue === null || value.selected_issue === 'IMMUTABLE_REQUIREMENT_VIEW_UNAVAILABLE');
    const selected = value.selected, expected = options.selected ?? value.links.at(-1)?.requirement_ref ?? null;
    if (selected) {
        check(value.selected_issue === null && reference(selected.ref) && same(selected.ref, expected) && text(selected.title, 200) && typeof selected.confirmed === 'boolean' && ['manual', 'mock', 'live_suggestion'].includes(selected.origin) && count(selected.current_revision, 2147483647) && selected.current_revision >= selected.ref.revision);
        for (const rows of [selected.requirements, selected.acceptance]) {
            check(Array.isArray(rows) && rows.length <= 100 && new Set(rows.map(r => r.id)).size === rows.length);
            for (const row of rows)
                check(key(row.id) && text(row.text_excerpt, 240) && count(row.text_length, 4000) && row.text_length >= 1 && Array.from(row.text_excerpt).length === Math.min(row.text_length, 240) && sha(row.text_sha256) && row.text_truncated === (row.text_length > 240));
        }
        const linked = value.links.find(r => same(r.requirement_ref, selected.ref)), authority = options.sourceAuthority ?? linked?.source_authority;
        check(same(selected.source_authority, authority));
        if (authority)
            check(validRequirementSourceAuthority(authority) && authority.source_project_id !== session.project_id);
        check(selected.requirements.every(r => count(r.source_count, 20)));
        const reqIds = selected.requirements.map(r => r.id);
        check(selected.acceptance.every(r => Array.isArray(r.requirement_ids) && r.requirement_ids.length > 0 && r.requirement_ids.length <= 100 && r.requirement_ids.every(key) && subset(r.requirement_ids, reqIds)));
        sources(selected.sources, reqIds);
        if (options.detail) {
            const detail = selected.detail, row = (detail?.kind === 'requirement' ? selected.requirements : selected.acceptance).find(r => r.id === detail?.id);
            check(detail && row && same({ kind: detail.kind, id: detail.id }, options.detail) && text(detail.text, 4000) && Array.from(detail.text).length === row.text_length);
            const raw = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(detail.text));
            check([...new Uint8Array(raw)].map(v => v.toString(16).padStart(2, '0')).join('') === row.text_sha256);
        }
        else
            check(selected.detail === null);
    }
    else
        check(expected === null || value.selected_issue === 'IMMUTABLE_REQUIREMENT_VIEW_UNAVAILABLE');
    return structuredClone(value);
}
const selection = (() => {
    const same = (a, b) => canonical(a) === canonical(b), copy = v => structuredClone(v);
    const sha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
    const id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v);
    const count = (v, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) <= max;
    const object = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
    const exact = (v, names) => object(v) && Object.keys(v).length === names.length && Object.keys(v).every(k => names.includes(k));
    function check(value, code = 'WORK_REQUIREMENT_LOCAL_INVALID') { if (!value)
        throw Error(code); }
    function reference(value) { const r = value; return exact(r, ['id', 'revision', 'sha256']) && id(r.id) && count(r.revision, 2147483647) && r.revision >= 1 && sha(r.sha256); }
    function ids(value) { return Array.isArray(value) && value.length <= 100 && value.every(v => typeof v === 'string' && /^[A-Za-z0-9_-]{1,40}$/.test(v)) && new Set(value).size === value.length; }
    function choice(value) { const c = value; return exact(c, ['requirement_ref', 'requirement_ids', 'acceptance_ids', ...(c.source_authority !== undefined ? ['source_authority'] : [])]) && (c.source_authority === undefined || validRequirementSourceAuthority(c.source_authority)) && reference(c.requirement_ref) && ids(c.requirement_ids) && ids(c.acceptance_ids); }
    function scope(value, owner) { const s = value; return exact(s, ['session_ref', 'generation', 'workspace_ref', 'root_operation_id']) && same(s.session_ref, owner.session_ref) && same(s.workspace_ref, owner.workspace_ref) && s.root_operation_id === owner.root_operation_id && count(s.generation) && s.generation >= 1; }
    function request(value, owner) { const r = value; return exact(r, ['session_ref', 'generation', 'workspace_ref', 'root_operation_id', 'operation_id', 'root_sha256', 'expected_revision', 'action', 'requirement_ref', 'requirement_ids', 'acceptance_ids', ...(r.source_authority !== undefined ? ['source_authority'] : [])]) && (r.source_authority === undefined || validRequirementSourceAuthority(r.source_authority) && r.source_authority.source_project_id !== owner.project_id) && scope({ session_ref: r.session_ref, generation: r.generation, workspace_ref: r.workspace_ref, root_operation_id: r.root_operation_id }, owner) && id(r.operation_id) && sha(r.root_sha256) && count(r.expected_revision, 512) && reference(r.requirement_ref) && ids(r.requirement_ids) && ids(r.acceptance_ids) && (r.action === 'ASSOCIATE' ? r.requirement_ids.length > 0 : r.action === 'REMOVE' && r.requirement_ids.length === 0 && r.acceptance_ids.length === 0); }
    function draft(value, owner) { const d = value; return exact(d, ['scope', 'root_sha256', 'revision', 'choice', 'basis']) && scope(d.scope, owner) && sha(d.root_sha256) && count(d.revision, 512) && choice(d.choice) && object(d.basis); }
    function verifyReceipt(value, r, checksum, owner) { parseRequirementLink(value, { project_id: owner.project_id, session_ref: owner.session_ref }, owner.root_operation_id, r.root_sha256); check(value.operation_id === r.operation_id && value.request_sha256 === checksum && value.revision === r.expected_revision + 1 && value.action === r.action && same(value.requirement_ref, r.requirement_ref) && same(value.requirement_ids, r.requirement_ids) && same(value.acceptance_ids, r.acceptance_ids) && same(value.source_authority, r.source_authority), 'WORK_REQUIREMENT_RECEIPT_CHANGED'); return copy(value); }
    function workRequirementDraft(view, selected) {
        check(choice(selected) && view.selected && same(view.selected.ref, selected.requirement_ref), 'WORK_REQUIREMENT_SELECTION_UNAVAILABLE');
        const visible = view.selected, requirements = [...selected.requirement_ids].sort().map(key => { const row = visible.requirements.find(r => r.id === key), source = visible.sources.find(r => r.id === key); return row ? { id: key, text_sha256: row.text_sha256, source_count: row.source_count, source_status: source?.status ?? 'UNAVAILABLE' } : { id: key, missing: true }; }), acceptance = [...selected.acceptance_ids].sort().map(key => { const row = visible.acceptance.find(r => r.id === key); return row ? { id: key, text_sha256: row.text_sha256, requirement_ids: [...row.requirement_ids].sort() } : { id: key, missing: true }; });
        return { scope: copy(view.scope), root_sha256: view.root_sha256, revision: view.revision, choice: copy(selected), basis: { ...(visible.source_authority ? { source_authority: copy(visible.source_authority) } : {}), ref: copy(visible.ref), confirmed: visible.confirmed, origin: visible.origin, current_revision: visible.current_revision, requirements, acceptance } };
    }
    function workRequirementDraftMatches(view, d) { try {
        return same(workRequirementDraft(view, d.choice), d);
    }
    catch {
        return false;
    } }
    function workRequirementCanSave(view, d) { try {
        check(workRequirementDraftMatches(view, d), 'WORK_REQUIREMENT_REVIEW_REQUIRED');
        const v = view.selected;
        check(same(v.source_authority, d.choice.source_authority), 'WORK_REQUIREMENT_SOURCE_AUTHORITY_CHANGED');
        check(v.confirmed && v.current_revision === d.choice.requirement_ref.revision && d.choice.requirement_ids.length > 0, 'WORK_REQUIREMENT_SELECTION_STALE');
        check(d.choice.requirement_ids.every(key => v.requirements.some(r => r.id === key) && !v.sources.some(r => r.id === key && ['SUPERSEDED', 'UNAVAILABLE'].includes(r.status))), 'WORK_REQUIREMENT_SELECTION_STALE');
        check(d.choice.acceptance_ids.every(key => v.acceptance.some(a => a.id === key && a.requirement_ids.every(id => d.choice.requirement_ids.includes(id)))), 'WORK_REQUIREMENT_INCOMPLETE_CRITERION');
        return true;
    }
    catch {
        return false;
    } }
    function workRequirementUnchanged(view, d) { const old = view.links.find(r => r.requirement_ref.id === d.choice.requirement_ref.id); return !!old && same(old.requirement_ref, d.choice.requirement_ref) && same([...old.requirement_ids].sort(), [...d.choice.requirement_ids].sort()) && same([...old.acceptance_ids].sort(), [...d.choice.acceptance_ids].sort()) && same(old.source_authority, d.choice.source_authority); }
    return { draftFor: workRequirementDraft, matches: workRequirementDraftMatches, canSave: workRequirementCanSave, unchanged: workRequirementUnchanged, validChoice: choice, validRequest: request, validDraft: draft, verifyReceipt };
})();
module.exports = { canonical, hash, same, validateView, parseRequirementLink, ...selection };
