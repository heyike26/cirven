import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText} from './ui-language';
import { createSignal, For, onCleanup, Show } from 'solid-js';
import { Button } from '@opencode-ai/ui/button';
import { Icon } from '@opencode-ai/ui/icon';
import type { ArtifactEnvelope, FileContent, Harness, HarnessState, RendererContext, ReportArtifact, SourceRef, TaskBook } from './contracts';
import type { ModuleRegistry } from './registry';
import { api } from './api';
import TaskGraph from './TaskGraph';
import ProposalRenderer from './ProposalRenderer';
import ProviderRenderer from './ProviderRenderer';
import DiscoveryRenderer from './DiscoveryRenderer';
import { PlanningDeliveryRenderer } from './PlanningDeliveryResult';
import CompetitionRenderer from './CompetitionRenderer';
import ExperienceRenderer from './ExperienceRenderer';
import WordPreview from './WordPreview';
import {SavedTextFeedbacks} from './TextFeedbackEditor';
import NativeDocument from './NativeDocument';
import BitmapFile from './BitmapFile';
import {stableItems} from './stable-items';
import {draftSignal,DraftStatus,useDraftSession} from './ObjectDraft';
import type {DiscoverySession} from './discovery-contracts';
import {ArtifactSummary,PlanningCapabilitiesSummary} from './ArtifactSummary';

