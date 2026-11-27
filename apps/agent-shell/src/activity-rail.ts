/** Native button Tab behavior remains available. Arrow navigation is scoped to
 * this rail and never sends computer input or leaves the current side pane. */
export function railTarget(key:string,index:number,count:number){if(count<1)return;if(key==='ArrowDown'||key==='ArrowRight')return(index+1)%count;if(key==='ArrowUp'||key==='ArrowLeft')return(index+count-1)%count;if(key==='Home')return 0;if(key==='End')return count-1;}
export function railKey(event:KeyboardEvent,rail:HTMLElement){const buttons=[...rail.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')],index=buttons.findIndex(button=>button===event.target),target=railTarget(event.key,index,buttons.length);if(index<0||target===undefined)return;event.preventDefault();event.stopPropagation();buttons[target]?.focus();}
