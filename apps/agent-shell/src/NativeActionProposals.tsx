import {createEffect,createMemo,createSignal,onCleanup,Show} from 'solid-js';
import NativeAppendPanel from './NativeAppendPanel';
import {uiLocale} from './ui-language';
import type {NativeMessage,NativeRef} from './native-collaboration';
import {checkNativeActionProposal,verifyNativeActionProposal,type NativeProposalAssessment,type NativeProposalScope} from './native-action-proposals';
import './native-append.css';

export type NativeActionProposalsProps={message:NativeMessage;scope:NativeProposalScope;contextRef?:NativeRef;onConfigureModel?:()=>void};
const copy={zh:{reopen:'查看追加建议',readonly:'追加建议暂不可执行',invalid:'此操作建议无法核对',dismiss:'暂不操作',details:'查看原因',historical:'这是历史记录，请在当前页面提出新的修改要求。',changed:'当前页面已变化，请回到原页面后核查。',waiting:'原任务尚未完成。',unavailable:'原建议的条件已变化，请核查原任务。'},en:{reopen:'View append suggestion',readonly:'Append suggestion is read-only',invalid:'This action suggestion could not be verified',dismiss:'Not now',details:'View reason',historical:'This is a historical record. Request a new change on the current page.',changed:'The current page has changed. Return to the original page to review it.',waiting:'The original task has not finished.',unavailable:'The original conditions have changed. Review the original task.'}};
export default function NativeActionProposals(props:NativeActionProposalsProps){
  const text=()=>uiLocale().startsWith('zh')?copy.zh:copy.en;
  const candidate=createMemo(()=>checkNativeActionProposal(props.message,props.scope,props.contextRef));
  const [assessment,setAssessment]=createSignal<NativeProposalAssessment>(),[dismissed,setDismissed]=createSignal('');
  let alive=true,generation=0,lastKey='';onCleanup(()=>{alive=false;generation++;});
  createEffect(()=>{
    const value=candidate();if(value.validationKey===lastKey)return;lastKey=value.validationKey;const current=++generation;setAssessment(undefined);
    void verifyNativeActionProposal(value).then(result=>{if(alive&&current===generation)setAssessment(result);});
  });
  const visible=createMemo(()=>{const value=assessment();return value&&value.status!=='ABSENT'?value:undefined;});
  const closed=()=>visible()?.identity===dismissed();
  const reason=()=>{const value=visible();if(!value||value.status==='APPEND')return '';if(value.reason==='CONTEXT_CHANGED')return text().changed;if(value.reason==='WAITING_PARENT_RESULT')return text().waiting;if(value.reason==='HISTORICAL_READONLY'||props.message.record_origin==='HISTORICAL_READONLY')return text().historical;return text().unavailable;};
  return <Show when={visible()}>{value=><section class="as-append-action" data-native-proposal-parent={props.message.operation_id}>
    <Show when={closed()}><div class="as-append-actions"><button type="button" onClick={()=>setDismissed('')}>{text().reopen}</button></div></Show>
    <div hidden={closed()} inert={closed()}>
      <Show when={value().status==='APPEND'}><NativeAppendPanel visible={!closed()} request={(value() as Extract<NativeProposalAssessment,{status:'APPEND'}>).request} contextRef={props.contextRef} onConfigureModel={props.onConfigureModel} onDismiss={()=>setDismissed(value().identity)}/></Show>
      <Show when={value().status!=='APPEND'}><article class="as-append-card"><header><strong>{value().status==='INVALID'?text().invalid:text().readonly}</strong></header><details><summary>{text().details}</summary><p class="as-append-note">{reason()}</p></details><div class="as-append-actions"><button type="button" onClick={()=>setDismissed(value().identity)}>{text().dismiss}</button></div></article></Show>
    </div>
  </section>}</Show>;
}
