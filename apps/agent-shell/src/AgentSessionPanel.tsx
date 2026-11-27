import ResourceAllowanceEditor from './ResourceAllowanceEditor';
import OriginalOperationRecovery from './OriginalOperationRecovery';
import ConnectionConfiguration from './ConnectionConfiguration';
import type {ConfigurationOwner} from './conversation-configuration';
import type {OwnerCatalogue} from './original-owner-selection';
import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText} from './ui-language';
import { createEffect, createSignal, For, Show, untrack, onCleanup } from 'solid-js';
import { agentSessionsApi, originalSessionReady, originalSessionCanStop, AgentSessionOutcomeError, type OriginalModelBudget, type OriginalWorkResourceBudget, originalResourceOperation, originalResourceNeedsReview, originalResourceBytes, type AgentSession, type AgentSessionOperation, type OriginalAgent, type SessionOpenOptions } from './agent-sessions';
import { sameNativeReference, type NativeRef, type NativeConversationRef } from './native-collaboration';
import type { Workspace } from './contracts';
import DynamicWorkflowPanel from './DynamicWorkflowPanel';
import {mcpPermissionVisibility,type MCPToolSelectionDraft} from './native-tools';

export default function AgentSessionPanel(props: { active: boolean; logicalProjectId?:string|null; onOwnerPrepare?:()=>Promise<OwnerCatalogue>;onOwnerChoose?:(source:ConfigurationOwner,current:()=>boolean)=>Promise<void>; onConfigure?:(options:SessionOpenOptions,current:()=>boolean)=>Promise<AgentSession|undefined>; configurationPending?:boolean; agent?:OriginalAgent; onPending?:()=>void; conversationRef?:NativeConversationRef; conversationSources?:NativeRef[]; projectId?: string; workspace?: Workspace; provider?: NativeRef; selected?: AgentSession; toolSelection?:MCPToolSelectionDraft; onToolSelectionUsed?:()=>void; onSelect: (value?: AgentSession) => void; onUse: (value: AgentSession, agent: OriginalAgent) => void|Promise<unknown> }) {
  const [ownerCatalogue,setOwnerCatalogue]=createSignal<OwnerCatalogue>();
  const [sessions, setSessions] = createSignal<AgentSession[]>([]), [pending, setPending] = createSignal(''), [error, setError] = createSignal<UIErrorCopy>('');
  const [budget,setBudget]=createSignal<OriginalModelBudget>(),[resources,setResources]=createSignal<OriginalWorkResourceBudget>();
  let budgetScope='';createEffect(()=>{const s=props.selected,key=JSON.stringify([s?.project_id,s?.session_ref.id,s?.session_ref.revision,s?.session_ref.sha256,s?.generation,s?originalResourceOperation(s):undefined]);if(key!==budgetScope){budgetScope=key;setBudget(undefined);setResources(undefined);}});
  const agent=()=>props.agent??'plan';
  const [useWorkspace, setUseWorkspace] = createSignal(false), [processStart, setProcessStart] = createSignal(false), [remoteConnect, setRemoteConnect] = createSignal(false);
  const [workspaceWrite, setWorkspaceWrite] = createSignal(false),[ttlSeconds,setTTLSeconds]=createSignal(600),[maxCalls,setMaxCalls]=createSignal(24),[maxOutputTokens,setMaxOutputTokens]=createSignal(4096);
  let writeScope = '';
  createEffect(() => { const w=props.workspace,usingWorkspace=useWorkspace(),key=JSON.stringify([props.projectId,w?.id,w?.revision,w?.binding_sha256]); if(key!==writeScope||!usingWorkspace)setWorkspaceWrite(false);writeScope=key; });
  const [commands, setCommands] = createSignal(''), [tools, setTools] = createSignal(''), [last, setLast] = createSignal<AgentSessionOperation>();
  const [createOpen,setCreateOpen]=createSignal(false),[selectedTools,setSelectedTools]=createSignal<MCPToolSelectionDraft>(),[advancedOpen,setAdvancedOpen]=createSignal(false);
  createEffect(()=>{const selection=props.toolSelection;if(!selection)return;setSelectedTools(selection);setTools(selection.items.map(item=>item.tool_id).join('\n'));setCreateOpen(true);});
  const mcpPermissions=()=>mcpPermissionVisibility(selectedTools(),literalArray(tools()));
  const mcpScope=()=>JSON.stringify([props.projectId,props.conversationRef??null,selectedTools()??null,literalArray(tools())]);
  let permissionScope='';
  createEffect(()=>{const scope=mcpScope();if(scope!==permissionScope){permissionScope=scope;setProcessStart(false);setRemoteConnect(false);}});
  const [workflow,setWorkflow]=createSignal<NativeRef>(),[workflowProfiles,setWorkflowProfiles]=createSignal<NativeRef[]>([]),[workflowEnabled,setWorkflowEnabled]=createSignal(false);
  let workflowProject=props.projectId;
  createEffect(()=>{const pid=props.projectId;if(pid!==workflowProject){workflowProject=pid;setWorkflow(undefined);setWorkflowProfiles([]);setWorkflowEnabled(false);}});
  let generation = 0, disposed = false; onCleanup(()=>{disposed=true;generation++;});
  const current = () => props.selected;
  // A saved owner identifier is diagnostic metadata, never a directory path.
  function currentWorkspacePath(value:AgentSession){
    const ref=value.workspace_ref as Pick<Workspace,'id'|'revision'|'binding_sha256'>|null|undefined,w=props.workspace;
    return ref&&w&&value.project_id===w.project_id&&ref.id===w.id&&ref.revision===w.revision&&ref.binding_sha256===w.binding_sha256?w.root:undefined;
  }
  const advancedConfigured=()=>!!commands().trim()||!selectedTools()&&!!tools().trim()||workflowEnabled()||ttlSeconds()!==600||maxCalls()!==24||maxOutputTokens()!==4096;
  async function run(label: string, task: () => Promise<void>) { if (pending()) return; setPending(label); setError(''); try { await task(); } catch (error) { if(!disposed){setError(authoredErrorCopy(error)); if (error instanceof AgentSessionOutcomeError) setLast(error.receipt);} } finally { if(!disposed)setPending(''); } }
  async function directory() {
    if(props.conversationRef){const ref={...props.conversationRef},owner=++generation;if(!props.onOwnerPrepare)throw Error('ORIGINAL_OWNER_SELECTION_REQUIRED');const catalogue=await props.onOwnerPrepare();if(!disposed&&props.active&&owner===generation&&JSON.stringify(ref)===JSON.stringify(props.conversationRef))setOwnerCatalogue(catalogue);return;}
    setOwnerCatalogue(undefined);
    const projects=()=>[...new Set([props.projectId,current()?.project_id].filter((v):v is string=>!!v))];
    const pids=projects(),scope=JSON.stringify([pids,props.conversationRef]);
    if (!pids.length) { setSessions([]); return; }
    const owner = ++generation, values = (await Promise.all(pids.map(pid=>agentSessionsApi.list(pid)))).flat();
    if (!disposed && props.active && owner === generation && scope===JSON.stringify([projects(),props.conversationRef])) setSessions(values);
  }
  createEffect(() => { const active = props.active, pid = props.projectId; props.conversationRef; if (!active) generation++; if (active) untrack(() => { if (!props.conversationRef&&current() && pid && current()!.project_id !== pid) props.onSelect(undefined); void run('读取原会话目录', directory); }); });
  function literalArray(text: string) { return text.split(/\r?\n/).filter(item => !!item.trim()); }
  async function open() {
    if (!props.provider) throw new Error('请先在模型连接中选择实际配置版本');
    if (useWorkspace() && !props.workspace) throw new Error('所选工作目录已变化，请重新确认目录或使用内部只读目录');
    if(workflowEnabled()&&!workflow())throw new Error('请先选择当前项目的任务链，已有草稿保留');
    if(props.conversationRef&&(!props.onConfigure||props.configurationPending))throw Error('CONNECTION_CHECK_REQUIRED');
    const owner=++generation,selection=selectedTools(),pid=props.projectId,scope=mcpScope(),permissions=mcpPermissions(), options:SessionOpenOptions={ conversationRef:props.conversationRef, provider: props.provider, projectId: pid, workspace: useWorkspace() ? props.workspace : undefined, workspaceWrite: workspaceWrite(), processStart: scope===permissionScope&&permissions.local&&processStart(), remoteMCPConnect: scope===permissionScope&&permissions.remote&&remoteConnect(), commandAllowlist: literalArray(commands()), mcpToolAllowlist: literalArray(tools()),mcpToolSelection:selection,workflowRef:workflowEnabled()?workflow():undefined,workflowProviderRefs:workflowProfiles(),ttlSeconds:ttlSeconds(),maxCalls:maxCalls(),maxOutputTokens:maxOutputTokens() };
    const stillCurrent=()=>!disposed&&owner===generation&&props.active&&props.projectId===pid&&selection===selectedTools()&&scope===mcpScope();
    const value=props.conversationRef?await props.onConfigure!(options,stillCurrent):await agentSessionsApi.open(options);if(!value)return;
    if(disposed||owner!==generation||!props.active||props.projectId!==pid)return;
    if(selection!==selectedTools()||scope!==mcpScope()){await directory();return;}
    if(selection||props.conversationRef)await props.onUse(value,agent());else props.onSelect(value);
    setSelectedTools(undefined);props.onToolSelectionUsed?.();await directory();
  }
  function sameSelection(value:AgentSession){return !disposed&&sameNativeReference(current()?.session_ref,value.session_ref)&&current()?.project_id===value.project_id;}
  async function select(value: AgentSession) { const pid=props.projectId,owner=++generation,actual=await agentSessionsApi.get(value.project_id,value.session_ref);if(disposed||owner!==generation||props.projectId!==pid||!props.active)return;props.onSelect(actual); }
  async function reconnect() { const value = current(); if (!value) return; const result = await agentSessionsApi.load(value); if(!sameSelection(value))return;setLast(result.operation); props.onSelect(result.session); }
  async function refresh() { const value = current(); if (!value) return;const actual=await agentSessionsApi.get(value.project_id,value.session_ref);if(sameSelection(value))props.onSelect(actual); }
  const loaded = () => current()?.capability_load;
  return <section class="as-owner-manager" aria-label={uiText("Agent 连接与权限")}>
    <div class="as-capability-heading"><strong>{uiText("已有连接")}</strong><button type="button" disabled={!!pending()} onClick={() => void run('读取原会话目录', directory)}>{uiText("刷新")}</button></div><Show when={!props.projectId&&!current()}><p role="status">{uiText("请先关联工作目录，或在下方创建会话。")}</p></Show>
    
    <Show when={props.conversationRef&&ownerCatalogue()?.selection_allowed}><div class="as-owner-directory"><For each={ownerCatalogue()?.candidates}>{choice=><button type="button" disabled={!!pending()||choice.unavailable||props.configurationPending} aria-pressed={current()?.project_id===choice.original_source.project_id&&sameNativeReference(current()?.session_ref,choice.original_source.session_ref)} onClick={()=>void run('选择已有连接',async()=>{const scope=JSON.stringify(props.conversationRef),owner=++generation;await props.onOwnerChoose?.(choice.original_source,()=>!disposed&&props.active&&owner===generation&&scope===JSON.stringify(props.conversationRef));await directory();})}><span class="as-session-title">{choice.title??choice.original_source.session_ref.id}</span><small>{choice.unavailable?uiText('连接暂不能核对'):uiText('沿用此连接')}</small></button>}</For></div></Show>
    <Show when={!props.conversationRef}>    <div class="as-owner-directory"><For each={props.conversationRef?sessions().filter(value=>props.conversationSources?.some(ref=>sameNativeReference(ref,value.session_ref))):sessions()}>{value => <button type="button" disabled={!!pending()} aria-pressed={current()?.session_ref.id === value.session_ref.id} onClick={() => void run('读取原会话', () => select(value))} title={value.original_session_id ?? value.session_ref.id}><span class="as-session-title">{value.original_session_id ?? value.session_ref.id.slice(0, 12)}</span><small>{originalSessionReady(value)?uiText("会话已就绪"):value.connected_owner?uiText("需要核对状态"):uiText("尚未连接")}</small></button>}</For></div></Show>
    <Show when={!!props.conversationRef}><ConnectionConfiguration active={props.active} reference={props.conversationRef!} projectId={props.logicalProjectId??null} provider={props.provider} selected={props.selected} pending={props.configurationPending} toolSelection={props.toolSelection} onToolSelectionUsed={props.onToolSelectionUsed} onConfigure={(options,current)=>{if(!props.onConfigure)throw Error('CONNECTION_CHECK_REQUIRED');return props.onConfigure(options,current);}} onApplied={value=>props.onUse(value,agent())}/></Show>
    <Show when={!props.conversationRef}><details class="as-capability-form" open={createOpen()} onToggle={event=>setCreateOpen(event.currentTarget.open)}><summary>{uiText(props.conversationRef?"当前会话执行范围":"新建 Agent 连接")}</summary><div class="as-owner-create">
      <label class="as-check"><input type="checkbox" checked={useWorkspace()} disabled={!props.workspace || !!pending()} onChange={event => setUseWorkspace(event.currentTarget.checked)}/><span>{uiText("使用已关联目录；未选则由服务建立内部只读目录")}</span></label>
      <label class="as-check"><input type="checkbox" checked={workspaceWrite()} disabled={!useWorkspace()||!props.workspace?.allowed_write_paths.length||!!pending()} onChange={event=>setWorkspaceWrite(event.currentTarget.checked)}/><span>{uiText("允许修改已授权文件")}</span></label>
      <Show when={mcpPermissions().local}><label class="as-check"><input type="checkbox" checked={processStart()} disabled={!!pending()} onChange={event => setProcessStart(event.currentTarget.checked)}/><span>{uiText("允许启动所选的本地 MCP 进程")}</span></label></Show>
      <Show when={mcpPermissions().remote}><label class="as-check"><input type="checkbox" checked={remoteConnect()} disabled={!!pending()} onChange={event => setRemoteConnect(event.currentTarget.checked)}/><span>{uiText("允许连接所选的远端 MCP")}</span></label></Show>
      <Show when={selectedTools()}><p class="as-capability-result">{uiText("已选择 {count} 个工具，用于新连接",{count:selectedTools()!.items.length})}</p></Show>
      <details class="as-owner-advanced" open={advancedOpen()} onToggle={event=>setAdvancedOpen(event.currentTarget.open)}>
        <summary>{uiText("高级执行配置")}<Show when={advancedConfigured()}><span class="as-owner-configured">{uiText("已配置")}</span></Show></summary>
        <div class="as-owner-advanced-body">
      <label>{uiText("明确命令名单（每行一条完整命令）")}<textarea aria-label={uiText("原会话明确命令名单")} value={commands()} onInput={event => setCommands(event.currentTarget.value)}/></label>
      <label>{uiText("MCP 工具名单（每行一个原工具名）")}<textarea aria-label={uiText("原会话 MCP 工具名单")} value={tools()} onInput={event => {setTools(event.currentTarget.value);setSelectedTools(undefined);props.onToolSelectionUsed?.();}}/></label>
      <div class="as-owner-execution-limits"><label>{uiText("调用上限")}<input type="number" min="1" max="128" value={maxCalls()} onInput={event=>setMaxCalls(Number(event.currentTarget.value))}/></label><label>{uiText("单次输出上限")}<input type="number" min="64" max="16384" value={maxOutputTokens()} onInput={event=>setMaxOutputTokens(Number(event.currentTarget.value))}/></label><label>{uiText("连接时限（秒）")}<input type="number" min="30" max="3600" value={ttlSeconds()} onInput={event=>setTTLSeconds(Number(event.currentTarget.value))}/></label></div>
      <label class="as-check"><input type="checkbox" checked={workflowEnabled()} disabled={!workflow()||!!pending()} onChange={event=>setWorkflowEnabled(event.currentTarget.checked)}/><span>{uiText("启用 Dynamic Workflow")}{workflow()?uiText(" · 已选任务链"):uiText(" · 请先在下方选择任务链")}</span></label>
        </div>
      </details>
      <button type="button" disabled={!props.provider || !!pending() || props.configurationPending} onClick={() => void run('创建原会话', open)}>{uiText(props.conversationRef?"确认并应用执行范围":"创建并连接")}</button>
    </div></details></Show>
    <Show when={current()} keyed>{value => <details class="as-session-owner-controls" open><summary>{uiText("连接与执行")}</summary>
      <p class="as-capability-result">{originalSessionReady(value)?uiText("会话已就绪"):!value.configuration_current?uiText("模型或工具配置已变化，请核对后继续。"):!value.connected_owner?uiText("会话尚未连接"):uiText("会话状态需要核对")}</p>
      <small class="as-muted as-owner-workspace">{value.workspace_kind==='APP_INTERNAL_READONLY'?uiText("应用只读目录"):currentWorkspacePath(value)?<>{uiText("会话工作目录")} · {currentWorkspacePath(value)}</>:uiText(props.conversationRef?"工作目录见当前会话执行范围":"工作目录需重新核对")}</small>
      <details class="as-owner-identity"><summary>{uiText("连接详情")}</summary><dl><dt>{uiText("连接标识")}</dt><dd>{value.original_session_id??value.session_ref.id}</dd></dl></details>
      <div class="as-capability-actions"><button type="button" disabled={!!pending()} onClick={() => void run('核对原会话', refresh)}>{uiText("核对状态")}</button><button type="button" disabled={!!pending() || props.configurationPending || value.connected_owner || !value.configuration_current} onClick={() => void run('重新连接原会话', reconnect)}>{uiText("重新连接")}</button><button type="button" disabled={!!pending() || !originalSessionCanStop(value)} onClick={() => void run('中断原执行', async () => { setLast(await agentSessionsApi.interrupt(value)); await refresh(); })}>{uiText("中断原执行")}</button><button type="button" disabled={!!pending() || !value.connected_owner} onClick={()=>void run('断开本会话进程',async()=>{const result=await agentSessionsApi.disconnect(value);if(sameSelection(value)){setLast(result.operation);props.onSelect(result.session);}})}>{uiText("断开本会话进程")}</button></div>
      <div class="as-owner-mode"><span>{uiText("后续消息方式")} · {agent()==='workflow'?'Workflow':agent()==='build'?'Build':'Plan'}</span><button type="button" disabled={!!pending() || !originalSessionReady(value)} onClick={() => props.onUse(value, agent())}>{uiText("在当前输入框继续")}</button></div>
      <Show when={value.pending_questions.length||value.pending_permissions.length}><div class="as-owner-request-summary"><p>{uiText("原 Agent 等待回应")}</p><button type="button" onClick={props.onPending}>{uiText("返回对话")}</button></div></Show>
      <details class="as-resource-budget"><summary>{uiText('额度与资源')}</summary>
        <p>{uiText('原工作与后续操作共用累计预算；连接调用上限仍分别生效。')}</p>
        <button type="button" disabled={!!pending()} onClick={()=>void run('核对资源',async()=>{
          const operationId=originalResourceOperation(value);setBudget(undefined);setResources(undefined);
          const [model,work]=await Promise.all([agentSessionsApi.modelBudget(value),agentSessionsApi.resourceBudget(value,operationId)]);
          const selected=current();if(sameSelection(value)&&selected?.generation===value.generation&&originalResourceOperation(selected)===operationId){setBudget(model);setResources(work);}
        })}>{uiText('核对资源')}</button>
        <Show when={budget()}>{b=><><p>{b().status==='AVAILABLE'?`${uiText('连接调用预留')} ${b().reservations} / ${b().max_calls}`:uiText('此原会话的计量记录暂不可核实，原历史保留。')}</p><small>{uiText('预留次数包含尚未确认转发的尝试，不表示实际用量或费用。')}</small><Show when={b().counter==='SUBSCRIPTION_HTTP_RESERVATIONS'}><p>{uiText('订阅连接使用原生 HTTP 路径；输出 Token 上限尚未核实。')}</p></Show></>}</Show>
        <Show when={resources()}>{b=><>
          <Show when={b().status==='NO_WORK_SELECTED'}><p>{uiText('此连接当前没有可定位的原工作。')}</p></Show>
          <Show when={b().status==='NOT_ALLOCATED'}><p>{uiText('这项工作尚未分配累计预算；历史消耗未知。')}</p></Show>
          <Show when={b().status==='UNAVAILABLE'}><p role="status">{uiText('原工作资源记录暂不可核实，原内容保留。')}</p></Show>
          <Show when={b().usage}>{u=><dl class="as-work-resource-counts">
            <dt>{uiText('资料读取')}</dt><dd>{originalResourceBytes(u().used.source_bytes)} / {originalResourceBytes(u().limits.source_bytes)}</dd>
            <dt>{uiText('读取次数')}</dt><dd>{u().used.source_reads} / {u().limits.source_reads}</dd>
            <dt>{uiText('工作模型预留')}</dt><dd>{u().used.model_attempts} / {u().limits.model_attempts}</dd>
            <dt>{uiText('内容保留量')}</dt><dd>{originalResourceBytes(u().used.retained_output_bytes)} / {originalResourceBytes(u().limits.retained_output_bytes)}</dd>
            <Show when={u().pending_reads>0}><dt>{uiText('读取待确认')}</dt><dd>{u().pending_reads} · {originalResourceBytes(u().pending_read_bytes)}</dd></Show>
          </dl>}</Show>
          <Show when={b().retained_event_attribution?.pending_bytes}><p>{uiText('内容归属待确认')} · {originalResourceBytes(b().retained_event_attribution!.pending_bytes)}</p></Show>
          <Show when={originalResourceNeedsReview(b())}><p role="status">{uiText('部分计量待确认或资源已达阈值；原内容保留。')}</p></Show>
          <small>{uiText('累计量从预算分配后计算；保留量是应用记录字节，不是磁盘占用或 Token。')}</small>
          <small>{uiText('超出保留阈值时先保留已收到的内容，再阻止下一次生成。')}</small>
          <small>{uiText('上次核对')} · {new Date(b().observed_at).toLocaleString()}</small>
          <Show when={b().status==='AVAILABLE'&&b().usage?.adjustment_capacity===32}><ResourceAllowanceEditor session={value} receipt={b()} onRefreshed={result=>{const selected=current();if(sameSelection(value)&&selected?.generation===value.generation&&originalResourceOperation(selected)===result.operation_id)setResources(result);}}/></Show>
        </>}</Show>
      </details>
      <OriginalOperationRecovery session={value} active={props.active}/>
      <details><summary>{uiText("原消息观察 · ")}{value.messages.length}</summary><For each={value.messages}>{item=><article class="as-history-message"><small>{item.info.role}</small><For each={item.parts}>{part=><Show when={part.type==='text'} fallback={<span class="as-session-part">{part.type==='file'?(typeof part.filename==='string'?part.filename:uiText("文件附件")):part.type==='tool'?uiText("工具执行记录"):part.type==='reasoning'?uiText("思考过程"):uiText("会话附件")}</span>}><p>{part.text}</p></Show>}</For></article>}</For></details><details><summary>{uiText("实际待办 · ")}{value.todo.length}</summary><ul class="as-session-todos"><For each={value.todo}>{item=><li><span>{typeof item.content==='string'?item.content:typeof item.title==='string'?item.title:uiText("任务内容尚未读取")}</span><small>{item.status==='completed'?uiText("已完成"):item.status==='in_progress'?uiText("执行中"):item.status==='pending'?uiText("等待处理"):item.status==='cancelled'?uiText("已停止"):uiText("待核对")}</small></li>}</For></ul></details>

      <details><summary>{uiText("本会话 Skill / MCP 加载与调用")}</summary><For each={loaded()?.skills ?? []}>{item => <p>{item.id} · {item.loaded_in_original_session ? uiText("原会话已加载") : uiText("尚未加载")} · {item.invoked ? uiText("有真实调用") : uiText("尚未调用")} · {item.qualified ? uiText("资格已核实") : uiText("资格未核实")}</p>}</For><For each={Object.entries(loaded()?.mcp ?? {})}>{([id, item]) => <p>{id} · {item.load_status} · {item.invoked ? uiText("有真实调用") : uiText("尚未调用")} · {item.qualified ? uiText("资格已核实") : uiText("资格未核实")}</p>}</For><Show when={!loaded()}><p>{uiText("尚未读取会话能力状态。")}</p></Show></details>
      <Show when={value.native_messages_error||value.observation_is_current===false}><p class="as-inline-warning">{uiText("原消息读取尚未完成；旧观察保留，不能当作最新消息。")}</p></Show>
      <Show when={value.observation_at}><small>{uiText("上次核对时间")} · {value.observation_at}</small></Show>
    </details>}</Show>
    <Show when={!props.conversationRef}><DynamicWorkflowPanel projectId={props.projectId??current()?.project_id} session={current()} active={props.active} onWorkflowSelected={(reference,routes)=>{setWorkflow(reference);setWorkflowProfiles(routes??[]);if(!reference)setWorkflowEnabled(false);}}/></Show>
    <Show when={pending()}><p class="as-capability-pending" role="status">{uiText(pending())}…</p></Show><Show when={error()}><p class="as-inline-error" role="alert">{authoredText(error(),uiText)}</p></Show><Show when={last()}><p class="as-capability-result" role="status" data-status={last()!.status}>{last()!.status==='UNKNOWN_OUTCOME'?uiText("操作结果待核对，请检查原会话状态。"):['FAILED','CANCELLED'].includes(last()!.status)?uiText("操作未完成，原会话记录保留。"):['ACCEPTED','RETURNED','COMPLETED'].includes(last()!.status)?uiText("操作已返回，请核对当前会话。 "):uiText("正在核对会话操作。")}</p></Show>
  </section>;
}
