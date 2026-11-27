import {batch,createEffect,createMemo,createSignal,For,onCleanup,onMount,Show,untrack} from 'solid-js';
import {ApiError,operationId} from './api';
import {blankNode,draftKey,emptyCatalog,selectStage,stageApi,stageModeKey,type Catalog,type CoverageOverviewRow,type CoverageQuery,type CoverageRecord,type CoverageSelector,type CoverageTarget,type Definition,type DefinitionImprovementSource,type DialogueReference,type FollowUpType,type Kernel,type PendingTransition,type RunStage,type Scope,type StageDestination,type StageIndex,type StageKind,type StageRun,type TransitionKind,type TransitionPlan} from './stage-workflows';
import {clearTransitionRetryKey,matchesTransition,readPendingTransition,settleStageDraft,settleTransitionDraft} from './workflow-transition';
import StageDefinitionEditor from './StageDefinitionEditor';
import WorkflowStagePage from './WorkflowStagePage';
import WorkflowKernelSettings from './WorkflowKernelSettings';
import WorkflowDialog from './WorkflowDialog';
import WorkflowDefinitionHistory from './WorkflowDefinitionHistory';
import WorkflowHistoricalDraft from './WorkflowHistoricalDraft';
import WorkflowCoverage,{type CoverageReading} from './WorkflowCoverage';
import WorkflowCoverageOverview,{emptyOverviewReading,type OverviewReading} from './WorkflowCoverageOverview';
import WorkflowNavigation from './WorkflowNavigation';
import {WorkflowIcon,stageGlyph} from './WorkflowIcon';
import {WorkflowReading,artifactReadingOperation,captureWorkflowReading,restoreWorkflowReading} from './workflow-reading';
import './stage-workflow.css';

export type WorkflowArrival={run:string;node:string;conversation:string;previous:string;kind:TransitionKind|'source'};
export type WorkflowViewContext={title:string;readonly:boolean};
type SavedStageLink=WorkflowArrival&{kind:TransitionKind;targetName:string;operation?:string;historical?:boolean};
type DefinitionSave={id:string;conversation:string;edit:string;value:Definition};
type DefinitionDraft={value:Definition;edit:string;pending?:DefinitionSave;conflict?:boolean};
type CoverageOverviewView={run:string;page:string;reading:OverviewReading};
type CoverageEntry={
 query:CoverageQuery;top:number;width:number;reader?:CoverageRecord;reading:CoverageReading;
 overview?:CoverageOverviewView;
 artifact?:{row:string;indexOpen?:boolean;search?:string;sourceScroll?:number};
 followUp?:{id:string;asking:boolean;scroll:number;executionOpen:boolean};
};

function readStageLink(key:string):SavedStageLink|undefined{
 try{
  const raw=localStorage.getItem(key);
  if(!raw||raw.length>4096)return;
  const value=JSON.parse(raw) as Record<string,unknown>;
  if(!value||typeof value.kind!=='string'||!['approve','rework','revise','start'].includes(value.kind))return;
  if(!['run','node','conversation','previous','targetName'].every(field=>typeof value[field]==='string'&&String(value[field]).length>0))return;
  if(value.historical!==undefined&&typeof value.historical!=='boolean')return;
  return value as SavedStageLink;
 }catch{/* The authoritative stage is still available from the project directory. */}
}

