import {createEffect,createMemo,createSignal,For,on,onCleanup,Show} from 'solid-js';
import type {AgentSession} from './agent-sessions';
import type {OriginalCommandObservation} from './original-command-observations';
import {originalFileScope} from './original-session-files';
import {OriginalTestReportApi,type CommandTestReport,type ReportFilter,type ReportSource} from './original-test-report';
import WorkCaseEditor from './WorkCaseEditor';

export default function OriginalTestReport(props:{session:AgentSession;command:OriginalCommandObservation;source:ReportSource;locale:string}){
 const api=new OriginalTestReportApi(),[report,setReport]=createSignal<CommandTestReport>(),[loading,setLoading]=createSignal(false),[error,setError]=createSignal(false);
 const t=(zh:string,en:string)=>props.locale.startsWith('en')?en:zh;
 const identity=createMemo(()=>JSON.stringify([originalFileScope(props.session),props.session.original_session_id,props.command.record_ref,props.source.path,props.source.after?.sha256]));
 let disposed=false,sequence=0,abort:AbortController|undefined;
 onCleanup(()=>{disposed=true;sequence++;abort?.abort();});
 createEffect(on(identity,()=>{sequence++;abort?.abort();setReport(undefined);setLoading(false);setError(false);}));
 async function read(offset=0,outcome:ReportFilter=report()?.page.outcome??'ALL'){
  if(loading())return;const key=identity(),epoch=++sequence,prior=report();abort?.abort();abort=new AbortController();setLoading(true);setError(false);
  try{const value=await api.read(props.session,props.command,props.source,{offset,outcome,expectedRef:prior?.report_ref},abort.signal);if(!disposed&&identity()===key&&epoch===sequence)setReport(value);}
  catch{if(!disposed&&identity()===key&&epoch===sequence)setError(true);}
  finally{if(!disposed&&identity()===key&&epoch===sequence)setLoading(false);}
 }
 const outcome=(value:string)=>({PASSED:t('报告未列出失败','No failures reported'),PASSED_WITH_SKIPS:t('报告无失败，含跳过用例','No failures reported; some cases skipped'),FAILED:t('报告列出了失败或错误','Failures or errors reported'),SKIPPED:t('报告用例均已跳过','All reported cases skipped'),NO_TESTS:t('报告没有测试用例','No test cases in this report'),INCONSISTENT:t('报告计数或结果有矛盾','Report counts or results are inconsistent')}[value]??t('报告尚不能核实','Report not verified'));
 const status=(value:string)=>({PASSED:t('通过','Passed'),FAILED:t('失败','Failed'),ERROR:t('错误','Error'),SKIPPED:t('跳过','Skipped'),UNKNOWN:t('未核实','Unverified')}[value]??value);
 return <section class="as-command-test-report" data-command-test-report={props.command.record_ref.id} aria-busy={loading()}>
  <Show when={!report()}><button type="button" class="as-command-report-read" disabled={loading()} onClick={()=>void read()}><span>{loading()?t('正在核对报告…','Checking report…'):t('读取测试报告','Read test report')}</span><span class="as-command-report-path" title={props.source.path}>{props.source.path}</span></button></Show>
  <Show when={error()}><p role="alert">{t('已保存的报告暂不能核对，原命令记录保留。不会重新运行命令。','The saved report could not be checked. The original command is kept and will not run again.')}</p><button type="button" disabled={loading()} onClick={()=>void read(report()?.page.offset??0)}>{t('重新核对原报告','Check original report again')}</button></Show>
  <Show when={report()}>{value=><>
   <header><strong>{outcome(value().summary.outcome)}</strong><span class="as-command-report-path" title={props.source.path}>{props.source.path}</span></header>
   <dl class="as-command-report-counts"><For each={['PASSED','FAILED','ERROR','SKIPPED','UNKNOWN'] as const}>{key=><Show when={key!=='UNKNOWN'||value().summary.counts.UNKNOWN>0}><div><dt>{status(key)}</dt><dd>{value().summary.counts[key]}</dd></div></Show>}</For></dl>
   <p class="as-command-scope">{t('原命令执行后的已保存快照；尚未复核当前工程、目标环境或整体验收。','Saved after the original command; current workspace, target environment and whole-task acceptance remain unverified.')}</p>
   <Show when={value().association==='UNCHANGED_BYTES'}><p class="as-command-scope">{t('报告前后内容相同，无法据此确认本次重新生成。','Report bytes match the earlier snapshot. This does not prove a newly generated result.')}</p></Show>
   <Show when={value().association==='BEFORE_UNAVAILABLE'}><p class="as-command-scope">{t('缺少执行前的报告观察，生成来源仍待核实。','The earlier report snapshot is unavailable; report generation remains unverified.')}</p></Show>
   <Show when={value().command_status==='UNKNOWN_OUTCOME'}><p class="as-command-scope">{t('原命令结果仍不确定，报告内容不改变该状态。','The original command outcome is still unknown. Report content does not change that status.')}</p></Show>
   <Show when={value().reason_codes.includes('COMMAND_NONZERO_WITH_REPORT_PASS')}><p class="as-command-scope">{t('报告未列失败，但原命令非零退出，请同时核查原输出。','No failures are reported, but the command exited nonzero. Check its original output too.')}</p></Show>
   <label class="as-command-report-filter"><span>{t('显示用例','Show cases')}</span><select disabled={loading()} value={value().page.outcome} onChange={e=>void read(0,e.currentTarget.value as ReportFilter)}><option value="ALL">{t('全部','All')}</option><option value="PROBLEMS">{t('失败、错误和未核实','Failed, errors and unverified')}</option><option value="SKIPPED">{t('跳过','Skipped')}</option><option value="PASSED">{t('通过','Passed')}</option></select></label>
   <div class="as-command-report-cases"><For each={value().cases}>{item=><details class="as-command-report-case"><summary><span>{status(item.status)}</span><strong>{item.name||t('未命名用例','Unnamed case')}</strong></summary><Show when={item.suite||item.classname}><p>{[item.suite,item.classname].filter(Boolean).join(' · ')}</p></Show><Show when={item.file}><p>{t('报告位置：','Reported location: ')}{item.file}{item.line!==null?':'+item.line:''}</p></Show><Show when={item.duration_seconds!==null}><p>{t('报告用时：','Reported duration: ')}{item.duration_seconds} s</p></Show><Show when={item.message}><p>{item.message}</p></Show><Show when={item.detail}><pre class="as-command-output" tabIndex={0}>{item.detail}</pre></Show><Show when={item.text_truncated}><p class="as-command-scope">{t('详情已截断，原始快照仍保留。','Details are truncated; the original snapshot is kept.')}</p></Show><Show when={typeof value().basis.root_operation_id==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value().basis.root_operation_id??'')}><WorkCaseEditor session={props.session} root={value().basis.root_operation_id!} report={value()} item={item} locale={props.locale}/></Show></details>}</For></div>
   <Show when={!value().cases.length}><p>{t('当前筛选没有用例。','No cases match this filter.')}</p></Show>
   <nav class="as-command-report-pages" aria-label={t('报告用例分页','Report case pages')}><button type="button" disabled={loading()||value().page.offset===0} onClick={()=>void read(Math.max(0,value().page.offset-20))}>{t('上一页','Previous')}</button><span>{value().page.matching?value().page.offset+1:0}–{value().page.offset+value().cases.length} / {value().page.matching}</span><button type="button" disabled={loading()||value().page.next_offset===null} onClick={()=>{const next=value().page.next_offset;if(next!==null)void read(next);}}>{t('下一页','Next')}</button></nav>
  </>}</Show>
 </section>;
}
