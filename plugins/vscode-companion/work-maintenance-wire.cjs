'use strict';
// Generated from desktop source authority; do not edit independently.
// SHA256 apps/agent-shell/src/original-work-maintenance.ts d82af858d52312e6896e1e7e235c82c3f92ea475eb9fced645b5addb18ffa710
// SHA256 apps/agent-shell/src/work-maintenance-editor.ts 3670098173a4daa29746ae00e582f1168783a87839c9ef77b6cc0652b82a1684
// SHA256 apps/agent-shell/src/original-work-requirements.ts 069eb91b1e7d58985943cea5f5a20a1f07f923ca84b1f9e3dd717c0ffba6bc85
// SHA256 apps/agent-shell/src/original-work-test-cases.ts 673435c5e7472b1ef9c301165b573596f189abb18b82e054292b99eb6856d1b6
const { webcrypto: crypto } = require('node:crypto');
const { canonical, hash, workRequirementScope } = require('./work-case-wire.cjs');
const { validNativeExportScope: validScope } = require('./native-export-wire.cjs');
const { deliveryArchivePath } = require('./work-delivery-wire.cjs');
const api = Object.freeze({ request() { throw Error('WORK_MAINTENANCE_TRANSPORT_REQUIRED'); } });
const validRequirementSourceAuthority = (() => {
    const sha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
    const id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v);
    const count = (v, max = 512) => Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) <= max;
    function validRequirementSourceAuthority(v) { const a = v; return !!a && typeof a === 'object' && !Array.isArray(a) && Object.keys(a).length === 4 && Object.keys(a).every(k => ['source_project_id', 'grant_id', 'revision', 'receipt_sha256'].includes(k)) && id(a.source_project_id) && sha(a.grant_id) && count(a.revision, 512) && a.revision > 0 && sha(a.receipt_sha256); }
    ;
    return validRequirementSourceAuthority;
})();
const validateWorkCaseSource = (() => {
    const same = (a, b) => canonical(a) === canonical(b), sha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v), id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v), count = (v, max = 512) => Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) <= max;
    const ref = (v) => !!v && id(v.id) && count(v.revision, 2147483647) && v.revision > 0 && sha(v.sha256), text = (v, max) => typeof v === 'string' && Array.from(v).length <= max && !v.includes('\0');
    function check(value) { if (!value)
        throw Error('ORIGINAL_WORK_CASE_MAPPING_CHANGED'); }
    function source(v, generation) { check(v && ref(v.command_ref) && ref(v.report_ref) && count(v.command_generation, Number.MAX_SAFE_INTEGER) && v.command_generation >= 1 && v.command_generation <= generation && typeof v.call_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v.call_id) && text(v.report_path, 1024) && v.report_path.length > 0 && !/[\\:*?\[\]\x00-\x1f\x7f]/.test(v.report_path) && !v.report_path.startsWith('/') && v.report_path.split('/').every(p => p !== '' && p !== '.' && p !== '..' && !['.git', '.eeas-sdk-state', '.eeas-runtime'].includes(p.toLowerCase()) && !p.startsWith('.eeas-write-')) && sha(v.report_sha256) && count(v.case_ordinal, 1999) && sha(v.case_sha256)); }
    ;
    return source;
})();
const same = (a, b) => canonical(a) === canonical(b), obj = (v) => !!v && typeof v === 'object' && !Array.isArray(v);
const exact = (v, keys) => obj(v) && Object.keys(v).length === keys.length && Object.keys(v).every(k => keys.includes(k));
const sha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v), id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v);
const count = (v, max = Number.MAX_SAFE_INTEGER) => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 && v <= max;
const text = (v, max) => typeof v === 'string' && Array.from(v).length <= max && !/[\0\uD800-\uDFFF]/u.test(v);
const strings = (v, max = 32, size = 200) => Array.isArray(v) && v.length <= max && v.every(s => text(s, size));
const ids = (v) => strings(v, 100, 40) && v.every(s => /^[A-Za-z0-9_-]{1,40}$/.test(s)) && new Set(v).size === v.length;
const bytes = (v) => new TextEncoder().encode(canonical(v)).length;
const sections = ['FILES', 'REQUIREMENTS', 'CASES', 'REPORTS', 'NOTES'];
function check(v, code = 'WORK_MAINTENANCE_CHANGED') { if (!v)
    throw Error(code); }
