import {For,Show} from 'solid-js';
import type {DeliveryReceipt} from './planning-delivery';
import {deliveryReview} from './delivery-review';
import {t} from './ui-language';

export default function DeliveryReviewSummary(props:{receipt:DeliveryReceipt}) {
  const review=()=>deliveryReview(props.receipt);
  const status={STALE:'文件已变化',MISSING:'文件不存在',UNKNOWN:'暂未能核对'};
  return <Show when={review().notices.length}><div class="as-delivery-review">
    <For each={review().notices}>{notice=><p class="as-inline-warning">{t(notice.message,notice.params)}</p>}</For>
    <Show when={review().issues.length}><details><summary>{t('查看需核对的文件（{count}）',{count:review().issues.length})}</summary><ul><For each={review().issues}>{issue=><li><span>{t(issue.name)}</span> · {t(status[issue.status])}</li>}</For></ul></details></Show>
  </div></Show>;
}
