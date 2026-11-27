import {createEffect,createMemo,createSignal,For,Show,onCleanup,untrack,type Accessor,type JSX} from 'solid-js';
import {agentSessionsApi,AgentSessionOutcomeError,originalSessionReady,contextAuthorityNotice,type AgentSession,type PendingRequest} from './agent-sessions';
import {OriginalControlDecisions,ControlQuestionDraft,onControlQuestionDraftChange,controlIdentity,controlKey,controlQuestions,pendingPatches,pendingFileName,pendingDiffPreview,pendingActionTitle,questionAnswers,type ControlAttempt,type ControlClient,type ControlDraft,type ControlLocks} from './original-pending-controls';
import {readQuestionRecoveries,markQuestionRecovered,type QuestionDraftRecovery} from './original-question-recovery';
import './original-pending-controls.css';

type Props={session?:AgentSession;locale:string;ownerRecovery?:JSX.Element;contextRecovery?:JSX.Element;onUpdated:(value:AgentSession)=>void;onRecovery?:(attempt:ControlAttempt)=>Promise<boolean>;onRecoverAnswer?:(draft:QuestionDraftRecovery)=>Promise<string|undefined>;client?:ControlClient&{get:typeof agentSessionsApi.get};storage?:Storage;locks?:ControlLocks};
const closed=(a?:ControlAttempt)=>!!a&&['RETURNED','COMPLETED'].includes(a.status);
const unresolved=(a?:ControlAttempt)=>!!a&&!['RETURNED','COMPLETED','FAILED','CANCELLED','REJECTED'].includes(a.status);
const copy=(locale:string,zh:string,en:string)=>locale.startsWith('en')?en:zh;

/** Current work stays beside the composer. Connection and session management
 * have no second permission form; both navigate to this same original request. */