function reference(v) { return exact(v, ['id', 'revision', 'sha256']) && id(v.id) && v.revision === 1 && sha(v.sha256); }
function validateMaintenanceRef(v) { check(exact(v, ['session_ref', 'root_operation_id', 'root_sha256', 'operation_id', 'request_sha256', 'manifest_sha256', 'revision', 'receipt_sha256']) && reference(v.session_ref) && id(v.root_operation_id) && sha(v.root_sha256) && id(v.operation_id) && sha(v.request_sha256) && sha(v.manifest_sha256) && count(v.revision, 32) && v.revision > 0 && sha(v.receipt_sha256)); return structuredClone(v); }
function validateMaintenancePrepare(v, editing = false) { const { root_sha256, expected_revision, action, delivery_ref, note, ...scope } = v; check(validScope(scope) && sha(root_sha256) && count(expected_revision, 128) && ['ASSOCIATE', 'REMOVE'].includes(action) && text(note, 4000) && (editing || note.trim().length > 0) && bytes(v) <= 32768); validateMaintenanceRef(delivery_ref); return structuredClone(v); }
function validateMaintenanceRequest(v) { const { operation_id, review_sha256, review_confirmed, ...q } = v; validateMaintenancePrepare(q); check(id(operation_id) && sha(review_sha256) && review_confirmed === true && bytes(v) <= 32768); return structuredClone(v); }
function flags(v) { check(v && v.read_only_sources === true && v.model_calls === 0 && v.commands_started === 0 && v.source_files_written === 0 && v.artifact_files_written === 0 && v.permission_granted === false && v.deployment_performed === false && v.restore_performed === false && v.original_task_status_changed === false && v.issue_resolution === 'NOT_ASSESSED' && v.automatic_replay === false && v.whole_task_acceptance === 'NOT_ASSESSED' && v.target_environment_verified === false && v.current_workspace_verified === false && v.current_baseline_bytes_verified === false && v.provenance_scope === 'FROZEN_SAME_PROJECT_DELIVERY_METADATA_ONLY'); }
async function root(v, rootSha, pid, s) { check(exact(v, ['project_id', 'session_ref', 'workspace_ref', 'root_operation_id', 'root_generation', 'native_session_id', 'native_message_id', 'request_sha256']) && sha(rootSha) && await hash(v) === rootSha && v.project_id === pid && same(v.session_ref, s.session_ref) && same(v.workspace_ref, s.workspace_ref) && v.root_operation_id === s.root_operation_id && count(v.root_generation, s.generation) && v.root_generation >= 1 && sha(v.request_sha256)); for (const name of [v.native_session_id, v.native_message_id])
    check(typeof name === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(name)); }
