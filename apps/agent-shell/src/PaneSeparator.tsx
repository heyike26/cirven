import {t as uiText} from './ui-language';
import {createSignal,onCleanup} from 'solid-js';
import {PANE_MIN,paneWidth} from './panes';
export default function PaneSeparator(props:{kind:'files'|'agent';value:number;onChange:(value:number)=>void;hidden?:boolean}){
 const [dragging,setDragging]=createSignal(false);let release:(()=>void)|undefined;
 function start(event:PointerEvent){if(event.button!==0)return;event.preventDefault();release?.();const x=event.clientX,width=props.value;setDragging(true);const move=(move:PointerEvent)=>props.onChange(paneWidth(width+(move.clientX-x)*(props.kind==='files'?1:-1),props.kind,window.innerWidth));const finish=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',finish);window.removeEventListener('pointercancel',finish);setDragging(false);release=undefined;};release=finish;window.addEventListener('pointermove',move);window.addEventListener('pointerup',finish);window.addEventListener('pointercancel',finish);}
 onCleanup(()=>release?.());
 return <div class="as-pane-separator" data-kind={props.kind} data-dragging={String(dragging())} role="separator" aria-label={props.kind==='files'?uiText("调整文件面板宽度"):uiText("调整 Agent 面板宽度")} aria-orientation="vertical" aria-valuemin={PANE_MIN[props.kind]} aria-valuemax={paneWidth(10000,props.kind,window.innerWidth)} aria-valuenow={Math.round(props.value)} tabIndex={props.hidden?-1:0} onPointerDown={start} onKeyDown={event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();props.onChange(paneWidth(props.value+(event.key==='ArrowRight'?16:-16)*(props.kind==='files'?1:-1),props.kind,window.innerWidth));}}}/>;
}
