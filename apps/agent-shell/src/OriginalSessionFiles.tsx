import {For,Show,createResource,createSignal,onCleanup} from 'solid-js';
import {Icon} from '@opencode-ai/ui/icon';
import type {AgentSession} from './agent-sessions';
import type {FileContent} from './contracts';
import {OriginalSessionFilesApi,originalFilesObservation,type OriginalFileEntry} from './original-session-files';
import './original-session-files.css';

/** Existing admitted file history, next to the conversation that produced it. */
export default function OriginalSessionFiles(props:{session:AgentSession;contextKey:string;locale:string;onOpen:(file:FileContent)=>Promise<void>}){
 const client=new OriginalSessionFilesApi(),[expanded,setExpanded]=createSignal(false),[pending,setPending]=createSignal(''),[failed,setFailed]=createSignal(''),[changed,setChanged]=createSignal('');
 const en=()=>props.locale.startsWith('en'),key=()=>{const observation=originalFilesObservation(props.session);return observation?JSON.stringify([props.contextKey,observation]):undefined;};
 let alive=true,abort:AbortController|undefined;onCleanup(()=>{alive=false;abort?.abort();});
 const [catalog,{refetch}]=createResource(key,async()=>{abort?.abort();abort=new AbortController();setFailed('');setChanged('');setPending('');return client.list(props.session,abort.signal);});
 function revealAction(element:HTMLButtonElement){
  const basis=key(),frame=requestAnimationFrame(()=>{
   if(!alive||key()!==basis||!element.isConnected)return;
   const rect=element.getBoundingClientRect();let top=0,bottom=innerHeight;
   for(let parent=element.parentElement;parent;parent=parent.parentElement){if(getComputedStyle(parent).overflowY!=='visible'){const box=parent.getBoundingClientRect();top=Math.max(top,box.top);bottom=Math.min(bottom,box.bottom);}}
   if(rect.top<top||rect.bottom>bottom)element.scrollIntoView({block:'nearest',inline:'nearest'});
  });
  onCleanup(()=>cancelAnimationFrame(frame));
 }
 async function open(entry:OriginalFileEntry,acceptCurrent=false){
  if(pending())return;const basis=key(),value=catalog();if(!value)return;setPending(entry.diff_ref.id);setFailed('');
  try{const result=await client.read(value,entry,abort?.signal);if(!alive||key()!==basis)return;if(result.changed&&!acceptCurrent){setChanged(entry.diff_ref.id);return;}await props.onOpen(result.file);if(alive&&key()===basis)setChanged('');}
  catch{if(alive&&key()===basis)setFailed(entry.diff_ref.id);}
  finally{if(alive&&key()===basis)setPending('');}
 }
 return <Show when={key()&&(catalog.error||!catalog.loading&&catalog()?.files.length)}><section class="as-original-files" aria-label={en()?'Conversation files':'会话文件'}>
  <Show when={catalog.error}><div class="as-original-files-check" role="status"><span>{en()?'Files could not be checked.':'文件暂未核对完成。'}</span><button type="button" ref={revealAction} onClick={()=>void refetch()}>{en()?'Check files':'核对文件'}</button></div></Show>
  <Show when={!catalog.loading&&!catalog.error&&catalog()?.files.length}><strong>{en()?'Conversation files':'会话文件'}</strong><For each={expanded()?catalog()!.files:catalog()!.files.slice(-3)}>{entry=><div class="as-original-file">
   <button type="button" class="as-original-file-open" title={entry.path} disabled={!!pending()} aria-label={(en()?'Open file: ':'打开文件：')+entry.path} onClick={()=>void open(entry)}><Icon name="open-file"/><span>{entry.path.split('/').at(-1)}</span><small>{pending()===entry.diff_ref.id?(en()?'Checking…':'核对中…'):entry.status==='added'?(en()?'Created':'已创建'):(en()?'Updated':'已修改')}</small></button>
   <Show when={changed()===entry.diff_ref.id}><p role="status">{en()?'This file has later changes.':'文件已有后续修改。'} <button type="button" ref={revealAction} disabled={!!pending()} onClick={()=>void open(entry,true)}>{en()?'Open current version':'打开当前版本'}</button></p></Show>
   <Show when={failed()===entry.diff_ref.id}><p role="status">{en()?'This file could not be opened. Your file is kept.':'文件暂未打开，原件保留。'} <button type="button" ref={revealAction} disabled={!!pending()} onClick={()=>void refetch()}>{en()?'Check files':'重新核对文件'}</button></p></Show>
  </div>}</For><Show when={catalog()!.files.length>3}><button type="button" class="as-original-files-more" onClick={()=>setExpanded(v=>!v)}>{expanded()?(en()?'Show fewer':'收起'):(en()?'All files':'全部文件')+' ('+catalog()!.files.length+')'}</button></Show></Show>
 </section></Show>;
}
