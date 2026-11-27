import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText} from './ui-language';
import {createSignal,For,Show} from 'solid-js';
import {Button} from '@opencode-ai/ui/button';
import {draftSignal} from './ObjectDraft';
import {textDraftKey,type TextFeedbackDraft} from './annotation-drafts';
import type {TextAnnotation,TextSelection} from './text-feedback';
export default function TextFeedbackEditor(props:{selection:TextSelection;onClose:()=>void;onSubmit:(input:TextAnnotation)=>Promise<unknown>}){
 const [drafts,setDrafts]=draftSignal<Record<string,TextFeedbackDraft>>('textFeedback',{});const key=textDraftKey(props.selection);const comment=()=>drafts()[key]?.comment??'';const setComment=(value:string)=>setDrafts(current=>({...current,[key]:{comment:value,selection:props.selection}}));const [pending,setPending]=createSignal(false);const [error,setError]=createSignal<UIErrorCopy>('');
 async function submit(delivery:'DRAFT'|'SEND'){setPending(true);setError('');try{const sent=comment();await props.onSubmit({tool:'TEXT_SELECTION',comment:sent.trim(),selection:{text_selection:props.selection},delivery});if(comment()===sent){setDrafts(current=>{const next={...current};delete next[key];return next;});props.onClose();}}catch(e){setError(authoredErrorCopy(e));}finally{setPending(false);}}
 return <section class="as-text-feedback"><div class="as-section-heading"><strong>{uiText("对选中原文批注")}</strong><button disabled={pending()} onClick={props.onClose}>{uiText("收起批注")}</button></div><blockquote>{props.selection.text}</blockquote><label>{uiText("修改意见")}<textarea aria-label={uiText("选文修改意见")} value={comment()} onInput={event=>setComment(event.currentTarget.value)}/></label><div class="as-feedback-delivery"><Button size="small" variant="secondary" disabled={pending()||!comment().trim()} onClick={()=>void submit('DRAFT')}>{uiText("加入聊天草稿")}</Button><Button size="small" variant="primary" disabled={pending()||!comment().trim()} onClick={()=>void submit('SEND')}>{uiText("直接发送给 Agent")}</Button></div><Show when={error()}><p role="alert" class="as-inline-error">{authoredText(error(),uiText)}</p></Show></section>;
}
export function SavedTextFeedbacks(props:{onSubmit:(input:TextAnnotation)=>Promise<unknown>}){
 const [drafts]=draftSignal<Record<string,TextFeedbackDraft>>('textFeedback',{}),[selected,setSelected]=createSignal('');const keys=()=>Object.keys(drafts()).filter(key=>drafts()[key].comment.trim());
 return <Show when={keys().length}><section class="as-saved-text-feedback"><details><summary>{uiText("未发送的选文批注 · ")}{keys().length}</summary><For each={keys()}>{key=><button onClick={()=>setSelected(key)}>{drafts()[key].selection.text.slice(0,70)}{uiText(" · 继续批注")}</button>}</For></details><Show when={selected()} keyed>{key=>{const selection=drafts()[key]?.selection;return <Show when={selection}><TextFeedbackEditor selection={selection!} onClose={()=>setSelected('')} onSubmit={props.onSubmit}/></Show>;}}</Show></section></Show>;
}
