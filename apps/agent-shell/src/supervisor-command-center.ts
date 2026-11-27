import type {SupervisorSnapshot,SupervisorTask,SupervisorTaskKey} from './global-supervisor';
import {centerText,type SupervisorCenterLocale} from './supervisor-command-center-copy.ts';

export type CenterReadStatus='INITIAL'|'READY'|'OBSERVATION_UNAVAILABLE'|'NOT_READY';
export interface CenterItem {id:string;task?:SupervisorTask;tone:'error'|'attention'|'running'|'quiet';priority:number;label:string;status:string}
export interface CenterProjection {items:CenterItem[];tasks:CenterItem[];counts:{running:number;attention:number;recent:number};observedAt:string|null;cached:boolean}
export interface CenterStatusAnswer {kind:'LOCAL_STATUS';text:string;tasks:SupervisorTask[];observedAt:string|null;cached:boolean}
export const supervisorTaskIdentity=(key:SupervisorTaskKey)=>JSON.stringify([key.kind,key.project_id??null,key.object_id,key.operation_id??null,key.generation]);
function taskItem(task:SupervisorTask,locale:SupervisorCenterLocale,now:number):CenterItem{
  const stale=task.observation_status==='STALE'||task.observation_status==='CURRENT'&&now>Date.parse(task.observed_at)+task.fresh_for_seconds*1000;
  const uncertain=stale||task.observation_status==='NEEDS_CHECK_AFTER_RESTART';
  const failed=task.classification==='EXECUTION_ERROR'||task.classification==='TIMED_OUT'||task.validation_status==='FAILED';
  const attention=uncertain||failed||task.classification==='WAITING_USER'||task.classification==='NEEDS_CHECK';
  const running=!attention&&['RUNNING','CANCEL_REQUESTED'].includes(task.classification);
  const status=centerText(locale,task.classification==='WAITING_USER'&&task.wait_reason==='PERMISSION'?'PERMISSION':task.classification)+
    (task.observation_status==='NEEDS_CHECK_AFTER_RESTART'?' · '+centerText(locale,'restart'):stale?' · '+centerText(locale,'stale'):'')+
    (task.validation_status==='FAILED'?' · '+centerText(locale,'validation'):'');
  return {id:supervisorTaskIdentity(task.key),task,label:task.label,status,tone:failed?'error':attention?'attention':running?'running':'quiet',
    priority:failed?0:uncertain||task.classification==='NEEDS_CHECK'?1:attention?2:running?3:4};
}
export function projectSupervisorCenter(snapshot:SupervisorSnapshot|undefined,locale:SupervisorCenterLocale,readStatus:CenterReadStatus='READY',now=Date.now()):CenterProjection{
  const counts={running:0,attention:0,recent:0};
  if(!snapshot)return {items:[{id:'observer:initial',label:centerText(locale,readStatus==='OBSERVATION_UNAVAILABLE'?'unavailable':'initial'),status:'',tone:readStatus==='OBSERVATION_UNAVAILABLE'?'attention':'quiet',priority:1}],tasks:[],counts,observedAt:null,cached:true};
  const observer=snapshot.observer.status;
  const cached=readStatus!=='READY'||!snapshot.settings.enabled||!['READY','PARTIAL'].includes(observer);
  const duplicate=new Set<string>(),seen=new Set<string>();
  for(const task of snapshot.tasks){const key=supervisorTaskIdentity(task.key);if(seen.has(key))duplicate.add(key);seen.add(key);}
  const tasks=snapshot.tasks.filter(task=>!duplicate.has(supervisorTaskIdentity(task.key))).map(task=>{
    const item=taskItem(task,locale,now);return cached?{...item,status:item.status+' · '+centerText(locale,'last')}:item;
  }).sort((a,b)=>a.priority-b.priority||Date.parse(b.task!.observed_at)-Date.parse(a.task!.observed_at)||a.id.localeCompare(b.id));
  for(const item of tasks){if(item.tone==='running')counts.running++;else if(item.tone==='attention'||item.tone==='error')counts.attention++;else counts.recent++;}
  const notice=!snapshot.settings.enabled?'disabled':observer==='CLOSED'?'closed':readStatus==='OBSERVATION_UNAVAILABLE'||observer==='OBSERVATION_UNAVAILABLE'?'unavailable':duplicate.size||observer==='PARTIAL'?'partial':observer==='INITIAL'?'initial':undefined;
  const items:CenterItem[]=notice?[{id:'observer:'+notice,label:centerText(locale,notice),status:cached?centerText(locale,'last'):'',tone:['unavailable','partial'].includes(notice)?'attention':'quiet',priority:1},...tasks]:[...tasks];
  if(!items.length)items.push({id:'observer:empty',label:centerText(locale,'noTasks'),status:'',tone:'quiet',priority:4});
  return {items,tasks,counts,observedAt:snapshot.observer.last_success_at,cached};
}
export function summarizeSupervisorCenter(projection:CenterProjection,locale:SupervisorCenterLocale):{label:string;status:string;tone:CenterItem['tone']}{
  const {running,attention}=projection.counts;
  const counts=[running?centerText(locale,'filtered',{count:running,status:centerText(locale,'running')}):'',attention?centerText(locale,'filtered',{count:attention,status:centerText(locale,'attention')}):''].filter(Boolean).join(' · ');
  const notice=projection.items.find(item=>!item.task&&item.id!=='observer:empty');
  const tone=projection.tasks.some(item=>item.tone==='error')?'error':attention||notice?.tone==='attention'?'attention':running?'running':notice?.tone??'quiet';
  return {label:notice?.label??(counts||centerText(locale,'noTasks')),status:notice?[projection.cached?centerText(locale,'last'):'',counts].filter(Boolean).join(' · '):projection.cached?centerText(locale,'last'):'',tone};
}
export function answerSupervisorStatusQuery(text:string,projection:CenterProjection,locale:SupervisorCenterLocale):CenterStatusAnswer|undefined{
  const query=text.trim().replace(/[?？!！。]+$/u,'').toLocaleLowerCase();
  let selected:CenterItem[]|undefined,label:'running'|'attention'|'recent'|'returned'|undefined;
  if(/^(?:任务状态|任务进度|查看进度|查看状态|目前进度|总进度|进度|现在进度如何|任务怎么样了|task status|task progress|status|progress|what is running|what's running)$/u.test(query))selected=projection.tasks;
  else if(/^(?:哪些任务在运行|谁在运行|正在运行的任务|哪些还在运行|running tasks|show running tasks)$/u.test(query)){label='running';selected=projection.tasks.filter(item=>item.tone==='running');}
  else if(/^(?:谁需要我处理|哪些任务需要我处理|哪些任务异常|待处理任务|需要我做什么|tasks needing attention|what needs my attention|failed tasks)$/u.test(query)){label='attention';selected=projection.tasks.filter(item=>item.tone==='attention'||item.tone==='error');}
  else if(/^(?:哪些任务已返回|最近返回|已返回的任务|returned tasks)$/u.test(query)){label='returned';selected=projection.tasks.filter(item=>item.task?.classification==='EXECUTION_RETURNED');}
  else if(/^(?:哪些任务完成了|最近结束|finished tasks)$/u.test(query)){label='recent';selected=projection.tasks.filter(item=>item.tone==='quiet');}
  if(!selected)return;
  const status=label?centerText(locale,label):'';
  const summary=label?centerText(locale,selected.length?'filtered':'emptyFilter',{count:selected.length,status}):centerText(locale,'summary',projection.counts);
  const observer=projection.items.find(item=>!item.task&&item.id!=='observer:empty');
  return {kind:'LOCAL_STATUS',text:[projection.cached?centerText(locale,'last'):undefined,summary,observer?.label].filter(Boolean).join('\n'),tasks:selected.map(item=>item.task!),observedAt:projection.observedAt,cached:projection.cached};
}
export type CenterPauseReason='hover'|'focus'|'composition'|'draft'|'expanded'|'hidden'|'manual';
export class SupervisorTicker {
  private pauses=new Set<CenterPauseReason>();private values:CenterItem[]=[];private selectedId='';private pendingUrgent='';
  get paused(){return this.pauses.size>0;}
  get current(){return this.values.find(item=>item.id===this.selectedId)??this.values[0];}
  get length(){return this.values.length;}
  pause(reason:CenterPauseReason,value:boolean){if(value)this.pauses.add(reason);else this.pauses.delete(reason);if(!this.paused)this.flushUrgent();}
  update(values:CenterItem[]){
    const previous=this.current,old=new Map(this.values.map(item=>[item.id,item]));this.values=values;
    const urgent=values.filter(item=>item.priority<=1&&(!old.has(item.id)||old.get(item.id)!.priority>item.priority))[0];
    if(urgent&&(!previous||urgent.priority<previous.priority)){if(this.paused)this.pendingUrgent=urgent.id;else this.selectedId=urgent.id;}
    if(!values.some(item=>item.id===this.selectedId))this.selectedId=values[0]?.id??'';
  }
  private flushUrgent(){if(this.values.some(item=>item.id===this.pendingUrgent))this.selectedId=this.pendingUrgent;this.pendingUrgent='';}
  advance(){if(this.paused||this.values.length<2)return;const index=this.values.findIndex(item=>item.id===this.selectedId);this.selectedId=this.values[(index+1)%this.values.length].id;}
}
