import {t as uiText,uiLocale} from './ui-language';
import {createEffect,createMemo,createSignal,For,on,onCleanup,Show,untrack} from 'solid-js';
import {GlobalSupervisorPanelModel,globalSupervisor,supervisorActivity,supervisorAlertStatus,supervisorCreatedAt,supervisorGroup,supervisorObservedAt,supervisorTaskContext,supervisorTaskIdentity,supervisorTaskStatus,
  type GlobalSupervisorClient,type SupervisorAlert,type SupervisorAlertGroup,type SupervisorGroup,type SupervisorPanelState,type SupervisorSnapshot,type SupervisorTask} from './global-supervisor';
import './global-supervisor.css';

export interface GlobalSupervisorPanelProps {
  active:boolean;onOpenTask:(task:SupervisorTask)=>unknown;snapshot?:SupervisorSnapshot;managed?:boolean;
  onSnapshot?:(snapshot:SupervisorSnapshot)=>void;client?:GlobalSupervisorClient;
}
const groups:{id:SupervisorGroup;title:string}[]=[{id:'attention',title:'待处理'},{id:'running',title:'运行'},{id:'recent',title:'已返回 / 已结束'}];
const alertIdentity=(alert:SupervisorAlert)=>JSON.stringify(alert.alert_ref);

