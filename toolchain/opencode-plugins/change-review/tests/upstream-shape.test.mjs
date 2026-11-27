import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {digest} from '../files.mjs';
const source=process.env.EEAS_CHANGE_REVIEW_UPSTREAM_ROOT;
test('exact official source pins: hook input shape, after-success order and original tool parameters', {skip:!source},()=>{
  const pins={
    'index.ts':'7ce98ddab759903e6618b40a1c8beb1b621f0691f8f508ad2c07dba2041744e1',
    'orig-tools.ts':'5ad3f44682fc8e2bacb2222157943de8ab7dd663a1e0ddf5e4a213539b1f67d7',
    'edit.ts':'f84d9d242137e1f18ce912188efb7a97b71bf4f255e0a69f16a1fe9d1ff236d4',
    'write.ts':'861de91cc67849e138c32a697c8eb1abab8e4912bfd97811ea1866b8e9af4096',
    'apply_patch.ts':'9bc5a1384a5e1b1cdd21943cb529dcde6e5e596706dee2111ccdb999d6b0ba22',
  },texts={};
  for(const [name,sha] of Object.entries(pins)){const b=fs.readFileSync(path.join(source,name));assert.equal(digest(b),sha);texts[name]=b.toString();}
  assert.match(texts['index.ts'],/"tool.execute.before"\?:\s*\(\s*input: \{ tool: string; sessionID: string; callID: string \}/);
  assert.match(texts['index.ts'],/"tool.execute.after"\?:\s*\(\s*input: \{ tool: string; sessionID: string; callID: string; args: any \}/);
  const tools=texts['orig-tools.ts'];const before=tools.indexOf('"tool.execute.before"'),execute=tools.indexOf('const result = yield* item.execute(args, ctx)'),after=tools.indexOf('"tool.execute.after"');
  assert.ok(before>=0&&execute>before&&after>execute);assert.match(tools.slice(execute,after+120),/\{ tool: item.id, sessionID: ctx.sessionID, callID: ctx.callID, args \}/);
  for(const k of ['filePath','oldString','newString','replaceAll'])assert.ok(texts['edit.ts'].includes(k));
  for(const k of ['filePath','content','exists: exists'])assert.ok(texts['write.ts'].includes(k));
  for(const k of ['patchText','filePath: change.filePath','movePath: change.movePath','patch: change.diff'])assert.ok(texts['apply_patch.ts'].includes(k));
});
