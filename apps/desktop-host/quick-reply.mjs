import {summaryHTML} from '../agent-shell/src/summary-content.ts';
export {replyLinkTarget} from './quick-reply-links.mjs';

const rendered=new WeakMap();

// Presentation only: the durable reply and its original-operation scope stay intact.
export function renderQuickReply(element,{text='',identity='',markdown=true,locale='zh-CN'}={}){
  if(!element)return;
  const previous=rendered.get(element);
  element.hidden=!text;
  if(previous?.text===text&&previous.markdown===markdown){
    for(const region of element.querySelectorAll('.reply-table'))region.setAttribute('aria-label',locale==='en'?'Table':'表格');
    for(const pre of element.querySelectorAll('pre'))pre.setAttribute('aria-label',locale==='en'?'Code':'代码');
    rendered.set(element,{text,identity,markdown});return;
  }
  const scroll=previous?.identity===identity?element.scrollTop:0;
  if(markdown&&text){
    const template=element.ownerDocument.createElement('template');
    // summaryHTML is the existing Main renderer's Marked + DOMPurify boundary.
    template.innerHTML=summaryHTML(text);
    for(const table of template.content.querySelectorAll('table')){
      const region=element.ownerDocument.createElement('div');
      region.className='reply-table';region.tabIndex=0;
      region.setAttribute('role','region');region.setAttribute('aria-label',locale==='en'?'Table':'表格');
      table.replaceWith(region);region.append(table);
    }
    for(const pre of template.content.querySelectorAll('pre')){
      pre.tabIndex=0;pre.setAttribute('aria-label',locale==='en'?'Code':'代码');
    }
    element.replaceChildren(template.content);
  }else element.textContent=text;
  element.dataset.format=markdown?'markdown':'text';
  element.scrollTop=scroll;rendered.set(element,{text,identity,markdown});
}

