import type {FileTree} from '@pierre/trees';
import {statusDecorations,treePath,type ExplorerEntry} from './explorer.ts';
/** Coalesce data updates after the upstream pointer/selection event completes.
 * Loading notifications never rebuild the OSS model. User collapse after a
 * pending request wins over late children; active search refreshes its matches.
 */
export class ExplorerModel{
 private tree:FileTree;private rows:()=>ExplorerEntry[];private updated:()=>void;private paths:string[]=[];private restore:string[];private open:string[]=[];private queued=false;private stopped=false;applying=false;
 constructor(tree:FileTree,rows:()=>ExplorerEntry[],updated:()=>void,restore:string[]=[]){this.tree=tree;this.rows=rows;this.updated=updated;this.restore=restore;}
 observe(){if(this.applying||this.stopped)return;this.open=this.paths.filter(path=>{const item=this.tree.getItem(path);return item&&'isExpanded' in item&&item.isExpanded();});}
 expanded(){return this.open;}
 schedule(){if(this.queued||this.stopped)return;this.queued=true;queueMicrotask(()=>{this.queued=false;if(!this.stopped)this.apply();});}
 private apply(){
  const rows=this.rows(),paths=rows.map(treePath),changed=JSON.stringify(paths)!==JSON.stringify(this.paths);this.applying=true;
  try{
   if(changed){const restored=this.restore.filter(path=>paths.includes(path));this.restore=this.restore.filter(path=>!restored.includes(path));const wanted=[...new Set([...this.open,...restored])].filter(path=>paths.includes(path)),selected=this.tree.getSelectedPaths(),focused=this.tree.getFocusedPath(),query=this.tree.isSearchOpen()?this.tree.getSearchValue():null;
    // resetPaths does not recompute matches for newly added paths under an
    // unchanged query. Close/reopen under ownership suppression to refresh.
    if(query!==null)this.tree.setSearch(null);
    this.tree.resetPaths(paths,{initialExpandedPaths:wanted});this.paths=paths;
    for(const path of wanted){const item=this.tree.getItem(path);if(item&&'expand' in item)item.expand();}
    if(query!==null)this.tree.setSearch(query);
    for(const path of selected){const row=rows.find(entry=>treePath(entry)===path);if(row?.type==='file'&&row.readable&&!row.deleted)this.tree.getItem(path)?.select();}
    if(focused&&this.tree.getItem(focused))this.tree.focusPath(focused);
   }
   this.tree.setGitStatus(statusDecorations(rows));
  }finally{this.applying=false;}
  this.observe();this.updated();
 }
 stop(){this.stopped=true;}
}
