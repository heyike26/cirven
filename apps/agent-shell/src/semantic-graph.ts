import type { DiscoveryGraph } from './discovery-contracts';

export function semanticElements(graph:DiscoveryGraph){
  const ids=new Set(graph.nodes.map(node=>node.id));
  if(ids.size!==graph.nodes.length)throw new Error('图节点 ID 重复');
  if(new Set(graph.edges.map(edge=>edge.id)).size!==graph.edges.length)throw new Error('图关系 ID 重复');
  const nodes=graph.nodes.map(node=>({data:{id:'node:'+node.id,original_id:node.id,label:node.label,origin:node.origin}}));
  const edges=graph.edges.map(edge=>{if(!ids.has(edge.source)||!ids.has(edge.target))throw new Error('图关系指向不存在的节点');return{data:{id:'edge:'+edge.id,original_id:edge.id,source:'node:'+edge.source,target:'node:'+edge.target,label:edge.relation}};});
  return[...nodes,...edges];
}
