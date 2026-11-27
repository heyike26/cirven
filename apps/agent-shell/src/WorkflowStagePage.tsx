import {batch,createEffect,createMemo,createSignal,createUniqueId,For,Index,Show,onCleanup,onMount,untrack} from 'solid-js';
import {ApiError,operationId} from './api';
import {approvedDeliverableDownload,draftKey,stageApi,templateDownload,type Catalog,type CoverageSelector,type DialogueReference,type ExecutionRecord,type FollowUpStatus,type FollowUpType,type ManualCheck,type NodeConfig,type RevisionBasis,type RunStage,type StageDialogue,type StageFollowUp,type StageInputSnapshot,type StageKind,type StageNode,type StageRun,type StageTemplate} from './stage-workflows';
import StageArtifact,{stageEventText,WorkflowReadingDetail,type Mark} from './StageArtifact';
import {StagePolicy} from './StageDefinitionEditor';
import WorkflowDialog,{WorkflowDraftNotice} from './WorkflowDialog';
import WorkflowDeliveryReceipt from './WorkflowDeliveryReceipt';
import WorkflowPlanningSource from './WorkflowPlanningSource';
import WorkflowTemplateInput,{type TemplateReadState} from './WorkflowTemplateInput';
import WorkflowReworkForm,{type ReworkDraft} from './WorkflowReworkForm';
import WorkflowReworkEvidence from './WorkflowReworkEvidence';
import WorkflowHardwareReadback from './WorkflowHardwareReadback';
import WorkflowRevisionDiff from './WorkflowRevisionDiff';
import WorkflowImprovement,{hasImprovementSource} from './WorkflowImprovement';
import WorkflowManualChecks,{benchPayload,manualActions,type ManualAction,type ManualDraft} from './WorkflowManualChecks';
import type {ManualFileUpload} from './workflow-manual-files';
import {WorkflowIcon} from './WorkflowIcon';
import {nativeCollaborationBridge} from './native-collaboration';
import {stageDraftSettledEvent,type StageDraftSettled} from './workflow-transition';
import './workflow-stage-detail.css';

