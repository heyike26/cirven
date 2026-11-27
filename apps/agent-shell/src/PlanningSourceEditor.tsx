import {createEffect,createSignal,Index,onCleanup,Show} from 'solid-js';
import {PlanningSourceError,planningSources,planningSourceBasis,samePlanningAnchor,selectedPlanningAnchor} from './planning-source';
import SourceNotes from './SourceNotes';
import {t} from './ui-language';
import {savedTextareaRange} from './saved-textarea-range';

export default function PlanningSourceEditor(props:{anchors:unknown[];receipt:unknown;disabled?:boolean;onChange:(value:unknown[])=>void}) {
  const [open,setOpen]=createSignal(false),[documentIndex,setDocumentIndex]=createSignal(''),[chunkIndex,setChunkIndex]=createSignal(0);
  const [selection,setSelection]=createSignal<{start:number;end:number;buffer:string}>(),[pending,setPending]=createSignal(false),[error,setError]=createSignal('');
  let alive=true;onCleanup(()=>{alive=false;});
  const documents=()=>planningSources(props.receipt),source=()=>documents()[Number(documentIndex())];
  const chosen=()=>documentIndex()!==''?source():undefined,chunk=()=>chosen()?.chunks[chunkIndex()];
  const basis=()=>planningSourceBasis(chosen(),chunk(),props.anchors);
  let previousBasis=basis();
  createEffect(()=>{const next=basis();if(next!==previousBasis){previousBasis=next;setSelection(undefined);setError('');}});
  async function add() {
    const document=chosen(),part=chunk(),range=selection(),receipt=props.receipt,anchors=props.anchors,originalBasis=basis();
    if(pending()||props.disabled||!document||!part||!range)return;
    setPending(true);setError('');
    try {
      const saved=savedTextareaRange(part.content,range.buffer,range.start,range.end);
      const anchor=await selectedPlanningAnchor(receipt,document,part,saved.start,saved.end);
      if(!alive)return;
      if(props.disabled||basis()!==originalBasis||selection()?.start!==range.start||selection()?.end!==range.end){setError('来源或选段已变化，请重新核对后引用。');return;}
      if(anchors.some(value=>samePlanningAnchor(value,anchor))){setError('这段原文已在来源中。');return;}
      if(anchors.length>=32){setError('一个任务最多保留 32 处来源，请先核对已有引用。');return;}
      props.onChange([...anchors,anchor]);
      if(alive){setOpen(false);setSelection(undefined);}
    } catch(error) {if(alive)setError(error instanceof PlanningSourceError?error.message:'来源摘录无法核对，未添加引用。');}
    finally {if(alive)setPending(false);}
  }
  return <section class="as-planning-source-editor" aria-label={t('任务来源')}>
    <SourceNotes anchors={props.anchors} onRemove={props.disabled||pending()?undefined:index=>props.onChange(props.anchors.filter((_,i)=>i!==index))}/>
    <button type="button" class="as-text-action" disabled={props.disabled||pending()} aria-expanded={open()} onClick={()=>setOpen(value=>!value)}>{t(open()?'收起资料引用':'引用资料中的原文')}</button>
    <Show when={open()}><div class="as-planning-source-picker">
      <Show when={documents().length} fallback={<p class="as-muted">{t('当前来源快照没有可引用的原文，已有引用保留。')}</p>}>
        <label>{t('引用资料')}<select aria-label={t('引用资料')} value={documentIndex()} disabled={pending()||props.disabled} onChange={event=>{setDocumentIndex(event.currentTarget.value);setChunkIndex(0);}}><option value="">{t('选择资料')}</option><Index each={documents()}>{(document,index)=><option value={index}>{document().filename} · v{document().revision}</option>}</Index></select></label>
        <Show when={chosen()}><Show when={chosen()!.chunks.length>1}><label>{t('原文片段')}<select aria-label={t('原文片段')} value={chunkIndex()} disabled={pending()||props.disabled} onChange={event=>setChunkIndex(Number(event.currentTarget.value))}><Index each={chosen()!.chunks}>{(part,index)=><option value={index}>{t('片段 {index}',{index:part().index+1})}</option>}</Index></select></label></Show>
          <Show when={chunk()}><p class="as-muted">{t('在原文中选取需要引用的内容。')}</p><textarea aria-label={t('引用原文')} readOnly value={chunk()!.content} onSelect={event=>{const value=event.currentTarget;setSelection(value.selectionEnd>value.selectionStart?{start:value.selectionStart,end:value.selectionEnd,buffer:value.value}:undefined);}}/><button type="button" disabled={pending()||props.disabled||!selection()} onClick={()=>void add()}>{t(pending()?'核对摘录中…':'引用选中原文')}</button></Show>
        </Show>
      </Show>
      <Show when={error()}><p role="alert" class="as-inline-warning">{t(error())}</p></Show>
    </div></Show>
  </section>;
}
