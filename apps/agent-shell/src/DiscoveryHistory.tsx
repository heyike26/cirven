import {createMemo,For,Show} from 'solid-js';
import type {DiscoverySession} from './discovery-contracts';
import {t as uiText} from './ui-language';

type RecordValue=Record<string,unknown>;
const object=(value:unknown):RecordValue=>value!==null&&typeof value==='object'&&!Array.isArray(value)?value as RecordValue:{};
const string=(value:unknown)=>typeof value==='string'?value:'';
const list=(value:unknown)=>Array.isArray(value)?value:[];
const historyLabels:Record<string,string>={USER_STATEMENT:'起始陈述',USER_MESSAGE:'追加说明',USER_ANSWER:'保存的回答',USER_FEEDBACK:'文稿与图批注'};

// Presentation only. The immutable history and receipt remain the authority;
// unknown/internal records never become a generic JSON viewer.
export default function DiscoveryHistory(props:{session:DiscoverySession}){
  const sources=createMemo(()=>{
    const receipt=object(props.session.payload.source_receipt),coverage=list(receipt.coverage).map(object);
    return list(receipt.documents).map(object).map(doc=>{
      const included=coverage.find(row=>row.document_id===doc.document_id);
      return {
        name:string(doc.source).startsWith('discovery-user-statements:')?uiText('本次讨论的陈述与补充'):string(doc.filename)||uiText('来源名称待核对'),
        revision:Number.isSafeInteger(doc.revision)&&Number(doc.revision)>0?Number(doc.revision):undefined,
        coverage:included?.text_coverage==='FULL_PARSED_TEXT'?'已包含全部解析文本':included?.text_coverage==='SELECTED_CHUNKS'?'使用所选文本片段':'文本覆盖范围待核对',
        omitted:list(included?.omitted_chunk_indexes).length,
        modalityUnknown:!included?.original_modal_coverage||included.original_modal_coverage==='UNASSESSED',
      };
    });
  });
  const history=createMemo(()=>list(props.session.payload.history).map(object).map(row=>{
    const kind=string(row.kind),raw=string(row.text);
    let text='';
    if(kind==='USER_FEEDBACK'){
      try{text=string(object(JSON.parse(raw)).comment);}catch{/* Keep internal/broken wire out of the user view. */}
    }else if(kind==='USER_STATEMENT'||kind==='USER_MESSAGE'||kind==='USER_ANSWER'){
      text=raw;
      if(kind==='USER_ANSWER'){
        const question=props.session.payload.candidate.questions.find(value=>raw.startsWith(value.id+'：'));
        if(question)text=question.text+'：'+raw.slice(question.id.length+1);
      }
    }
    const date=new Date(string(row.created_at));
    return {label:historyLabels[kind]??'工作记录',text:text||uiText('已记录，完整内容保留在原讨论中。'),dateTime:Number.isFinite(date.getTime())?date.toISOString():undefined,date:Number.isFinite(date.getTime())?date.toLocaleString():undefined};
  }));
  return <div class="as-discovery-records">
    <dl class="as-discovery-facts">
      <div><dt>{uiText('文稿标题')}</dt><dd>{props.session.payload.candidate.title}</dd></div>
      <div><dt>{uiText('生成时阶段')}</dt><dd>{uiText(({DISCOVER:'探索问题',DEFINE:'明确问题',RESEARCH:'核查与比较',SPECIFY:'完善要求',PLAN:'准备任务计划'})[props.session.payload.candidate.phase])}</dd></div>
      <div><dt>{uiText('当前确认状态')}</dt><dd>{props.session.payload.status==='CONFIRMED'?uiText('要求已确认'):uiText('待完善与确认')}</dd></div>
      <div><dt>{uiText('来源版本')}</dt><dd>{props.session.source_current?uiText('来源版本已核对'):uiText('来源需要复核')}</dd></div>
      <div><dt>{uiText('执行授权')}</dt><dd>{props.session.readiness.execution_authorized?uiText('请在执行记录中核对授权范围'):uiText('未授权执行')}</dd></div>
    </dl>
    <h3>{uiText('本轮依据的资料')}</h3>
    <Show when={sources().length} fallback={<p>{uiText('本轮未附加资料。')}</p>}>
      <ul class="as-discovery-source-list"><For each={sources()}>{source=><li>
        <strong>{source.name}</strong><span>{source.revision?uiText('版本 {revision}',{revision:source.revision}):uiText('版本待核对')}</span>
        <p>{uiText(source.coverage)}<Show when={source.omitted}> · {uiText('未纳入 {count} 个文本片段',{count:source.omitted})}</Show></p>
        <Show when={source.modalityUnknown}><p class="as-muted">{uiText('原文件版式与图像覆盖尚未核实')}</p></Show>
      </li>}</For></ul>
    </Show>
    <Show when={history().length}><h3>{uiText('讨论记录')}</h3><ol class="as-discovery-history-list"><For each={history()}>{row=><li>
      <div><strong>{uiText(row.label)}</strong><Show when={row.date}><time dateTime={row.dateTime}>{row.date}</time></Show></div><p>{row.text}</p>
    </li>}</For></ol></Show>
    <p class="as-muted">{uiText('结构检查不等于工程内容已验证；流程可含合法回路。')}</p>
    <p class="as-muted">{uiText('可以逐项回答并以后继续。保存回答不会自动接受答案。')}</p>
  </div>;
}
