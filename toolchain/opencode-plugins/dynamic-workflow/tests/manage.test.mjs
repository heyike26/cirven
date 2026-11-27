import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {configure,verifyBundle} from '../manage.mjs';
import {digest} from '../protocol.mjs';

test('offline portable copy, activate, upgrade and uninstall preserve unrelated config and owner history; stale/drift cannot overwrite',async()=>{
  const source=fileURLToPath(new URL('..',import.meta.url)),testRoot=process.env.EEAS_WORKFLOW_TEST_ROOT??os.tmpdir();await fs.mkdir(testRoot,{recursive:true});
  const base=await fs.mkdtemp(path.join(testRoot,'workflow-activation-'));
  const manifest=JSON.parse(await fs.readFile(path.join(source,'manifest.json'),'utf8'));
  const sha=digest(await fs.readFile(path.join(source,'manifest.json'),'utf8'));
  const old=path.join(base,'installed-previous'),next=path.join(base,'installed-upgrade');
  for(const directory of [old,next]){await fs.mkdir(directory);for(const name of ['manifest.json',...manifest.files.map(row=>row.path)]){await fs.mkdir(path.dirname(path.join(directory,name)),{recursive:true});await fs.copyFile(path.join(source,name),path.join(directory,name));}}
  await verifyBundle(old,sha);
  const configPath=path.join(base,'opencode.json'),privatePath=path.join(base,'plugin-config.json'),history=path.join(base,'user-history.json');
  await fs.writeFile(privatePath,'{}');await fs.writeFile(history,'USER_HISTORY_MUST_SURVIVE');
  const original={plugin:['some-original-plugin'],agent:{build:{model:'original/model'}},permission:{'*':'ask'}};
  await fs.writeFile(configPath,JSON.stringify(original));
  const argumentsFor=async bundleRoot=>({configPath,expectedConfigSha256:digest(await fs.readFile(configPath,'utf8')),bundleRoot,manifestSha256:sha,pluginConfigPath:privatePath,pluginConfigSha256:digest('{}')});
  const first=await configure(await argumentsFor(old));assert.equal(first.changed,true);
  assert.deepEqual(JSON.parse(await fs.readFile(configPath,'utf8')).plugin[0],original.plugin[0]);
  const stable=await configure(await argumentsFor(old));assert.equal(stable.changed,false);
  await assert.rejects(configure({...await argumentsFor(old),expectedConfigSha256:'a'.repeat(64)}),/CAS/);
  const upgraded=await configure({...await argumentsFor(next),replaceEntry:first.entry});assert.equal(upgraded.changed,true);
  let actual=JSON.parse(await fs.readFile(configPath,'utf8'));assert.equal(actual.plugin.length,2);assert.equal(actual.plugin[1][0],pathToFileURL(path.join(next,'index.mjs')).href);assert.deepEqual(actual.agent,original.agent);assert.deepEqual(actual.permission,original.permission);
  const before=await fs.readFile(configPath,'utf8');await fs.appendFile(path.join(next,'index.mjs'),'\n// unexpected edit');
  await assert.rejects(configure({...await argumentsFor(next),disable:true}),/SOURCE_DRIFT/);assert.equal(await fs.readFile(configPath,'utf8'),before);
  await fs.copyFile(path.join(source,'index.mjs'),path.join(next,'index.mjs'));
  await configure({...await argumentsFor(next),disable:true});actual=JSON.parse(await fs.readFile(configPath,'utf8'));assert.deepEqual(actual,original);assert.equal(await fs.readFile(history,'utf8'),'USER_HISTORY_MUST_SURVIVE');
  const originalVendor=path.join(next,'vendor/zod'),movedVendor=path.join(base,'same-bytes-outside-bundle');
  await fs.rename(originalVendor,movedVendor);await fs.symlink(movedVendor,originalVendor,'dir');
  await assert.rejects(verifyBundle(next,sha),/SYMLINK_REJECTED/,'recursive vendor closure must not escape via a same-byte directory link');
});
