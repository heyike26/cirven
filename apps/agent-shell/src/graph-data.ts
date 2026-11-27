import type { NodeState } from './contracts';
import {AuthoredUIError} from './authored-ui-copy.ts';
export type GraphNode = { node_id:string;role:string;dependency_node_ids:string[];kind?:string;parent_id?:string|null;description?:string;inputs?:string[];outputs?:string[];acceptance?:string[];risks?:string[];capabilities?:string[] };

export function graphElements(nodes: GraphNode[], states: NodeState[]) {
  const ids=new Set(nodes.map(node=>node.node_id));
  const stateById=new Map(states.map(state=>[state.node_id,state]));
  if(ids.size!==nodes.length)throw new Error('任务图节点重复');
  const result: { data: { id: string; label?: string; status?: string; source?: string; target?: string;kind?:string } }[]=[];
  for(const node of nodes){
    const status=stateById.get(node.node_id);
    result.push({data:{id:node.node_id,label:node.node_id+'\n'+node.role,status:status?.latest_run?.verification_status??status?.status??'UNKNOWN'}});
    for(const dependency of node.dependency_node_ids){
      if(!ids.has(dependency))throw new AuthoredUIError('任务图缺少依赖节点 {dependency}',{dependency});
      if(dependency===node.node_id)throw new Error('任务图不能自依赖');
      result.push({data:{id:`edge:${dependency.length}:${dependency}:${node.node_id}`,source:dependency,target:node.node_id}});
    }
  }
  return result;
}
