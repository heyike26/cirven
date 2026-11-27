import {createMemo,createSignal,Show} from 'solid-js';
import {t as uiText} from './ui-language';
import {summaryHTML,summaryNeedsDisclosure} from './summary-content';

/** Existing task summary, with optional disclosure; no draft or owner writes. */
export default function SummaryContent(props:{text:string;fieldPointer?:string;disclosure?:boolean}){
 const html=createMemo(()=>summaryHTML(props.text));
 const [expanded,setExpanded]=createSignal(false);
 const content=()=><div class="as-proposal-summary as-summary-content" data-candidate-field={props.fieldPointer} innerHTML={html()}/>;
 return <Show when={props.disclosure??summaryNeedsDisclosure(props.text)} fallback={content()}>
  <details class="as-summary-disclosure" open={expanded()} onToggle={event=>setExpanded(event.currentTarget.open)}>
   <summary>{expanded()?uiText('收起说明'):uiText('展开说明')}</summary>
   {content()}
  </details>
 </Show>;
}
