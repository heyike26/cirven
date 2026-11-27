import {t as uiText} from './ui-language';
import {createEffect,createMemo,createSignal,For,Show,onCleanup,untrack,type Accessor,type Setter} from 'solid-js';
import {Dynamic} from 'solid-js/web';
import type {ArtifactEnvelope,RendererContext,SourceRef,FileContent} from './contracts';
import type {ModuleRegistry} from './registry';
import {observeArtifactResult,panelKey} from './interaction-state';
import {GenericRenderer} from './renderers';
import {ObjectDraftBoundary,useDraftSession} from './ObjectDraft';
import ArtifactActionContext from './ArtifactActionContext';
function DocumentFrame(props:{artifact:ArtifactEnvelope;visible:Accessor<boolean|undefined>;registry:ModuleRegistry;context:RendererContext;onBack:()=>void;onFileRelated?:(source:SourceRef,trigger:HTMLElement)=>void;observe:(target:ArtifactEnvelope,result:unknown)=>void}){
 const owner=useDraftSession();let node:HTMLDivElement|undefined,restored=false,touched=false,frame:number|undefined;
 createEffect(()=>{owner?.version();if(restored||!node||!props.visible()||owner?.session?.status==='loading')return;restored=true;const scroll=owner?.session?.view.scroll as {x:number;y:number}|undefined;if(scroll&&!touched)frame=requestAnimationFrame(()=>{if(node){node.scrollTop=scroll.y;node.scrollLeft=scroll.x;}});});
 onCleanup(()=>{if(frame!==undefined)cancelAnimationFrame(frame);});
 return <div ref={node} class="as-inspector as-cached-document" hidden={!props.visible()} inert={!props.visible()} aria-hidden={!props.visible()} onScroll={event=>{if(!props.visible())return;touched=true;owner?.session?.set('view','scroll',{x:event.currentTarget.scrollLeft,y:event.currentTarget.scrollTop});}}><div class="as-inspector-navigation"><button class="as-return-desktop" onClick={props.onBack}>{uiText("返回项目工作台")}</button><Show when={props.artifact.type==='local-file'&&props.onFileRelated}><button class="as-return-desktop" onClick={event=>{if(props.visible())props.onFileRelated?.((props.artifact.data as FileContent).source_ref,event.currentTarget);}}>{uiText("已保存文件的相关讨论")}</button></Show></div><ArtifactActionContext context={props.context} owned={target=>!!props.visible()&&panelKey(target)===panelKey(props.artifact)} observe={props.observe}>{context=><Dynamic component={props.registry.renderer(props.artifact)?.component??GenericRenderer} artifact={props.artifact} context={context}/>}</ArtifactActionContext></div>;
}
/** Same-project object changes hide exact-version DOM. Cross-project/restart
 * restores its independent persisted fields/view, never a newer object's copy. */
export default function InspectorDocuments(props:{active?:ArtifactEnvelope;projectId?:string;registry:ModuleRegistry;context:RendererContext;onBack:()=>void;onFileRelated?:(source:SourceRef,trigger:HTMLElement)=>void}){
 const records=new Map<string,[Accessor<ArtifactEnvelope>,Setter<ArtifactEnvelope>]>();const [version,setVersion]=createSignal(0);
 const observe=(target:ArtifactEnvelope,result:unknown)=>{if(target.scope.projectId!==props.projectId)return;const entry=records.get(panelKey(target));if(!entry)return;const next=observeArtifactResult(untrack(entry[0]),result);if(next)entry[1](next);};
 createEffect(()=>{const active=props.active;if(!active||active.scope.projectId!==props.projectId)return;const key=panelKey(active);if(!records.has(key)){records.set(key,createSignal(active));setVersion(value=>value+1);}else observe(active,active.data);});
 const keys=createMemo(()=>{version();return [...records.keys()].filter(key=>untrack(records.get(key)![0]).scope.projectId===props.projectId);});
 return <For each={keys()}>{key=>{const artifact=records.get(key)![0];return <ObjectDraftBoundary artifact={artifact()}><DocumentFrame artifact={artifact()} visible={()=>!!props.active&&panelKey(props.active)===key} registry={props.registry} context={props.context} onBack={props.onBack} onFileRelated={props.onFileRelated} observe={observe}/></ObjectDraftBoundary>;}}</For>;
}
