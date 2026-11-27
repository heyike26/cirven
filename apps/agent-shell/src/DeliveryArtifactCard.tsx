import DeliveryReviewSummary from './DeliveryReviewSummary';
import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText} from './ui-language';
import { createEffect, createSignal, onCleanup, For, Show } from 'solid-js';
import { Icon } from '@opencode-ai/ui/icon';
import type { ArtifactEnvelope, RendererContext } from './contracts';
import { chatDeliveryFiles, openChatDeliveryFile, type ChatDeliveryFile } from './chat-artifacts';
import { nativeCollaborationBridge } from './native-collaboration';
import { deliveryComplete, deliveryFileStatus, savedDeliveryReport, resumableExport, assertResumedExport, originalExportObservation, DeliveryError, type DeliveryOperation } from './planning-delivery';

export default function DeliveryArtifactCard(props: { artifact: ArtifactEnvelope; context: RendererContext; onUpdated?:(value:DeliveryOperation)=>void }) {
  const [pending, setPending] = createSignal(''), [error, setError] = createSignal<UIErrorCopy>('');
  const [currentChoice,setCurrentChoice]=createSignal<ChatDeliveryFile>();
  const [unverified,setUnverified]=createSignal(new Set<string>());
  const sourceKey=(file:ChatDeliveryFile)=>JSON.stringify([file.source.project_id,file.source.workspace_id,file.source.binding_revision,file.source.binding_sha256,file.source.path,file.source.revision,file.source.sha256]);
  const bridge = nativeCollaborationBridge(globalThis);
  const [continued,setContinued]=createSignal<{source:DeliveryOperation;value:DeliveryOperation}>();
  const [resumeObservation,setResumeObservation]=createSignal<{source:DeliveryOperation;value?:DeliveryOperation}>();
  const sourceReceipt=()=>props.artifact.data as DeliveryOperation;
  const receipt=()=>continued()?.source===sourceReceipt()?continued()!.value:sourceReceipt();
  const resume=()=>{const observed=resumeObservation();return observed?.source===sourceReceipt()?(observed.value?resumableExport(observed.value,props.artifact.scope.projectId):undefined):resumableExport(receipt(),props.artifact.scope.projectId);};
  let element:HTMLElement|undefined;
  const complete = () => { const value = receipt(); return deliveryComplete(value) ? value : undefined; };
  const savedReport=()=>savedDeliveryReport(receipt(),props.artifact.scope.projectId);
  const actionIdentity=()=>JSON.stringify([props.artifact.scope.projectId,props.artifact.scope.workspaceId??null,props.artifact.scope.sessionId??null,props.artifact.scope.runId??null,props.artifact.artifactId,props.artifact.revision,props.artifact.contentHash??null,receipt().operation_id,receipt().status,(complete()?.report??savedReport()?.report)?.artifact.id,(complete()?.report??savedReport()?.report)?.artifact.sha256]);
  let alive=true,generation=0,lastIdentity=actionIdentity();onCleanup(()=>{alive=false;generation++;});
  createEffect(()=>{const key=actionIdentity();if(key!==lastIdentity){lastIdentity=key;generation++;setPending('');setError('');setCurrentChoice(undefined);setUnverified(new Set<string>());}});
  const files = () => chatDeliveryFiles(receipt());
  const status = (file: ChatDeliveryFile) => { const value = complete(); return !unverified().has(sourceKey(file))&&value ? deliveryFileStatus(value, file.file) : 'UNKNOWN'; };
  const statusLabel = (file:ChatDeliveryFile) => (({CURRENT:uiText('版本一致'),STALE:uiText('文件已变化'),MISSING:uiText('文件不存在'),UNKNOWN:uiText('来源待核对')} as Record<string,string>)[status(file)]??uiText('来源待核对'));
  async function open(file: ChatDeliveryFile,current=false) {
    if (pending()) return;
    const key=actionIdentity(),token=++generation,target=props.artifact,value=receipt();
    const owns=()=>alive&&generation===token&&actionIdentity()===key;
    setPending(file.source.path); setError('');
    try { await openChatDeliveryFile(props.context, target, value, file,current);if(owns()){if(!current)setUnverified(previous=>{const next=new Set(previous);next.delete(sourceKey(file));return next;});setCurrentChoice(undefined);} }
    catch (error) { if(owns()){setUnverified(previous=>new Set([...previous,sourceKey(file)]));setError(authoredErrorCopy(error));setCurrentChoice(file);} }
    finally { if(owns())setPending(''); }
  }
  async function openReport() {
    const value=complete(),saved=savedReport();
    if (pending() || (!value&&!saved)) return;
    const key=actionIdentity(),token=++generation,target=props.artifact,input=value?{receipt:value}:{report:saved!.report};
    const owns=()=>alive&&generation===token&&actionIdentity()===key;
    setPending('report'); setError('');
    try { await props.context.dispatch('delivery.report.open', target, input); }
    catch (error) { if(owns())setError(authoredErrorCopy(error)); }
    finally { if(owns())setPending(''); }
  }
  async function continueOriginal() {
    const observed=resume();
    if(pending()||!observed||!element?.isConnected||!element.getClientRects().length||element.closest('[hidden],[inert],[aria-hidden="true"]'))return;
    const key=actionIdentity(),token=++generation,target=props.artifact,source=sourceReceipt();
    const owns=()=>alive&&generation===token&&actionIdentity()===key;
    setPending('resume');setError('');
    try {
      const value=await props.context.dispatch('delivery.export.resume',target,undefined);
      if(value===undefined||!owns())return;
      const result=assertResumedExport(value,observed);
      setContinued({source,value:result});props.onUpdated?.(result);
    }catch(error){if(owns()){
      if(error instanceof DeliveryError&&error.operationId===source.operation_id){
        const current=originalExportObservation(error.receipt,observed);
        setResumeObservation({source,value:current});
        if(current)props.onUpdated?.(current);
      }
      setError(authoredErrorCopy(error));
    }}finally{if(owns())setPending('');}
  }
  const fileButton = (file: ChatDeliveryFile) => <button class="as-chat-artifact" disabled={!!pending()} title={file.source.path} onClick={() => void open(file)}>
    <Icon name="open-file"/><span><span class="as-chat-artifact-name">{file.kind==='HARNESS'?uiText("项目任务入口"):file.kind==='PENDING'?uiText("待确认"):file.diagramNumber?`${uiText("图")} ${file.diagramNumber} · ${file.filename.split('.').at(-1)?.toUpperCase()}`:file.label??file.filename}</span><small>{file.kind === 'WORD' ? 'Word' : file.kind === 'DIAGRAM' ? uiText("图") : file.kind==='HARNESS'||file.kind==='PENDING'?'Markdown':uiText("项目文件")} · {complete()?.proposal_ref?uiText("计划 v{revision}",{revision:complete()!.proposal_ref!.revision}):complete()?.session_ref?uiText("要求 v{revision}",{revision:complete()!.session_ref!.revision}):'v'+file.source.revision} · {statusLabel(file)}</small></span>
    <small>{pending() === file.source.path ? uiText("核对中…") : bridge?.openDocument ? uiText("原软件打开") : uiText("工作台预览")}</small>
  </button>;
  return <section ref={element} class="as-chat-delivery" aria-busy={!!pending()} aria-label={uiText("回答中的交付文件")}>
    <For each={files().primary}>{file => fileButton(file)}</For>
    <Show when={!files().all.length && complete()}><button class="as-chat-artifact" disabled={!!pending()} onClick={() => void openReport()}><Icon name="open-file"/><span class="as-chat-artifact-name">{complete()?.report.artifact.filename ?? 'Word'}</span><small>{pending() === 'report' ? uiText("核对中…") : bridge?.openReportArtifact ? uiText("原软件打开") : uiText("工作台预览")}</small></button></Show>
    <Show when={savedReport()} keyed>{saved=><button class="as-chat-artifact" disabled={!!pending()} onClick={()=>void openReport()}><Icon name="open-file"/><span><span class="as-chat-artifact-name">{saved.report.artifact.filename}</span><small>{uiText('报告已生成')} · {saved.sourceKind==='PROPOSAL'?uiText('计划 v{revision}',{revision:saved.basis.revision}):uiText('要求 v{revision}',{revision:saved.basis.revision})}</small></span><small>{pending()==='report'?uiText('核对中…'):bridge?.openReportArtifact?uiText('原软件打开'):uiText('工作台预览')}</small></button>}</Show>
    <Show when={resume()}><button type="button" class="as-text-action" disabled={!!pending()} onClick={()=>void continueOriginal()}>{pending()==='resume'?uiText('继续中…'):uiText('继续原交付')}</button></Show>
    <Show when={files().remaining.length}><details class="as-chat-delivery-files"><summary>{uiText("其他文件（{count}）",{count:files().remaining.length})}</summary><For each={files().remaining}>{file => fileButton(file)}</For></details></Show>
    <Show when={files().missingSources}><p class="as-inline-warning">{uiText("另有 ")}{files().missingSources}{uiText(" 份目录产物尚未返回实际文件来源，请核对回执。")}</p></Show>
    <Show when={complete()}>{value=><DeliveryReviewSummary receipt={value()}/>}</Show>
    <Show when={complete()}>{value=><details class="as-chat-delivery-receipt"><summary>{uiText("交付依据与回执")}</summary><dl><dt>{uiText("依据版本")}</dt><dd>v{(value().session_ref??value().proposal_ref)!.revision}</dd><dt>{uiText("可打开文件")}</dt><dd>{files().userFiles.length||(!files().all.length?1:0)}</dd><dt>{uiText("执行状态")}</dt><dd>{uiText("仅生成交付文件，尚未授权执行任务。")}</dd></dl></details>}</Show>
    <Show when={error()}><p class="as-inline-error" role="alert">{authoredText(error(),uiText)}</p></Show>
    <Show when={currentChoice()}>{file=><div class="as-companion-file-actions"><button type="button" disabled={!!pending()} onClick={()=>void open(file(),true)}>{uiText("核对并打开当前稿")}</button><button type="button" disabled={!!pending()} onClick={()=>setCurrentChoice(undefined)}>{uiText("取消")}</button><small>{uiText("保留旧交付依据，只读取同一位置的当前保存稿；不覆盖人工修改。")}</small></div>}</Show>
  </section>;
}
