import type {TreeEntry} from './contracts';
import type {GitStatusEntry} from '@pierre/trees';

export type ExplorerEntry=TreeEntry&{change:'UNCHANGED'|'ADDED'|'MODIFIED'|'DELETED'|'UNVERIFIED';deleted:boolean;comparison_basis:string;sha256:string|null;verification:string;reason?:string;restored:boolean};
export type ExplorerRef={id:string;revision:number;sha256:string};
export type ExplorerPage={entries:ExplorerEntry[];total:number;next_offset:number|null;baseline_ref:ExplorerRef;observation_ref:ExplorerRef;path:string;observed_at:string;scope:'LAZY_DIRECTORY_ONLY';directory_missing:boolean;hash_bytes:number;hash_budget_bytes:number;atomic_filesystem_snapshot:false};
export const treePath=(entry:ExplorerEntry)=>entry.type==='directory'?entry.path+'/':entry.path;
export const directoryPath=(path:string)=>path.endsWith('/')?path.slice(0,-1):path;
export const canOpen=(entry:ExplorerEntry|undefined):entry is ExplorerEntry=>!!entry&&entry.type==='file'&&entry.readable&&!entry.deleted;
export const changeLabel=(entry:ExplorerEntry)=>({UNCHANGED:'与首次观察一致',ADDED:'新增',MODIFIED:'已修改',DELETED:'已删除',UNVERIFIED:'未核验'}[entry.change]);
// The OSS renderer calls this decoration gitStatus. These are local snapshot
// comparisons only: no Git process, repository, or Git diff provider is used.
export function statusDecorations(rows:ExplorerEntry[]):GitStatusEntry[]{const result:GitStatusEntry[]=[];for(const entry of rows){const status=entry.change==='ADDED'?'added':entry.change==='MODIFIED'?'modified':entry.change==='DELETED'?'deleted':undefined;if(status)result.push({path:treePath(entry),status});}return result;}
type ReadPage=(pid:string,wid:string,path:string,offset:number,observationId:string|undefined,signal:AbortSignal)=>Promise<ExplorerPage>;
export class ExplorerStore{
 readonly pages=new Map<string,ExplorerPage>();readonly errors=new Map<string,string>();readonly loading=new Set<string>();
 private epoch=0;private abort=new AbortController();private reader:ReadPage;private changed:()=>void;private pid='';private wid='';
 constructor(reader:ReadPage,changed:()=>void){this.reader=reader;this.changed=changed;}
 bind(pid:string,wid:string){this.abort.abort();this.abort=new AbortController();this.epoch++;this.pid=pid;this.wid=wid;this.pages.clear();this.errors.clear();this.loading.clear();this.changed();}
 rows(){return [...this.pages.values()].flatMap(page=>page.entries);}
 entry(path:string){const actual=directoryPath(path);return this.rows().find(entry=>entry.path===actual);}
 async load(path='',append=false){
  path=directoryPath(path);if(this.loading.has(path)||!this.pid||!this.wid)return;
  const old=this.pages.get(path);if(append&&(!old||old.next_offset===null))return;
  const epoch=this.epoch,signal=this.abort.signal;this.loading.add(path);this.errors.delete(path);this.changed();
  try{const value=await this.reader(this.pid,this.wid,path,append?old!.next_offset!:0,append?old!.observation_ref.id:undefined,signal);if(epoch!==this.epoch||signal.aborted)return;
   if(value.path!==path||value.scope!=='LAZY_DIRECTORY_ONLY')throw new Error('目录观察范围与请求不符');
   if(append&&(value.observation_ref.id!==old!.observation_ref.id||value.baseline_ref.sha256!==old!.baseline_ref.sha256||value.total!==old!.total))throw new Error('目录分页版本已变化，请刷新后重试');
   if(value.entries.some(entry=>entry.path.slice(0,entry.path.lastIndexOf('/')<0?0:entry.path.lastIndexOf('/'))!==path))throw new Error('目录分页包含范围外条目');
   const entries=append?[...old!.entries,...value.entries]:value.entries;if(new Set(entries.map(entry=>entry.path)).size!==entries.length)throw new Error('目录分页包含重复条目');
   this.pages.set(path,{...value,entries});
   for(const entry of value.entries){if(entry.deleted||entry.type!=='directory')for(const cached of this.pages.keys()){if(cached===entry.path||cached.startsWith(entry.path+'/')){this.pages.delete(cached);this.errors.delete(cached);}}}
  }catch(error){if(epoch===this.epoch&&!signal.aborted)this.errors.set(path,error instanceof Error?error.message:String(error));}
  finally{if(epoch===this.epoch){this.loading.delete(path);this.changed();}}
 }
 stop(){this.abort.abort();this.epoch++;this.loading.clear();}
}
