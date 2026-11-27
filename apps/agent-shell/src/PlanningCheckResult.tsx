import {For,Show} from 'solid-js';
import {t} from './ui-language';
import {planningCheck,type PlanningCheckReceipt,type MissingPlanningField} from './planning-checks';

export default function PlanningCheckResult(props:{receipt:PlanningCheckReceipt;currentBasis:string}) {
  const result=()=>planningCheck(props.receipt.kind,props.receipt.value);
  const labels:Record<MissingPlanningField,string>={outputs:'交付物',acceptance:'验收条目',source_basis:'来源依据'};
  return <section class="as-planning-check" aria-label={t('本次检查结果')} aria-live="polite">
    <Show when={props.receipt.basis!==props.currentBasis}><p class="as-inline-warning">{t('检查后内容已变化，以下结果对应检查时的内容。')}</p></Show>
    <Show when={result()} fallback={<p class="as-inline-warning">{t('检查回执不完整，请核对后重新检查。')}</p>} keyed>{value=><>
      <strong>{t(value.kind==='network'?'网络结构检查完成':'交付与验收条目检查完成')}</strong>
      <p>{value.kind==='network'?t('{nodes} 个节点 · {questions} 项疑问；结构有效。',{nodes:value.nodes,questions:value.questions}):t('{complete} / {total} 项任务已填写交付物、验收条目和来源。',{complete:value.complete,total:value.total})}</p>
      <Show when={value.kind==='acceptance'&&value.issues.length}><ul><For each={value.kind==='acceptance'?value.issues:[]}>{row=><li><span>{row.id}</span> · {t('缺少：{fields}',{fields:row.missing.map(key=>t(labels[key])).join(' / ')})}</li>}</For></ul></Show>
      <small>{t('仅检查结构与条目；业务正确性和实际执行尚未验收。')}</small>
    </>}</Show>
  </section>;
}
