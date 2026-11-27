'use strict';
// Generated from renderer authority; edit that source and run the source generator.
// SHA256 apps/agent-shell/src/original-work-test-cases.ts 673435c5e7472b1ef9c301165b573596f189abb18b82e054292b99eb6856d1b6
// SHA256 apps/agent-shell/src/work-case-editor.ts a324ba8bed0c3cbab3efd183bdc5583c2056d5b731797cd076453a24bbae6e19
// SHA256 apps/agent-shell/src/original-work-requirements.ts 069eb91b1e7d58985943cea5f5a20a1f07f923ca84b1f9e3dd717c0ffba6bc85
// SHA256 apps/agent-shell/src/original-session-files.ts e7f0fb0a83b3317251cff5b34cf90c341c13b0db1cf104bf29baa6a2325fcfbe
const { createHash } = require('node:crypto');
const canonical = v => JSON.stringify(v, (_k, x) => x !== null && typeof x === 'object' && !Array.isArray(x) ? Object.fromEntries(Object.keys(x).sort().map(k => [k, x[k]])) : x);
const hash = v => createHash('sha256').update(canonical(v)).digest('hex');
const same = (a, b) => canonical(a) === canonical(b), sha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v), id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v), count = (v, max = 512) => Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) <= max;
const ref = (v) => !!v && id(v.id) && count(v.revision, 2147483647) && v.revision > 0 && sha(v.sha256), text = (v, max) => typeof v === 'string' && Array.from(v).length <= max && !v.includes('\0');
const ids = (v) => Array.isArray(v) && v.length <= 100 && v.every(x => typeof x === 'string' && /^[A-Za-z0-9_-]{1,40}$/.test(x)) && new Set(v).size === v.length;
const requirementStatuses = ['CURRENT', 'SELECTED_UNCHANGED_NEWER_BUNDLE', 'NEEDS_REVIEW', 'UNAVAILABLE', 'REQUIREMENT_LINK_CHANGED', 'NOT_CHECKED_BUDGET'];
function check(value) { if (!value)
    throw Error('ORIGINAL_WORK_CASE_MAPPING_CHANGED'); }
function flags(v) { check(v && v.model_calls === 0 && v.commands_started === 0 && v.source_files_written === 0 && v.permission_granted === false && v.original_task_status_changed === false && v.whole_task_acceptance === 'NOT_ASSESSED' && v.automatic_replay === false && v.coverage === 'HUMAN_DECLARED_MAPPING_NOT_MEASURED_TEST_COVERAGE' && v.current_workspace_verified === false && v.target_environment_verified === false && v.writer_attribution === 'NOT_PROVEN'); }
function source(v, generation) { check(v && ref(v.command_ref) && ref(v.report_ref) && count(v.command_generation, Number.MAX_SAFE_INTEGER) && v.command_generation >= 1 && v.command_generation <= generation && typeof v.call_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v.call_id) && text(v.report_path, 1024) && v.report_path.length > 0 && !/[\\:*?\[\]\x00-\x1f\x7f]/.test(v.report_path) && !v.report_path.startsWith('/') && v.report_path.split('/').every(p => p !== '' && p !== '.' && p !== '..' && !['.git', '.eeas-sdk-state', '.eeas-runtime'].includes(p.toLowerCase()) && !p.startsWith('.eeas-write-')) && sha(v.report_sha256) && count(v.case_ordinal, 1999) && sha(v.case_sha256)); }
function commandFacts(v) { check(v && ['EXITED', 'UNKNOWN_OUTCOME'].includes(v.command_status) && (v.command_exit_code === null || Number.isSafeInteger(v.command_exit_code)) && ['NEW_SNAPSHOT', 'CHANGED_BYTES', 'UNCHANGED_BYTES', 'BEFORE_UNAVAILABLE'].includes(v.association) && Array.isArray(v.reason_codes) && v.reason_codes.length <= 32 && v.reason_codes.every(code => typeof code === 'string' && /^[A-Z_]{1,100}$/.test(code))); if (v.command_status === 'EXITED')
    check(Number.isSafeInteger(v.command_exit_code)); if (v.command_status === 'UNKNOWN_OUTCOME')
    check(v.command_exit_code === null && v.reason_codes.includes('COMMAND_OUTCOME_UNKNOWN')); if (['UNCHANGED_BYTES', 'BEFORE_UNAVAILABLE'].includes(v.association))
    check(v.reason_codes.includes('NEW_REPORT_GENERATION_NOT_PROVEN')); }
