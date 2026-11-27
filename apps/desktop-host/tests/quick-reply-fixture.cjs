'use strict';
// Zero-model browser fixture: real controller/HTML, only host transport callbacks are fake.
const fs=require('node:fs'),path=require('node:path'),{EventEmitter}=require('node:events');
const {QuickSurface,quickHtml,CHANNEL}=require('../quick-surface.cjs');
class Window extends EventEmitter{
 constructor(options){super();this.options=options;this.bounds=options;this.webContents=new EventEmitter();this.webContents.mainFrame={url:''};this.webContents.getURL=()=>this.webContents.mainFrame.url;this.webContents.send=()=>{};this.webContents.setWindowOpenHandler=()=>{};}
 loadURL(url){this.webContents.mainFrame.url=url;return Promise.resolve();}isDestroyed(){return false;}setContentProtection(){}getBounds(){return this.bounds;}setBounds(value){this.bounds=value;}show(){}showInactive(){}hide(){}destroy(){}
}
(async()=>{
 const [output,receipt]=process.argv.slice(2);if(!path.isAbsolute(output)||!path.isAbsolute(receipt))throw new Error('Absolute fixture output and saved public receipt required');
 const actual=JSON.parse(fs.readFileSync(receipt,'utf8')).value;
 if(actual.operation_id!=='quick_6536a7bf-b792-4b05-92ca-2b6936fec327'||actual.status!=='RETURNED'||typeof actual.text!=='string')throw new Error('Wrong immutable fixture');
 const selection=actual.review.selection;
 const catalog={schema:'eeas.native-review-capabilities.v1',skills:[{id:'saved-skill',title:'验收检查',selectable:true,config_ref:selection.skill_ref}],
 tools:[{id:'saved-tool',title:'任务验收覆盖',selectable:true,selection:selection.tool_selection}],
 capability_binding_sha256:selection.capability_binding_sha256,basis_ref:selection.basis_ref,permission_hint:{required_effects:['PROCESS_START'],can_submit:true}};
 const screen=new EventEmitter();screen.getCursorScreenPoint=()=>({x:600,y:600});screen.getDisplayNearestPoint=()=>({workArea:{x:0,y:0,width:1400,height:900}});screen.getAllDisplays=()=>[{bounds:{x:0,y:0,width:1400,height:900}}];
 const handlers=new Map(),ipcMain={handle:(name,fn)=>handlers.set(name,fn),removeHandler:()=>{}};
 const surface=new QuickSurface({BrowserWindow:Window,screen,ipcMain,
 onCapture:async()=>({snapshot:{current:true,observation:{status:'OBSERVED',window:{title:'Local fixture'}}}}),
 onDraft:async value=>({id:value.draftId,revision:value.expectedRevision+1,sha256:'a'.repeat(64),status:value.delivery}),
 onSend:async value=>({operation_id:value.operationId,status:'RETURNED',text:actual.text}),onInspect:async()=>actual,
 onReviewCatalog:async()=>catalog,onExpand:async()=>{},onOpenArtifact:async()=>{}});
 await surface.ready;await surface.openAsk();const own=surface.windows.quickPopover;
 const send=payload=>handlers.get(CHANNEL)({sender:own.webContents,senderFrame:own.webContents.mainFrame},payload);
 await send({kind:'command',epoch:surface.epoch,command:'review-catalog'});
 await send({kind:'review-selection',epoch:surface.epoch,skillId:'saved-skill',toolId:'saved-tool'});
 await send({kind:'review-consent',epoch:surface.epoch,enabled:true});
 await send({kind:'edit',epoch:surface.epoch,comment:'本地重放已保存回复，不发送模型',marks:[]});
 const editing=surface.get();await send({kind:'command',epoch:surface.epoch,command:'send'});await surface.openAsk();
 const completed=surface.get();if(completed.draft?.phase!=='COMPLETED'||completed.result?.text!==actual.text)throw new Error('Fixture did not restore complete reply: '+completed.error);fs.mkdirSync(output,{recursive:true});
 for(const locale of ['zh-CN','en'])fs.writeFileSync(path.join(output,locale+'.html'),quickHtml('popover',locale));
 fs.writeFileSync(path.join(output,'states.json'),JSON.stringify({editing,completed}));surface.dispose();
 console.log(JSON.stringify({output,reply_characters:[...actual.text].length,model_calls:0}));
})().catch(error=>{console.error(error);process.exitCode=1;});
