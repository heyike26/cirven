'use strict';
// Generated from desktop source authority; do not edit independently.
// SHA256 apps/agent-shell/src/original-work-deliveries.ts fa6ed60c3ea12341f71a47b5e6e4c7f4cc219a881bb23dff740d2366a6cf5016
// SHA256 apps/agent-shell/src/work-delivery-editor.ts eabd9f74cb8e6921077b3d8900c157d8248d83d0047dc935020c3d23947ed352
// SHA256 apps/agent-shell/src/original-work-roots.ts 5b36d33523c7a4f1f0216b2fa334d23b9d0fc8c5c5816edc190b5b74e144ffb8
// SHA256 apps/agent-shell/src/original-session-files.ts e7f0fb0a83b3317251cff5b34cf90c341c13b0db1cf104bf29baa6a2325fcfbe
// SHA256 apps/agent-shell/src/api.ts e0aef9d6bd347e452ad63402ac9ef398286066251bf3c7c78b4be73abd4f78e2
const { webcrypto: crypto } = require('node:crypto');
const { canonical, hash, workRequirementScope } = require('./work-case-wire.cjs');
const api = Object.freeze({ request() { throw Error('WORK_DELIVERY_TRANSPORT_REQUIRED'); }, bytes() { throw Error('WORK_DELIVERY_TRANSPORT_REQUIRED'); } });
const same = (a, b) => canonical(a) === canonical(b);
const bytes = (v) => new TextEncoder().encode(canonical(v)).length;
const obj = (v) => !!v && typeof v === 'object' && !Array.isArray(v);
const exact = (v, keys) => obj(v) && Object.keys(v).length === keys.length && Object.keys(v).every(k => keys.includes(k));
const sha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v);
const count = (v, max = Number.MAX_SAFE_INTEGER) => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 && v <= max;
const text = (v, max) => typeof v === 'string' && Array.from(v).length <= max && !/[\0\uD800-\uDFFF]/u.test(v);
const native = (v) => typeof v === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v);
function check(value, code = 'ORIGINAL_WORK_DELIVERY_CHANGED') { if (!value)
    throw Error(code); }
