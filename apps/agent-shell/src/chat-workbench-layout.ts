export type WorkspaceView = 'chat' | 'split' | 'workbench';
export const WORKSPACE_VIEW_KEY = 'eeas-workspace-view-v2';

export function workspaceView(value: string | null): WorkspaceView {
  return value === 'split' || value === 'workbench' ? value : 'chat';
}

export function defaultConversationWidth(viewport: number, files: number): number {
  return Math.round(Math.min(800, Math.max(420, (viewport - files - 12) * .6)));
}

type ReadingPosition={node:HTMLElement;top:number;left:number;bottom:boolean;anchor?:HTMLElement;offset:number;intentVersion:number};
const readingPositions=new WeakMap<HTMLElement,ReadingPosition>();
const readingIntentVersions=new WeakMap<HTMLElement,number>();
const readingOwners=new WeakMap<HTMLElement,()=>boolean>();
export function readingIntentChanged(node:HTMLElement){readingIntentVersions.set(node,(readingIntentVersions.get(node)??0)+1);}
export function protectLayoutReading(node:HTMLElement,following:()=>boolean){
  readingOwners.set(node,following);
  return ()=>{if(readingOwners.get(node)===following)readingOwners.delete(node);};
}
function layoutTop(node:HTMLElement):number {
  let top=0;
  for(let current:HTMLElement|null=node;current;){
    top+=current.offsetTop;
    const parent=current.offsetParent as HTMLElement|null;
    if(parent)top+=parent.clientTop;
    current=parent;
  }
  return top;
}
export function layoutReadingOffset(anchor:HTMLElement,node:HTMLElement):number {
  let offset=layoutTop(anchor)-layoutTop(node)-node.scrollTop;
  for(let parent=anchor.parentElement;parent&&parent!==node;parent=parent.parentElement)offset-=parent.scrollTop;
  return offset;
}
export function preserveLayoutReading(root?: HTMLElement,onRestored?:()=>void,current:()=>boolean=()=>true): () => void {
  const nodes = [...root?.querySelectorAll<HTMLElement>('.as-conversation,.as-inspector,.as-workspace-body') ?? []];
  for (const node of nodes) {
    if (node.clientHeight > 0) {
      const rect = node.getBoundingClientRect();
      const anchor = [...node.querySelectorAll<HTMLElement>('article,.as-user-message,.as-native-message')]
        .find(item => item.getBoundingClientRect().bottom > rect.top + 4);
      readingPositions.set(node,{node, top: node.scrollTop, left: node.scrollLeft, bottom: node.scrollHeight - node.clientHeight - node.scrollTop < 8, anchor, offset: anchor ? layoutReadingOffset(anchor,node) : 0,intentVersion:readingIntentVersions.get(node)??0});
    }
  }
  return () => requestAnimationFrame(() => requestAnimationFrame(() => {
    if(!current())return;
    for (const node of nodes) {
      const saved=readingPositions.get(node);
      if (!saved || !saved.node.isConnected || !saved.node.clientHeight) continue;
      if(saved.intentVersion!==(readingIntentVersions.get(node)??0)||readingOwners.get(node)?.())continue;
      saved.node.scrollTop = saved.bottom ? saved.node.scrollHeight : saved.top;
      saved.node.scrollLeft = saved.left;
      if (!saved.bottom && saved.anchor?.isConnected && saved.node.contains(saved.anchor)) {
        saved.node.scrollTop += layoutReadingOffset(saved.anchor,saved.node) - saved.offset;
      }
    }
    onRestored?.();
  }));
}
