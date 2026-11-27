import {t as uiText} from './ui-language';
import {createEffect,createMemo,For,onCleanup,Show,type JSX} from 'solid-js';
import {Dynamic} from 'solid-js/web';
import {Icon} from '@opencode-ai/ui/icon';
import type {RendererContext} from './contracts';
import {ObjectDraftBoundary,useDraftSession} from './ObjectDraft';
import {GenericRenderer} from './renderers';
import {panelKey,type InteractionState} from './interaction-state';
import type {ModuleRegistry} from './registry';
import ArtifactActionContext from './ArtifactActionContext';
import './interaction-recovery.css';
function InteractionContent(props:{visible:boolean;children:JSX.Element}){
 const owner=useDraftSession();let node:HTMLDivElement|undefined,restored=false,frame:number|undefined;
 createEffect(()=>{owner?.version();if(restored||!node||!props.visible||owner?.session?.status==='loading')return;restored=true;const view=owner?.session?.view.scroll as {x:number;y:number}|undefined;if(view)frame=requestAnimationFrame(()=>{if(node){node.scrollTop=view.y;node.scrollLeft=view.x;}});});
 onCleanup(()=>{if(frame!==undefined)cancelAnimationFrame(frame);});
 return <div ref={node} class="as-interaction-content as-inspector" onScroll={event=>{if(props.visible)owner?.session?.set('view','scroll',{x:event.currentTarget.scrollLeft,y:event.currentTarget.scrollTop});}}>{props.children}</div>;
}
/** Restore the existing current-scope pane without a new request or renderer. */
export function InteractionRestoreMenuItems(props:{state:InteractionState;version:number;registry:ModuleRegistry;onRestore?:(key:string)=>void}){
 const keys=createMemo(()=>{props.version;return props.state.current().filter(item=>!item.visible).map(item=>item.key);});
 return <For each={keys()}>{key=>{
  const panel=()=>{props.version;return props.state.current().find(item=>item.key===key);};
  return <button type="button" class="as-interaction-restore-menu-item" data-interaction-restore-key={key} onClick={()=>{if(!props.state.current().some(item=>item.key===key&&!item.visible))return;if(props.state.expand(key)===key)props.onRestore?.(key);}}><Icon name="arrow-up"/><span>{panel()?props.registry.renderer(panel()!.artifact)?.descriptor.label??uiText("交互产物"):uiText("交互产物")}</span></button>;
 }}</For>;
}
export default function InteractionPanels(props:{state:InteractionState;version:number;registry:ModuleRegistry;context:RendererContext;fullReady?:boolean;showRestores?:boolean;onCollapse?:(key:string)=>void}){
 const panels=()=>{props.version;return props.state.current();};const keys=createMemo(()=>panels().map(item=>item.key));
 const collapse=(key:string)=>{props.state.collapse(key);props.onCollapse?.(key);};
 return <div class="as-interaction-layer"><For each={keys()}>{key=>{const panel=()=>{props.version;return props.state.records.get(key)!;};const renderer=()=>props.registry.renderer(panel().artifact)?.component??GenericRenderer;return <section class="as-interaction-panel" data-key={key} data-interaction-key={key} data-visible={String(panel().visible)} data-shape-ready={String(panel().request.mode!=='FULL_WORKSPACE'||props.fullReady!==false)} data-mode={panel().request.mode} aria-hidden={!panel().visible||(panel().request.mode==='FULL_WORKSPACE'&&props.fullReady===false)} inert={!panel().visible||(panel().request.mode==='FULL_WORKSPACE'&&props.fullReady===false)} role="region" aria-label={props.registry.renderer(panel().artifact)?.descriptor.label??uiText("交互产物")} style={{height:panel().request.mode==='MODELESS_POPOVER'?`min(${panel().height}px, calc(100% - 32px))`:undefined,left:panel().request.mode==='MODELESS_POPOVER'?panel().position.x+'px':undefined,top:panel().request.mode==='MODELESS_POPOVER'?panel().position.y+'px':undefined}} onPointerDown={event=>{if(panel().request.mode==='FULL_WORKSPACE')event.stopPropagation();}} onKeyDown={event=>{if(event.key==='Escape'&&!event.isComposing){event.preventDefault();event.stopPropagation();collapse(key);}}}><header class="as-interaction-header"><strong>{props.registry.renderer(panel().artifact)?.descriptor.label??uiText("交互产物")}</strong><button type="button" data-interaction-collapse aria-label={uiText("收起交互面板")} onClick={()=>collapse(key)}><Icon name="collapse"/></button></header><ObjectDraftBoundary artifact={panel().artifact}><InteractionContent visible={panel().visible}><ArtifactActionContext context={props.context} owned={target=>props.state.current().some(item=>item.key===key&&item.visible)&&panelKey(target)===key} observe={(target,result)=>props.state.observe(target,result)}>{context=><Dynamic component={renderer()} artifact={panel().artifact} context={context}/>}</ArtifactActionContext></InteractionContent></ObjectDraftBoundary><Show when={panel().request.mode==='MODELESS_POPOVER'}><input class="as-interaction-height" type="range" aria-label={uiText("交互面板高度")} min="220" max="1000" value={panel().height} onInput={event=>props.state.resize(key,Number(event.currentTarget.value))}/></Show></section>;}}</For><Show when={props.showRestores!==false&&panels().some(item=>!item.visible)}><nav class="as-interaction-restores" aria-label={uiText("已收起的交互面板")}><For each={panels().filter(item=>!item.visible)}>{item=><button onClick={()=>props.state.expand(item.key)}>{props.registry.renderer(item.artifact)?.descriptor.label??uiText("交互产物")} · v{item.artifact.revision}<Icon name="arrow-up"/></button>}</For></nav></Show></div>;
}
