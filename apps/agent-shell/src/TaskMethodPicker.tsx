import {createSignal,Show} from 'solid-js';
import StudioPopover from './StudioPopover';
import {railKey} from './activity-rail';
import {t as uiText} from './ui-language';
import type {OriginalAgent} from './agent-sessions';

/** Task methods belong to input; session selection and management belong to the left conversation area. */
export default function TaskMethodPicker(props:{mode:string;onChoose:(mode:string)=>void;onOpening:()=>void;agentMode?:OriginalAgent;onAgentMode?:(mode:OriginalAgent)=>void;workflowUnavailable?:boolean;activeTask?:boolean;locale?:string}){
 const [open,setOpen]=createSignal(false);
 let trigger:HTMLButtonElement|undefined;

 function choose(mode:'direct'|'discovery'){setOpen(false);props.onChoose(mode);}
 return <StudioPopover open={open()} onOpenChange={setOpen} label={uiText("任务方式")} placement="top-start" class="as-task-method-popover" initialFocus={()=>document.querySelector<HTMLElement>('#as-task-method-options [aria-pressed=true]')??undefined} trigger={<button type="button" ref={trigger} class="as-composer-tool as-task-method-trigger" data-agent-mode={props.agentMode} aria-label={uiText("选择任务方式")+(props.agentMode?" · "+props.agentMode:"")} aria-expanded={open()} aria-controls="as-task-method-options" onClick={()=>{if(!open())props.onOpening();setOpen(value=>!value);}}>
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M3 4h12M3 9h12M3 14h12"/><circle cx="6" cy="4" r="2" fill="white"/><circle cx="12" cy="9" r="2" fill="white"/><circle cx="7" cy="14" r="2" fill="white"/></svg>
  <span>{props.agentMode==='workflow'?'Workflow':props.agentMode?(props.locale?.startsWith('en')?(props.agentMode==='plan'?'Plan':'Build'):(props.agentMode==='plan'?'规划':'执行')):props.mode==='original'?uiText("项目会话"):props.mode==='discovery'?uiText("需求梳理"):uiText("普通任务")}</span>
 </button>}>
  <div class="as-task-method-options" id="as-task-method-options" role="group" aria-label={uiText("任务方式")} onKeyDown={event=>railKey(event,event.currentTarget)}>
   <strong>{uiText("任务方式")}</strong>
   <Show when={props.agentMode} fallback={<><button type="button" aria-pressed={props.mode==='direct'} onClick={()=>choose('direct')}><span>{uiText("普通任务")}</span><small>{uiText("处理当前工作或所选文件")}</small></button><button type="button" aria-pressed={props.mode==='discovery'} onClick={()=>choose('discovery')}><span>{uiText("需求梳理")}</span><small>{uiText("澄清要求、拆解任务与交互图")}</small></button></>}>
    <button type="button" aria-pressed={props.agentMode==='build'} onClick={()=>{setOpen(false);props.onAgentMode?.('build');}}><span>{props.locale?.startsWith('en')?'Build':'执行'}</span><small>{props.locale?.startsWith('en')?'Use available tools and check the result':'使用已有工具完成工作并检查结果'}</small></button>
    <button type="button" disabled={props.workflowUnavailable} aria-pressed={props.agentMode==='workflow'} onClick={()=>{setOpen(false);props.onAgentMode?.('workflow');}}><span>Workflow</span><small>{uiText(props.workflowUnavailable?'当前连接未加载此工作模式':'按目标组织待办，用已有工具推进并检查')}</small></button>
    <button type="button" aria-pressed={props.agentMode==='plan'} onClick={()=>{setOpen(false);props.onAgentMode?.('plan');}}><span>{props.locale?.startsWith('en')?'Plan':'规划'}</span><small>{props.locale?.startsWith('en')?'Review the approach before execution':'先梳理方案，再确认执行'}</small></button>
    <Show when={props.activeTask}><small class="as-task-method-current">{uiText('此选择用于后续消息，当前任务保持原方式')}</small></Show>
   </Show>
  </div>
 </StudioPopover>;
}
