'use strict';
// Real extension HTML factory; VSCode host and runtimeGuard boundary only.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const origin=process.argv[2],out=process.argv[3],data=process.argv[4];
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)||!out||!data)throw Error('explicit fixture argv required');
const events=[],disposable={dispose(){}},vscode={env:{language:'zh-CN'},workspace:{isTrusted:false,getConfiguration:()=>({get:(_key,fallback)=>fallback})},commands:{executeCommand:async()=>{}},Uri:{joinPath:(_base,...parts)=>({toString:()=>origin+'/'+parts.join('/')})}};
const moduleFixture={exports:{}},source=fs.readFileSync(path.join(root,'extension.cjs'),'utf8');
const req=id=>id==='vscode'?vscode:require(require.resolve(id,{paths:[root]}));
vm.runInNewContext('(function(require,module,exports,__dirname){'+source+'\n})',{process:{platform:'linux',env:process.env},Buffer,setTimeout,clearTimeout,console})(req,moduleFixture,moduleFixture.exports,root);
(async()=>{const sidebar=new moduleFixture.exports.Sidebar({globalStorageUri:{fsPath:data},extensionUri:'owned-test-extension',subscriptions:[]});const view={visible:false,webview:{cspSource:origin,asWebviewUri:uri=>uri.toString(),onDidReceiveMessage:()=>disposable,postMessage:m=>events.push(m)},onDidChangeVisibility:()=>disposable,onDidDispose:()=>disposable};await sidebar.resolveWebviewView(view);fs.writeFileSync(out,view.webview.html,{flag:'wx'});sidebar.dispose();})().catch(error=>{console.error(error);process.exitCode=1;});
