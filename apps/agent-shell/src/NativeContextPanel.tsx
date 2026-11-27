import {t as uiText} from './ui-language';
import { createEffect, createSignal, For, onCleanup, onMount, Show } from 'solid-js';
import StudioPopover from './StudioPopover';
import './native-context-entry.css';
import { Icon } from '@opencode-ai/ui/icon';
import { nativeCollaborationBridge, currentNativeReference, sameNativeReference, nativeContextLabel, nativePreview, nativeRegion, nativeObservationScope, type NativeCollaborationState, type NativeContextSnapshot, type NativeRegion } from './native-collaboration';

export default function NativeContextPanel(props: {
  snapshot?: NativeContextSnapshot; state?: NativeCollaborationState; available: boolean; pending: boolean; reason?: string;
  onCapture: (visual: boolean) => void;
  onFeedback: (comment: string, regions: NativeRegion[], delivery: 'DRAFT' | 'SEND', basis?: NativeContextSnapshot) => Promise<void>;
}) {
  const [expanded, setExpanded] = createSignal(false), [comment, setComment] = createSignal(''), [regions, setRegions] = createSignal<NativeRegion[]>([]), [marking,setMarking] = createSignal(false), [error,setError] = createSignal('');
  const [basis,setBasis] = createSignal<NativeContextSnapshot>(), [feedbackPending,setFeedbackPending]=createSignal(false);
  let feedbackEdit=0;const pending=()=>props.pending||feedbackPending();
  let panel:HTMLElement|undefined, toggle:HTMLButtonElement|undefined, preview!: HTMLDivElement, commentInput:HTMLTextAreaElement|undefined, annotationIntent=false, start: { x: number; y: number } | undefined, pointerId: number | undefined;
  const [drag,setDrag] = createSignal<NativeRegion>();
  createEffect(() => { props.snapshot?.observation?.context_ref?.sha256; if(!basis()) { setRegions([]); setDrag(undefined); setMarking(false); start = undefined; } });
  const ref = () => currentNativeReference(props.snapshot), feedbackRef = () => currentNativeReference(basis()??props.snapshot), image = () => nativePreview(basis()??props.snapshot);
  const changed = () => !!basis() && (!!feedbackRef() || !!ref()) && !sameNativeReference(feedbackRef(),ref());
  function revealAnnotation(){const alreadyOpen=expanded();annotationIntent=true;setExpanded(true);if(!basis())setBasis(props.snapshot);if(image())setMarking(true);if(alreadyOpen)commentInput?.focus({preventScroll:true});}
  onMount(()=>{const bridge=nativeCollaborationBridge(globalThis);const release=bridge?.onAnnotationRequested?.(revealAnnotation);onCleanup(()=>release?.());});


  async function feedback(delivery: 'DRAFT' | 'SEND') {
    const text = comment(); if (!text.trim() || !feedbackRef() || pending() || delivery==='SEND'&&changed()) return;
    const edit=feedbackEdit;setFeedbackPending(true);setError(''); try { await props.onFeedback(text, regions(), delivery, basis()??props.snapshot); if (feedbackEdit===edit) { setComment('');setBasis(undefined);setRegions([]); } } catch(error) { setError(error instanceof Error ? error.message : String(error)); } finally { setFeedbackPending(false); }
  }
  function finish(event: PointerEvent, commit: boolean) {
    if (pointerId !== event.pointerId) return;
    const region = start && nativeRegion(start,{x:event.clientX,y:event.clientY},preview.getBoundingClientRect());
    if (commit && region) {feedbackEdit++;setRegions(value => [...value,region]);}
    if (preview.hasPointerCapture(event.pointerId)) preview.releasePointerCapture(event.pointerId);
    start=undefined;pointerId=undefined;setDrag(undefined);
  }
  return <div class="as-native-context-entry"><StudioPopover open={expanded()} onOpenChange={value=>{setExpanded(value);if(!value)annotationIntent=false;}} initialFocus={()=>annotationIntent?commentInput:undefined} placement="top-start" label={uiText("原生当前对象")} class="as-current-page-popover" trigger={
    <button ref={toggle} type="button" class="as-current-page-trigger" aria-label={uiText("展开当前对象与批注")} title={ref()?nativeContextLabel(props.snapshot,uiText):uiText("当前页面")} aria-expanded={expanded()} data-risk={String(changed()||!!error())} onClick={()=>{annotationIntent=false;setExpanded(value=>!value);}}><Icon name="open-file"/><span>{ref()?nativeContextLabel(props.snapshot,uiText):uiText("当前页面")}</span><Show when={changed()||error()}><span class="as-current-page-risk" aria-label={uiText("需要核对")}>!</span></Show></button>
  }><section ref={panel} class="as-native-context" aria-label={uiText("原生当前对象")} data-current={String(!!ref())} data-expanded="true"><header class="as-current-page-heading"><strong>{nativeContextLabel(props.snapshot,uiText)}</strong><button type="button" aria-label={uiText("关闭")} onClick={()=>setExpanded(false)}><Icon name="close"/></button></header><div class="as-native-context-expanded">
      <Show when={!props.available&&props.reason}><p class="as-native-coverage as-native-context-reason" role="status">{uiText(props.reason!)}</p></Show>
      <p class="as-native-coverage">{nativeObservationScope(props.state,props.snapshot,uiText)}</p>
      <div class="as-native-context-actions"><button type="button" disabled={!props.available||pending()} onClick={()=>props.onCapture(false)}><Icon name="arrow-right"/>{uiText("引用当前对象")}</button><button type="button" disabled={!props.available||pending()} onClick={()=>props.onCapture(true)}><Icon name="photo"/>{uiText("取得当前画面")}</button></div>
      <Show when={props.snapshot?.observation?.coverage}><p class="as-native-coverage">{props.snapshot?.observation?.coverage?.metadata?uiText("窗口信息已取得"):uiText("窗口信息未取得")} · {props.snapshot?.observation?.coverage?.accessibility?uiText("可访问对象已取得"):uiText("对象内容未取得")} · {image()?uiText("实际画面已取得"):uiText("没有可用画面")}</p></Show>
      <Show when={image()} keyed>{url=><><div ref={preview} class="as-native-frame" data-marking={String(marking())} onPointerDown={event=>{if(!marking()||event.button!==0)return;event.preventDefault();start={x:event.clientX,y:event.clientY};pointerId=event.pointerId;preview.setPointerCapture(event.pointerId);}} onPointerMove={event=>{if(start&&pointerId===event.pointerId)setDrag(nativeRegion(start,{x:event.clientX,y:event.clientY},preview.getBoundingClientRect()));}} onPointerUp={event=>finish(event,true)} onPointerCancel={event=>finish(event,false)}><img src={url} alt={uiText("反馈依据的已冻结原生窗口画面")} draggable={false}/><For each={[...regions(),...(drag()?[drag()!]:[])]}>{area=><span class="as-native-mark" style={{left:`${area.x*100}%`,top:`${area.y*100}%`,width:`${area.width*100}%`,height:`${area.height*100}%`}}/>}</For></div><div class="as-native-frame-tools"><button type="button" aria-pressed={marking()} onClick={()=>{if(!basis())setBasis(props.snapshot);setMarking(value=>!value);}}><Icon name="pencil-line"/>{marking()?uiText("结束框选"):uiText("框选反馈区域")}</button><Show when={regions().length}><button type="button" onClick={()=>{feedbackEdit++;setRegions([]);}}>{uiText("清除框选 · ")}{regions().length}</button></Show></div></>}</Show>
      <Show when={props.snapshot||props.state?.context_status}><details><summary>{uiText("对象、来源与观测范围")}</summary><pre class="as-json">{JSON.stringify({status:props.snapshot?.observation.status,reason:props.snapshot?.observation.reason,context_ref:props.snapshot?.observation.context_ref,window_ref:props.snapshot?.observation.window_ref,observations:props.snapshot?.observation.observations,coverage:props.snapshot?.observation.coverage,questions:props.snapshot?.observation.questions,context_status:props.state?.context_status,context_capabilities:props.state?.context_capabilities,native_input:false},null,2)}</pre></details></Show>
      <Show when={changed()}><p class="as-inline-warning"><Show when={feedbackRef()} fallback={uiText("批注尚未关联当前页面。")}>{saved=><>{uiText("批注仍依据「")}{nativeContextLabel(basis(),uiText)}」v{saved().revision}{uiText("。当前对象已变化，可先保存旧批注，或明确重新定位。")}</>}</Show></p><button type="button" class="as-native-rebind" disabled={!ref()||pending()} onClick={()=>{if(!ref()||pending())return;feedbackEdit++;setBasis(props.snapshot);setRegions([]);setMarking(false);}}>{uiText("将批注重新定位到当前对象")}</button></Show>
      <label class="as-native-comment-label">{uiText("对当前对象的反馈")}<textarea ref={commentInput} aria-label={uiText("当前对象批注")} value={comment()} onInput={event=>{feedbackEdit++;if(!basis())setBasis(props.snapshot);setComment(event.currentTarget.value);}} placeholder={uiText("添加批注…")}/></label>
      <div class="as-native-feedback-actions"><button type="button" disabled={!feedbackRef()||!comment().trim()||pending()} onClick={()=>void feedback('DRAFT')}>{uiText("加入草稿")}</button><button type="button" disabled={!feedbackRef()||changed()||!comment().trim()||pending()} onClick={()=>void feedback('SEND')}>{uiText("直接发送反馈")}<Icon name="arrow-up"/></button></div>
    </div><Show when={error()}><p role="alert" class="as-inline-error">{uiText(error())}</p></Show>
  </section></StudioPopover></div>;
}
