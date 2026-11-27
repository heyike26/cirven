import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText} from './ui-language';
import cytoscape from 'cytoscape';
import {batch,createEffect,createMemo,createSignal,For,on,onCleanup,onMount,Show} from 'solid-js';
import type {NodeState} from './contracts';
import {useGraphView} from './GraphViewState';
import {graphCameraForViewport,type GraphViewport} from './graph-view';
import {graphElements,type GraphNode} from './graph-data';
import {boundedGraphLabel} from './semantic-layout';
import {layoutTasks,TASK_NODE_WIDTH,TASK_NODE_HEIGHT} from './task-graph-layout';
import {trapFocus} from './focus';
import './task-network.css';

export type TaskGraphQuestion={id:string;text:string;reason:string;affects_node_ids:string[];blocking:boolean;resolved?:boolean};
type Props={stateId?:string;nodes:GraphNode[];states:NodeState[];extraEdges?:{data:{id:string;source:string;target:string;kind:string}}[];questions?:TaskGraphQuestion[];selectedId?:string;onSelect:(nodeId:string)=>void;onInspect?:(nodeId:string)=>void};
type Mode='flow-down'|'flow-right'|'hierarchy'|'network';

export default function TaskGraph(props:Props){
 let container:HTMLDivElement|undefined,shell:HTMLDivElement|undefined,minimap:HTMLCanvasElement|undefined,revealTarget:HTMLSpanElement|undefined,core:cytoscape.Core|undefined;
 let disposed=false,initialized=false,initializationStarted=false,layoutEpoch=0,layoutAbort:AbortController|undefined,saveTimer:ReturnType<typeof setTimeout>|undefined,mapFrame=0;
 let positions:Record<string,{x:number;y:number}>={},topology='',mapProjection:{scale:number;x:number;y:number}|undefined;
 let viewportSize:GraphViewport|undefined,resizing=false,resizeCanvas=()=>{};
 const viewKey=props.stateId??'task-network',stored=useGraphView(`${viewKey}:readable-v2`),legacy=useGraphView(viewKey);
 const [ready,setReady]=createSignal(false),[presentationReady,setPresentationReady]=createSignal(false),[size,setSize]=createSignal({width:0,height:0}),[busy,setBusy]=createSignal(false),[expanded,setExpanded]=createSignal(false),[zoom,setZoom]=createSignal(100);
 const [mode,setMode]=createSignal<Mode>('flow-down'),[search,setSearch]=createSignal(''),[selected,setSelected]=createSignal(''),[detailOpen,setDetailOpen]=createSignal(false),[questionsOpen,setQuestionsOpen]=createSignal(false),[questionId,setQuestionId]=createSignal(''),[nearby,setNearby]=createSignal(false),[error,setError]=createSignal<UIErrorCopy>('');
 const [selectedOffscreen,setSelectedOffscreen]=createSignal(false);
 const tasks=createMemo(()=>{const excluded=new Set(props.states.filter(state=>state.status==='QUESTION').map(state=>state.node_id));return props.nodes.filter(node=>node.kind!=='QUESTION'&&!excluded.has(node.node_id));});
 const byId=createMemo(()=>new Map(tasks().map(node=>[node.node_id,node])));
 const questions=()=>props.questions??[];
 const current=createMemo(()=>byId().get(selected()));
 const activeQuestion=createMemo(()=>questions().find(question=>question.id===questionId()));
 const successors=(id:string)=>tasks().filter(node=>node.dependency_node_ids.includes(id));
 const parents=()=>tasks().filter(node=>node.parent_id&&byId().has(node.parent_id));
 const pendingQuestions=()=>questions().filter(question=>!question.resolved);
 const status=(id:string)=>{const state=props.states.find(state=>state.node_id===id);return state?.latest_run?.execution_status==='RUNNING'?'RUNNING':state?.latest_run?.verification_status&&state.latest_run.verification_status!=='NOT_RUN'?state.latest_run.verification_status:state?.status??'UNKNOWN';};
 const kindLabel=(kind?:string)=>uiText(({TASK:'任务',DECISION:'决策',MILESTONE:'里程碑',DELIVERABLE:'交付物'} as Record<string,string>)[kind??'TASK']??'任务');
 const statusLabel=(value:string)=>uiText(({ACCEPTED_PLAN:'计划已确认',NEEDS_INPUT:'待确认',DRAFT:'草案',PROPOSED:'草案',UNSAVED:'未保存',PASSED:'验收通过',FAILED:'验收失败',READY:'可开始',RUNNING:'运行中',BLOCKED:'受阻',UNKNOWN:'待复核',NOT_RUN:'尚未验证',BLOCKED_ON_APPROVAL:'等待批准',BLOCKED_ON_DEPENDENCY:'等待前置任务'} as Record<string,string>)[value]??value);
 const measure=document.createElement('canvas').getContext('2d');
 function caption(node:GraphNode){if(measure)measure.font='500 13px "Segoe UI", "Microsoft YaHei UI", sans-serif';return `${boundedGraphLabel(`${node.node_id} · ${kindLabel(node.kind)}`,TASK_NODE_WIDTH-40,1,text=>measure?.measureText(text).width??Array.from(text).length*13)}\n${boundedGraphLabel(node.role,TASK_NODE_WIDTH-40,2,text=>measure?.measureText(text).width??Array.from(text).length*13)}`;}
 function writeView(){if(!core||!initialized||busy()||!viewportSize)return;stored.write({zoom:core.zoom(),pan:core.pan(),positions,selected:selected()?[selected()]:[],mode:mode(),search:search(),viewport:{...viewportSize}});}
 function scheduleSave(){if(!initialized||busy())return;if(saveTimer)clearTimeout(saveTimer);saveTimer=setTimeout(()=>{saveTimer=undefined;writeView();},180);}
 function reconciledPositions(saved:Record<string,{x:number;y:number}>){const next:Record<string,{x:number;y:number}>={};for(const id of byId().keys()){const p=saved[id];if(p)next[id]={...p};}const known=Object.values(next),left=known.length?Math.min(...known.map(p=>p.x)):TASK_NODE_WIDTH/2+24,top=known.length?Math.max(...known.map(p=>p.y))+TASK_NODE_HEIGHT+48:TASK_NODE_HEIGHT/2+24;let missing=0;for(const id of byId().keys()){if(next[id])continue;next[id]={x:left+(missing%3)*(TASK_NODE_WIDTH+48),y:top+Math.floor(missing/3)*(TASK_NODE_HEIGHT+48)};missing++;}return next;}
 function collectPositions(){if(core)positions=Object.fromEntries(core.nodes().map(node=>[node.id(),{...node.position()}]));}
 function scheduleMap(){if(mapFrame||disposed)return;mapFrame=requestAnimationFrame(()=>{mapFrame=0;paintMap();});}
 function paintMap(){
  if(core&&core.width()>0&&core.height()>0){const node=core.getElementById(selected()),b=node.length?node.renderedBoundingBox({includeLabels:true,includeOverlays:false}):undefined;setSelectedOffscreen(!!b&&(b.x1<0||b.y1<0||b.x2>core.width()||b.y2>core.height()));}
  if(!core||!minimap||!core.nodes().length)return;const ctx=minimap.getContext('2d');if(!ctx)return;
  const width=minimap.width,height=minimap.height,b=core.nodes().boundingBox({includeLabels:false,includeOverlays:false}),scale=Math.min((width-16)/Math.max(b.w,1),(height-16)/Math.max(b.h,1)),x=(width-b.w*scale)/2-b.x1*scale,y=(height-b.h*scale)/2-b.y1*scale;mapProjection={scale,x,y};
  ctx.clearRect(0,0,width,height);ctx.lineWidth=1;ctx.strokeStyle='#b7c2ca';
  for(const edge of core.edges()){const a=edge.source().position(),b=edge.target().position();ctx.beginPath();ctx.moveTo(a.x*scale+x,a.y*scale+y);ctx.lineTo(b.x*scale+x,b.y*scale+y);ctx.stroke();}
  for(const node of core.nodes()){const p=node.position();ctx.fillStyle=node.id()===selected()?'#486a7f':node.hasClass('question-impact')?'#b39761':'#bac6cd';ctx.fillRect((p.x-TASK_NODE_WIDTH/2)*scale+x,(p.y-TASK_NODE_HEIGHT/2)*scale+y,Math.max(2,TASK_NODE_WIDTH*scale),Math.max(2,TASK_NODE_HEIGHT*scale));}
  const z=core.zoom(),pan=core.pan();ctx.fillStyle='#607f9814';ctx.strokeStyle='#486a7f';ctx.lineWidth=1.5;ctx.beginPath();ctx.rect(-pan.x/z*scale+x,-pan.y/z*scale+y,core.width()/z*scale,core.height()/z*scale);ctx.fill();ctx.stroke();
 }
 function navigateMap(event:PointerEvent){if(!core||!minimap||!mapProjection||busy()||!presentationReady())return;const r=minimap.getBoundingClientRect(),p=mapProjection;const point={x:((event.clientX-r.left)*minimap.width/r.width-p.x)/p.scale,y:((event.clientY-r.top)*minimap.height/r.height-p.y)/p.scale},z=Math.max(.9,core.zoom());core.stop();core.viewport({zoom:z,pan:{x:core.width()/2-point.x*z,y:core.height()/2-point.y*z}});}
 function revealBounds(bounds:{x1:number;y1:number;w:number;h:number}){
  if(!container||!revealTarget||disposed||!container.isConnected||!bounds.w||!bounds.h)return;
  const area=container.closest('.as-task-map-area');if(!area)return;
  const canvas=container.getBoundingClientRect(),parent=area.getBoundingClientRect();
  Object.assign(revealTarget.style,{left:`${canvas.left-parent.left+bounds.x1}px`,top:`${canvas.top-parent.top+bounds.y1}px`,width:`${bounds.w}px`,height:`${bounds.h}px`});
  revealTarget.hidden=false;
  // Only explicit navigation reveals content; restore/resize never scroll the reader.
  revealTarget.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});
  revealTarget.hidden=true;
 }
 function readable(id?:string,fromStart=false,reveal=false){
  if(!core||!core.nodes().length||busy())return;core.stop();const node=id?core.getElementById(id):undefined,b=core.nodes().boundingBox({includeLabels:false,includeOverlays:false}),z=1;
  if(node?.length&&!fromStart){const p=node.position();core.viewport({zoom:z,pan:{x:core.width()/2-p.x*z,y:core.height()*.42-p.y*z}});}
  else core.viewport({zoom:z,pan:{x:mode()==='flow-right'?40-b.x1:(core.width()-Math.min(b.w,core.width()-64))/2-b.x1,y:32-b.y1}});
  if(reveal&&node?.length)revealBounds(node.renderedBoundingBox({includeLabels:true,includeOverlays:false}));
 }
 function applyEmphasis(){
  if(!core)return;const id=selected(),target=core.getElementById(id),neighbors=target.length?target.closedNeighborhood():core.collection();
  core.batch(()=>{core!.elements().removeClass('muted related question-impact scope-hidden');core!.nodes().unselect();if(target.length)target.select();
   if(id&&target.length){neighbors.addClass('related');if(nearby())core!.elements().difference(neighbors).addClass('scope-hidden');}
   const q=activeQuestion();if(q){const ids=q.affects_node_ids.length?q.affects_node_ids:tasks().map(node=>node.node_id);for(const affected of ids)core!.getElementById(affected).addClass('question-impact');}
  });scheduleMap();
 }
 function selectTask(id:string,open=true,reveal=true){if(!core||!byId().has(id))return;batch(()=>{setSelected(id);setQuestionId('');setError('');setDetailOpen(open);setQuestionsOpen(false);props.onSelect(id);});applyEmphasis();requestAnimationFrame(()=>{if(!core||disposed||selected()!==id)return;resizeCanvas();readable(id,false,reveal);scheduleMap();scheduleSave();});}
 const recoverSelected=()=>!search().trim()&&selectedOffscreen();
 function locate(){if(busy()||!presentationReady())return;const term=search().trim().toLocaleLowerCase();if(!term){if(selectedOffscreen())readable(selected(),false,true);return;}const exact=tasks().find(node=>node.node_id.toLocaleLowerCase()===term),match=exact??tasks().find(node=>node.role.toLocaleLowerCase().includes(term));if(!match){setError('没有找到匹配节点');return;}selectTask(match.node_id);}
 function overview(){if(!core||busy()||!presentationReady())return;core.stop();setNearby(false);applyEmphasis();core.fit(undefined,48);revealBounds(core.nodes().renderedBoundingBox({includeLabels:true,includeOverlays:false}));scheduleSave();}
 function zoomBy(factor:number){if(!core||busy()||!presentationReady())return;core.stop();core.zoom({level:Math.max(.08,Math.min(3,core.zoom()*factor)),renderedPosition:{x:core.width()/2,y:core.height()/2}});}
 async function arrange(nextMode=mode(),initial=false){
  if(!core||disposed)return;layoutAbort?.abort();const abort=new AbortController();layoutAbort=abort;const epoch=++layoutEpoch;setBusy(true);setError('');
  try{let next:Record<string,{x:number;y:number}>;
   if(nextMode==='network'&&tasks().length<=200){core.elements().removeClass('scope-hidden');core.layout({name:'cose',fit:false,animate:false,randomize:false,nodeRepulsion:()=>16000,idealEdgeLength:()=>120,numIter:250}).run();next=Object.fromEntries(core.nodes().map(node=>[node.id(),{...node.position()}]));}
   else next=await layoutTasks(tasks(),{direction:nextMode==='flow-right'?'RIGHT':'DOWN',...(nextMode==='hierarchy'?{edges:parents().map(node=>({source:node.parent_id!,target:node.node_id}))}:{}),signal:abort.signal});
   if(disposed||epoch!==layoutEpoch||abort.signal.aborted||!core)return;
   core.batch(()=>{for(const node of core!.nodes()){const p=next[node.id()];if(p)node.position(p);}});positions=next;setMode(nextMode);core.edges().style('taxi-direction',nextMode==='flow-right'?'rightward':'downward');initialized=true;setPresentationReady(true);setBusy(false);setReady(true);applyEmphasis();readable(initial?undefined:selected(),initial,!initial);if(!initial)writeView();scheduleMap();
  }catch(e){if(disposed||epoch!==layoutEpoch||abort.signal.aborted)return;setError(e instanceof Error&&e.name==='TaskGraphLayoutError'?'任务图布局未完成，请重试。':authoredErrorCopy(e));setBusy(false);setReady(true);}
 }
 function restoreLegacy(){const value=legacy.read();if(!core||!value)return;layoutAbort?.abort();layoutEpoch++;setBusy(false);core.batch(()=>{for(const [id,p] of Object.entries(value.positions)){const node=core!.getElementById(id);if(node.length)node.position(p);}});collectPositions();readable(value.selected.find(id=>byId().has(id))??selected(),false,true);writeView();scheduleMap();}
 onMount(()=>{
  try{core=cytoscape({container,elements:[],minZoom:.08,maxZoom:3,wheelSensitivity:.15,layout:{name:'preset',fit:false},style:[
   {selector:'node',style:{shape:'round-rectangle',width:TASK_NODE_WIDTH,height:TASK_NODE_HEIGHT,label:'data(display_label)','text-wrap':'wrap','text-overflow-wrap':'anywhere','text-max-width':`${TASK_NODE_WIDTH-36}px`,'line-height':1.45,'font-family':'Segoe UI, Microsoft YaHei UI, sans-serif','font-size':13,'font-weight':500,color:'#30383d','text-valign':'center','background-color':'#ffffff','border-color':'#cbd5db','border-width':1.5,'overlay-opacity':0}},
   {selector:'node[kind="MILESTONE"]',style:{'border-width':2,'background-color':'#eef3f5'}},
   {selector:'node[kind="DECISION"]',style:{'border-color':'#b4a58a','background-color':'#fcfaf5'}},
   {selector:'edge',style:{width:1.5,'line-color':'#a9bac4','target-arrow-color':'#a9bac4','target-arrow-shape':'triangle','curve-style':'taxi','taxi-direction':'downward','taxi-turn':'50%','arrow-scale':1}},
   {selector:'edge[kind="parent"]',style:{'line-style':'dashed','target-arrow-shape':'none'}},
   {selector:'node[status="NEEDS_INPUT"]',style:{'border-color':'#b69a67'}},
   {selector:'node[status="PASSED"]',style:{'border-color':'#688c79','background-color':'#f2f7f3'}},
   {selector:'node[status="FAILED"]',style:{'border-color':'#a66565','background-color':'#fbf3f3'}},
   {selector:'.muted',style:{opacity:.25}},
   {selector:'edge.related',style:{width:2,'line-color':'#54788f','target-arrow-color':'#54788f'}},
   {selector:'node.question-impact',style:{'border-color':'#ad8b49','border-width':3,'background-color':'#fbf7ed',opacity:1}},
   {selector:'node:selected',style:{'border-color':'#486b82','border-width':2.5,'background-color':'#eef4f8',opacity:1}},
   {selector:'.scope-hidden',style:{display:'none'}}
  ]});
  core.on('tap','node',event=>{if(!busy())selectTask(event.target.id());});core.on('zoom pan',()=>{if(core)setZoom(Math.round(core.zoom()*100));scheduleMap();if(!resizing)scheduleSave();});core.on('dragfree',()=>{collectPositions();scheduleMap();scheduleSave();});
  resizeCanvas=()=>{if(!core||!container||disposed)return;const width=container.clientWidth,height=container.clientHeight,previous=viewportSize,changed=!previous||previous.width!==width||previous.height!==height,camera=initialized&&previous&&width>0&&height>0?graphCameraForViewport({zoom:core.zoom(),pan:core.pan(),viewport:previous},{width,height}):undefined;resizing=true;try{core.resize();if(width>0&&height>0){viewportSize={width,height};if(camera&&changed)core.viewport(camera);}setSize(old=>width===old.width&&height===old.height?old:{width,height});}finally{resizing=false;}scheduleMap();if(initialized&&changed&&width>0&&height>0)scheduleSave();};
  const observer=new ResizeObserver(resizeCanvas);observer.observe(container!);onCleanup(()=>observer.disconnect());setReady(true);resizeCanvas();
  }catch(e){setError(authoredErrorCopy(e));}
 });
 createEffect(()=>{ready();const nodes=tasks(),states=props.states;if(!core)return;try{
  const dependencies=graphElements(nodes,states);const elements=mode()==='hierarchy'?[...dependencies.filter(element=>!element.data.source),...parents().map(node=>({data:{id:`parent:${node.node_id}`,source:node.parent_id!,target:node.node_id,kind:'parent'}}))]:dependencies;for(const element of elements){const node=byId().get(element.data.id);if(node)Object.assign(element.data,{display_label:caption(node),kind:node.kind??'TASK'});}
  const ids=new Set(elements.map(element=>element.data.id));core.batch(()=>{core!.elements().filter(element=>!ids.has(element.id())).remove();for(const element of [...elements.filter(element=>!element.data.source),...elements.filter(element=>element.data.source)]){const existing=core!.getElementById(element.data.id);if(existing.length)existing.data(element.data);else core!.add(element);}});
  const signature=JSON.stringify(nodes.map(node=>[node.node_id,node.dependency_node_ids,node.parent_id]));if(topology!==signature){const previous=topology;topology=signature;if(previous){layoutAbort?.abort();layoutEpoch++;setBusy(false);if(initialized){positions=reconciledPositions(positions);core.batch(()=>{for(const node of core!.nodes())node.position(positions[node.id()]);});scheduleSave();}else if(initializationStarted)void arrange(mode(),true);}}applyEmphasis();
 }catch(e){setError(authoredErrorCopy(e));}});
 createEffect(()=>{ready();const dimensions=size();if(!core||initializationStarted||initialized||busy()||!stored.ready()||dimensions.width<=0||dimensions.height<=0)return;
  initializationStarted=true;const value=stored.read(),ids=tasks().map(node=>node.node_id),valid=value&&['flow-down','flow-right','hierarchy','network'].includes(value.mode)&&ids.some(id=>value.positions[id]);
  const id=value?.selected.find(id=>byId().has(id))??(props.selectedId&&byId().has(props.selectedId)?props.selectedId:ids[0]??'');batch(()=>{setSelected(id);if(id)props.onSelect(id);});
  if(valid){positions=reconciledPositions(value.positions);core.batch(()=>{for(const id of ids)core!.getElementById(id).position(positions[id]);});setMode(value.mode as Mode);setSearch(value.search);core.viewport(graphCameraForViewport({...value,zoom:Math.max(.08,Math.min(3,value.zoom))},dimensions)!);initialized=true;setPresentationReady(true);setZoom(Math.round(core.zoom()*100));applyEmphasis();scheduleMap();}
  else void arrange('flow-down',true);
 });
 createEffect(on(()=>props.selectedId,id=>{if(initialized&&id&&byId().has(id)&&id!==selected())selectTask(id,false,false);}));
 createEffect(()=>{expanded();if(expanded()&&shell){const release=trapFocus(shell,()=>setExpanded(false));onCleanup(release);}requestAnimationFrame(()=>{if(core&&!disposed)resizeCanvas();});});
 onCleanup(()=>{if(saveTimer)clearTimeout(saveTimer);writeView();disposed=true;layoutEpoch++;layoutAbort?.abort();if(mapFrame)cancelAnimationFrame(mapFrame);core?.destroy();core=undefined;});
 return <section ref={shell} class="as-graph-shell as-task-network" classList={{'as-graph-expanded':expanded()}} aria-label={uiText('任务网络')} data-layout-ready={String(presentationReady()&&!busy())}>
  <header class="as-task-toolbar"><div class="as-task-heading"><strong>{uiText('任务流程')}</strong><span>{uiText('{count} 个任务',{count:tasks().length})}</span></div><div class="as-task-toolbar-actions">
   <Show when={questions().length}><button type="button" class="as-task-question-trigger" aria-pressed={questionsOpen()} onClick={()=>{setQuestionsOpen(!questionsOpen());setDetailOpen(false);}}>{uiText('待确认')}<span>{pendingQuestions().length}</span></button></Show>
   <select aria-label={uiText('图布局')} value={mode()} disabled={busy()} onChange={event=>void arrange(event.currentTarget.value as Mode)}><option value="flow-down">{uiText('纵向流程')}</option><option value="flow-right">{uiText('横向流程')}</option><Show when={parents().length}><option value="hierarchy">{uiText('任务层级')}</option></Show><Show when={tasks().length<=200}><option value="network">{uiText('关系网络')}</option></Show></select>
   <button type="button" onClick={()=>setExpanded(!expanded())}>{expanded()?uiText('收起大图'):uiText('展开大图')}</button>
  </div></header>
  <form class="as-task-find" onSubmit={event=>{event.preventDefault();locate();}}><input aria-label={uiText('搜索图中节点')} value={search()} onInput={event=>{setSearch(event.currentTarget.value);scheduleSave();}} placeholder={uiText('搜索任务名称或 ID')}/><button type="submit" disabled={busy()||!search().trim()&&!selectedOffscreen()} aria-label={uiText(recoverSelected()?'恢复可读视图':'定位')} title={uiText(recoverSelected()?'恢复可读视图':'定位')}>{uiText(recoverSelected()?'恢复可读视图':'定位')}</button><button type="button" disabled={busy()} aria-pressed={nearby()} onClick={()=>{setNearby(!nearby());applyEmphasis();readable(selected(),false,true);}}>{uiText('相关任务')}</button></form>
  <div class="as-task-stage" classList={{'as-task-has-inspector':detailOpen()||questionsOpen()}}>
   <div class="as-task-map-area"><div class="as-task-view-tools">
    <div class="as-task-map-controls"><button type="button" disabled={busy()||!presentationReady()} aria-label={uiText('缩小任务图')} onClick={()=>zoomBy(.8)}>−</button><button type="button" disabled={busy()||!presentationReady()} aria-label={uiText('恢复可读视图')} onClick={()=>readable(selected(),false,true)}>{zoom()}%<Show when={zoom()<75}> · {uiText('可读')}</Show></button><button type="button" disabled={busy()||!presentationReady()} aria-label={uiText('放大任务图')} onClick={()=>zoomBy(1.25)}>+</button><span/><button type="button" disabled={busy()||!presentationReady()} onClick={overview}>{uiText('全图')}</button></div>
    <div class="as-task-minimap"><canvas ref={minimap} width="176" height="104" aria-label={uiText('任务图导航')} aria-disabled={busy()||!presentationReady()} role="img" onPointerDown={event=>{event.currentTarget.setPointerCapture(event.pointerId);navigateMap(event);}} onPointerMove={event=>{if(event.currentTarget.hasPointerCapture(event.pointerId))navigateMap(event);}} onPointerUp={event=>{if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);}}/><small>{uiText('概览')}</small></div>
    </div><div class="as-task-canvas-host"><div ref={container} class="as-task-canvas" aria-label={uiText('拖动平移、滚轮缩放、点击查看任务节点')} aria-busy={busy()}/><Show when={busy()}><div class="as-task-layout-loading" role="status">{uiText('正在布局…')}</div></Show></div>
    <span ref={revealTarget} class="as-task-reveal-target" aria-hidden="true" hidden/>
   </div>
   <Show when={questionsOpen()||detailOpen()&&current()}><aside class="as-task-inspector" aria-label={uiText(questionsOpen()?'待确认问题':'任务详情')}>
    <div class="as-task-inspector-head"><strong>{uiText(questionsOpen()?'待确认问题':'任务详情')}</strong><button type="button" aria-label={uiText('关闭详情')} onClick={()=>{setDetailOpen(false);setQuestionsOpen(false);setQuestionId('');applyEmphasis();}}>×</button></div>
    <Show when={questionsOpen()} fallback={<Show when={current()} keyed>{node=><div class="as-task-detail-body"><div class="as-task-node-meta"><span>{node.node_id}</span><span>{kindLabel(node.kind)}</span><span>{statusLabel(status(node.node_id))}</span></div><h3>{node.role}</h3><Show when={node.description}><p>{node.description}</p></Show>
     <For each={['inputs','outputs','acceptance','risks','capabilities'] as const}>{key=><Show when={node[key]?.length}><section><h4>{uiText(({inputs:'输入',outputs:'交付物',acceptance:'验收',risks:'风险',capabilities:'所需能力'})[key])}</h4><ul><For each={node[key]}>{text=><li>{text}</li>}</For></ul></section></Show>}</For>
     <For each={[{label:'前置任务',rows:node.dependency_node_ids.map(id=>byId().get(id)).filter((node):node is GraphNode=>!!node)},{label:'后续任务',rows:successors(node.node_id)}]}>{group=><Show when={group.rows.length}><section><h4>{uiText(group.label)}</h4><For each={group.rows}>{task=><button class="as-task-relation" type="button" onClick={()=>selectTask(task.node_id)}><span>{task.node_id}</span><span>{task.role}</span><span aria-hidden="true">→</span></button>}</For></section></Show>}</For>
     <Show when={questions().some(question=>!question.resolved&&(!question.affects_node_ids.length||question.affects_node_ids.includes(node.node_id)))}><button type="button" class="as-task-detail-link" onClick={()=>{setQuestionsOpen(true);setDetailOpen(false);}}>{uiText('查看相关问题')}</button></Show>
     <Show when={props.onInspect}><button type="button" class="as-task-detail-link" onClick={()=>{setExpanded(false);props.onInspect?.(node.node_id);}}>{uiText('编辑此任务')}</button></Show>
    </div>}</Show>}><div class="as-task-question-list"><For each={[...questions().filter(question=>!question.resolved),...questions().filter(question=>question.resolved)]}>{question=><article classList={{'is-selected':questionId()===question.id}}><button type="button" aria-pressed={questionId()===question.id} onClick={()=>{setNearby(false);setQuestionId(questionId()===question.id?'':question.id);applyEmphasis();if(questionId()&&question.affects_node_ids.length&&!question.affects_node_ids.includes(selected()))readable(question.affects_node_ids[0],false,true);}}><span>{question.id}<small>{uiText(question.resolved?'已采用':question.blocking?'影响开始':'待补充')}</small></span><strong>{question.text}</strong></button><Show when={questionId()===question.id}><p>{question.reason}</p><div class="as-task-question-targets"><For each={question.affects_node_ids.length?question.affects_node_ids:tasks().map(node=>node.node_id)}>{id=><button type="button" onClick={()=>selectTask(id)}>{id}</button>}</For></div><Show when={props.onInspect}><button type="button" class="as-task-detail-link" onClick={()=>{setExpanded(false);props.onInspect?.(question.id);}}>{uiText(question.resolved?'查看答复':'回答此问题')}</button></Show></Show></article>}</For></div></Show>
   </aside></Show>
  </div>
  <footer class="as-task-footer"><span>{uiText(mode()==='hierarchy'?'任务层级':'依赖关系')}<span aria-hidden="true"> → </span>{uiText('后续任务')}</span><Show when={legacy.read()}><button type="button" onClick={restoreLegacy}>{uiText('恢复旧版布局')}</button></Show></footer>
  <Show when={error()}><div class="as-task-layout-error" role="alert"><p>{authoredText(error(),uiText)}</p><button type="button" onClick={()=>void arrange()}>{uiText('重试')}</button></div></Show>
 </section>;
}