async function caseIdentity(caseRef, basis, s, scope, project) { source(s, scope.generation); check(caseRef?.revision === 1 && caseRef.sha256 === s.case_sha256 && caseRef.id === await hash({ schema: 'eeas.junit.case.v1', report_ref: s.report_ref, ordinal: s.case_ordinal })); check(basis && basis.schema === 'eeas.command-test-report.v1' && same(basis.scope, { project_id: project, session_ref: scope.session_ref, generation: s.command_generation, workspace_ref: scope.workspace_ref }) && same(basis.command_ref, s.command_ref) && basis.command_generation === s.command_generation && basis.call_id === s.call_id && basis.report_path === s.report_path && basis.report_sha256 === s.report_sha256 && basis.root_operation_id === scope.root_operation_id && id(basis.operation_id) && s.report_ref.revision === 1 && s.report_ref.id === await hash(basis)); }
async function workCaseSourceFromReport(report, item) { check(report.cases.some(row => same(row, item))); return { command_ref: structuredClone(report.basis.command_ref), command_generation: report.basis.command_generation, call_id: report.basis.call_id, report_path: report.basis.report_path, report_sha256: report.basis.report_sha256, report_ref: structuredClone(report.report_ref), case_ordinal: item.ordinal, case_sha256: await hash(item) }; }
async function workCaseMappingKey(rootSha, requirementId, s) { return hash({ schema: 'eeas.work-case-slot.v1', root_sha256: rootSha, requirement_id: requirementId, report_ref: s.report_ref, case_ordinal: s.case_ordinal }); }
async function receipt(v, session, scope, rootSha) {
    flags(v);
    check(v.kind === 'ORIGINAL_WORK_CASE_OPERATION' && v.version === 1 && v.project_id === session.project_id && same(v.session_ref, scope.session_ref) && v.root_operation_id === scope.root_operation_id && v.root_sha256 === rootSha && v.metadata_only === true && id(v.operation_id) && sha(v.request_sha256) && count(v.revision) && v.revision > 0 && count(v.requirement_revision) && sha(v.mapping_key) && text(v.recorded_at, 100));
    if (v.action === 'REMOVE') {
        check(v.selection === null && v.proof === null);
        return;
    }
    check(v.action === 'MAP' && v.selection && v.proof);
    const s = v.selection, p = v.proof;
    check(id(s.requirement_link_operation_id) && sha(s.requirement_link_request_sha256) && ids(s.acceptance_ids) && s.acceptance_ids.length > 0 && p.requirement_link_operation_id === s.requirement_link_operation_id && p.requirement_link_request_sha256 === s.requirement_link_request_sha256 && same(p.acceptance_ids, s.acceptance_ids) && ref(p.requirement_ref) && ids(p.requirement_ids) && p.requirement_ids.length > 0 && sha(p.selection_sha256) && v.mapping_key === await workCaseMappingKey(rootSha, p.requirement_ref.id, s.source));
    await caseIdentity(p.case_ref, p.report_basis, s.source, scope, session.project_id);
    check(p.case_label?.ordinal === s.source.case_ordinal && text(p.case_label.suite, 160) && text(p.case_label.classname, 160) && text(p.case_label.name, 160) && ['PASSED', 'FAILED', 'ERROR', 'SKIPPED', 'UNKNOWN'].includes(p.case_label.status) && typeof p.case_label.text_truncated === 'boolean' && ['INCONSISTENT', 'NO_TESTS', 'FAILED', 'SKIPPED', 'PASSED_WITH_SKIPS', 'PASSED'].includes(p.report_outcome));
    commandFacts(p);
}
const originalFileScope = (() => {
    const sha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
    const positive = (v) => Number.isSafeInteger(v) && Number(v) > 0;
    const ref = (v) => !!v && typeof v === 'object' && typeof v.id === 'string' && v.id.length > 0 && v.revision === 1 && sha(v.sha256);
    function originalFileScope(session) {
        const w = session.workspace_ref;
        if (!w || !sha(w.id) || !positive(w.revision) || !sha(w.binding_sha256) || !ref(session.session_ref) || !positive(session.generation))
            return;
        return { project_id: session.project_id, session_ref: session.session_ref, generation: session.generation, workspace_ref: { id: w.id, revision: w.revision, binding_sha256: w.binding_sha256 } };
    }
    ;
    return originalFileScope;
})();
function workRequirementScope(session, root) { const scope = originalFileScope(session); check(scope && id(root)); const { project_id: _, ...rest } = scope; return { ...rest, root_operation_id: root }; }
const api = Object.freeze({ request() { throw Error('WORK_CASE_TRANSPORT_REQUIRED'); } });
class OriginalWorkTestCasesApi {
    client;
    constructor(client = api) {
        this.client = client;
    }
    path(s) { return '/api/projects/' + encodeURIComponent(s.project_id) + '/agent-sessions/' + encodeURIComponent(s.session_ref.id) + '/work-test-cases'; }
    async inspect(session, root, options = {}, signal) {
        const scope = workRequirementScope(session, root), request = { ...scope, offset: options.offset ?? 0, case_source: options.caseSource ?? null, selected_requirement_link_operation_id: options.selectedLink ?? null, mapping_key: options.mappingKey ?? null }, v = await this.client.request(this.path(session) + '/inspect', 'POST', request, signal, false);
        check(v && new TextEncoder().encode(JSON.stringify(v)).length <= 1048576);
        flags(v);
        check(v.kind === 'ORIGINAL_WORK_CASE_MAPPINGS' && v.version === 1 && v.project_id === session.project_id && same(v.scope, scope) && v.query_sha256 === await hash(request) && sha(v.root_sha256) && v.root_sha256 === await hash(v.root_basis) && v.root_basis.project_id === session.project_id && same(v.root_basis.session_ref, scope.session_ref) && same(v.root_basis.workspace_ref, scope.workspace_ref) && v.root_basis.root_operation_id === root && count(v.root_basis.root_generation, scope.generation) && v.root_basis.root_generation >= 1 && sha(v.root_basis.request_sha256) && typeof v.root_basis.native_session_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v.root_basis.native_session_id) && typeof v.root_basis.native_message_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v.root_basis.native_message_id) && text(v.root_status, 80) && count(v.revision) && count(v.requirement_revision) && v.read_only === true && v.metadata_only === true);
        check(Array.isArray(v.rows) && v.rows.length === Math.min(20, Math.max(0, v.page.total - request.offset)) && v.page.offset === request.offset && v.page.limit === 20 && count(v.page.total, 128) && v.page.next_offset === (request.offset + v.rows.length < v.page.total ? request.offset + v.rows.length : null) && new Set(v.rows.map(row => row.operation.mapping_key)).size === v.rows.length);
        for (const row of v.rows) {
            await receipt(row.operation, session, scope, v.root_sha256);
            check(row.operation.action === 'MAP' && row.operation.revision <= v.revision && requirementStatuses.includes(row.requirement_status) && ['VERIFIED_SAVED_CASE', 'UNAVAILABLE', 'NOT_CHECKED_BUDGET'].includes(row.report_status) && row.automatic_adoption === false && row.new_execution_proven === false);
            const p = row.operation.proof, impact = row.requirement_impact;
            if (impact) {
                check(impact.operation_id === p.requirement_link_operation_id && same(impact.requirement_ref, p.requirement_ref) && same(impact.requirement_ids, p.requirement_ids) && same(impact.acceptance_ids, p.acceptance_ids) && impact.selection_sha256 === p.selection_sha256 && impact.review_status === row.requirement_status && impact.automatic_adoption === false);
            }
            else
                check(['REQUIREMENT_LINK_CHANGED', 'NOT_CHECKED_BUDGET', 'UNAVAILABLE'].includes(row.requirement_status));
        }
        check(Array.isArray(v.requirement_links) && v.requirement_links.length <= 10 && new Set(v.requirement_links.map(row => row.operation_id)).size === v.requirement_links.length && v.requirement_links.every(row => id(row.operation_id) && sha(row.request_sha256) && ref(row.requirement_ref) && count(row.acceptance_count, 100) && typeof row.available === 'boolean' && (row.title === null || text(row.title, 200))));
        const selected = v.selected_requirement_link;
        if (selected) {
            const link = v.requirement_links.find(row => row.operation_id === selected.operation_id);
            check(link && selected.request_sha256 === link.request_sha256 && same(selected.requirement_ref, link.requirement_ref) && ids(selected.requirement_ids) && typeof selected.available === 'boolean' && Array.isArray(selected.acceptance) && selected.acceptance.length <= 100);
            if (options.selectedLink)
                check(selected.operation_id === options.selectedLink);
            for (const item of selected.acceptance)
                check(ids([item.id]) && text(item.text_excerpt, 240) && count(item.text_length, 4000) && item.text_length >= 1 && Array.from(item.text_excerpt).length === Math.min(240, item.text_length) && sha(item.text_sha256) && item.text_truncated === (item.text_length > 240) && ids(item.requirement_ids) && item.requirement_ids.every(id => selected.requirement_ids.includes(id)) && requirementStatuses.includes(item.basis_status) && item.eligible === ['CURRENT', 'SELECTED_UNCHANGED_NEWER_BUNDLE'].includes(item.basis_status));
        }
        else
            check(v.requirement_links.every(row => row.acceptance_count === 0) && !options.selectedLink);
        if (v.selected_mapping) {
            await receipt(v.selected_mapping, session, scope, v.root_sha256);
            check(v.selected_mapping.action === 'MAP' && v.selected_mapping.revision <= v.revision);
            if (request.mapping_key)
                check(v.selected_mapping.mapping_key === request.mapping_key);
            else {
                check(request.case_source && v.selected_requirement_link);
                check(v.selected_mapping.mapping_key === await workCaseMappingKey(v.root_sha256, v.selected_requirement_link.requirement_ref.id, request.case_source));
            }
        }
        else
            check(v.selected_mapping === null);
        if (request.case_source) {
            const c = v.selected_case;
            check(c && same(c.source, request.case_source) && c.source_scope === 'IMMUTABLE_AFTER_COMMAND_SNAPSHOT' && c.writer_attribution === 'NOT_PROVEN' && await hash(c.case) === c.source.case_sha256 && c.case.ordinal === c.source.case_ordinal);
            await caseIdentity(c.case_ref, c.basis, c.source, scope, session.project_id);
            commandFacts(c);
        }
        else
            check(v.selected_case === null);
        const budget = v.source_budget;
        check(budget && budget.report_sources_limit === 8 && count(budget.report_sources_reserved, 8) && budget.reserved_report_bytes === budget.report_sources_reserved * 262144 && budget.report_bytes_limit === 2097152 && budget.requirement_versions_limit === 32 && count(budget.requirement_versions_reserved, 32));
        return structuredClone(v);
    }
    async operation(session, root, rootSha, operationId, requestSha, signal) { const scope = workRequirementScope(session, root), request = { ...scope, operation_id: operationId, request_sha256: requestSha }, v = await this.client.request(this.path(session) + '/operations/inspect', 'POST', request, signal, false); check(v && new TextEncoder().encode(JSON.stringify(v)).length <= 32768); await receipt(v, session, scope, rootSha); check(v.operation_id === operationId && v.request_sha256 === requestSha); return structuredClone(v); }
}
const validateWorkCaseSource = source;
async function validateView(v, session, root, request) {
    const { offset, case_source, selected_requirement_link_operation_id, mapping_key, ...scope } = request;
    const options = { selectedLink: selected_requirement_link_operation_id };
    check(v && new TextEncoder().encode(JSON.stringify(v)).length <= 1048576);
    flags(v);
    check(v.kind === 'ORIGINAL_WORK_CASE_MAPPINGS' && v.version === 1 && v.project_id === session.project_id && same(v.scope, scope) && v.query_sha256 === await hash(request) && sha(v.root_sha256) && v.root_sha256 === await hash(v.root_basis) && v.root_basis.project_id === session.project_id && same(v.root_basis.session_ref, scope.session_ref) && same(v.root_basis.workspace_ref, scope.workspace_ref) && v.root_basis.root_operation_id === root && count(v.root_basis.root_generation, scope.generation) && v.root_basis.root_generation >= 1 && sha(v.root_basis.request_sha256) && typeof v.root_basis.native_session_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v.root_basis.native_session_id) && typeof v.root_basis.native_message_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v.root_basis.native_message_id) && text(v.root_status, 80) && count(v.revision) && count(v.requirement_revision) && v.read_only === true && v.metadata_only === true);
    check(Array.isArray(v.rows) && v.rows.length === Math.min(20, Math.max(0, v.page.total - request.offset)) && v.page.offset === request.offset && v.page.limit === 20 && count(v.page.total, 128) && v.page.next_offset === (request.offset + v.rows.length < v.page.total ? request.offset + v.rows.length : null) && new Set(v.rows.map(row => row.operation.mapping_key)).size === v.rows.length);
    for (const row of v.rows) {
        await receipt(row.operation, session, scope, v.root_sha256);
        check(row.operation.action === 'MAP' && row.operation.revision <= v.revision && requirementStatuses.includes(row.requirement_status) && ['VERIFIED_SAVED_CASE', 'UNAVAILABLE', 'NOT_CHECKED_BUDGET'].includes(row.report_status) && row.automatic_adoption === false && row.new_execution_proven === false);
        const p = row.operation.proof, impact = row.requirement_impact;
        if (impact) {
            check(impact.operation_id === p.requirement_link_operation_id && same(impact.requirement_ref, p.requirement_ref) && same(impact.requirement_ids, p.requirement_ids) && same(impact.acceptance_ids, p.acceptance_ids) && impact.selection_sha256 === p.selection_sha256 && impact.review_status === row.requirement_status && impact.automatic_adoption === false);
        }
        else
            check(['REQUIREMENT_LINK_CHANGED', 'NOT_CHECKED_BUDGET', 'UNAVAILABLE'].includes(row.requirement_status));
    }
    check(Array.isArray(v.requirement_links) && v.requirement_links.length <= 10 && new Set(v.requirement_links.map(row => row.operation_id)).size === v.requirement_links.length && v.requirement_links.every(row => id(row.operation_id) && sha(row.request_sha256) && ref(row.requirement_ref) && count(row.acceptance_count, 100) && typeof row.available === 'boolean' && (row.title === null || text(row.title, 200))));
    const selected = v.selected_requirement_link;
    if (selected) {
        const link = v.requirement_links.find(row => row.operation_id === selected.operation_id);
        check(link && selected.request_sha256 === link.request_sha256 && same(selected.requirement_ref, link.requirement_ref) && ids(selected.requirement_ids) && typeof selected.available === 'boolean' && Array.isArray(selected.acceptance) && selected.acceptance.length <= 100);
        if (options.selectedLink)
            check(selected.operation_id === options.selectedLink);
        for (const item of selected.acceptance)
            check(ids([item.id]) && text(item.text_excerpt, 240) && count(item.text_length, 4000) && item.text_length >= 1 && Array.from(item.text_excerpt).length === Math.min(240, item.text_length) && sha(item.text_sha256) && item.text_truncated === (item.text_length > 240) && ids(item.requirement_ids) && item.requirement_ids.every(id => selected.requirement_ids.includes(id)) && requirementStatuses.includes(item.basis_status) && item.eligible === ['CURRENT', 'SELECTED_UNCHANGED_NEWER_BUNDLE'].includes(item.basis_status));
    }
    else
        check(v.requirement_links.every(row => row.acceptance_count === 0) && !options.selectedLink);
    if (v.selected_mapping) {
        await receipt(v.selected_mapping, session, scope, v.root_sha256);
        check(v.selected_mapping.action === 'MAP' && v.selected_mapping.revision <= v.revision);
        if (request.mapping_key)
            check(v.selected_mapping.mapping_key === request.mapping_key);
        else {
            check(request.case_source && v.selected_requirement_link);
            check(v.selected_mapping.mapping_key === await workCaseMappingKey(v.root_sha256, v.selected_requirement_link.requirement_ref.id, request.case_source));
        }
    }
    else
        check(v.selected_mapping === null);
    if (request.case_source) {
        const c = v.selected_case;
        check(c && same(c.source, request.case_source) && c.source_scope === 'IMMUTABLE_AFTER_COMMAND_SNAPSHOT' && c.writer_attribution === 'NOT_PROVEN' && await hash(c.case) === c.source.case_sha256 && c.case.ordinal === c.source.case_ordinal);
        await caseIdentity(c.case_ref, c.basis, c.source, scope, session.project_id);
        commandFacts(c);
    }
    else
        check(v.selected_case === null);
    const budget = v.source_budget;
    check(budget && budget.report_sources_limit === 8 && count(budget.report_sources_reserved, 8) && budget.reserved_report_bytes === budget.report_sources_reserved * 262144 && budget.report_bytes_limit === 2097152 && budget.requirement_versions_limit === 32 && count(budget.requirement_versions_reserved, 32));
    return structuredClone(v);
}
const selection = (() => {
    const same = (a, b) => canonical(a) === canonical(b), copy = v => structuredClone(v);
    const sha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v), id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v), count = (v, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) <= max;
    const object = (v) => v !== null && typeof v === 'object' && !Array.isArray(v), exact = (v, keys) => object(v) && Object.keys(v).length === keys.length && Object.keys(v).every(k => keys.includes(k));
    function check(value, code = 'WORK_CASE_LOCAL_INVALID') { if (!value)
        throw Error(code); }
    function ids(v) { return Array.isArray(v) && v.length <= 100 && v.every(x => typeof x === 'string' && /^[A-Za-z0-9_-]{1,40}$/.test(x)) && new Set(v).size === v.length; }
    function reference(v) { return !!v && exact(v, ['id', 'revision', 'sha256']) && id(v.id) && count(v.revision, 2147483647) && v.revision >= 1 && sha(v.sha256); }
    function source(v, generation = Number.MAX_SAFE_INTEGER) { try {
        check(exact(v, ['command_ref', 'command_generation', 'call_id', 'report_path', 'report_sha256', 'report_ref', 'case_ordinal', 'case_sha256']));
        validateWorkCaseSource(v, generation);
        return true;
    }
    catch {
        return false;
    } }
    function choice(v) { const c = v; return exact(c, ['source', 'link_id', 'link_sha256', 'requirement_ref', 'acceptance_ids']) && source(c.source) && ids(c.acceptance_ids) && (c.link_id === null ? c.link_sha256 === null && c.requirement_ref === null && c.acceptance_ids.length === 0 : id(c.link_id) && sha(c.link_sha256) && reference(c.requirement_ref)); }
    function scope(v, o) { return exact(v, ['session_ref', 'generation', 'workspace_ref', 'root_operation_id']) && same(v.session_ref, o.session_ref) && same(v.workspace_ref, o.workspace_ref) && v.root_operation_id === o.root_operation_id && count(v.generation) && v.generation >= 1; }
    function request(v, o) {
        const r = v;
        if (!exact(r, ['session_ref', 'generation', 'workspace_ref', 'root_operation_id', 'operation_id', 'root_sha256', 'expected_revision', 'expected_requirement_revision', 'action', 'mapping_key', 'selection']) || !scope({ session_ref: r.session_ref, generation: r.generation, workspace_ref: r.workspace_ref, root_operation_id: r.root_operation_id }, o) || !id(r.operation_id) || !sha(r.root_sha256) || !count(r.expected_revision, 512) || !count(r.expected_requirement_revision, 512) || !sha(r.mapping_key))
            return false;
        const s = r.selection;
        return r.action === 'REMOVE' ? s === null : r.action === 'MAP' && !!s && exact(s, ['requirement_link_operation_id', 'requirement_link_request_sha256', 'source', 'acceptance_ids']) && id(s.requirement_link_operation_id) && sha(s.requirement_link_request_sha256) && source(s.source, r.generation) && ids(s.acceptance_ids) && s.acceptance_ids.length > 0;
    }
    function draft(v, o) { const d = v; return exact(d, ['scope', 'root_sha256', 'revision', 'requirement_revision', 'choice', 'basis']) && scope(d.scope, o) && sha(d.root_sha256) && count(d.revision, 512) && count(d.requirement_revision, 512) && choice(d.choice) && source(d.choice.source, d.scope.generation) && object(d.basis); }
    function basicReceipt(v, r, sum, o) { check(v && v.kind === 'ORIGINAL_WORK_CASE_OPERATION' && v.version === 1 && v.project_id === o.project_id && same(v.session_ref, o.session_ref) && v.root_operation_id === o.root_operation_id && v.root_sha256 === r.root_sha256 && v.operation_id === r.operation_id && v.request_sha256 === sum && v.revision === r.expected_revision + 1 && v.requirement_revision === r.expected_requirement_revision && v.action === r.action && v.mapping_key === r.mapping_key && same(v.selection, r.selection) && v.metadata_only === true && v.model_calls === 0 && v.commands_started === 0 && v.source_files_written === 0 && v.permission_granted === false && v.original_task_status_changed === false && v.whole_task_acceptance === 'NOT_ASSESSED' && v.automatic_replay === false && v.coverage === 'HUMAN_DECLARED_MAPPING_NOT_MEASURED_TEST_COVERAGE' && v.current_workspace_verified === false && v.target_environment_verified === false && v.writer_attribution === 'NOT_PROVEN', 'WORK_CASE_RECEIPT_CHANGED'); }
    function workCaseDraft(v, c) {
        check(choice(c) && v.selected_case && same(v.selected_case.source, c.source), 'WORK_CASE_SELECTION_UNAVAILABLE');
        const link = v.selected_requirement_link;
        check(c.link_id === null || link?.operation_id === c.link_id, 'WORK_CASE_SELECTION_UNAVAILABLE');
        const criteria = [...c.acceptance_ids].sort().map(id => { const row = link?.acceptance.find(r => r.id === id); return row ? { id, text_sha256: row.text_sha256, requirement_ids: [...row.requirement_ids].sort(), basis_status: row.eligible ? 'ELIGIBLE' : row.basis_status, eligible: row.eligible } : { id, missing: true }; });
        return { scope: copy(v.scope), root_sha256: v.root_sha256, revision: v.revision, requirement_revision: v.requirement_revision, choice: copy(c), basis: { case_ref: copy(v.selected_case.case_ref), command_status: v.selected_case.command_status, command_exit_code: v.selected_case.command_exit_code, association: v.selected_case.association, reason_codes: copy(v.selected_case.reason_codes), link: link && c.link_id ? { operation_id: link.operation_id, request_sha256: link.request_sha256, requirement_ref: link.requirement_ref, available: link.available } : null, criteria } };
    }
    function workCaseDraftMatches(v, d) { try {
        return same(workCaseDraft(v, d.choice), d);
    }
    catch {
        return false;
    } }
    function workCaseCanSave(v, d) { try {
        const c = d.choice, link = v.selected_requirement_link;
        check(workCaseDraftMatches(v, d) && c.link_id && link?.available && link.operation_id === c.link_id && link.request_sha256 === c.link_sha256 && same(link.requirement_ref, c.requirement_ref) && c.acceptance_ids.length > 0 && c.acceptance_ids.every(id => link.acceptance.some(r => r.id === id && r.eligible)), 'WORK_CASE_REVIEW_REQUIRED');
        return true;
    }
    catch {
        return false;
    } }
    function workCaseUnchanged(v, d) { const old = v.selected_mapping, c = d.choice; return !!old && old.action === 'MAP' && old.selection?.requirement_link_operation_id === c.link_id && old.selection?.requirement_link_request_sha256 === c.link_sha256 && same(old.selection?.source, c.source) && same([...(old.selection?.acceptance_ids ?? [])].sort(), [...c.acceptance_ids].sort()); }
    return { validSource: source, validChoice: choice, validRequest: request, validDraft: draft, draftFor: workCaseDraft, matches: workCaseDraftMatches, canSave: workCaseCanSave, unchanged: workCaseUnchanged, basicReceipt };
})();
async function verifyReceipt(v, r, sum, owner) { if (!selection.validRequest(r, owner) || hash(r) !== sum || Buffer.byteLength(JSON.stringify(v)) > 32768)
    throw Error('WORK_CASE_RECEIPT_CHANGED'); await receipt(v, { project_id: owner.project_id }, r, r.root_sha256); selection.basicReceipt(v, r, sum, owner); return structuredClone(v); }
module.exports = { canonical, hash, same, validateView, verifyReceipt, validateReceipt: receipt, caseSourceFromReport: workCaseSourceFromReport, mappingKey: workCaseMappingKey, workRequirementScope, OriginalWorkTestCasesApi, ...selection };