function counts(v) { check(exact(v, sections) && count(v.FILES, 64) && v.FILES > 0 && count(v.REQUIREMENTS, 10) && count(v.CASES, 128) && count(v.REPORTS, 8) && v.NOTES === 1); }
async function hashText(value) { return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))), n => n.toString(16).padStart(2, '0')).join(''); }
async function validateMaintenanceContext(value, pid, rootSha) {
    const v = structuredClone(value);
    flags(v);
    check(exact(v, ['schema', 'project_id', 'root_sha256', 'revision', 'associations', 'read_only', 'read_only_sources', 'model_calls', 'commands_started', 'source_files_written', 'artifact_files_written', 'permission_granted', 'deployment_performed', 'restore_performed', 'original_task_status_changed', 'issue_resolution', 'automatic_replay', 'whole_task_acceptance', 'target_environment_verified', 'current_workspace_verified', 'current_baseline_bytes_verified', 'provenance_scope']) && bytes(v) <= 32768 && v.schema === 'cirven.original-work-maintenance-context.v1' && v.project_id === pid && id(pid) && sha(rootSha) && v.root_sha256 === rootSha && count(v.revision, 128) && v.read_only === true && Array.isArray(v.associations) && v.associations.length <= 8 && v.associations.length <= v.revision);
    let revision = 0;
    const seen = new Set();
    for (const a of v.associations) {
        const d = a.decision_ref;
        check(exact(a, ['decision_ref', 'delivery_ref', 'baseline_sha256', 'version_label', 'note_excerpt', 'note_sha256', 'note_length', 'warnings_excerpt', 'warnings_sha256', 'warnings_count', 'evidence_included', 'counts']) && exact(d, ['operation_id', 'request_sha256', 'receipt_sha256', 'revision']) && id(d.operation_id) && sha(d.request_sha256) && sha(d.receipt_sha256) && count(d.revision, v.revision) && d.revision > revision && sha(a.baseline_sha256) && text(a.version_label, 100) && a.version_label.length > 0 && text(a.note_excerpt, 240) && count(a.note_length, 4000) && a.note_length > 0 && Array.from(a.note_excerpt).length === Math.min(240, a.note_length) && sha(a.note_sha256) && strings(a.warnings_excerpt, 8, 200) && sha(a.warnings_sha256) && count(a.warnings_count, 32) && a.warnings_excerpt.length === Math.min(8, a.warnings_count) && typeof a.evidence_included === 'boolean');
        validateMaintenanceRef(a.delivery_ref);
        counts(a.counts);
        const key = canonical(a.delivery_ref);
        check(!seen.has(key));
        seen.add(key);
        revision = d.revision;
        if (a.note_length <= 240)
            check(await hashText(a.note_excerpt) === a.note_sha256);
        if (a.warnings_count <= 8)
            check(await hash(a.warnings_excerpt) === a.warnings_sha256);
    }
    return v;
}
function artifact(v, revision) { check(exact(v, ['path', 'sha256', 'byte_length', 'filename']) && /^attempts\/0[1-4]\/package\.zip$/.test(v.path) && sha(v.sha256) && count(v.byte_length, 25165824) && v.byte_length >= 1 && v.filename === 'cirven-delivery-' + revision + '.zip'); }
function summary(v, expected) { check(exact(v, ['reference', 'version_label', 'notes_excerpt', 'notes_sha256', 'notes_length', 'parent', 'artifact', 'recorded_at', 'warnings', 'evidence_included', 'counts']) && bytes(v) <= 16384 && text(v.version_label, 100) && v.version_label.length > 0 && text(v.notes_excerpt, 240) && sha(v.notes_sha256) && count(v.notes_length, 4000) && v.notes_length >= 1 && Array.from(v.notes_excerpt).length === Math.min(240, v.notes_length) && text(v.recorded_at, 100) && v.recorded_at.length > 0 && strings(v.warnings) && typeof v.evidence_included === 'boolean'); validateMaintenanceRef(v.reference); check(!expected || same(v.reference, expected)); artifact(v.artifact, v.reference.revision); counts(v.counts); if (v.reference.revision === 1)
    check(v.parent === null);
else
    check(exact(v.parent, ['operation_id', 'request_sha256', 'manifest_sha256', 'revision']) && id(v.parent.operation_id) && sha(v.parent.request_sha256) && sha(v.parent.manifest_sha256) && v.parent.revision === v.reference.revision - 1); }
async function common(v, pid, s, rootSha, q, kind) { check(obj(v) && bytes(v) <= 131072 && id(pid) && validScope(s) && sha(rootSha)); flags(v); check(v.kind === kind && v.version === 1 && v.project_id === pid && same(v.scope, s) && v.query_sha256 === await hash(q) && v.root_sha256 === rootSha && v.read_only === true); }
function page(v, offset, total) { check(Array.isArray(v.entries) && v.entries.length <= 8 && (v.next_offset === null || v.entries.length === 8 && v.next_offset === offset + 8)); if (total !== undefined)
    check(count(total, 10000) && v.entries.length === Math.max(0, Math.min(8, total - offset)) && v.next_offset === (offset + 8 < total ? offset + 8 : null)); }
