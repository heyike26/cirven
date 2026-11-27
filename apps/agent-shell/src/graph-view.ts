export type GraphViewport={width:number;height:number};
export type GraphView={zoom:number;pan:{x:number;y:number};positions:Record<string,{x:number;y:number}>;selected:string[];mode:string;search:string;viewport?:GraphViewport};
export function graphViewport(value:unknown):value is GraphViewport{if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join(',')!=='height,width')return false;const v=value as GraphViewport;return [v.width,v.height].every(n=>typeof n==='number'&&Number.isFinite(n)&&n>0&&n<=100000);}
export function graphView(value:unknown):GraphView|undefined{const v=value as GraphView;if(!v||!Number.isFinite(v.zoom)||v.zoom<=0||!Number.isFinite(v.pan?.x)||!Number.isFinite(v.pan?.y)||!v.positions||!Array.isArray(v.selected)||v.selected.some(id=>typeof id!=='string')||typeof v.mode!=='string'||typeof v.search!=='string'||v.viewport!==undefined&&!graphViewport(v.viewport))return;for(const p of Object.values(v.positions))if(!Number.isFinite(p?.x)||!Number.isFinite(p?.y))return;return v;}
/** A smaller surface keeps the same model point at its centre and the user's scale.
 * Old records have no size witness: keep their exact pixel pan instead of guessing. */
export function graphCameraForViewport(view:Pick<GraphView,'zoom'|'pan'|'viewport'>,next:GraphViewport){
 if(!graphViewport(next)||!Number.isFinite(view.zoom)||view.zoom<=0||!Number.isFinite(view.pan.x)||!Number.isFinite(view.pan.y))return;
 const previous=view.viewport;
 return {zoom:view.zoom,pan:previous&&graphViewport(previous)?{x:view.pan.x+(next.width-previous.width)/2,y:view.pan.y+(next.height-previous.height)/2}:{...view.pan}};
}
/** Resize is presentation only: graph zoom/pan and node coordinates stay intact. */
export function resizeGraph(core:{resize:()=>unknown}){core.resize();}
