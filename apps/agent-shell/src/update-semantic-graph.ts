import type cytoscape from 'cytoscape';
import type {DiscoveryGraph} from './discovery-contracts';
import {semanticElements} from './semantic-graph.ts';
export function updateSemanticGraph(core:cytoscape.Core,graph:DiscoveryGraph){
 const elements=semanticElements(graph),ids=new Set(elements.map(element=>element.data.id));
 core.batch(()=>{core.elements().filter(element=>!ids.has(element.id())).remove();for(const element of elements){const current=core.getElementById(element.data.id);if(current.length){if('source' in element.data&&'target' in element.data&&(current.data('source')!==element.data.source||current.data('target')!==element.data.target))current.move({source:element.data.source,target:element.data.target});current.data(element.data);}else core.add(element);}});
 return graph.kind+'|'+elements.map(element=>`${element.data.id}:${'source' in element.data?element.data.source:''}:${'target' in element.data?element.data.target:''}`).join('|');
}
