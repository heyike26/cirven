import {t as uiText} from './ui-language';
import { createContext, createMemo, createResource, createSignal, For, Show, useContext } from 'solid-js';
import { verifyQuotedEvidence } from './source-evidence';
import {planningAnchor,planningSourceBasis,planningSourceDocument,planningSourceName} from './planning-source';
import './source-notes.css';

export const SourceEvidenceContext=createContext<{receipt:unknown;sourceCurrent:boolean}>();
function AnchorNote(props:{anchor:unknown;open:boolean}){
  const context=useContext(SourceEvidenceContext);
  const anchor=()=>planningAnchor(props.anchor)?props.anchor:undefined;
  const input=createMemo<false|{stamp:string;anchor:unknown;receipt:unknown}>(()=>{
    if(!props.open||!context||!anchor())return false;
    const source=planningSourceDocument(context.receipt,props.anchor),chunk=source?.chunks.find(value=>value.index===anchor()!.chunk_index&&value.sha256===anchor()!.chunk_sha256);
    return {stamp:planningSourceBasis(source,chunk,[props.anchor]),anchor:props.anchor,receipt:context.receipt};
  },false,{equals:(a,b)=>a===b||!!a&&!!b&&a.stamp===b.stamp});
  const [quote]=createResource(input,async value=>verifyQuotedEvidence(value.receipt,value.anchor));
  return <div class="as-source-note"><Show when={anchor()} fallback={<p class="as-inline-warning">{uiText('这条来源引用不完整，原引用保留。')}</p>}>{value=><>
    <strong>{planningSourceName(context?.receipt,value())??uiText('资料引用')}</strong>
    <p class="as-muted">{uiText('版本 {revision} · 片段 {chunk} · 原文第 {start}–{end} 字',{revision:value().revision,chunk:value().chunk_index+1,start:value().char_start+1,end:value().char_end})}</p>
    <Show when={props.open&&context} fallback={<Show when={props.open}><p class="as-muted">{uiText('来源快照尚未读取，已有引用保留。')}</p></Show>}><Show when={!quote.loading} fallback={<p class="as-muted">{uiText("正在核对摘录…")}</p>}><Show when={!quote.error} fallback={<p class="as-inline-warning">{uiText('这处摘录暂未能核对，原引用保留。')}</p>}><Show when={quote()}>{value=><><blockquote>{value().text}</blockquote><small>{uiText('摘录内容已核对 · ')}{context!.sourceCurrent?uiText("原件当前版本一致"):uiText("原件状态需要复核")}</small></>}</Show></Show></Show></Show>
  </>}</Show></div>;
}

export default function SourceNotes(props:{anchors:unknown[];onRemove?:(index:number)=>void}){
  const [open,setOpen]=createSignal(false);
  return <Show when={props.anchors.length}><details class="as-source-notes" onToggle={event=>setOpen(event.currentTarget.open)}><summary>{uiText('来源引用（{count}）',{count:props.anchors.length})}</summary><For each={props.anchors}>{(anchor,index)=><div><AnchorNote anchor={anchor} open={open()}/><Show when={props.onRemove}><button type="button" class="as-text-action" onClick={()=>props.onRemove?.(index())}>{uiText('移除此来源')}</button></Show></div>}</For></details></Show>;
}
