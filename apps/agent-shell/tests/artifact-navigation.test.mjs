import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile, writeFile, mkdtemp, rm } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import readline from 'node:readline';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import { renderToString } from 'solid-js/web';
import { ApiClient } from '../src/api.ts';
import { DiscoveryApi } from '../src/discovery-api.ts';
import {reviewObjects} from '../src/workbench-review.ts';
import {ProjectObjectCatalog} from '../src/project-object-catalog.ts';
import { NavigationOwner } from '../src/registry.ts';
import {authoredCopy,authoredErrorCopy,authoredText} from '../src/authored-ui-copy.ts';
import {t as uiText} from './fixture-ui-language.mjs';

const app = resolve(dirname(fileURLToPath(import.meta.url)), '..'), root = resolve(app, '../..');
const source = await readFile(resolve(app, 'src/App.tsx'), 'utf8');
const parsed = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const component = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'App');
assert.ok(component?.body);
function actualFunction(name) {
  const node = component.body.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name);
  assert.ok(node, `actual App function ${name} must exist`); return node.getText(parsed);
}
let action;
function visit(node) {
  if (ts.isCallExpression(node) && node.expression.getText(parsed) === 'registry.registerAction') {
    const descriptor = node.arguments[0];
    if (ts.isObjectLiteralExpression(descriptor) && descriptor.properties.some(property => ts.isPropertyAssignment(property) && property.name.getText(parsed) === 'actionId' && property.initializer.text === 'proposal.taskbook')) action = node.arguments[1].getText(parsed);
  }
  ts.forEachChild(node, visit);
}
visit(component); assert.ok(action);

// Execute the exact App callbacks. UI presentation is observed at inspect(),
// while API, revisions and navigation ownership remain their real code.
const callbacks = ts.transpileModule([
  ...['envelope', 'refreshObjects', 'openProjectArtifact', 'openHarness'].map(actualFunction),
  `const generateTaskbook = (${action});`,
].join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
function controller(api, initialProject = 'project-a') {
  let projectId = initialProject, opened = [], drawer = true, errors = [], notices = [];
  const navigation = new NavigationOwner(), objects = { books: [], harnesses: [], reports: [], proposals: [], sessions: [] };
  const projectObjects=new ProjectObjectCatalog({books:(id,signal)=>api.taskbooks(id,signal),harnesses:(id,signal)=>api.harnesses(id,signal),reports:(id,signal)=>api.reports(id,signal),proposals:(id,signal)=>api.proposals(id,signal),sessions:(id,signal)=>new DiscoveryApi(api).list(id,signal)},id=>id===projectId,()=>{for(const key of Object.keys(objects))objects[key]=[];},patch=>Object.assign(objects,patch),()=>{},()=>({...objects}));
  projectObjects.activate(projectId);
  const context = {projectObjects,
    api, discoveryApi: new DiscoveryApi(api), navigation, project: () => ({ id: projectId }),
    scope: () => ({ projectId }), inspect: value => { opened.push(value); navigation.begin(); },
    setSkillDrawer: value => { drawer = value; }, setError: value => errors.push(value), setNotice: value => notices.push(value),
    message: value => value instanceof Error ? value.message : String(value),
    setBooks: value => { objects.books = value; }, setHarnesses: value => { objects.harnesses = value; },
    setReports: value => { objects.reports = value; }, setProposals: value => { objects.proposals = value; },
    setDiscoverySessions: value => { objects.sessions = value; }, DOMException,authoredCopy,authoredErrorCopy,authoredText,uiText,
  };
  const values = new Function(...Object.keys(context), callbacks + '\nreturn {openProjectArtifact,openHarness,generateTaskbook};')(...Object.values(context));
  return { ...values, navigation, objects, opened, errors, notices, get drawer() { return drawer; },
    switchProject(value) { navigation.begin(); projectId = value; projectObjects.activate(value); }, newNavigation() { navigation.begin(); } };
}
const entity = (id, project_id = 'project-a', revision = 3) => ({ id, project_id, revision, sha256: 'a'.repeat(64), review_status: 'CURRENT', payload: { title: id + ' 标题', candidate: { title: id + ' 标题' } } });
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

function fixture(t) {
  const child = spawn(resolve(root, '.venv/bin/python'), [resolve(app, 'tests/delivery-http-fixture.py')], {
    cwd: root, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }, stdio: ['pipe', 'pipe', 'pipe'],
  });
  const waiting = [], calls = []; let stderr = '', intercept;
  child.stderr.on('data', value => { stderr = (stderr + value).slice(-5000); });
  const lines = readline.createInterface({ input: child.stdout });
  lines.on('line', line => { const item = waiting.shift(); if (item) try { item.resolve(JSON.parse(line)); } catch (error) { item.reject(error); } });
  child.on('error', error => { for (const item of waiting.splice(0)) item.reject(error); });
  child.on('exit', code => { for (const item of waiting.splice(0)) item.reject(Error(`Actual API fixture exited ${code}: ${stderr}`)); });
  t.after(async () => { child.stdin.end(); await new Promise(done => child.exitCode !== null ? done() : child.once('exit', done)); lines.close(); });
  const raw = command => new Promise((resolve, reject) => { waiting.push({ resolve, reject }); child.stdin.write(JSON.stringify(command) + '\n'); });
  const api = new ApiClient(async (url, options) => {
    const call = { path: url, method: options?.method ?? 'GET' }; calls.push(call);
    let reply = await raw({ method: call.method, path: url, headers: options?.headers ?? {}, body: options?.body ? JSON.parse(options.body) : undefined });
    if (intercept) reply = await intercept(call, reply);
    return new Response(JSON.stringify(reply.body), { status: reply.status });
  });
  return { raw, api, calls, setIntercept(value) { intercept = value; } };
}
async function originalProposal(f) {
  const setup = await f.raw({ fixture: 'setup' }); assert.equal(setup.status, 200, JSON.stringify(setup.body));
  const scene = setup.body;
  const imported = await f.api.materials(scene.pid, scene.workspace.id, ['普通说明.md']);
  const materials = imported.materials.map(value => value.document_ref);
  const proposal = await f.api.request(`${f.api.projectPath(scene.pid)}/planning/proposals`, 'POST', {
    operation_id: 'user-authored-navigation-proposal', mode: 'manual', prompt: '用户提供原创工作要求，用于验证产物入口。', materials,
    candidate: { title: '用户原创资料核对工作', summary: '核对来源并保留结果。', nodes: [{ id: 'review', title: '核对来源', description: '逐项核对原始资料。', outputs: ['核对记录'], acceptance: ['保留原资料版本'] }], questions: [] },
  });
  assert.equal(proposal.payload.execution.model_calls, 0);
  f.calls.length = 0; return { scene, proposal };
}

