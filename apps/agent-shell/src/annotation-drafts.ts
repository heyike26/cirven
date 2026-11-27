import type {Annotation} from './annotations';
import type {TextSelection} from './text-feedback';
export type GraphFeedbackDraft={comment:string;tool:Annotation['tool'];selection:Annotation['selection']};
export type TextFeedbackDraft={comment:string;selection:TextSelection};
export function textDraftKey(selection:TextSelection){return selection.quote_sha256+':'+JSON.stringify(Object.fromEntries(Object.entries(selection.locator).sort(([a],[b])=>a.localeCompare(b))));}
