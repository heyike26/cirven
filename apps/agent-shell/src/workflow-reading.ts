import type {RunStage} from './stage-workflows';

const storageKey = 'cirven-workflow-reading-v1';
const limits = {entries:48, text:65536, position:1e7};

export type WorkflowReadingBasis = {key:string; operation:string};
type Position = {top:number; width:number; offset:number; row?:string; field?:string; compact?:boolean; indexOpen?:boolean};
type Bookmark = WorkflowReadingBasis & Position;
type ReadingStorage = Pick<Storage,'getItem'|'setItem'>;

export function artifactReadingOperation(stage:RunStage|undefined):string|undefined{
 if(!stage?.artifact)return;
 const event=stage.log.findLast(entry=>typeof entry.operation_id==='string'&&
  (entry.event==='manual_results_started'||entry.event==='finished'&&
   (entry.artifact_written===true||
    // Earlier receipts only reported a successful review when they adopted the result.
    entry.artifact_written===undefined&&entry.status==='review'&&!entry.error)));
 return typeof event?.operation_id==='string'?event.operation_id:undefined;
}

function persisted(storage:ReadingStorage):Bookmark[]{
 try{
  const raw=storage.getItem(storageKey);
  if(!raw||raw.length>limits.text)return [];
  const value=JSON.parse(raw);
  if(!Array.isArray(value)||value.length>limits.entries)return [];
  return value.filter((entry:Bookmark)=>entry&&typeof entry.key==='string'&&entry.key.length<=1400&&
   typeof entry.operation==='string'&&entry.operation.length<=100&&
   Number.isFinite(entry.top)&&entry.top>=0&&entry.top<=limits.position&&
   Number.isFinite(entry.width)&&entry.width>0&&entry.width<=limits.position&&
   Number.isFinite(entry.offset)&&Math.abs(entry.offset)<=limits.position&&
   (entry.row===undefined||typeof entry.row==='string'&&entry.row.length<=300)&&
   (entry.field===undefined||typeof entry.field==='string'&&entry.field.length<=100)&&
   (entry.compact===undefined||typeof entry.compact==='boolean')&&
   (entry.indexOpen===undefined||typeof entry.indexOpen==='boolean'));
 }catch{return [];}
}

export class WorkflowReading {
 private pending=new Map<string,Bookmark>();
 private timer?:ReturnType<typeof setTimeout>;
 constructor(private storage:ReadingStorage){}
 load(basis:WorkflowReadingBasis){
  const saved=this.pending.get(basis.key)??persisted(this.storage).find(entry=>entry.key===basis.key);
  return saved?.operation===basis.operation?saved:undefined;
 }
 remember(basis:WorkflowReadingBasis,position:Position){
  this.pending.set(basis.key,{...basis,...position});
  if(this.timer===undefined)this.timer=setTimeout(()=>this.flush(),160);
 }
 flush(){
  if(this.timer!==undefined)clearTimeout(this.timer);
  this.timer=undefined;
  if(!this.pending.size)return;
  const before=persisted(this.storage);
  const next=[...before.filter(x=>!this.pending.has(x.key)),...this.pending.values()].slice(-limits.entries);
  const beforeText=JSON.stringify(before),afterText=JSON.stringify(next);
  this.pending.clear();
  try{if(beforeText!==afterText&&afterText.length<=limits.text)this.storage.setItem(storageKey,afterText);}catch{/* Reading state is optional. */}
 }
}

function artifact(root:HTMLElement){return root.querySelector<HTMLElement>('.wf-page .wf-artifact');}
function atTop(rows:HTMLElement[],top:number){
 return rows.find(row=>row.getBoundingClientRect().bottom>top+4);
}
export function captureWorkflowReading(root:HTMLElement):Position{
 const output=artifact(root), list=output?.querySelector<HTMLElement>('.wf-artifact-browser');
 const compact=list?list.dataset.compact==='true':undefined;
 const top=root.getBoundingClientRect().top;
 const row=atTop([...output?.querySelectorAll<HTMLElement>('[data-workflow-row]')??[]],top);
 const fields=[...row?.querySelectorAll<HTMLElement>('[data-field-name]')??[]];
 const field=row&&row.getBoundingClientRect().top<top?atTop(fields,top):undefined;
 const target=field??row;
 return {top:root.scrollTop,width:root.clientWidth,offset:target?target.getBoundingClientRect().top-top:0,
  row:row?.dataset.workflowRow,field:field?.dataset.fieldName,compact,
  indexOpen:compact===false?list?.dataset.indexOpen==='true':undefined};
}
export function restoreWorkflowReading(root:HTMLElement,saved:Position){
 const output=artifact(root);
 if(!output||!root.clientHeight)return false;
 if(saved.row){
  const compact=output.querySelector<HTMLElement>('.wf-artifact-browser')?.dataset.compact==='true';
  output.dispatchEvent(new CustomEvent('workflow-reveal-row',{detail:{id:saved.row,focus:false,
   indexOpen:!compact&&saved.compact===false?saved.indexOpen:undefined}}));
 }
 const matches=[...output.querySelectorAll<HTMLElement>('[data-workflow-row]')].filter(item=>item.dataset.workflowRow===saved.row);
 const row=matches.length===1?matches[0]:undefined;
 // A missing or ambiguous target must never apply its old scroll position to a different item.
 if(saved.row&&!row)return false;
 const field=[...row?.querySelectorAll<HTMLElement>('[data-field-name]')??[]].find(item=>item.dataset.fieldName===saved.field);
 const target=field??row;
 if(!target||saved.top===0)root.scrollTop=saved.top;
 else{
  const offset=root.clientWidth===saved.width?saved.offset:
   Math.max(field?-field.offsetHeight/2:0,Math.min(saved.offset,root.clientHeight/3));
  root.scrollTop+=target.getBoundingClientRect().top-root.getBoundingClientRect().top-offset;
 }
 return true;
}