type Tr=(zh:string,en:string)=>string;
type Draft={input:Record<string,string>;template?:StageTemplate|null;mark?:Mark;basis?:string;comment?:string;note?:string;followUp?:string;followUpType?:FollowUpType;followUpEdit?:string;question?:string;questionAnnotation?:string;questionFollowUp?:string;manual?:Record<string,ManualDraft>;rework?:ReworkDraft;reworkEdit?:string};
const topics=[
 ['background','项目背景','Project background'],['development_constraints','开发约束','Development constraints'],
 ['build_environment','编译环境','Build environment'],['runtime_environment','执行环境','Runtime environment'],
 ['code_map','代码图谱（如有）','Code map (optional)'],['other_constraints','其他约束（如有）','Other constraints (optional)']
] as const;
export default function WorkflowStagePage(p:{
 run:StageRun;node:StageNode;stage:RunStage;catalog:Catalog;t:Tr;busy:boolean;completed?:boolean;executing?:boolean;sending?:boolean;asking?:boolean;dialogueReadOnly?:boolean;actionError?:string;receiptPending?:boolean;
 onAsk?:(question:string,reference?:DialogueReference)=>Promise<boolean>;observeHistoricalDialogue?:boolean;
 onStop?:(operation:string)=>Promise<void>;stopSubmitting?:boolean;
 onSupplement?:(text:string,sourceType:FollowUpType,draftEdit?:string)=>Promise<boolean>;onStageVersion?:(id:string)=>void;
 onCoverage?:(selector:CoverageSelector,id:string,opener:HTMLButtonElement)=>void;
 inputVersions?:StageInputSnapshot[];
 onManageCapabilities:()=>void;onOpenKernelSettings?:()=>void;
 improvementReadOnly:boolean;onImprovementSaved:(definition:StageRun['definition'])=>void;onOpenImprovedDefinition:(id:string)=>void;
 onAction:(action:string,payload:Record<string,unknown>,onReceipt?:(stage:RunStage,op:string)=>void,
  pending?:{revision:number;onConflict?:()=>void;draftEdit?:string;identity?:Record<string,unknown>})=>Promise<boolean>;onRef:(kind:StageKind,id:string)=>void;
}){
 const storageKey=draftKey(p.run,p.node.id,p.stage.stage_session_id);
 const [comparison,setComparison]=createSignal<RevisionBasis>();
 const [feedbackOpen,setFeedbackOpen]=createSignal(false);
 const [approvalIssuesOpen,setApprovalIssuesOpen]=createSignal(false);
 let statusControl!:HTMLButtonElement;
 const [stopTarget,setStopTarget]=createSignal<string>();
 let stopTrigger:HTMLButtonElement|undefined,stoppedRecord:HTMLButtonElement|undefined,stopHadFocus=false;
 const [draftError,setDraftError]=createSignal('');
 function restore():Draft{try{const s=localStorage.getItem(storageKey);if(s){const parsed=JSON.parse(s);if(!parsed.input||typeof parsed.input!=='object')throw Error();return parsed;}}catch{setDraftError(p.t('此节点的本机草稿无法读取。','This stage’s local draft could not be read.'));}
  return {input:Object.fromEntries(Object.entries(p.stage.user_input??{}).filter((x):x is [string,string]=>typeof x[1]==='string'))};}
 const purposeName=createUniqueId();
 type Panel='tools'|'settings'|'input'|'activity'|'basis'|'execution'|'comments'|'rework'|'manual';
 const [panel,setPanel]=createSignal<Panel|null>(null),[compactTools,setCompactTools]=createSignal(false);
 const [panelFromTools,setPanelFromTools]=createSignal(false);
 let toolsButton:HTMLButtonElement|undefined;
 const panelLabels:Record<Panel,[string,string]>={tools:['本步工具','Stage tools'],settings:['本步配置','Stage settings'],input:['阶段输入与模板','Stage inputs and template'],activity:['笔记与执行记录','Notes and activity'],basis:['本步依据','Approved inputs'],execution:['本次执行记录','Execution record'],comments:['审核批注','Review comments'],rework:['返回开发返工','Return to development'],manual:['人工实测','Manual observations']};
 const [recordTarget,setRecordTarget]=createSignal<string>(),[executionRecord,setExecutionRecord]=createSignal<ExecutionRecord>();
 const [recordError,setRecordError]=createSignal('');
 const [recordRetry,retryRecord]=createSignal(0);
 const executionEvidence=createMemo(()=>new Map(executionRecord()?.evidence.map(item=>[item.id,item])));
 const liveRecordEpoch=createMemo(()=>recordTarget()===p.stage.operation_id?
  JSON.stringify([p.stage.status,p.stage.progress?.at,p.stage.execution_state]):'');
 let retryControl:HTMLButtonElement|undefined;
 let recordReturn:{index:number;operation:string;offset:number;scroll:number}|undefined;
 createEffect(()=>{
  const op=recordTarget();
  if(panel()!=='execution'||!op||!untrack(()=>p.stage.stage_session_id))return;
  recordRetry();
  liveRecordEpoch();
  const controller=new AbortController();
  if(untrack(executionRecord)?.operation_id!==op){setExecutionRecord(undefined);setRecordError('');}
  void untrack(()=>stageApi.executionRecord(p.run,p.stage,op,controller.signal))
   .then(record=>{if(!controller.signal.aborted&&record.operation_id===op){setExecutionRecord(record);setRecordError('');}})
   .catch(e=>{if(!controller.signal.aborted)setRecordError(e instanceof Error?e.message:String(e));});
  onCleanup(()=>controller.abort());
 });
 const [followUpsOpen,setFollowUpsOpen]=createSignal(false);
 const [conversationOpen,setConversationOpen]=createSignal(false),[askingMode,setAskingMode]=createSignal(false);
 const conversationId=createUniqueId();
 let dialogueInput:HTMLTextAreaElement|undefined,supplementInput:HTMLTextAreaElement|undefined;
 let dialogueTrigger:HTMLButtonElement|undefined,conversationOpener:HTMLElement|undefined;
 const bridge=nativeCollaborationBridge(globalThis);
 const [draft,setDraft]=createSignal<Draft>(restore());
 const [marking,setMarking]=createSignal(false);
 const [templateRead,setTemplateRead]=createSignal<TemplateReadState>();
 const parseBusy=()=>templateRead()?.kind==='reading';
 let templateRequest:AbortController|undefined;
 const reworkDraft=()=>{const value=draft().rework;return value&&typeof value.reason==='string'&&
  (value.target===undefined||typeof value.target==='string')?value:{reason:''};};
 const canRework=()=>p.node.kind==='testing'&&active()&&p.run.definition.nodes.some(n=>n.kind==='development'&&
  p.run.stages.some(s=>s.node_id===n.id&&s.status==='approved'))&&(p.stage.status==='failed'||
  p.stage.artifact?.items.some(x=>x.status==='failed'||x.status==='blocked')||
  p.stage.verified_evidence.some(r=>r.kind==='manual_check'&&'status' in r&&r.status==='blocked'));
 const [policy,setPolicy]=createSignal<NodeConfig|null>(null);
 let disposed=false,commentBox:HTMLTextAreaElement|undefined,pageRoot!:HTMLElement;
 onMount(()=>{
  const container=pageRoot.closest<HTMLElement>('.wf-studio')??pageRoot;
  const update=()=>setCompactTools(container.clientWidth<=749);
  const observer=new ResizeObserver(update);observer.observe(container);update();onCleanup(()=>observer.disconnect());
 });
 onCleanup(()=>{disposed=true;templateRequest?.abort();});
 onMount(()=>{
  const stage_session_id=p.stage.stage_session_id;
  if(!stage_session_id||!bridge?.setWorkflowOrbScope)return;
  const scope={project_id:p.run.project_id,run_id:p.run.id,node_id:p.node.id,stage_session_id};
  const unsubscribe=bridge.onWorkflowConversationOpen?.(target=>{
   if(target.stage_session_id!==scope.stage_session_id||target.node_id!==scope.node_id||target.run_id!==scope.run_id||target.project_id!==scope.project_id)return;
   if(target.intent==='annotate'&&reviewing()&&editable()){
    if(conversationOpen()&&!draft().mark)closeConversation(false);
    if(!draft().mark)setMarking(true);
    else commentBox?.focus({preventScroll:true});
   }else openConversation('question');
  });
  void bridge.setWorkflowOrbScope(scope).catch(()=>{});
  onCleanup(()=>{unsubscribe?.();void bridge.setWorkflowOrbScope?.(null).catch(()=>{});});
 });
 onMount(()=>{
  function settled(event:Event){
   const {key,field,edit}=(event as CustomEvent<StageDraftSettled>).detail;
   const editField=`${field}Edit` as const;
   if(key===storageKey&&draft()[editField]===edit)setDraft(previous=>({...previous,[field]:undefined,[editField]:undefined}));
  }
  window.addEventListener(stageDraftSettledEvent,settled);
  onCleanup(()=>window.removeEventListener(stageDraftSettledEvent,settled));
 });
 function save(patch:Partial<Draft>){
  const next={...draft(),...patch};
  if('followUp' in patch||'followUpType' in patch)next.followUpEdit=operationId();
  if('rework' in patch)next.reworkEdit=operationId();
  setDraft(next);
  try{localStorage.setItem(storageKey,JSON.stringify(next));setDraftError('');}
  catch{setDraftError(p.t('本机未能保存草稿，请保留此页。','Local draft saving failed. Keep this page open.'));}
  return next;
 }
 function field(key:string,value:string){save({input:{...draft().input,[key]:value}});}
 const active=()=>p.run.active_node_id===p.node.id;
 const editable=()=>active()&&!['approved','running','pending'].includes(p.stage.status)&&!p.busy;
 const reviewing=()=>p.stage.status==='review'&&active();
 const interrupted=()=>p.stage.status==='running'&&p.stage.execution_state==='interrupted';
 const annotationsById=createMemo(()=>new Map(p.stage.annotations.map(item=>[item.id,item])));
 const notesById=createMemo(()=>new Map(p.stage.notes.map(item=>[item.id,item])));
 // Activity is append-only within this stage session. Prepending a new row in the
 // display keeps every earlier entry at its original key, even when timestamps tie.
 const activityOrder=createMemo(()=>Array.from({length:p.stage.log.length},(_,i)=>p.stage.log.length-1-i));
 const openAnnotations=()=>p.stage.annotations.filter(a=>a.status==='open');
 const followUps=()=>p.stage.follow_ups??[];
 const pendingFollowUps=()=>followUps().filter(item=>!item.processed_by&&!item.withdrawn_at);
 const followUpType=():FollowUpType=>p.node.kind==='requirements'&&draft().followUpType==='requirement'?'requirement':'instruction';
 const inFlightFollowUps=()=>p.stage.status==='running'?
  p.stage.log.findLast(event=>event.event==='started'&&event.operation_id===p.stage.operation_id)?.follow_up_ids:undefined;
 function followUpStatus(item:StageFollowUp):FollowUpStatus{
  if(item.withdrawn_at)return 'withdrawn';
  if(item.processed_by)return 'processed';
  const inFlight=inFlightFollowUps();
  return Array.isArray(inFlight)&&inFlight.includes(item.id)?'running':'queued';
 }
 function statusText(status:FollowUpStatus){
  return {
   queued:p.t('待随下次执行处理','Queued for the next run'),
   running:p.t('本次执行中','Included in the current run'),
   processed:p.t('已纳入执行','Included in a run'),
   withdrawn:p.t('已撤回','Withdrawn'),
  }[status];
 }
 function executionSummary(item:StageFollowUp){
  const event=item.processed_by&&p.stage.log.findLast(event=>event.event==='finished'&&event.operation_id===item.processed_by);
  return event&&typeof event.reply==='string'?event.reply:'';
 }
 const executing=()=>p.executing||p.stage.status==='running';
 const activeQuestion=()=>p.stage.annotations.find(item=>item.id===draft().questionAnnotation);
 const activeFollowUp=()=>followUps().find(item=>item.id===draft().questionFollowUp);
 const conversationReference=createMemo(()=>{
  const mark=draft().mark;
  if(mark&&reviewing())return mark.anchor?.quote
   ?{label:p.t('引用原文','Selected text'),text:mark.anchor.quote,removable:false}:undefined;
  if(!askingMode()||!p.onAsk)return;
  const followUp=activeFollowUp(),annotation=activeQuestion();
  if(followUp)return {label:p.t('引用补充要求','Referenced request')+(followUp.source_id?' · '+followUp.source_id:''),
   text:followUp.text,removable:true};
  if(annotation)return {label:p.t('引用批注','Referenced comment')+' · '+annotation.target,
   text:annotation.comment,removable:true};
 });
 const [historicalReplies,setHistoricalReplies]=createSignal(new Map<string,StageDialogue>());
 createEffect(()=>{
  if(!p.observeHistoricalDialogue)return;
  const original=p.stage;
  const {stage_session_id,node_id}=original;
  const pending=new Map(original.dialogue?.filter(turn=>turn.status==='pending').map(turn=>[turn.id,turn]));
  if(!stage_session_id||!pending.size)return;
  const {id,project_id,conversation_id}=untrack(()=>p.run);
  const control=new AbortController();
  let timer:ReturnType<typeof setTimeout>;
  async function checkReplies(){
   try{
    const observed=await stageApi.list({project_id,conversation_id:stage_session_id!},control.signal);
    if(control.signal.aborted||p.stage!==original||observed.owner_conversation_id!==conversation_id)return;
    const source=observed.runs.find(run=>run.id===id&&run.project_id===project_id&&run.conversation_id===conversation_id);
    if(!source)return;
    const exact=(stage:RunStage)=>stage.stage_session_id===stage_session_id&&stage.node_id===node_id;
    const latest=observed.historical_stage??source.stages.find(exact);
    if(!latest||!exact(latest))return;
    const replies=new Map<string,StageDialogue>();
    for(const turn of latest.dialogue??[]){
     const before=pending.get(turn.id);
     if(turn.status==='pending'||!before||before.question!==turn.question||before.at!==turn.at||
        before.annotation_id!==turn.annotation_id||before.follow_up_id!==turn.follow_up_id)continue;
     pending.delete(turn.id);
     replies.set(turn.id,turn);
    }
    if(replies.size)setHistoricalReplies(previous=>new Map([...previous,...replies]));
   }catch(error){
    if(error instanceof ApiError&&[401,403,404,409].includes(error.status))return;
   }
   if(!control.signal.aborted&&pending.size)timer=setTimeout(checkReplies,1400);
  }
  timer=setTimeout(checkReplies,650);
  onCleanup(()=>{control.abort();clearTimeout(timer);});
 });
 const discussion=createMemo(()=>new Map([
  ...(p.stage.dialogue??[]).map(turn=>({id:'turn:'+turn.id,at:turn.at,turn:historicalReplies().get(turn.id)??turn} as const)),
  ...p.stage.log.filter(event=>event.event==='finished'&&(event.reply||event.questions||event.error)).slice(-12)
   .map(event=>({id:'attempt:'+String(event.operation_id??event.at),at:event.at,event} as const)),
 ].sort((a,b)=>a.at.localeCompare(b.at)).map(item=>[item.id,item] as const)));
 let conversationScroll:HTMLDivElement|undefined,followLatest=true;
 function scrollToLatest(){
  if(conversationScroll&&followLatest)conversationScroll.scrollTop=conversationScroll.scrollHeight;
 }
 function scrollWithinConversation(target:HTMLElement){
  const viewport=conversationScroll;
  if(!viewport?.contains(target))return;
  const bounds=target.getBoundingClientRect(),frame=viewport.getBoundingClientRect();
  const top=frame.top+viewport.clientTop+4,bottom=frame.top+viewport.clientTop+viewport.clientHeight-4;
  if(bounds.top<top)viewport.scrollTop+=bounds.top-top;
  else if(bounds.bottom>bottom)viewport.scrollTop+=bounds.bottom-bottom;
 }
 createEffect(()=>{
  if(!conversationOpen()||!conversationScroll)return;
  const observer=new ResizeObserver(scrollToLatest);
  observer.observe(conversationScroll);
  onCleanup(()=>observer.disconnect());
 });
 createEffect(()=>{
  if(!conversationOpen())return;
  discussion();
  if(followLatest){
   const frame=requestAnimationFrame(scrollToLatest);
   onCleanup(()=>cancelAnimationFrame(frame));
  }
 });
 function rememberConversationOpener(source?:HTMLElement){
  if(source)conversationOpener=source;
  else if(!conversationOpen())conversationOpener=document.activeElement instanceof HTMLElement?document.activeElement:undefined;
 }
 function openConversation(mode:'question'|'follow-up'=p.stage.questions.length&&!activeFollowUp()?'follow-up':'question'){
  const wasOpen=conversationOpen();
  rememberConversationOpener();
  setAskingMode(!!p.onAsk&&mode==='question');
  if(!wasOpen){
   if(mode==='question')setFollowUpsOpen(false);
   followLatest=true;
  }
  setConversationOpen(true);
  queueMicrotask(()=>{
   if(!conversationOpen())return;
   const input=draft().mark&&reviewing()?commentBox:askingMode()?dialogueInput:supplementInput;
   (input??conversationScroll?.closest('.wf-conversation-float')?.querySelector<HTMLButtonElement>('.wf-conversation-heading button'))?.focus({preventScroll:true});
  });
 }
 function closeConversation(restoreFocus=true){
  setImpactOpen(false);
  setConversationOpen(false);
  setAskingMode(false);
  const opener=conversationOpener;
  conversationOpener=undefined;
  if(restoreFocus&&opener?.isConnected&&opener!==document.body)opener.focus({preventScroll:true});
  if(restoreFocus&&document.activeElement!==opener)dialogueTrigger?.focus({preventScroll:true});
 }
 function questionFromAnnotation(id:string){
  save({questionAnnotation:id,questionFollowUp:undefined});
  openConversation('question');
 }
 function questionFromFollowUp(id:string){
  save({questionFollowUp:id,questionAnnotation:undefined});
  openConversation('question');
 }
 function revealFollowUp(id:string){
  followLatest=false;
  setFollowUpsOpen(true);
  requestAnimationFrame(()=>{
   const row=Array.from(conversationScroll?.querySelectorAll<HTMLElement>('[data-follow-up]')??[])
    .find(item=>item.dataset.followUp===id);
   if(row){row.focus({preventScroll:true});scrollWithinConversation(row);}
  });
 }
 onMount(()=>{
  function restoreFollowUp(event:Event){
   const detail=(event as CustomEvent<{id:string;asking:boolean;scroll:number;executionOpen:boolean}>).detail;
   if(!followUps().some(item=>item.id===detail.id))return;
   if(!conversationOpen())rememberConversationOpener(dialogueTrigger);
   followLatest=false;
   batch(()=>{setAskingMode(detail.asking&&!!p.onAsk);setFollowUpsOpen(true);setConversationOpen(true);});
   const row=[...conversationScroll?.querySelectorAll<HTMLElement>('[data-follow-up]')??[]].find(element=>element.dataset.followUp===detail.id);
   const execution=row?.querySelector<HTMLDetailsElement>('.wf-followup-execution');
   if(execution)execution.open=detail.executionOpen;
   const opener=row?.querySelector<HTMLButtonElement>('[data-coverage-source]');
   if(conversationScroll&&opener){
    conversationScroll.scrollTop=detail.scroll;
    scrollWithinConversation(opener);
    opener.focus({preventScroll:true});
   }
  }
  pageRoot.addEventListener('workflow-restore-follow-up',restoreFollowUp);
  onCleanup(()=>pageRoot.removeEventListener('workflow-restore-follow-up',restoreFollowUp));
 });
 async function askQuestion(){
  const {question:content='',questionAnnotation:annotation,questionFollowUp:followUp}=draft();
  if(!p.onAsk||p.asking||!content.trim())return;
  setFollowUpsOpen(false);
  followLatest=true;
  const reference=followUp?{followUpId:followUp}:annotation?{annotationId:annotation}:undefined;
  if(await p.onAsk(content.trim(),reference)&&!disposed&&content===draft().question&&annotation===draft().questionAnnotation&&followUp===draft().questionFollowUp){
   save({question:''});
  }
 }
 const amending=()=>p.stage.status==='approved'&&!!p.onSupplement;
 const [impactOpen,setImpactOpen]=createSignal(false);
 const workflowRunning=()=>p.executing||p.run.stages.some(s=>s.status==='running');
 const previousVersion=()=>p.stage.log.findLast(event=>
  (event.event==='revised'||event.event==='rework')&&typeof event.previous_session_id==='string')?.previous_session_id as string|undefined;
 const hasComparison=()=>!!p.stage.stage_session_id&&!!p.stage.artifact&&!!previousVersion();
 const canCompare=()=>hasComparison()&&!p.stage.error&&(p.stage.status==='review'||p.stage.status==='approved');
 function openComparison(){
  const stageSession=p.stage.stage_session_id;
  if(!stageSession||!canCompare())return;
  setComparison({project_id:p.run.project_id,run_id:p.run.id,node_id:p.node.id,
   stage_session_id:stageSession,revision:p.run.revision});
 }
 const impacted=()=>{
  if(!amending())return [];
  const ids=new Set([p.node.id]),names:string[]=[];
  for(const node of p.run.definition.nodes){
   if(node.id!==p.node.id&&node.inputs.some(id=>ids.has(id))){
    ids.add(node.id);
    if(p.run.stages.find(stage=>stage.node_id===node.id)?.stage_session_id)names.push(node.name);
   }
  }
  return names;
 };
 const template=()=>draft().template===undefined?p.stage.template:draft().template;
 const activePolicy=()=>policy()??p.stage.config;
 function canSavePolicy(){
  if(!editable())return false;
  if(p.stage.status==='failed')return true;
  const next=policy(),saved=p.stage.config;
  if(!next)return false;
  if(next.kernel!==saved.kernel||next.max_subagents!==saved.max_subagents)return true;
  return (['skills','tools','mcp'] as const).some(category=>{
   const before=saved.capabilities[category],after=next.capabilities[category];
   return before.length!==after.length||after.some(id=>!before.includes(id));
  });
 }
 async function parseFile(file:File){
  if(!editable())return;
  const controller=new AbortController();
  templateRequest?.abort();
  templateRequest=controller;
  setTemplateRead({kind:'reading',name:file.name});
  const current=()=>!disposed&&templateRequest===controller;
  try{
   const result=await stageApi.template({project_id:p.run.project_id,conversation_id:p.run.conversation_id},file,controller.signal);
   if(!current())return;
   batch(()=>{
    if(active()&&!['approved','pending','running'].includes(p.stage.status))save({template:result});
    setTemplateRead(undefined);
   });
  }catch(error){
   if(current())setTemplateRead({kind:'failed',name:file.name,message:error instanceof Error?error.message:String(error)});
  }finally{
   if(templateRequest===controller)templateRequest=undefined;
  }
 }
 function returnToMark(id:string){requestAnimationFrame(()=>{
  if(disposed)return;
  if(id==='summary'){
   const summary=pageRoot.querySelector<HTMLElement>('.wf-summary');
   summary?.focus({preventScroll:true});
   summary?.scrollIntoView({block:'nearest',behavior:'instant'});
  }
  else pageRoot.querySelector('.wf-artifact')?.dispatchEvent(new CustomEvent('workflow-reveal-row',{detail:{id}}));
 });}
 function revealReferencedMark(target:string){
  if(window.innerWidth<1250)closeConversation();
  returnToMark(target);
 }
 function dismissMark(){
  save({mark:undefined,comment:''});
  closeConversation();
 }
 function mark(value:Mark,source?:HTMLElement){
  rememberConversationOpener(source);
  setMarking(false);save({mark:value,basis:p.stage.operation_id});
  setAskingMode(!!p.onAsk);setConversationOpen(true);
  queueMicrotask(()=>commentBox?.focus({preventScroll:true}));
 }
 async function annotate(){const pending=draft(),target=pending.mark;if(!target||!pending.comment?.trim())return;
 if(pending.basis!==p.stage.operation_id){setDraftError(p.t('交付物已有新版本，请重新选择要批注的位置。原批注文字已保留。','The output changed. Select its new location to keep this comment.'));return;}
  await p.onAction('annotate',{...target,comment:pending.comment.trim()},(stage,op)=>{
   if(disposed||pending.mark!==draft().mark||pending.comment!==draft().comment||pending.basis!==draft().basis)return;
   const saved=stage.annotations.find(item=>item.created_operation_id===op);
   if(!saved){setDraftError(p.t('未能定位批注回执，文字仍保留。','Could not locate the saved comment. Your text is preserved.'));return;}
   save({mark:undefined,comment:'',questionAnnotation:saved.id,questionFollowUp:undefined});
   if(p.onAsk)openConversation('question');
   else returnToMark(target.target);
  });
 }
 async function sendFollowUp(){
  const content=draft().followUp??'';
  if(!p.onSupplement||!content.trim()||p.sending||(p.busy&&!executing())||(amending()&&workflowRunning()))return;
  const edit=draft().followUpEdit??save({followUp:content}).followUpEdit;
  if(await p.onSupplement(content.trim(),followUpType(),edit)&&!disposed&&conversationOpen()&&!askingMode()&&!draft().mark){
   setFollowUpsOpen(true);
   requestAnimationFrame(scrollToLatest);
  }
 }
 type ConnectorAcknowledgement={operation_id:string|null;evidence_ids:string[]};
 const [retryPrompt,setRetryPrompt]=createSignal<{ack:ConnectorAcknowledgement;annotationIds?:string[]}>();
 const pendingConnectors=()=>p.stage.connector_retry?.evidence??[];
 const retryAcknowledgement=createMemo(()=>{
  const pending=p.stage.connector_retry;
  if(pending?.evidence.length)return {operation_id:pending.operation_id,evidence_ids:pending.evidence.map(item=>item.id).sort()};
 });
 const currentRetry=()=>{
  const shown=retryPrompt(),current=retryAcknowledgement();
  if(!shown||!current||!editable()||shown.ack.operation_id!==current.operation_id)return;
  if(shown.ack.evidence_ids.length===current.evidence_ids.length&&
   shown.ack.evidence_ids.every((id,i)=>id===current.evidence_ids[i]))return shown;
 };
 createEffect(()=>{if(retryPrompt()&&!currentRetry())setRetryPrompt(undefined);});
 function submitExecution(ids?:string[],ack?:ConnectorAcknowledgement){
  if(parseBusy())return;
  return p.onAction('execute',{user_input:draft().input,template:template()??null,...(ids?{annotation_ids:ids}:{}),
   ...(ack?{connector_retry_ack:ack}:{})});
 }
 function execute(ids?:string[]){
  const ack=retryAcknowledgement();
  if(ack){setRetryPrompt({ack,annotationIds:ids});return;}
  return submitExecution(ids);
 }
 function confirmRetry(){
  const decision=currentRetry();
  if(!decision)return;
  setRetryPrompt(undefined);
  void submitExecution(decision.annotationIds,decision.ack);
 }
 const retrySelection=createMemo(()=>{
  const start=p.stage.log.findLast(event=>event.event==='started'&&event.operation_id===p.stage.operation_id);
  const ids=start?.annotation_ids;
  if(!Array.isArray(ids)||!ids.length)return undefined;
  const available=new Set(openAnnotations().map(a=>a.id));
  return ids.every((id):id is string=>typeof id==='string'&&available.has(id))?ids:undefined;
 });
 function openAttempt(op:unknown,origin?:{index:number;button:HTMLButtonElement}){
  if(typeof op!=='string'||!p.stage.stage_session_id)return;
  const body=origin?.button.closest<HTMLElement>('.wf-dialog-body');
  recordReturn=panel()==='activity'&&origin&&body?{
   index:origin.index,operation:op,scroll:body.scrollTop,
   offset:origin.button.getBoundingClientRect().top-body.getBoundingClientRect().top-body.clientTop,
  }:undefined;
  setRecordTarget(op);openPanel('execution',!!recordReturn);
 }
 async function recoverAttempt(){
  const op=p.stage.operation_id;
  if(!op||!await p.onAction('recover',{})||disposed)return;
  if(feedbackAvailable())statusControl.focus({preventScroll:true});
  openAttempt(op);
 }
 function evidenceLabel(item:ExecutionRecord['evidence'][number]){
  const labels:Record<string,[string,string]>={file:['文件版本','File version'],check:['检查','Check'],
   build:['构建','Build'],design_item:['开发项','Development item'],package:['交付包','Package'],
   connector_call:['MCP 调用','MCP call']};
  return p.t(...(labels[item.kind]??['执行记录','Recorded action']));
 }
 const recoveryHref=(id:string)=>stageApi.evidenceFileHref(p.run,p.stage.stage_session_id!,id);
 const fileRecords=()=>[p.stage,...p.run.stages.filter(s=>s.status==='approved'&&p.node.inputs.includes(s.node_id))].flatMap(s=>s.verified_evidence??[]).filter(e=>e.kind==='file');
 const filenameHref=(id:string)=>fileRecords().some(e=>e.id===id)?'/api/stage-workflows/runs/'+encodeURIComponent(p.run.id)+'/files/'+encodeURIComponent(id)+'?'+new URLSearchParams({project_id:p.run.project_id,conversation_id:p.run.conversation_id}):undefined;
 const currentProgress=()=>{
  const {status,progress,error,approval_issues:issues}=p.stage;
  if(status==='running')return !interrupted()&&progress&&Number.isFinite(progress.total)&&progress.total>0?progress:null;
  if(status!=='approved'&&(status!=='review'||error||issues?.length!==0))return null;
  const source=p.node.kind==='development'?'design':p.node.kind==='testing'?'cases':undefined;
  if(!source)return null;
  const total=new Set(sameStage(source).flatMap(node=>inputStage(node.id)?.artifact?.items.map(row=>row.id)??[])).size;
  return total?{completed:total,total,message:''}:null;
 };
 const sameStage=(kind:StageKind)=>p.run.definition.nodes.filter(n=>n.kind===kind&&p.node.inputs.includes(n.id));
 const inputStage=(id:string)=>(p.inputVersions??p.run.stages).find(s=>s.node_id===id&&s.status==='approved');
 const approvedInputs=createMemo(()=>new Map(p.node.inputs.flatMap(id=>{
  const node=p.run.definition.nodes.find(n=>n.id===id),stage=inputStage(id);
  return node&&stage?[[id,{node,stage}] as const]:[];
 })));
 const hasBasis=()=>['development','delivery','custom','init'].includes(p.node.kind)&&approvedInputs().size>0;
 const upstreamCases=()=>sameStage('cases').flatMap(n=>inputStage(n.id)?.artifact?.items??[]);
 async function readScenarioBasis(row:string,signal:AbortSignal){
  const {stage_session_id,node_id,operation_id}=p.stage;
  if(!stage_session_id)throw new Error('Design session unavailable');
  const record=await stageApi.coverageRecord({project_id:p.run.project_id,run_id:p.run.id},
   {stage_session_id,node_id,operation_id,row_id:row},signal);
  const items=(kind:StageKind)=>record.inputs.filter(source=>p.run.definition.nodes.some(node=>node.id===source.node_id&&node.kind===kind))
   .flatMap(source=>source.artifact.items);
  return {cases:items('cases'),requirements:items('requirements')};
 }
 const manualOptions=createMemo(()=>p.node.kind==='testing'?manualActions(upstreamCases()):[]);
 const manualRecords=()=>p.stage.verified_evidence.filter((item):item is ManualCheck=>item.kind==='manual_check');
 const manualOnly=()=>manualOptions().length>0&&upstreamCases().every(row=>Array.isArray(row.actions)&&
  row.actions.length>0&&row.actions.every(action=>action.method==='manual_observation'));
 const canRecordManual=()=>active()&&['ready','review'].includes(p.stage.status)&&!p.sending;
 async function recordManual(action:ManualAction,value:ManualDraft,attachments:ManualFileUpload[]){
  const revision=value.pendingRevision??p.run.revision;
  const pending=value.pendingRevision===revision?value:{...value,pendingRevision:revision};
  if(pending!==value)save({manual:{...draft().manual,[action.key]:pending}});
  const current=()=>!disposed&&draft().manual?.[action.key]===pending;
  const fields={case_id:action.caseId,action_id:action.id,status:pending.status,
   environment:pending.environment,observation:pending.observation,bench:benchPayload(pending.bench)};
  return p.onAction('record_manual_check',attachments.length?{...fields,attachments}:fields,()=>{
    if(!current())return;
    const manual={...draft().manual};delete manual[action.key];save({manual});
  },{revision,identity:attachments.length?{...fields,attachments:attachments.map(({id,name})=>({id,name}))}:undefined,onConflict:()=>{
    if(current())save({manual:{...draft().manual,[action.key]:{...pending,pendingRevision:undefined}}});
  }});
 }
 const workStatus=()=>p.stage.status;
 const approvalIssues=()=>reviewing()?p.stage.approval_issues??[]:[];
 const hasApprovalIssues=()=>approvalIssues().length>0;
 const approvalIssueTitle=()=>p.t('审核待办','Review tasks');
 const feedbackAvailable=()=>!!p.stage.artifact&&(workStatus()==='failed'||!!p.stage.error||p.stage.questions.length>0);
 const canOpenStageStatus=()=>hasApprovalIssues()||feedbackAvailable();
 createEffect(()=>{if(!feedbackAvailable())setFeedbackOpen(false);});
 const stopping=()=>p.stage.status==='running'&&!!p.stage.stop_request&&!interrupted();
 const statusLabel=()=>interrupted()?p.t('执行中断','Interrupted'):stopping()?p.t('正在停止','Stopping')
  :p.stage.stop_request?.status==='stopped'?p.t('已停止','Stopped')
  :p.stage.stop_request?.status==='unconfirmed'?p.t('待核查','Check required')
  :p.stage.artifact&&p.stage.error&&workStatus()!=='failed'?p.t('查看反馈','View feedback'):stageEventText(workStatus(),p.t);
 createEffect(()=>{
  if(p.stage.status==='running'||!stopHadFocus)return;
  stopHadFocus=false;
  if(document.activeElement===stopTrigger||document.activeElement===document.body)
   queueMicrotask(()=>{if(!disposed&&(document.activeElement===document.body||document.activeElement===stopTrigger))
    (feedbackAvailable()?statusControl:stoppedRecord??pageRoot.querySelector<HTMLElement>('h1'))?.focus({preventScroll:true});});
 });
 function confirmStop(){
  const op=stopTarget();
  if(!op||p.stage.operation_id!==op||workStatus()!=='running'||stopping()||interrupted())return;
  stopHadFocus=true;
  setStopTarget(undefined);
  void p.onStop?.(op);
 }
 const progressLabel=()=>p.node.kind==='testing'?p.t('测试进度','Test progress'):p.t('执行进度','Execution progress');
 const acceptsTemplate=()=>['requirements','alternatives','cases','design','custom'].includes(p.node.kind);
 const inputIsPrimary=()=>active()&&!p.stage.artifact&&(p.stage.status==='needs_input'||
  p.stage.status==='running'&&p.stage.questions.length>0||
  ['init','requirements'].includes(p.node.kind)&&['ready','running'].includes(p.stage.status));
 const kernelIssue=()=>/内核|网关|模型|连接|kernel|gateway|model|connect|credential/i.test(p.stage.error??'');
 function engineName(kernel:ExecutionRecord['kernel']){
  if(kernel==='claude-code')return 'Claude Code';
  if(kernel==='opencode')return 'OpenCode';
  return p.t('原内核未记录','Original engine not recorded');
 }
 const failureOrigin=()=>{
  const origin=p.stage.execution_origin;
  if(origin&&origin.operation_id===p.stage.operation_id)return origin;
 };
 const stageTitles:Record<StageKind,[string,string]>={
  init:['项目要点','Project brief'],requirements:['包需求','Requirements'],alternatives:['方案比较','Design decisions'],
  cases:['用例明细','Case details'],design:['设计文档','Design documents'],development:['开发项','Development items'],
  testing:['测试结果','Test results'],delivery:['交付文件','Deliverables'],custom:['阶段交付物','Stage output']
 };
 const icons:Record<StageKind,string>={init:'target',requirements:'list',alternatives:'branch',cases:'clipboard',design:'layers',development:'code',testing:'activity',delivery:'box',custom:'file'};
 function StageInput(){
  return <div class="wf-input-panel">
   <Show when={p.node.kind==='init'}>
    <div class="wf-initial-inputs"><For each={topics.slice(0,4)}>{([key,zh,en])=><label>{p.t(zh,en)}<textarea rows={key==='background'?3:2} disabled={!editable()} value={draft().input[key]??''} onInput={e=>field(key,e.currentTarget.value)}/></label>}</For></div>
    <details class="wf-optional-topics" open={untrack(()=>!!(draft().input.code_map||draft().input.other_constraints))}><summary>{p.t('代码图谱与其他约束（可选）','Code map and other constraints (optional)')}</summary><div class="wf-initial-inputs"><For each={topics.slice(4)}>{([key,zh,en])=><label>{p.t(zh,en)}<textarea rows="2" disabled={!editable()} value={draft().input[key]??''} onInput={e=>field(key,e.currentTarget.value)}/></label>}</For></div></details>
   </Show>
   <Show when={p.node.kind==='requirements'}><label>{p.t('用户需求 UR','User requests (UR)')}<textarea rows="4" disabled={!editable()} value={draft().input.ur??''} onInput={e=>field('ur',e.currentTarget.value)} placeholder={p.t('用自己的话写下需求，每行一项。','Describe your needs in your own words, one per line.')}/></label></Show>
 <Show when={acceptsTemplate()}><WorkflowTemplateInput template={template()} reader={templateRead()} editable={editable()} t={p.t}
  onChoose={file=>void parseFile(file)} onRemove={()=>{
   if(!editable()||parseBusy())return;
   batch(()=>{save({template:null});setTemplateRead(undefined);});
  }}/></Show>
 </div>;
 }
 function openPanel(next:Panel,fromMenu=false){
  batch(()=>{
   if(!panel())setPanelFromTools(next==='tools');
   setPanel(next);
  });
  if(fromMenu)queueMicrotask(()=>{
   if(panel()!==next||disposed)return;
   const dialog=pageRoot.querySelector('dialog[open]');
   const body=dialog?.querySelector<HTMLElement>('.wf-dialog-body');
   if(body)body.scrollTop=0;
   if(!dialog?.contains(document.activeElement))
    dialog?.querySelector<HTMLButtonElement>('.wf-dialog-heading button')?.focus({preventScroll:true});
  });
 }
 function returnToTools(){
  const previous=panel();
  const tool=previous==='execution'&&recordReturn?'activity':previous;
  recordReturn=undefined;
  setPanel('tools');
  queueMicrotask(()=>{
   if(disposed||panel()!=='tools')return;
   pageRoot.querySelector<HTMLButtonElement>(`dialog[open] [data-stage-tool="${tool}"]`)?.focus({preventScroll:true});
  });
 }
 function closePanel(){
  const wasExecution=panel()==='execution';
  if(wasExecution&&recordReturn){
   const origin=recordReturn;recordReturn=undefined;setPanel('activity');
   queueMicrotask(()=>{
    if(disposed||panel()!=='activity')return;
    const dialog=pageRoot.querySelector('dialog[open]');
    const body=dialog?.querySelector<HTMLElement>('.wf-dialog-body');
    if(!body)return;
    const indexed=body.querySelector<HTMLButtonElement>(`[data-execution-event="${origin.index}"]`);
    const button=indexed?.dataset.executionRecord===origin.operation?indexed:
     body.querySelector<HTMLButtonElement>(`[data-execution-record="${CSS.escape(origin.operation)}"]`);
    if(button?.dataset.executionRecord!==origin.operation){
     body.scrollTo({top:origin.scroll,behavior:'instant'});
     dialog?.querySelector<HTMLButtonElement>('.wf-dialog-heading button')?.focus({preventScroll:true});
     return;
    }
    const bodyTop=body.getBoundingClientRect().top+body.clientTop;
    const bounds=button.getBoundingClientRect();
    const visibleOffset=Math.max(0,Math.min(origin.offset,body.clientHeight-bounds.height));
    const top=body.scrollTop+bounds.top-bodyTop-visibleOffset;
    const log=button.closest<HTMLElement>('.wf-log');
    if(log&&top>body.scrollHeight-body.clientHeight){
     // A short list had empty space below the selected entry. Keep that space
     // on this return so the browser can scroll to the original visible position.
     const end=body.scrollTop+log.getBoundingClientRect().bottom-bodyTop;
     const padding=Number.parseFloat(getComputedStyle(body).paddingBlockEnd);
     log.style.paddingBlockEnd=`${Math.max(0,Math.ceil(top+body.clientHeight-end-padding))}px`;
    }
    body.scrollTo({top,behavior:'instant'});
    button.focus({preventScroll:true});
   });
   return;
  }
  setPanel(null);
  requestAnimationFrame(()=>{
   if(disposed)return;
   const focus=document.activeElement as HTMLElement|null;
   if(!focus||focus===document.body||!focus.getClientRects().length)
    ((wasExecution?retryControl:undefined)??(compactTools()?toolsButton:pageRoot.querySelector<HTMLButtonElement>('.wf-stage-tools button')))?.focus({preventScroll:true});
  });
 }
 createEffect(()=>{if(p.receiptPending&&panel()==='rework')closePanel();});
 function ToolChoices(choice:{menu:boolean}){
  return <>
   <Show when={canInput()}><button type="button" data-stage-tool="settings" onClick={()=>openPanel('settings',choice.menu)}><WorkflowIcon name="sliders" size={16}/>{p.t(...panelLabels.settings)}</button>
    <Show when={(p.node.kind==='init'||acceptsTemplate())&&!inputIsPrimary()}><button type="button" data-stage-tool="input" onClick={()=>openPanel('input',choice.menu)}><WorkflowIcon name="pen" size={16}/>{p.t(...panelLabels.input)}</button></Show></Show>
   <Show when={hasBasis()}><button type="button" data-stage-tool="basis" aria-haspopup="dialog" onClick={()=>openPanel('basis',choice.menu)}><WorkflowIcon name="layers" size={16}/>{p.t('查看依据','View approved inputs')}</button></Show>
   <Show when={manualOptions().length}><button type="button" data-stage-tool="manual" aria-haspopup="dialog" onClick={()=>openPanel('manual',choice.menu)}><WorkflowIcon name="check" size={16}/>{p.t(...panelLabels.manual)}</button></Show>
   <Show when={p.stage.artifact&&(!!p.onAsk||p.stage.annotations.length)}><button type="button" data-stage-tool="comments" aria-haspopup="dialog" onClick={()=>openPanel('comments',choice.menu)}><WorkflowIcon name="message" size={16}/>{p.t(...panelLabels.comments)}</button></Show>
   <button type="button" data-stage-tool="activity" onClick={()=>openPanel('activity',choice.menu)}><WorkflowIcon name="activity" size={16}/>{p.t(...panelLabels.activity)}</button>
  </>;
 }
 function BasisEntries(){
  return <For each={[...approvedInputs().keys()]}>{id=>
   <Show when={approvedInputs().get(id)}>{entry=>
    <div class="wf-basis-item"><WorkflowIcon name="file" size={18}/>
     <div><strong>{entry().node.name}</strong><Show when={entry().stage.artifact?.summary}>{summary=><p>{summary()}</p>}</Show></div>
     <Show when={entry().stage.artifact?.items[0]}>{row=>
      <button type="button" onClick={()=>{setPanel(null);p.onRef(entry().node.kind,row().id);}}>{p.t('查看','View')}<WorkflowIcon name="arrow" size={15}/></button>
     }</Show>
    </div>
   }</Show>
  }</For>;
 }
 function openSettings(){setPanel(null);p.onOpenKernelSettings?.();}
 function RunFeedback(){
  return <>
   <Show when={workStatus()==='failed'}><section class="wf-stage-failure" aria-label={p.t('执行故障','Execution issue')}><span class="wf-failure-icon"><WorkflowIcon name="alert" size={21}/></span><div>
    <h2>{p.stage.stop_request?.status==='unconfirmed'?p.t('停止结果待核查','Check whether execution stopped'):p.t('本次执行已停止','This attempt stopped')}</h2><p role="alert">{p.stage.error||p.t('此节点执行失败。','This stage failed.')}</p>
    <div class="wf-stage-run-meta"><span><WorkflowIcon name="terminal" size={15}/>{engineName(failureOrigin()?.kernel)}</span>
     <Show when={failureOrigin()?.at}>{at=><time dateTime={at()}>{new Date(at()).toLocaleString()}</time>}</Show>
    </div>
    <div class="wf-actions">
     <Show when={active()&&kernelIssue()&&p.onOpenKernelSettings}><button type="button" class="wf-primary" onClick={openSettings}><WorkflowIcon name="settings" size={16}/>{p.t('检查内核连接','Check engine connection')}</button></Show>
     <Show when={p.stage.operation_id}><button ref={stoppedRecord} type="button" onClick={()=>openAttempt(p.stage.operation_id)}><WorkflowIcon name="activity" size={16}/>{p.t('查看本次记录','View this execution')}</button></Show>
     <Show when={active()}><button ref={retryControl} type="button" aria-haspopup={retryAcknowledgement()?'dialog':undefined} classList={{'wf-primary':!kernelIssue()||!p.onOpenKernelSettings}} disabled={!editable()||parseBusy()||openAnnotations().length>0&&!retrySelection()} onClick={()=>void execute(retrySelection())}><WorkflowIcon name="play" size={16}/>{retrySelection()?p.t('重试原批注','Retry selected comments'):p.t('重试本步','Retry stage')}</button></Show>
    </div></div></section></Show>
   <Show when={p.stage.questions.length}><section class="wf-questions"><strong>{p.t('请补充以下信息','Clarification needed')}</strong><ul><For each={p.stage.questions}>{q=><li>{q}</li>}</For></ul></section></Show>
   <Show when={p.stage.error&&workStatus()!=='failed'}><p class="wf-error" role="alert">{p.stage.error}</p></Show>
  </>;
 }
 const canInput=()=>active()&&workStatus()!=='approved';
 const canSupplement=()=>!!p.onSupplement&&(active()||amending());
 function FollowUpList(){
  return <Show when={followUps().length}><details class="wf-followups" open={followUpsOpen()}
   onToggle={event=>setFollowUpsOpen(event.currentTarget.open)}>
   <summary>{p.t('补充要求','Follow-up requests')} · {followUps().length}<Show when={pendingFollowUps().length}> · {pendingFollowUps().length} {p.t('待处理','pending')}</Show></summary>
   <ul><Index each={followUps()}>{item=><li data-follow-up={item().id} tabIndex={-1}>
    <div class="wf-followup-status">
     <Show when={item().source_id}><strong class="wf-index-id">{item().source_id}</strong></Show>
     <small>{statusText(followUpStatus(item()))}</small>
    </div>
    <p>{item().text}</p>
    <Show when={executionSummary(item())}>{summary=><details class="wf-followup-execution">
     <summary onClick={event=>{
      const details=event.currentTarget.parentElement as HTMLDetailsElement;
      if(details.open)return;
      requestAnimationFrame(()=>{
       if(!details.isConnected||!details.open)return;
       const summary=details.querySelector('p');
       if(summary)scrollWithinConversation(summary);
      });
     followLatest=false;
     }}>{p.t('对应执行摘要','Associated run summary')}</summary><p>{summary()}</p>
    </details>}</Show>
    <div class="wf-followup-actions">
     <Show when={p.onAsk&&!draft().mark}><button type="button" class="wf-link" onClick={()=>questionFromFollowUp(item().id)}>{p.t('追问这条','Ask about this request')}</button></Show>
     <Show when={p.node.kind==='requirements'&&p.onCoverage&&item().source_id}>{id=>
      <button type="button" class="wf-link" data-coverage-source={id()} onClick={event=>p.onCoverage?.('ur',id(),event.currentTarget)}>{p.t('查看覆盖','View coverage')}</button>
     }</Show>
     <Show when={active()&&!executing()&&followUpStatus(item())==='queued'}><button type="button" class="wf-link" disabled={!editable()||!!p.sending} onClick={()=>void p.onAction('withdraw_supplement',{id:item().id})}>{p.t('撤回','Withdraw')}</button></Show>
    </div>
   </li>}</Index></ul>
  </details></Show>;
 }
 function SupplementForm(){
  return <form class="wf-composer-form" onSubmit={e=>{e.preventDefault();void sendFollowUp();}}>
    <Show when={p.node.kind==='requirements'}>
     <div class="wf-followup-purpose" role="group" aria-label={p.t('输入用途','Input purpose')}>
      <label><input type="radio" name={purposeName} checked={followUpType()==='instruction'} disabled={p.sending} onChange={()=>save({followUpType:'instruction'})}/>{p.t('修改说明','Revision note')}</label>
      <label><input type="radio" name={purposeName} checked={followUpType()==='requirement'} disabled={p.sending} onChange={()=>save({followUpType:'requirement'})}/>{p.t('新增需求','New requirement')}</label>
     </div>
    </Show>
    <label>{followUpType()==='requirement'?p.t('用户原始需求','Original user requirement'):amending()?p.t('修改这一版成果','Revise this approved output'):p.stage.status==='needs_input'?p.t('回答与补充','Answers and requests'):p.t('补充本步要求','Add a request for this stage')}
     <textarea ref={supplementInput} rows="2" maxLength="4000" value={draft().followUp??''} onInput={e=>save({followUp:e.currentTarget.value})}
      placeholder={p.t('说明你希望补充或修改的内容','Describe what to add or change')}/>
    </label>
    <div class="wf-follow-up-footer">
     <small class="wf-follow-up-status" role="status">{p.sending?p.t('正在发送…','Sending…'):amending()
      ?workflowRunning()?p.t('执行期间可以继续写草稿。','You can keep drafting while the workflow runs.')
       :impacted().length?p.t('将重新打开 '+impacted().length+' 个下游阶段。','Reopens '+impacted().length+' downstream stages.')
       :p.t('创建新版本并保留原版。','Creates a new version and keeps the original.')
      :executing()?p.t('本次继续执行，下次处理补充。','This run continues. Requests apply next time.')
       :followUpType()==='requirement'?p.t('登记后纳入本阶段审批。','Registered requirements are included in this review.')
       :p.t('下次执行时处理补充。','Requests apply to the next run.')}</small>
     <div class="wf-follow-up-actions">
      <Show when={amending()&&impacted().length}><button type="button" class="wf-link wf-revision-impact-trigger" aria-haspopup="dialog"
       onClick={()=>setImpactOpen(true)}>{p.t('查看影响范围','View revision impact')}</button></Show>
      <button class="wf-primary" type="submit" aria-disabled={p.sending||undefined}
       disabled={(p.busy&&!executing()&&!p.sending)||(amending()&&workflowRunning())||!draft().followUp?.trim()}><WorkflowIcon name="arrow" size={15}/>{amending()?p.t('创建修订','Create revision'):followUpType()==='requirement'?p.t('登记需求','Register requirement'):p.t('发送要求','Send request')}</button>
     </div>
    </div>
   </form>;
 }
 return <article ref={pageRoot} class="wf-page wf-stage-detail" data-phase={p.node.kind} data-stage-status={workStatus()}>
  <header class="wf-heading"><div class="wf-stage-identity"><span class="wf-stage-symbol" aria-hidden="true"><WorkflowIcon name={icons[p.node.kind]} size={23}/></span><div><span class="wf-eyebrow">{p.t('步骤','Step')} {p.run.definition.nodes.findIndex(n=>n.id===p.node.id)+1} / {p.run.stages.length}</span><h1 tabindex="-1">{p.node.name}</h1></div></div>
   <div class="wf-heading-state">
    <button ref={statusControl} type="button" class="wf-status wf-heading-status"
     data-status={hasApprovalIssues()?'review-tasks':interrupted()||p.stage.error?'failed':workStatus()}
     data-clickable={canOpenStageStatus()}
     tabIndex={canOpenStageStatus()?0:-1} aria-disabled={!canOpenStageStatus()} aria-haspopup={canOpenStageStatus()?'dialog':undefined}
     title={hasApprovalIssues()?approvalIssueTitle():statusLabel()} onClick={()=>{
      if(hasApprovalIssues())setApprovalIssuesOpen(true);
      else if(feedbackAvailable())setFeedbackOpen(true);
     }}>
     <span role="status">{hasApprovalIssues()?approvalIssueTitle():statusLabel()}</span>
     <Show when={canOpenStageStatus()}><WorkflowIcon name="chevron" size={12}/></Show>
    </button>
    <div class="wf-heading-progress" title={currentProgress()?.message||progressLabel()}>
     <Show when={currentProgress()}>{value=><>
      <progress aria-label={progressLabel()} value={value().completed} max={value().total}/>
      <span aria-hidden="true">{value().completed} / {value().total}</span>
     </>}</Show>
    </div>
   </div>
   <WorkflowDraftNotice message={conversationOpen()||panel()||feedbackOpen()&&feedbackAvailable()?'':draftError()}/>
  </header>
  <div class="wf-stage-tools"><Show when={compactTools()} fallback={<ToolChoices menu={false}/>}>
   <button ref={toolsButton} type="button" aria-haspopup="dialog" onClick={()=>openPanel('tools')}><WorkflowIcon name="sliders" size={15}/>{p.t(...panelLabels.tools)}<WorkflowIcon name="chevron" size={13}/></button>
  </Show>
  </div>
  <div class="wf-stage-main">
  <Show when={!p.stage.artifact}><RunFeedback/></Show>
  <Show when={inputIsPrimary()}><StageInput/></Show>
 <Show when={p.stage.rework_request}>{record=><section class="wf-questions wf-rework-request">
  <strong>{p.t('测试退回开发','Returned from testing')}</strong><p>{record().reason}</p>
  <small>{record().case_ids.join(' · ')} · {new Date(record().at).toLocaleString()}</small>
  <WorkflowReworkEvidence run={p.run} stage={p.stage} t={p.t}/>
 </section>}</Show>
 <Show when={p.stage.artifact}><div class="wf-output-heading"><h2>{p.t(...stageTitles[p.node.kind])}</h2><div class="wf-actions"><Show when={active()}><button type="button" aria-pressed={marking()&&reviewing()} disabled={!editable()||!reviewing()} onClick={()=>setMarking(v=>!v)}>{p.t('圈画','Draw')}</button><button type="button" class="wf-annotation-orb" aria-label={p.t('批注','Comment')} title={p.t('批注','Comment')} disabled={!editable()||!reviewing()} data-annotating={conversationOpen()&&draft().mark?.target==='summary'} onClick={e=>mark({target:'summary'},e.currentTarget)}><WorkflowIcon name="message" size={17}/></button></Show>
 <Show when={hasComparison()}><button type="button" class="wf-link" classList={{'wf-comparison-reserved':!canCompare()}}
  aria-haspopup="dialog" disabled={!canCompare()} onClick={openComparison}>
  <WorkflowIcon name="history" size={15}/>{p.t('对比上一版','Compare previous version')}
 </button></Show>
 <Show when={p.node.kind==='delivery'&&p.stage.status==='approved'&&p.stage.approval_sha256} keyed>
  {_approval=><WorkflowDeliveryReceipt run={p.run} stage={p.stage} t={p.t}/>}
 </Show></div>
 <Show when={marking()&&reviewing()}><p class="wf-marking-hint" role="status">{p.t('在摘要或展开的字段上圈画，然后填写批注。','Draw on the summary or an expanded field, then add a comment.')}</p></Show></div>
 <Show when={previousVersion()&&['ready','running'].includes(p.stage.status)}>
  <small class="wf-composer-caption">{p.stage.status==='running'?
   p.t('新版正在执行，以下为上次成果。','The new version is running. Previous output is shown below.'):
   p.t('以下是修订前的成果，供本次修改参考。','Previous output shown as a basis for this revision.')}</small>
 </Show>
 <Show when={p.stage.reused_from}><div class="wf-planning-source-note">
  <small>{p.t('沿用本会话已审核的同输入成果。','Reused an approved result for identical inputs in this conversation.')}</small>
  <Show when={p.stage.operation_id} keyed>{operation=><WorkflowPlanningSource run={p.run} stage={p.stage} operation={operation} t={p.t}/>}</Show>
 </div></Show>
 <StageArtifact approvedCases={upstreamCases()} readScenarioBasis={p.node.kind==='design'?readScenarioBasis:undefined} stage={p.stage} kind={p.node.kind} t={p.t} marking={marking()} editable={editable()&&reviewing()} commentable={active()&&!!p.onAsk} commentTarget={conversationOpen()?draft().mark?.target:undefined} onMark={mark} onRef={p.onRef} onCoverage={p.onCoverage} fileLink={filenameHref}
  deliveryLink={row=>{
   if(p.stage.status==='approved')return approvedDeliverableDownload(p.run,p.stage,row.id);
   const file=fileRecords().find(e=>e.path===row.path&&e.sha256===row.sha256);
   return file&&filenameHref(file.id);
  }}/>
 <Show when={p.stage.artifact?.template_edits?.length}>
  <WorkflowReadingDetail title={p.t('模板填写内容','Template fields')}
   label={p.t('查看模板填写内容','View template fields')+' · '+p.stage.artifact!.template_edits!.length}
   basis={p.stage.operation_id??''} t={p.t}>
   <For each={p.stage.artifact!.template_edits}>{edit=><dl class="wf-fields"><For each={edit.values}>{(value,i)=><div><dt>{p.stage.template?.edit_targets?.find(slot=>slot.target===edit.target)?.columns[i()]??edit.target}</dt><dd>{value}</dd></div>}</For></dl>}</For>
  </WorkflowReadingDetail>
 </Show>
 <Show when={templateDownload(p.run,p.stage)}>{href=><div class="wf-actions"><a href={href()} download="">{p.t('下载已审核文稿','Download approved document')} · {p.stage.template?.export_extension??'.txt'}</a><Show when={p.stage.template?.export_extension&&p.stage.template.export_extension!=='.txt'}><a href={templateDownload(p.run,p.stage,true)} download="">{p.t('下载纯文本','Download plain text')}</a></Show></div>}</Show>
 <Show when={p.node.kind==='delivery'&&p.run.stages.some(s=>templateDownload(p.run,s))}><details><summary>{p.t('已审核的阶段文稿','Approved stage documents')}</summary><ul><For each={p.run.stages.filter(s=>templateDownload(p.run,s))}>{s=><li><a href={templateDownload(p.run,s)} download="">{p.run.definition.nodes.find(n=>n.id===s.node_id)?.name} · {s.template?.export_extension??'.txt'}</a></li>}</For></ul></details></Show>
 <Show when={hasImprovementSource(p.stage)}>
  <WorkflowImprovement run={p.run} stage={p.stage} catalog={p.catalog} readOnly={p.improvementReadOnly} t={p.t}
   onManage={p.onManageCapabilities} onSaved={p.onImprovementSaved} onOpenDefinition={p.onOpenImprovedDefinition}/>
 </Show>
 </Show>
  </div>
 <WorkflowDialog open={approvalIssuesOpen()} title={approvalIssueTitle()} size="compact"
  locale={p.t('zh-CN','en')} onClose={()=>setApprovalIssuesOpen(false)}>
  <Show when={approvalIssuesOpen()}><div class="wf-stage-panel">
   <Show when={hasApprovalIssues()} fallback={<p role="status">{p.t('当前没有审核待办。','There are no current review tasks.')}</p>}>
    <p>{p.t('完成以下事项后可审核通过。','Complete these tasks before approving.')}</p>
    <ul class="wf-approval-issues"><For each={approvalIssues()}>{issue=><li>{issue}</li>}</For></ul>
   </Show>
   <Show when={feedbackAvailable()}><button type="button" class="wf-link" onClick={()=>{
    setApprovalIssuesOpen(false);
    queueMicrotask(()=>{if(!disposed)setFeedbackOpen(true);});
   }}>{p.t('查看本步反馈','View stage feedback')}</button></Show>
  </div></Show>
 </WorkflowDialog>
 <WorkflowDialog open={feedbackOpen()&&feedbackAvailable()} title={p.t('本步反馈','Stage feedback')}
  locale={p.t('zh-CN','en')} draftNotice={draftError()} onClose={()=>setFeedbackOpen(false)}>
  <Show when={feedbackOpen()&&feedbackAvailable()}><div class="wf-stage-panel">
   <RunFeedback/>
   <Show when={active()&&workStatus()==='needs_input'}>
    <StageInput/>
    <div class="wf-actions">
     <Show when={canSupplement()}><button type="button" onClick={()=>{
      setFeedbackOpen(false);queueMicrotask(()=>{if(!disposed)openConversation('follow-up');});
     }}>{p.t('在悬浮对话中补充','Reply in stage conversation')}</button></Show>
     <button type="button" class="wf-primary" aria-haspopup={retryAcknowledgement()?'dialog':undefined}
      disabled={!editable()||!!p.sending||parseBusy()||openAnnotations().length>0}
      onClick={()=>void execute()}>{p.t('提交补充并执行','Submit answers and run')}</button>
    </div>
   </Show>
  </div></Show>
 </WorkflowDialog>
 <WorkflowDialog open={!!stopTarget()&&stopTarget()===p.stage.operation_id&&workStatus()==='running'&&!stopping()&&!interrupted()} size="compact"
   title={p.t('停止本次执行？','Stop this execution?')} locale={p.t('zh-CN','en')} onClose={()=>setStopTarget(undefined)}>
  <div class="wf-stop-confirm">
   <p>{p.t('已保存的文件和工具记录会保留。外部工具已开始的操作，请按原记录核查。','Saved files and tool records are kept. Check the original records for external operations already started.')}</p>
   <div class="wf-actions">
    <button autofocus type="button" onClick={()=>setStopTarget(undefined)}>{p.t('继续执行','Keep running')}</button>
    <button type="button" class="wf-primary" onClick={confirmStop}>{p.t('停止执行','Stop execution')}</button>
   </div>
  </div>
 </WorkflowDialog>
 <WorkflowDialog open={!!currentRetry()} size="compact" title={p.t('重试前核对工具结果','Check tool outcomes before retrying')}
   locale={p.t('zh-CN','en')} onClose={()=>setRetryPrompt(undefined)}>
  <div class="wf-connector-retry">
   <p>{p.t('以下调用可能已在设备或服务上执行。请先核对实际状态，再决定是否开始新一轮。','These calls may already have run on the device or service. Check its state before starting another attempt.')}</p>
   <ul class="wf-connector-retry-list" aria-label={p.t('结果未确认的调用','Calls with unconfirmed outcomes')}>
    <For each={pendingConnectors()}>{item=><li><strong>{item.tool?item.server+' · '+item.tool:item.server}</strong>
     <Show when={item.previous}><span>{item.tool?p.t('历史阶段会话','Previous stage session')
      :p.t('原阶段会话不可查看','Original stage session unavailable')}</span></Show>
     <Show when={item.case_id}><span>{item.case_id}</span></Show>
     <Show when={item.recorded_at}>{at=><time dateTime={at()}>{new Date(at()).toLocaleString()}</time>}</Show>
    </li>}</For>
   </ul>
   <div class="wf-actions">
    <button autofocus type="button" onClick={()=>setRetryPrompt(undefined)}>{p.t('先去核对','Check first')}</button>
    <button type="button" class="wf-primary" onClick={confirmRetry}>{p.t('已核对，继续执行','Checked, start execution')}</button>
   </div>
  </div>
 </WorkflowDialog>
 <WorkflowDialog open={!!comparison()} size="regular" childScroll title={p.t('修订对比','Revision comparison')}
   locale={p.t('zh-CN','en')} onClose={()=>setComparison(undefined)}>
   <Show when={comparison()} keyed>{basis=><WorkflowRevisionDiff basis={basis} t={p.t}/>}</Show>
  </WorkflowDialog>
  <WorkflowDialog open={!!panel()} size={panelFromTools()?'tools':['basis','comments'].includes(panel()??'')?'compact':'regular'}
   side={!panelFromTools()&&(panel()==='activity'||panel()==='execution'&&!!recordReturn)} childScroll={panel()==='manual'}
   title={panel()?p.t(...panelLabels[panel()!]):''} locale={p.t('zh-CN','en')} draftNotice={draftError()} onClose={closePanel}>
   <Show when={panelFromTools()&&panel()!=='tools'&&(panel()!=='execution'||!recordReturn)}>
    <button type="button" class="wf-link wf-tools-back" onClick={returnToTools}><WorkflowIcon name="back" size={15}/>{p.t('返回本步工具','Back to stage tools')}</button>
   </Show>
   <Show when={p.actionError&&(panel()==='settings'||panel()==='rework'||panel()==='activity'||panel()==='manual')}><p class="wf-error wf-dialog-error" role="alert">{p.actionError}</p></Show>
   <Show when={panel()==='tools'}><div class="wf-stage-tool-menu"><ToolChoices menu={true}/></div></Show>
   <Show when={panel()==='basis'}><div class="wf-stage-basis"><BasisEntries/></div></Show>
   <Show when={panel()==='manual'}><WorkflowManualChecks actions={manualOptions()} records={manualRecords()} drafts={draft().manual??{}}
    loadBench={(action,signal)=>stageApi.benchFirmware(p.run,p.stage,action,signal)} fileHref={recoveryHref}
    draftScope={storageKey} attachmentHref={(receipt,id)=>stageApi.manualFileHref(p.run,p.stage,receipt,id)}
    retest={p.stage.log.filter(entry=>entry.event==='rework'||entry.event==='revised').at(-1)?.event==='rework'}
    readOnly={!canRecordManual()} busy={p.busy} t={p.t} onDraft={(key,value)=>save({manual:{...draft().manual,[key]:value}})}
    onSave={recordManual}/></Show>
   <Show when={panel()==='comments'}><div class="wf-review-comments">
    <Show when={p.stage.annotations.length} fallback={<p>{p.t('此阶段暂无批注。','This stage has no comments yet.')}</p>}>
     <p role="status">{p.stage.annotations.length} {p.t('条批注','comments')} · {openAnnotations().length} {p.t('待处理','open')}</p>
     <For each={[...annotationsById().keys()]}>{id=><Show when={annotationsById().get(id)}>{annotation=><article>
      <button type="button" class="wf-link" onClick={()=>{closePanel();returnToMark(annotation().target);}}>{p.t('定位','Locate')} {annotation().target==='summary'?p.t('摘要','summary'):annotation().target}</button>
      <Show when={annotation().anchor?.quote}>{quote=><blockquote>{quote()}</blockquote>}</Show><p>{annotation().comment}</p>
      <div class="wf-actions">
       <Show when={p.onAsk}><button type="button" class="wf-link" onClick={()=>{closePanel();queueMicrotask(()=>{if(!disposed)questionFromAnnotation(id);});}}>{p.t('在对话中讨论','Discuss in conversation')}</button></Show>
       <Show when={annotation().status==='open'&&reviewing()} fallback={<small>{stageEventText(annotation().status,p.t)}</small>}>
        <button type="button" disabled={!editable()||parseBusy()} onClick={()=>{closePanel();void execute([id]);}}>{p.t('按此条修改','Apply this comment')}</button>
       </Show>
      </div>
     </article>}</Show>}</For>
     <Show when={openAnnotations().length>1&&reviewing()}><button type="button" disabled={!editable()||parseBusy()} onClick={()=>{closePanel();void execute(openAnnotations().map(a=>a.id));}}>{p.t('按全部待处理批注修改','Apply all open comments')}</button></Show>
    </Show>
   </div></Show>
   <Show when={panel()==='rework'}><WorkflowReworkForm run={p.run} stage={p.stage} draft={reworkDraft()} t={p.t}
    editable={editable()} onDraft={value=>save({rework:value})} onSubmit={async(revision,target,caseIds,reason)=>{
     const draftEdit=draft().reworkEdit??save({rework:reworkDraft()}).reworkEdit;
     if(await p.onAction('rework',{reason,target_node_id:target,case_ids:caseIds},undefined,{revision,draftEdit})&&!disposed)closePanel();
    }}/></Show>
   <Show when={panel()==='settings'}>
   <div class="wf-stage-panel"><div><h3>{p.t('允许读取的交付物','Approved inputs')}</h3><p>{p.node.inputs.map(id=>p.run.definition.nodes.find(n=>n.id===id)?.name??id).join(' · ')||p.t('仅本步输入','Stage input only')}</p></div>
    <StagePolicy value={activePolicy()} catalog={p.catalog} t={p.t} onChange={setPolicy}/>
    <div class="wf-actions"><button type="button" onClick={()=>{setPanel(null);p.onManageCapabilities();}}>{p.t('管理已注册的插件','Manage registered plugins')}</button>
     <button disabled={!canSavePolicy()} class="wf-primary" type="button" onClick={async()=>{if(canSavePolicy()&&await p.onAction('configure',activePolicy())&&!disposed){setPolicy(null);setPanel(null);}}}>{p.t('保存节点权限','Save stage permissions')}</button></div></div>
   </Show>
   <Show when={panel()==='input'}><StageInput/><button type="button" class="wf-primary" onClick={closePanel}>{p.t('返回本步','Return to stage')}</button></Show>
   <Show when={panel()==='activity'}><div class="wf-log wf-stage-panel">
 <Show when={notesById().size}><ul><For each={[...notesById().keys()]}>{id=>
  <Show when={notesById().get(id)}>{note=><li><time dateTime={note().at}>{new Date(note().at).toLocaleString()}</time> {note().text}</li>}</Show>
 }</For></ul></Show>
 <Show when={active()}><label>{p.t('添加笔记','Add a note')}<textarea rows="2" value={draft().note??''} onInput={e=>save({note:e.currentTarget.value})}/></label><button type="button" disabled={!editable()||!draft().note?.trim()} onClick={async()=>{const note=draft().note;if(note&&await p.onAction('note',{text:note})&&!disposed&&note===draft().note)save({note:''});}}>{p.t('保存笔记','Save note')}</button></Show>
 <ol><For each={activityOrder()}>{index=><Show when={p.stage.log[index]}>{entry=>
  <li><time dateTime={entry().at}>{new Date(entry().at).toLocaleString()}</time> {stageEventText(entry().event,p.t)}
   <Show when={typeof entry().message==='string'}> · {String(entry().message)}</Show>
   <Show when={Array.isArray(entry().checks)&&Boolean((entry().checks as unknown[]).length)}>
    <ul><Index each={entry().checks as {case_id:string;status:string;id:string}[]}>{check=><li>{check().case_id} · {stageEventText(check().status,p.t)}</li>}</Index></ul>
   </Show>
   <Show when={entry().event==='finished'?entry().operation_id:
     entry().event==='started'&&p.stage.status==='running'&&entry().operation_id===p.stage.operation_id?entry().operation_id:entry().original_operation_id}>{op=>
    <button class="wf-link wf-execution-link" type="button" data-execution-record={String(op())} data-execution-event={index}
     onClick={event=>openAttempt(op(),{index,button:event.currentTarget})}>{entry().event==='started'?
      p.t('查看本次实时记录','View live execution record'):p.t('查看该次记录','View execution record')}</button>
   }</Show>
  </li>
 }</Show>}</For></ol>
 </div></Show>
 <Show when={panel()==='execution'}>
  <Show when={recordReturn}><button class="wf-link wf-execution-back" type="button" onClick={closePanel}><WorkflowIcon name="back" size={15}/>{p.t('返回执行记录列表','Back to execution history')}</button></Show>
  <Show when={recordError()}>{message=><div class="wf-actions">
   <p role="alert">{message()}</p><button type="button" onClick={()=>retryRecord(n=>n+1)}>{p.t('重新读取执行记录','Read execution record again')}</button>
  </div>}</Show>
  <Show when={executionRecord()} fallback={<Show when={!recordError()}><p role="status">{p.t('正在读取执行记录…','Reading execution record…')}</p></Show>}>{record=>
   <section class="wf-execution-record">
    <div><strong>{record().status==='interrupted_unknown'?p.t('执行已中断，整步结果未确认','Execution interrupted. Stage outcome is unconfirmed.'):
     record().status==='stopping'?p.t('正在停止','Stopping'):stageEventText(record().status,p.t)}</strong>
     <span>{engineName(record().kernel)}</span><time dateTime={record().at}>{new Date(record().at).toLocaleString()}</time></div>
    <Show when={record().annotation_count}><p>{p.t('当时选中的批注','Selected comments')} · {record().annotation_count}</p></Show>
    <Show when={record().inputs?.length}><details class="wf-execution-basis">
     <summary>{p.t('本次采用的已审输入','Approved inputs used')} · {record().inputs!.length}</summary>
     <ul><For each={record().inputs}>{input=><li><span>{p.run.definition.nodes.find(node=>node.id===input.node_id)?.name??input.node_id}</span>
      <code>{input.approval_sha256}</code></li>}</For></ul>
    </details></Show>
    <Show when={executionEvidence().size} fallback={<p>{record().live?p.t('等待本次首条工具回执。','Waiting for the first tool receipt.'):
     p.t('这次没有保存工具回执。','This execution saved no tool receipts.')}</p>}>
     <ul><For each={[...executionEvidence().keys()]}>{id=><Show when={executionEvidence().get(id)}>{item=><li><div><small>{evidenceLabel(item())}
      <Show when={item().path&&!(item().server&&item().tool)&&item().case_id}>{caseId=><> · <span>{caseId()}</span></>}</Show>
      <Show when={item().status}> · {stageEventText(item().status!,p.t)}</Show>
      <Show when={item().output_matches===false||item().control&&item().status==='failed'}>
       · {p.t('输出待核对','Output needs review')}
      </Show></small>
      <strong>{item().server&&item().tool?[item().case_id,item().server,item().tool].filter(Boolean).join(' · '):
       item().path??item().case_id??item().design_id??evidenceLabel(item())}</strong>
      <Show when={item().recorded_at}>{at=><time dateTime={at()}>{new Date(at()).toLocaleTimeString()}</time>}</Show>
      <Show when={item().sha256||item().exit_code!==undefined||item().source_manifest_sha256||item().hardware_readback}>
       <details class="wf-execution-facts"><summary>{p.t('核对输入输出','Inspect inputs and outputs')}</summary>
        <dl>
         <Show when={item().hardware_readback}>{value=>
          <WorkflowHardwareReadback value={value()} issue={item().readback_issue} t={p.t}/>
         }</Show>
         <Show when={item().subject}>{subject=><>
          <dt>{p.t('经启动器核对的程序','Program verified by launcher')}</dt>
          <dd><strong>{subject().path}</strong></dd>
         </>}</Show>
         <Show when={item().expected_output_sha256}><dt>{p.t('实际输出','Observed output')}</dt><dd>
          <span>{item().output_matches?p.t('符合预期','Matches expectation'):
           item().output_truncated?p.t('输出被截断','Output truncated'):item().output_preview?p.t('与预期不符','Output differs'):p.t('没有捕获到输出','No output captured')}</span>
          <Show when={item().output_preview}><pre class="wf-execution-output">{item().output_preview}</pre></Show>
          <Show when={item().output_preview_partial}><small>{p.t('仅显示前 1000 字符。','Showing the first 1,000 characters.')}</small></Show>
         </dd></Show>
         <Show when={item().control}>{control=><>
          <dt>{p.t('对照输入结果','Contrast result')}</dt>
          <dd>
           <span>{!control().limit&&control().output_matches&&control().exit_code===control().expected_exit_code
            ?p.t('符合对照预期','Matches contrast expectation'):p.t('与对照预期不符','Contrast result differs')}</span>
           <p>{p.t('实际退出码','Actual exit code')} {control().exit_code} · {p.t('预期','Expected')} {control().expected_exit_code}</p>
           <Show when={control().output_preview}><pre class="wf-execution-output">{control().output_preview}</pre></Show>
           <Show when={control().output_truncated||control().output_preview_partial}>
            <small>{p.t('当前显示部分输出。','Showing partial output.')}</small>
           </Show>
          </dd>
         </>}</Show>
         <Show when={item().action_id}><dt>{p.t('验收动作','Check')}</dt><dd>{item().action_id}</dd></Show>
         <Show when={item().input_sha256}><dt>{p.t('正常输入摘要','Normal input digest')}</dt><dd><code>{item().input_sha256}</code></dd></Show>
         <Show when={item().previous_sha256!==undefined}><dt>{p.t('写入前摘要','Before write')}</dt>
          <dd><code>{item().previous_sha256??p.t('新建文件','New file')}</code></dd></Show>
         <Show when={item().sha256}><dt>{item().kind==='file'?p.t('写入后摘要','After write'):p.t('被测文件摘要','File under test')}</dt>
          <dd><code>{item().sha256}</code></dd></Show>
         <Show when={item().size!==undefined}><dt>{p.t('字节数','Size')}</dt><dd>{item().size} B</dd></Show>
         <Show when={item().exit_code!==undefined}><dt>{p.t('退出码','Exit code')}</dt><dd>{item().exit_code}</dd></Show>
         <Show when={item().source_manifest_sha256}><dt>{p.t('输入清单摘要','Input manifest')}</dt>
          <dd><code>{item().source_manifest_sha256}</code></dd></Show>
         <Show when={item().expected_output_sha256}><dt>{p.t('已审输出摘要','Approved output digest')}</dt>
          <dd><code>{item().expected_output_sha256}</code></dd></Show>
         <Show when={item().observed_output_sha256}><dt>{p.t('实际输出摘要','Observed output digest')}</dt>
          <dd><code>{item().observed_output_sha256}</code></dd></Show>
         <Show when={item().subject}>{subject=><>
          <dt>{p.t('程序摘要','Program digest')}</dt><dd><code>{subject().sha256}</code></dd>
          <dt>{p.t('来源开发审批','Development approval')}</dt><dd><code>{subject().approval_sha256}</code></dd>
         </>}</Show>
         <Show when={item().control}>{control=><>
          <Show when={control().input_sha256}><dt>{p.t('对照输入摘要','Contrast input digest')}</dt>
           <dd><code>{control().input_sha256}</code></dd></Show>
          <Show when={control().expected_output_sha256}><dt>{p.t('对照预期输出摘要','Expected contrast output digest')}</dt>
           <dd><code>{control().expected_output_sha256}</code></dd></Show>
          <Show when={control().observed_output_sha256}><dt>{p.t('对照实际输出摘要','Observed contrast output digest')}</dt>
           <dd><code>{control().observed_output_sha256}</code></dd></Show>
         </>}</Show>
        </dl>
       </details>
      </Show>
     </div>
      <Show when={!record().live&&item().kind==='file'&&item().path}><a class="wf-file-download" href={recoveryHref(id)} download="" aria-label={p.t('下载','Download')+' '+item().path}><WorkflowIcon name="download" size={15}/>{p.t('下载','Download')}</a></Show>
     </li>}</Show>}</For></ul>
    </Show>
   </section>}
  </Show>
 </Show>
 </WorkflowDialog>
 <Show when={canRework()}><div class="wf-actions"><button type="button" aria-haspopup="dialog" onClick={()=>openPanel('rework')}><WorkflowIcon name="history" size={16}/>{p.t(...panelLabels.rework)}</button></div></Show>
 <section class="wf-stage-composer" aria-label={p.t('阶段对话','Stage conversation')}>
  <div class="wf-conversation-inline" inert={conversationOpen()}>
   <button ref={dialogueTrigger} type="button" class="wf-conversation-entry" aria-controls={conversationOpen()?conversationId:undefined}
    aria-expanded={conversationOpen()} onClick={()=>openConversation()}><WorkflowIcon name="message" size={16}/>{p.t('打开阶段对话','Open stage conversation')}</button>
   <Show when={previousVersion()&&p.onStageVersion}><button type="button" class="wf-previous-version" onClick={()=>p.onStageVersion?.(previousVersion()!)}><WorkflowIcon name="history" size={15}/>{p.t('查看此阶段上一版','View previous stage version')}</button></Show>
  </div>
  <Show when={conversationOpen()}>
   <div id={conversationId} class="wf-conversation-float" data-asking={askingMode()} role="group" aria-label={p.t('阶段对话面板','Stage conversation panel')}
    onKeyDown={e=>{if(e.key==='Escape'&&!e.isComposing){e.preventDefault();closeConversation();}}}>
    <div class="wf-conversation-heading"><div><h2>{draft().mark?p.t('页面批注','Page comment'):p.t('阶段对话','Stage conversation')}</h2><span>{p.node.name}</span></div>
     <button type="button" class="wf-icon-button" aria-label={p.t('收起阶段对话','Close stage conversation')} onClick={()=>closeConversation()}><WorkflowIcon name="close" size={16}/></button>
    </div>
  <WorkflowDraftNotice message={panel()||feedbackOpen()&&feedbackAvailable()?'':draftError()}/>
  <div class="wf-conversation-history">
  <div ref={conversationScroll} class="wf-conversation-scroll" onWheel={()=>{followLatest=false;}}
   onPointerDown={()=>{followLatest=false;}} onKeyDown={event=>{
    if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End'].includes(event.key))followLatest=false;
   }} onScroll={event=>{
    const {scrollTop,scrollHeight,clientHeight}=event.currentTarget;
    if(scrollHeight-scrollTop-clientHeight<48)followLatest=true;
   }}>
    <div class="wf-conversation-log" role="log" aria-label={p.t('阶段对话记录','Stage discussion history')}>
     <For each={[...discussion().keys()]}>{id=>{
      const initial=untrack(()=>discussion().get(id)!);
      const item=()=>discussion().get(id)??initial;
      const turn=()=>{const value=item();return 'turn' in value?value.turn:undefined;};
      const event=()=>{const value=item();return 'event' in value?value.event:undefined;};
      return <Show when={turn()} fallback={<article class="wf-message-assistant">
       <Show when={typeof event()?.reply==='string'}><p>{String(event()?.reply)}</p></Show>
       <Show when={Array.isArray(event()?.questions)}><strong>{p.t('需要你确认','Please clarify')}</strong><ul><For each={event()!.questions as string[]}>{q=><li>{q}</li>}</For></ul></Show>
       <Show when={typeof event()?.error==='string'}><p>{String(event()?.error)}</p></Show>
      </article>}>{turn=><article>
      <p class="wf-message-user">{turn().question}</p>
      <Show when={p.stage.annotations.find(a=>a.id===turn().annotation_id)}>{reference=>
       <button type="button" class="wf-link wf-message-reference" onClick={()=>revealReferencedMark(reference().target)}>
        {p.t('定位引用','Locate reference')} · {reference().target}
       </button>
      }</Show>
      <Show when={followUps().find(a=>a.id===turn().follow_up_id)}>{reference=>
       <button type="button" class="wf-link wf-message-reference" onClick={()=>revealFollowUp(reference().id)}>
        {p.t('查看引用的补充要求','View referenced request')}<Show when={reference().source_id}> · {reference().source_id}</Show>
       </button>
      }</Show>
      <p class="wf-message-assistant">{turn().answer??turn().error??p.t('正在回答…','Working on a reply…')}</p>
     </article>}</Show>;}}</For>
    </div>
    <FollowUpList/>

  </div>
  <Show when={conversationReference()}>{reference=><div class="wf-conversation-annotation">
   <span>{reference().label}</span><p title={reference().text}>{reference().text}</p>
   <Show when={reference().removable}><button type="button" class="wf-icon-button" aria-label={p.t('移除引用','Remove reference')}
    onClick={()=>{save({questionFollowUp:undefined,questionAnnotation:undefined});dialogueInput?.focus({preventScroll:true});}}>
     <WorkflowIcon name="close" size={14}/>
    </button></Show>
  </div>}</Show>
  </div>
  <div class="wf-conversation-controls">
  <Show when={conversationOpen()&&p.onAsk&&!draft().mark&&(active()||amending())}>
   <div class="wf-conversation-modes" role="group" aria-label={p.t('对话用途','Conversation purpose')}>
    <button type="button" aria-pressed={askingMode()} onClick={()=>setAskingMode(true)}>{p.t('提问','Ask')}</button>
    <button type="button" aria-pressed={!askingMode()} onClick={()=>setAskingMode(false)}>{p.t('补充或修订','Follow up or revise')}</button>
   </div>
  </Show>
  <Show when={conversationOpen()&&askingMode()&&p.onAsk&&!draft().mark}>
   <form class="wf-composer-form" onSubmit={e=>{e.preventDefault();void askQuestion();}}>
    <label>{p.t('对当前阶段提问','Ask about this stage')}<textarea ref={dialogueInput} rows="2" maxLength={4000} value={draft().question??''}
     onInput={e=>save({question:e.currentTarget.value})}/></label>
    <div class="wf-composer-actions"><button type="submit" disabled={p.asking||p.dialogueReadOnly||!draft().question?.trim()}>{p.asking?p.t('正在回答…','Answering…'):p.t('发送问题','Send question')}</button></div>
   </form>
  </Show>
  <Show when={canSupplement()&&(!askingMode()||draft().mark)}>
   <Show when={draft().mark&&reviewing()} fallback={<SupplementForm/>}>
    <form class="wf-composer-form" data-annotation-editor onSubmit={e=>{e.preventDefault();void annotate();}}>
     <label>{p.t('批注','Comment')} · {draft().mark!.target}<textarea ref={commentBox} value={draft().comment??''} onInput={e=>save({comment:e.currentTarget.value})} rows="2" required/></label>
     <div class="wf-composer-actions"><button type="button" onClick={dismissMark}>{p.t('取消','Cancel')}</button><button type="submit" disabled={!editable()||!draft().comment?.trim()}>{p.t('保存批注','Save comment')}</button></div>
    </form>
   </Show>
  </Show>
   </div>
  </div>
  </Show>
 </section>
 <WorkflowDialog open={impactOpen()&&conversationOpen()&&amending()} title={p.t('修订影响','Revision impact')} size="compact"
  locale={p.t('zh-CN','en')} onClose={()=>setImpactOpen(false)}>
  <Show when={impactOpen()}><div class="wf-stage-panel">
   <p>{p.t('将重新打开以下已开始的阶段。原版本可在会话列表中查看。','These stages will reopen. Earlier versions remain in the conversation list.')}</p>
   <ol class="wf-revision-impact-list"><For each={impacted()}>{name=><li>{name}</li>}</For></ol>
  </div></Show>
 </WorkflowDialog>
 <Show when={p.completed||active()&&(workStatus()!=='failed'||!!p.stage.artifact)}><footer class="wf-footer">
 <Show when={p.completed} fallback={<>
 <Show when={p.stage.status==='running'}><span role="status">{interrupted()?p.t('原执行已停止，等待核查。','The original execution stopped. Its record is ready to check.'):stopping()?p.t('已请求停止，正在收尾…','Stop requested. Finishing up…'):p.t('正在执行此节点…','Running this stage…')}</span>
 <Show when={p.onStop&&p.stage.operation_id&&!interrupted()}><button ref={stopTrigger} type="button" class="wf-stop-trigger"
   aria-haspopup="dialog" aria-disabled={stopping()||p.stopSubmitting} onClick={event=>{
    if(stopping()||p.stopSubmitting)return;
    event.currentTarget.focus({preventScroll:true});setStopTarget(p.stage.operation_id);
   }}>{p.t('停止本次执行','Stop this execution')}</button></Show>
 <Show when={interrupted()}><button type="button" disabled={p.busy} onClick={()=>void recoverAttempt()}>{p.t('核查执行状态','Check interrupted execution')}</button></Show></Show>
 <Show when={p.stage.artifact&&(workStatus()==='failed'||workStatus()==='needs_input')}
  fallback={<Show when={p.stage.status!=='running'&&(!manualOnly()||pendingFollowUps().length||p.stage.status==='needs_input')}><button class={reviewing()&&!pendingFollowUps().length?'':'wf-primary'} type="button" aria-haspopup={retryAcknowledgement()?'dialog':undefined} disabled={!editable()||!!p.sending||parseBusy()||openAnnotations().length>0} onClick={()=>void execute()}>{pendingFollowUps().length?p.t('按补充要求执行','Run with requests'):reviewing()?p.t('重新生成','Regenerate'):p.stage.status==='needs_input'?p.t('提交补充并执行','Submit answers and run'):p.t('执行此步','Run this stage')}</button></Show>}>
  <button type="button" class="wf-primary" aria-haspopup="dialog" onClick={()=>setFeedbackOpen(true)}>
   {workStatus()==='failed'?p.t('查看执行故障','View execution issue'):p.t('查看澄清问题','View clarification')}
  </button></Show>
 <Show when={reviewing()}><button class="wf-primary" type="button" title={hasApprovalIssues()?p.t('在本步标题处查看审核待办','Open review tasks at the stage heading'):undefined} disabled={!editable()||!!p.sending||pendingFollowUps().length>0||openAnnotations().length>0||!!draft().mark||hasApprovalIssues()||parseBusy()} onClick={()=>void p.onAction('approve',{})}>{p.run.stages.at(-1)?.node_id===p.node.id?p.t('审核通过并完成','Approve and finish'):p.t('审核通过，进入下一步','Approve and continue')} →</button></Show>
 </>}><p class="wf-completion-message" role="status" tabindex="-1"><WorkflowIcon name="check" size={16}/>{p.t('所有节点均已审核通过。','All stages have been approved.')}</p></Show>
 </footer></Show>
 </article>;
}
