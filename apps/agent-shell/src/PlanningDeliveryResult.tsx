import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText} from './ui-language';
import { createSignal, For, Show } from 'solid-js';
import { Icon } from '@opencode-ai/ui/icon';
import type { ArtifactEnvelope, RendererContext } from './contracts';
import { nativeCollaborationBridge } from './native-collaboration';
import { deliveryComplete, deliveryFiles, deliveryFileStatus, type DeliveryOperation } from './planning-delivery';
import DeliveryArtifactCard from './DeliveryArtifactCard';

export default function PlanningDeliveryResult(props:{value:DeliveryOperation;artifact:ArtifactEnvelope;context:RendererContext}) {
  const [pending,setPending]=createSignal(''),[error,setError]=createSignal<UIErrorCopy>('');
  const [continued,setContinued]=createSignal<{source:DeliveryOperation;value:DeliveryOperation}>();
  const currentValue=()=>continued()?.source===props.value?continued()!.value:props.value;
  const adoptContinuation=(value:DeliveryOperation)=>setContinued({source:props.value,value});
  const bridge=nativeCollaborationBridge(globalThis);
  const complete=()=>{const value=currentValue();return deliveryComplete(value)?value:undefined;};
  // Keep a partial Card's original source object so a status observation does not clear its error or retry state.
  const cardValue=()=>complete()?currentValue():props.value;
  const deliveryArtifact=():ArtifactEnvelope=>({artifactId:cardValue().operation_id,type:'planning-delivery',schemaVersion:1,revision:(complete()?.proposal_ref??complete()?.session_ref)?.revision??props.artifact.revision,scope:props.artifact.scope,availability:props.artifact.availability,data:cardValue()});
  const operationStatus=()=>complete()?uiText('已保存'):currentValue().status==='PROCESSING'?uiText('处理中'):currentValue().status==='INTERRUPTED_REVIEW_REQUIRED'?uiText('已中断，需核对原步骤'):uiText('失败或部分完成');
  async function open(action:string,input:unknown,label:string){if(pending())return;setPending(label);setError('');try{await props.context.dispatch(action,props.artifact,input);}catch(error){setError(authoredErrorCopy(error));}finally{setPending('');}}
  return <section class="as-delivery-result" aria-label={uiText("实际阶段与交付产物")}>
    <Show when={complete()} keyed>{result=><>
      <div class="as-delivery-heading"><strong>{result.status==='STAGE_REPORT'?uiText("阶段产物已保存"):uiText("完整交付文件已保存")}</strong><span>{uiText("依据 v")}{(result.session_ref??result.proposal_ref)?.revision}</span></div>
      <p>{result.status==='STAGE_REPORT'?uiText("待确认 {value0} 项，其中 {value1} 项影响后续确认。可继续在页面回答。",{value0:String(result.pending_count??0),value1:String(result.blocking_count??0)}):uiText("任务书、同源图与 harness 已按确认计划派生；任务书仍为派生草稿，实际执行尚未授权。")}</p>
      <DeliveryArtifactCard artifact={deliveryArtifact()} context={props.context} onUpdated={adoptContinuation}/>
      <Show when={deliveryFiles(result).length}><details class="as-delivery-file-checks"><summary>{uiText('文件核对与来源（{count}）',{count:deliveryFiles(result).length})}</summary><Show when={result.version_directory}><p class="as-delivery-directory">{uiText("目录：")}{result.version_directory}</p></Show>
      <div class="as-delivery-files"><For each={deliveryFiles(result)}>{file=><article><button disabled={!!pending()} onClick={()=>void open('delivery.file.open',{receipt:result,file},file.path)}><Icon name="open-file"/><span>{file.path}<small>{file.file_source_ref??file.source_ref?uiText("文件 v{value0}",{value0:String((file.file_source_ref??file.source_ref)!.revision)}):uiText("点开时读取实际文件来源")} · {deliveryFileStatus(result,file)==='CURRENT'?uiText('版本一致'):uiText('等待核对')}</small></span><span>{pending()===file.path?uiText("核对中…"):bridge?.openDocument?uiText("原软件打开"):uiText("读取文件")}</span></button><Show when={file.file_source_ref??file.source_ref}>{source=><details><summary>{uiText('来源版本')}</summary><dl><dt>{uiText('项目内文件')}</dt><dd>{source().path}</dd><dt>{uiText('来源版本')}</dt><dd>v{source().revision}</dd><dt>{uiText('内容校验')}</dt><dd>{source().sha256}</dd></dl></details>}</Show></article>}</For></div></details></Show>
      <Show when={!deliveryFiles(result).length}><p class="as-muted">{uiText("本次保存于产物库，未写入项目目录。")}</p></Show>
      <details class="as-delivery-downloads" open={!deliveryFiles(result).length}><summary>{uiText("产物库原件与下载")}</summary><div class="as-delivery-actions"><For each={result.artifacts}>{item=><a class="as-text-action" href={item.download_url} download={item.filename}>{item.kind==='docx'?uiText("Word 原件"):item.kind.startsWith('diagram_')?`${item.filename}`:item.filename}</a>}</For></div></details>
    </>}</Show>
    <Show when={!complete()}><p class="as-inline-warning">{currentValue().status==='PROCESSING'?uiText("操作尚在处理中"):currentValue().status==='INTERRUPTED_REVIEW_REQUIRED'?uiText("操作已中断，需要核对原步骤"):uiText("操作失败或仅完成部分步骤")}{uiText("。已保存步骤保留，不会自动重放。")}</p><DeliveryArtifactCard artifact={deliveryArtifact()} context={props.context} onUpdated={adoptContinuation}/></Show>
    <details class="as-delivery-receipt"><summary>{uiText("来源、文件核对与持久回执")}</summary><dl><dt>{uiText('操作状态')}</dt><dd>{operationStatus()}</dd><Show when={complete()}>{value=><><dt>{uiText('依据版本')}</dt><dd>v{(value().proposal_ref??value().session_ref)?.revision}</dd><dt>{uiText('可打开文件')}</dt><dd>{deliveryFiles(value()).length}</dd></>}</Show></dl></details>
    <Show when={error()}><p class="as-inline-error" role="alert">{authoredText(error(),uiText)}</p></Show>
  </section>;
}
export function PlanningDeliveryRenderer(props:{artifact:ArtifactEnvelope;context:RendererContext}){return <div class="as-object"><h2>{uiText("阶段与交付文件")}</h2><PlanningDeliveryResult value={props.artifact.data as DeliveryOperation} artifact={props.artifact} context={props.context}/></div>;}
