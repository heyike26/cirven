import {batch,createMemo,createSignal,For,onCleanup,onMount,Show} from 'solid-js';
import WorkflowDialog from './WorkflowDialog';
import {WorkflowIcon} from './WorkflowIcon';
import {stageDraftSettledEvent,type StageDraftSettled} from './workflow-transition';

const draftFields=[
 ['followUp','补充或修订','Follow-up or revision'],
 ['comment','页面批注','Page comment'],
 ['question','阶段提问','Stage question'],
 ['note','阶段笔记','Stage note'],
 ['rework','返工原因','Reason for rework']
] as const;
type DraftField=typeof draftFields[number][0];
type DraftEntry={field:DraftField;text:string;label:string;target?:string};
type Snapshot={entries:DraftEntry[];failed:boolean};
type Translate=(zh:string,en:string)=>string;

function readDraft(key:string,t:Translate):Snapshot{
 try{
  const saved=localStorage.getItem(key);
  if(!saved)return {entries:[],failed:false};
  const draft=JSON.parse(saved);
  if(!draft||typeof draft!=='object'||Array.isArray(draft))return {entries:[],failed:true};
  const entries=draftFields.flatMap(([field,zh,en])=>{
   const text=field==='rework'?draft.rework?.reason:draft[field];
   if(typeof text!=='string'||!text.trim())return [];
   const target=field==='comment'&&typeof draft.mark?.target==='string'?draft.mark.target:undefined;
   return [{field,label:t(zh,en),text,target}];
  });
  return {entries,failed:false};
 }catch{
  return {entries:[],failed:true};
 }
}

export default function WorkflowHistoricalDraft(p:{storageKey:string;stageName:string;t:Translate}){
 const key=p.storageKey;
 const [snapshot,setSnapshot]=createSignal(readDraft(key,p.t));
 const [available,setAvailable]=createSignal(snapshot().failed||snapshot().entries.length>0);
 const [open,setOpen]=createSignal(false),[selected,setSelected]=createSignal<DraftField>();
 const [copyStatus,setCopyStatus]=createSignal<'copied'|'selected'>();
 const current=createMemo(()=>snapshot().entries.find(entry=>entry.field===selected())??snapshot().entries[0]);
 let textBox:HTMLTextAreaElement|undefined;
 let copyEpoch=0;

 function refresh(){
  const next=readDraft(key,p.t);
  copyEpoch++;
  batch(()=>{
   setCopyStatus(undefined);
   setSnapshot(next);
   if(next.failed||next.entries.length)setAvailable(true);
  });
 }
 onMount(()=>{
  function storageChanged(event:StorageEvent){
   if(event.key===key||event.key===null)refresh();
  }
  function draftSettled(event:Event){
   if((event as CustomEvent<StageDraftSettled>).detail?.key===key)refresh();
  }
  window.addEventListener('storage',storageChanged);
  window.addEventListener(stageDraftSettledEvent,draftSettled);
  onCleanup(()=>{
   copyEpoch++;
   window.removeEventListener('storage',storageChanged);
   window.removeEventListener(stageDraftSettledEvent,draftSettled);
  });
 });

 function entryLabel(entry:DraftEntry){
  return entry.target?entry.label+' · '+(entry.target==='summary'?p.t('摘要','Summary'):entry.target):entry.label;
 }
 function close(){
  copyEpoch++;
  batch(()=>{setCopyStatus(undefined);setOpen(false);});
 }
 async function copy(){
  const item=current();
  if(!item)return;
  const epoch=++copyEpoch;
  const stillCurrent=()=>epoch===copyEpoch&&open()&&current()?.field===item.field&&current()?.text===item.text;
  setCopyStatus(undefined);
  try{
   await navigator.clipboard.writeText(item.text);
   if(stillCurrent())setCopyStatus('copied');
  }catch{
   if(!stillCurrent()||!textBox)return;
   textBox.focus({preventScroll:true});
   textBox.select();
   setCopyStatus('selected');
  }
 }
 return <Show when={available()}>
  <button class="wf-history-draft-trigger" type="button" aria-haspopup="dialog" onClick={()=>{
   refresh();setSelected(undefined);setOpen(true);
  }}><WorkflowIcon name="file" size={15}/>{p.t('查看本机草稿','View local drafts')}</button>
  <WorkflowDialog open={open()} title={p.t('本机草稿','Local drafts')} size="regular" locale={p.t('zh-CN','en')} onClose={close}>
   <Show when={open()}><div class="wf-history-drafts">
    <p class="wf-history-draft-source">{p.t('原阶段','Original stage')} · {p.stageName}</p>
    <Show when={current()} fallback={<Show when={snapshot().failed} fallback={
     <p role="status">{p.t('此阶段没有可取回的文字草稿。','There are no text drafts to recover for this stage.')}</p>
    }>
     <p role="alert">{p.t('暂时无法读取本机草稿。','Local drafts could not be read.')}</p>
     <button type="button" onClick={refresh}>{p.t('重新读取','Read again')}</button>
    </Show>}>{entry=><>
     <Show when={snapshot().entries.length>1} fallback={<h3>{entryLabel(entry())}</h3>}>
      <label>{p.t('选择草稿','Choose draft')}
       <select value={entry().field} onChange={event=>{
        copyEpoch++;
        batch(()=>{setSelected(event.currentTarget.value as DraftField);setCopyStatus(undefined);});
       }}>
        <For each={snapshot().entries}>{item=><option value={item.field} selected={item.field===entry().field}>{entryLabel(item)}</option>}</For>
       </select>
      </label>
     </Show>
     <textarea ref={textBox} rows={7} readonly aria-label={entry().label} value={entry().text}/>
     <div class="wf-history-draft-actions">
      <small role="status">{copyStatus()==='copied'?p.t('已复制到剪贴板。','Copied to clipboard.'):
       copyStatus()==='selected'?p.t('已选中原文，可用系统复制快捷键。','Text selected. Use your system copy shortcut.'):''}</small>
      <button type="button" class="wf-primary" onClick={()=>void copy()}>{p.t('复制草稿','Copy draft')}</button>
     </div>
    </>}</Show>
   </div></Show>
  </WorkflowDialog>
 </Show>;
}
