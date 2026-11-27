import {createEffect,createSignal,For,onCleanup,Show,untrack} from 'solid-js';
import {api} from './api';
import type {Workspace} from './contracts';
import {ContextGraphClient,type ContextFolderRule} from './context-graph';

/** Directory metadata is listed only when requested. Inheritance covers the
 * registered file index; this picker neither indexes nor opens file bodies. */
export default function ContextFolders(props:{client:ContextGraphClient;scopeId:string;conversationId?:string;workspace?:Workspace;en:boolean;busy:boolean;pending:boolean;version:number;change:(work:()=>Promise<unknown>)=>void}){
  const [rules,setRules]=createSignal<ContextFolderRule[]>([]),[next,setNext]=createSignal<number|null>(null),[error,setError]=createSignal('');
  const [path,setPath]=createSignal(''),[folders,setFolders]=createSignal<{name:string;path:string}[]>([]),[folderNext,setFolderNext]=createSignal<number|null>(null),[listing,setListing]=createSignal(false),[opened,setOpened]=createSignal(false);
  const [range,setRange]=createSignal<ContextFolderRule['range_kind']>('CURRENT_DIRECTORY');
  let ruleEpoch=0,browseEpoch=0;
  async function load(more=false){const own=++ruleEpoch,sid=props.scopeId;try{const page=await props.client.folderRules(sid,more?next()??0:0);if(own!==ruleEpoch||sid!==props.scopeId)return;setRules(old=>more?[...old,...page.items]:page.items);setNext(page.next_offset);}catch{if(own===ruleEpoch)setError(props.en?'Could not load directory links.':'暂时无法读取目录关联。');}}
  async function browse(value:string,more=false){
    const ws=props.workspace;if(!ws)return;const own=++browseEpoch;setListing(true);setError('');
    try{const page=await api.request<{entries:{name:string;path:string;type:string;readable:boolean}[];next_offset:number|null}>(api.workspacePath(ws.project_id,ws.id)+'/tree?path='+encodeURIComponent(value)+'&offset='+(more?folderNext()??0:0));
      if(own!==browseEpoch||ws!==props.workspace)return;
      setPath(value);setFolders(old=>more?[...old,...page.entries.filter(row=>row.type==='directory'&&row.readable)]:page.entries.filter(row=>row.type==='directory'&&row.readable));setFolderNext(page.next_offset);
    }catch{if(own===browseEpoch)setError(props.en?'This directory is unavailable. The previous selection is kept.':'该目录暂不可用，已保留上次选择。');}finally{if(own===browseEpoch)setListing(false);}
  }
  createEffect(()=>{props.version;props.scopeId;setError('');void load();});
  createEffect(()=>{const ws=props.workspace;untrack(()=>{setPath('');setFolders([]);setFolderNext(null);browseEpoch++;if(ws&&opened())void browse('');});});
  onCleanup(()=>{ruleEpoch++;browseEpoch++;});
  function link(){const ws=props.workspace,sid=props.scopeId,cid=props.conversationId??null,value=path(),mode=range();if(!ws||listing())return;
    props.change(async()=>{const {folder_ref}=await props.client.resolveFolder(ws,value?value.split('/'):[]);
      const previous=rules().find(row=>row.scope_id===sid&&row.conversation_id===cid&&JSON.stringify(row.folder_ref)===JSON.stringify(folder_ref));
      return props.client.folderRule(sid,folder_ref,mode,cid,previous);});
  }
  return <details onToggle={event=>{setOpened(event.currentTarget.open);if(event.currentTarget.open&&props.workspace&&!folders().length&&!listing())void browse(path());}}><summary>{props.en?'Directory links':'目录关联'}</summary>
    <Show when={error()}><p role="alert">{error()}</p></Show>
    <p class="as-context-muted">{props.en?'Includes registered saved-file sources only. File exclusions take priority.':'只继承已登记的保存文件来源；手动排除优先。'}</p>
    <For each={rules()}>{row=><article class="as-context-item"><p class="as-context-path">{row.folder_ref.segments.join('/')||'/'}</p><p class="as-context-muted">{row.range_kind==='CURRENT_DIRECTORY'?(props.en?'Current directory':'仅当前目录'):(props.en?'Including subdirectories':'包含子目录')} · {row.state==='ACTIVE'?(props.en?'Active':'已采用'):(props.en?'Inactive':'未采用')}{!row.source_current?' · '+(props.en?'Directory changed':'目录已变化'):''}</p><button disabled={props.busy||props.pending||row.state!=='ACTIVE'&&!row.source_current} onClick={()=>props.change(()=>props.client.folderRule(row.scope_id,row.folder_ref,row.range_kind,row.conversation_id,row,row.state==='ACTIVE'?'RETRACTED':'ACTIVE'))}>{row.state==='ACTIVE'?(props.en?'Remove directory link':'移除目录关联'):(props.en?'Restore directory link':'恢复目录关联')}</button></article>}</For>
    <Show when={next()!==null}><button disabled={props.busy} onClick={()=>void load(true)}>{props.en?'More directory links':'更多目录关联'}</button></Show>
    <Show when={props.workspace} fallback={<p>{props.en?'Select a local workspace to link a directory.':'选择本地工作目录后可关联文件夹。'}</p>}>
      <div class="as-context-folder-browser" aria-busy={listing()}><p class="as-context-path">{path()||props.workspace?.root}</p><div class="as-context-item-actions"><button disabled={listing()||!path()} onClick={()=>void browse(path().split('/').slice(0,-1).join('/'))}>{props.en?'Parent directory':'上级目录'}</button><button disabled={listing()} onClick={()=>void browse(path())}>{props.en?'Refresh directories':'刷新目录'}</button></div>
      <div class="as-context-folder-list"><For each={folders()}>{row=><button disabled={listing()||props.busy} onClick={()=>void browse(row.path)}>{row.name}/</button>}</For></div><Show when={folderNext()!==null}><button disabled={listing()} onClick={()=>void browse(path(),true)}>{props.en?'More directories':'更多目录'}</button></Show></div>
      <label>{props.en?'Include':'包含范围'}<select disabled={props.busy} value={range()} onChange={event=>setRange(event.currentTarget.value as ContextFolderRule['range_kind'])}><option value="CURRENT_DIRECTORY">{props.en?'Current directory only':'仅当前目录'}</option><option value="INCLUDING_DESCENDANTS">{props.en?'Directory and subdirectories':'当前目录及子目录'}</option></select></label><button disabled={props.busy||props.pending||listing()} onClick={link}>{props.en?'Link selected directory':'关联所选目录'}</button>
    </Show>
  </details>;
}
