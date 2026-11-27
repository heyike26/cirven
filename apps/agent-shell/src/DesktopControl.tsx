import {t as uiText} from './ui-language';
import {createEffect,createSignal,onCleanup,onMount,Show,untrack} from 'solid-js';
import {operationId} from './api';
import {desktopControlBridge,DesktopControlController,delegationReason,takeoverAvailable,type DesktopControlSnapshot,type DesktopExecutionRef,type DesktopControl as DesktopControlState} from './desktop-control';
import type {WorkspaceSurface} from './workspace-surface';
export default function DesktopControl(props:{surface?:WorkspaceSurface;execution?:DesktopExecutionRef;onControl?:(value:DesktopControlState)=>void}){
 const bridge=desktopControlBridge(globalThis);const [state,setState]=createSignal<DesktopControlSnapshot>({});const [notice,setNotice]=createSignal('');const controller=bridge?new DesktopControlController(bridge,setState):undefined;let timer:ReturnType<typeof setTimeout>|undefined;let live=true;
 async function refresh(){await controller?.refresh();if(controller&&live)timer=setTimeout(()=>void refresh(),4000);}
 onMount(()=>{void refresh();onCleanup(()=>{live=false;if(timer)clearTimeout(timer);controller?.stop();});});
 createEffect(()=>{const surface=props.surface;untrack(()=>{controller?.setSource(surface);void controller?.refresh();});});
 createEffect(()=>{const value=state().control;if(value)untrack(()=>props.onControl?.(value));});
 const reason=()=>delegationReason(state().control,props.surface,props.execution);
 async function transfer(kind:'DELEGATE'|'TAKEOVER'){setNotice('');try{if(kind==='DELEGATE')await controller?.delegate(operationId(),props.execution);else await controller?.takeover(operationId());const actual=state().control;if(state().error)return;if(actual?.owner==='USER'&&kind==='TAKEOVER')setNotice('已撤回桌面控制授权；停止回执可在控制详情查看。');}catch(error){setNotice(error instanceof Error?error.message:String(error));}}
 return <section class="as-desktop-control" aria-label={uiText("电脑操作归属")} data-owner={state().control?.owner??'USER'}>
  <div class="as-control-line"><span class="as-control-owner" role="status"><i aria-hidden="true"/>{state().pending==='TAKEOVER'?uiText("正在接管…"):state().pending==='DELEGATE'?uiText("正在交接…"):state().control?.owner==='AGENT'?uiText("Agent 正在操作"):uiText("用户操作")}</span><button type="button" disabled={!controller||state().pending==='TAKEOVER'||!takeoverAvailable(state().control,state().pending)} onClick={()=>void transfer('TAKEOVER')}>{state().pending==='DELEGATE'?uiText("撤回交接"):uiText("接管")}</button><button type="button" disabled={!controller||!!state().pending||!!reason()} title={uiText(reason())||uiText("授权当前确切 Agent 执行操作电脑")} onClick={()=>void transfer('DELEGATE')}>{uiText("交给 Agent")}</button></div>
  <Show when={state().error||notice()}><p role={state().error?'alert':'status'}>{uiText(state().error??notice())}</p></Show>
  <details><summary>{state().control?.owner==='AGENT'?uiText("查看控制与停止状态"):uiText(reason())||uiText("当前电脑可交接给 Agent")}</summary><p>{uiText("只交接真实桌面操作授权；应用、视口与未保存内容保留。文件修改按其自身范围与版本校验。")}</p><Show when={state().control} fallback={<p>{uiText("尚未取得宿主控制回执，不声明 Agent 已控制电脑。")}</p>}>{control=><p>{uiText("控制版本 ")}{control().control_epoch}{uiText(" · 输入版本 ")}{control().input_epoch??uiText("未绑定")}<br/>{uiText("工作面 ")}{control().surface_ref?.id??uiText("未绑定")}<br/>{uiText("本机会话 ")}{props.surface?.session_ref?.id??uiText("未绑定")}<br/>{uiText("来源 ")}{control().source_current?uiText("当前"):uiText("尚未核对")}{uiText(" · 排队输入 ")}{control().queued_actions_enabled?uiText("已启用"):uiText("已关闭")}<Show when={control().cancellation?.status}><br/>{uiText("停止状态 ")}{control().cancellation?.status}</Show></p>}</Show><button type="button" disabled={!controller||!!state().pending} onClick={()=>void controller?.refresh()}>{uiText("刷新控制状态")}</button></details>
 </section>;
}
