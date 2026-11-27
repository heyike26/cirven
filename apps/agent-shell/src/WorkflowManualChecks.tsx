import {createEffect,createSignal,createUniqueId,For,onCleanup,Show} from 'solid-js';
import type {BenchContext,BenchDraft,BenchFirmware,ManualCheck,Row} from './stage-workflows';
import WorkflowTestBench,{TestBenchDetails} from './WorkflowTestBench';
import WorkflowManualFiles,{ObservationFiles} from './WorkflowManualFiles';
import {encodeManualFile,manualDraftFiles,mergeManualFiles,prepareManualFiles,type ManualDraftFile,type ManualFileUpload} from './workflow-manual-files';

export type ManualDraft={status:''|ManualCheck['status'];environment:string;observation:string;bench?:BenchDraft;pendingRevision?:number};
export type ManualAction={key:string;caseId:string;title:string;id:string;instruction:string;expected:string};
const emptyDraft:ManualDraft={status:'',environment:'',observation:''};
const emptyBench:BenchDraft={devices:[]};
export function benchPayload(bench:BenchDraft|undefined):BenchDraft|undefined{
 if(!bench)return;
 const host=bench.host?.trim(),fixture=bench.fixture?.trim();
 const devices=bench.devices.map(({label,identity,link,firmware_label,firmware_id})=>({
  label:label.trim(),identity:identity?.trim()||undefined,link:link?.trim()||undefined,
  firmware_label:firmware_label?.trim()||undefined,firmware_id
 }));
 if(devices.length||host||fixture)return {devices,host:host||undefined,fixture:fixture||undefined};
}
export function manualActions(rows:Row[]):ManualAction[]{
 return rows.flatMap(row=>(Array.isArray(row.actions)?row.actions:[]).flatMap(action=>
  action?.method==='manual_observation'&&typeof action.id==='string'&&typeof action.instruction==='string'&&typeof action.expected==='string'?[{key:JSON.stringify([row.id,action.id]),caseId:row.id,
   title:row.title,id:action.id,instruction:action.instruction,expected:action.expected}]:[]));
}

