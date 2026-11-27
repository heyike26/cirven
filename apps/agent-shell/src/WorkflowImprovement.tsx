import {createMemo,createSignal,For,Show,onCleanup} from 'solid-js';
import {ApiError,operationId} from './api';
import {stageApi,type Catalog,type Definition,type ImprovementReceipt,type ImprovementRequest,type RunStage,type StageRun} from './stage-workflows';
import WorkflowDialog from './WorkflowDialog';
import {WorkflowIcon} from './WorkflowIcon';

const categories=['skills','tools','mcp'] as const;
const sourceFields=['lessons','plugin_suggestions'] as const;
type Pending={operation:string;request:ImprovementRequest};

export function hasImprovementSource(stage:RunStage){
 return sourceFields.some(key=>Array.isArray(stage.artifact?.[key])&&stage.artifact[key].length>0);
}

export default function WorkflowImprovement(p:{run:StageRun;stage:RunStage;catalog:Catalog;readOnly:boolean;
 t:(zh:string,en:string)=>string;onManage:()=>void;onSaved:(value:Definition)=>void;onOpenDefinition:(id:string)=>void}){
 const t=p.t;
 const [open,setOpen]=createSignal(false),[error,setError]=createSignal(''),[saving,setSaving]=createSignal(false);
 const [history,setHistory]=createSignal<ImprovementReceipt[]>([]),[result,setResult]=createSignal<ImprovementReceipt>();
 const [pending,setPending]=createSignal<Pending>();
 const [sourceKey,setSourceKey]=createSignal(''),[targetId,setTargetId]=createSignal(p.stage.node_id);
 const [category,setCategory]=createSignal<ImprovementRequest['category']>('skills');
 const [change,setChange]=createSignal<ImprovementRequest['change']>('add');
 const [capability,setCapability]=createSignal(''),[mode,setMode]=createSignal<ImprovementRequest['mode']>(p.run.definition.id==='default'?'copy':'update');
 const [name,setName]=createSignal(p.run.definition.name+t(' · 改进版',' · improved'));
 let reader:AbortController|undefined,disposed=false;
 onCleanup(()=>{disposed=true;reader?.abort();});
 const source=()=>sourceItems().find(item=>item.key===sourceKey())??sourceItems()[0];
 const target=()=>p.run.definition.nodes.find(node=>node.id===targetId());
 const approved=()=>p.stage.status==='approved'?p.stage.approval_sha256:undefined;
 const sourceItems=createMemo(()=>sourceFields.flatMap(field=>(Array.isArray(p.stage.artifact?.[field])?p.stage.artifact[field] as string[]:[]).map((text,index)=>({
  field,index,text,key:field+':'+index,label:(field==='lessons'?t('经验','Lesson'):t('建议','Suggestion'))+' '+(index+1),
 }))));
 const options=()=>{
  const allowed=target()?.capabilities[category()]??[];
  const catalog=p.catalog[category()];
  return change()==='add'?catalog.filter(item=>!allowed.includes(item.id)):
   allowed.map(id=>catalog.find(item=>item.id===id)??{id,name:id});
 };
 const option=()=>options().find(item=>item.id===capability());
 const categoryName=(kind:ImprovementRequest['category'])=>kind==='skills'?'Skill':kind==='tools'?'Tool':'MCP';
 const changeName=(kind:ImprovementRequest['change'])=>kind==='add'?t('新增','Add'):t('移除','Remove');
 const storageKey=(sha:string)=>'cirven-workflow-improvement:'+JSON.stringify([p.run.project_id,p.run.id,p.stage.stage_session_id,sha]);
 function restore(){
  const sha=approved();
  if(!sha)return;
  try{
   const snapshot=JSON.parse(localStorage.getItem(storageKey(sha))??'null') as Pending|null;
   if(!snapshot)return;
   const req=snapshot.request;
   if(!req||req.approval_sha256!==sha||typeof snapshot.operation!=='string'||
      !sourceItems().some(x=>x.field===req.source_field&&x.index===req.source_index)||
      !p.run.definition.nodes.some(n=>n.id===req.target_node_id)||!categories.includes(req.category)||
      !['add','remove'].includes(req.change)||!['copy','update'].includes(req.mode)||
      typeof req.capability_id!=='string'||req.copy_name!==undefined&&typeof req.copy_name!=='string')
    throw Error();
   setPending(snapshot);setSourceKey(req.source_field+':'+req.source_index);setTargetId(req.target_node_id);
   setCategory(req.category);setChange(req.change);setCapability(req.capability_id);setMode(req.mode);
   if(req.copy_name)setName(req.copy_name);
  }catch{setError(t('暂时无法读取上次的改进保存记录。','Could not read the previous improvement request.'));}
 }
 function clear(value:Pending){
  const key=storageKey(value.request.approval_sha256);
  try{if(localStorage.getItem(key)===JSON.stringify(value))localStorage.removeItem(key);}
  catch{/* The workflow receipt still allows exact recovery. */}
  if(pending()===value)setPending(undefined);
 }
 async function readHistory(){
  const sha=approved();
  if(!sha)return;
  reader?.abort();
  const request=new AbortController();
  reader=request;
  try{
   const values=await stageApi.improvements(p.run,p.stage,sha,request.signal);
   if(request.signal.aborted||approved()!==sha)return;
   setHistory(items=>[...items.filter(item=>!values.some(value=>value.operation_id===item.operation_id)),...values]);
   const stillPending=pending();
   const saved=values.find(item=>item.operation_id===stillPending?.operation);
   if(stillPending&&saved){setResult(saved);clear(stillPending);setCapability('');}
  }catch(e){if(!request.signal.aborted&&!error())setError(e instanceof Error?e.message:String(e));}
 }
 function close(){
  reader?.abort();setOpen(false);
 }
 function prepare():ImprovementRequest|undefined{
  const sha=approved(),item=source(),node=target(),selected=option();
  if(!sha||!item||!node||!selected||mode()==='copy'&&!name().trim())return;
  return {approval_sha256:sha,source_field:item.field,source_index:item.index,target_node_id:node.id,
   category:category(),change:change(),capability_id:selected.id,
   ...(change()==='add'&&'sha256' in selected&&selected.sha256?{capability_sha256:selected.sha256}:{}),
   mode:mode(),...(mode()==='copy'?{copy_name:name().trim()}:{})};
 }
 async function submit(){
  if(saving()||p.readOnly)return;
  let value=pending();
  if(!value){
   const request=prepare();
   if(!request)return;
   value={operation:operationId(),request};
   try{localStorage.setItem(storageKey(request.approval_sha256),JSON.stringify(value));}
   catch{setError(t('本机未能保存此次改进请求，请保留此页。','Could not retain this improvement request. Keep this page open.'));return;}
   setPending(value);
  }
  setSaving(true);setError('');
  const sourceRun=p.run,sourceStage=p.stage;
  try{
   const {receipt,definition}=await stageApi.improve(sourceRun,sourceStage,value.request,value.operation);
   if(receipt.operation_id!==value.operation||receipt.source.run_id!==sourceRun.id||
      receipt.source.conversation_id!==sourceStage.stage_session_id||receipt.source.approval_sha256!==value.request.approval_sha256||
      receipt.definition.id!==definition.id||receipt.definition.revision!==definition.revision)
    throw Error(t('改进回执与本次操作不一致。','The receipt does not match this improvement.'));
   clear(value);
   if(!disposed){
    setResult(receipt);setHistory(items=>[receipt,...items.filter(item=>item.operation_id!==receipt.operation_id)]);
    setCapability('');p.onSaved(definition);
   }
  }catch(e){
   if(e instanceof ApiError&&[400,404,409,422].includes(e.status))clear(value);
   if(!disposed)setError(e instanceof Error?e.message:String(e));
  }finally{if(!disposed)setSaving(false);}
 }
 function receiptLine(item:ImprovementReceipt){
  return item.definition.name+' · v'+item.definition.revision+' · '+item.target_name+' · '+changeName(item.change)+' '+item.capability_id;
 }
 return <>
  <div class="wf-actions"><button type="button" class="wf-link" aria-haspopup="dialog" onClick={event=>{
   event.currentTarget.focus({preventScroll:true});
   if(!saving())setError('');
   if(!pending())restore();
   setOpen(true);void readHistory();
  }}><WorkflowIcon name="history" size={16}/>{t('经验与改进','Lessons and improvements')}</button></div>
  <WorkflowDialog open={open()} title={t('经验与改进','Lessons and improvements')} size="regular" childScroll locale={t('zh-CN','en')} onClose={close}>
   <form class="wf-improvement" onSubmit={event=>{event.preventDefault();void submit();}}>
    <div class="wf-improvement-scroll">
     <fieldset disabled={!!pending()||saving()}>
      <label>{t('阶段记录','Stage note')}<select value={source()?.key??''} onChange={e=>setSourceKey(e.currentTarget.value)}>
       <For each={sourceItems()}>{item=><option value={item.key}>{item.label}</option>}</For>
      </select></label>
      <blockquote class="wf-improvement-source">{source()?.text}</blockquote>
      <Show when={approved()&&!p.readOnly} fallback={<p class="wf-muted">{approved()?
       t('可查看已保存的改进。','Saved improvements are available to review.'):
       t('阶段审核通过后，经验可用于后续工作流。','Use this lesson in future workflows after approval.')}</p>}>
       <div class="wf-controls">
        <label>{t('目标节点','Target stage')}<select value={targetId()} onChange={e=>{setTargetId(e.currentTarget.value);setCapability('');}}>
         <For each={p.run.definition.nodes}>{node=><option value={node.id}>{node.name}</option>}</For></select></label>
        <label>{t('能力类别','Capability category')}<select value={category()} onChange={e=>{setCategory(e.currentTarget.value as ImprovementRequest['category']);setCapability('');}}>
         <For each={categories}>{item=><option value={item}>{categoryName(item)}</option>}</For></select></label>
        <label>{t('调整方式','Change')}<select value={change()} onChange={e=>{setChange(e.currentTarget.value as ImprovementRequest['change']);setCapability('');}}>
         <option value="add">{changeName('add')}</option><option value="remove">{changeName('remove')}</option></select></label>
        <label>{t('选择能力','Capability')}<select value={capability()} onChange={e=>setCapability(e.currentTarget.value)}>
         <option value="">{t('请选择','Choose a capability')}</option>
         <For each={options()}>{item=><option value={item.id}>{item.name}{'revision' in item&&item.revision?' · v'+item.revision:''}</option>}</For></select></label>
       </div>
       <Show when={p.run.definition.id!=='default'}><label>{t('保存位置','Save as')}<select value={mode()} onChange={e=>setMode(e.currentTarget.value as ImprovementRequest['mode'])}>
        <option value="update">{t('追加定义版本','New revision of this definition')}</option>
        <option value="copy">{t('另存工作流副本','Separate workflow copy')}</option></select></label></Show>
       <Show when={mode()==='copy'}><label>{t('工作流副本名称','Workflow copy name')}<input required maxlength="100" value={name()} onInput={e=>setName(e.currentTarget.value)}/></label></Show>
       <Show when={option()||pending()}><p class="wf-improvement-diff">
        <strong>{target()?.name} · {changeName(change())} {categoryName(category())} {capability()}</strong>
        <small>{t('应用于后续运行。已启动的流程保留原定义。','Applies to future runs. Started runs keep their original definition.')}</small>
       </p></Show>
      </Show>
     </fieldset>
     <Show when={error()}><p class="wf-error" role="alert">{error()}</p></Show>
     <Show when={result()}>{item=><p class="wf-improvement-receipt" role="status">{t('已保存','Saved')} {receiptLine(item())}</p>}</Show>
     <Show when={history().length}><details class="wf-improvement-history">
      <summary>{t('已保存的改进','Saved improvements')} · {history().length}</summary>
      <ul><For each={history()}>{item=><li><span>{receiptLine(item)}</span>
       <button class="wf-link" type="button" onClick={()=>{close();p.onOpenDefinition(item.definition.id);}}>{t('在流程库中查看','View in workflow library')}</button>
      </li>}</For></ul></details></Show>
    </div>
    <Show when={approved()&&!p.readOnly}><div class="wf-improvement-actions">
     <button type="button" disabled={!!pending()||saving()} onClick={()=>{close();p.onManage();}}>{t('管理已注册能力','Manage registered capabilities')}</button>
     <button type="submit" class="wf-primary" aria-disabled={saving()||!pending()&&!prepare()}>
      {saving()?t('正在保存…','Saving…'):pending()?t('核对或重试原保存','Check or retry original save'):t('保存改进','Save improvement')}
     </button>
    </div></Show>
   </form>
  </WorkflowDialog>
 </>;
}
