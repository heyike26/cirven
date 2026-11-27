'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net');
const {controllerPort,savedPort}=require('../controller-port.cjs');
function directory(t){const value=fs.mkdtempSync(path.join(os.tmpdir(),'eeas-owned-port-'));t.after(()=>fs.rmSync(value,{recursive:true,force:true}));return value;}
test('actual loopback port survives restart without rewriting its origin record',async t=>{
  const root=directory(t),first=await controllerPort(root),file=path.join(root,'controller-port.json'),before=fs.readFileSync(file);
  assert.ok(first>=1024&&first<=65535);assert.equal(await controllerPort(root),first);assert.deepEqual(fs.readFileSync(file),before);
});
test('an actual occupied saved port refuses a different origin and makes no request to its server',async t=>{
  const root=directory(t),port=await controllerPort(root),file=path.join(root,'controller-port.json'),before=fs.readFileSync(file);let connections=0;
  const server=net.createServer(()=>connections++);await new Promise(r=>server.listen(port,'127.0.0.1',r));
  try {await assert.rejects(controllerPort(root),/正在被占用/);assert.equal(connections,0);assert.deepEqual(fs.readFileSync(file),before);}
  finally {await new Promise(r=>server.close(r));}
});
test('malformed or linked local port records cannot become an arbitrary endpoint',async t=>{
  const root=directory(t),file=path.join(root,'controller-port.json');
  for(const value of [{schema:'eeas.controller-port.v1',port:true},{schema:'eeas.controller-port.v1',port:80},{schema:'eeas.controller-port.v1',port:65536},{schema:'eeas.controller-port.v1',port:1234,url:'http://alien.invalid'}]){
    fs.writeFileSync(file,JSON.stringify(value));assert.throws(()=>savedPort(file),/记录无效/);
  }
  fs.unlinkSync(file);const other=path.join(root,'other.json');fs.writeFileSync(other,JSON.stringify({schema:'eeas.controller-port.v1',port:1234}));fs.symlinkSync(other,file);assert.throws(()=>savedPort(file),/记录无效/);
});
