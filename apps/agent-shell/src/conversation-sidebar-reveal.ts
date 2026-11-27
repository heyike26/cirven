export type SidebarSelectionTicket={id:string;projectKey:string;generation:number};

/** One explicit selection owns one reveal. Refreshes never create tickets. */
export function createSidebarSelectionReveal(options:{
  activeId:()=>string|null|undefined;
  reveal:(ticket:SidebarSelectionTicket)=>void;
  schedule?:(callback:()=>void)=>number;
  cancelFrame?:(handle:number)=>void;
}){
  let generation=0,frame:number|undefined,disposed=false;
  const cancel=(ticket?:SidebarSelectionTicket)=>{
    if(ticket&&ticket.generation!==generation)return;
    generation++;
    const pending=frame;frame=undefined;
    if(pending!==undefined){try{if(options.cancelFrame)options.cancelFrame(pending);else cancelAnimationFrame(pending);}catch{/* Auxiliary cancellation must not block selection or disposal. */}}
  };
  return {
    begin(id:string,projectKey:string){
      cancel();
      return disposed||options.activeId()===id?undefined:{id,projectKey,generation};
    },
    complete(ticket:SidebarSelectionTicket|undefined){
      if(!ticket||disposed||ticket.generation!==generation||options.activeId()!==ticket.id)return;
      const afterLayout=()=>{
        frame=undefined;
        if(disposed||ticket.generation!==generation||options.activeId()!==ticket.id)return;
        generation++; // Consume before touching layout; no background follow loop.
        try{options.reveal(ticket);}catch{/* Selection already succeeded; a failed reveal is not an open failure. */}
      };
      try{frame=options.schedule?options.schedule(afterLayout):requestAnimationFrame(afterLayout);}catch{cancel(ticket);}
    },
    cancel,
    dispose(){disposed=true;cancel();}
  };
}

/** Scroll only this sidebar list; never focus or scroll an outer reading pane. */
export function revealSidebarSelection(list:HTMLElement|undefined,id:string,projectKey:string){
  if(!list?.isConnected||!list.getClientRects().length)return;
  const matches=Array.from(list.querySelectorAll<HTMLElement>('[data-conversation-id]'))
    .filter(row=>row.dataset.conversationId===id&&row.closest<HTMLElement>('[data-project-key]')?.dataset.projectKey===projectKey
      &&!row.closest('[hidden],[inert]')&&row.getClientRects().length>0);
  if(matches.length!==1)return;
  const row=matches[0],box=list.getBoundingClientRect();
  let top=box.top+list.clientTop,bottom=top+list.clientHeight;
  for(let parent=list.parentElement;parent;parent=parent.parentElement){
    if(/auto|scroll|hidden|clip/.test(getComputedStyle(parent).overflowY)){
      const rect=parent.getBoundingClientRect();
      top=Math.max(top,rect.top+parent.clientTop);bottom=Math.min(bottom,rect.top+parent.clientTop+parent.clientHeight);
    }
  }
  if(bottom<=top)return;
  const margin=Math.min(8,(bottom-top)/4),rect=row.getBoundingClientRect();
  if(rect.height<=0)return;
  const delta=rect.top<top+margin?rect.top-top-margin:rect.bottom>bottom-margin?rect.bottom-bottom+margin:0;
  if(delta)list.scrollTop+=delta;
}
