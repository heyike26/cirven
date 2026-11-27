import {createEffect,createMemo,createSignal,For,onCleanup,Show,type Accessor} from 'solid-js';
import {Button} from '@opencode-ai/ui/button';
import {coverageDraftEqual,explicitCoverageRejection} from './claim-coverage';
import {api,operationId} from './api';
import {t as uiText} from './ui-language';
import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {draftSignal,useDraftSession} from './ObjectDraft';
import {workbenchApi} from './workbench-api';
import type {ArtifactEnvelope,ClaimCoverageDraft,PlanningCandidate,PlanningProposal,RendererContext} from './contracts';
import {applyManualDecisions,manualDecisions,manualGateFromError,manualHunks,manualInspection,manualReference,prepareManualCapture,prepareManualSave,sameManualRef,startManualDraft,verifyManualCapture,verifyManualSave,type ManualCapture,type ManualCaptureOperation,type ManualChoice,type ManualGate,type ManualReviewDraft,type ManualSave,type ManualWorkspace} from './manual-harness';

export function createManualChanges(props:{artifact:ArtifactEnvelope;context:RendererContext;proposal:Accessor<PlanningProposal>;candidate:Accessor<PlanningCandidate>;setCandidate:(value:PlanningCandidate)=>void;reason:Accessor<string>;requirementCoverage?:Accessor<ClaimCoverageDraft|undefined>;canNavigate?:()=>boolean;onInspectNode:(id:string)=>void;onReturnToExport:()=>void;workspace?:Accessor<ManualWorkspace|undefined>}){
 const owner=useDraftSession(),pid=props.artifact.scope.projectId;
 const [gate,setGate]=draftSignal<ManualGate|null>('manualGate',null),[operation,setOperation]=draftSignal<ManualCaptureOperation|null>('manualCapture',null),[draft,setDraft]=draftSignal<ManualReviewDraft|null>('manualReview',null),[save,setSave]=draftSignal<ManualSave|null>('manualSave',null);
 const [capture,setCapture]=createSignal<ManualCapture>(),[pending,setPending]=createSignal(''),[error,setError]=createSignal<UIErrorCopy>('');let alive=true,readEpoch=0;
 onCleanup(()=>{alive=false;readEpoch++;});
 const ownsCapture=(value:ManualCaptureOperation)=>alive&&operation()?.request.operation_id===value.request.operation_id&&operation()?.request_sha256===value.request_sha256;
 const ownsSave=(value:ManualSave)=>alive&&save()?.operation_id===value.operation_id&&save()?.request_sha256===value.request_sha256;
 const locked=()=>!!pending()||save()?.state==='UNKNOWN'||save()?.state==='PREPARED';
 const reviewLocked=()=>locked()||capture()?.current.status!=='APPLICABLE'||!capture()?.snapshot.coverage.complete;
 const canCapture=()=>{const detail=gate(),previous=operation();if(!detail||detail.code!=='MANUAL_REVIEW_REQUIRED'||!detail.coverage.complete||!detail.files.length)return false;if(!capture())return true;if(capture()!.current.status!=='STALE'||!previous)return false;const offered=detail.files.map(({export_ref,path,basis_sha256,edited_sha256})=>({export_ref,path,basis_sha256,edited_sha256}));return JSON.stringify(offered)!==JSON.stringify(previous.request.files);};

 async function persist(){if(!owner?.session)throw new Error('本机草稿暂未就绪');await owner.session.finish();}
 async function run(label:string,fn:()=>Promise<unknown>){if(pending())return;setPending(label);setError('');try{await fn();}catch(e){if(alive&&props.canNavigate?.()!==false)setError(authoredErrorCopy(e));}finally{if(alive)setPending('');}}
 async function acceptCapture(result:ManualCapture,original:ManualCaptureOperation,initialize:boolean){
  await verifyManualCapture(result,props.proposal(),original);if(!ownsCapture(original))return;
  setCapture(result);setOperation({...original,state:'COMPLETED',capture_ref:result.capture_ref});
  if(!draft()||initialize&&!sameManualRef(draft()!.change_ref,result.capture_ref))setDraft(startManualDraft(result));
 }
 const captureBasis=createMemo(()=>JSON.stringify(operation()?.state==='COMPLETED'?operation()?.capture_ref:null));
 createEffect(()=>{const ref=JSON.parse(captureBasis()) as ManualCapture['capture_ref']|null,epoch=++readEpoch;if(!ref)return;const original=operation();if(!original||sameManualRef(capture()?.capture_ref,ref))return;void workbenchApi.manualCapture(pid,props.proposal().id,ref).then(async result=>{await verifyManualCapture(result,props.proposal(),original);if(alive&&epoch===readEpoch&&ownsCapture(original))setCapture(result);}).catch(e=>{if(alive&&epoch===readEpoch)setError(authoredErrorCopy(e));});});
 const currentView=()=>[props.candidate(),props.reason(),draft(),...props.requirementCoverage?.()?[props.requirementCoverage()]:[]];
 const lease=()=>JSON.stringify([operation(),gate(),save(),...currentView()]);
 function receiveExportError(value:unknown,originalLease:string){if(!alive||lease()!==originalLease)return false;const detail=manualGateFromError(value,props.proposal());if(!detail)return false;setGate(detail);setError('');return true;}
 const sameWorkspace=(a:ManualWorkspace|null|undefined,b:ManualWorkspace|undefined)=>!!a&&!!b&&a.id===b.id&&a.revision===b.revision&&a.binding_sha256===b.binding_sha256;
 const exportBlocked=()=>!!gate()&&sameWorkspace(gate()!.workspace_ref,props.workspace?.());
 async function checkForExport(workspace:ManualWorkspace):Promise<boolean>{
  const originalLease=lease(),target=manualReference(props.proposal()),owned=()=>alive&&props.canNavigate?.()!==false&&lease()===originalLease&&sameManualRef(target,manualReference(props.proposal()))&&sameWorkspace(workspace,props.workspace?.());
  try{const detail=manualInspection(await workbenchApi.inspectManual(pid,props.proposal().id,target,workspace),props.proposal(),workspace);if(!owned())return false;setGate(detail);setError('');return !detail;}
  catch(error){if(!owned())return false;if(receiveExportError(error,originalLease))return false;throw error;}
 }
 const primaryLabel=()=>operation()?.state==='UNKNOWN'||operation()?.state==='PREPARED'?'读取原核对结果':save()&&['PREPARED','UNKNOWN','COMPLETED'].includes(save()!.state)?save()!.state==='COMPLETED'?'打开已保存版本':'读取原保存结果':canCapture()?capture()?'核对最新保存稿':'核对人工修改':capture()?.current.status==='APPLICABLE'?'核对人工修改':'返回交付检查';
 async function reviewPrimary(){
  if(operation()?.state==='UNKNOWN'||operation()?.state==='PREPARED'){await inspectCapture();return;}
  if(save()&&['PREPARED','UNKNOWN','COMPLETED'].includes(save()!.state)){await readSaved();return;}
  if(canCapture()){await beginCapture();return;}
  if(capture()?.current.status==='APPLICABLE'){const node=capture()!.snapshot.files.find(file=>file.node_id&&props.candidate().nodes.some(n=>n.id===file.node_id))?.node_id??props.candidate().nodes[0]?.id;if(node)props.onInspectNode(node);return;}
  const workspace=props.workspace?.();if(workspace)await checkForExport(workspace);
 }
 async function beginCapture(){
  const detail=gate();if(!detail||!canCapture()||operation()?.state==='UNKNOWN'||operation()?.state==='PREPARED'||save()?.state==='UNKNOWN'||save()?.state==='PREPARED')return;
  const previous=operation(),original=await prepareManualCapture(detail,operationId());if(!alive||operation()!==previous||gate()!==detail)return;
  setOperation(original);try{await persist();}catch(e){if(ownsCapture(original))setOperation({...original,state:'REJECTED'});throw e;}
  if(!ownsCapture(original))return;
  let result:ManualCapture;
  try{result=await props.context.dispatch('proposal.manual.capture',props.artifact,structuredClone(original.request)) as ManualCapture;}
  catch(e){if(ownsCapture(original)){const status=(e as {status?:number}).status;setOperation({...original,state:status&&status>=400&&status<500?'REJECTED':'UNKNOWN'});const changed=manualGateFromError(e,props.proposal());if(changed)setGate(changed);}throw e;}
  await acceptCapture(result,original,true);
 }
 async function inspectCapture(){const original=operation();if(!original)return;
  const receipt=await workbenchApi.manualOperation(pid,props.proposal().id,original.request.operation_id,original.request_sha256);
  if(receipt.operation_hash!==original.operation_hash||receipt.request_sha256!==original.request_sha256)throw new Error('核对结果不属于原操作');if(!ownsCapture(original))return;
  if(receipt.status==='COMPLETED'){if(!receipt.capture_ref)throw new Error('原操作没有返回完整核对依据');const result=await workbenchApi.manualCapture(pid,props.proposal().id,receipt.capture_ref);await acceptCapture(result,{...original,capture_ref:receipt.capture_ref},true);return;}
  if(!['NOT_FOUND','INTENT_ONLY','PENDING'].includes(receipt.status))throw new Error('原核对操作状态无法识别');throw new Error('原核对结果尚未完成，已保留原请求，不重复捕获');
 }
 async function refreshCapture(){const original=operation();if(!original?.capture_ref)return;const result=await workbenchApi.manualCapture(pid,props.proposal().id,original.capture_ref);await acceptCapture(result,original,false);}
 function choose(hunkId:string,patch:Partial<ManualChoice>){if(locked())return;setDraft(value=>value?{...value,choices:value.choices.map(choice=>choice.hunk_id===hunkId?{...choice,...patch}:choice)}:value);}
 async function apply(){const current=capture(),review=draft();if(!current||!review)return;const before=props.candidate(),decisions=await manualDecisions(props.proposal(),current,review,false);if(!alive||draft()!==review||props.candidate()!==before)return;const next=applyManualDecisions(props.proposal().payload.candidate,before,decisions,review.applied);props.setCandidate(next);setDraft({...review,applied:decisions});await persist();}
 function openResult(result:PlanningProposal,original:ManualSave){if(!ownsSave(original)||props.canNavigate?.()===false)return;props.context.open({...props.artifact,artifactId:result.id,revision:result.revision,contentHash:result.sha256,data:result});}
 async function readSaved(){const original=save(),viewBefore=JSON.stringify(currentView());if(!original)return;const result=verifyManualSave(await api.planningOperation(pid,original.operation_id) as PlanningProposal,props.proposal(),original);if(!ownsSave(original))return;setSave({...original,state:'COMPLETED',result_ref:manualReference(result)});if(JSON.stringify(currentView())===viewBefore&&(!original.requirement_coverage||!!props.requirementCoverage?.()&&coverageDraftEqual(original.requirement_coverage,props.requirementCoverage()!)))openResult(result,original);}
 async function saveReview():Promise<boolean>{
  if(!draft()&&!capture())return false;if(save()?.state==='UNKNOWN'||save()?.state==='PREPARED')throw new Error('先读取原保存结果，不重复提交');
  const current=capture(),review=draft();if(!current||!review)throw new Error('先读取原人工修改依据');const before=props.candidate(),decisions=await manualDecisions(props.proposal(),current,review,true);if(!alive||draft()!==review||props.candidate()!==before)return true;
  const next=applyManualDecisions(props.proposal().payload.candidate,before,decisions,review.applied),previous=save();const original=await prepareManualSave(props.proposal(),next,props.reason(),{change_ref:current.capture_ref,decisions},operationId(),props.requirementCoverage?.());if(!alive||save()!==previous||props.candidate()!==before||draft()!==review)return true;
  props.setCandidate(next);setDraft({...review,applied:decisions});setSave(original);const viewAtDispatch=JSON.stringify(currentView());
  try{await persist();}catch(e){if(ownsSave(original))setSave({...original,state:'REJECTED'});throw e;}
  if(!ownsSave(original))return true;let result:PlanningProposal;
  try{result=verifyManualSave(await props.context.dispatch('proposal.save',props.artifact,{candidate:original.candidate,reason:original.change_reason,operationId:original.operation_id,manualReview:original.manual_review,...original.requirement_coverage?{requirementCoverage:original.requirement_coverage}:{}}) as PlanningProposal,props.proposal(),original);}
  catch(e){if(ownsSave(original)){const status=(e as {status?:number}).status;setSave({...original,state:(original.requirement_coverage?explicitCoverageRejection(e):status&&status>=400&&status<500)?'REJECTED':'UNKNOWN'});}throw e;}
  if(!ownsSave(original))return true;setSave({...original,state:'COMPLETED',result_ref:manualReference(result)});
  if(JSON.stringify(currentView())===viewAtDispatch)openResult(result,original);return true;
 }
 return {exportBlocked,checkForExport,primaryLabel,reviewPrimary,gate,operation,draft,capture,saved:save,pending,error,locked,reviewLocked,canCapture,lease,receiveExportError,saveReview,choose,run,beginCapture,inspectCapture,refreshCapture,apply,readSaved,onInspectNode:props.onInspectNode,onReturnToExport:props.onReturnToExport,candidate:props.candidate,proposal:props.proposal};
}
export type ManualChangesController=ReturnType<typeof createManualChanges>;
const fields={description:'工作说明',inputs:'输入',outputs:'交付物',acceptance:'验收',risks:'风险'};
export function ManualChangesSummary(props:{controller:ManualChangesController;primaryHosted?:boolean}){
 const c=props.controller;const files=()=>c.capture()?.snapshot.files??[],mapped=(id:string|null)=>!!id&&c.candidate().nodes.some(node=>node.id===id),nodes=()=>[...new Set(files().map(file=>file.node_id).filter((id):id is string=>mapped(id)))],unmapped=()=>files().some(file=>!mapped(file.node_id));
 return <Show when={c.gate()||c.operation()}><section class="as-manual-summary" aria-label={uiText('人工修改核对')}><div><strong>{uiText('人工修改')}</strong><span>{c.saved()?.state==='COMPLETED'?uiText('审阅版本已保存'):c.capture()?uiText(c.capture()!.current.status==='APPLIED'?'{count} 处已核对':'{count} 处待核对',{count:manualHunks(c.capture()!).length}):uiText('{count} 份文件待核对',{count:c.gate()?.files.length??0})}</span></div>
  <Show when={!props.primaryHosted}><Show when={c.operation()?.state==='UNKNOWN'||c.operation()?.state==='PREPARED'} fallback={<Show when={!c.capture()||c.canCapture()}><Button size="small" variant="secondary" disabled={!!c.pending()||c.locked()||!c.canCapture()} onClick={()=>void c.run('核对人工修改',c.beginCapture)}>{uiText(c.capture()?'核对最新保存稿':'核对人工修改')}</Button></Show>}><Button size="small" variant="secondary" disabled={!!c.pending()} onClick={()=>void c.run('读取原核对结果',c.inspectCapture)}>{uiText('读取原核对结果')}</Button></Show></Show>
  <Show when={c.capture()}><For each={nodes()}>{id=><Button size="small" variant="ghost" onClick={()=>c.onInspectNode(id)}>{uiText('核对节点 {value0}',{value0:id})}</Button>}</For><Show when={unmapped()&&c.candidate().nodes.length}><Button size="small" variant="ghost" onClick={()=>c.onInspectNode(c.candidate().nodes[0].id)}>{uiText('核对未匹配修改')}</Button></Show><Button size="small" variant="ghost" disabled={!!c.pending()} onClick={()=>void c.run('核对保存稿状态',c.refreshCapture)}>{uiText('核对保存稿状态')}</Button><Show when={c.capture()!.current.status!=='APPLICABLE'}><p role="status">{uiText(c.capture()!.current.status==='APPLIED'?'这些决定已进入保存版本。':'原依据已变化或尚未完整覆盖，当前草稿保留。')}</p></Show></Show>
  <Show when={!props.primaryHosted&&(c.capture()?.current.status==='STALE'&&!c.canCapture()||c.gate()?.code==='SOURCE_DRIFT')}><Button size="small" variant="secondary" disabled={!!c.pending()} onClick={c.onReturnToExport}>{uiText('返回交付检查')}</Button></Show>
  <Show when={c.gate()&&!c.gate()!.coverage.complete}><p role="status">{uiText('有文件尚未核对，暂不能确认完整交付。')}</p><details><summary>{uiText('未核对的文件')}</summary><For each={c.gate()!.coverage.unverified}>{file=><p>{file.path??uiText('原文件范围')} · {file.reason}</p>}</For></details></Show>
  <Show when={!props.primaryHosted&&c.saved()&&['PREPARED','UNKNOWN','COMPLETED'].includes(c.saved()!.state)}><Button size="small" variant="secondary" disabled={!!c.pending()} onClick={()=>void c.run('读取原保存结果',c.readSaved)}>{uiText(c.saved()!.state==='COMPLETED'?'打开已保存版本':'读取原保存结果')}</Button></Show>
  <Show when={c.error()}><p role="alert" class="as-inline-error">{authoredText(c.error(),uiText)}</p></Show>
 </section></Show>;
}
export function ManualChangesNode(props:{controller:ManualChangesController;nodeId:string}){
 const c=props.controller;
 // ObjectDraft notifies every accessor on choice/view/save-state changes.
 // Keep immutable hunk wrappers stable so those notifications do not replace
 // a focused input's For subtree. New captures and actual node placement still
 // publish a new projection; no previous captures are retained in a cache.
 const projection=createMemo(()=>{
  const capture=c.capture(),nodeId=props.nodeId,known=new Set(c.candidate().nodes.map(node=>node.id));
  return {scope:capture?JSON.stringify(capture.capture_ref):'',nodeId,
   items:capture?manualHunks(capture).filter(({file})=>file.node_id===nodeId||!file.node_id||!known.has(file.node_id)):[]};
 },undefined,{equals:(before,after)=>!!before&&before.scope===after.scope&&before.nodeId===after.nodeId&&before.items.length===after.items.length&&before.items.every((item,index)=>item.hunk.id===after.items[index].hunk.id&&item.hunk.sha256===after.items[index].hunk.sha256&&item.file.file_ref.path===after.items[index].file.file_ref.path)});
 const rows=()=>projection().items,choice=(id:string)=>c.draft()?.choices.find(item=>item.hunk_id===id);
 return <Show when={rows().length&&c.draft()}><section class="as-manual-node" aria-label={uiText('此节点的人工修改')}><h4>{uiText('人工修改')}</h4><For each={rows()}>{({file,hunk})=><article class="as-manual-hunk" data-manual-hunk={hunk.id}><small>{file.file_ref.path}</small><details><summary>{uiText('查看原文与改文')}</summary><div class="as-manual-diff"><div><span>{uiText('原文')}</span><pre>{hunk.removed_text||uiText('无')}</pre></div><div><span>{uiText('人工改文')}</span><pre>{hunk.added_text||uiText('删除')}</pre></div></div></details>
  <div class="as-manual-modes" role="group" aria-label={uiText('处理此处修改')}><For each={['KEEP','REPLACE','DISCARD'] as const}>{mode=><button type="button" aria-pressed={choice(hunk.id)?.mode===mode} disabled={c.reviewLocked()} onClick={()=>c.choose(hunk.id,{mode})}>{uiText(({KEEP:'采用',REPLACE:'改写',DISCARD:'不采用'})[mode])}</button>}</For></div>
  <Show when={choice(hunk.id)?.mode&&choice(hunk.id)!.mode!=='DISCARD'}><label>{uiText('放入节点')}<select aria-label={uiText('人工修改目标节点')} disabled={c.reviewLocked()} value={choice(hunk.id)?.node_id??''} onChange={e=>c.choose(hunk.id,{node_id:e.currentTarget.value})}><option value="">{uiText('选择实际节点')}</option><For each={c.candidate().nodes.filter(node=>c.proposal().payload.candidate.nodes.some(original=>original.id===node.id))}>{node=><option value={node.id}>{node.title}</option>}</For></select></label><label>{uiText('目标字段')}<select aria-label={uiText('人工修改目标字段')} value={choice(hunk.id)?.field??''} disabled={c.reviewLocked()} onChange={e=>c.choose(hunk.id,{field:e.currentTarget.value as ManualChoice['field'],edit_action:'',item_index:''})}><option value="">{uiText('选择字段')}</option><For each={Object.entries(fields)}>{([key,label])=><option value={key}>{uiText(label)}</option>}</For></select></label>
  <Show when={choice(hunk.id)?.field}><label>{uiText('采用方式')}<select aria-label={uiText('人工修改采用方式')} disabled={c.reviewLocked()} value={choice(hunk.id)?.edit_action??''} onChange={e=>c.choose(hunk.id,{edit_action:e.currentTarget.value as ManualChoice['edit_action'],item_index:''})}><option value="">{uiText('选择方式')}</option><Show when={choice(hunk.id)?.field!=='description'}><option value="ADD">{uiText('新增条目')}</option></Show><option value="REPLACE">{uiText('替换')}</option><Show when={choice(hunk.id)?.field!=='description'&&!hunk.added_text}><option value="REMOVE">{uiText('删除原条目')}</option></Show></select></label>
  <Show when={choice(hunk.id)?.field!=='description'&&['REPLACE','REMOVE'].includes(choice(hunk.id)?.edit_action??'')}><label>{uiText('原条目')}<select aria-label={uiText('人工修改原条目')} value={choice(hunk.id)?.item_index??''} disabled={c.reviewLocked()} onChange={e=>c.choose(hunk.id,{item_index:e.currentTarget.value})}><option value="">{uiText('选择原条目')}</option><For each={(c.proposal().payload.candidate.nodes.find(node=>node.id===choice(hunk.id)?.node_id)?.[choice(hunk.id)!.field as keyof typeof fields]??[]) as string[]}>{(text,index)=><option value={String(index())}>{text}</option>}</For></select></label></Show></Show>
  <Show when={choice(hunk.id)?.mode==='KEEP'&&choice(hunk.id)?.edit_action!=='REMOVE'}><p class="as-manual-wording">{hunk.keep_text}</p></Show>
  <Show when={choice(hunk.id)?.mode==='REPLACE'&&choice(hunk.id)?.edit_action!=='REMOVE'}><label>{uiText('采用的文字')}<textarea aria-label={uiText('改写人工修改')} maxLength={24000} disabled={c.reviewLocked()} value={choice(hunk.id)?.text??''} onInput={e=>c.choose(hunk.id,{text:e.currentTarget.value})}/></label></Show></Show>
  <Show when={choice(hunk.id)?.mode}><label>{uiText('决定理由')}<input aria-label={uiText('人工修改决定理由')} maxLength={4000} disabled={c.reviewLocked()} value={choice(hunk.id)?.reason??''} onInput={e=>c.choose(hunk.id,{reason:e.currentTarget.value})}/></label></Show>
 </article>}</For><Button size="small" variant="secondary" disabled={c.reviewLocked()} onClick={()=>void c.run('放入节点草稿',c.apply)}>{uiText('放入节点草稿')}</Button></section></Show>;
}
