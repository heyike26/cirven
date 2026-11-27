import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import {presentationFor} from '../src/interaction-policy.ts';
const code=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8'),source=ts.createSourceFile('App.tsx',code,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const matches=[];
function visit(node){if(ts.isJsxElement(node)&&node.children.some(child=>ts.isJsxExpression(child)&&child.expression&&ts.isCallExpression(child.expression)&&child.expression.expression.getText(source)==='uiText'&&child.expression.arguments[0]?.text==='查看要求'))matches.push(node);ts.forEachChild(node,visit);}
visit(source);assert.equal(matches.length,1);
const attr=matches[0].openingElement.attributes.properties.find(p=>p.name?.getText(source)==='onClick');assert.ok(attr?.initializer?.expression);
const callback=ts.transpileModule('const openCurrent = '+attr.initializer.expression.getText(source)+';', {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
test('actual current conversation View requirements callback is an explicit open of the exact existing version',()=>{
 const value={id:'same-session',project_id:'same-project',revision:4,sha256:'a'.repeat(64)},calls=[];
 const envelope=(type,id,data,revision,contentHash)=>({type,artifactId:id,data,revision,contentHash,scope:{projectId:value.project_id}});
 const inspect=(artifact,cause='EXPLICIT_OPEN')=>calls.push({artifact,cause,presentation:presentationFor(artifact,cause)});
 const action=new Function('activeSession','envelope','inspect',callback+'return openCurrent;')(()=>value,envelope,inspect);action();
 assert.equal(calls.length,1);assert.equal(calls[0].artifact.data,value);assert.equal(calls[0].artifact.contentHash,value.sha256);assert.equal(calls[0].cause,'EXPLICIT_OPEN');assert.equal(calls[0].presentation.mode,'FULL_WORKSPACE');
});
test('automatic ordinary results still remain in chat and never request the workspace',()=>{
 const a={type:'discovery',artifactId:'same-session',revision:4,contentHash:'a'.repeat(64),scope:{projectId:'same-project'}};assert.equal(presentationFor(a,'TOOL_RESULT').destination,'CHAT');
});
