'use strict';
// Targeted browser host: actual Sidebar/controller/BridgeClient, real loopback
// HTTP; the existing suite supplies only VS Code/Windows metadata boundaries
// and an external bridge fixture. Never connects to an installed App.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),Module=require('node:module');
const out=process.argv[2];if(!out||!path.isAbsolute(out))throw Error('Explicit owned fixture output required');
const filename=path.join(__dirname,'view.test.cjs'),fixture=new Module(filename,module),req=Module.createRequire(filename);
fixture.filename=filename;fixture.paths=Module._nodeModulePaths(__dirname);
fixture.require=id=>id==='node:test'?()=>{}:req(id);
fixture._compile(fs.readFileSync(filename,'utf8')+'\nmodule.exports={readonlyRecoveryScene};',filename);
(async()=>{
 const cleanup=[],scene=await fixture.exports.readonlyRecoveryScene({after:fn=>cleanup.push(fn)}),sidebar=scene.sidebar;
 let scheduledResolve;const publicAudit=()=>({calls:scene.calls,secret_reads:scene.secretReads,discovery_reads:scene.discoveryReads,connection:sidebar.connection,draft:sidebar.controller.snapshot().draft,selected:sidebar.controller.selected,requests:sidebar.state.value.requests,old_closed:scene.old.closed,created_clients:scene.created.length});
 sidebar.view.webview.postMessage=value=>{scene.posted.push(value);if(value.snapshot?.error==='ECONNRESET')scheduledResolve?.();};
 const server=http.createServer(async(request,response)=>{try{
  if(request.method==='GET'&&request.url==='/audit'){response.setHeader('content-type','application/json');response.end(JSON.stringify(publicAudit()));return;}
  if(request.method==='POST'&&['/message','/schedule'].includes(request.url)){
   let raw='';for await(const chunk of request){raw+=chunk;if(raw.length>65536)throw Error('Bounded fixture message required');}
   const start=scene.posted.length;
   if(request.url==='/message')await sidebar.handle(JSON.parse(raw));
   else{const failed=new Promise(resolve=>{scheduledResolve=resolve;});sidebar.visible=true;sidebar.schedule();await failed;sidebar.visible=false;clearTimeout(sidebar.timer);}
   response.setHeader('content-type','application/json');response.end(JSON.stringify({messages:scene.posted.slice(start),audit:publicAudit()}));return;
  }
  const file={'/':path.join(out,'webview.html'),'/media/view.js':path.join(__dirname,'../media/view.js'),'/media/view.css':path.join(__dirname,'../media/view.css'),'/media/vendor/marked.js':path.join(__dirname,'../media/vendor/marked.js')}[request.url];
  if(request.method!=='GET'||!file){response.writeHead(404);response.end();return;}
  response.setHeader('Content-Type',request.url==='/'?'text/html; charset=utf-8':request.url.endsWith('.css')?'text/css':'text/javascript');response.end(fs.readFileSync(file));
 }catch(error){response.writeHead(500);response.end(String(error.stack));}});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));console.log(JSON.stringify({origin:'http://127.0.0.1:'+server.address().port}));
 process.stdin.resume();process.stdin.once('data',async()=>{sidebar.visible=false;clearTimeout(sidebar.timer);for(const fn of cleanup)await fn();await new Promise(resolve=>server.close(resolve));process.stdin.pause();});
})().catch(error=>{console.error(error);process.exitCode=1;});
