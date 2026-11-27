import { createEffect, createMemo, createSignal, createUniqueId, onCleanup, For, Show, type Accessor, type JSX } from 'solid-js';
import { t, uiLocale } from './ui-language';
import { deliveryHistoryPlan, originalDeliveryTime, stableDeliveryOrder, type HistoryFeedItem } from './delivery-history';
import type { DeliveryDirectoryView } from './delivery-recovery';

export type DeliveryHistoryPresentation = { title?: string; createdAt?: string; timeLabel?: string; location?: string };
export default function DeliveryHistoryFeed(props: {
  items: HistoryFeedItem[]; directories: Record<string, DeliveryDirectoryView>;
  render: (id: string, presentation: Accessor<DeliveryHistoryPresentation>) => JSX.Element;
}) {
  const plan = createMemo(() => deliveryHistoryPlan(props.items, props.directories));
  const byId = createMemo(() => new Map(props.items.map(item => [item.id, item])));
  const [opened, setOpened] = createSignal(new Set<string>()), [order, setOrder] = createSignal<string[]>([]), [retained, setRetained] = createSignal(new Set<string>());
  const [interaction, setInteraction] = createSignal(0), controls = new Map<string, HTMLElement>(), previousHistory = new Map<string, string[]>(), prefix = createUniqueId();
  const groupOpen = (key: string) => opened().has(key);
  const cardHeld = (element: HTMLElement) => element.contains(document.activeElement) || !!element.querySelector('[aria-busy="true"],.as-chat-delivery details[open]');
  createEffect(() => {
    interaction(); const next = plan(), open = opened();
    let interacting = next.groups.some(group => open.has(group.key)); const keep = new Set<string>();
    for(const group of next.groups)if(group.ordinary.length)previousHistory.set(group.key,group.ordinary);
    for(const key of previousHistory.keys())if(!next.groups.some(group=>group.key===key))previousHistory.delete(key);
    for (const [id, element] of controls) {
      if (cardHeld(element)) { interacting = true; keep.add(id); }
      if (element.parentElement?.contains(document.activeElement)) interacting = true;
      // A changed-file error stays visible, but it is not an ongoing interaction that freezes the timeline.
      if(element.querySelector('.as-chat-delivery [role="alert"]'))keep.add(id);
    }
    // Once an older card is being read/opened, a later worker must not hide its controls.
    setRetained(keep);
    setOrder(previous => stableDeliveryOrder(previous, next.ids, interacting));
  });
  const first = (key: string) => order().find(id => plan().byId.get(id)?.key === key);
  const visible = (id: string) => { const group = plan().byId.get(id); return !group || !group.ordinary.includes(id) || groupOpen(group.key) || retained().has(id); };
  const presentation = (id: string): DeliveryHistoryPresentation => {
    const item = byId().get(id), group = plan().byId.get(id), raw = item?.deliveryHistory?.createdAt, time = originalDeliveryTime(raw);
    const location = item?.artifact?.type === 'planning-delivery' ? (item.artifact.data as {project_files?: {path:string}[]}).project_files?.[0]?.path.split('/').slice(0, -1).join('/') : undefined;
    return { title: group?.primary === id ? t(group.previous ? '上一次可用导出' : '最近已核对的导出') : undefined,
      createdAt: raw, timeLabel: time === undefined ? undefined : new Intl.DateTimeFormat(uiLocale(), { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', timeZoneName:'short' }).format(time),
      location: group?.ambiguous || time === undefined ? location : undefined };
  };
  let alive = true; onCleanup(() => { alive = false; });
  const changed = () => { if(alive)setInteraction(value => value + 1); };
  return <For each={order()}>{id => {
    onCleanup(() => controls.delete(id));
    const group = () => plan().byId.get(id), heading = () => !!group() && first(group()!.key) === id;
    const history = () => {
      interaction();const value=group();if(!value)return [];
      if(value.ordinary.length)return value.ordinary;
      const focused=(document.activeElement as HTMLElement|null)?.dataset.deliveryHistoryDisclosure===value.key;
      return groupOpen(value.key)||focused ? (previousHistory.get(value.key)??[]).filter(other=>plan().ids.includes(other)) : [];
    };
    return <div data-delivery-group={group()?.key} onFocusIn={changed} onFocusOut={() => queueMicrotask(changed)}>
      <Show when={heading() && !group()!.directoryComplete}><p class="as-muted" role="status">{t('导出目录尚未完整核对，保留各次回执供选择。')}</p></Show>
      <Show when={heading() && group()!.ambiguous && group()!.directoryComplete}><p class="as-muted">{t('创建时间缺失或并列，请结合时间与输出位置选择导出。')}</p></Show>
      <div ref={element => controls.set(id, element)} id={`${prefix}-${encodeURIComponent(id)}`} data-delivery-operation={id} hidden={!visible(id)}>{props.render(id, () => presentation(id))}</div>
      <Show when={heading() && history().length > 0}>
        <button type="button" class="as-text-action" data-delivery-history-disclosure={group()!.key} aria-disabled={!group()!.ordinary.length} aria-expanded={!group()!.ordinary.length||groupOpen(group()!.key)} aria-controls={history().map(other => `${prefix}-${encodeURIComponent(other)}`).join(' ')}
          onClick={() => { if(!group()!.ordinary.length)return;const key = group()!.key; setOpened(previous => { const next = new Set(previous); next.has(key) ? next.delete(key) : next.add(key); return next; }); changed(); }}>
          {t('其他导出（{count}）', {count:history().length})}
        </button>
      </Show>
    </div>;
  }}</For>;
}