test('actual Workbench review renders real proposal/book/harness/report objects', async t => {
  assert.ok(process.env.TMPDIR);const directory=await mkdtemp(resolve(process.env.TMPDIR,'workbench-review-'));t.after(()=>rm(directory,{recursive:true,force:true}));
  const viewSource=await readFile(resolve(app,'src/WorkbenchReview.tsx'),'utf8');
  const js=ts.transpileModule(viewSource,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
  const compiled=babel.transformSync(js,{filename:'actual-workbench-review.jsx',presets:[[solidPreset,{generate:'ssr',hydratable:false}]],configFile:false,babelrc:false}).code.replace(/from (["'])\.\/ui-language\1/g,(_all,quote)=>`from ${quote}${new URL('./fixture-ui-language.mjs',import.meta.url).href}${quote}`).replace(/from (["'])solid-js(\/web)?\1/g,(_all,quote,suffix='')=>`from ${quote}${import.meta.resolve('solid-js'+suffix)}${quote}`);
  const file=resolve(directory,'actual-review.mjs');await writeFile(file,compiled);const {default:View}=await import(pathToFileURL(file).href);
  const proposal=entity('actual-proposal');proposal.payload.candidate.questions=[];const foreign=entity('foreign-proposal','other');foreign.payload.candidate.questions=[];
  const objects=reviewObjects('project-a',{sessions:[],proposals:[proposal,foreign],books:[entity('actual-book','project-a',8)],harnesses:[entity('actual-harness','project-a',2)],reports:[{id:'actual-report',filename:'原创报告.docx',sha256:'f'.repeat(64)}]});
  const props={projectName:'实际项目',objects,tab:'current',onTab:()=>assert.fail('SSR cannot mutate'),onOpen:()=>assert.fail('SSR cannot open'),onFolder:()=>assert.fail('SSR cannot bind')};
  const html=renderToString(()=>View(props));for(const text of ['任务与图','任务书','Harness','交付','actual-proposal 标题','actual-book 标题','actual-harness 标题','v8','v2','原创报告.docx'])assert.ok(html.includes(text),text);assert.doesNotMatch(html,/foreign-proposal/);
  assert.match(renderToString(()=>View({...props,objects:[]})),/还没有任务与产物/);
});

test('actual App open callbacks preserve source revision/SHA, close the drawer and reject a foreign or superseded harness read', async () => {
  const read = deferred(), calls = [], api = { harnessState: (...args) => { calls.push(args); return read.promise; } };
  const value = controller(api), book = entity('book'); value.openProjectArtifact('taskbook', book);
  assert.equal(value.drawer, false); assert.equal(value.opened[0].data, book);
  assert.equal(value.opened[0].revision, 3); assert.equal(value.opened[0].contentHash, book.sha256);
  value.openProjectArtifact('proposal', entity('foreign', 'other')); assert.equal(value.opened.length, 1);
  await value.openHarness(entity('foreign-harness', 'other')); assert.equal(calls.length, 0);
  const pending = value.openHarness(entity('owned-harness')); value.switchProject('other'); value.switchProject('project-a');
  read.resolve({ harness_id: 'owned-harness', status: 'READY', nodes: [] }); await pending;
  assert.equal(value.opened.length, 1, 'switching away and back cannot let an old request steal the current page');
  const ready = controller({ harnessState: async () => ({ harness_id: 'owned-harness', status: 'READY', nodes: [] }) });
  await ready.openHarness(entity('owned-harness', 'project-a', 7)); assert.equal(ready.opened[0].revision, 7); assert.equal(ready.opened[0].data.harness.id, 'owned-harness');
});

test('actual app API creates the proposal taskbook once, reads that exact immutable version and opens it with refreshed artifact lists', async t => {
  const f = fixture(t), { scene, proposal } = await originalProposal(f), value = controller(f.api, scene.pid);
  const result = await value.generateTaskbook({ data: proposal });
  assert.equal(value.opened.length, 1); const opened = value.opened[0];
  assert.equal(opened.type, 'taskbook'); assert.equal(opened.artifactId, result.taskbook_id);
  assert.equal(opened.revision, result.revision); assert.equal(opened.contentHash, result.sha256); assert.equal(opened.data.project_id, scene.pid);
  assert.equal(value.drawer, false); assert.ok(value.objects.books.some(book => book.id === result.taskbook_id)); assert.ok(value.objects.proposals.some(item => item.id === proposal.id));
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
  assert.ok(f.calls.some(call => call.method === 'GET' && call.path.endsWith(`/taskbooks/${result.taskbook_id}?revision=${result.revision}`)));
  assert.equal(f.calls.some(call => /advance|confirm|\/runs|native-messages/.test(call.path)), false);
});

test('real API writes remain saved when the user navigates away during POST, exact-book read or list refresh; old results never steal the page', async t => {
  const f = fixture(t), { scene, proposal } = await originalProposal(f);
  for (const phase of ['POST', 'BOOK_READ', 'LIST_REFRESH']) {
    const received = deferred(), release = deferred(); let held = false;
    f.calls.length = 0;
    f.setIntercept(async (call, reply) => {
      if (!held && (phase === 'POST' && call.method === 'POST' || phase === 'BOOK_READ' && /\/taskbooks\/[^/]+\?revision=/.test(call.path) || phase === 'LIST_REFRESH' && call.path.endsWith('/taskbooks'))) { held = true; received.resolve(); await release.promise; }
      return reply;
    });
    const value = controller(f.api, scene.pid), pending = value.generateTaskbook({ data: proposal });
    await received.promise; value.switchProject('another-project'); value.switchProject(scene.pid); release.resolve(); await pending;
    assert.deepEqual(value.opened, []); assert.equal(value.notices.length, 0); assert.equal(value.objects.books.length, 0);
    assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
    f.setIntercept(undefined);
    assert.ok((await f.api.taskbooks(scene.pid)).length > 0, 'actual saved taskbook survives navigation without another POST');
  }
});

test('mismatched exact book response is never opened and a lost/401 mutation result never replays the original POST', async t => {
  const f = fixture(t), { scene, proposal } = await originalProposal(f);
  f.setIntercept(async (call, reply) => /\/taskbooks\/[^/]+\?revision=/.test(call.path) ? { ...reply, body: { ...reply.body, sha256: '0'.repeat(64) } } : reply);
  const mismatched = controller(f.api, scene.pid);
  await assert.rejects(mismatched.generateTaskbook({ data: proposal }), /原生成版本不一致/); assert.deepEqual(mismatched.opened, []);
  f.calls.length = 0;
  f.setIntercept(async (call, reply) => call.method === 'POST' ? { status: 401, body: { error: { code: 'AUTH_CHANGED', message: 'reply lost after persisted write' } } } : reply);
  const unauthorized = controller(f.api, scene.pid);
  await assert.rejects(unauthorized.generateTaskbook({ data: proposal }), error => error.status === 401);
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 1); assert.deepEqual(unauthorized.opened, []);
});
