import type { ArtifactEnvelope } from './contracts';
import { nativeReference } from './native-collaboration.ts';
import { deliveryComplete, type DeliveryOperation } from './planning-delivery.ts';
import { deliveryReview } from './delivery-review.ts';
import { chatDeliveryFiles } from './chat-artifacts.ts';
import type { DeliveryDirectoryView, DeliveryFeedItem, DeliveryTarget } from './delivery-recovery.ts';

export type HistoryFeedItem = { id: string; status: string; artifact?: ArtifactEnvelope; deliveryTarget?: DeliveryTarget; deliveryHistory?: DeliveryFeedItem['deliveryHistory']; deliveryInspection?: DeliveryFeedItem['deliveryInspection'] };
export type DeliveryHistoryGroup = { key: string; ids: string[]; ordinary: string[]; primary?: string; previous: boolean; directoryComplete: boolean; ambiguous: boolean };
export type DeliveryHistoryPlan = { ids: string[]; groups: DeliveryHistoryGroup[]; byId: Map<string, DeliveryHistoryGroup> };
/** Directory completeness belongs to the currently inspected source, not to an operation's completion order. */
export const deliveryDirectoryKey = (target: DeliveryTarget) => JSON.stringify([target.projectId, target.kind, target.basis.id]);
export function originalDeliveryTime(value: string | undefined): number | undefined {
  if (!value || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return undefined;
  const time = Date.parse(value); return Number.isFinite(time) ? time : undefined;
}
export function deliveryHistoryScope(item: HistoryFeedItem): string | undefined {
  const target = item.deliveryTarget;
  if (!target) return undefined;
  const value = item.artifact?.type === 'planning-delivery' ? item.artifact.data as DeliveryOperation : undefined;
  const ref = value ? target.kind === 'STAGE' ? value.session_ref : value.proposal_ref : item.deliveryHistory?.basis;
  // A missing/unbound local key remains visible on its own; it cannot claim a version group.
  if (!nativeReference(ref) || ref.id !== target.basis.id) return undefined;
  return JSON.stringify([target.projectId, target.kind, ref.id, ref.revision, ref.sha256]);
}
export function checkedDeliveryAvailable(item: HistoryFeedItem): boolean {
  if (item.deliveryInspection || item.artifact?.type !== 'planning-delivery') return false;
  const value = item.artifact.data as DeliveryOperation;
  return deliveryComplete(value) && item.status === value.status && value.basis_current === true && value.current_review_status === 'CURRENT' && chatDeliveryFiles(value).all.length > 0 && chatDeliveryFiles(value).missingSources === 0 && new Set(value.file_checks.map(check => check.path)).size === value.file_checks.length && deliveryReview(value).notices.length === 0;
}
/** A view projection only. Every operation and its original file authority remain intact. */
export function deliveryHistoryPlan(items: HistoryFeedItem[], directories: Record<string, DeliveryDirectoryView>): DeliveryHistoryPlan {
  const grouped = new Map<string, HistoryFeedItem[]>();
  for (const item of items) { const key = deliveryHistoryScope(item); if (!key) continue; const list = grouped.get(key) ?? []; list.push(item); grouped.set(key, list); }
  const groups: DeliveryHistoryGroup[] = [], byId = new Map<string, DeliveryHistoryGroup>();
  const compare = (a: HistoryFeedItem, b: HistoryFeedItem) => {
    const at = originalDeliveryTime(a.deliveryHistory?.createdAt), bt = originalDeliveryTime(b.deliveryHistory?.createdAt);
    if (at === undefined && bt !== undefined) return 1;
    if (at !== undefined && bt === undefined) return -1;
    return at !== undefined && bt !== undefined && at !== bt ? bt - at : a.id.localeCompare(b.id);
  };
  for (const [key, records] of [...grouped].sort(([a], [b]) => a.localeCompare(b))) {
    const ordered = [...records].sort(compare), usable = ordered.filter(checkedDeliveryAvailable), issues = ordered.filter(item => !checkedDeliveryAvailable(item));
    const directoryComplete = records.every(item => item.deliveryTarget && directories[deliveryDirectoryKey(item.deliveryTarget)]?.state === 'COMPLETE' && directories[deliveryDirectoryKey(item.deliveryTarget)]!.operationIds.includes(item.artifact?.artifactId ?? item.deliveryInspection?.operationId ?? ''));
    const reliable = records.every(item => originalDeliveryTime(item.deliveryHistory?.createdAt) !== undefined);
    const latest = usable[0], time = originalDeliveryTime(latest?.deliveryHistory?.createdAt);
    const unique = latest && time !== undefined && records.filter(item => originalDeliveryTime(item.deliveryHistory?.createdAt) === time).length === 1;
    const primary = directoryComplete && reliable && unique ? latest.id : undefined;
    const ordinary = primary ? usable.filter(item => item.id !== primary).map(item => item.id) : [];
    const ids = primary ? [primary, ...issues.map(item => item.id), ...ordinary] : ordered.map(item => item.id);
    const previous = !!primary && issues.some(item => originalDeliveryTime(item.deliveryHistory?.createdAt)! > time!);
    const group = { key, ids, ordinary, primary, previous, directoryComplete, ambiguous: !primary && usable.length > 1 };
    groups.push(group); for (const id of ids) byId.set(id, group);
  }
  // A mixed result stream is still a timeline. Emit each source group at its last original
  // member, while ordinary results keep their place and order relative to those anchors.
  const anchors = new Map<string, number>();
  items.forEach((item, index) => { const group = byId.get(item.id); if(group)anchors.set(group.key,index); });
  const ids = items.flatMap((item,index) => { const group = byId.get(item.id); return !group ? [item.id] : anchors.get(group.key) === index ? group.ids : []; });
  return { ids, groups, byId };
}
/** Do not move existing DOM under the user's focus, a pending action, or an expanded history. */
export function stableDeliveryOrder(previous: string[], desired: string[], interacting: boolean): string[] {
  if (!interacting) return desired;
  const present = new Set(desired), kept = previous.filter(id => present.has(id)), known = new Set(kept);
  return [...kept, ...desired.filter(id => !known.has(id))];
}
