import type {GraphNode} from './graph-data';
import {boundedGraphLabel} from './semantic-layout.ts';
import elkWorkerUrl from '../../../packages/adapters/diagram_layout_assets/elk-worker.min.js?url';

export const TASK_NODE_WIDTH=232;
export const TASK_NODE_HEIGHT=96;
export const TASK_CAPTION_WIDTH=200;
export const TASK_CAPTION_LINES=3;
export const MAX_TASK_LAYOUT_NODES=2000;
export const MAX_TASK_LAYOUT_EDGES=16000;

export type TaskPosition={x:number;y:number};
export type TaskLayoutEdge={id:string;source:string;target:string};
export type TaskLayoutDiagnostic={code:string;node_ids?:string[];source?:string;target?:string};
export type TaskDependencyGraph={
  source:'DEPENDENCIES'|'EXPLICIT_EDGES';
  node_ids:string[];
  roots:string[];
  dependencies:Record<string,string[]>;
  dependents:Record<string,string[]>;
  ranks:Record<string,number>;
  edges:TaskLayoutEdge[];
};
export type TaskLayoutOptions={
  direction?:'RIGHT'|'DOWN';
  /** Supply only the intended relation (for example explicit parents in a mind map). */
  edges?:readonly {source:string;target:string}[];
  signal?:AbortSignal;
  timeoutMs?:number;
};
export type TaskLayoutResult={
  positions:Record<string,TaskPosition>;
  graph:TaskDependencyGraph;
  bounds:{width:number;height:number};
};

export class TaskGraphLayoutError extends Error {
  readonly diagnostics:readonly TaskLayoutDiagnostic[];
  constructor(diagnostics:readonly TaskLayoutDiagnostic[]){
    super(diagnostics[0]?.code??'TASK_GRAPH_LAYOUT_FAILED');
    this.name='TaskGraphLayoutError';
    this.diagnostics=diagnostics;
  }
}

const compare=(a:string,b:string)=>a<b?-1:a>b?1:0;
const record=<T>():Record<string,T>=>Object.create(null) as Record<string,T>;
const validId=(id:unknown):id is string=>typeof id==='string'&&id.length>0&&id.length<=512;
const edgeId=(source:string,target:string)=>`edge:${source.length}:${source}:${target}`;

/** Only supplied dependency_node_ids affect ranks. Pass task nodes, not question-impact edges. */
export function taskDependencyGraph(nodes:readonly GraphNode[],edges?:TaskLayoutOptions['edges']):TaskDependencyGraph {
  if(!Array.isArray(nodes)||nodes.length>MAX_TASK_LAYOUT_NODES)throw new TaskGraphLayoutError([{code:'NODE_LIMIT_EXCEEDED'}]);
  const diagnostics:TaskLayoutDiagnostic[]=[],ids=new Set<string>();
  for(const node of nodes){
    if(!node||!validId(node.node_id)){diagnostics.push({code:'INVALID_NODE_ID'});continue;}
    if(ids.has(node.node_id))diagnostics.push({code:'DUPLICATE_NODE_ID',node_ids:[node.node_id]});
    ids.add(node.node_id);
  }
  if(diagnostics.length)throw new TaskGraphLayoutError(diagnostics);
  const node_ids=[...ids].sort(compare),dependencies=record<string[]>(),dependents=record<string[]>(),ranks=record<number>();
  for(const id of node_ids){dependencies[id]=[];dependents[id]=[];ranks[id]=0;}
  const relations:{source:string;target:string}[]=[];
  if(edges!==undefined){
    if(!Array.isArray(edges)||edges.length>MAX_TASK_LAYOUT_EDGES)throw new TaskGraphLayoutError([{code:'EDGE_LIMIT_EXCEEDED'}]);
    for(const edge of edges){
      if(!edge||!validId(edge.source)||!validId(edge.target)){diagnostics.push({code:'INVALID_EDGE'});continue;}
      relations.push({source:edge.source,target:edge.target});
    }
  }else{
    let count=0;
    for(const node of nodes){
      if(!Array.isArray(node.dependency_node_ids)){diagnostics.push({code:'INVALID_DEPENDENCIES',node_ids:[node.node_id]});continue;}
      count+=node.dependency_node_ids.length;
      if(count>MAX_TASK_LAYOUT_EDGES)throw new TaskGraphLayoutError([{code:'EDGE_LIMIT_EXCEEDED'}]);
      for(const dependency of node.dependency_node_ids){
        if(!validId(dependency)){diagnostics.push({code:'INVALID_DEPENDENCY',node_ids:[node.node_id]});continue;}
        relations.push({source:dependency,target:node.node_id});
      }
    }
  }
  const unique=new Map<string,TaskLayoutEdge>();
  for(const {source,target} of relations){
    if(!ids.has(source)||!ids.has(target)){diagnostics.push({code:'MISSING_REFERENCE',source,target});continue;}
    unique.set(edgeId(source,target),{id:edgeId(source,target),source,target});
  }
  if(diagnostics.length)throw new TaskGraphLayoutError(diagnostics);
  const graphEdges=[...unique.values()].sort((a,b)=>compare(a.source,b.source)||compare(a.target,b.target));
  for(const edge of graphEdges){dependencies[edge.target].push(edge.source);dependents[edge.source].push(edge.target);}
  const roots=node_ids.filter(id=>dependencies[id].length===0),degree=new Map(node_ids.map(id=>[id,dependencies[id].length]));
  const queue=[...roots];
  for(let index=0;index<queue.length;index++){
    const id=queue[index];
    for(const next of dependents[id]){
      ranks[next]=Math.max(ranks[next],ranks[id]+1);
      degree.set(next,degree.get(next)!-1);
      if(degree.get(next)===0)queue.push(next);
    }
  }
  if(queue.length!==node_ids.length){
    // These are unresolved nodes, including any successors of a cycle; no fake ordering is returned.
    throw new TaskGraphLayoutError([{code:'CYCLIC_DEPENDENCIES',node_ids:node_ids.filter(id=>degree.get(id)!>0)}]);
  }
  return {source:edges===undefined?'DEPENDENCIES':'EXPLICIT_EDGES',node_ids,roots,dependencies,dependents,ranks,edges:graphEdges};
}

