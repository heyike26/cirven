import { api, operationId, type ApiClient } from './api.ts';
import type { SourceRef } from './contracts';
import type { NativeRef } from './native-collaboration';

export type FeedbackBinding = { binding_ref: NativeRef; payload: { file_source_ref: SourceRef; locator: unknown; selected_value?: unknown; [key: string]: unknown }; native_context_status: string };
export type ReworkFeedback = { feedback_ref: NativeRef; payload: { comment: string; binding_ref: NativeRef; [key: string]: unknown }; status: 'DRAFT' | 'SUBMITTED'; native_requested: false };
export class FeedbackReworkApi {
  private client: ApiClient;
  constructor(client: ApiClient = api) { this.client=client; }
  path(projectId: string) { return `${this.client.projectPath(projectId)}/feedback-rework`; }
  bind(projectId: string, source: SourceRef, options: { nativeContext?: Record<string, unknown>; proposalRef?: NativeRef; taskIds?: string[] } = {}, key = operationId()) {
    if (source.project_id !== projectId) throw new Error('反馈文件不属于当前项目');
    return this.client.request<FeedbackBinding>(`${this.path(projectId)}/bindings`, 'POST', { operation_id: key, file_source_ref: source, locator: { kind: 'FILE' }, ...(options.nativeContext ? { native_context_ref: options.nativeContext } : {}), ...(options.proposalRef ? { proposal_ref: options.proposalRef } : {}), ...(options.taskIds ? { task_ids: options.taskIds } : {}) });
  }
  record(projectId: string, binding: NativeRef, comment: string, mode: 'DRAFT' | 'SUBMITTED', key = operationId()) {
    if (!comment.trim()) throw new Error('请先写下反馈');
    return this.client.request<ReworkFeedback>(`${this.path(projectId)}/feedback`, 'POST', { operation_id: key, binding_ref: binding, comment, mode });
  }
}
export const feedbackReworkApi = new FeedbackReworkApi();