export default function GlobalSupervisorPanel(props:GlobalSupervisorPanelProps){
  const client=props.client??globalSupervisor;
  const [view,setView]=createSignal<SupervisorPanelState>({draft:{enabled:true,notifications_enabled:false,interval_seconds:'30'},dirty:false,busy:'',error:'',notice:'',unknown:false,conflict:false});
  const [search,setSearch]=createSignal('');
  const model=new GlobalSupervisorPanelModel(client,setView,task=>props.onOpenTask(task),snapshot=>props.onSnapshot?.(snapshot));
  let timer:ReturnType<typeof setTimeout>|undefined,stopped=false;
  const snapshot=()=>view().snapshot;
  const taskMap=createMemo(()=>new Map((snapshot()?.tasks??[]).map(task=>[supervisorTaskIdentity(task.key),task])));
  const activity=createMemo(()=>{view();return supervisorActivity(snapshot()?.alerts??[],ref=>client.alertUnresolved(ref));});
  const historyMap=createMemo(()=>new Map(activity().history.map(group=>[group.identity,group])));
  const attentionMap=createMemo(()=>new Map(activity().attention.map(group=>[group.identity,group])));
  const filtered=createMemo(()=>{
    const value=search().trim().toLocaleLowerCase();
    return (snapshot()?.tasks??[]).filter(task=>!value||[task.label,task.display_context?.project_label,task.display_context?.workspace_label].some(text=>text?.toLocaleLowerCase().includes(value)));
  });
  // Source creation dates can order tasks; repeated observation timestamps cannot establish completion order.
  const tasks=(group:SupervisorGroup)=>filtered().filter(task=>supervisorGroup(task)===group).sort((a,b)=>{
    const left=a.display_context?.task_created_at,right=b.display_context?.task_created_at;
    return left&&right?Date.parse(right)-Date.parse(left):0;
  });
  let focusedTask:{identity:string;element:HTMLButtonElement;group:SupervisorGroup}|undefined;
  // One keyed sequence owns every task button, including a move between status groups.
  const taskRows=()=>{
    const ticket=focusedTask,current=ticket&&taskMap().get(ticket.identity),doc=ticket?.element.ownerDocument;
    if(ticket&&current&&doc?.activeElement===ticket.element&&ticket.group!==supervisorGroup(current)){
      ticket.group=supervisorGroup(current);
      // Moving the same DOM node can blur it. Restore only its own lost focus;
      // a newly focused draft/control, removed task or closed panel always wins.
      queueMicrotask(()=>{
        const element=ticket.element;
        if(stopped||!props.active||focusedTask!==ticket||!element.isConnected||!taskMap().has(ticket.identity)||
          doc.activeElement!==doc.body||doc.visibilityState!=='visible'||!doc.hasFocus()||element.disabled||!element.checkVisibility())return;
        const rect=element.getBoundingClientRect(),surface=element.closest('.as-supervisor')?.getBoundingClientRect();
        if(surface&&Math.max(rect.top,surface.top,0)<Math.min(rect.bottom,surface.bottom,doc.documentElement.clientHeight)&&
          Math.max(rect.left,surface.left,0)<Math.min(rect.right,surface.right,doc.documentElement.clientWidth))element.focus({preventScroll:true});
      });
    }
    return groups.flatMap(group=>{
      const rows=tasks(group.id);return rows.length?[`group:${group.id}`,...rows.slice(0,50).map(task=>supervisorTaskIdentity(task.key)),...rows.length>50?[`limit:${group.id}`]:[]]:[];
    });
  };
  createEffect(on(()=>props.snapshot,value=>{if(value)untrack(()=>model.accept(value));}));
  createEffect(on(()=>[props.active,props.managed] as const,([active,managed])=>{
    if(timer!==undefined)clearTimeout(timer);timer=undefined;model.setActive(active);
    if(!active||managed)return;
    const read=async()=>{
      await model.refresh();
      if(!stopped&&props.active&&!props.managed)timer=setTimeout(()=>void read(),Math.min(30,Math.max(5,view().snapshot?.settings.interval_seconds??30))*1000);
    };
    untrack(()=>void read());
  }));
  onCleanup(()=>{stopped=true;if(timer!==undefined)clearTimeout(timer);model.dispose();});
  const blocked=()=>!props.active||!!view().busy;
  const observerText=()=>{
    const value=snapshot();if(!value)return '正在读取任务动态';
    if(value.observer.status==='CLOSED')return '本地监督已关闭';
    if(!value.settings.enabled)return '监督已关闭，保留最后观察';
    if(value.observer.status==='PARTIAL')return '部分任务状态待核查';
    if(value.observer.status==='OBSERVATION_UNAVAILABLE')return '最近一次观察不可用';
    if(value.observer.status==='INITIAL')return '等待首次本地检查';
    return uiText('最近观察 ')+(value.observer.last_success_at?supervisorObservedAt(value.observer.last_success_at,uiLocale()):uiText('尚未观察'));
  };
  function TaskContext(contextProps:{task:SupervisorTask}){
    const context=createMemo(()=>supervisorTaskContext(contextProps.task,snapshot()?.tasks??[]));
    return <Show when={context().labels.length||context().created_at}><span class="as-supervisor-task-context">
      <Show when={context().labels.length}><span>{context().labels.join(' · ')}</span></Show>
      <Show when={context().created_at}><span>{uiText(context().created_kind==='task'?'任务创建于 {time}':'会话创建于 {time}',{time:supervisorCreatedAt(context().created_at!,uiLocale())})}</span></Show>
    </span></Show>;
  }
  function AlertEvents(eventProps:{group:SupervisorAlertGroup;actionable?:boolean}){
    const [limit,setLimit]=createSignal(20);
    const rows=createMemo(()=>new Map(eventProps.group.alerts.map(alert=>[alertIdentity(alert),alert])));
    return <div class="as-supervisor-events"><For each={eventProps.group.alerts.slice(0,limit()).map(alertIdentity)}>{identity=>{
      const alert=()=>rows().get(identity)!;
      return <article class="as-supervisor-event"><div><strong>{supervisorAlertStatus(alert(),uiText)}</strong>
        <small>{uiText('记录于 {time}',{time:supervisorCreatedAt(alert().created_at,uiLocale())})}
          <Show when={alert().acknowledged}> · {uiText('已确认')}</Show></small></div>
        <Show when={eventProps.actionable&&(!alert().acknowledged||client.alertUnresolved(alert().alert_ref))}><button type="button" disabled={blocked()||client.alertUnresolved(alert().alert_ref)}
          onClick={()=>void model.acknowledge(alert())} aria-label={uiText('确认提醒：')+alert().label}>
          {client.alertUnresolved(alert().alert_ref)?uiText('待核对'):uiText('知道了')}</button></Show>
      </article>;
    }}</For><Show when={eventProps.group.alerts.length>limit()}><button type="button" class="as-supervisor-more" onClick={()=>setLimit(value=>value+20)}>{uiText('显示更多变化')}</button></Show></div>;
  }
  function AlertGroup(groupProps:{group:SupervisorAlertGroup;important?:boolean}){
    const [open,setOpen]=createSignal(false);
    const task=()=>taskMap().get(groupProps.group.identity);
    const statuses=()=>[...new Set(groupProps.group.alerts.map(alert=>client.alertUnresolved(alert.alert_ref)?uiText('确认结果待核对'):supervisorAlertStatus(alert,uiText)))];
    return <details class={groupProps.important?'as-supervisor-important-group':'as-supervisor-history-group'} onToggle={event=>setOpen(event.currentTarget.open)}>
      <summary><span class="as-supervisor-change-heading"><strong>{task()?.label??groupProps.group.alerts[0]?.label}</strong>
        <span class="as-supervisor-change-count">{uiText('{count} 条变化',{count:groupProps.group.alerts.length})}</span></span>
        <Show when={task()}><TaskContext task={task()!}/></Show>
        <Show when={groupProps.important}><span class="as-supervisor-warning-status">{statuses().slice(0,3).join(' · ')}
          <Show when={statuses().length>3}> · {uiText('另有 {count} 类记录',{count:statuses().length-3})}</Show></span></Show>
      </summary><Show when={open()}><AlertEvents group={groupProps.group} actionable={groupProps.important}/></Show>
    </details>;
  }
  return <Show when={props.active}><section class="as-supervisor" aria-label={uiText('全局任务动态')}>
    <header class="as-supervisor-header"><div><h2>{uiText('任务动态')}</h2><p>{uiText(observerText())}</p></div>
      <button type="button" class="as-supervisor-icon" aria-label={uiText('读取任务动态')} disabled={blocked()} onClick={()=>void model.refresh()} title={uiText('读取现有任务状态')}>
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M15.7 6.5A6 6 0 1 0 16 12M16 3v4h-4"/></svg>
      </button></header>
    <Show when={view().busy}><p class="as-supervisor-feedback" role="status">{uiText(view().busy)}…</p></Show>
    <Show when={view().error}><p class="as-supervisor-feedback" data-tone="error" role="alert">{uiText(view().error)}</p></Show>
    <Show when={view().notice}><p class="as-supervisor-feedback" role="status">{uiText(view().notice)}</p></Show>
    <Show when={snapshot()?.observer.actual_error_code}><p class="as-supervisor-feedback" data-tone="error">{snapshot()!.observer.actual_error_code}</p></Show>
    <Show when={(snapshot()?.tasks.length??0)>12||search()}><input class="as-supervisor-search" type="search" aria-label={uiText('筛选任务')} placeholder={uiText('筛选任务')} value={search()} onInput={event=>setSearch(event.currentTarget.value)}/></Show>
    <div class="as-supervisor-groups"><For each={taskRows()}>{identity=>{
      if(identity.startsWith('group:')){
        const group=groups.find(group=>group.id===identity.slice(6))!;
        return <h3>{uiText(group.title)}<span>{tasks(group.id).length}</span></h3>;
      }
      if(identity.startsWith('limit:'))return <p class="as-supervisor-limit">{uiText('显示 50 项，可按名称或来源筛选。')}</p>;
      const task=()=>taskMap().get(identity)!;
      return <button type="button" class="as-supervisor-task" disabled={!props.active} onClick={()=>void model.open(task())} title={task().label}
        onFocus={event=>{focusedTask={identity,element:event.currentTarget,group:supervisorGroup(task())};}}>
        <span class="as-supervisor-task-heading"><span class="as-supervisor-task-label">{task().label}</span>
          <span class="as-supervisor-task-status" data-category={supervisorGroup(task())}>{uiText(supervisorTaskStatus(task(),uiText))}</span></span>
        <TaskContext task={task()}/>
        <Show when={task().actual_error_code||task().validation_status==='FAILED'||task().validation_status==='PASSED'}><small>
          <Show when={task().actual_error_code}>{task().actual_error_code}</Show>
          <Show when={task().validation_status==='FAILED'}> {uiText('验证失败')}</Show><Show when={task().validation_status==='PASSED'}> {uiText('有验证通过记录')}</Show>
        </small></Show>
      </button>;
    }}</For></div>
    <Show when={snapshot()&&!filtered().length}><p class="as-supervisor-empty">{search()?uiText('没有匹配的任务'):uiText('还没有可观察的任务')}</p></Show>
    <Show when={!snapshot()&&!view().busy}><p class="as-supervisor-empty">{uiText('尚未读取到任务状态，可刷新核对。')}</p></Show>
    <Show when={activity().attention.length}><section class="as-supervisor-important" aria-label={uiText('需核查的记录')}>
      <h3>{uiText('需核查的记录')}</h3><p>{uiText('保留尚未确认的问题记录；不代表当前任务仍处于该状态。')}</p>
      <For each={activity().attention.map(group=>group.identity)}>{identity=><AlertGroup group={attentionMap().get(identity)!} important/>}</For>
    </section></Show>
    <Show when={activity().history.length||snapshot()?.alerts_has_more}><details class="as-supervisor-history">
      <summary>{uiText('已载入的变化记录')}<span>{snapshot()?.alerts.length??0}</span></summary>
      <p class="as-supervisor-limit">{uiText('按任务收拢，展开查看；无需逐条确认普通状态变化。')}</p>
      <For each={activity().history.map(group=>group.identity)}>{identity=><AlertGroup group={historyMap().get(identity)!}/>}</For>
      <Show when={snapshot()?.alerts_has_more}><p class="as-supervisor-limit">{uiText('仅载入最近 ')}{snapshot()?.alerts.length}{uiText(' 条提醒；更早记录仍保留。')}</p></Show>
    </details></Show>
    <details class="as-supervisor-settings"><summary aria-label={uiText('监督设置')}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m8 2-.5 2-2 .9-2-.5L2 7l1.5 1.5v2L2 12l1.5 2.6 2-.5 2 .9.5 2h3l.5-2 2-.9 2 .5L18 12l-1.5-1.5v-2L18 7l-1.5-2.6-2 .5-2-.9-.5-2Z"/><circle cx="10" cy="9.5" r="2.4"/></svg>{uiText('监督设置')}</summary>
      <form onSubmit={event=>{event.preventDefault();void model.save();}}>
        <label><input type="checkbox" checked={view().draft.enabled} disabled={blocked()} onChange={event=>model.edit({enabled:event.currentTarget.checked})}/>{uiText('后台监督')}</label>
        <label><input type="checkbox" checked={view().draft.notifications_enabled} disabled={blocked()} onChange={event=>model.edit({notifications_enabled:event.currentTarget.checked})}/>{uiText('系统提醒')}</label>
        <label class="as-supervisor-interval"><span>{uiText('检查间隔')}</span><input type="number" min="5" max="3600" step="1" aria-label={uiText('检查间隔（秒）')} value={view().draft.interval_seconds}
          disabled={blocked()} onInput={event=>model.edit({interval_seconds:event.currentTarget.value})}/><span>{uiText('秒')}</span></label>
        <p>{uiText('本地状态检查不调用模型。关闭提醒仍保留任务记录。')}</p>
        <div class="as-supervisor-setting-actions"><button type="button" disabled={blocked()} onClick={()=>void model.check()}>{uiText('检查一次')}</button>
          <button type="submit" disabled={blocked()||view().unknown||!view().dirty||!view().basis}>{view().conflict?uiText('按当前版本保存'):uiText('保存设置')}</button></div>
      </form>
    </details>
  </section></Show>;
}