export default function WorkflowStudio(p:{arrival?:WorkflowArrival;onArrival:(a:WorkflowArrival|undefined)=>void;onArrivalShown:(a:WorkflowArrival)=>void;onViewContext:(scope:Scope,view:WorkflowViewContext|undefined)=>void;scope:Scope;locale:string;archived:boolean;initial?:StageIndex;capabilitiesOpen:boolean;onManageCapabilities:()=>void;onDirectory:(run:StageRun)=>void;onChild:(id:string,destination?:StageDestination)=>Promise<void>}){
 const t=(zh:string,en:string)=>p.locale==='en'?en:zh;
 const [index,setIndex]=createSignal<StageIndex>(),[catalog,setCatalog]=createSignal<Catalog>(emptyCatalog()),[catalogError,setCatalogError]=createSignal('');
 const [definition,setDefinition]=createSignal('default'),[runId,setRunId]=createSignal(''),[page,setPage]=createSignal('');
 const [editor,setEditor]=createSignal<DefinitionDraft>(),[editorNotice,setEditorNotice]=createSignal('');
 const [choosing,setChoosing]=createSignal(false),[settings,setSettings]=createSignal(false);
 const [settingsVisited,setSettingsVisited]=createSignal(false);
 const [engineRequest,setEngineRequest]=createSignal<{engine:Kernel}>();
 function openSettings(engine?:Kernel){
  const requested=engine??(!settingsVisited()&&!inLibrary()?stage()?.config.kernel:undefined);
  setEngineRequest(requested?{engine:requested}:undefined);setSettingsVisited(true);setSettings(true);
 }
 const [busy,setBusy]=createSignal(''),[error,setError]=createSignal(''),[loading,setLoading]=createSignal(true);
 const [sending,setSending]=createSignal(false);
 const [asking,setAsking]=createSignal(false);
 const [definitionHistory,setDefinitionHistory]=createSignal<Definition>();
 const [coverage,setCoverage]=createSignal<CoverageQuery>();
 const [coverageOverview,setCoverageOverview]=createSignal<CoverageOverviewView>();
 const [coverageReading,setCoverageReading]=createSignal<CoverageReading>();
 const [coverageReturn,setCoverageReturn]=createSignal<CoverageEntry>();
 const [coverageError,setCoverageError]=createSignal('');
 const [coverageReader,setCoverageReader]=createSignal<CoverageRecord>();
 let coverageRequest:AbortController|undefined,coverageOpener:HTMLButtonElement|undefined;
 let overviewTrigger:HTMLButtonElement|undefined,overviewBack:HTMLButtonElement|undefined;
 const scope=p.scope,control=new AbortController(),chosenKey=stageModeKey(scope)+':run',editKey=stageModeKey(scope)+':definition-draft';
 const linkKey=stageModeKey(scope)+':open-saved-stage';
 const pendingKey=stageModeKey(scope)+':pending-transition';
 const [pendingTransition,setPendingTransition]=createSignal(readPendingTransition(pendingKey));
 const [checkingTransition,setCheckingTransition]=createSignal(false),[missingTransition,setMissingTransition]=createSignal(false);
 const [transitionError,setTransitionError]=createSignal(''),[transitionNotice,setTransitionNotice]=createSignal('');
 const [savedStage,setSavedStage]=createSignal(readStageLink(linkKey));
 const [showSavedStage,setShowSavedStage]=createSignal(!!savedStage());
 const [openingSavedStage,setOpeningSavedStage]=createSignal(false);
 const run=()=>index()?.runs.find(x=>x.id===runId());
 const sourceWasReplaced=()=>savedStage()?.run===runId()&&(savedStage()?.kind==='rework'||savedStage()?.kind==='revise');
 const canOpenSavedStage=()=>!!index()&&!loading()&&!openingSavedStage()&&!busy()&&!sending()&&!checkingTransition();
 const awaitingReceipt=()=>!!pendingTransition()&&!busy()&&!sending()&&!savedStage();
 const selected=()=>index()?.definitions.find(x=>x.id===definition());
 const node=()=>run()?.definition.nodes.find(x=>x.id===page());
 const stage=()=>run()?.stages.find(x=>x.node_id===page());
 const historyStage=()=>coverageReader()?.stage??index()?.historical_stage;
 const displayedNode=()=>historyStage()?run()?.definition.nodes.find(n=>n.id===historyStage()!.node_id):node();
 const historyInputs=()=>coverageReader()?.inputs??index()?.historical_inputs??[];
 const owner=()=>({...scope,conversation_id:index()?.owner_conversation_id??scope.conversation_id});
 let root:HTMLElement|undefined,viewChange=0;
 const reading=new WorkflowReading(localStorage);
 let readingFrame:number|undefined,readingReady:string|undefined;
 const errorText=(e:unknown)=>e instanceof Error?e.message:String(e);
 const current=()=>!control.signal.aborted;
 const rejected=(e:unknown)=>e instanceof ApiError&&e.status>=400&&e.status<500&&e.status!==408;
 function clearPending(operation:string){
  const request=pendingTransition();
  if(request?.operation_id!==operation)return;
  try{if(readPendingTransition(pendingKey)?.operation_id===operation)localStorage.removeItem(pendingKey);}catch{/* Receipt lookup remains safe after restart. */}
  clearTransitionRetryKey(scope.project_id,request);
  if(current())batch(()=>{setPendingTransition(undefined);setMissingTransition(false);setTransitionError('');});
 }
 async function sendTransition(operation_id:string,request_conversation_id:string,plan:TransitionPlan,legacy:boolean,draftEdit?:string){
  const request:PendingTransition={operation_id,request_conversation_id,plan,draft_edit:legacy?undefined:draftEdit};
  const source=owner();
  localStorage.setItem(pendingKey,JSON.stringify(request));
  batch(()=>{setPendingTransition(request);setTransitionNotice('');setMissingTransition(false);setTransitionError('');});
  if(legacy)throw Error(t('已有原操作等待核查。','An earlier request is waiting to be checked.'));
  try{
   const result=await stageApi.submitTransition(scope.project_id,request);
   if(plan.kind!=='start'&&result.id===plan.run_id&&result.project_id===source.project_id&&result.conversation_id===source.conversation_id)
    settleTransitionDraft(source,request);
   return result;
  }
  catch(e){if(rejected(e))clearPending(operation_id);throw e;}
 }
 async function checkTransition(retry=false){
  const request=pendingTransition();
  if(!request||!canOpenSavedStage()||retry&&(!missingTransition()||p.archived))return;
  const restoreFocus=root?.querySelector('.wf-transition-recovery')?.contains(document.activeElement);
  const ref={project_id:scope.project_id,conversation_id:request.request_conversation_id};
  let submission:unknown,replied=false;
  setCheckingTransition(true);setTransitionError('');
  try{
   if(retry){
    try{await stageApi.submitTransition(scope.project_id,request);replied=true;}
    catch(e){submission=e;}
   }
   const result=await stageApi.transitionReceipt(ref,request.operation_id,control.signal);
   if(!current()||pendingTransition()!==request)return;
   if(!matchesTransition(request,result,scope.project_id))throw Error(t('回执与原操作不一致。','The receipt does not match the original request.'));
   settleTransitionDraft(owner(),request);
   if(result.target&&(result.target_current||result.target_historical)){
    rememberSavedStage({run:result.run_id,node:result.target.node_id,conversation:result.target.stage_session_id,
     targetName:result.target.stage_name,previous:result.previous,kind:result.kind,operation:result.operation_id,
     historical:result.target_historical},true);
   }else{
    await reload();
    if(!current())return;
    setTransitionNotice(result.target_current
     ?t('审核已保存，工作流已完成。','Approval saved. The workflow is complete.')
     :result.target?t('原操作已保存，原阶段暂不可查看。','The original request was saved. Its destination is unavailable.')
      :t('原审核已保存，工作流随后已有更新。','The original approval was saved. The workflow has since changed.'));
   }
   clearPending(request.operation_id);setError('');
   if(restoreFocus)queueMicrotask(()=>root?.querySelector<HTMLElement>('[data-transition-next]')?.focus({preventScroll:true}));
  }catch(e){
   if(!current()||pendingTransition()!==request)return;
   if(e instanceof ApiError&&e.status===404){
    if(replied||rejected(submission)){
     clearPending(request.operation_id);
     await reload();
     if(current())setTransitionNotice(replied
      ?t('收到原请求响应。可在会话目录核对工作流记录。','The original request responded. Check the workflow in the conversation list.')
      :errorText(submission));
    }else setMissingTransition(true);
   }else setTransitionError(errorText(e));
  }finally{if(current())setCheckingTransition(false);}
 }
 function clearSavedStage(link:SavedStageLink){
  try{if(localStorage.getItem(linkKey)===JSON.stringify(link))localStorage.removeItem(linkKey);}catch{/* The project directory still provides the original route. */}
  if(link.operation)clearPending(link.operation);
  if(current()&&savedStage()===link){setSavedStage(undefined);setShowSavedStage(false);}
 }
 async function openSavedStage(link:SavedStageLink){
  if(openingSavedStage()||!current())return false;
  setOpeningSavedStage(true);
  try{
   p.onArrival(link.historical?{...link,kind:'source'}:link);
   await p.onChild(link.conversation,{project:scope.project_id,owner:owner().conversation_id,run:link.run,
    node:link.node,historical:link.historical});
   clearSavedStage(link);
   return true;
  }catch{
   if(current()&&savedStage()===link){p.onArrival(undefined);setShowSavedStage(true);}
   return false;
  }finally{if(current())setOpeningSavedStage(false);}
 }
 function rememberSavedStage(link:SavedStageLink,show:boolean){
  setSavedStage(link);setShowSavedStage(show);
  try{localStorage.setItem(linkKey,JSON.stringify(link));}catch{/* This window can still retry the confirmed destination. */}
 }
 function openTransition(record:StageRun,next:RunStage,kind:TransitionKind,previous:string,operation:string){
  const link={run:record.id,node:next.node_id,conversation:next.stage_session_id!,kind,previous,operation,
   targetName:record.definition.nodes.find(item=>item.id===next.node_id)?.name??next.node_id};
  rememberSavedStage(link,false);
  return openSavedStage(link);
 }
 function savedStageMessage(link:SavedStageLink){
  const stored=link.kind==='approve'?t('审核已保存','Approval saved'):link.kind==='rework'?t('返工已记录','Rework recorded')
   :link.kind==='revise'?t('修订已创建','Revision created'):t('工作流已开始','Workflow started');
  if(link.historical)return openingSavedStage()?t(`正在打开「${link.targetName}」的历史版本。`,`Opening the historical version of “${link.targetName}”…`)
   :t(`${stored}。可查看「${link.targetName}」的历史版本。`,`${stored}. A historical version of “${link.targetName}” is available.`);
  return openingSavedStage()?t(`${stored}，正在打开「${link.targetName}」。`,`${stored}. Opening “${link.targetName}”…`)
   :t(`${stored}，「${link.targetName}」暂未打开。`,`${stored}. “${link.targetName}” has yet to open.`);
 }
 function remember(id:string){try{localStorage.setItem(chosenKey,id);}catch{/* Server index retains history. */}}
 function install(value:StageIndex,initial:boolean){
  if(!current())return;
  setIndex(value);
  if(!value.definitions.some(x=>x.id===definition()))setDefinition(value.definitions[0]?.id??'');
  if(initial||!value.runs.some(x=>x.id===runId())){
   let preferred:string|null=null;try{preferred=localStorage.getItem(chosenKey);}catch{/* use server ordering */}
   const selected=selectStage(value,scope.conversation_id,preferred);setRunId(selected.run?.id??'');setPage(selected.page??'');
  }
 }
 async function reload(initial=false){try{const data=await stageApi.list(scope,control.signal);install(data,initial);if(current())setError('');}catch(e){if(current())setError(errorText(e));}finally{if(current())setLoading(false);}}
 async function loadCatalog(){try{const response=await stageApi.capabilities(control.signal);if(current()){setCatalog(response.capabilities);setCatalogError('');}}catch(e){if(current())setCatalogError(errorText(e));}}
 let catalogWasOpen=false;createEffect(()=>{const open=p.capabilitiesOpen;if(catalogWasOpen&&!open)void loadCatalog();catalogWasOpen=open;});
 let editorOrigin:{button:'new'|'edit';top:number}|undefined;
 const validDefinitionDraft=(value:unknown):value is Definition=>{
  const d=value as Partial<Definition>|null;
  return !!d&&typeof d.id==='string'&&typeof d.name==='string'&&Number.isSafeInteger(d.revision)&&Array.isArray(d.nodes);
 };
 function restoreEditor(){
  if(editor())return;
  try{
   const raw=localStorage.getItem(editKey);
   if(!raw)return;
   const data=JSON.parse(raw);
   if(validDefinitionDraft(data)){setEditor({value:data,edit:operationId()});setChoosing(true);return;}
   if(!validDefinitionDraft(data?.value)||typeof data.edit!=='string')throw Error();
   const pending=data.pending;
   if(pending&&(typeof pending.id!=='string'||typeof pending.conversation!=='string'||typeof pending.edit!=='string'||
    !validDefinitionDraft(pending.value)||pending.value.id!==data.value.id||pending.value.revision!==data.value.revision))throw Error();
   setEditor(data);setChoosing(true);
  }catch{setError(t('本机的工作流定义草稿无法读取。','The local workflow-definition draft could not be read.'));}
 }
 function editorStorageError(){
  setEditorNotice(t('本机草稿未保存，请保留此页。','The local draft could not be saved. Keep this page open.'));
 }
 function storeEditor(d:DefinitionDraft){
  try{localStorage.setItem(editKey,JSON.stringify(d));return true;}
  catch{editorStorageError();return false;}
 }
 function changeEditor(value:Definition){
  const draft=editor();
  if(!draft)return;
  const next={...draft,value,edit:operationId()};
  setEditor(next);storeEditor(next);
 }
 function closeEditor(button=editorOrigin?.button){
  try{localStorage.removeItem(editKey);}catch{editorStorageError();return false;}
  const top=editorOrigin?.top;
  editorOrigin=undefined;
  batch(()=>{setEditor(undefined);setEditorNotice('');});
  if(inLibrary())queueMicrotask(()=>{
   if(!root||!inLibrary()||editor())return;
   if(top!==undefined)root.scrollTop=top;
   (root.querySelector<HTMLElement>(`[data-definition-origin="${button}"]`)??root.querySelector<HTMLElement>('.wf-library-choose select'))?.focus({preventScroll:true});
  });
  return true;
 }
 function editDefinition(value:Definition,button:'new'|'edit'){
  editorOrigin={button,top:root?.scrollTop??0};
  const draft={value,edit:operationId()};
  batch(()=>{setEditorNotice('');setEditor(draft);});
  storeEditor(draft);
  queueMicrotask(()=>{
   const heading=root?.querySelector<HTMLElement>('.wf-editor-head h1');
   if(heading&&root){root.scrollTop=0;heading.focus({preventScroll:true});}
  });
 }
 async function saveDefinition(){
  const draft=editor();
  if(!draft||busy()||openingSavedStage()||pendingTransition()||p.archived)return;
  if(draft.conflict){
   const value={...draft.value,id:'wf-'+operationId(),revision:0};
   const next={value,edit:operationId()};
   batch(()=>{setEditor(next);setEditorNotice(t('已转为副本草稿。保存后创建新工作流。','This is now a copy. Save to create a new workflow.'));});
   storeEditor(next);
   return;
  }
  const source=owner();
  const pending=draft.pending??{id:operationId(),conversation:source.conversation_id,edit:draft.edit,value:draft.value};
  if(pending.conversation!==source.conversation_id){
   setEditorNotice(t('请从原会话重试这次保存。','Retry this save from its original conversation.'));
   return;
  }
  const submitting={...draft,pending};
  if(!storeEditor(submitting))return;
  batch(()=>{setEditor(submitting);setEditorNotice('');setBusy('define');});
  try{
   const saved=await stageApi.define(source,pending.value,pending.id);
   const latest=editor();
   if(!current()||latest?.pending!==pending)return;
   if(saved.id!==pending.value.id||saved.revision!==pending.value.revision+1)
    throw Error(t('收到的版本与保存请求不一致。','The saved revision does not match this request.'));
   batch(()=>{installDefinition(saved);setDefinition(saved.id);});
   if(latest.edit===pending.edit&&closeEditor())return;
   const next={...latest,pending:undefined,value:{...latest.value,revision:saved.revision}};
   setEditor(next);
   setEditorNotice(t(`已保存 v${saved.revision}，后续修改仍在本机。`,`Version ${saved.revision} saved. Later edits are kept locally.`));
   storeEditor(next);
  }catch(e){
   const latest=editor();
   if(!current()||latest?.pending!==pending)return;
   if(rejected(e)&&!(e instanceof ApiError&&e.status===429)){
    const conflict=e instanceof ApiError&&e.status===409;
    const next={...latest,pending:undefined,conflict};
    setEditor(next);
    setEditorNotice(conflict?t('定义已更新。当前编辑可转为副本。','The definition changed. You can keep these edits as a copy.'):errorText(e));
    storeEditor(next);
    if(conflict)await reload();
   }else setEditorNotice(t('上次保存结果未确认。重试会沿用原提交。','Save result unconfirmed. Retry uses the original submission.'));
  }finally{if(current())setBusy('');}
 }
 const initial=p.initial;
 if(initial){install(initial,true);setLoading(false);}
 onMount(()=>{
  restoreEditor();if(!initial)void reload(true);void loadCatalog();
  root?.addEventListener('workflow-reading-change',scheduleReading);
  window.addEventListener('pagehide',flushReading);
  onCleanup(()=>{
   root?.removeEventListener('workflow-reading-change',scheduleReading);
   window.removeEventListener('pagehide',flushReading);
   if(readingFrame!==undefined)cancelAnimationFrame(readingFrame);
   reading.flush();
  });
 });
 onCleanup(()=>{control.abort();coverageRequest?.abort();p.onViewContext(scope,undefined);});
 function update(value:StageRun){
  if(!current()||value.project_id!==scope.project_id||value.conversation_id!==owner().conversation_id)throw Error(t('收到其他会话的工作流回执。','The workflow receipt belongs to another conversation.'));
  setIndex(old=>{
   if(!old)return old;
   const previous=old.runs.find(run=>run.id===value.id);
   if(previous&&previous.revision>value.revision)return old;
   // The definition is pinned for a run. Keep its navigation buttons mounted during polling.
   const definition=previous?.definition;
   const stages=value.stages.map(stage=>{
    // Write receipts can omit the worker observation supplied by a read.
    if(stage.status!=='running'||stage.execution_state||!stage.operation_id)return stage;
    const observed=previous?.stages.find(item=>item.status==='running'&&item.node_id===stage.node_id&&
     item.stage_session_id===stage.stage_session_id&&item.operation_id===stage.operation_id)?.execution_state;
    return observed?{...stage,execution_state:observed}:stage;
   });
   const next={...value,stages,definition:
    definition?.id===value.definition.id&&definition.revision===value.definition.revision?definition:value.definition};
   return {...old,runs:[next,...old.runs.filter(run=>run.id!==next.id)]};
  });
 }
 const polling=createMemo(()=>asking()||busy()==='execute'||!!run()?.stages.some(s=>
  s.status==='running'&&s.execution_state!=='interrupted'||s.dialogue?.some(item=>item.status==='pending')));
 createEffect(()=>{
  const id=runId();
  if(!id||!polling())return;
  const timer=new AbortController();
  let handle:ReturnType<typeof setTimeout>;
  async function poll(){try{const next=await stageApi.run(owner(),id,timer.signal);if(!timer.signal.aborted&&current())update(next);}catch{/* The action receipt or a manual refresh still reports errors. */}finally{if(!timer.signal.aborted)handle=setTimeout(poll,1400);}}
  handle=setTimeout(poll,650);onCleanup(()=>{timer.abort();clearTimeout(handle);});
 });
 const [stopSubmitting,setStopSubmitting]=createSignal(false);
 async function stopExecution(operation:string){
  const basis=run(),currentStage=stage(),session=currentStage?.stage_session_id;
  if(!basis||!session||basis.active_node_id!==currentStage.node_id||currentStage.status!=='running'||
     currentStage.operation_id!==operation||currentStage.stop_request||stopSubmitting()||p.archived)return;
  setStopSubmitting(true);setError('');
  try{
   const next=await stageApi.act(basis,currentStage.node_id,'stop',{operation_id:operation},operationId(),session);
   if(current())update(next);
  }catch(e){
   if(!current())return;
   try{const next=await stageApi.run(owner(),basis.id,control.signal);if(current())update(next);}catch{/* The regular running poll can still observe the request. */}
   if(current()&&stage()?.operation_id===operation&&stage()?.status==='running'&&!stage()?.stop_request)setError(errorText(e));
  }finally{if(current())setStopSubmitting(false);}
 }
 // Reuse the exact operation on a retry after a lost response, also after a reload.
 async function mutate<T>(label:string,identity:unknown,write:(op:string,legacy:boolean)=>Promise<T>,success:(v:T,op:string)=>void|Promise<void>,
  savedOperation?:{key:string;onConflict:()=>void}):Promise<boolean>{
  if(busy()||openingSavedStage()||pendingTransition()||p.archived||sourceWasReplaced()&&label!=='start'&&label!=='define')return false;
  setBusy(label);setError('');
  const key=savedOperation?.key??stageModeKey(owner())+':operation:'+label;
  try{
   const body=JSON.stringify(identity),stored=JSON.parse(localStorage.getItem(key)??'null');
   const legacy=stored?.body===body&&typeof stored.id==='string';
   const op=legacy?stored.id:operationId();
   localStorage.setItem(key,JSON.stringify({body,id:op}));
   const result=await write(op,legacy);
   if(!current())return false;
   localStorage.removeItem(key);await success(result,op);clearPending(op);return true;
  }catch(e){if(current()){
   if(e instanceof ApiError&&e.status===409){await reload();savedOperation?.onConflict();}
   if(!pendingTransition())setError(errorText(e));
  }return false;}finally{if(current())setBusy('');}
 }
 function chooseRun(r:StageRun){
  rememberReading();
  const destination=r.active_node_id??r.stages.at(-1)!.node_id;
  const changed=inLibrary()||runId()!==r.id||page()!==destination||!!coverageReader();
  batch(()=>{p.onArrival(undefined);setRunId(r.id);setPage(destination);setChoosing(false);setError('');setCoverageReturn(undefined);setCoverageReader(undefined);});
  remember(r.id);
  if(changed)settleView();
 }
 function newDraft(from?:Definition){
  const nodes=from?structuredClone(from.nodes):[blankNode()];
  editDefinition({id:'wf-'+operationId(),revision:0,name:from?from.name+' '+t('副本','copy'):'',nodes},from?'edit':'new');
 }
 async function action(name:string,payload:Record<string,unknown>,onReceipt?:(stage:RunStage,op:string)=>void,
  pending?:{revision:number;onConflict?:()=>void;draftEdit?:string;identity?:Record<string,unknown>}){
  const r=run(),n=node(),origin=stage(),session=origin?.stage_session_id;if(!r||!n)return false;
  // A new attempt owns the stage status immediately, even when it fails quickly.
  if(name==='execute'&&p.arrival)p.onArrival(undefined);
  const conversation=name==='record_manual_check'?(session??undefined):undefined;
  const revision=pending?.revision??r.revision;
  const footer=name==='approve'?root?.querySelector('.wf-footer'):undefined;
  const approvalHadFocus=footer?.contains(document.activeElement);
  return mutate(name,[r.id,revision,n.id,pending?.identity??payload],(op,legacy)=>
   (name==='approve'||name==='rework')&&session
    ?sendTransition(op,r.conversation_id,{kind:name,run_id:r.id,node_id:n.id,stage_session_id:session,expected_revision:revision,payload},legacy,pending?.draftEdit)
    :stageApi.act({...r,revision},n.id,name,payload,op,conversation),async(v,op)=>{
   if(name==='approve'||name==='rework'){
    setCoverageReturn(undefined);
    p.onDirectory(v);
    const next=v.stages.find(s=>s.node_id===v.active_node_id);
    if(next?.stage_session_id&&next.stage_session_id!==scope.conversation_id){
     if(!await openTransition(v,next,name,n.name,op)&&current()&&name==='approve')update(v);
     return;
    }
    const destination=v.active_node_id??v.stages.at(-1)!.node_id;
    const focusCompletion=!next&&approvalHadFocus&&(document.activeElement===document.body||footer?.contains(document.activeElement));
    batch(()=>{
     if(next)p.onArrival({run:v.id,node:next.node_id,conversation:next.stage_session_id??scope.conversation_id,previous:n.name,kind:name});
     update(v);setPage(destination);
    });
    if(!next){
     if(focusCompletion)queueMicrotask(()=>root?.querySelector<HTMLElement>('.wf-completion-message')?.focus({preventScroll:true}));
    }else settleView();
   }else{
    update(v);
    const saved=onReceipt&&v.stages.find(s=>s.node_id===n.id&&s.stage_session_id===session);
    if(saved)onReceipt(saved,op);
   }
  },pending?.onConflict&&conversation?{onConflict:pending.onConflict,
   key:stageModeKey({...scope,conversation_id:conversation})+':manual:'+JSON.stringify([payload.case_id,payload.action_id])}:undefined);
 }
 async function supplement(text:string,sourceType:FollowUpType,draftEdit?:string){
  const value=run(),target=stage();
  if(!value||!target?.stage_session_id||sending()||openingSavedStage()||pendingTransition()||sourceWasReplaced()||p.archived)return false;
  const amend=target.status==='approved';
  const name=amend?'revise':'supplement';
  const key=stageModeKey({project_id:value.project_id,conversation_id:target.stage_session_id})+':'+name;
  setSending(true);setError('');
  try{
   const stored=JSON.parse(localStorage.getItem(key)??'null');
   const sameRequest=stored?.text===text&&(stored?.sourceType??'instruction')===sourceType;
   const legacy=sameRequest&&typeof stored.id==='string';
   const id=legacy?stored.id:operationId();
   const submittedEdit=legacy?stored.draftEdit:draftEdit;
   localStorage.setItem(key,JSON.stringify({id,text,sourceType,draftEdit:submittedEdit}));
   const result=amend
    ?await sendTransition(id,target.stage_session_id,{kind:'revise',run_id:value.id,node_id:target.node_id,
     stage_session_id:target.stage_session_id,expected_revision:value.revision,payload:sourceType==='requirement'?{text,source_type:sourceType}:{text}},legacy,submittedEdit)
    :await stageApi.supplement(value,target,name,text,sourceType,id);
   if(!amend&&result.id===value.id&&result.project_id===value.project_id&&result.conversation_id===value.conversation_id)
    settleStageDraft(draftKey(value,target.node_id,target.stage_session_id),{field:'followUp',text,sourceType},submittedEdit);
   if(!current())return false;
   localStorage.removeItem(key);
   if(amend){
    p.onDirectory(result);
    const next=result.stages.find(s=>s.node_id===target.node_id);
    if(next?.stage_session_id&&next.stage_session_id!==scope.conversation_id){
     await openTransition(result,next,'revise',value.definition.nodes.find(n=>n.id===target.node_id)?.name??target.node_id,id);
     clearPending(id);
     return true;
    }
   }
   update(result);
   clearPending(id);
   return true;
  }catch(e){
   if(current()){
    if(e instanceof ApiError&&e.status===409)await reload();
    if(!pendingTransition())setError(errorText(e));
   }
   return false;
  }finally{if(current())setSending(false);}
 }
 async function ask(question:string,reference?:DialogueReference){
  const value=run(),target=stage();
  if(!value||!target?.stage_session_id||asking()||pendingTransition()||sourceWasReplaced()||p.archived)return false;
  const key=stageModeKey({project_id:value.project_id,conversation_id:target.stage_session_id})+':ask';
  const identity=[question,reference?.annotationId??null];
  if(reference?.followUpId)identity.push(reference.followUpId);
  const body=JSON.stringify(identity);
  setAsking(true);setError('');
  try{
   const stored=JSON.parse(localStorage.getItem(key)??'null');
   const id=stored?.body===body&&typeof stored.id==='string'?stored.id:operationId();
   localStorage.setItem(key,JSON.stringify({body,id}));
   const next=await stageApi.ask(value,target,question,reference,id);
   if(!current())return false;
   update(next);localStorage.removeItem(key);
   return true;
  }catch(e){
   if(current())setError(errorText(e));
   return false;
  }finally{if(current())setAsking(false);}
 }
 function jump(kind:StageKind,id:string){
  const r=run(),source=node();if(!r||!source)return;
  const matches=r.definition.nodes.filter(n=>n.kind===kind&&source.inputs.includes(n.id)&&r.stages.find(s=>s.node_id===n.id&&s.status==='approved')?.artifact?.items.some(row=>row.id===id));
  if(matches.length!==1){setError(t('找不到唯一的已审核来源。','The reference does not identify a unique approved source.'));return;}
  rememberReading();
  batch(()=>{setCoverageReturn(undefined);p.onArrival(undefined);setPage(matches[0].id);});
  settleView({row:id});
 }
 function closeCoverage(){
  coverageRequest?.abort();coverageRequest=undefined;coverageOpener=undefined;
  batch(()=>{setCoverage(undefined);setCoverageReading(undefined);setCoverageOverview(undefined);});
 }
 function openOverview(){
  const selected=run();
  if(selected)setCoverageOverview({run:selected.id,page:page(),reading:emptyOverviewReading()});
 }
 function selectOverviewRow(row:CoverageOverviewRow,reading:OverviewReading){
  const view=coverageOverview(),selected=run();
  if(!view||selected?.id!==view.run)return;
  coverageOpener=overviewTrigger;
  batch(()=>{
   setCoverageError('');
   setCoverageOverview({...view,reading});
   setCoverage({project_id:selected.project_id,run_id:selected.id,node_id:row.node_id,
    stage_session_id:row.stage_session_id,selector:row.selector,row_id:row.row_id});
  });
  queueMicrotask(()=>{
   overviewBack?.closest('.wf-dialog-body')?.scrollTo({top:0,behavior:'instant'});
   overviewBack?.focus({preventScroll:true});
  });
 }
 function backToOverview(){
  coverageRequest?.abort();coverageRequest=undefined;
  batch(()=>{setCoverage(undefined);setCoverageReading(undefined);setCoverageError('');});
  queueMicrotask(()=>root?.querySelector<HTMLInputElement>('.wf-coverage-overview input[type=search]')?.focus({preventScroll:true}));
 }
 function openCoverage(origin:RunStage|undefined,selector:CoverageSelector,row_id:string,opener:HTMLButtonElement){
  const value=run();
  if(!value||!origin?.stage_session_id)return;
  coverageOpener=opener;
  setCoverageError('');
  setCoverage({project_id:value.project_id,run_id:value.id,node_id:origin.node_id,
   stage_session_id:origin.stage_session_id,selector,row_id});
 }
 function captureCoverage(query:CoverageQuery,reading:CoverageReading):CoverageEntry{
  const source:CoverageEntry={query,reading,top:root?.scrollTop??0,width:root?.clientWidth??0,reader:coverageReader(),
   overview:coverageOverview()};
  const followUp=coverageOpener?.closest<HTMLElement>('[data-follow-up]');
  const row=coverageOpener?.closest<HTMLElement>('[data-workflow-row]')??
   (followUp?root?.querySelector<HTMLElement>('.wf-artifact-browser [data-workflow-row]'):undefined);
  if(row?.dataset.workflowRow){
   const index=row.closest<HTMLElement>('[data-index-open]');
   source.artifact={row:row.dataset.workflowRow,indexOpen:index?index.dataset.indexOpen==='true':undefined,
    search:index?.querySelector<HTMLInputElement>('.wf-index-items input[type=search]')?.value,
    sourceScroll:coverageOpener?.closest<HTMLElement>('.wf-reading-detail')?.closest<HTMLElement>('.wf-dialog-body')?.scrollTop};
  }
  if(followUp?.dataset.followUp)source.followUp={id:followUp.dataset.followUp,
   asking:followUp.closest<HTMLElement>('.wf-conversation-float')?.dataset.asking==='true',
   scroll:followUp.closest<HTMLElement>('.wf-conversation-scroll')?.scrollTop??0,
   executionOpen:!!followUp.querySelector<HTMLDetailsElement>('.wf-followup-execution')?.open};
  return source;
 }
 function restoreCoverage(view:CoverageEntry){
  if(!root)return;
  const {artifact,followUp,query}=view;
  let opener:HTMLButtonElement|undefined=view.overview?overviewTrigger:undefined;
  if(artifact){
   const output=root.querySelector('.wf-artifact');
   output?.dispatchEvent(new CustomEvent('workflow-reveal-row',{detail:{id:artifact.row,focus:false,indexOpen:artifact.indexOpen,search:artifact.search}}));
   const row=[...output?.querySelectorAll<HTMLElement>('[data-workflow-row]')??[]].find(element=>element.dataset.workflowRow===artifact.row);
   if(artifact.sourceScroll!==undefined)row?.querySelector<HTMLButtonElement>('[data-read-source]')?.click();
   opener=[...row?.querySelectorAll<HTMLButtonElement>('[data-coverage-source]')??[]]
    .find(button=>button.dataset.coverageSource===query.row_id);
   const sourceBody=opener?.closest<HTMLElement>('.wf-dialog-body');
   if(sourceBody&&artifact.sourceScroll!==undefined)sourceBody.scrollTop=artifact.sourceScroll;
  }
  if(followUp){
   root.querySelector('.wf-page')?.dispatchEvent(new CustomEvent('workflow-restore-follow-up',{detail:followUp}));
   const current=document.activeElement;
   opener=current instanceof HTMLButtonElement&&current.dataset.coverageSource===query.row_id&&
    current.closest<HTMLElement>('[data-follow-up]')?.dataset.followUp===followUp.id?current:undefined;
  }
  (opener??root.querySelector<HTMLElement>('.wf-heading h1'))?.focus({preventScroll:true});
  root.scrollTo({top:view.top,behavior:'instant'});
  if(artifact&&!followUp&&opener&&view.width!==root.clientWidth)opener.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});
  coverageOpener=opener;
  batch(()=>{setCoverageError('');setCoverageReading(view.reading);setCoverageOverview(view.overview);setCoverage(query);});
 }
 function settleView(options:{row?:string;coverage?:CoverageEntry}={}){
  const change=++viewChange;
  queueMicrotask(()=>{
   if(!root||!current()||change!==viewChange)return;
   readingReady=readingKey();
   const artifact=root.querySelector('.wf-artifact');
   if(options.coverage)restoreCoverage(options.coverage);
   else if(options.row)artifact?.dispatchEvent(new CustomEvent('workflow-reveal-row',{detail:{id:options.row}}));
   else{
    if(!restoreReading())root.scrollTo({top:0,behavior:'instant'});
    root.querySelector<HTMLElement>('.wf-heading h1,.wf-library-head h1,.wf-editor-head h1')?.focus({preventScroll:true});
   }
  });
 }
 async function openCoverageTarget(target:CoverageTarget,reading:CoverageReading){
  const query=coverage();
  if(!query||coverageRequest)return;
  const request=coverageRequest=new AbortController();
  setCoverageError('');
  const source=captureCoverage(query,reading);
  try{
   const record=await stageApi.coverageRecord(query,target,request.signal);
   if(!current()||coverage()!==query)return;
   if(record.historical||historyStage()){
    batch(()=>{closeCoverage();setCoverageReturn(source);p.onArrival(undefined);setCoverageReader(record);});
    settleView({row:target.row_id});
    return;
   }
   const live=await stageApi.run(owner(),query.run_id,request.signal);
   if(!current()||coverage()!==query)return;
   const existing=run();
   if(existing?.id!==query.run_id)return;
   const displayed=existing.revision>live.revision?existing:live;
   const origin=displayed.stages.find(s=>s.node_id===query.node_id&&s.stage_session_id===query.stage_session_id);
   const next=displayed.stages.find(s=>s.node_id===target.node_id&&
    s.stage_session_id===target.stage_session_id&&(s.operation_id??null)===(target.operation_id??null));
   if(!origin||!next?.artifact?.items.some(row=>row.id===target.row_id)){
    setCoverageError(t('目标成果已有新版本。请查看更新后的覆盖关系。','The output changed. Check the refreshed coverage.'));
    return;
   }
   batch(()=>{update(live);closeCoverage();setCoverageReturn(source);p.onArrival(undefined);setPage(next.node_id);});
   settleView({row:target.row_id});
  }catch(e){if(current()&&coverage()===query)setCoverageError(errorText(e));}
  finally{if(coverageRequest===request)coverageRequest=undefined;}
 }
 function returnToCoverage(){
  const view=coverageReturn();
  if(!view)return;
  const {query}=view;
  setCoverageReturn(undefined);
  const originalSnapshot=view.reader?.stage??index()?.historical_stage;
  const fromSnapshot=originalSnapshot?.node_id===query.node_id&&originalSnapshot.stage_session_id===query.stage_session_id;
  const origin=fromSnapshot?originalSnapshot:run()?.stages.find(s=>s.node_id===query.node_id&&s.stage_session_id===query.stage_session_id);
  if(view.overview){
   batch(()=>{setCoverageReader(view.reader);setPage(view.overview!.page);});
   if(origin)settleView({coverage:view});
   else {
    setCoverageOverview(view.overview);
    settleView();
   }
   return;
  }
  if(!origin){setError(t('原需求已有新版本，请从当前需求查看覆盖。','The requirement has a new version. Open coverage from the current requirement.'));return;}
  batch(()=>{setCoverageReader(view.reader);if(!fromSnapshot)setPage(origin.node_id);});
  settleView({coverage:view});
 }
 function startSelected(){
  const d=selected();if(!d)return;
  void mutate('start',[d.id,d.revision],(op,legacy)=>sendTransition(op,owner().conversation_id,
   {kind:'start',definition_id:d.id,expected_definition_revision:d.revision},legacy),async(r,op)=>{
   p.onDirectory(r);
   const child=r.stages.find(x=>x.node_id===r.active_node_id);
   if(child?.stage_session_id){
    if(!await openTransition(r,child,'start',d.name,op)&&current())update(r);
    return;
   }
   update(r);chooseRun(r);
  });
 }
 function installDefinition(saved:Definition,improved=false){
  setIndex(old=>{
   if(!old)return old;
   const improvedIds=old.improved_definition_ids??[];
   const definitions=old.definitions.some(item=>item.id===saved.id&&item.revision>saved.revision)?
    old.definitions:[...old.definitions.filter(item=>item.id!==saved.id),saved];
   return {...old,definitions,improved_definition_ids:improved&&!improvedIds.includes(saved.id)?[...improvedIds,saved.id]:improvedIds};
  });
 }
 async function openHistorySource(source:DefinitionImprovementSource){
  if(source.stage_session_id!==scope.conversation_id){
   p.onArrival({run:source.run_id,node:source.node_id,conversation:source.stage_session_id,kind:'source',previous:''});
   try{await p.onChild(source.stage_session_id);}
   catch(e){if(current())p.onArrival(undefined);throw e;}
   if(current())setDefinitionHistory(undefined);
   return;
  }
  batch(()=>{p.onArrival(undefined);setDefinitionHistory(undefined);setCoverageReturn(undefined);setPage(source.node_id);setChoosing(false);});
  settleView();
 }
 async function openImprovedDefinition(id:string){
  if(!index()?.definitions.some(item=>item.id===id))await reload();
  if(!current()||!index()?.definitions.some(item=>item.id===id))return;
  setDefinition(id);setChoosing(true);
  queueMicrotask(()=>root?.querySelector<HTMLElement>(editor()?'.wf-editor-head h1':'.wf-library-choose select')?.focus({preventScroll:true}));
 }
 function selectPage(id:string){
  if(id===page())return;
  rememberReading();
  batch(()=>{p.onArrival(undefined);setPage(id);setError('');setCoverageReturn(undefined);});
  settleView();
 }
 function openVersion(id:string){p.onArrival(undefined);void p.onChild(id).catch(e=>{if(current())setError(errorText(e));});}
 function returnToCurrent(){
  const value=run();
  if(!value)return;
  const currentStage=value.stages.find(s=>s.node_id===index()?.historical_node_id);
  const activeStage=value.stages.find(s=>s.node_id===value.active_node_id);
  openVersion(currentStage?.stage_session_id??activeStage?.stage_session_id??value.conversation_id);
 }
 const inLibrary=()=>choosing()||!run();
 const viewTitle=createMemo(()=>{
  const currentNode=displayedNode(),currentStage=historyStage()??stage();
  if(inLibrary()||!currentNode||!currentStage)return undefined;
  const version=1+currentStage.log.filter(event=>event.event==='revised'||event.event==='rework').length;
  return version===1?currentNode.name:currentNode.name+' · v'+version;
 });
 createEffect(()=>{const title=viewTitle();p.onViewContext(scope,title?{title,readonly:!!historyStage()}:undefined);});
 const arrival=()=>{const a=p.arrival;return a&&!inLibrary()&&a.run===runId()&&a.node===(historyStage()?.node_id??page())&&a.conversation===scope.conversation_id?a:undefined;};
 const readingStage=()=>inLibrary()?undefined:historyStage()??stage();
 const readingKey=createMemo(()=>{
  const item=readingStage(),session=item?.stage_session_id;
  return session?JSON.stringify([scope.project_id,runId(),item.node_id,session]):undefined;
 });
 const readingBasis=createMemo(()=>{
  const key=readingKey(),operation=artifactReadingOperation(readingStage());
  if(key&&operation)return {key,operation};
 });
 function rememberReading(){
  const basis=readingBasis();
  if(basis&&readingReady===basis.key&&root?.isConnected&&root.clientHeight&&root.querySelector('.wf-artifact'))
   reading.remember(basis,captureWorkflowReading(root));
 }
 function scheduleReading(){
  if(readingFrame!==undefined)return;
  const basis=readingBasis();
  if(!basis||readingReady!==basis.key)return;
  readingFrame=requestAnimationFrame(()=>{
   readingFrame=undefined;
   const currentBasis=readingBasis();
   if(currentBasis?.key===basis.key&&currentBasis.operation===basis.operation)rememberReading();
  });
 }
 function flushReading(){rememberReading();reading.flush();}
 function restoreReading(){
  const basis=readingBasis(),saved=basis?reading.load(basis):undefined;
  return !!(root&&saved&&restoreWorkflowReading(root,saved));
 }
 createEffect(()=>{
  const key=readingKey(),change=viewChange,explicit=untrack(arrival);
  queueMicrotask(()=>{
   if(!current()||change!==viewChange||key!==readingKey())return;
   readingReady=key;
   if(key&&!explicit&&!restoreReading()&&root)root.scrollTop=0;
  });
 });
 createEffect(()=>{
  const target=arrival();if(!target)return;
  queueMicrotask(()=>{
   if(!current()||arrival()!==target)return;
   const heading=root?.querySelector<HTMLElement>('.wf-heading h1');
   if(!heading)return;
   heading.focus({preventScroll:true});p.onArrivalShown(target);
  });
 });
 return <main ref={root} class="wf-studio" aria-label={t('工作流','Workflow')} data-view={inLibrary()?'library':'stage'} data-stage-kind={displayedNode()?.kind} data-arrival={arrival()&&historyStage()?'source':arrival()?.kind} onScroll={scheduleReading}>
 <div class="wf-column">
 <div class="wf-studio-feedback">
  <Show when={showSavedStage()&&savedStage()} fallback={<Show when={awaitingReceipt()} fallback={
   <Show when={transitionNotice()} fallback={<Show when={error()||catalogError()}><div class="wf-error wf-notice">
   <p role="alert">{error()||t('能力目录读取失败','Capability catalog unavailable')+' · '+catalogError()}</p>
   <div class="wf-notice-actions">
    <button type="button" disabled={!!busy()} onClick={()=>{if(error())void reload();else void loadCatalog();}}>{t('重新读取','Refresh')}</button>
    <button type="button" class="wf-icon-button" aria-label={t('关闭提示','Dismiss notice')} onClick={()=>{
     if(error())setError('');else setCatalogError('');
     if(!error()&&!catalogError())root?.querySelector<HTMLElement>('h1')?.focus({preventScroll:true});
    }}><WorkflowIcon name="close" size={16}/></button>
   </div>
   </div></Show>}>{notice=><div class="wf-error wf-notice">
    <p role="status" tabindex="-1" data-transition-next>{notice()}</p>
    <button type="button" class="wf-icon-button" aria-label={t('关闭提示','Dismiss notice')} onClick={()=>{
     setTransitionNotice('');root?.querySelector<HTMLElement>('h1')?.focus({preventScroll:true});
    }}><WorkflowIcon name="close" size={16}/></button>
   </div>}</Show>
  }><div class="wf-error wf-notice wf-transition-recovery">
   <p role="status">{checkingTransition()?t('正在核查原操作…','Checking the original request…')
    :transitionError()|| (missingTransition()?t('暂未查到保存回执。','There is no saved receipt yet.')
     :t('尚未确认原操作是否保存。','The original request is still unconfirmed.'))}</p>
   <div class="wf-notice-actions">
    <button type="button" class="wf-primary" aria-disabled={!canOpenSavedStage()} onClick={()=>void checkTransition()}>
     {t('核查原操作','Check original request')}</button>
    <Show when={missingTransition()&&!p.archived}><button type="button" aria-disabled={!canOpenSavedStage()} onClick={()=>void checkTransition(true)}>
     {t('按原请求重试','Retry original request')}</button></Show>
   </div>
  </div></Show>}>{link=><div class="wf-error wf-notice wf-stage-reopen">
    <p role="status">{savedStageMessage(link())}</p>
    <div class="wf-notice-actions">
     <button type="button" class="wf-primary" data-transition-next aria-disabled={!canOpenSavedStage()} onClick={()=>{
      if(canOpenSavedStage())void openSavedStage(link());
     }}><WorkflowIcon name={link().historical?'history':'arrow'} size={15}/>{link().historical
      ?t('查看原阶段','View original stage'):t('打开已保存阶段','Open saved stage')}</button>
     <button type="button" class="wf-icon-button" aria-label={t('关闭提示','Dismiss notice')} disabled={openingSavedStage()} onClick={()=>{
      if(link().operation)clearPending(link().operation!);
      setShowSavedStage(false);root?.querySelector<HTMLElement>('h1')?.focus({preventScroll:true});
     }}><WorkflowIcon name="close" size={16}/></button>
    </div>
  </div>}</Show>
 </div>
 <Show when={loading()}><p role="status">{t('正在读取工作流…','Loading workflows…')}</p></Show>
 <Show when={index()}>
 <div class="wf-journey-controls" data-has-nav={!!run()&&!choosing()&&!historyStage()}>
 <header class="wf-toolbar">
  <div class="wf-crumb"><span class="wf-workspace-symbol"><WorkflowIcon name="layers" size={19}/></span>
   <span class="wf-workspace-name">{t('工程工作流','Engineering workflow')}</span><span aria-hidden="true" class="wf-crumb-sep">/</span>
   <Show when={run()} fallback={<strong>{t('流程库','Library')}</strong>}>
    <button type="button" aria-expanded={inLibrary()} onClick={()=>{rememberReading();setChoosing(v=>!v);if(choosing())restoreEditor();settleView();}}>
    {run()!.definition.name}<WorkflowIcon name="chevron" size={13}/></button>
   </Show>
  </div>
  <div class="wf-toolbar-actions">
   <Show when={run()&&!inLibrary()&&!historyStage()&&run()!.definition.nodes.some(item=>item.kind==='requirements')}>
    <button ref={overviewTrigger} type="button" class="wf-toolbar-settings wf-overview-trigger"
     title={t('覆盖总览','Coverage overview')} aria-label={t('覆盖总览','Coverage overview')} aria-haspopup="dialog" onClick={openOverview}>
     <WorkflowIcon name="clipboard" size={16}/><span class="wf-settings-label">{t('覆盖总览','Coverage overview')}</span>
    </button>
   </Show>
   <button type="button" class="wf-toolbar-settings" title={t('内核与连接','Engines and connections')} aria-label={t('内核与连接','Engines and connections')} aria-haspopup="dialog" onClick={()=>openSettings()}>
    <WorkflowIcon name="sliders" size={16}/><span class="wf-settings-label">{t('内核与连接','Engines and connections')}</span>
   </button>
  </div>
 </header>
 <Show when={run()&&!choosing()&&!historyStage()}><WorkflowNavigation run={run()!} page={page()} busy={!!busy()&&busy()!=='execute'} t={t} onSelect={selectPage}/></Show>
 </div>
 <WorkflowDialog open={settings()} size="regular" childScroll title={t('内核与连接','Engines and connections')} locale={p.locale} onClose={()=>setSettings(false)}>
  <Show when={settingsVisited()}><WorkflowKernelSettings t={t} request={engineRequest()}/></Show>
 </WorkflowDialog>
 <WorkflowDialog open={!!coverage()||!!coverageOverview()} size="regular"
  title={coverageOverview()?t('覆盖总览','Coverage overview'):t('需求覆盖','Requirement coverage')} locale={p.locale} onClose={closeCoverage}>
  <Show when={coverageOverview()&&!coverage()&&run()?.id===coverageOverview()?.run?coverageOverview():undefined}>{view=>
   <WorkflowCoverageOverview scope={scope} run={view().run} revision={run()?.revision??0} t={t}
    restore={view().reading} onSelect={selectOverviewRow}/>
  }</Show>
  <Show when={coverageOverview()&&coverage()}><button type="button" class="wf-link wf-overview-back" ref={overviewBack} onClick={backToOverview}>
   <WorkflowIcon name="back" size={15}/>{t('返回覆盖总览','Back to coverage overview')}
  </button></Show>
  <Show when={coverage()} keyed>{query=><WorkflowCoverage origin={query} revision={run()?.revision??0} error={coverageError()}
   restore={coverageReading()} t={t} onTarget={(target,reading)=>void openCoverageTarget(target,reading)}/>}</Show>
 </WorkflowDialog>
 <Show when={coverageReturn()&&!inLibrary()}>
  <button type="button" class="wf-link wf-return-coverage" onClick={returnToCoverage}>{t('返回需求覆盖','Back to requirement coverage')}</button>
 </Show>
 <Show when={inLibrary()}>
 <WorkflowDialog open={!!definitionHistory()} size="regular" childScroll title={t('改进记录','Improvement history')}
  locale={p.locale} onClose={()=>setDefinitionHistory(undefined)}>
  <Show when={definitionHistory()} keyed>{snapshot=><WorkflowDefinitionHistory scope={scope} definition={snapshot}
   t={t} onOpenSource={openHistorySource}/>}</Show>
 </WorkflowDialog>
 <Show when={editor()} fallback={<section class="wf-setup">
  <header class="wf-library-head"><h1>{t('选择工作流','Choose a workflow')}</h1>
   <button type="button" data-definition-origin="new" disabled={p.archived} onClick={()=>newDraft()}><WorkflowIcon name="plus" size={16}/>{t('新建','New')}</button>
  </header>
  <div class="wf-library-layout">
   <section class="wf-library-choose">
    <label>{t('工作流定义','Workflow definition')}<select value={definition()} onChange={e=>setDefinition(e.currentTarget.value)}><For each={index()?.definitions}>{d=><option value={d.id} selected={d.id===definition()}>{d.name}</option>}</For></select></label>
    <Show when={(index()?.runs.length??0)>0}><label>{t('已开始的工作流','Existing runs')}<select value={runId()} onChange={e=>{const r=index()?.runs.find(x=>x.id===e.currentTarget.value);if(r)chooseRun(r);}}><For each={index()?.runs}>{r=><option value={r.id} selected={r.id===runId()}>{r.definition.name} · {new Date(r.created_at).toLocaleString()} · {r.stages.filter(s=>s.status==='approved').length}/{r.stages.length}</option>}</For></select></label>
      <Show when={run()}><button class="wf-link" type="button" onClick={()=>chooseRun(run()!)}><WorkflowIcon name="history" size={15}/>{t('继续当前工作流','Continue current run')}</button></Show>
    </Show>
   </section>
   <Show when={selected()}>{d=><section class="wf-library-preview">
    <div class="wf-library-title"><span class="wf-stage-symbol"><WorkflowIcon name="branch" size={21}/></span><div><h2>{d().name}</h2><small>v{d().revision} · {d().nodes.length} {t('个流程节点','stages')}</small></div>
    <Show when={index()?.improved_definition_ids?.includes(d().id)}>
     <button class="wf-link wf-library-history" type="button" aria-haspopup="dialog" onClick={()=>setDefinitionHistory(d())}>
      <WorkflowIcon name="history" size={15}/>{t('改进记录','Improvement history')}
     </button>
    </Show></div>
    <ol class="wf-outline"><For each={d().nodes}>{n=><li><WorkflowIcon name={stageGlyph[n.kind]} size={17}/>{n.name}</li>}</For></ol>
    <div class="wf-actions"><button class="wf-primary" type="button" disabled={!!busy()||!!pendingTransition()||p.archived} onClick={startSelected}>{t('开始此工作流','Start workflow')} →</button>
    <button type="button" data-definition-origin="edit" disabled={p.archived} onClick={()=>d().id==='default'?newDraft(d()):editDefinition(structuredClone(d()),'edit')}><WorkflowIcon name="edit" size={16}/>{d().id==='default'?t('基于默认创建','Customize default'):t('编辑定义','Edit definition')}</button></div>
   </section>}</Show>
  </div>
 </section>}>{initialDraft=>{
  const initial=untrack(initialDraft);
  const state=()=>editor()??initial;
  return <StageDefinitionEditor draft={state().value} catalog={catalog()} t={t} busy={!!busy()}
   mode={state().conflict?'copy':state().pending&&busy()!=='define'?'retry':'save'}
   status={busy()==='define'?t('正在保存工作流…','Saving workflow…'):editorNotice()||
    (state().conflict?t('定义已更新。当前编辑可转为副本。','The definition changed. You can keep these edits as a copy.'):
     state().pending?t('上次保存结果未确认。重试会沿用原提交。','Save result unconfirmed. Retry uses the original submission.'):'')}
   onDraft={changeEditor} onCancel={()=>closeEditor()} onSave={()=>void saveDefinition()}/>;
 }}</Show>
 </Show>
 <Show when={run()&&!choosing()&&historyStage()} keyed>{snapshot=><>
  <div class="wf-questions wf-history-banner">
   <p>{coverageReader()&&!coverageReader()?.historical?t('正在查看关联的只读成果。','Viewing the linked read-only output.'):t('正在查看阶段的历史版本。','Viewing a historical stage version.')}</p>
   <div class="wf-history-actions">
    <Show when={snapshot.stage_session_id&&(coverageReader()?.historical||snapshot.stage_session_id===scope.conversation_id&&!coverageReader())}>
     <WorkflowHistoricalDraft storageKey={draftKey(run()!,snapshot.node_id,snapshot.stage_session_id)}
      stageName={run()!.definition.nodes.find(n=>n.id===snapshot.node_id)!.name} t={t}/>
    </Show>
    <Show when={!coverageReader()}><button type="button" onClick={returnToCurrent}>{t('返回当前版本','Return to current version')}</button></Show>
   </div>
  </div>
  <WorkflowStagePage stage={snapshot} node={run()!.definition.nodes.find(n=>n.id===snapshot.node_id)!} run={{...run()!,active_node_id:null}}
   inputVersions={historyInputs()} catalog={catalog()} busy={true} t={t} onStageVersion={openVersion} observeHistoricalDialogue
   onCoverage={(kind,id,opener)=>openCoverage(snapshot,kind,id,opener)} onManageCapabilities={p.onManageCapabilities}
   improvementReadOnly={p.archived} onImprovementSaved={saved=>installDefinition(saved,true)} onOpenImprovedDefinition={openImprovedDefinition}
   onAction={async()=>false} onRef={()=>setError(coverageReturn()
    ?t('请用「返回需求覆盖」查看当时的关联条目。','Use Back to requirement coverage to see the original links.')
    :t('请从原需求的覆盖关系打开关联条目。','Open the linked item from the original requirement coverage.'))}/>
 </>}</Show>
 <Show when={run()&&!choosing()&&!historyStage()}>
 <div class="wf-announcement" role="status" aria-live="polite" aria-atomic="true"><Show when={arrival()?.kind==='approve'||arrival()?.kind==='rework'}><Show when={arrival()}>{a=>
 <span><WorkflowIcon name={a().kind==='approve'?'check':'history'} size={15}/>{a().previous} {a().kind==='approve'?t('已审核','approved'):t('已记录返工','rework recorded')}</span>}</Show></Show></div>
 <Show when={node()&&stage()?JSON.stringify([runId(),page(),stage()?.stage_session_id]):null} keyed>{_=><WorkflowStagePage run={run()!} stage={stage()!} node={node()!} catalog={catalog()} t={t} completed={run()!.status==='completed'&&page()===run()!.stages.at(-1)?.node_id} busy={!!busy()||awaitingReceipt()||openingSavedStage()||sourceWasReplaced()||p.archived} receiptPending={awaitingReceipt()} executing={busy()==='execute'} sending={sending()} asking={asking()} dialogueReadOnly={sourceWasReplaced()||awaitingReceipt()} actionError={error()} onAsk={p.archived?undefined:ask} onSupplement={p.archived?undefined:supplement} onStageVersion={openVersion} onCoverage={(kind,id,opener)=>openCoverage(stage(),kind,id,opener)} onManageCapabilities={p.onManageCapabilities} onOpenKernelSettings={()=>openSettings(stage()?.config.kernel)} onAction={action} onRef={jump}
 improvementReadOnly={p.archived} onStop={p.archived?undefined:stopExecution} stopSubmitting={stopSubmitting()} onImprovementSaved={saved=>installDefinition(saved,true)} onOpenImprovedDefinition={openImprovedDefinition}/>}</Show>
 </Show></Show>
 </div></main>;
}
