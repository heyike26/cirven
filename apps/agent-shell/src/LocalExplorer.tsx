import {t as uiText,uiLocale} from './ui-language';
import {createEffect,createSignal,For,onCleanup,onMount,Show,untrack} from 'solid-js';
import {FileTree} from '@pierre/trees';
import {Tooltip} from '@kobalte/core/tooltip';
import {Button} from '@opencode-ai/ui/button';
import {Icon} from '@opencode-ai/ui/icon';
import {api} from './api';
import {canOpen,changeLabel,directoryPath,ExplorerStore,statusDecorations,treePath,type ExplorerEntry} from './explorer';
import type {Workspace} from './contracts';
import {ExplorerModel} from './explorer-model';
import './explorer-layout.css';

const shadowCSS=`:host{height:100%;min-height:0;display:block;color-scheme:light;--trees-bg-override:transparent;--trees-fg-override:#344552;--trees-fg-muted-override:#667580;--trees-font-family-override:var(--font-family-sans);--trees-font-size-override:13px;--trees-selected-bg-override:#d7e5ef;--trees-selected-fg-override:#243e50;--trees-bg-muted-override:#ffffff95;--trees-border-color-override:#cad4db;--trees-accent-override:#4c728c;--trees-status-added-override:#26724a;--trees-status-modified-override:#806000;--trees-status-deleted-override:#ad3440;--trees-padding-inline-override:0px;--trees-item-padding-x-override:4px;--trees-item-row-gap-override:4px;--trees-item-margin-x-override:2px;}[data-item-git-status=deleted] [data-item-section=content]{text-decoration:line-through;text-decoration-thickness:1px;}[data-item-section=content]{flex:1 1 0;min-width:0;}[data-item-section=git],[data-item-section=decoration]:empty{display:none;}[data-item-section=decoration]{font-size:11px;flex:0 0 auto;}button[data-type=item]{transition:background 140ms ease-out;}button[data-type=item][aria-disabled=true]{cursor:not-allowed;}button[data-type=item]:focus-visible{outline:2px solid #527d9b;outline-offset:-2px;}`;
export default function LocalExplorer(props:{workspace:Workspace;selection:string[];onSelection:(paths:string[])=>void;onOpen:(entry:ExplorerEntry)=>void}){
 let host!:HTMLDivElement;let tree:FileTree|undefined;let model:ExplorerModel|undefined;let suppress=false;let disposeSubscribe:()=>void=()=>{};let mutation:MutationObserver|undefined;let restoreExpanded:string[]=[];let markQueued=false;
 const [version,setVersion]=createSignal(0);const [query,setQuery]=createSignal('');const [expanded,setExpanded]=createSignal<string[]>([]);
 const store=new ExplorerStore((pid,wid,path,offset,id,signal)=>api.explorer(pid,wid,path,offset,id,signal),()=>untrack(()=>{setVersion(value=>value+1);model?.schedule();}));
 const rows=()=>{version();return store.rows();};const pending=()=>{version();return [...store.loading];};const errors=()=>{version();return [...store.errors];};
 const pages=()=>{version();return [...store.pages.values()].filter(page=>page.next_offset!==null&&(page.path===''||expanded().includes(page.path+'/')));};
 function readExpanded(){return model?.expanded()??[];}
 function decoration(entry:ExplorerEntry){return entry.change==='UNVERIFIED'?{text:'?',title:uiText('未核验：{reason}',{reason:entry.reason??entry.verification})}:entry.change==='DELETED'?{text:uiText('删除'),title:uiText('已删除的历史条目；无法打开原件')}:entry.change==='ADDED'?{text:uiText('新增'),title:uiText('相对首次观察新增')}:entry.change==='MODIFIED'?{text:uiText('修改'),title:uiText('实际字节 SHA 已变化')}:null;}
 function markRows(){
  const root=tree?.getFileTreeContainer()?.shadowRoot;if(!root)return;
  for(const node of root.querySelectorAll<HTMLElement>('[data-item-path]')){
   const entry=store.entry(node.dataset.itemPath??'');if(!entry)continue;
   const title=`${entry.path} · ${uiText(changeLabel(entry))}${entry.reason?' · '+entry.reason:''} · ${entry.verification}`;
   if(node.title!==title)node.title=title;
   const marker=node.querySelector<HTMLElement>('[data-item-section=decoration]>span'),value=decoration(entry);
   if(marker&&value){if(marker.textContent!==value.text)marker.textContent=value.text;if(marker.title!==value.title)marker.title=value.title;}
   if(entry.type!=='directory'&&!canOpen(entry)){if(node.getAttribute('aria-disabled')!=='true')node.setAttribute('aria-disabled','true');}
   else if(node.hasAttribute('aria-disabled'))node.removeAttribute('aria-disabled');
  }
 }
 function loadExpanded(){if(!tree)return;for(const path of readExpanded()){if(!store.pages.has(directoryPath(path))&&!store.loading.has(directoryPath(path))&&!store.errors.has(directoryPath(path)))void store.load(path);}}
 function scheduleMarks(){if(markQueued)return;markQueued=true;queueMicrotask(()=>{markQueued=false;markRows();});}
 async function refresh(){const open=readExpanded();await store.load();for(const path of open){const entry=store.entry(path);if(entry?.type==='directory')await store.load(path);}}
 onMount(()=>{
  try{const saved=JSON.parse(localStorage.getItem(`eeas-explorer-expanded:${props.workspace.project_id}:${props.workspace.id}`)??'[]');if(Array.isArray(saved))restoreExpanded=saved.filter((path:unknown):path is string=>typeof path==='string'&&path.endsWith('/')&&path.length<4096).slice(0,200);}catch{}
  tree=new FileTree({paths:[],flattenEmptyDirectories:false,initialExpansion:'closed',dragAndDrop:false,renaming:false,search:false,stickyFolders:true,itemHeight:30,unsafeCSS:shadowCSS,
   onSelectionChange(paths){if(suppress||model?.applying)return;const allowed=paths.filter(path=>canOpen(store.entry(path)));props.onSelection(allowed.map(directoryPath));const selected=allowed.at(-1),entry=selected?store.entry(selected):undefined;if(canOpen(entry))props.onOpen(entry);},
   renderRowDecoration({item}){const entry=store.entry(item.path);return entry?decoration(entry):null;}
  });tree.render({containerWrapper:host});
  const blockUnavailable=(event:Event)=>{const button=event.composedPath().find(node=>node instanceof HTMLElement&&node.hasAttribute('data-item-path')) as HTMLElement|undefined;const entry=button?store.entry(button.dataset.itemPath??''):store.entry(tree?.getFocusedPath()??'');if(entry&&entry.type!=='directory'&&!canOpen(entry)&&(event.type==='click'||['Enter',' '].includes((event as KeyboardEvent).key))){event.preventDefault();event.stopImmediatePropagation();}};
  host.addEventListener('click',blockUnavailable,true);host.addEventListener('keydown',blockUnavailable,true);onCleanup(()=>{host.removeEventListener('click',blockUnavailable,true);host.removeEventListener('keydown',blockUnavailable,true);});
  model=new ExplorerModel(tree,()=>store.rows(),()=>{setExpanded(readExpanded());props.onSelection(tree!.getSelectedPaths().filter(path=>canOpen(store.entry(path))).map(directoryPath));scheduleMarks();queueMicrotask(loadExpanded);},restoreExpanded);
  mutation=new MutationObserver(scheduleMarks);const root=tree.getFileTreeContainer()?.shadowRoot;if(root)mutation.observe(root,{childList:true,subtree:true,attributes:true,attributeFilter:['data-item-path','data-item-type','data-item-git-status','role','aria-selected','aria-expanded']});
  disposeSubscribe=tree.subscribe(()=>{if(suppress||model?.applying)return;model?.observe();const open=readExpanded();setExpanded(open);queueMicrotask(loadExpanded);try{localStorage.setItem(`eeas-explorer-expanded:${props.workspace.project_id}:${props.workspace.id}`,JSON.stringify(open));}catch{}});
  model.schedule();void store.load();
 });
 createEffect(()=>{const workspace=props.workspace;untrack(()=>{store.bind(workspace.project_id,workspace.id);setExpanded([]);setQuery('');tree?.setSearch(null);void store.load();});});
 createEffect(()=>{const wanted=props.selection;if(!tree)return;suppress=true;try{for(const path of tree.getSelectedPaths())if(!wanted.includes(directoryPath(path)))tree.getItem(path)?.deselect();}finally{suppress=false;}});
 createEffect(()=>{uiLocale();scheduleMarks();});
 onCleanup(()=>{store.stop();disposeSubscribe();mutation?.disconnect();model?.stop();tree?.cleanUp();tree=undefined;});
 return <section class="as-explorer" aria-label={uiText("工作文件树")}><header class="as-tree-header"><span class="as-tree-heading"><strong>{uiText("工作文件")}</strong><Tooltip openDelay={400} gutter={8} flip slide fitViewport><Tooltip.Trigger type="button" class="as-tree-help" aria-label={uiText("目录状态说明")}><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7"/><path d="M8 7.5a2 2 0 0 1 4 .5c0 1.5-2 1.5-2 3M10 14h.01"/></svg></Tooltip.Trigger><Tooltip.Portal><Tooltip.Content class="as-explorer-status-help">{uiText("绿色为新增，黄色为修改，红色删除线为已删除。问号表示尚未核验。")}</Tooltip.Content></Tooltip.Portal></Tooltip></span><button aria-label={uiText("刷新文件树与已展开目录")} disabled={pending().length>0} onClick={()=>void refresh()}><Icon name="reset"/></button></header><label class="as-explorer-search"><Icon name="magnifying-glass"/><input aria-label={uiText("搜索已展开的文件树")} placeholder={uiText("搜索已加载文件")} value={query()} onInput={event=>{setQuery(event.currentTarget.value);tree?.setSearch(event.currentTarget.value||null);}}/></label><div ref={host} class="as-explorer-host"/><Show when={pending().length}><p class="as-explorer-note" role="status">{uiText("正在读取 ")}{pending().map(path=>path||uiText('工作目录')).join('、')}…</p></Show><Show when={!rows().length&&!pending().length&&!errors().length}><p class="as-explorer-note">{uiText("当前目录没有条目。")}</p></Show><For each={errors()}>{([path,error])=><div class="as-explorer-warning" role="alert"><p>{path||uiText("工作目录")}：{error}</p><Button size="small" variant="ghost" onClick={()=>void store.load(path)}>{uiText("重新读取")}</Button></div>}</For><For each={pages()}>{page=><Button size="small" variant="ghost" class="as-explorer-more" onClick={()=>void store.load(page.path,true)}>{uiText("加载更多 · ")}{page.path||uiText("工作目录")} ({page.entries.length}/{page.total})</Button>}</For></section>;
}