function reference(v) { return exact(v, ['id', 'revision', 'sha256']) && id(v.id) && v.revision === 1 && sha(v.sha256); }
function validScope(v) { return exact(v, ['session_ref', 'generation', 'workspace_ref', 'root_operation_id']) && reference(v.session_ref) && count(v.generation) && v.generation >= 1 && exact(v.workspace_ref, ['id', 'revision', 'binding_sha256']) && sha(v.workspace_ref.id) && count(v.workspace_ref.revision) && v.workspace_ref.revision >= 1 && sha(v.workspace_ref.binding_sha256) && id(v.root_operation_id); }
function deliveryArchivePath(path) {
    check(text(path, 1024) && path.length > 0 && !/[\\:\x00-\x1f\x7f]/.test(path) && !path.startsWith('/'));
    for (const part of path.split('/'))
        check(part !== '' && part !== '.' && part !== '..' && !/[<>"|?*]/.test(part) && !/[ .]$/.test(part) && !/^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)/i.test(part));
    return 'files/' + path;
}
function source(v, s, pid) {
    check(exact(v, ['kind', 'project_id', 'workspace_id', 'binding_revision', 'binding_sha256', 'path', 'revision', 'sha256']) && v.kind === 'local_file' && v.project_id === pid && v.workspace_id === s.workspace_ref.id && v.binding_revision === s.workspace_ref.revision && v.binding_sha256 === s.workspace_ref.binding_sha256 && count(v.revision) && v.revision >= 1 && sha(v.sha256));
    deliveryArchivePath(v.path);
}
function prepareQuery(v, pid, editing = false) {
    const { root_sha256, expected_revision, version_label, notes, files, include_evidence, ...scope } = v;
    check(validScope(scope) && sha(root_sha256) && count(expected_revision, 32) && text(version_label, 100) && (editing || version_label.length > 0) && text(notes, 4000) && (editing || notes.length > 0) && typeof include_evidence === 'boolean' && Array.isArray(files) && (editing || files.length >= 1) && files.length <= 64 && bytes(v) <= 131072);
    const names = new Set();
    for (const f of files) {
        source(f, scope, pid);
        const name = deliveryArchivePath(f.path).normalize('NFC').toUpperCase();
        check(!names.has(name));
        names.add(name);
    }
    return structuredClone(v);
}
function validateDeliveryPrepare(v, pid) { return prepareQuery(v, pid); }
function validateDeliveryDraftQuery(v, pid) { return prepareQuery(v, pid, true); }
function validateDeliveryRequest(v, pid) {
    const { operation_id, review_sha256, review_confirmed, known_gaps_acknowledged, ...query } = v;
    check(id(operation_id) && sha(review_sha256) && review_confirmed === true && typeof known_gaps_acknowledged === 'boolean' && bytes(v) <= 131072);
    validateDeliveryPrepare(query, pid);
    return structuredClone(v);
}
function flags(v) { check(v && v.model_calls === 0 && v.commands_started === 0 && v.source_files_written === 0 && v.permission_granted === false && v.build_performed === false && v.deployment_performed === false && v.restore_performed === false && v.original_task_status_changed === false && v.whole_task_acceptance === 'NOT_ASSESSED' && v.target_environment_verified === false && v.all_dependencies_captured === false && v.unsaved_editor_contents_included === false && v.atomic_workspace_snapshot === false && v.file_metadata_restoration === false && v.source_writer_attribution === 'NOT_PROVEN'); }
function parent(v, previous) { if (previous === 0) {
    check(v === null);
    return;
} check(exact(v, ['operation_id', 'request_sha256', 'manifest_sha256', 'revision']) && id(v?.operation_id) && sha(v?.request_sha256) && sha(v?.manifest_sha256) && v?.revision === previous); }
function warnings(v) { check(Array.isArray(v) && v.length >= 1 && v.length <= 1024 && new Set(v).size === v.length && v.includes('TARGET_AND_WHOLE_TASK_ACCEPTANCE_NOT_ASSESSED') && v.every(s => typeof s === 'string' && /^[A-Z0-9_]{1,160}$/.test(s))); }
function inventory(rows, total, s, pid, expected) {
    check(Array.isArray(rows) && rows.length >= 1 && rows.length <= 64 && count(total, 16777216) && (!expected || expected.length === rows.length));
    let sum = 0;
    const names = new Set();
    for (const [i, row] of rows.entries()) {
        check(exact(row, ['source_ref', 'byte_length', 'archive_path']) && count(row.byte_length, 16777216));
        source(row.source_ref, s, pid);
        check(row.archive_path === deliveryArchivePath(row.source_ref.path) && (!expected || same(row.source_ref, expected[i])));
        const name = row.archive_path.normalize('NFC').toUpperCase();
        check(!names.has(name));
        names.add(name);
        sum += row.byte_length;
    }
    check(sum === total);
}
async function rootBasis(v, checksum, pid, scope) {
    check(v && sha(checksum) && await hash(v) === checksum && v.project_id === pid && same(v.session_ref, scope.session_ref) && same(v.workspace_ref, scope.workspace_ref) && v.root_operation_id === scope.root_operation_id && count(v.root_generation, scope.generation) && v.root_generation >= 1 && native(v.native_session_id) && native(v.native_message_id) && sha(v.request_sha256));
}
function pointers(v) { check(Array.isArray(v) && v.length <= 1 && v.every(row => exact(row, ['operation_id', 'request_sha256']) && id(row.operation_id) && sha(row.request_sha256))); }
function deliveryDownloadPath(pid, sid, root, op, requestSha) { return '/api/projects/' + encodeURIComponent(pid) + '/agent-sessions/' + encodeURIComponent(sid) + '/work-deliveries/' + encodeURIComponent(op) + '/archive?' + new URLSearchParams({ root_operation_id: root, request_sha256: requestSha }); }
function artifact(v, revision) { check(exact(v, ['path', 'sha256', 'byte_length', 'filename']) && /^attempts\/0[1-4]\/package\.zip$/.test(v.path) && sha(v.sha256) && count(v.byte_length, 25165824) && v.byte_length >= 1 && v.filename === 'cirven-delivery-' + revision + '.zip'); }
async function validateDeliveryReview(v, pid, query) {
    validateDeliveryPrepare(query, pid);
    check(v && bytes(v) <= 131072);
    flags(v);
    check(v.kind === 'ORIGINAL_WORK_DELIVERY_REVIEW' && v.version === 1 && v.project_id === pid && v.query_sha256 === await hash(query) && v.root_sha256 === query.root_sha256 && v.revision === query.expected_revision && sha(v.manifest_sha256) && v.read_only === true && v.artifact_files_written === 0 && v.version_label === query.version_label && v.notes === query.notes && count(v.history_capacity_remaining, 32));
    const expected = { schema: 'cirven.original-work-delivery-review.v1', query, manifest_sha256: v.manifest_sha256, root_sha256: query.root_sha256, expected_revision: query.expected_revision };
    check(same(v.basis, expected) && v.review_sha256 === await hash(expected));
    await rootBasis(v.root_basis, v.root_sha256, pid, query);
    parent(v.parent, query.expected_revision);
    inventory(v.files, v.file_bytes, query, pid, query.files);
    warnings(v.warnings);
    pointers(v.pending);
    return structuredClone(v);
}
async function validateDeliveryOutcome(v, pid, identity, expected) {
    check(validScope(identity.scope) && sha(identity.root_sha256) && id(identity.operation_id) && sha(identity.request_sha256) && v && bytes(v) <= 131072);
    flags(v);
    check(v.version === 1 && v.project_id === pid && v.operation_id === identity.operation_id && v.request_sha256 === identity.request_sha256 && v.root_sha256 === identity.root_sha256 && sha(v.manifest_sha256) && (!identity.manifest_sha256 || identity.manifest_sha256 === v.manifest_sha256));
    if (expected) {
        validateDeliveryRequest(expected, pid);
        check(await hash(expected) === identity.request_sha256 && expected.operation_id === identity.operation_id && expected.root_sha256 === identity.root_sha256 && expected.root_operation_id === identity.scope.root_operation_id && same(expected.session_ref, identity.scope.session_ref) && same(expected.workspace_ref, identity.scope.workspace_ref) && expected.generation <= identity.scope.generation);
    }
    if (v.kind === 'ORIGINAL_WORK_DELIVERY_PENDING') {
        check(v.status === 'INCOMPLETE_REVIEW_REQUIRED' && v.automatic_retry === false);
        if (v.retention !== null) {
            const r = v.retention;
            check(r && r.operation_id === v.operation_id && r.request_sha256 === v.request_sha256 && text(r.statement, 4000) && r.statement.length >= 1 && text(r.retained_at, 100) && r.retained_at.length > 0 && r.status === 'UNKNOWN_RETAINED_NOT_CANCELLED_OR_ROLLED_BACK' && bytes(r) <= 16384);
        }
    }
    else {
        check(v.kind === 'ORIGINAL_WORK_DELIVERY' && same(v.session_ref, identity.scope.session_ref) && v.root_operation_id === identity.scope.root_operation_id && count(v.revision, 32) && v.revision >= 1 && v.artifact_files_written === 1 && text(v.version_label, 100) && v.version_label.length >= 1 && text(v.notes, 4000) && v.notes.length >= 1 && text(v.recorded_at, 100) && v.recorded_at.length >= 1);
        check(!expected || v.revision === expected.expected_revision + 1 && v.version_label === expected.version_label && v.notes === expected.notes);
        parent(v.parent, v.revision - 1);
        inventory(v.files, v.file_bytes, identity.scope, pid, expected?.files);
        warnings(v.warnings);
        artifact(v.artifact, v.revision);
        check(v.download_url === deliveryDownloadPath(pid, identity.scope.session_ref.id, identity.scope.root_operation_id, v.operation_id, v.request_sha256));
    }
    return structuredClone(v);
}
async function validateDeliveries(v, pid, scope, offset) {
    check(validScope(scope) && count(offset, 32) && v && bytes(v) <= 131072);
    flags(v);
    check(v.kind === 'ORIGINAL_WORK_DELIVERIES' && v.version === 1 && v.project_id === pid && same(v.scope, scope) && v.query_sha256 === await hash({ ...scope, offset }) && v.read_only === true && count(v.revision, 32) && count(v.history_capacity_remaining, 32));
    await rootBasis(v.root_basis, v.root_sha256, pid, scope);
    pointers(v.pending);
    check(Array.isArray(v.entries) && v.entries.length === Math.min(10, Math.max(0, v.revision - offset)) && v.next_offset === (offset + 10 < v.revision ? offset + 10 : null) && Array.isArray(v.retained) && v.retained.length <= 32 && v.revision + v.retained.length + v.pending.length + v.history_capacity_remaining === 32);
    const ops = new Set();
    for (const [i, e] of v.entries.entries()) {
        check(e && id(e.operation_id) && !ops.has(e.operation_id) && sha(e.request_sha256) && sha(e.manifest_sha256) && e.revision === offset + i + 1 && text(e.version_label, 100) && e.version_label.length > 0 && text(e.recorded_at, 100) && e.recorded_at.length > 0 && count(e.file_count, 64) && e.file_count >= 1 && count(e.file_bytes, 16777216) && text(e.notes_excerpt, 240) && count(e.notes_length, 4000) && e.notes_length >= Array.from(e.notes_excerpt).length && sha(e.notes_sha256));
        ops.add(e.operation_id);
        parent(e.parent, e.revision - 1);
        if (i > 0) {
            const p = v.entries[i - 1];
            check(same(e.parent, { operation_id: p.operation_id, request_sha256: p.request_sha256, manifest_sha256: p.manifest_sha256, revision: p.revision }));
        }
        artifact(e.artifact, e.revision);
        warnings(e.warnings);
        check(e.download_url === deliveryDownloadPath(pid, scope.session_ref.id, scope.root_operation_id, e.operation_id, e.request_sha256));
        if (e.notes_length <= 240)
            check(e.notes_length === Array.from(e.notes_excerpt).length && await utf8Hash(e.notes_excerpt) === e.notes_sha256);
    }
    for (const r of v.retained) {
        const k = r.retention;
        check(r && id(r.operation_id) && !ops.has(r.operation_id) && sha(r.request_sha256) && sha(r.manifest_sha256) && k && k.status === 'UNKNOWN_RETAINED_NOT_CANCELLED_OR_ROLLED_BACK' && text(k.retained_at, 100) && k.retained_at.length > 0 && text(k.statement_excerpt, 240) && count(k.statement_length, 4000) && k.statement_length >= Array.from(k.statement_excerpt).length && sha(k.statement_sha256));
        ops.add(r.operation_id);
        if (k.statement_length <= 240)
            check(k.statement_length === Array.from(k.statement_excerpt).length && await utf8Hash(k.statement_excerpt) === k.statement_sha256);
    }
    for (const p of v.pending)
        check(!ops.has(p.operation_id));
    return structuredClone(v);
}
async function validateDeliveryRecovery(v, pid, identity) {
    check(validScope(identity.scope) && v && bytes(v) <= 524288);
    flags(v);
    const query = { ...identity.scope, operation_id: identity.operation_id, request_sha256: identity.request_sha256 };
    check(v.kind === 'ORIGINAL_WORK_DELIVERY_RECOVERY' && v.version === 1 && v.project_id === pid && v.query_sha256 === await hash(query) && v.root_sha256 === identity.root_sha256 && sha(v.manifest_sha256) && (!identity.manifest_sha256 || identity.manifest_sha256 === v.manifest_sha256) && v.read_only === true && v.metadata_only === true && v.artifact_files_written === 0);
    await rootBasis(v.root_basis, v.root_sha256, pid, identity.scope);
    const r = validateDeliveryRequest(v.original_request, pid);
    const { operation_id, review_sha256, review_confirmed, known_gaps_acknowledged, ...originalQuery } = r;
    check(r.root_sha256 === v.root_sha256 && r.operation_id === identity.operation_id && await hash(r) === identity.request_sha256 && r.review_sha256 === await hash({ schema: 'cirven.original-work-delivery-review.v1', query: originalQuery, manifest_sha256: v.manifest_sha256, root_sha256: r.root_sha256, expected_revision: r.expected_revision }));
    await validateDeliveryOutcome(v.outcome, pid, { ...identity, manifest_sha256: v.manifest_sha256 }, r);
    const p = v.preview;
    check(p && bytes(p) <= 131072 && v.preview_sha256 === await hash(p) && p.version_label === r.version_label && p.notes === r.notes);
    inventory(p.files, p.file_bytes, identity.scope, pid, r.files);
    parent(p.parent, r.expected_revision);
    warnings(p.warnings);
    check(p.warnings.includes('EVIDENCE_CONTENT_NOT_INCLUDED') === !r.include_evidence);
    if (v.outcome.kind === 'ORIGINAL_WORK_DELIVERY')
        check(same(p, { version_label: v.outcome.version_label, notes: v.outcome.notes, parent: v.outcome.parent, files: v.outcome.files, file_bytes: v.outcome.file_bytes, warnings: v.outcome.warnings }));
    return structuredClone(v);
}
async function utf8Hash(value) { return [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)))].map(b => b.toString(16).padStart(2, '0')).join(''); }
function validateDeliveryTree(v, path, offset) {
    check(typeof path === 'string' && count(offset, 10000));
    if (path)
        deliveryArchivePath(path);
    check(exact(v, ['entries', 'total', 'next_offset']) && bytes(v) <= 262144 && count(v.total, 10000) && Array.isArray(v.entries) && v.entries.length === Math.min(100, Math.max(0, v.total - offset)) && v.next_offset === (offset + 100 < v.total ? offset + 100 : null));
    const seen = new Set();
    for (const e of v.entries) {
        check(exact(e, ['name', 'path', 'type', 'readable', 'byte_length']) && text(e.name, 255) && e.name.length > 0 && !/[\\/]/.test(e.name) && e.path === (path ? path + '/' : '') + e.name && !seen.has(e.path) && ['directory', 'file', 'symlink', 'unsupported'].includes(e.type) && typeof e.readable === 'boolean' && (!e.readable || ['directory', 'file'].includes(e.type)) && count(e.byte_length));
        seen.add(e.path);
    }
    return structuredClone(v);
}
class OriginalWorkDeliveriesApi {
    client;
    constructor(client = api) {
        this.client = client;
    }
    path(pid, sid) { check(id(pid) && id(sid)); return '/api/projects/' + encodeURIComponent(pid) + '/agent-sessions/' + encodeURIComponent(sid) + '/work-deliveries'; }
    async prepare(s, input, signal) { const pid = s.project_id, query = validateDeliveryPrepare(structuredClone(input), pid), path = this.path(pid, s.session_ref.id); check(same(workRequirementScope(s, query.root_operation_id), ((({ root_sha256, expected_revision, version_label, notes, files, include_evidence, ...scope }) => scope)(query)))); return validateDeliveryReview(await this.client.request(path + '/prepare', 'POST', query, signal, false), pid, query); }
    async list(s, root, offset = 0, signal) { const pid = s.project_id, scope = workRequirementScope(s, root), path = this.path(pid, s.session_ref.id); check(count(offset, 32)); return validateDeliveries(await this.client.request(path + '/list', 'POST', { ...scope, offset }, signal, false), pid, scope, offset); }
    async submit(s, input, manifestSha, signal) { const pid = s.project_id, request = validateDeliveryRequest(structuredClone(input), pid), scope = workRequirementScope(s, request.root_operation_id), path = this.path(pid, s.session_ref.id); check(sha(manifestSha) && same(scope, { session_ref: request.session_ref, generation: request.generation, workspace_ref: request.workspace_ref, root_operation_id: request.root_operation_id })); const identity = { scope, root_sha256: request.root_sha256, operation_id: request.operation_id, request_sha256: await hash(request), manifest_sha256: manifestSha }; return validateDeliveryOutcome(await this.client.request(path + '/packages', 'POST', request, signal, false), pid, identity, request); }
    async inspect(s, identity, signal, expected) { return this.lookup('operations/inspect', s, identity, undefined, signal, expected); }
    async recovery(s, input, signal) { const pid = s.project_id, identity = structuredClone(input), scope = structuredClone(workRequirementScope(s, identity.scope.root_operation_id)); check(same(scope, identity.scope) && sha(identity.root_sha256) && id(identity.operation_id) && sha(identity.request_sha256)); const path = this.path(pid, scope.session_ref.id), query = { ...scope, operation_id: identity.operation_id, request_sha256: identity.request_sha256 }; return validateDeliveryRecovery(await this.client.request(path + '/operations/recovery', 'POST', query, signal, false), pid, identity); }
    async continue(s, identity, signal, expected) { return this.lookup('continue', s, identity, undefined, signal, expected); }
    async retain(s, identity, statement, signal, expected) { check(text(statement, 4000) && statement.length > 0); const value = await this.lookup('retain', s, identity, statement, signal, expected); check(value.kind === 'ORIGINAL_WORK_DELIVERY' || value.retention?.statement === statement); return value; }
    async lookup(action, s, input, statement, signal, expectedInput) { const pid = s.project_id, identity = structuredClone(input), expected = expectedInput && validateDeliveryRequest(structuredClone(expectedInput), pid), scope = workRequirementScope(s, identity.scope.root_operation_id), path = this.path(pid, s.session_ref.id); check(validScope(identity.scope) && same(scope, identity.scope) && sha(identity.root_sha256) && id(identity.operation_id) && sha(identity.request_sha256) && (!identity.manifest_sha256 || sha(identity.manifest_sha256))); const body = { ...scope, operation_id: identity.operation_id, request_sha256: identity.request_sha256, ...(statement === undefined ? {} : { statement }) }; return validateDeliveryOutcome(await this.client.request(path + '/' + action, 'POST', body, signal, false), pid, identity, expected); }
    async download(s, root, input, signal) { const pid = s.project_id, receipt = structuredClone(input), identity = { scope: workRequirementScope(s, root), root_sha256: receipt.root_sha256, operation_id: receipt.operation_id, request_sha256: receipt.request_sha256, manifest_sha256: receipt.manifest_sha256 }; await validateDeliveryOutcome(receipt, pid, identity); const raw = await this.client.bytes(deliveryDownloadPath(pid, identity.scope.session_ref.id, root, receipt.operation_id, receipt.request_sha256), signal); check(raw.byteLength === receipt.artifact.byte_length && raw.byteLength <= 25165824); const checksum = [...new Uint8Array(await crypto.subtle.digest('SHA-256', raw))].map(b => b.toString(16).padStart(2, '0')).join(''); check(checksum === receipt.artifact.sha256); return { bytes: raw, filename: receipt.artifact.filename, sha256: checksum }; }
    async selectSaved(s, root, path, signal) { const scope = workRequirementScope(s, root), pid = s.project_id; deliveryArchivePath(path); const url = '/api/projects/' + encodeURIComponent(pid) + '/local-workspaces/' + encodeURIComponent(scope.workspace_ref.id) + '/file?' + new URLSearchParams({ path }), value = await this.client.request(url, 'GET', undefined, signal, false); source(value.source_ref, scope, pid); check(value.source_ref.path === path && value.current === true && value.live_sha256 === value.source_ref.sha256 && count(value.byte_length, 16777216)); return { source_ref: structuredClone(value.source_ref), byte_length: value.byte_length }; }
    async tree(s, root, path = '', offset = 0, signal) { const scope = workRequirementScope(s, root), pid = s.project_id; check(typeof path === 'string' && count(offset, 10000)); if (path)
        deliveryArchivePath(path); const url = '/api/projects/' + encodeURIComponent(pid) + '/local-workspaces/' + encodeURIComponent(scope.workspace_ref.id) + '/tree?' + new URLSearchParams({ path, offset: String(offset), limit: '100' }); return validateDeliveryTree(await this.client.request(url, 'GET', undefined, signal, false), path, offset); }
}
const roots = (() => {
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
    const same = (a, b) => canonical(a) === canonical(b), sha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v), id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v), native = (v) => typeof v === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(v), count = (v, max) => Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) <= max, text = (v, max) => typeof v === 'string' && Array.from(v).length <= max && !v.includes('\0');
    function check(v) { if (!v)
        throw Error('ORIGINAL_WORK_ROOTS_CHANGED'); }
    function workRootScope(s) { const scope = originalFileScope(s); check(scope); const { project_id, ...rest } = scope; return structuredClone(rest); }
    async function validateWorkRoots(v, pid, scope, offset) {
        check(v && new TextEncoder().encode(JSON.stringify(v)).length <= 65536 && count(offset, 1024) && v.kind === 'ORIGINAL_WORK_ROOTS' && v.version === 1 && v.project_id === pid && same(v.scope, scope) && v.query_sha256 === await hash({ ...scope, offset }) && Array.isArray(v.entries) && v.entries.length <= 8 && v.read_only === true && v.metadata_only === true && v.model_calls === 0 && v.commands_started === 0 && v.source_files_written === 0 && v.permission_granted === false && v.automatic_replay === false && v.whole_task_acceptance === 'NOT_ASSESSED');
        const c = v.coverage;
        check(c && typeof c.complete === 'boolean' && Array.isArray(c.reason_codes) && c.reason_codes.length <= 8 && new Set(c.reason_codes).size === c.reason_codes.length && c.reason_codes.every(r => ['NATIVE_WORK_USES_EXISTING_OBJECT_CONTEXT_ENTRY', 'OPERATION_SCAN_CAPACITY_REACHED', 'OPERATION_BYTE_CAPACITY_REACHED', 'ORIGINAL_OPERATION_UNAVAILABLE', 'OPERATION_DIRECTORY_UNAVAILABLE', 'SELECTED_ROOT_UNAVAILABLE'].includes(r)) && c.complete === (c.reason_codes.length === 0) && count(c.scanned_entries, 1024) && count(c.operation_bytes_read, 8388608) && count(c.unacknowledged_messages, c.scanned_entries) && count(c.candidate_count, c.scanned_entries) && c.scope === 'ORDINARY_ACKNOWLEDGED_MESSAGE_METADATA_NOT_TASK_ACCEPTANCE' && v.next_offset === (offset + 8 < c.candidate_count ? offset + 8 : null));
        const expected = Math.min(8, Math.max(0, c.candidate_count - offset));
        check(v.entries.length <= expected && (v.entries.length === expected || c.reason_codes.includes('SELECTED_ROOT_UNAVAILABLE')));
        const seen = new Set();
        for (const e of v.entries) {
            const b = e.root_basis;
            check(e && id(e.operation_id) && !seen.has(e.operation_id) && sha(e.root_sha256) && b && await hash(b) === e.root_sha256 && b.project_id === pid && same(b.session_ref, scope.session_ref) && same(b.workspace_ref, scope.workspace_ref) && b.root_operation_id === e.operation_id && count(b.root_generation, scope.generation) && b.root_generation >= 1 && native(b.native_session_id) && native(b.native_message_id) && sha(b.request_sha256) && text(e.label, 280) && text(e.status, 100) && e.dispatch_acknowledged === true && e.whole_task_acceptance === 'NOT_ASSESSED');
            seen.add(e.operation_id);
        }
        return structuredClone(v);
    }
    class OriginalWorkRootsApi {
        client;
        constructor(client = api) {
            this.client = client;
        }
        async inspect(s, offset = 0, signal) { const pid = s.project_id, scope = workRootScope(s); check(id(pid) && count(offset, 1024)); const path = '/api/projects/' + encodeURIComponent(pid) + '/agent-sessions/' + encodeURIComponent(scope.session_ref.id) + '/work-roots/inspect'; return validateWorkRoots(await this.client.request(path, 'POST', { ...scope, offset }, signal, false), pid, scope, offset); }
    }
    return { workRootScope, validateWorkRoots, OriginalWorkRootsApi };
})();
module.exports = { canonical, hash, same, workRequirementScope, validScope, sha, id, count, text, deliveryArchivePath, validateDeliveryTree, validateDeliveryPrepare, validateDeliveryDraftQuery, validateDeliveryRequest, validateDeliveryReview, validateDeliveryOutcome, validateDeliveries, validateDeliveryRecovery, deliveryDownloadPath, OriginalWorkDeliveriesApi, ...roots };
