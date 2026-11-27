import {uiLocale} from './ui-language.ts';
import type {NativeAppendState} from './native-append';

export function appendStatusCopy(state?:NativeAppendState){
  const en=uiLocale()==='en';
  if(state?.task_completed===true&&state.task?.task_outcome?.task_outcome?.verification?.status==='VERIFIED')return en?'Append verified · not saved':'追加已核实 · 未保存';
  const labels:Record<string,[string,string]>={
    NOT_ADAPTED:['当前页面暂不支持','Unavailable for this page'],ADAPTED_REQUIRES_APPROVAL:['可以准备追加预览','Append preview available'],
    PREPARED:['等待确认','Ready for review'],APPROVED:['本次操作已允许','Action approved'],NOT_CONFIGURED:['需要选择模型','Model required'],
    HANDOFF_REQUIRED:['请核查原窗口','Check the original window'],BASE_CHANGED:['原文稿已变化','Original document changed'],PARTIAL:['结果需核查','Check the result'],
    UNKNOWN_OUTCOME:['结果待核查','Outcome unconfirmed'],UNKNOWN:['状态待核查','Status unconfirmed'],HISTORICAL_READONLY:['历史操作','Recorded action'],
    REVOKED:['权限已撤销','Permission revoked'],FINISHED:['执行结束，结果待核查','Finished · result unverified'],
    APPROVAL_CANCELLED:['已取消','Cancelled'],CANCELLED:['已取消','Cancelled'],STARTING:['正在切换到原窗口','Opening the original window'],
    INTERPRETING:['正在处理','Processing'],RUNNING:['正在执行','Running'],AWAITING_VERIFICATION:['正在核查结果','Checking the result'],NO_RECORD:['原回执待核查','Receipt unavailable'],
  };
  return labels[state?.state??'']?.[en?1:0]??(en?'Status unavailable':'状态暂不可用');
}

export function appendCopy(){return uiLocale()==='en'?{
  title:'Append to current document',review:'Check and allow',dismiss:'Not now',collapse:'Dismiss',
  preparing:'Preparing preview…',preparingLabel:'Append preview',target:'Target',payload:'Text to append',
  scope:'One append · brings the target forward · does not save',inspect:'Check operation',details:'Operation details',
  changed:'The current page changed. This preview still refers to its original target.',
  model:'Choose a model to continue.',configure:'Choose model',unavailable:'This action is unavailable for the current page.',
  history:'Recent native actions',empty:'No recorded actions.',refresh:'Refresh',takeover:'Take control',
  recovered:'Recorded action · inspect only',unverified:'Check the original document before starting another action.',
  inspecting:'Checking operation…',approving:'Waiting for confirmation…',takingOver:'Requesting control…',
  noBridge:'Native actions are unavailable here.',noContext:'Reference the current page first.',
}: {
  title:'追加到当前文稿',review:'检查并允许',dismiss:'暂不操作',collapse:'收起',
  preparing:'正在准备预览…',preparingLabel:'追加预览',target:'目标',payload:'将追加的文字',
  scope:'单次追加 · 会切到原窗口 · 不保存',inspect:'核查原操作',details:'操作详情',
  changed:'当前页面已变化，此预览仍指向原目标。',
  model:'选择模型后继续。',configure:'选择模型',unavailable:'当前页面暂不支持此操作。',
  history:'最近的原生操作',empty:'暂无操作记录。',refresh:'刷新',takeover:'由我接管',
  recovered:'历史回执 · 仅供核查',unverified:'请先核对原文稿的实际内容，再发起新操作。',
  inspecting:'正在核查…',approving:'等待确认…',takingOver:'正在请求接管…',
  noBridge:'此处暂不可用原生操作。',noContext:'请先引用当前页面。',
};}
