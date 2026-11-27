import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText} from './ui-language';
import {createEffect,createSignal,onCleanup,onMount,Show,untrack} from 'solid-js';
import {workspaceBridge,WorkspaceSurfaceController,surfaceBounds,surfaceIntersection,type SurfacePresentation,type WorkspaceSurface,type SurfaceBounds} from './workspace-surface';
export default function NativeDesktopSurface(props:{presentation:SurfacePresentation;onSurface:(surface?:WorkspaceSurface)=>void;layoutVersion:number;referenceVersion?:number}){
 let area!:HTMLDivElement;let timer:ReturnType<typeof setTimeout>|undefined;const bridge=workspaceBridge(globalThis);const [surface,setSurface]=createSignal<WorkspaceSurface>();const [error,setError]=createSignal<UIErrorCopy>('');
 const controller=bridge?new WorkspaceSurfaceController(bridge,(value,reason)=>{setSurface(value);setError(reason??'');props.onSurface(value);}):undefined;
 function layout(refresh=false){if(!controller||!area?.getClientRects().length)return;const shell=area.closest('.as-shell'),rect=surfaceBounds(area.getBoundingClientRect());if(!rect)return;const popup:SurfaceBounds[]=[];
  const selectors=['.as-interaction-panel[data-visible=true][data-mode=MODELESS_POPOVER]','.as-fullscreen-edge','.as-fullscreen-controls','.as-dialog'];if(shell?.getAttribute('data-fullscreen-files')==='true')selectors.push('.as-sidebar');if(shell?.getAttribute('data-fullscreen-agent')==='true')selectors.push('.as-agent');
  for(const element of shell?.querySelectorAll<HTMLElement>(selectors.join(','))??[]){if(element.getClientRects().length){const actual=element.getBoundingClientRect(),bounds=surfaceIntersection(actual,rect);if(bounds)popup.push(bounds);}}
  const value={bounds:rect,presentation:props.presentation,popover_bounds:popup};if(refresh)controller.refreshReference(value);else controller.layout(value);
 }
 function schedule(){if(timer)clearTimeout(timer);timer=setTimeout(()=>layout(),40);}
 onMount(()=>{const resize=new ResizeObserver(schedule);resize.observe(area);const shell=area.closest('.as-shell');if(shell)resize.observe(shell);const mutation=new MutationObserver(schedule);if(shell)mutation.observe(shell,{attributes:true,subtree:true,attributeFilter:['data-mode','data-visible','data-fullscreen','data-fullscreen-files','data-fullscreen-agent','data-files-hidden','data-agent-hidden','style']});window.addEventListener('resize',schedule);window.addEventListener('scroll',schedule,true);schedule();onCleanup(()=>{resize.disconnect();mutation.disconnect();window.removeEventListener('resize',schedule);window.removeEventListener('scroll',schedule,true);if(timer)clearTimeout(timer);controller?.stop();});});
 let lastReferenceVersion=props.referenceVersion??0;
 createEffect(()=>{const version=props.referenceVersion??0;if(version===lastReferenceVersion)return;lastReferenceVersion=version;untrack(()=>layout(true));});
 createEffect(()=>{props.presentation;props.layoutVersion;untrack(schedule);});
 return <div ref={area} class="as-native-desktop-surface" data-native-status={surface()?.status??'WINDOWS_DESKTOP_PENDING'} aria-label={uiText("本机主工作面")}><Show when={!bridge||surface()?.status!=='ACTIVE'}><div class="as-desktop-unavailable"><h2>{uiText("本机工作面")}</h2><p>{surface()?.reason??surface()?.error?.message??uiText("此入口尚未连接 Windows 桌面。目录与已支持的文稿预览仍可使用。")}</p><span>WINDOWS_DESKTOP_PENDING</span></div></Show><Show when={error()}><p role="alert" class="as-desktop-error">{authoredText(error(),uiText)}</p></Show></div>;
}