export default function OriginalPendingControls(props:Props){
 const storage=props.storage??localStorage,client=props.client??agentSessionsApi,[version,setVersion]=createSignal(0),[error,setError]=createSignal('');
 const decisions=new OriginalControlDecisions(storage,client,props.locks??navigator.locks,()=>setVersion(n=>n+1));
 const t=(zh:string,en:string)=>copy(props.locale,zh,en);
 const state=createMemo(()=>{version();const s=props.session;if(!s)return {attempts:[] as ControlAttempt[],error:false};try{return {attempts:decisions.attempts(s),error:false};}catch{return {attempts:[],error:true};}});
 const requests=createMemo(()=>new Map((props.session?[...props.session.pending_questions,...props.session.pending_permissions]:[]).map(item=>[controlKey(controlIdentity(props.session!,item)),item])));
 const keys=createMemo(()=>[...requests().keys()]);
 const unknown=createMemo(()=>state().attempts.filter(a=>unresolved(a)&&!keys().includes(controlKey(a.identity))));
 const recoveries=createMemo(()=>{version();const s=props.session;if(!s||state().error)return [];return decisions.recoveries(s);});
 const [answerVersion,setAnswerVersion]=createSignal(0),[answerBusy,setAnswerBusy]=createSignal(''),[answerError,setAnswerError]=createSignal('');
 onCleanup(onControlQuestionDraftChange(()=>setAnswerVersion(v=>v+1)));
 const answerScope=createMemo(()=>{const s=props.session;return s?JSON.stringify([s.project_id,s.session_ref,s.generation,s.active_operation_id,s.owner_status,s.connected_owner,s.configuration_current,s.observation_is_current,s.pending_questions.map(p=>p.request_ref),state().attempts]):'';});
 const answers=createMemo(()=>{answerVersion();answerScope();return untrack(()=>{const s=props.session;if(!s||!props.onRecoverAnswer||state().error)return {rows:[] as QuestionDraftRecovery[],error:false};try{return {rows:readQuestionRecoveries(storage,s,state().attempts),error:false};}catch{return {rows:[] as QuestionDraftRecovery[],error:true};}});});
 async function restoreAnswer(draft:QuestionDraftRecovery){if(answerBusy()||!props.onRecoverAnswer)return;setAnswerBusy(draft.key);setAnswerError('');try{const target=await props.onRecoverAnswer(draft);if(target){markQuestionRecovered(storage,draft,target);setAnswerVersion(v=>v+1);}}catch{setAnswerError(t('回答已保留；请核对当前消息后再取回。','Your answers are kept. Check your current message before restoring.'));}finally{setAnswerBusy('');}}
 const changed=()=>{setVersion(n=>n+1);};
 if(typeof window!=='undefined'){const listener=(e:StorageEvent)=>{if(e.key?.startsWith('eeas-original-controls-v1:'))changed();if(e.key?.startsWith('eeas-discussion-draft-v1:'))setAnswerVersion(v=>v+1);};window.addEventListener('storage',listener);onCleanup(()=>window.removeEventListener('storage',listener));}
 const same=(s:AgentSession)=>props.session?.project_id===s.project_id&&props.session?.session_ref.id===s.session_ref.id&&props.session?.session_ref.sha256===s.session_ref.sha256&&props.session?.generation===s.generation;
 async function refresh(s:AgentSession){const next=await client.get(s.project_id,s.session_ref);if(same(s))props.onUpdated(next);}
 async function inspect(a:ControlAttempt){const s=props.session;if(!s)return;setError('');try{await decisions.inspect(s,a);await refresh(s);}catch{if(same(s))setError(t('原操作暂未核对，未重复回应。','The original action could not be confirmed. Nothing was resent.'));}}
 async function prepare(a:ControlAttempt){const s=props.session;if(!s||!props.onRecovery)return;setError('');try{if(await props.onRecovery(a))await decisions.recoveryPrepared(s,a);}catch{if(same(s))setError(t('当前输入已保留，请先核对草稿与文件选择。','Your input is kept. Check the draft and selected file first.'));}}
 const offline=()=>!!props.session&&!props.session.connected_owner&&['EXPIRED','DISCONNECTED','CLOSED','BLOCKED','BLOCKED_PROCESS_STOP_UNCONFIRMED'].includes(props.session.owner_status);
 const cards=()=> <For each={keys()}>{key=><PendingCard session={()=>props.session!} item={()=>requests().get(key)!} locale={()=>props.locale} storage={storage} decisions={decisions} version={version} blocked={()=>state().error} onUpdated={refresh}/>}</For>;
 return <Show when={props.session&&(keys().length||unknown().length||recoveries().length||answers().rows.length||answers().error||state().error||props.ownerRecovery||props.contextRecovery||contextAuthorityNotice(props.session))}><section class="as-original-pending" aria-label={t('当前任务请求','Current task requests')} tabindex="-1">
  {props.ownerRecovery}
  {props.contextRecovery??<Show when={contextAuthorityNotice(props.session)}><p role="alert">{contextAuthorityNotice(props.session,props.locale.startsWith('en'))}</p></Show>}
  <Show when={state().error}><p role="alert">{t('已保存的回应记录暂未核对，当前输入保留。','Saved responses could not be checked. Your input is kept.')}</p></Show>
  <Show when={offline()&&keys().length} fallback={cards()}><details class="as-expired-proposal" onToggle={e=>{if(!e.currentTarget.open)e.currentTarget.closest<HTMLElement>('.as-original-pending')?.scrollTo({top:0});}}><summary>{t('旧请求（已失效）','Previous request (inactive)')}</summary>{cards()}</details></Show>
  <For each={unknown()}>{a=><article class="as-pending-recovery"><p>{t('此前的回应结果尚未确认。先核查原操作，避免重复授权。','An earlier response needs checking before another approval.')}</p><button type="button" disabled={decisions.busy(props.session!)} onClick={()=>void inspect(a)}>{t('核查原回应','Check original response')}</button></article>}</For>
  <For each={recoveries()}>{a=><article class="as-pending-recovery"><p>{t('旧修改已停止，人工修改已保留。','The old edit stopped. Your saved changes are kept.')}</p><Show when={props.onRecovery}><button type="button" disabled={!originalSessionReady(props.session)||!!props.session?.active_operation_id||decisions.busy(props.session!)} onClick={()=>void prepare(a)}>{t('取回修改要求','Restore edit request')}</button><small>{t('核对当前文件后确认发送，不会自动重跑。','Check the current file and restored input before sending.')}</small></Show></article>}</For>
  <Show when={answers().rows.length}><details class="as-answer-recovery"><summary>{answers().rows.some(row=>!row.copiedTo)?t('未发送的回答','Unsent answers'):t('回答草稿','Answer drafts')} · {answers().rows.length}</summary><p>{t('可加入当前消息，检查后再发送。旧请求不会重新回应。','Add them to your current message, then review and send. Earlier requests will not be answered again.')}</p><For each={answers().rows}>{row=><article data-answer-recovery={row.identity.request.id}><For each={Array.from({length:Math.max(row.draft.answers.length,row.draft.custom.length)},(_,index)=>index)}>{index=><Show when={row.draft.answers[index]?.length||row.draft.custom[index]?.trim()}><div class="as-answer-recovery-item"><strong>{row.questions[index]||t('回答 ','Answer ')+(index+1)}</strong><For each={row.draft.answers[index]??[]}>{text=><p>{text}</p>}</For><Show when={row.draft.custom[index]?.trim()}><p>{row.draft.custom[index]}</p></Show></div></Show>}</For><button type="button" disabled={!!answerBusy()||!!props.session?.active_operation_id} onClick={()=>void restoreAnswer(row)}>{answerBusy()===row.key?t('正在取回…','Restoring…'):row.copiedTo?t('再次加入当前消息','Add to current message again'):t('加入当前消息','Add to current message')}</button></article>}</For></details></Show>
  <Show when={answers().error||answerError()}><p class="as-pending-recovery" role="alert">{answerError()||t('此前的回答草稿暂未核对，原内容保留。','Earlier answer drafts could not be checked. Your input is kept.')}</p></Show>
  <Show when={error()}><p role="alert">{error()}</p></Show>
 </section></Show>;
}

