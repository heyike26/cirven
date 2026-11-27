import type {ClaimCoverageDraft,ClaimCoverageConfirmation} from './contracts';
import type {ManualReview} from './manual-harness';
import type { FileContent, Harness, HarnessState, MaterialResult, PlanningCandidate, PlanningOperation, PlanningProposal, ProviderProfile, Project, ReportArtifact, SourceRef, TaskBook, TreeEntry, Workspace } from './contracts';

export class ApiError extends Error {
  readonly status:number;readonly code:string;readonly detail?:unknown;
  constructor(status: number, code: string, message: string, detail?: unknown) { super(message);this.status=status;this.code=code;this.detail=detail;this.name = 'ApiError'; }
}
export const operationId = () => crypto.randomUUID();
const segment = (id: string) => encodeURIComponent(id);
export class ApiClient {
  private csrf?: string;
  private boot?: Promise<void>;
  private readonly transport:typeof fetch;private readonly prefix:string;
  constructor(transport?: typeof fetch, prefix = '') {const send=transport??globalThis.fetch.bind(globalThis);this.transport=(input,init)=>send(input,init);this.prefix=prefix;}
  bootstrap(): Promise<void> {
    return this.boot ??= this.transport(`${this.prefix}/api/bootstrap`, { credentials: 'same-origin', headers: { 'x-eeas-bootstrap': '1' } }).then(async response => {
      const data = await this.decode<{ csrf_token: string }>(response);
      if (!data.csrf_token) throw new ApiError(502, 'INVALID_BOOTSTRAP', '本地服务没有返回写入令牌');
      this.csrf = data.csrf_token;
    }).catch(error => { this.boot = undefined; throw error; });
  }
  private async decode<T>(response: Response): Promise<T> {
    const text = await response.text();
    let body: unknown;
    try { body = text ? JSON.parse(text) : null; } catch { throw new ApiError(response.status, 'INVALID_RESPONSE', '本地服务返回了无法解析的响应'); }
    if (!response.ok) {
      const value = body as { error?: { code?: string; message?: string } | string; detail?: unknown; message?: string; code?: string };
      const failure=typeof value?.error==='object'?value.error:undefined;
      const detail=typeof value?.detail==='string'?value.detail:Array.isArray(value?.detail)?value.detail.map(item=>{const field=item as {loc?:unknown[];msg?:string};return `${field.loc?.join('.')??'输入'}：${field.msg??'请检查此项'}`;}).join('；'):undefined;
      throw new ApiError(response.status, failure?.code ?? value?.code ?? 'REQUEST_FAILED', failure?.message ?? (typeof value?.error==='string'?value.error:undefined) ?? value?.message ?? detail ?? `请求失败 (${response.status})`, body);
    }
    return body as T;
  }
  async request<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal, retryUnauthorized = true): Promise<T> {
    const frozenBody=body===undefined?undefined:JSON.stringify(body);
    const negativeReply=(body as {reply?:unknown}|null|undefined)?.reply==='reject';
    await this.bootstrap();
    if(signal?.aborted)throw signal.reason??new DOMException('Aborted','AbortError');
    // Only reconnect the Main metadata channel on an explicit action. The
    // backend still decides whether any fresh editor capture is required.
    if(method==='POST'&&!negativeReply&&(/^\/api\/(?:agent-sessions\/open|conversations\/[^/]+\/messages)(?:$|\?)/.test(path)||/\/local-workspaces\/[^/]+\/edits$/.test(path)||/\/agent-sessions\/[^/]+\/(?:load|messages|guidance|child-messages|(?:permissions|questions)\/[^/]+\/reply)$/.test(path))){
      const host=globalThis as typeof globalThis&{eeasDesktop?:{ensureEditorCaptureChannel?:()=>Promise<unknown>}};
      try{await host.eeasDesktop?.ensureEditorCaptureChannel?.();}catch{/* Backend guard remains authoritative. */}
    }
    if (signal?.aborted) throw signal.reason ?? new DOMException('Aborted', 'AbortError');
    const send=()=>this.transport(`${this.prefix}${path}`, { method, credentials: 'same-origin', signal, headers: { ...(body === undefined ? {} : { 'content-type': 'application/json' }), ...(method === 'GET' ? {} : { 'x-eeas-csrf': this.csrf! }) }, ...(body === undefined ? {} : { body:frozenBody }) });
    let response=await send();
    if(response.status===401&&retryUnauthorized){this.boot=undefined;this.csrf=undefined;await this.bootstrap();if(signal?.aborted)throw signal.reason??new DOMException('Aborted','AbortError');response=await send();}
    return this.decode<T>(response);
  }
  async form<T>(path:string,form:FormData,signal?:AbortSignal):Promise<T>{
    await this.bootstrap();
    const send=()=>this.transport(this.prefix+path,{method:'POST',credentials:'same-origin',signal,headers:{'x-eeas-csrf':this.csrf!},body:form});
    let response=await send();
    if(response.status===401&&!signal?.aborted){this.csrf=undefined;this.boot=undefined;await this.bootstrap();response=await send();}
    return this.decode<T>(response);
  }
  async download(path:string,signal?:AbortSignal){
    await this.bootstrap();
    const send=()=>this.transport(`${this.prefix}${path}`,{credentials:'same-origin',signal});
    let response=await send();
    if(response.status===401){this.boot=undefined;this.csrf=undefined;await this.bootstrap();response=await send();}
    if(!response.ok){await this.decode(response);throw new ApiError(response.status,'DOWNLOAD_FAILED','文件读取失败');}
    return response;
  }
  async bytes(path:string,signal?:AbortSignal){return (await this.download(path,signal)).arrayBuffer();}
  async exactExperienceJSON<T>(path:string,raw:string,signal?:AbortSignal):Promise<T>{
    if(!/^\/api\/projects\/[^/]+\/agent-sessions\/[a-f0-9]{32}\/experience\/(?:candidates|reviews|tool-validations|skill-validations|promotion|rollback|source-policy|pending-configuration|configuration-operations\/recover)$/.test(path)||new TextEncoder().encode(raw).length>262144)throw new Error('EXPERIENCE_REQUEST_CHANGED');
    const value=JSON.parse(raw);if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('EXPERIENCE_REQUEST_CHANGED');
    await this.bootstrap();if(signal?.aborted)throw signal.reason??new DOMException('Aborted','AbortError');
    return this.decode<T>(await this.transport(`${this.prefix}${path}`,{method:'POST',credentials:'same-origin',signal,headers:{'content-type':'application/json','x-eeas-csrf':this.csrf!},body:raw}));
  }
  projectPath(projectId: string) { return `/api/projects/${segment(projectId)}`; }
  workspacePath(projectId: string, workspaceId: string) { return `${this.projectPath(projectId)}/local-workspaces/${segment(workspaceId)}`; }
  async projects(signal?: AbortSignal) { return (await this.request<{ projects: Project[] }>('/api/projects', 'GET', undefined, signal)).projects; }
  createProject(name: string, key?:string) { return this.request<Project>('/api/projects', 'POST', { name, description: '',...key?{operation_id:key}:{} },undefined,false); }
  projectOperation(key:string){return this.request<{operation_id:string;request_sha256:string;project:Project}>(`/api/project-operations/${segment(key)}`);}
  async workspaces(projectId: string, signal?: AbortSignal) { return (await this.request<{ workspaces: Workspace[] }>(`${this.projectPath(projectId)}/local-workspaces`, 'GET', undefined, signal)).workspaces; }
  bindWorkspace(projectId: string, root: string, write: boolean, key: string = operationId()) { return this.request<Workspace>(`${this.projectPath(projectId)}/local-workspaces`, 'POST', { idempotency_key: key, root, allowed_write_paths: write ? ['*'] : [] },undefined,false); }
  workspaceBindingOperation(pid:string,key:string){return this.request<{operation_id:string;workspace:Workspace}>(`${this.projectPath(pid)}/local-workspace-operations/${segment(key)}`);}
  tree(projectId: string, workspaceId: string, path = '', offset = 0, signal?: AbortSignal) { return this.request<{ entries: TreeEntry[]; total: number; next_offset: number | null }>(`${this.workspacePath(projectId, workspaceId)}/tree?${new URLSearchParams({ path, offset: String(offset), limit: '100' })}`, 'GET', undefined, signal); }
  explorer(pid:string,wid:string,path='',offset=0,observationId?:string,signal?:AbortSignal){if(offset>0&&!observationId)throw new Error('加载更多需要当前目录的观察版本');return this.request<import('./explorer').ExplorerPage>(`${this.workspacePath(pid,wid)}/explorer?${new URLSearchParams({path,offset:String(offset),limit:'100',...observationId?{observation_id:observationId}:{}})}`,'GET',undefined,signal);}
  file(projectId: string, workspaceId: string, path: string, signal?: AbortSignal) { return this.request<FileContent>(`${this.workspacePath(projectId, workspaceId)}/file?${new URLSearchParams({ path })}`, 'GET', undefined, signal); }
  edit(projectId: string, workspaceId: string, file: FileContent, content: string, key = operationId()) { return this.request<{ source_ref: SourceRef; outcome: string; current: boolean; replayed: boolean }>(`${this.workspacePath(projectId, workspaceId)}/edits`, 'POST', { operation_id: key, path: file.source_ref.path, expected_revision: file.source_ref.revision, expected_sha256: file.source_ref.sha256, content }); }
  materials(projectId: string, workspaceId: string, paths: string[], key: string = operationId(),expectedSources?:SourceRef[]) { return this.request<MaterialResult>(`${this.workspacePath(projectId, workspaceId)}/materials`, 'POST', { operation_id: key, paths,...expectedSources?{expected_sources:expectedSources}:{} }); }
  async sourceCheck(pid:string,wid:string,sources:SourceRef[],signal?:AbortSignal){return(await this.request<{sources:import('./source-watch').SourceCheck[]}>(`${this.workspacePath(pid,wid)}/source-check`,'POST',{sources},signal)).sources;}
  async taskbooks(projectId: string, signal?: AbortSignal) { return (await this.request<{ taskbooks: TaskBook[] }>(`${this.projectPath(projectId)}/taskbooks`, 'GET', undefined, signal)).taskbooks; }
  saveBook(book: TaskBook, sections: TaskBook['payload']['sections'], key = operationId()) { const p = book.payload; return this.request<TaskBook>(`${this.projectPath(book.project_id)}/taskbooks/${segment(book.id)}`, 'PUT', { idempotency_key: key, expected_revision: book.revision, intake_ref: p.intake_ref, title: p.title, sections, template_ref: p.template_ref, template_mapping: p.template_mapping, ...(p.provider_profile_ref ? { provider_profile_ref: p.provider_profile_ref } : {}) }); }
  async harnesses(projectId: string, signal?: AbortSignal) { return (await this.request<{ harnesses: Harness[] }>(`${this.projectPath(projectId)}/harnesses`, 'GET', undefined, signal)).harnesses; }
  harnessState(projectId: string, harnessId: string, revision?:number, signal?:AbortSignal) { return this.request<HarnessState>(`${this.projectPath(projectId)}/harnesses/${segment(harnessId)}/state${revision===undefined?'':'?revision='+revision}`, 'GET', undefined, signal); }
  startNode(harness: Harness, nodeId: string, reason: string, key = operationId()) { return this.request(`${this.projectPath(harness.project_id)}/harnesses/${segment(harness.id)}/nodes/${segment(nodeId)}/runs`, 'POST', { idempotency_key: key, expected_revision: harness.revision, harness_sha256: harness.sha256, intent: 'USER_START', user_ready: true, scope_reason: reason }); }
  async reports(projectId: string, signal?: AbortSignal) { return (await this.request<{ artifacts: ReportArtifact[] }>(`${this.projectPath(projectId)}/report-artifacts`, 'GET', undefined, signal)).artifacts; }
  proposal(projectId:string,id:string,signal?:AbortSignal){return this.request<PlanningProposal>(`${this.projectPath(projectId)}/planning/proposals/${segment(id)}`,'GET',undefined,signal);}
  async proposals(projectId:string,signal?:AbortSignal){return(await this.request<{proposals:PlanningProposal[]}>(`${this.projectPath(projectId)}/planning/proposals`,'GET',undefined,signal)).proposals;}
  createProposal(projectId:string,prompt:string,materials:{document_id:string;revision:number;sha256:string}[],workspace:Workspace,options:{profile?:ProviderProfile;mode?:'opencode'|'source_outline'}={},key=operationId()){return this.request<PlanningProposal|PlanningOperation>(`${this.projectPath(projectId)}/planning/proposals`,'POST',{operation_id:key,prompt,materials,workspace_ref:{id:workspace.id,revision:workspace.revision,binding_sha256:workspace.binding_sha256},mode:options.mode??'opencode',...options.profile?{provider_profile_ref:options.profile.profile_ref}:{}});}
  planningOperation(projectId:string,key:string,signal?:AbortSignal){return this.request<PlanningOperation|PlanningProposal>(`${this.projectPath(projectId)}/planning/operations/${segment(key)}`,'GET',undefined,signal);}
  cancelPlanning(projectId:string,key:string){return this.request(`${this.projectPath(projectId)}/planning/operations/${segment(key)}/cancel`,'POST');}
  async providers(){return(await this.request<{providers:ProviderProfile[]}>('/api/planning/providers')).providers;}
  editProposal(proposal:PlanningProposal,candidate:PlanningCandidate,reason:string,key:string=operationId(),manualReview?:ManualReview,requirementCoverage?:ClaimCoverageDraft){return this.request<PlanningProposal>(`${this.projectPath(proposal.project_id)}/planning/proposals/${segment(proposal.id)}`,'PUT',{operation_id:key,expected_revision:proposal.revision,candidate,change_reason:reason,...manualReview?{manual_review:manualReview}:{},...requirementCoverage?{requirement_coverage:requirementCoverage}:{}},undefined,!manualReview&&!requirementCoverage);}
  answerProposal(proposal:PlanningProposal,questionId:string,text:string,key=operationId()){return this.request<PlanningProposal>(`${this.projectPath(proposal.project_id)}/planning/proposals/${segment(proposal.id)}/answers`,'POST',{operation_id:key,expected_revision:proposal.revision,question_id:questionId,text});}
  confirmProposal(proposal:PlanningProposal,resolutions:unknown[],key:string=operationId(),coverage?:ClaimCoverageConfirmation){return this.request<PlanningProposal>(`${this.projectPath(proposal.project_id)}/planning/proposals/${segment(proposal.id)}/confirm`,'POST',{operation_id:key,expected_revision:proposal.revision,proposal_sha256:proposal.sha256,user_confirmed:true,resolutions,...(coverage??{})},undefined,!coverage);}
  reportDownload(projectId: string, artifactId: string) { return `${this.prefix}${this.projectPath(projectId)}/report-artifacts/${segment(artifactId)}/download`; }
}
export const api = new ApiClient();
