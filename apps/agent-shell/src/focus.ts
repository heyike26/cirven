export function trapFocus(dialog:HTMLElement,close:()=>void,options:{restoreTarget?:HTMLElement;restoreIfContained?:boolean}={}) {
  const before=options.restoreTarget??(document.activeElement instanceof HTMLElement?document.activeElement:undefined);
  const elements=()=>[...dialog.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),summary,a[href],[tabindex="0"]')].filter(element=>element.getClientRects().length&&element.checkVisibility?.()!==false);
  const frame=requestAnimationFrame(()=>{(dialog.querySelector<HTMLElement>('[autofocus]')??elements()[0])?.focus();});
  const key=(event:KeyboardEvent)=>{if(event.isComposing)return;if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();return;}if(event.key!=='Tab')return;const list=elements();if(!list.length){event.preventDefault();dialog.focus();return;}const first=list[0],last=list.at(-1)!;if(event.shiftKey&&(document.activeElement===first||!dialog.contains(document.activeElement))){event.preventDefault();last.focus();}else if(!event.shiftKey&&(document.activeElement===last||!dialog.contains(document.activeElement))){event.preventDefault();first.focus();}};
  dialog.addEventListener('keydown',key);
  return ()=>{cancelAnimationFrame(frame);dialog.removeEventListener('keydown',key);const active=document.activeElement;if(options.restoreIfContained&&active!==document.body&&active&&!dialog.contains(active))return;if(before?.isConnected)before.focus({preventScroll:true});};
}