type Props = { artifact: ArtifactEnvelope; context: RendererContext };
export function GenericRenderer(props: Props) { return <ArtifactSummary {...props}/>; }
export function FileRenderer(props: Props) {
  const file = () => props.artifact.data as FileContent;
  const owner=useDraftSession();const [draft, setDraft] = draftSignal('text',file().content);
  const [pending, setPending] = createSignal(false);
  const [error, setError] = createSignal<UIErrorCopy>('');
  const [readFailed,setReadFailed]=createSignal(false),[syncError,setSyncError]=createSignal<UIErrorCopy>('');
  const [saved, setSaved] = createSignal<SourceRef>();
  const [available,setAvailable]=createSignal<SourceRef>();
  const [editEpoch,setEditEpoch]=createSignal(0),[composing,setComposing]=createSignal(false);
  let alive=true;onCleanup(()=>{alive=false;});
  async function readCurrent(epoch:number,expected?:SourceRef){
    await owner?.session?.finish();
    if(!alive||editEpoch()!==epoch||composing())return;
    const result=await props.context.dispatch('workspace.file.reload',props.artifact,{expected,canPresent:()=>alive&&editEpoch()===epoch&&!composing()}) as {file:FileContent;presented:boolean};
    if(alive&&!result.presented&&(result.file.source_ref.sha256!==file().source_ref.sha256||result.file.source_ref.revision!==file().source_ref.revision))setAvailable(result.file.source_ref);
    return result;
  }
  async function reload(){if(pending())return;setPending(true);setError('');setReadFailed(false);const epoch=editEpoch();try{await readCurrent(epoch);}catch(e){if(alive){setReadFailed(!!saved());setError(authoredErrorCopy(e));}}finally{if(alive)setPending(false);}}
  async function save(){
    if(pending()||saved()||available())return;setPending(true);setError('');const submitted=draft(),epoch=editEpoch();
    try{
      const result=await props.context.dispatch('workspace.file.save',props.artifact,submitted) as {source_ref:SourceRef;syncContext?:(canPublish:()=>boolean)=>Promise<void>};
      if(!alive)return;setSaved(result.source_ref);
      // The write receipt remains successful even if this separate GET fails.
      try{const latest=await readCurrent(epoch,result.source_ref);if(latest?.presented&&editEpoch()===epoch)owner?.session?.markClean();}
      catch(e){if(alive){setReadFailed(true);setError(authoredErrorCopy(e));}}
      if(alive&&editEpoch()===epoch&&!composing())try{await result.syncContext?.(()=>alive&&editEpoch()===epoch&&!composing());}catch(e){if(alive)setSyncError(authoredErrorCopy(e));}
    }catch(e){if(alive)setError(authoredErrorCopy(e));}finally{if(alive)setPending(false);}
  }
  return <div class="as-object as-editor"><DraftStatus/><div class="as-object-meta"><span>{uiText("文件版本 ")}{file().source_ref.revision}</span><span>{file().byte_length} bytes</span><span class="as-hash" title={file().source_ref.sha256}>{file().source_ref.sha256.slice(0,12)}</span><Button variant="ghost" size="small" data-file-reload aria-disabled={pending()} aria-busy={pending()} aria-label={uiText('读取当前版本')} title={uiText('读取当前版本')} onClick={reload}><Icon name="reset" size="small"/></Button></div>
    <Show when={!file().current}><p class="as-inline-warning">{uiText("磁盘文件已变化。先重新打开并核对；保存时后端会检查原 SHA，草稿不会自动覆盖磁盘。")}</p></Show>
    <Show when={file().encoding === 'utf-8'} fallback={<Show when={/\.(png|jpe?g)$/i.test(file().source_ref.path)} fallback={<NativeDocument file={file()} artifact={props.artifact} context={props.context} autoOpen/>}><BitmapFile file={file()} artifact={props.artifact} context={props.context}/></Show>}><textarea class="as-file-editor" data-editor-epoch={editEpoch()} data-editor-composing={composing()?'true':'false'} aria-label={uiText("编辑工作文件")} spellcheck={false} value={draft()} onInput={e => {setEditEpoch(value=>value+1);setDraft(e.currentTarget.value);}} onCompositionStart={()=>{setComposing(true);setEditEpoch(value=>value+1);}} onCompositionEnd={()=>{setComposing(false);setEditEpoch(value=>value+1);}} /><div class="as-editor-footer"><span>{draft() === file().content ? uiText("与打开的版本一致") : uiText("有未保存修改")}</span><Button variant="primary" disabled={pending() || !!saved() || !!available() || draft() === file().content} onClick={save}>{pending() ? uiText("保存中…") : uiText("保存文件")}</Button></div></Show>
    <Show when={/\.(png|jpe?g)$/i.test(file().source_ref.path)}><SavedTextFeedbacks onSubmit={annotation=>{const locator=annotation.selection.text_selection.locator;if(locator.kind!=='OCR_LINE')throw new Error('此批注不属于当前图片的 OCR 选文');return props.context.dispatch('bitmap.file.feedback',props.artifact,{...annotation,selection:{...annotation.selection,bitmap_ref:{kind:'LOCAL_FILE',file_source_ref:file().source_ref,sha256:file().source_ref.sha256},ocr_ref:locator.observation_ref,node_ids:[],normalized_points:[]}});}}/></Show><Show when={error()}><Show when={readFailed()} fallback={<p class="as-inline-error" role="alert">{authoredText(error(),uiText)}</p>}><p class="as-inline-error" role="alert">{uiText('文件已保存，但新版本尚未读到。请读取当前版本。')}</p><details><summary>{uiText('查看详情')}</summary><p>{authoredText(error(),uiText)}</p></details></Show></Show><Show when={syncError()}><p class="as-inline-warning">{uiText('同步原件')} · {authoredText(syncError(),uiText)}</p></Show><Show when={saved()}>{source=><p class="as-inline-success">{uiText('已保存 · ')}{uiText('文件版本 ')}{source().revision} · {source().sha256.slice(0,12)}</p>}</Show><Show when={available()}>{source=><p class="as-inline-warning">{uiText('实际更新版本 v')}{source().revision} · {source().sha256.slice(0,12)} · {uiText('当前显示历史版本，草稿保留。')}</p>}</Show>
  </div>;
}
function BookRenderer(props: Props) {
  const book = () => props.artifact.data as TaskBook;
  const owner=useDraftSession();const [sections, setSections] = draftSignal('sections',structuredClone(book().payload.sections));
  const [editing, setEditing] = draftSignal('editing',false,'view'); const [pending, setPending] = createSignal(false); const [error, setError] = createSignal<UIErrorCopy>('');
  async function save() { setPending(true); setError(''); try { const submitted=sections();await props.context.dispatch('taskbook.save', props.artifact, submitted);if(sections()===submitted){owner?.session?.markClean();setEditing(false);} } catch (e) { setError(authoredErrorCopy(e)); } finally { setPending(false); } }
  const renderedSections=stableItems(sections,value=>value.section_id);
  function change(sectionId: string, entryId: string, text: string) { setSections(value => value.map(section => section.section_id === sectionId ? { ...section, entries: section.entries.map(entry => entry.entry_id === entryId ? { ...entry, text,origin:'USER_STATEMENT',acceptance_reason:null } : entry) } : section)); }
  return <div class="as-object as-document"><DraftStatus/><div class="as-object-meta"><span>{uiText("任务书 · 版本 ")}{book().revision}</span><span>{book().review_status === 'CURRENT' ? uiText("来源当前有效") : uiText("来源待复核")}</span><Button variant="ghost" size="small" onClick={() => setEditing(!editing())}>{editing() ? uiText("退出编辑") : uiText("编辑文稿")}</Button></div><h2>{book().payload.title}</h2>
    <For each={renderedSections()}>{section => {const renderedEntries=stableItems(()=>section.entries,value=>value.entry_id);return <section class="as-book-section"><h3>{section.heading}</h3><Show when={section.entries.length} fallback={<p class="as-muted">{uiText("此节尚未补充。")}</p>}><For each={renderedEntries()}>{entry => <div class="as-book-entry"><span class="as-origin">{uiText(({ SOURCE_FACT: '来源事实', USER_STATEMENT: '用户陈述', MODEL_SUGGESTION: '模型建议', ASSUMPTION: '待确认假设', ACCEPTED_ASSUMPTION: '已接受假设' } as Record<string,string>)[entry.origin] ?? entry.origin)}</span><Show when={editing()} fallback={<p>{entry.text}</p>}><textarea aria-label={uiText("{value0}内容",{value0:String(section.heading)})} value={entry.text} onInput={e => change(section.section_id, entry.entry_id, e.currentTarget.value)} /></Show><Show when={entry.source_anchors.length}><details><summary>{entry.source_anchors.length}{uiText(" 处精确来源")}</summary><pre class="as-json">{JSON.stringify(entry.source_anchors,null,2)}</pre></details></Show></div>}</For></Show></section>;}}</For>
    <Show when={editing()}><div class="as-editor-footer"><span>{uiText("保存将追加新版本")}</span><Button variant="primary" disabled={pending()} onClick={save}>{pending() ? uiText("保存中…") : uiText("保存新版本")}</Button></div></Show><Show when={error()}><p role="alert" class="as-inline-error">{authoredText(error(),uiText)}</p></Show>
  </div>;
}
function HarnessRenderer(props: Props) {
  const value = () => props.artifact.data as { harness: Harness; state: HarnessState };
  const state = (id: string) => value().state.nodes.find(node => node.node_id === id);
  const [selected, setSelected] = createSignal<string>(); const [inspectedIds,setInspectedIds]=draftSignal<string[]>('selected',[],'view');const inspected=()=>inspectedIds()[0];const setInspected=(value:string)=>setInspectedIds([value]); const [reason, setReason] = createSignal(''); const [pending, setPending] = createSignal(false); const [error, setError] = createSignal<UIErrorCopy>('');
  async function start() { setPending(true); setError(''); try { await props.context.dispatch('harness.node.start', props.artifact, { nodeId: selected(), reason: reason() }); setSelected(undefined); } catch (e) { setError(authoredErrorCopy(e)); } finally { setPending(false); } }
  return <div class="as-object as-flow"><div class="as-object-meta"><span>{uiText("任务链 · 版本 ")}{value().harness.revision}</span><span>{value().state.status}</span></div><h2>{value().harness.payload.title}</h2><p class="as-muted">{uiText("依赖通过验收后，下游任务才能开始。每个任务保留自己的运行与证据。")}</p><TaskGraph nodes={value().harness.payload.nodes} states={value().state.nodes} onSelect={setInspected}/><details open={!!inspected()} class="as-node-details"><summary>{inspected()?uiText("节点 {value0} 的详情",{value0:String(inspected())}):uiText("逐项查看任务")}</summary><div class="as-dag" aria-label={uiText("任务节点详情")}>
    <For each={value().harness.payload.nodes.filter(node=>!inspected()||node.node_id===inspected())}>{node => <article class="as-task-node" data-state={state(node.node_id)?.status ?? 'UNKNOWN'}><div class="as-node-head"><span class="as-state-dot"/><strong>{node.node_id}</strong><span class="as-state-label">{state(node.node_id)?.latest_run?.verification_status ?? state(node.node_id)?.status ?? uiText("待复核")}</span></div><p>{node.role}</p><Show when={node.dependency_node_ids.length}><div class="as-dependencies">{uiText("依赖 ")}<For each={node.dependency_node_ids}>{id => <button onClick={() => setInspected(id)}>{id}</button>}</For></div></Show><div id={`as-node-${node.node_id}`} class="as-output-list"><For each={node.expected_outputs}>{output => <span>{output}</span>}</For></div><details><summary>{uiText("工具与绑定任务")}</summary><p>{node.tool_ids.join('、')}</p><code>{node.task_ref.task_id}</code></details><Show when={state(node.node_id)?.latest_run}><p class="as-muted">{uiText("运行 ")}{state(node.node_id)!.latest_run!.id}</p></Show><p class="as-muted">{uiText("本流程交付到 harness 文件；任务执行由后续工作显式决定。")}</p></article>}</For></div></details>
    <Show when={selected()}><div class="as-start-gate"><strong>{uiText("开始 ")}{selected()}</strong><label>{uiText("此次开始的范围与理由")}<textarea value={reason()} onInput={e => setReason(e.currentTarget.value)} placeholder={uiText("说明已确认的输入和此次要完成的范围")} /></label><div><Button variant="ghost" onClick={() => setSelected(undefined)}>{uiText("取消")}</Button><Button variant="primary" disabled={pending() || !reason().trim()} onClick={start}>{pending() ? uiText("提交中…") : uiText("明确开始")}</Button></div></div></Show><Show when={error()}><p class="as-inline-error" role="alert">{authoredText(error(),uiText)}</p></Show>
  </div>;
}
function ReportRenderer(props:Props){const report=()=>props.artifact.data as ReportArtifact,source=()=>report().source_session as DiscoverySession|undefined;const target=()=>{const value=source()!;return{...props.artifact,type:'discovery',artifactId:value.id,revision:value.revision,contentHash:value.sha256,data:value} as ArtifactEnvelope;};return <div class="as-object"><DraftStatus/><h2>{report().filename??uiText("报告产物")}</h2><Show when={typeof report().download_url==='string'&&typeof report().filename==='string'&&/\.docx$/i.test(report().filename as string)}><WordPreview url={report().download_url as string} sha256={report().sha256} report={source()?{projectId:source()!.project_id,sourceId:source()!.id,sourceKind:'DISCOVERY',artifactId:report().id,sessionRef:{id:source()!.id,revision:source()!.revision,sha256:source()!.sha256}}:undefined} onFeedback={source()?value=>props.context.dispatch('workbench.feedback',target(),value):undefined}/></Show><Show when={source()}><SavedTextFeedbacks onSubmit={value=>props.context.dispatch('workbench.feedback',target(),value)}/></Show><a class="as-download" href={typeof report().download_url==='string'?report().download_url as string:api.reportDownload(props.artifact.scope.projectId,report().id)}>{uiText("下载报告")}</a><Show when={typeof report().pending_download_url==='string'}><a class="as-text-action" href={report().pending_download_url as string}>{uiText("下载独立待确认文件")}</a></Show><details><summary>{uiText("文稿来源与生成回执")}</summary><pre class="as-json">{JSON.stringify(report(),null,2)}</pre></details></div>;}
export function registerBuiltins(registry: ModuleRegistry, sourceHash: string) {
  for (const [type, label, component] of [['local-file','工作文件',FileRenderer],['taskbook','任务书',BookRenderer],['harness','任务链',HarnessRenderer],['report','报告',ReportRenderer],['proposal','任务计划',ProposalRenderer],['providers','模型连接',ProviderRenderer],['discovery','需求工作',DiscoveryRenderer],['competition','竞品资料',CompetitionRenderer],['experience','写法经验',ExperienceRenderer],['capabilities','需求工具',PlanningCapabilitiesSummary],['planning-delivery','阶段与交付',PlanningDeliveryRenderer]] as const) registry.registerRenderer({ descriptor: { moduleId: `eeas.${type}`, version: '1', sourceHash, type, schemaVersion: 1, get label(){return uiText(label);}, availability: 'IMPLEMENTED',group:type==='local-file'?'FILES':type==='providers'?'MODEL':'TOOLS',interactionModes:['local-file','report'].includes(type)?['NONE']:['providers','competition','experience','capabilities'].includes(type)?['MODELESS_POPOVER']:['NONE','FULL_WORKSPACE'] }, component });
}