/** Conservative fallback metrics; a canvas measureText callback can supply the actual UI font. */
export function measureTaskCaption(text:string):number {
  let width=0;
  for(const character of text){
    if(/[\p{Mark}\u200d\ufe0f]/u.test(character))continue;
    width+=/\s/u.test(character)?4:/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}\p{Extended_Pictographic}]/u.test(character)?13:7;
  }
  return width;
}

/** A caption only: full original titles and descriptions remain in the caller's detail view. */
export function taskCaption(text:string,measure:(text:string)=>number=measureTaskCaption):string {
  const clipped=text.length>4096?text.slice(0,4096).replace(/[\ud800-\udbff]$/u,'')+'…':text;
  return boundedGraphLabel(clipped,TASK_CAPTION_WIDTH,TASK_CAPTION_LINES,measure);
}

function cancelled():Error {const error=new Error('Task graph layout cancelled');error.name='AbortError';return error;}

/** Run the pinned official worker protocol; retain only positions/bounds from its graph reply. */
export async function layoutTaskGraph(nodes:readonly GraphNode[],options:TaskLayoutOptions={}):Promise<TaskLayoutResult> {
  const signal=options.signal;
  if(signal?.aborted)throw cancelled();
  if(options.direction!==undefined&&options.direction!=='RIGHT'&&options.direction!=='DOWN')throw new TaskGraphLayoutError([{code:'INVALID_DIRECTION'}]);
  const graph=taskDependencyGraph(nodes,options.edges);
  if(!graph.node_ids.length)return {positions:record<TaskPosition>(),graph,bounds:{width:0,height:0}};
  const timeout=options.timeoutMs??8000;
  if(!Number.isFinite(timeout)||timeout<1||timeout>15000)throw new TaskGraphLayoutError([{code:'INVALID_TIMEOUT'}]);
  if(typeof Worker==='undefined')throw new TaskGraphLayoutError([{code:'WORKER_UNAVAILABLE'}]);
  const internalIds=new Map(graph.node_ids.map((id,index)=>[id,`n${index}`]));
  const input={
    direction:options.direction??'RIGHT',
    nodes:graph.node_ids.map(id=>({id:internalIds.get(id)!,width:TASK_NODE_WIDTH,height:TASK_NODE_HEIGHT})),
    edges:graph.edges.map((edge,index)=>({id:`e${index}`,sources:[internalIds.get(edge.source)!],targets:[internalIds.get(edge.target)!]})),
  };
  return new Promise<TaskLayoutResult>((resolve,reject)=>{
    let worker:Worker;
    // A local asset URL, not an engine import: parsing and layout stay off the UI thread.
    try{worker=new Worker(elkWorkerUrl);}
    catch{reject(new TaskGraphLayoutError([{code:'WORKER_UNAVAILABLE'}]));return;}
    let finished=false;
    const cleanup=()=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);worker.terminate();};
    const fail=(error:Error)=>{if(finished)return;finished=true;cleanup();reject(error);};
    const abort=()=>fail(cancelled());
    const timer=setTimeout(()=>fail(new TaskGraphLayoutError([{code:'LAYOUT_TIMEOUT'}])),timeout);
    signal?.addEventListener('abort',abort,{once:true});
    if(signal?.aborted){abort();return;}
    worker.onerror=event=>{event.preventDefault();fail(new TaskGraphLayoutError([{code:'ELK_LAYOUT_FAILED'}]));};
    let registered=false;
    worker.onmessage=({data:response}:MessageEvent)=>{
      if(finished)return;
      if(!response||response.error){fail(new TaskGraphLayoutError([{code:'ELK_LAYOUT_FAILED'}]));return;}
      if(!registered){
        if(response.id!==0){fail(new TaskGraphLayoutError([{code:'ELK_LAYOUT_FAILED'}]));return;}
        registered=true;
        try{worker.postMessage({id:1,cmd:'layout',graph:{id:'root',children:input.nodes,edges:input.edges},layoutOptions:{
          'elk.algorithm':'layered','elk.direction':input.direction,'elk.edgeRouting':'ORTHOGONAL',
          'elk.spacing.nodeNode':'48','elk.layered.spacing.nodeNodeBetweenLayers':'88',
          'elk.padding':'[top=24,left=24,bottom=24,right=24]','elk.randomSeed':'1',
          'elk.layered.considerModelOrder.strategy':'NODES_AND_EDGES','elk.layered.mergeEdges':'false',
        }});}catch{fail(new TaskGraphLayoutError([{code:'ELK_LAYOUT_FAILED'}]));}
        return;
      }
      const data=response.id===1?response.data:undefined;
      if(!data||!Array.isArray(data.edges)||data.edges.length!==graph.edges.length||data.edges.length>MAX_TASK_LAYOUT_EDGES){fail(new TaskGraphLayoutError([{code:'INVALID_LAYOUT_GEOMETRY'}]));return;}
      const children:unknown=data.children,positions=record<TaskPosition>();
      if(!Array.isArray(children)||children.length!==graph.node_ids.length||!Number.isFinite(data.width)||!Number.isFinite(data.height)||data.width<=0||data.height<=0||data.width>1e7||data.height>1e7){fail(new TaskGraphLayoutError([{code:'INVALID_LAYOUT_GEOMETRY'}]));return;}
      const remaining=new Map([...internalIds].map(([id,internal])=>[internal,id]));
      for(const child of children){
        const id=remaining.get(child?.id);
        if(id===undefined||![child.x,child.y].every(Number.isFinite)||child.x<0||child.y<0||child.x+TASK_NODE_WIDTH>data.width+0.01||child.y+TASK_NODE_HEIGHT>data.height+0.01){fail(new TaskGraphLayoutError([{code:'INVALID_LAYOUT_GEOMETRY'}]));return;}
        positions[id]={x:child.x+TASK_NODE_WIDTH/2,y:child.y+TASK_NODE_HEIGHT/2};remaining.delete(child.id);
      }
      if(remaining.size){fail(new TaskGraphLayoutError([{code:'INVALID_LAYOUT_GEOMETRY'}]));return;}
      finished=true;cleanup();resolve({positions,graph,bounds:{width:data.width,height:data.height}});
    };
    try{worker.postMessage({id:0,cmd:'register',algorithms:['layered']});}catch{fail(new TaskGraphLayoutError([{code:'ELK_LAYOUT_FAILED'}]));}
  });
}

/** Cytoscape preset positions use node centres; no fit, zoom, selection, or saved view is changed. */
export async function layoutTasks(nodes:readonly GraphNode[],options:TaskLayoutOptions={}):Promise<Record<string,TaskPosition>> {
  return (await layoutTaskGraph(nodes,options)).positions;
}
