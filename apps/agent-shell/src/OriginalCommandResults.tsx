import {For,Show,createEffect,createMemo,createSignal,on,onCleanup} from 'solid-js';
import type {AgentSession} from './agent-sessions';
import {originalFileScope} from './original-session-files';
import {OriginalCommandObservationsApi,originalCommandsObservation,originalCommandDisplayCoverage,type OriginalCommandDisplayCoverage,type OriginalCommandCatalog,type CommandSourceSnapshot} from './original-command-observations';
import OriginalTestReport from './OriginalTestReport';
import OriginalWorkRoots from './OriginalWorkRoots';
import {observedReportSources} from './original-test-report';
import './original-command-results.css';
import type {OriginalWorkInputSource} from './original-work-input';

/** Read-only original command facts beside their owning conversation. */
export default function OriginalCommandResults(props:{session:AgentSession;contextKey:string;locale:string;onCoverage?:(value:OriginalCommandDisplayCoverage)=>void;onContinueWork?:(source:OriginalWorkInputSource,current:()=>boolean)=>Promise<void>}){
 const [requestedWork,setRequestedWork]=createSignal<{root:string}>();
 const client=new OriginalCommandObservationsApi(),[catalog,setCatalog]=createSignal<OriginalCommandCatalog>(),[loading,setLoading]=createSignal(false),[failed,setFailed]=createSignal(false),[recovery,setRecovery]=createSignal(false),[expanded,setExpanded]=createSignal(false);
 const en=()=>props.locale.startsWith('en'),context=()=>JSON.stringify([props.contextKey,originalFileScope(props.session),props.session.original_session_id]),key=createMemo(()=>{const observation=originalCommandsObservation(props.session);return observation?JSON.stringify([context(),observation]):undefined;});
 let lastCoverage:OriginalCommandDisplayCoverage|undefined;
 function clearCoverage(){if(!lastCoverage)return;const previous=lastCoverage;lastCoverage=undefined;props.onCoverage?.({...previous,entries:[]});}
 let section:HTMLElement|undefined;
 let alive=true,sequence=0,previousContext='',abort:AbortController|undefined;
 onCleanup(()=>{alive=false;sequence++;abort?.abort();clearCoverage();});
 async function read(){
  const basis=key(),session=props.session;if(!basis)return;
  const request=++sequence;abort?.abort();abort=new AbortController();setLoading(true);setFailed(false);
  try{const value=await client.list(session,abort.signal);if(!alive||sequence!==request||key()!==basis)return;
   const focused=section?.contains(document.activeElement)?document.activeElement?.closest('.as-command-result')?.getAttribute('data-command-record'):undefined;
   if(focused&&!value.commands.slice(-3).some(v=>v.record_ref.id===focused))setExpanded(true);
   const previous=new Map((catalog()?.commands??[]).map(v=>[v.record_ref.id,v]));
   setCatalog({...value,commands:value.commands.map(v=>{const old=previous.get(v.record_ref.id);return old&&JSON.stringify(old)===JSON.stringify(v)?old:v;})});
  }catch{if(alive&&sequence===request&&key()===basis){setFailed(true);setRecovery(true);}}
  finally{if(alive&&sequence===request&&key()===basis)setLoading(false);}
 }
 createEffect(on(key,basis=>{
  const current=context();if(previousContext!==current){previousContext=current;setRequestedWork(undefined);sequence++;abort?.abort();clearCoverage();setCatalog(undefined);setFailed(false);setLoading(false);setRecovery(false);setExpanded(false);}
  if(!basis){sequence++;abort?.abort();setLoading(false);setCatalog(undefined);setFailed(false);setRecovery(false);return;}
  void read();
 }));
 createEffect(()=>{
  const next=originalCommandDisplayCoverage(catalog(),props.session,props.contextKey,expanded());
  if(!next){clearCoverage();return;}
  if(lastCoverage&&JSON.stringify([lastCoverage.contextKey,lastCoverage.scope,lastCoverage.original_session_id])!==JSON.stringify([next.contextKey,next.scope,next.original_session_id]))clearCoverage();
  if(JSON.stringify(lastCoverage)===JSON.stringify(next))return;lastCoverage=next;props.onCoverage?.(next);
 });
 const coverageNotice=()=>{const reasons=catalog()?.coverage.reason_codes??[];return reasons.includes('COMMAND_GENERATION_CAPACITY_REACHED')?(en()?'New command records were not enabled for this generation. Existing results are kept below.':'本轮未启用新的命令记录，下方保留已有结果。'):reasons.some(code=>/UNAVAILABLE|NOT_UNIQUELY_ASSOCIATED/.test(code))?(en()?'Some command results could not be checked. Checked records are shown below.':'部分命令结果尚无法核对，以下为已核对记录。'):'';};
 const sourceLabel=(s:'UNCHANGED'|'CHANGED'|'UNAVAILABLE')=>s==='UNCHANGED'?(en()?'Source snapshots match':'源码前后快照相同'):s==='CHANGED'?(en()?'Source observation changed':'源码观察已变化'):(en()?'Source observation unavailable':'源码观察不可用');
 const snapshotLabel=(s:CommandSourceSnapshot|null)=>s===null?(en()?'Not observed':'未观察'):s.state==='ABSENT'?(en()?'File absent':'文件不存在'):s.sha256!;
 const shown=()=>expanded()?catalog()?.commands??[]:(catalog()?.commands??[]).slice(-3);
 return <><OriginalWorkRoots session={props.session} locale={props.locale} requested={requestedWork()} onContinueWork={props.onContinueWork}/><Show when={key()&&(catalog()||failed()||loading())}><section ref={element=>section=element} class="as-command-results" aria-label={en()?'Command results':'命令结果'} aria-busy={loading()}>
  <header><strong>{en()?'Command results':'命令结果'}</strong><Show when={loading()}><span role="status">{en()?'Checking…':'核对中…'}</span></Show></header>
  <Show when={coverageNotice()}>{notice=><p class="as-command-scope" data-command-coverage role="status">{notice()}</p>}</Show>
  <Show when={recovery()}><div class="as-command-check" role="status"><span>{failed()?(en()?'Command results could not be checked.':'命令结果暂未核对完成。'):loading()?(en()?'Checking the original record…':'正在核对原记录…'):(en()?'Original record checked.':'原记录已核对。')}</span><button type="button" aria-disabled={loading()} onClick={()=>{if(!loading())void read();}}>{en()?'Check again':'重新核对'}</button></div></Show>
  <Show when={catalog()}>{value=><>
   <For each={shown()}>{entry=><article class="as-command-result" data-command-record={entry.record_ref.id} data-result={entry.status==='UNKNOWN_OUTCOME'?'unknown':entry.exit_code===0?'ended':'nonzero'}>
    <div class="as-command-result-title"><code title={entry.command}>{entry.command}</code><span class="as-command-state">{entry.status==='UNKNOWN_OUTCOME'?(en()?'Result unknown':'结果未知'):(en()?'Exited · code ':'命令结束 · 退出码 ')+entry.exit_code}</span></div>
    <p class="as-command-source-status">{sourceLabel(entry.source_status)}</p>
    <Show when={shown().find(row=>row.root_operation_id===entry.root_operation_id)?.record_ref.id===entry.record_ref.id}><button type="button" class="as-command-more" onClick={()=>setRequestedWork({root:entry.root_operation_id})}>{en()?'Requirements and delivery':'对应要求与交付'}</button></Show>
    <Show when={observedReportSources(entry).length}><details><summary>{en()?'Test reports':'测试报告'}</summary><For each={observedReportSources(entry)}>{source=><OriginalTestReport session={props.session} command={entry} source={source} locale={props.locale}/>}</For></details></Show>
    <details><summary>{en()?'Output and source details':'输出与源码详情'}</summary><div class="as-command-details">
     <p>{en()?'Full command':'完整命令'}</p><pre class="as-command-output" tabIndex={0}>{entry.command}</pre>
     <Show when={entry.workdir}><p class="as-command-workdir"><span>{en()?'Working directory':'工作目录'}</span><code>{entry.workdir}</code></p></Show>
     <Show when={entry.output} fallback={<p>{en()?'The original output was not observed.':'未观察到原命令输出。'}</p>}>{output=><>
      <p>{en()?'Combined command output':'命令合并输出'}<Show when={output().truncated}> · {en()?'Truncated':'已截断'}</Show></p>
      <Show when={output().text} fallback={<p class="as-command-empty-output">{en()?'The observed output is empty.':'观察到的输出为空。'}</p>}><pre class="as-command-output" tabIndex={0}>{output().text}</pre></Show>
      <Show when={output().original_truncated}><p>{en()?'The original tool already truncated this output.':'原工具输出本身已截断。'}</p></Show>
      <Show when={output().original_output_path}><p class="as-command-original-output">{en()?'Original output location (not opened):':'原输出位置（未自动读取）：'}<code>{output().original_output_path}</code></p></Show>
      <details class="as-command-hashes"><summary>{en()?'Output fingerprint':'输出指纹'}</summary><p>{en()?'Observed text':'所示文字'} · {output().bytes} B</p><code>{output().sha256}</code></details>
     </>}</Show>
     <Show when={entry.sources.length}><details class="as-command-hashes"><summary>{en()?'Source fingerprints':'源码指纹'} · {entry.sources.length}</summary><For each={entry.sources}>{source=><div class="as-command-source"><strong>{source.path}</strong><dl><dt>{en()?'Before':'执行前'}</dt><dd><code>{snapshotLabel(source.before)}</code></dd><dt>{en()?'After':'执行后'}</dt><dd><code>{snapshotLabel(source.after)}</code></dd></dl></div>}</For></details></Show>
     <p class="as-command-scope">{en()?'Only declared files were observed. Matching before and after snapshots do not prove files stayed unchanged during the command. The exit code alone does not establish that tests passed.':'仅观察已声明文件。前后快照相同不能证明执行期间文件始终未变；退出码本身不代表测试通过。'}</p>
    </div></details>
   </article>}</For>
   <Show when={!value().commands.length&&!loading()}><p class="as-command-empty">{en()?'No verifiable command result is available in this record.':'此记录尚无可核对的命令结果。'}</p></Show>
   <Show when={value().commands.length>3}><button type="button" class="as-command-more" aria-expanded={expanded()} onClick={()=>setExpanded(v=>!v)}>{expanded()?(en()?'Show fewer':'收起'):(en()?'All commands':'全部命令')+' ('+value().commands.length+')'}</button></Show>
  </>}</Show>
 </section></Show></>;
}
