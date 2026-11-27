import {createEffect,createMemo,on} from 'solid-js';
import {t as uiText} from './ui-language';
import {summaryHTML} from './summary-content';
import './assistant-reply.css';

/** Assistant prose only. The caller keeps user text, errors and receipts separate. */
export default function AssistantReply(props:{text:string;onOpenLink?:(href:string)=>void|Promise<void>}){
 let body!:HTMLDivElement;
 // Equal text produces no DOM write when only a surrounding receipt changes.
 const text=createMemo(()=>props.text),html=createMemo(()=>summaryHTML(text()));
 createEffect(on(html,()=>{
  for(const table of body.querySelectorAll('table')){
   const region=document.createElement('div');
   region.className='as-assistant-reply-table';
   region.tabIndex=0;
   region.setAttribute('role','region');
   table.replaceWith(region);
   region.append(table);
  }
  for(const pre of body.querySelectorAll('pre')){
   pre.tabIndex=0;
   pre.setAttribute('role','region');
  }
 }));
 createEffect(()=>{
  html();
  const tableLabel=uiText('表格内容'),codeLabel=uiText('代码内容');
  for(const region of body.querySelectorAll('.as-assistant-reply-table'))region.setAttribute('aria-label',tableLabel);
  for(const pre of body.querySelectorAll('pre'))pre.setAttribute('aria-label',codeLabel);
 });
 function openLink(event:MouseEvent){
  const anchor=event.target instanceof Element?event.target.closest('a'):null;
  if(!anchor||!body.contains(anchor))return;
  event.preventDefault();
  const href=anchor.getAttribute('href'),open=props.onOpenLink;
  if(!event.isTrusted||event.button!==0||!open||!href||!/^(?:https?:|mailto:)/i.test(href))return;
  // The host owns failure reporting; presentation never retries an open.
  try{void open(href)?.catch(()=>{});}catch{/* The host rejected the explicit open. */}
 }
 // summaryHTML is the existing Marked + DOMPurify allow-list boundary.
 return <div class="as-assistant-reply" ref={body} innerHTML={html()} onClick={openLink} onAuxClick={openLink}/>;
}