function PendingCard(props:{session:Accessor<AgentSession>;item:Accessor<PendingRequest>;locale:Accessor<string>;storage:Storage;decisions:OriginalControlDecisions;version:Accessor<number>;blocked:Accessor<boolean>;onUpdated:(s:AgentSession)=>Promise<void>}){
 const t=(zh:string,en:string)=>copy(props.locale(),zh,en),item=props.item,session=props.session;
 const [error,setError]=createSignal(''),[draftError,setDraftError]=createSignal(false),[conflict,setConflict]=createSignal<ControlDraft>(),[draft,setDraft]=createSignal<ControlDraft>({answers:[],custom:[]});
 let draftOwner:ControlQuestionDraft|undefined,footer:HTMLElement|undefined,disposed=false;onCleanup(()=>{disposed=true;});
 const frozenItem=JSON.parse(JSON.stringify(item())) as PendingRequest;
 if(item().kind==='QUESTION')try{draftOwner=new ControlQuestionDraft(props.storage,controlIdentity(session(),item()),controlQuestions(frozenItem).map(q=>q.question));setDraft(draftOwner.read());}catch{setDraftError(true);}
 const latest=()=>{props.version();if(props.blocked())return undefined;return props.decisions.latest(session(),item());};
 const busy=()=>{props.version();return props.decisions.busy(session());};
 const patches=()=>pendingPatches(frozenItem),questions=()=>controlQuestions(frozenItem);
 const permission=()=>String(item().native_request.permission??'');
 const compactScope=()=>permission()==='edit'&&patches().length>0&&Array.isArray(item().native_request.patterns)&&(item().native_request.patterns as unknown[]).every(path=>typeof path==='string'&&!/[?*\[\]{}]/.test(path)&&patches().some(file=>file.path.replaceAll('\\','/').replace(/^\/+/, '')===path.replaceAll('\\','/').replace(/^\/+/, '')));
 const readable=()=>Array.isArray(item().native_request.patterns)&&((item().native_request.patterns as unknown[]).length>0)&&(item().native_request.patterns as unknown[]).every(v=>typeof v==='string'&&!!v);
 const unavailable=()=>props.blocked()||!originalSessionReady(session())||item().request_ref.generation!==session().generation;
 const reveal=()=>queueMicrotask(()=>{if(footer?.isConnected)footer.scrollIntoView({block:'nearest',inline:'nearest'});});
 const save=(value:ControlDraft)=>{setDraft(value);try{if(!draftOwner)throw Error();draftOwner.set(value);setDraftError(false);setConflict(undefined);}catch{setDraftError(true);}};
 function choose(index:number,label:string,multiple:boolean){const d=structuredClone(draft()),old=d.answers[index]??[];d.answers[index]=multiple?(old.includes(label)?old.filter(v=>v!==label):[...old,label]):[label];if(!multiple)d.custom[index]='';save(d);}
 function text(index:number,value:string){const d=structuredClone(draft());d.custom[index]=value;if(!questions()[index]?.multiple&&value.trim())d.answers[index]=[];save(d);}
 async function decide(choice:string){
  const s=session(),p=item();setError('');
  try{if(draftOwner)draftOwner.flush();if(draftError())throw Error('CONTROL_DRAFT_UNAVAILABLE');const answers=p.kind==='QUESTION'&&choice==='reply'?questionAnswers(p,draft()):undefined;await props.decisions.decide(s,p,choice,answers);await props.onUpdated(s);}
  catch(e){if(disposed||!item()||controlKey(controlIdentity(session(),item()))!==controlKey(controlIdentity(s,p)))return;
   setError(e instanceof AgentSessionOutcomeError&&e.failureCode==='WORKSPACE_SOURCE_CHANGED'?t('文件已变化，这次修改尚未执行。请拒绝旧方案，再使用当前稿继续。','The file changed. This edit was not applied. Reject the old proposal and continue with the current file.'):(e as Error)?.message==='CONTROL_ANSWERS_REQUIRED'?t('请逐条选择或填写回答，已有内容保留。','Answer each question. Your input is kept.'):t('这次回应尚未确认，输入保留；请核查原回应。','This response needs checking. Your input is kept.'));
  }finally{reveal();}
 }
 async function inspect(){const s=session(),a=latest();if(!a)return;setError('');try{await props.decisions.inspect(s,a);await props.onUpdated(s);}catch{setError(t('原回应暂未核对，未重新提交。','The original response could not be checked. Nothing was resent.'));}finally{reveal();}}
 function inspectDraft(){try{if(!draftOwner)throw Error();setConflict(draftOwner.inspect());}catch{setError(t('已保存的回答暂未读到，当前回答保留。','Saved answers are unavailable. Your current answers are kept.'));}reveal();}
 function resolveDraft(choice:'local'|'saved'){try{if(!draftOwner)throw Error();setDraft(draftOwner.resolve(choice));setDraftError(false);setConflict(undefined);setError('');}catch{setError(t('回答尚未保存，两个版本均未替换。','Answers were not saved. Neither version was replaced.'));}reveal();}
 const title=()=>pendingActionTitle(item(),props.locale());
 const blocked=(choice:string)=>busy()||unavailable()||closed(latest())||unresolved(latest())||choice!=='reject'&&(!!latest()?.sourceChanged||item().kind==='PERMISSION'&&(!readable()||permission()==='edit'&&!patches().length)||item().kind==='QUESTION'&&!questions().length);
 const choices=()=>['reject','always','once','reply'].filter(choice=>item().allowed_choices.includes(choice));
 return <article class="as-pending-card" data-request={item().request_ref.id} onKeyDown={e=>{if(item().kind==='QUESTION'&&e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.isComposing&&!blocked('reply')){e.preventDefault();e.stopPropagation();void decide('reply');}}}>
  <header><strong>{title()}</strong></header>
  <div class="as-pending-body">
   <Show when={item().kind==='PERMISSION'}><Show when={!compactScope()}><For each={Array.isArray(item().native_request.patterns)?item().native_request.patterns as string[]:[]}>{path=><p class="as-pending-scope">{path}</p>}</For></Show><Show when={!readable()||permission()==='edit'&&!patches().length}><p>{t('修改内容或作用范围尚未完整读取。请先核查，也可以拒绝此次请求。','The full change or scope is unavailable. Check the request or decline it.')}</p></Show><For each={patches()}>{patch=><PatchPreview path={patch.path} patch={patch.patch} locale={props.locale()}/>}</For><Show when={compactScope()}><details class="as-pending-scope-details"><summary>{t('查看完整授权范围','View full permission scope')}</summary><For each={item().native_request.patterns as string[]}>{path=><p class="as-pending-scope">{path}</p>}</For></details></Show></Show>
   <Show when={item().kind==='QUESTION'}><Show when={!questions().length}><p>{t('问题尚未完整读取，请先核查原请求。','The complete questions are unavailable. Check the original request.')}</p></Show><For each={questions()}>{(q,index)=><fieldset><legend>{q.question}</legend><For each={q.options}>{option=><label class="as-pending-option"><input type={q.multiple?'checkbox':'radio'} name={controlKey(controlIdentity(session(),item()))+index()} checked={draft().answers[index()]?.includes(option.label)??false} disabled={busy()||unavailable()||closed(latest())||unresolved(latest())} onChange={()=>choose(index(),option.label,!!q.multiple)}/><span>{option.label}<Show when={option.description}><small>{option.description}</small></Show></span></label>}</For><Show when={q.custom!==false}><Show when={q.options.length} fallback={<label class="as-pending-custom"><textarea aria-label={q.question} rows="2" value={draft().custom[index()]??''} disabled={busy()||unavailable()||closed(latest())||unresolved(latest())} onInput={e=>text(index(),e.currentTarget.value)}/></label>}><details class="as-pending-other" open={!!draft().custom[index()]}><summary>{t('其他回答','Other answer')}</summary><label class="as-pending-custom"><textarea aria-label={q.question} rows="2" value={draft().custom[index()]??''} disabled={busy()||unavailable()||closed(latest())||unresolved(latest())} onInput={e=>text(index(),e.currentTarget.value)}/></label></details></Show></Show></fieldset>}</For></Show>
  </div>
  <footer ref={footer} class="as-pending-footer">
   <Show when={draftError()}><p role="alert">{t('回答暂未保存，当前输入保留。','Answers could not be saved. Your input is kept.')} <button type="button" onClick={inspectDraft}>{t('核对回答草稿','Check answer draft')}</button></p></Show>
   <Show when={conflict()}>{remote=><div class="as-pending-draft-conflict"><p>{t('已保存的回答','Saved answers')}: {[...remote().answers.flat(),...remote().custom.filter(Boolean)].join(' · ')||t('空白','Empty')}</p><button type="button" onClick={()=>resolveDraft('local')}>{t('保留此处回答','Keep these answers')}</button><button type="button" onClick={()=>resolveDraft('saved')}>{t('采用已存回答','Use saved answers')}</button></div>}</Show>
   <Show when={error()||latest()?.sourceChanged}><p role="alert">{error()||(t('文件已变化，旧修改未执行。请先拒绝旧方案。','The file changed. The old edit was not applied. Reject it first.'))}</p></Show>
   <Show when={closed(latest())}><p role="status">{t('回应已返回，正在核对当前任务。','Response returned. Checking the current task.')}</p></Show>
   <Show when={!originalSessionReady(session())}><p role="status">{t('此请求当前不可回应，请先核对连接状态。','This request cannot be answered now. Check the connection state first.')}</p></Show>
   <div class="as-pending-actions"><Show when={latest()}><button type="button" disabled={busy()} onClick={()=>void inspect()}>{t('核查原回应','Check original response')}</button></Show><Show when={originalSessionReady(session())}><For each={choices()}>{choice=><button type="button" data-primary={choice==='once'||choice==='reply'} disabled={blocked(choice)} onClick={()=>void decide(choice)}>{choice==='once'?t('仅允许这一次','Allow once'):choice==='always'?t('允许此请求范围','Allow this scope'):choice==='reply'?t('提交回答','Send answer'):latest()?.sourceChanged?t('拒绝旧方案','Reject old proposal'):t('拒绝','Decline')}</button>}</For></Show></div>
  </footer>
 </article>;
}

function PatchPreview(props:{path:string;patch:string;locale:string}){
 const [full,setFull]=createSignal(false),body=()=>pendingDiffPreview(props.patch),lines=()=>body().split('\n'),long=()=>lines().length>120||body()!==props.patch;
 return <section class="as-pending-patch"><strong title={props.path}>{pendingFileName(props.path)}</strong><Show when={!full()} fallback={<pre tabindex="0">{props.patch}</pre>}><pre tabindex="0"><For each={long()?lines().slice(0,120):lines()}>{line=><span data-kind={line.startsWith('+')?'add':line.startsWith('-')?'remove':'context'}>{line||'\u00a0'}</span>}</For></pre></Show><Show when={long()}><button type="button" aria-expanded={full()} onClick={()=>setFull(v=>!v)}>{copy(props.locale,full()?'收起完整差异':'查看完整差异',full()?'Show less':'Show full diff')}</button></Show></section>;
}