export default function WorkflowManualChecks(p:{
 actions:ManualAction[];records:ManualCheck[];drafts:Record<string,ManualDraft>;readOnly:boolean;busy:boolean;
 loadBench:(action:ManualAction,signal:AbortSignal)=>Promise<BenchContext>;fileHref:(id:string)=>string;retest:boolean;
 draftScope:string;attachmentHref:(receipt:string,id:string)=>string;
 onDraft:(key:string,draft:ManualDraft)=>void;
 onSave:(action:ManualAction,draft:ManualDraft,attachments:ManualFileUpload[])=>Promise<boolean>;
 t:(zh:string,en:string)=>string
}){
 const [selected,setSelected]=createSignal(p.actions[0]?.key??'');
 const [benchOpen,setBenchOpen]=createSignal(false);
 const [filesOpen,setFilesOpen]=createSignal(false),[files,setFiles]=createSignal<ManualDraftFile[]>([]);
 const [filesState,setFilesState]=createSignal<'loading'|'ready'|'failed'>('loading');
 const [fileNotice,setFileNotice]=createSignal(''),[saving,setSaving]=createSignal(false);
 const [firmwares,setFirmwares]=createSignal<BenchFirmware[]>([]),[firmwareStatus,setFirmwareStatus]=createSignal<'idle'|'loading'|'failed'>('idle');
 const [previous,setPrevious]=createSignal<{key:string;bench:NonNullable<BenchContext['previous_bench']>}>();
 let firmwareRead:AbortController|undefined;
 let fileRequest=0,disposed=false;
 onCleanup(()=>{firmwareRead?.abort();disposed=true;});
 const formId=createUniqueId();
 const choice=()=>p.actions.find(action=>action.key===selected());
 const draft=()=>p.drafts[selected()]??emptyDraft;
 const history=()=>p.records.filter(record=>record.case_id===choice()?.caseId&&record.action_id===choice()?.id);
 const bench=()=>draft().bench??emptyBench;
 const ownBench=()=>history().slice().reverse().find(record=>record.bench)?.bench;
 const latestBench=()=>p.records.slice().reverse().find(record=>record.bench)?.bench;
 const retake=()=>previous()?.key===selected()&&!ownBench()?previous()?.bench:undefined;
 const benchReady=()=>bench().devices.every(device=>device.label.trim()&&
  (!device.firmware_id||firmwares().some(file=>file.id===device.firmware_id)));
 const benchStatus=()=>!benchReady()?p.t('请补全设备信息或重新选择固件','Complete device details or reselect firmware'):
  benchPayload(bench())?p.t('已填写工位','Bench recorded in draft'):p.t('选填','Optional');
 const label=(status:ManualCheck['status'])=>status==='passed'?p.t('通过','Passed'):status==='failed'?p.t('失败','Failed'):p.t('受阻','Blocked');
 function edit(patch:Partial<ManualDraft>){p.onDraft(selected(),{...draft(),...patch,pendingRevision:undefined});}
 const scopeFor=(action:string)=>JSON.stringify([p.draftScope,action]);
 const filesSummary=()=>filesState()==='failed'?p.t('文件草稿待核对','Check file draft'):
  files().length?p.t(`已选 ${files().length} 个文件`,`${files().length} selected`):p.t('选填','Optional');
 async function readFiles(update?:(current:ManualDraftFile[])=>ManualDraftFile[],picked?:File[]){
  const action=selected(),request=++fileRequest;
  const current=()=>!disposed&&selected()===action&&request===fileRequest;
  setFilesState('loading');setFileNotice('');
  try{
   const prepared=picked?await prepareManualFiles(files(),picked):undefined;
   const value=await manualDraftFiles(scopeFor(action),prepared?current=>mergeManualFiles(current,prepared):update);
   if(current()){
    setFiles(previous=>value.map(item=>previous.find(old=>old.id===item.id)??item));
    setFilesState('ready');
   }
  }catch(error){
   if(!current())return;
   if(error instanceof Error&&['limit','invalid'].includes(error.message)){
    setFilesState('ready');
    setFileNotice(error.message==='limit'?p.t('最多 3 个文件，合计 1 MiB。','Up to 3 files, 1 MiB total.'):
     p.t('请选择非空文件，文件名不得包含路径。','Choose nonempty files with plain filenames.'));
   }else{
    setFilesState('failed');
    setFileNotice(p.t('本机文件草稿无法读写，请重试。','Could not access the local file draft. Try again.'));
   }
  }
 }
 createEffect(()=>{
  if(choice()&&!p.readOnly)void readFiles();
 });
 async function submit(){
  const action=choice(),value=draft();
  if(!action||!value.status||!value.environment.trim()||!value.observation.trim()||!benchReady()||
   p.busy||saving()||filesState()!=='ready')return;
  const scope=scopeFor(action.key),snapshot=files();
  setSaving(true);
  try{
   const attachments=await Promise.all(snapshot.map(encodeManualFile));
   if(disposed||choice()?.key!==action.key||draft()!==value||p.readOnly||p.busy)return;
   if(!await p.onSave(action,value,attachments)||!snapshot.length)return;
   const savedIds=new Set(snapshot.map(item=>item.id));
   if(!disposed&&choice()?.key===action.key)await readFiles(current=>current.filter(item=>!savedIds.has(item.id)));
   else await manualDraftFiles(scope,current=>current.filter(item=>!savedIds.has(item.id)));
  }catch{
   if(!disposed){
    setFileNotice(p.t('文件草稿未能处理，请打开实测证据重试。','Could not process the draft files. Open observation evidence to retry.'));
    setFilesState('failed');
   }
  }finally{if(!disposed)setSaving(false);}
 }
 function reuseBench(){
  if(benchPayload(bench()))return;
  const source=retake();
  if(source){edit({bench:structuredClone(source.setup)});return;}
  const recorded=ownBench()??latestBench();
  if(!recorded)return;
  const devices=recorded.devices.map(({firmware,...device})=>({...device,firmware_id:firmware?.id}));
  edit({bench:{host:recorded.host,fixture:recorded.fixture,devices}});
 }
 async function loadFirmwares(){
  const action=choice();
  if(!action)return;
  firmwareRead?.abort();
  const request=firmwareRead=new AbortController();
  setFirmwareStatus('loading');
  setPrevious(undefined);
  try{
   const value=await p.loadBench(action,request.signal);
   if(!request.signal.aborted&&selected()===action.key){
    setFirmwares(value.files);
    setPrevious(value.previous_bench?{key:action.key,bench:value.previous_bench}:undefined);
    setFirmwareStatus('idle');
   }
  }catch{if(!request.signal.aborted)setFirmwareStatus('failed');}
 }
 createEffect(()=>{
  if(!firmwareRead&&!p.readOnly&&bench().devices.some(device=>device.firmware_id))void loadFirmwares();
 });
 function changeAction(key:string){
  firmwareRead?.abort();firmwareRead=undefined;
  setPrevious(undefined);setFirmwareStatus('idle');setFiles([]);setSelected(key);
 }
 return <div class="wf-manual"><div class="wf-stage-panel wf-manual-scroll">
  <Show when={p.actions.length}><label>{p.t('已审核的实测动作','Approved manual check')}
   <select aria-label={p.t('已审核的实测动作','Approved manual check')} value={selected()} disabled={saving()} onChange={e=>changeAction(e.currentTarget.value)}>
    <For each={p.actions}>{entry=><option value={entry.key}>{entry.caseId} / {entry.id} · {entry.title}</option>}</For>
   </select></label>
  </Show>
  <Show when={choice()}>{entry=><dl class="wf-manual-criteria">
   <div><dt>{p.t('操作','Procedure')}</dt><dd>{entry().instruction}</dd></div>
   <div><dt>{p.t('预期','Expected')}</dt><dd>{entry().expected}</dd></div>
  </dl>}</Show>
  <Show when={choice()&&!p.readOnly}><form id={formId} class="wf-stage-panel" onSubmit={event=>{event.preventDefault();void submit();}}>
   <label>{p.t('结果','Outcome')}<select aria-label={p.t('结果','Outcome')} required value={draft().status} onChange={e=>edit({status:e.currentTarget.value as ManualDraft['status']})}>
    <option value="">{p.t('请选择','Select an outcome')}</option>
    <For each={['passed','failed','blocked'] as const}>{status=><option value={status}>{label(status)}</option>}</For>
   </select></label>
   <label>{p.t('实测环境','Environment')}<input required maxLength={500} value={draft().environment} onInput={e=>edit({environment:e.currentTarget.value})}/></label>
   <div class="wf-bench-entry"><button type="button" aria-haspopup="dialog" onClick={()=>{setBenchOpen(true);void loadFirmwares();}}>
    {p.t('工位与固件','Bench and firmware')}
   </button><small title={benchStatus()}>{benchStatus()}</small></div>
   <WorkflowTestBench open={benchOpen()} t={p.t} draft={bench()} firmwares={firmwares()} latestAvailable={!!latestBench()}
    retest={p.retest} previous={retake()} canReuse={!benchPayload(bench())} firmwareStatus={firmwareStatus()} onRetry={()=>void loadFirmwares()}
    onDraft={value=>edit({bench:value})} onReuse={reuseBench} onClose={()=>{firmwareRead?.abort();setBenchOpen(false);}}/>
   <label>{p.t('实测观察','Actual observation')}<textarea required rows="3" maxLength={4000} value={draft().observation} onInput={e=>edit({observation:e.currentTarget.value})}/></label>
   <div class="wf-bench-entry"><button type="button" aria-haspopup="dialog" disabled={saving()||p.busy}
    onClick={()=>setFilesOpen(true)}>{p.t('实测证据','Observation evidence')}</button>
    <small title={filesSummary()}>{filesSummary()}</small></div>
   <WorkflowManualFiles open={filesOpen()} files={files()} notice={fileNotice()} busy={filesState()==='loading'||saving()}
    unavailable={filesState()==='failed'} t={p.t} onClose={()=>setFilesOpen(false)} onRetry={()=>void readFiles()}
    onChoose={picked=>void readFiles(undefined,picked)}
    onRemove={id=>void readFiles(current=>current.filter(item=>item.id!==id))}/>
  </form></Show>
  <Show when={history().at(-1)}>{record=><section class="wf-manual-receipt">
   <p role="status"><strong>{p.t('人工实测','Manual observation')} · {label(record().status)}</strong> <time dateTime={record().at}>{new Date(record().at).toLocaleString()}</time></p>
   <p>{record().environment}</p><p>{record().observation}</p>
   <Show when={record().bench}>{value=><TestBenchDetails bench={value()} t={p.t} fileHref={p.fileHref}/>}</Show>
   <ObservationFiles files={record().attachments} href={id=>p.attachmentHref(record().id,id)} t={p.t}/>
   <Show when={history().length>1}><details><summary>{p.t('较早记录','Previous observations')} · {history().length-1}</summary>
    <ul><For each={history().slice(0,-1).reverse()}>{prior=><li><strong>{label(prior.status)}</strong> · <time dateTime={prior.at}>{new Date(prior.at).toLocaleString()}</time>
     <p>{prior.environment}</p><p>{prior.observation}</p><Show when={prior.bench}>{value=><TestBenchDetails bench={value()} t={p.t} fileHref={p.fileHref}/>}</Show>
     <ObservationFiles files={prior.attachments} href={id=>p.attachmentHref(prior.id,id)} t={p.t}/>
    </li>}</For></ul>
   </details></Show>
  </section>}</Show>
 </div><Show when={choice()&&!p.readOnly}><footer class="wf-manual-actions">
  <button type="submit" form={formId} class="wf-primary" disabled={p.busy||saving()||filesState()!=='ready'||!draft().status||!draft().environment.trim()||!draft().observation.trim()||!benchReady()}>{p.t('保存实测记录','Save observation')}</button>
 </footer></Show></div>;
}
