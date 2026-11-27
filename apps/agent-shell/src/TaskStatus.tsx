import {t} from './ui-language';

const labels:Record<string,[string,string]>={
  REQUESTING:['正在连接','active'],QUEUED:['排队中','quiet'],PENDING:['等待处理','quiet'],
  PROCESSING:['处理中','active'],RUNNING:['执行中','active'],EXECUTING:['执行中','active'],
  PROPOSED:['待审阅','review'],WAITING_USER:['等你回复','review'],NEEDS_CONFIRMATION:['待确认','review'],
  READY:['已就绪','quiet'],RETURNED:['已回复','success'],COMPLETED:['已完成','success'],SUCCEEDED:['已完成','success'],
  FAILED:['未完成','error'],CANCELLED:['已停止','quiet'],UNKNOWN_OUTCOME:['待核对','review'],PARTIAL:['部分完成','review'],
  STAGE_REPORT:['阶段报告','review'],EXPORTED_CONFIRMED_PLAN_DERIVATIVES:['计划已导出','success'],
  FAILED_OR_PARTIAL:['需要检查','review'],SAVED:['已保存','success'],ANSWER_VIEW_CHANGED:['回复已更新','quiet'],CHANGED_OUTPUTS:['产物已更新','quiet'],
  NOT_CONFIGURED:['未连接模型','review'],UNAVAILABLE:['暂不可用','review'],TIMED_OUT:['响应超时','review'],
};
export function taskStatus(value:string):{label:string;tone:string}{
  const [label,tone]=labels[value.toUpperCase()]??['待核对','review'];return{label,tone};
}
export default function TaskStatus(props:{value:string}){
  const state=()=>taskStatus(props.value);
  return <span class="as-task-status" data-tone={state().tone} data-status={props.value}>{t(state().label)}</span>;
}