async function validateMaintenanceCatalog(v, pid, s, rootSha, offset) { v = structuredClone(v); s = structuredClone(s); check(count(offset, 256)); await common(v, pid, s, rootSha, { ...s, offset }, 'ORIGINAL_WORK_MAINTENANCE_DELIVERIES'); await root(v.root_basis, rootSha, pid, s); page(v, offset); const seen = new Set(); for (const e of v.entries) {
    if (e.eligible === true) {
        check(exact(e, ['eligible', 'reference', 'version_label', 'notes_excerpt', 'warnings', 'counts', 'summary_sha256']) && text(e.version_label, 100) && e.version_label.length > 0 && text(e.notes_excerpt, 240) && strings(e.warnings) && sha(e.summary_sha256));
        validateMaintenanceRef(e.reference);
        counts(e.counts);
        const k = e.reference.session_ref.id + ':' + e.reference.operation_id;
        check(!seen.has(k));
        seen.add(k);
    }
    else
        check(exact(e, ['eligible', 'locator', 'reason']) && e.eligible === false && exact(e.locator, ['session_id', 'operation_id']) && id(e.locator.session_id) && id(e.locator.operation_id) && e.reason === 'FROZEN_DELIVERY_UNAVAILABLE');
} check(v.unavailable === v.entries.filter(e => !e.eligible).length); return structuredClone(v); }
async function validateMaintenanceReview(v, pid, q) { v = structuredClone(v); q = structuredClone(q); validateMaintenancePrepare(q); const { root_sha256, expected_revision, action, delivery_ref, note, ...s } = q; await common(v, pid, s, root_sha256, q, 'ORIGINAL_WORK_MAINTENANCE_REVIEW'); await root(v.root_basis, root_sha256, pid, s); summary(v.baseline, delivery_ref); check(v.baseline_sha256 === await hash(v.baseline) && exact(v.basis, ['schema', 'query', 'baseline_sha256']) && v.basis.schema === 'cirven.original-work-maintenance-review.v1' && same(v.basis.query, q) && v.basis.baseline_sha256 === v.baseline_sha256 && v.review_sha256 === await hash(v.basis)); return structuredClone(v); }
function pointer(v, revision) { check(id(v.operation_id) && sha(v.request_sha256) && count(v.revision, revision) && v.revision > 0 && text(v.recorded_at, 100) && sha(v.baseline_sha256) && text(v.note_excerpt, 240)); }
async function validateMaintenanceView(v, pid, s, rootSha, offset) { v = structuredClone(v); s = structuredClone(s); check(count(offset, 128)); await common(v, pid, s, rootSha, { ...s, history_offset: offset }, 'ORIGINAL_WORK_MAINTENANCE'); await root(v.root_basis, rootSha, pid, s); check(count(v.revision, 128) && Array.isArray(v.active) && v.active.length <= 8 && Array.isArray(v.history) && v.history.length <= 8 && v.history.length === Math.max(0, Math.min(8, v.revision - offset)) && v.next_offset === (offset + 8 < v.revision ? offset + 8 : null)); const seen = new Set(); for (const a of v.active) {
    pointer(a, v.revision);
    check(exact(a, ['operation_id', 'request_sha256', 'revision', 'recorded_at', 'baseline_sha256', 'note_excerpt', 'baseline']) && exact(a.baseline, ['reference', 'version_label', 'artifact', 'counts', 'warnings']) && text(a.baseline.version_label, 100) && a.baseline.version_label.length > 0 && strings(a.baseline.warnings));
    validateMaintenanceRef(a.baseline.reference);
    artifact(a.baseline.artifact, a.baseline.reference.revision);
    counts(a.baseline.counts);
    const k = canonical(a.baseline.reference);
    check(!seen.has(k));
    seen.add(k);
} for (const [i, h] of v.history.entries()) {
    pointer(h, v.revision);
    check(exact(h, ['operation_id', 'request_sha256', 'revision', 'action', 'recorded_at', 'baseline_sha256', 'note_excerpt']) && h.revision === offset + i + 1 && ['ASSOCIATE', 'REMOVE'].includes(h.action));
} return structuredClone(v); }
async function validateMaintenanceOperation(v, pid, i, options = {}) {
    v = structuredClone(v);
    i = structuredClone(i);
    options = structuredClone(options);
    check(obj(v) && bytes(v) <= 131072 && validScope(i.scope) && sha(i.root_sha256) && id(i.operation_id) && sha(i.request_sha256));
    flags(v);
    const q = { ...i.scope, operation_id: i.operation_id, request_sha256: i.request_sha256 };
    check(v.kind === 'ORIGINAL_WORK_MAINTENANCE_OPERATION' && v.version === 1 && v.project_id === pid && same(v.scope, q) && v.query_sha256 === await hash(q) && v.metadata_only === true && [0, 1].includes(v.current_call_metadata_writes) && v.read_only === (v.current_call_metadata_writes === 0) && (v.current_call_metadata_writes === 0 || options.allow_writes === true));
    const r = validateMaintenanceRequest(v.original_request), { operation_id, review_sha256, review_confirmed, ...query } = r, { root_sha256, expected_revision, action, delivery_ref, note, ...s } = query;
    check(operation_id === i.operation_id && await hash(r) === i.request_sha256 && root_sha256 === i.root_sha256 && same({ ...s, generation: i.scope.generation }, i.scope) && s.generation <= i.scope.generation && (!options.request || same(r, options.request)));
    const a = v.receipt;
    flags(a);
    check(a.kind === 'ORIGINAL_WORK_MAINTENANCE_DECISION' && a.version === 1 && a.project_id === pid && a.operation_id === operation_id && a.request_sha256 === i.request_sha256 && a.root_sha256 === root_sha256 && a.revision === expected_revision + 1 && a.action === action && a.note === note && text(a.recorded_at, 100) && a.recorded_at.length > 0 && v.receipt_sha256 === await hash(a) && bytes(a) <= 32768);
    await root(a.root_basis, root_sha256, pid, s);
    summary(a.baseline, delivery_ref);
    check(a.baseline_sha256 === await hash(a.baseline) && review_sha256 === await hash({ schema: 'cirven.original-work-maintenance-review.v1', query, baseline_sha256: a.baseline_sha256 }));
    return structuredClone(v);
}
async function validateMaintenanceEvidence(v, pid, s, rootSha, ref, section, offset) {
    v = structuredClone(v);
    s = structuredClone(s);
    ref = structuredClone(ref);
    validateMaintenanceRef(ref);
    check(sections.includes(section) && count(offset, 10000));
    const q = { ...s, delivery_ref: ref, section, offset };
    await common(v, pid, s, rootSha, q, 'ORIGINAL_WORK_MAINTENANCE_DELIVERY');
    summary(v.summary, ref);
    check(v.summary_sha256 === await hash(v.summary) && v.section === section && v.total === v.summary.counts[section]);
    page(v, offset, v.total);
    for (const [index, e] of v.entries.entries()) {
        check(obj(e) && e.ordinal === offset + index && sha(e.record_sha256));
        const record = e.record;
        if (section === 'FILES') {
            check(exact(e, ['ordinal', 'record_sha256', 'record']) && obj(record));
            const f = record, x = f.source_ref;
            check(exact(f, ['source_ref', 'byte_length', 'archive_path']) && exact(x, ['kind', 'project_id', 'workspace_id', 'binding_revision', 'binding_sha256', 'path', 'revision', 'sha256']) && x.kind === 'local_file' && x.project_id === pid && sha(x.workspace_id) && sha(x.binding_sha256) && count(x.binding_revision) && x.binding_revision > 0 && count(x.revision) && x.revision > 0 && sha(x.sha256) && count(f.byte_length, 16777216) && f.archive_path === deliveryArchivePath(x.path));
        }
        else if (section === 'NOTES') {
            check(exact(e, ['ordinal', 'record_sha256', 'record']) && exact(record, ['text']) && text(record.text, 4000) && Array.from(record.text).length === v.summary.notes_length);
            const raw = new TextEncoder().encode(record.text), digest = await crypto.subtle.digest('SHA-256', raw);
            check([...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, '0')).join('') === v.summary.notes_sha256);
        }
        else if (section === 'REQUIREMENTS') {
            check(exact(e, ['ordinal', 'record_sha256', 'requirement_ref', 'requirement_ids', 'acceptance_ids', 'source_authority', 'review_status', 'selected_snapshot_included']) && obj(e.requirement_ref) && id(e.requirement_ref.id) && count(e.requirement_ref.revision) && e.requirement_ref.revision > 0 && sha(e.requirement_ref.sha256) && ids(e.requirement_ids) && ids(e.acceptance_ids) && ['CURRENT', 'SELECTED_UNCHANGED_NEWER_BUNDLE', 'NEEDS_REVIEW', 'UNAVAILABLE', 'NOT_CHECKED_BUDGET'].includes(e.review_status ?? '') && typeof e.selected_snapshot_included === 'boolean' && (e.source_authority === null || validRequirementSourceAuthority(e.source_authority) && e.source_authority.source_project_id !== pid));
        }
        else if (section === 'CASES') {
            check(exact(e, ['ordinal', 'record_sha256', 'operation_id', 'request_sha256', 'mapping_key', 'selection', 'requirement_status', 'report_status', 'report_key', 'proof_sha256', 'case_label', 'command_status', 'command_exit_code', 'report_outcome', 'reason_codes']) && id(e.operation_id) && sha(e.request_sha256) && sha(e.mapping_key) && sha(e.proof_sha256) && obj(e.selection) && exact(e.selection, ['requirement_link_operation_id', 'requirement_link_request_sha256', 'source', 'acceptance_ids']) && id(e.selection.requirement_link_operation_id) && sha(e.selection.requirement_link_request_sha256) && ids(e.selection.acceptance_ids) && e.selection.acceptance_ids.length > 0);
            validateWorkCaseSource(e.selection.source, Number.MAX_SAFE_INTEGER);
            const c = e.case_label;
            check(exact(c, ['ordinal', 'suite', 'classname', 'name', 'status', 'text_truncated']) && c.ordinal === e.selection.source.case_ordinal && text(c.suite, 160) && text(c.classname, 160) && text(c.name, 160) && ['PASSED', 'FAILED', 'ERROR', 'SKIPPED', 'UNKNOWN'].includes(c.status) && typeof c.text_truncated === 'boolean' && ['EXITED', 'UNKNOWN_OUTCOME'].includes(e.command_status ?? '') && (e.command_exit_code === null || Number.isSafeInteger(e.command_exit_code)) && ['INCONSISTENT', 'NO_TESTS', 'FAILED', 'SKIPPED', 'PASSED_WITH_SKIPS', 'PASSED'].includes(e.report_outcome ?? '') && strings(e.reason_codes) && ['CURRENT', 'SELECTED_UNCHANGED_NEWER_BUNDLE', 'NEEDS_REVIEW', 'UNAVAILABLE', 'REQUIREMENT_LINK_CHANGED', 'NOT_CHECKED_BUDGET'].includes(String(e.requirement_status)) && ['SAVED_REPORT', 'UNAVAILABLE', 'NOT_INCLUDED'].includes(String(e.report_status)) && (e.report_key === null || sha(e.report_key)));
            if (e.command_status === 'UNKNOWN_OUTCOME')
                check(e.command_exit_code === null);
            else
                check(Number.isSafeInteger(e.command_exit_code));
        }
        else {
            check(exact(e, ['ordinal', 'record_sha256', 'record']) && obj(record));
            if (record.status === 'UNAVAILABLE') {
                check(exact(record, ['status', 'source', 'archive_path']) && record.archive_path === null);
                validateWorkCaseSource(record.source, Number.MAX_SAFE_INTEGER);
            }
            else {
                check(exact(record, ['status', 'basis', 'report_ref', 'summary', 'command_status', 'command_exit_code', 'association', 'reason_codes', 'raw_sha256', 'byte_length', 'archive_path']) && record.status === 'SAVED_REPORT' && obj(record.basis) && obj(record.summary) && reference(record.report_ref) && exact(record.basis, ['schema', 'scope', 'command_ref', 'command_generation', 'call_id', 'operation_id', 'root_operation_id', 'report_path', 'report_sha256']) && record.basis.schema === 'eeas.command-test-report.v1' && obj(record.basis.scope) && validScope({ session_ref: record.basis.scope.session_ref, generation: record.basis.scope.generation, workspace_ref: record.basis.scope.workspace_ref, root_operation_id: ref.root_operation_id }) && same(record.basis.scope.session_ref, ref.session_ref) && record.basis.scope.project_id === pid && record.basis.root_operation_id === ref.root_operation_id && record.report_ref.id === await hash(record.basis) && sha(record.raw_sha256) && record.basis.report_sha256 === record.raw_sha256 && count(record.byte_length, 262144) && typeof record.archive_path === 'string' && /^evidence\/reports\/[a-f0-9]{64}\.xml$/.test(record.archive_path) && ['EXITED', 'UNKNOWN_OUTCOME'].includes(String(record.command_status)) && (record.command_exit_code === null || Number.isSafeInteger(record.command_exit_code)) && strings(record.reason_codes) && ['INCONSISTENT', 'NO_TESTS', 'FAILED', 'SKIPPED', 'PASSED_WITH_SKIPS', 'PASSED'].includes(String(record.summary.outcome)));
                const b = record.basis;
                validateWorkCaseSource({ command_ref: b.command_ref, command_generation: b.command_generation, call_id: b.call_id, report_path: b.report_path, report_sha256: b.report_sha256, report_ref: record.report_ref, case_ordinal: 0, case_sha256: '0'.repeat(64) }, b.scope.generation);
                check(id(b.operation_id) && ['NEW_SNAPSHOT', 'CHANGED_BYTES', 'UNCHANGED_BYTES', 'BEFORE_UNAVAILABLE'].includes(String(record.association)));
                if (record.command_status === 'UNKNOWN_OUTCOME')
                    check(record.command_exit_code === null && record.reason_codes.includes('COMMAND_OUTCOME_UNKNOWN'));
                else
                    check(Number.isSafeInteger(record.command_exit_code));
                if (['UNCHANGED_BYTES', 'BEFORE_UNAVAILABLE'].includes(String(record.association)))
                    check(record.reason_codes.includes('NEW_REPORT_GENERATION_NOT_PROVEN'));
            }
        }
        if (record)
            check(e.record_sha256 === await hash(record));
    }
    return structuredClone(v);
}
class OriginalWorkMaintenanceApi {
    client;
    constructor(client = api) {
        this.client = client;
    }
    path(s) { check(id(s.project_id) && reference(s.session_ref)); return '/api/projects/' + encodeURIComponent(s.project_id) + '/agent-sessions/' + encodeURIComponent(s.session_ref.id) + '/work-maintenance'; }
    async inspect(s, root, rootSha, history_offset = 0, signal) { s = structuredClone(s); const scope = structuredClone(workRequirementScope(s, root)); check(count(history_offset, 128)); return validateMaintenanceView(await this.client.request(this.path(s) + '/inspect', 'POST', { ...scope, history_offset }, signal, false), s.project_id, scope, rootSha, history_offset); }
    async browse(s, root, rootSha, offset = 0, signal) { s = structuredClone(s); const scope = structuredClone(workRequirementScope(s, root)); check(count(offset, 256)); return validateMaintenanceCatalog(await this.client.request(this.path(s) + '/deliveries', 'POST', { ...scope, offset }, signal, false), s.project_id, scope, rootSha, offset); }
    async delivery(s, root, rootSha, input, section = 'FILES', offset = 0, signal) { s = structuredClone(s); const scope = structuredClone(workRequirementScope(s, root)), ref = validateMaintenanceRef(input); check(sections.includes(section) && count(offset, 10000)); return validateMaintenanceEvidence(await this.client.request(this.path(s) + '/delivery', 'POST', { ...scope, delivery_ref: ref, section, offset }, signal, false), s.project_id, scope, rootSha, ref, section, offset); }
    current(s, q) { check(same(workRequirementScope(s, q.root_operation_id), q), 'WORK_MAINTENANCE_SCOPE_CHANGED'); }
    async prepare(s, input, signal) { s = structuredClone(s); const q = validateMaintenancePrepare(input), { root_sha256, expected_revision, action, delivery_ref, note, ...scope } = q; this.current(s, scope); return validateMaintenanceReview(await this.client.request(this.path(s) + '/prepare', 'POST', q, signal, false), s.project_id, q); }
    async command(s, input, signal) { s = structuredClone(s); const r = validateMaintenanceRequest(input), { session_ref, generation, workspace_ref, root_operation_id } = r, scope = { session_ref, generation, workspace_ref, root_operation_id }; this.current(s, scope); const identity = { scope, root_sha256: r.root_sha256, operation_id: r.operation_id, request_sha256: await hash(r) }; return validateMaintenanceOperation(await this.client.request(this.path(s) + '/links', 'POST', r, signal, false), s.project_id, identity, { request: r, allow_writes: true }); }
    async operation(s, input, signal) { s = structuredClone(s); const i = structuredClone(input); this.current(s, i.scope); check(sha(i.root_sha256) && id(i.operation_id) && sha(i.request_sha256)); return validateMaintenanceOperation(await this.client.request(this.path(s) + '/operations/inspect', 'POST', { ...i.scope, operation_id: i.operation_id, request_sha256: i.request_sha256 }, signal, false), s.project_id, i); }
}
module.exports = { canonical, hash, workRequirementScope, validScope, same, id, sha, count, text, sections, validateMaintenanceRef, validateMaintenanceContext, validateMaintenancePrepare, validateMaintenanceRequest, validateMaintenanceCatalog, validateMaintenanceReview, validateMaintenanceView, validateMaintenanceOperation, validateMaintenanceEvidence, OriginalWorkMaintenanceApi };
