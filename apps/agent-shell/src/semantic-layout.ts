/** Presentation only. No graph identities, relationships or saved views change. */
export type LayoutPosition={x:number;y:number};
export type LayoutNode={id:string;position:LayoutPosition;width:number;height:number};
export type LayoutBounds={x1:number;y1:number;w:number;h:number};
export const semanticLabelWidth=164;
export const semanticEdgeWidth=140;

/** Explicitly ellipsised UI caption; the source label remains available in detail. */
export function boundedGraphLabel(text:string,width:number,maxLines:number,measure:(text:string)=>number):string{
 if(!Number.isFinite(width)||width<=0||!Number.isInteger(maxLines)||maxLines<1)throw new RangeError('Invalid graph caption bounds');
 if(measure('…')>width)return '';
 const units=typeof Intl.Segmenter==='function'?Array.from(new Intl.Segmenter(undefined,{granularity:'grapheme'}).segment(text.replace(/\r\n?/g,'\n')),value=>value.segment):Array.from(text.replace(/\r\n?/g,'\n'));
 const lines:string[][]=[[]];let omitted=false;
 for(const unit of units){
  let line=lines.at(-1)!;
  if(unit==='\n'){if(lines.length===maxLines){omitted=true;break;}lines.push([]);continue;}
  if(measure(unit)>width){omitted=true;break;}
  if(measure(line.join('')+unit)>width){if(lines.length===maxLines){omitted=true;break;}lines.push([]);line=lines.at(-1)!;}
  line.push(unit);
 }
 if(omitted){const last=lines.at(-1)!;while(last.length&&measure(last.join('')+'…')>width)last.pop();last.push('…');}
 return lines.map(line=>line.join('')).join('\n');
}

/** Keep the existing breadthfirst ranks/order, but separate width from height. */
export function compactSemanticPositions(nodes:readonly LayoutNode[]):Record<string,LayoutPosition>{
 if(!nodes.length)return {};
 if(nodes.some(node=>![node.position.x,node.position.y,node.width,node.height].every(Number.isFinite)||node.width<=0||node.height<=0)||new Set(nodes.map(node=>node.id)).size!==nodes.length)throw new RangeError('Invalid graph layout geometry');
 const ranks:{y:number;nodes:LayoutNode[]}[]=[];
 for(const node of [...nodes].sort((a,b)=>a.position.y-b.position.y||a.position.x-b.position.x||a.id.localeCompare(b.id))){let rank=ranks.at(-1);if(!rank||Math.abs(rank.y-node.position.y)>1e-5){rank={y:node.position.y,nodes:[]};ranks.push(rank);}rank.nodes.push(node);}
 const widest=Math.max(...nodes.map(node=>node.width));
 // Half a column also hosts branch captions. Keep an 8px caption gutter.
 const columnGap=Math.max(32,Math.ceil((2*(semanticEdgeWidth+8)-widest)/4)*4),rankGap=56;
 const result:Record<string,LayoutPosition>={};let top=0;
 for(const rank of ranks){const height=Math.max(...rank.nodes.map(node=>node.height)),width=rank.nodes.reduce((sum,node)=>sum+node.width,0)+columnGap*(rank.nodes.length-1);let left=-width/2;
  for(const node of rank.nodes){result[node.id]={x:left+node.width/2,y:top+height/2};left+=node.width+columnGap;}top+=height+rankGap;
 }
 return result;
}

/** A small canvas may require panning; it must not turn readable text into dots. */
export function readableSemanticViewport(width:number,height:number,bounds:LayoutBounds,focus?:LayoutPosition){
 if(![width,height,bounds.x1,bounds.y1,bounds.w,bounds.h].every(Number.isFinite)||width<=0||height<=0||bounds.w<=0||bounds.h<=0)return;
 const fit=Math.min(Math.max(1,width-64)/bounds.w,Math.max(1,height-64)/bounds.h),zoom=Math.max(1,Math.min(1.25,fit)),center=focus??{x:bounds.x1+bounds.w/2,y:bounds.y1+bounds.h/2};
 if(![center.x,center.y].every(Number.isFinite))return;
 return {zoom,pan:{x:width/2-center.x*zoom,y:height/2-center.y*zoom}};
}
