import {createSignal,For,Show} from 'solid-js';
import type {AgentSession} from './agent-sessions';
import {t as uiText} from './ui-language';
import {workflowTodoIsCurrent} from './workflow-progress';

/** The original SDK todo is an observation, not an inferred dependency graph. */
export default function WorkflowProgress(props:{session?:AgentSession}){
 const[open,setOpen]=createSignal(false);
 const binding=()=>props.session?.work_mode_message;
 const items=()=>props.session?.todo.filter(row=>typeof row.content==='string'&&row.content.trim())??[];
 const completed=()=>items().filter(row=>row.status==='completed').length;
 const current=()=>workflowTodoIsCurrent(props.session);
 const status=(value:unknown)=>uiText(value==='completed'?'已完成':value==='in_progress'?'执行中':value==='pending'?'等待处理':value==='cancelled'?'已停止':'待核对');
 return <Show when={binding()?.agent==='workflow'&&items().length}>
  <details class="as-workflow-progress" open={open()} onToggle={event=>setOpen(event.currentTarget.open)}>
   <summary><span>Workflow · {uiText(current()?'工作进度':'上次工作进度')}</span><span>{completed()}/{items().length}</span></summary>
   <div class="as-workflow-progress-body">
    <small>{uiText('来自原 Agent 待办，结果仍需核验')}</small>
    <Show when={!current()}><p>{uiText('尚未核实为当前任务的待办，保留上次观察')}</p></Show>
    <ol><For each={items()}>{item=><li><span>{item.content as string}</span><small>{status(item.status)}</small></li>}</For></ol>
   </div>
  </details>
 </Show>;
}
