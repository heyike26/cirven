import {createEffect,createMemo,createSignal,For,Show,onMount,onCleanup,type JSX} from 'solid-js';
import StudioPopover from './StudioPopover';
import {t as uiText} from './ui-language';
import './native-composer.css';
export type ComposerAttachment={id:string;label:string;title?:string;description?:string;meta?:string;warning?:string;image?:boolean;previewUrl?:string;previewStatus?:'LOADING'|'READY'|'ERROR';onLoadPreview?:()=>void;onPreview?:()=>void;onRemove?:()=>void};
export type NativePageConversationProps={context?:JSX.Element;mode?:JSX.Element;attachments?:readonly ComposerAttachment[];input:JSX.Element;toolbar:JSX.Element;folded?:boolean;fieldId?:string;scopeKey?:string;busy?:boolean};
export function ReferenceIcon(props:{kind?:'file'|'reference'}){
 return <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><Show when={props.kind==='file'} fallback={<path d="m7 12 6-6a2.1 2.1 0 0 1 3 3l-7.6 7.6a3.3 3.3 0 0 1-4.7-4.7l8.1-8.1"/>}><path d="M11.5 2.5H5.3A1.3 1.3 0 0 0 4 3.8v12.4a1.3 1.3 0 0 0 1.3 1.3h9.4a1.3 1.3 0 0 0 1.3-1.3V7m-4.5-4.5V7H16l-4.5-4.5ZM7 11h6m-6 3h4"/></Show></svg>;
}
/** Presentation only. Parent keeps the real textarea, draft, form and transport. */
export default function NativePageConversation(props:NativePageConversationProps){
 const [referencesOpen,setReferencesOpen]=createSignal(false);
 const [visibleLimit,setVisibleLimit]=createSignal(2);
 const attachments=createMemo(()=>props.attachments??[]),warnings=createMemo(()=>attachments().filter(item=>!!item.warning));
 const byId=createMemo(()=>new Map(attachments().map(item=>[item.id,item]))),ids=createMemo(()=>attachments().map(item=>item.id)),visibleIds=createMemo(()=>ids().slice(0,visibleLimit())),extraIds=createMemo(()=>ids().slice(visibleLimit()));
 let root:HTMLDivElement|undefined;
 onMount(()=>{if(!root||typeof root.getBoundingClientRect!=='function')return;const measure=()=>{const width=root!.getBoundingClientRect().width;setVisibleLimit(width>=540?3:width>=360?2:1);};measure();if(typeof ResizeObserver==='function'){const observer=new ResizeObserver(measure);observer.observe(root);onCleanup(()=>observer.disconnect());}});
 createEffect(()=>{const shown=referencesOpen()?ids():visibleIds();for(const id of shown)byId().get(id)?.onLoadPreview?.();});
 let previousScope=props.scopeKey;
 createEffect(()=>{const current=props.scopeKey;if(current!==previousScope){previousScope=current;setReferencesOpen(false);}});
 createEffect(()=>{if(props.folded||!attachments().length)setReferencesOpen(false);});
 const card=(id:string)=>{const item=()=>byId().get(id)!,[info,setInfo]=createSignal(false);const contents=()=> <><Show when={item().image} fallback={<ReferenceIcon kind="file"/>}><Show when={item().previewUrl} fallback={<span class="as-composer-image-placeholder"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="2"/><path d="m3 17 6-6 4 4 3-3 5 5"/></svg></span>}>{url=><img src={url()} alt="" draggable={false}/>}</Show></Show><span class="as-composer-attachment-copy"><strong>{item().label}</strong><Show when={item().meta}><small>{item().meta}</small></Show><Show when={item().previewStatus==='ERROR'}><small>{uiText('预览暂不可用')}</small></Show></span></>;const main=()=> <Show when={item().onPreview||item().description} fallback={<div class="as-composer-attachment-main" title={item().title??item().label}>{contents()}</div>}><button type="button" class="as-composer-attachment-main" title={item().title??item().label} aria-label={uiText('查看引用 {name}',{name:item().label})} onClick={()=>item().onPreview?item().onPreview!():setInfo(value=>!value)}>{contents()}</button></Show>;return <article class="as-composer-attachment" data-input-id={id} data-image={!!item().image}><Show when={item().description&&!item().onPreview} fallback={main()}><StudioPopover open={info()} onOpenChange={setInfo} label={uiText('查看引用 {name}',{name:item().label})} placement="top-start" trigger={main()}><section class="as-composer-attachment-note"><strong>{item().label}</strong><p>{item().description}</p></section></StudioPopover></Show><Show when={item().onRemove}><button type="button" class="as-composer-attachment-remove" aria-label={uiText('移除引用 {name}',{name:item().label})} disabled={props.busy} onClick={()=>{item().onRemove?.();if(typeof root?.querySelector==='function')root.querySelector<HTMLTextAreaElement>('textarea')?.focus({preventScroll:true});}}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8m0-8-8 8"/></svg></button></Show></article>;};
 return <div class="as-native-composer-shell" ref={root}>
  <div id={props.fieldId??'as-composer-fields'} class="as-composer-fields as-native-composer-fields" hidden={props.folded}>
   <div class="as-native-composer-meta">{props.mode}<div class="as-native-composer-references">{props.context}</div></div>
   <Show when={attachments().length}><div class="as-composer-attachments" aria-label={uiText('本轮引用')}><For each={visibleIds()}>{card}</For><Show when={extraIds().length}>
    <StudioPopover open={referencesOpen()} onOpenChange={setReferencesOpen} label={uiText("本轮引用")} placement="top-start" class="as-native-reference-popover" trigger={<button type="button" class="as-composer-attachment-more" data-attention={warnings().length>0} aria-expanded={referencesOpen()} aria-label={uiText('查看其余 {count} 个引用',{count:extraIds().length})} onClick={()=>setReferencesOpen(value=>!value)}>+{extraIds().length}</button>}>
     <header class="as-native-reference-heading"><strong>{uiText("本轮引用")}</strong><span>{attachments().length}</span></header>
     <div class="as-native-reference-list"><For each={extraIds()}>{card}</For></div>
    </StudioPopover>
   </Show></div></Show>
   <Show when={warnings().length}><p class="as-native-composer-warning" role="status">{warnings()[0].warning}<Show when={warnings().length>1}><span> · {uiText("{count} 项引用需核对",{count:warnings().length})}</span></Show></p></Show>
   <div class="as-native-composer-input">{props.input}</div>
  </div><div class="as-native-composer-tools">{props.toolbar}</div>
 </div>;
}
