'use strict';
const path = require('node:path');
const {WINDOW_TITLES,ICON_SVG,SHORT_NAME}=require('./product-brand.cjs');
const {stableOrbGeometry}=require('./orb-geometry.cjs');

const {t,resolveLocale,htmlCatalog}=require('../../packages/i18n/index.cjs');
const CHANNEL = 'eeas:assistant-orb:event';
const STATE_CHANNEL = 'eeas:assistant-orb:state';
const ACTIONS = Object.freeze(['ask', 'show-agent', 'workbench', 'annotate', 'tasks']);
// Asking stays at the center. Task activity is another in-place surface.
const MENU_ACTIONS = Object.freeze(['annotate', 'tasks', 'show-agent', 'workbench']);
const SMALL = 56, TARGET = 56, EXIT_MS = 220, LEAVE_MS = 140;
const NOTICE_MS = 4500;

function taskActivity(value) {
  if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(key=>!['running','attention','unread','urgency','notice','eventId'].includes(key))||
    !['running','attention','unread'].every(key=>Number.isSafeInteger(value[key])&&value[key]>=0)||
    !['none','complete','waiting','error'].includes(value.urgency)||
    (value.notice!==undefined&&(typeof value.notice!=='string'||value.notice.length>80||/[\u0000-\u001f\u007f-\u009f]/u.test(value.notice)))||
    (value.eventId!==undefined&&(typeof value.eventId!=='string'||! /^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/.test(value.eventId))))throw new Error('Explicit safe task activity is required');
  return {running:value.running,attention:value.attention,unread:value.unread,urgency:value.urgency,
    ...(value.notice===undefined?{}:{notice:value.notice.trim()}),...(value.eventId===undefined?{}:{eventId:value.eventId})};
}

function workArea(value) {
  if (!value || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(value[key])) || value.width < SMALL || value.height < SMALL) {
    throw new Error('A usable display work area is required');
  }
  return {x:Math.round(value.x),y:Math.round(value.y),width:Math.floor(value.width),height:Math.floor(value.height)};
}
function clamp(value, low, high) { return Math.min(Math.max(value, low), high); }
function clampOrb(bounds, area) {
  area = workArea(area);
  if(!bounds||!Number.isFinite(bounds.x)||!Number.isFinite(bounds.y))throw new Error('A finite own-window position is required');
  return {x:Math.round(clamp(bounds.x,area.x,area.x+area.width-SMALL)),
    y:Math.round(clamp(bounds.y,area.y,area.y+area.height-SMALL)),width:SMALL,height:SMALL};
}
function arcLayout(bubble, area, count=MENU_ACTIONS.length) {
  area = workArea(area);
  if(!Number.isSafeInteger(count)||count<1||count>5)throw new Error('An arc supports one to five real menu items');
  const center={x:bubble.x+SMALL/2,y:bubble.y+SMALL/2};
  const dx=area.x+area.width/2-center.x,dy=area.y+area.height/2-center.y;
  const preferred=dx||dy?Math.atan2(dy,dx):-Math.PI/2;
  const directions=[['N',-90],['NE',-45],['E',0],['SE',45],['S',90],['SW',135],['W',180],['NW',-135]]
    .map(([direction,degrees])=>({direction,angle:degrees*Math.PI/180}))
    .sort((a,b)=>Math.cos(b.angle-preferred)-Math.cos(a.angle-preferred));
  const radius=count===5?96:80;
  const sweeps=count===3?[100,90]:count===4?[132,156,180]:count===5?[156,180,240]:[Math.max(0,(count-1)*50)];
  const candidates=[];
  for(const {direction,angle} of directions)for(const sweepDegrees of sweeps){
    const candidateRadius=radius;
    candidates.push({direction,radius:candidateRadius,sweepDegrees,
    offsets:Array.from({length:count},(_,index)=>{
      const theta=angle+(count===1?0:(index/(count-1)-.5)*sweepDegrees*Math.PI/180);
      const exact=value=>Math.abs(value)<1e-9?0:value;
      return {dx:exact(candidateRadius*Math.cos(theta)),dy:exact(candidateRadius*Math.sin(theta))};
    })});
  }
  // The normal 24-DIP screen inset can fit a closer diagonal arc, even
  // though the exact corner needs the narrower inward quarter below.
  if(count===4)for(const {direction,angle}of directions.filter(item=>item.direction.length===2)){
    const radius=88,sweepDegrees=120;
    candidates.push({direction,radius,sweepDegrees,padding:0,offsets:Array.from({length:count},(_,index)=>{
      const theta=angle+(index/(count-1)-.5)*sweepDegrees*Math.PI/180;
      return{dx:radius*Math.cos(theta),dy:radius*Math.sin(theta)};
    })});
  }
  // Painted circles, rather than their empty bounding-box corners, define
  // pointer hits. Use the inward quarter without moving the actual orb.
  if(count>=3)for(const {direction}of directions.filter(item=>item.direction.length===2)){
    const radius=count===3?84:count===4?112:152,sweepDegrees=90;
    const exact=value=>Math.abs(value)<1e-9?0:value;
    const points=Array.from({length:count},(_,index)=>{
      const theta=((90-sweepDegrees)/2+index*sweepDegrees/(count-1))*Math.PI/180;
      return [exact(radius*Math.cos(theta)),exact(radius*Math.sin(theta))];
    });
    const quarter={SE:0,SW:1,NW:2,NE:3}[direction];
    const rotate=([x,y])=>quarter===0?{dx:x,dy:y}:quarter===1?{dx:-y,dy:x}:quarter===2?{dx:-x,dy:-y}:{dx:y,dy:-x};
    candidates.push({direction,radius,sweepDegrees,padding:0,offsets:points.map(rotate)});
  }
  for(const candidate of candidates){
    const {offsets}=candidate;
    // clip-path makes the circular hit region explicit; Chromium pointer
    // tests cover the intentionally intersecting empty rectangle corners.
    if(offsets.some((a,index)=>offsets.slice(index+1).some(b=>Math.hypot(a.dx-b.dx,a.dy-b.dy)<TARGET+1.75)))continue;
    if(offsets.some(a=>Math.hypot(a.dx,a.dy)<(TARGET+48)/2+2))continue;
    const padding=candidate.padding??5,half=TARGET/2;
    const x=Math.floor(Math.min(bubble.x,...offsets.map(item=>center.x+item.dx-half-padding)));
    const y=Math.floor(Math.min(bubble.y,...offsets.map(item=>center.y+item.dy-half-padding)));
    const right=Math.ceil(Math.max(bubble.x+SMALL,...offsets.map(item=>center.x+item.dx+half+padding)));
    const bottom=Math.ceil(Math.max(bubble.y+SMALL,...offsets.map(item=>center.y+item.dy+half+padding)));
    if(x<area.x||y<area.y||right>area.x+area.width||bottom>area.y+area.height||right-x>(count===5?320:280)||bottom-y>(count===5?320:280))continue;
    return {bounds:{x,y,width:right-x,height:bottom-y},...candidate};
  }
  // An unusually tiny work area cannot fit an honest, nonoverlapping arc.
  // Preserve the usable center action instead of moving the user's orb.
  return null;
}
function expandedBounds(bubble, area) {return arcLayout(bubble,area)?.bounds??{...bubble};}

function noticeLayout(bubble, area) {
  area=workArea(area);
  const width=Math.min(220,area.width),height=80,gap=8,center={x:bubble.x+28,y:bubble.y+28};
  const candidates=[
    {x:bubble.x+SMALL+gap,y:clamp(center.y-height/2,area.y,area.y+area.height-height),width,height},
    {x:bubble.x-width-gap,y:clamp(center.y-height/2,area.y,area.y+area.height-height),width,height},
    {x:clamp(center.x-width/2,area.x,area.x+area.width-width),y:bubble.y-height-gap,width,height},
    {x:clamp(center.x-width/2,area.x,area.x+area.width-width),y:bubble.y+SMALL+gap,width,height}
  ];
  for(const panel of candidates){
    if(panel.x<area.x||panel.y<area.y||panel.x+width>area.x+area.width||panel.y+height>area.y+area.height)continue;
    const x=Math.min(bubble.x,panel.x),y=Math.min(bubble.y,panel.y);
    return {bounds:{x,y,width:Math.max(bubble.x+SMALL,panel.x+width)-x,height:Math.max(bubble.y+SMALL,panel.y+height)-y},
      panel:{x:panel.x-x+4,y:panel.y-y+4,width:width-8,height:height-8}};
  }
  return null;
}

function orbHtml(locale='zh-CN',initialOrb={x:4,y:4}) {
  if(!initialOrb||!['x','y'].every(key=>Number.isSafeInteger(initialOrb[key])&&initialOrb[key]>=0))throw Error('Invalid initial local orb position');
  const icons = {
    ask:'<path d="M20 11.2a8 8 0 0 1-8 8H5l-3 3V11.2a9 9 0 0 1 18 0Z"/><path d="M7 10h8M7 14h5"/>',
    'show-agent':'<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M14 4v16M17 9h1M17 13h1"/>',
    workbench:'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 10h18M10 10v11"/>',
    annotate:'<path d="m14 5 4 4M5 19l4-1L20 7a2.8 2.8 0 0 0-4-4L5 14l-1 6ZM13 20h7"/>',
    tasks:'<path d="m3 6 2 2 3-4M11 6h10M3 13h5M11 13h10M3 20h5M11 20h10"/>'
  };
  const labels = {ask:'提问','show-agent':'对话',workbench:'工作台',annotate:'批注',tasks:'全局总管'};
  const buttons = MENU_ACTIONS.map(action => `<button class="action" data-action="${action}" aria-label="${t('host',labels[action],locale)}" data-ui-aria-label="${labels[action]}"><svg viewBox="0 0 24 24" aria-hidden="true">${icons[action]}</svg><span data-ui-key="orb.action.${action}">${t('host','orb.action.'+action,locale)}</span></button>`).join('');
  return `<!doctype html><html lang="${locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'"><title>${t('common','window.orb',locale,{short_name:SHORT_NAME})}</title><style>
:root{color-scheme:light;font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#36393c;background:transparent;--exit:cubic-bezier(.22,.61,.36,1);--enter:cubic-bezier(.16,1,.3,1)}*{box-sizing:border-box}html,body{width:100%;height:100%;margin:0;overflow:hidden;background:transparent;user-select:none}button{-webkit-app-region:no-drag;font:inherit;border:0;cursor:pointer;touch-action:none;caret-color:#36393c}button:focus-visible{outline:2px solid #616973;outline-offset:2px}button:disabled{cursor:progress}::selection{background:#dfe1e3;color:#36393c}.stage{position:relative;width:100%;height:100%;isolation:isolate}
.orb{position:absolute;left:${initialOrb.x}px;top:${initialOrb.y}px;width:48px;height:48px;padding:0;border-radius:50%;display:grid;place-items:center;background:linear-gradient(145deg,#fff 15%,#f5f6f8 100%);border:1px solid #dfe1e3;box-shadow:0 3px 9px #36393c24,inset 0 1px 0 #fff;transition:transform 180ms var(--exit),opacity 180ms var(--exit);z-index:2}.orb:before{content:"";position:absolute;inset:5px;border:1px solid #fff;border-radius:50%;pointer-events:none}.orb:hover{border-color:#b8bdc2}.orb:active,.orb[data-pressed=true]{transform:scale(.94);transition-duration:100ms}.orb .brand-mark{width:32px;height:32px;stroke:none;transition:transform 220ms var(--enter);transform-origin:center}.orb:hover .brand-mark{transform:scale(1.035)}.brand-mark [data-motion-part] path{transform-box:fill-box;transform-origin:center;transition:transform 220ms var(--enter)}.stage[data-expanded=true] .brand-mark [data-motion-part="outer-C"] path{transform:translateY(-2%)}.stage[data-expanded=true] .brand-mark [data-motion-part="inner-fold"] path{transform:translateY(1%)}.stage[data-expanded=true] .brand-mark [data-motion-part="lower-sweep"] path{transform:translateX(2%)}.stage[data-error=true] .orb{border-color:#9b6b50}
.orb:after{content:"";position:absolute;right:1px;top:1px;width:9px;height:9px;border:2px solid #f5f6f8;border-radius:50%;background:#36393c;opacity:0;transform:scale(.6);transition:opacity 180ms var(--exit),transform 180ms var(--exit)}.stage[data-unread=true] .orb:after{opacity:1;transform:scale(1)}.stage[data-processing=true] .orb{border-color:#616973}
.task-ring{position:absolute;inset:-3px;border:2px solid transparent;border-radius:50%;pointer-events:none}.stage[data-task-urgency=error] .task-ring{border-color:#b84949}.stage[data-task-urgency=waiting] .task-ring{border-color:#a46c19}.stage[data-task-running=true] .orb{border-color:#8797a2}.task-notice{position:absolute;display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:3px;padding:8px 12px;text-align:left;color:#36393c;background:#f5f6f8;border:1px solid #dfe1e3;border-radius:12px;box-shadow:0 3px 8px #36393c24;font-size:12px;line-height:17px;opacity:0;pointer-events:none;transform:translateY(3px);transition:opacity 180ms var(--exit),transform 220ms var(--enter)}.task-notice[hidden]{display:none}.task-notice strong{font-size:12px;font-weight:500;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.task-notice span{font-size:11px;line-height:14px;color:#616973}.stage[data-notice=true] .task-notice{opacity:1;pointer-events:auto;transform:translateY(0)}.task-notice:hover{background:#fff;border-color:#b8bdc2}
.guide{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;opacity:0;transition:opacity 180ms var(--exit)}.guide path{fill:none;stroke:#dfe1e3;stroke-width:1;stroke-linecap:round}.stage[data-expanded=true] .guide{opacity:1}/* Preload positions the former 44px target at center-22; offset the larger
   fixed target without changing that trusted positioning contract. */
.action{clip-path:circle(50%);position:absolute;left:0;top:0;width:${TARGET}px;height:${TARGET}px;min-width:${TARGET}px;max-width:${TARGET}px;min-height:${TARGET}px;max-height:${TARGET}px;margin-left:${22-TARGET/2}px;margin-top:${22-TARGET/2}px;padding:6px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;color:#616973;background:#f5f6f8;border:1px solid #dfe1e3;box-shadow:0 3px 7px #36393c1a;opacity:0;pointer-events:none;translate:var(--dx,0px) var(--dy,0px);transform:scale(.84);transform-origin:center;transition:transform 220ms var(--enter),opacity 180ms var(--exit)}.action svg{position:relative;top:-2.5px;flex:none;width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.65;stroke-linecap:round;stroke-linejoin:round}.action span{position:relative;top:-4px;flex:none;font-size:12px;font-weight:500;line-height:14px;white-space:nowrap;letter-spacing:.01em}.stage[data-expanded=true] .action{opacity:1;pointer-events:auto;transform:scale(1)}.stage[data-busy=true] .action{pointer-events:none}.action:hover{color:#36393c;background:#fff;border-color:#616973}.action:active{filter:brightness(.94)}.action:focus-visible{outline-offset:-4px}
@media(prefers-reduced-motion:reduce){.guide,.orb,.action,.task-notice,.brand-mark,.brand-mark [data-motion-part] path{transition:none}.orb:hover,.orb:active,.orb[data-pressed=true],.orb:hover .brand-mark,.stage[data-expanded=true] .brand-mark [data-motion-part] path{transform:none}}
</style></head><body data-ui-locale="${locale}" data-i18n="${htmlCatalog('host')}"><main class="stage" data-expanded="false" data-busy="false" data-error="false" aria-label="${t('common','window.orb',locale,{short_name:SHORT_NAME})}"><svg class="guide" aria-hidden="true"><path id="arc-guide"/></svg>${buttons}<button class="task-notice" id="task-notice" hidden><strong id="task-notice-text"></strong><span data-ui-key="查看任务">查看任务</span></button><button class="orb" id="orb" aria-label="询问当前页面" aria-description="${t('host','询问当前页面 · 拖动可移动',locale)}" aria-expanded="false" data-ui-aria-label="询问当前页面"><i class="task-ring" aria-hidden="true"></i>${ICON_SVG.replace('<svg ','<svg class="brand-mark" aria-hidden="true" ')}</button></main></body></html>`;
}

class AssistantOrb {
  constructor({BrowserWindow,screen,ipcMain,locale='zh-CN',onAction,onError=()=>{},isClosing=()=>false,enabled=true,clock,watchOutside}) {
    if (typeof BrowserWindow !== 'function' || !screen || !ipcMain || typeof onAction !== 'function' || typeof enabled !== 'boolean') {
      throw new Error('Assistant orb requires its explicit native host dependencies');
    }
    this.screen=screen;this.ipcMain=ipcMain;this.onAction=onAction;this.onError=onError;this.isClosing=isClosing;
    this.clock=clock??{now:()=>Date.now(),setTimeout,clearTimeout};
    if(!['now','setTimeout','clearTimeout'].every(key=>typeof this.clock[key]==='function'))throw new Error('An explicit orb clock requires now/setTimeout/clearTimeout');
    this.enabled=enabled;this.hostMinimized=false;this.workflowActive=false;this.visible=false;this.expanded=false;this.busy=false;
    if(watchOutside!==undefined&&typeof watchOutside!=='function')throw Error('An outside pointer watcher must be explicit');
    this.watchOutside=watchOutside;this.watchStop=null;this.watchEpoch=0;this.expansionRequested=false;this.watchBlocked=false;this.shapeExpanded=false;this.shapeKey=null;
    this.disposed=false;this.loaded=false;this.error=null;this.drag=null;this.shrinkTimer=null;this.leaveTimer=null;this.unread=false;this.processing=false;
    this.surfaceActive=false;this.surfaceId=null;
    this.taskActivity={running:0,attention:0,unread:0,urgency:'none'};this.notice=null;this.noticeTimer=null;this.seenEvents=new Set();
    const area=this._area();
    this.bubble=clampOrb({x:area.x+24,y:area.y+area.height-SMALL-24},area);
    this.geometryKey=null;this.geometry=null;const geometry=this._geometry();
    this.locale=resolveLocale(locale,locale);
    this.pageURL='data:text/html;charset=utf-8,'+encodeURIComponent(orbHtml(this.locale,{x:geometry.center.x-24,y:geometry.center.y-24}));
    this._window=new BrowserWindow({...geometry.bounds,title:t('common','window.orb',resolveLocale(locale,locale),{short_name:SHORT_NAME}),show:false,frame:false,transparent:true,
      backgroundColor:'#00000000',hasShadow:false,thickFrame:false,resizable:false,minimizable:false,maximizable:false,
      fullscreenable:false,skipTaskbar:true,alwaysOnTop:true,focusable:false,
      webPreferences:{preload:path.join(__dirname,'assistant-orb-preload.cjs'),nodeIntegration:false,
        contextIsolation:true,sandbox:true,webviewTag:false}});
    this._window.setContentProtection(true);
    this._applyShape();
    this._window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
    this._window.webContents.on('will-navigate',(event,url)=>{if(url!==this.pageURL)event.preventDefault();});
    this._window.webContents.on('will-redirect',event=>event.preventDefault());
    this._window.webContents.on('render-process-gone',()=>this._fatal(new Error('快捷入口暂不可用，请在 Agent 中继续。')));
    this._window.on('close',event=>{if(!this.disposed){event.preventDefault();void this.setEnabled(false);}});
    this._window.on('closed',()=>{if(!this.disposed)this._fatal(new Error('快捷入口窗口意外关闭。'));});
    this._handler=(event,payload)=>this._receive(event,payload);
    ipcMain.handle(CHANNEL,this._handler);
    this._displayChanged=()=>{if(!this.disposed){this._clearNotice();this.drag=null;this.bubble=clampOrb(this.bubble,this._area(this._center()));this._setExpanded(false,true);}};
    screen.on?.('display-removed',this._displayChanged);
    screen.on?.('display-metrics-changed',this._displayChanged);
    this.ready=Promise.resolve(this._window.loadURL(this.pageURL)).then(()=>{
      if(!this.disposed){this.loaded=true;this._syncVisibility();this._publish();}return this.get();
    },()=>{this._fatal(new Error('快捷入口未能加载，请在 Agent 中继续。'));return this.get();});
  }
  setLocale(locale){this.locale=resolveLocale(locale,locale);if(this._alive()){this._window.setTitle?.(t('common','window.orb',this.locale,{short_name:SHORT_NAME}));this._publish();}return this.get();}
  get window(){return this._window;}
  _center(){return {x:this.bubble.x+SMALL/2,y:this.bubble.y+SMALL/2};}
  getAnchor(){return {...this._center(),orbBounds:{...this.bubble}};}
  _area(point){return workArea(this.screen.getDisplayNearestPoint(point||this.screen.getCursorScreenPoint()).workArea);}
  _alive(){return !this.disposed&&!!this._window&&!this._window.isDestroyed();}
  _geometry(){
    const area=this._area(this._center()),key=JSON.stringify([this.bubble,area]);
    if(key!==this.geometryKey){
      const arc=arcLayout(this.bubble,area),notice=noticeLayout(this.bubble,area);
      this.geometry={...stableOrbGeometry({bubble:this.bubble,area,arc,notice}),arc,notice};this.geometryKey=key;this.shapeKey=null;
      if(this._alive())this._window.setBounds({...this.geometry.bounds},false);
    }
    return this.geometry;
  }
  _shape(){const geometry=this._geometry();return this.shapeExpanded?geometry.shape.expanded:this._noticeEligible()&&this._noticeBounds()?geometry.shape.notice:geometry.shape.collapsed;}
  _applyShape(){
    if(!this._alive())return;
    const shape=this._shape(),key=JSON.stringify(shape);
    if(key!==this.shapeKey){this._window.setShape(shape.map(value=>({...value})));this.shapeKey=key;}
  }
  containsScreenPoint(point,{corridor=false}={}){return this._geometry().contains(point,{expanded:this.expanded||this.expansionRequested,noticeVisible:this._noticeEligible()&&!!this._noticeBounds(),corridor});}
  _cancelWatch(){
    this.watchEpoch++;this.expansionRequested=false;
    const stop=this.watchStop;this.watchStop=null;
    if(stop)try{stop();}catch{/* A failed cleanup cannot revive an old menu. */}
  }
  _watchExpansion(){
    this.expansionRequested=true;const epoch=++this.watchEpoch;
    const current=()=>this._alive()&&this.watchEpoch===epoch&&this.expansionRequested&&this.visible&&!this.isClosing();
    const ready=()=>{if(current()&&!this.expanded){this.expanded=true;this.shapeExpanded=true;this._publish();this._window.moveTop?.();}};
    if(!this.watchOutside){ready();return;}
    const fail=error=>{if(!current())return;this._cancelWatch();this.expanded=false;this.shapeExpanded=false;this.watchBlocked=true;this._publish();try{this.onError(error instanceof Error?error:new Error('Outside pointer watcher unavailable'));}catch{}};
    try{
      const stop=this.watchOutside({onReady:ready,onOutside:()=>{if(current())this._setExpanded(false);},onError:fail});
      if(typeof stop!=='function'){fail(new Error('Outside pointer watcher lacks cleanup'));return;}
      if(current())this.watchStop=stop;else stop();
    }catch(error){fail(error);}
  }
  _cancelShrink(){if(this.shrinkTimer!==null){this.clock.clearTimeout(this.shrinkTimer);this.shrinkTimer=null;}}
  _cancelLeave(){if(this.leaveTimer!==null){this.clock.clearTimeout(this.leaveTimer);this.leaveTimer=null;}}
  _clearNotice(){if(this.noticeTimer!==null)this.clock.clearTimeout(this.noticeTimer);this.noticeTimer=null;this.notice=null;}
  _noticeBounds(){return this.notice&&this.notice.expiresAt>this.clock.now()?this._geometry().notice:null;}
  _noticeEligible(){return this.visible&&!this.disposed&&!this.isClosing()&&!this.expanded&&!this.expansionRequested&&!this.shapeExpanded&&!this.drag&&!this.busy;}
  _refreshNotice(){
    if(!this._alive())return;
    if(this.isClosing()){
      this._clearNotice();this._cancelShrink();this._cancelLeave();this._cancelWatch();this.expanded=false;this.shapeExpanded=false;this.drag=null;this.visible=false;this._window.hide();
    }
    this._publish();
  }
  _scheduleLeave(){
    this._cancelLeave();
    this.leaveTimer=this.clock.setTimeout(()=>{
      this.leaveTimer=null;if(!this._alive()||(!this.expanded&&!this.expansionRequested)||this.drag)return;
      // Resizing the owned window can emit a transient pointerleave. Never
      // collapse while the real cursor is still on the path to an arc item.
      const cursor=this.screen.getCursorScreenPoint();
      if(this.containsScreenPoint(cursor,{corridor:true}))return;
      this._setExpanded(false);
    },LEAVE_MS);
  }
  _layout(){
    const geometry=this._geometry(),bounds=geometry.bounds,center=geometry.center,arc=geometry.arc;
    const offsets=arc?.offsets??[];
    // Transparent travel gaps belong to the page below, not to a decorative
    // native hit band. The four targets alone express their common arc.
    const guide='';
    const panel=this._noticeEligible()?this._noticeBounds():null;
    const notice=panel?{...panel.panel,x:panel.bounds.x+panel.panel.x-bounds.x,y:panel.bounds.y+panel.panel.y-bounds.y}:null;
    return {width:bounds.width,height:bounds.height,orb:{x:center.x-24,y:center.y-24},center,notice:notice??null,
      direction:arc?.direction??null,radius:arc?.radius??null,sweepDegrees:arc?.sweepDegrees??null,guide,
      actions:offsets.map((offset,index)=>({action:MENU_ACTIONS[index],...offset}))};
  }
  get(){return {locale:this.locale,documentTitle:t('common','window.orb',this.locale,{short_name:SHORT_NAME}),enabled:this.enabled,visible:this.visible,hostMinimized:this.hostMinimized,expanded:this.expanded,
    busy:this.busy,processing:this.processing,unread:this.unread,surfaceActive:this.surfaceActive,expansionPending:this.expansionRequested&&!this.expanded,
    taskActivity:{...this.taskActivity},notice:{visible:!!(this._noticeEligible()&&this._noticeBounds()),text:this.notice?.text??null,eventId:this.notice?.eventId??null,expiresAt:this.notice?.expiresAt??null},
    dragging:!!this.drag,loaded:this.loaded,disposed:this.disposed,error:this.error?t('host',this.error,this.locale):this.error,
    captureAllowed:false,captureExclusion:'REQUESTED',bounds:this._alive()?{...this._geometry().bounds}:null,shape:this._shape().map(value=>({...value})),anchor:this.getAnchor(),layout:this._layout(),actions:[...ACTIONS]};}
  _publish(){if(this._alive()){this._applyShape();if(this.loaded)this._window.webContents.send(STATE_CHANNEL,this.get());}}
  _fatal(error){
    if(this.disposed)return;
    this.error=error.message;this.loaded=false;this.visible=false;this.expanded=false;this.shapeExpanded=false;this.drag=null;this._cancelWatch();this._cancelShrink();this._cancelLeave();this._clearNotice();
    if(this._alive())this._window.hide();
    try{this.onError(error);}catch{/* Error callbacks cannot destroy a bound HWND. */}
  }
  _syncVisibility(){
    if(!this._alive())return;
    const show=this.enabled&&(this.hostMinimized||this.workflowActive)&&this.loaded&&!this.isClosing();
    if(show&&!this.visible){this._window.showInactive();this.visible=true;}
    else if(!show){this.drag=null;this._clearNotice();this._setExpanded(false,true);this._window.hide();this.visible=false;}
  }
  async setEnabled(enabled){
    if(typeof enabled!=='boolean')throw new Error('An explicit orb preference is required');
    if(this.disposed)throw new Error('Assistant orb is disposed');
    this.enabled=enabled;await this.ready;if(!this.disposed){this._syncVisibility();this._publish();}return this.get();
  }
  async setHostMinimized(minimized){
    if(typeof minimized!=='boolean')throw new Error('An explicit host presentation is required');
    if(this.disposed)throw new Error('Assistant orb is disposed');
    this.hostMinimized=minimized;
    if(!minimized){this.drag=null;this._syncVisibility();this._publish();}
    await this.ready;if(!this.disposed){this._syncVisibility();this._publish();}return this.get();
  }
  async setWorkflowActive(active){
    if(typeof active!=='boolean')throw new TypeError('An explicit workflow presentation is required');
    if(this.disposed)return;
    this.workflowActive=active;
    await this.ready;
    if(!this.disposed){this._syncVisibility();this._publish();}
  }
  _setExpanded(expanded,immediate=false){
    this._cancelShrink();this._cancelLeave();
    if(!this._alive())return;
    if(expanded){
      if(this.watchBlocked||!this._geometry().arc||!this.visible||this.drag||this.isClosing()||this.busy&&!this.expanded)return;
      if(this.expanded||this.expansionRequested)return;
      this.shapeExpanded=false;this._watchExpansion();this._publish();return;
    }
    this._cancelWatch();this.watchBlocked=false;this.expanded=false;
    if(immediate)this.shapeExpanded=false;
    this._publish();
    const shrink=()=>{this.shrinkTimer=null;if(this._alive()&&!this.expanded){this.shapeExpanded=false;this._refreshNotice();}};
    if(immediate||!this.visible)shrink();else this.shrinkTimer=this.clock.setTimeout(shrink,EXIT_MS);
  }
  collapse(){this._setExpanded(false,true);return this.get();}
  setFeedback({busy,unread}){
    if(typeof busy!=='boolean'||typeof unread!=='boolean')throw new Error('Explicit quick surface feedback required');
    if(this.disposed)return this.get();
    this.processing=busy;this.unread=unread;this._refreshNotice();return this.get();
  }
  setTaskActivity(value){
    const next=taskActivity(value);if(this.disposed)return this.get();
    this.taskActivity=next;
    if(next.unread===0&&next.attention===0)this._clearNotice();
    if(next.eventId&&!this.seenEvents.has(next.eventId)){
      // Bound memory without evicting identities and accidentally re-toasting.
      const capacity=this.seenEvents.size<4096;if(capacity)this.seenEvents.add(next.eventId);
      if(capacity&&next.notice&&(next.unread>0||next.attention>0)&&this.visible&&!this.isClosing()){
        this._clearNotice();const notice={text:next.notice,eventId:next.eventId,expiresAt:this.clock.now()+NOTICE_MS};this.notice=notice;
        this.noticeTimer=this.clock.setTimeout(()=>{if(this.notice!==notice||this.disposed)return;this.noticeTimer=null;this.notice=null;this._refreshNotice();},NOTICE_MS);
      }
    }
    this._refreshNotice();return this.get();
  }
  setSurfaceActive(active,surfaceId=null){
    if(typeof active!=='boolean')throw new Error('An explicit quick surface state is required');
    if(surfaceId!==null&&(!active||!['quickPopover','annotationOverlay'].includes(surfaceId)))throw new Error('An explicit visible quick surface identity is required');
    if(this.disposed)return this.get();
    const previous=this.surfaceId;
    this.surfaceActive=active;this.surfaceId=active?surfaceId:null;
    this._syncVisibility();
    // The overlay may be shown after the popover's asynchronous PNG decode.
    // Raise only after that real surface transition, never every publication.
    if(this.visible&&this._alive()&&!this.isClosing()&&this.surfaceId!==null&&this.surfaceId!==previous)this._window.moveTop?.();
    this._publish();return this.get();
  }
  _trusted(event){
    return this._alive()&&event?.sender===this._window.webContents&&event.senderFrame===this._window.webContents.mainFrame&&
      event.senderFrame?.url===this.pageURL&&this._window.webContents.getURL()===this.pageURL;
  }
  async _receive(event,payload){
    if(!this._trusted(event))throw new Error('Untrusted assistant orb sender');
    if(!payload||typeof payload!=='object'||Array.isArray(payload)||typeof payload.kind!=='string')throw new Error('Invalid orb event');
    const keys=Object.keys(payload).sort().join(',');
    if(payload.kind==='state'&&keys==='kind')return this.get();
    if(!this.visible||!this.enabled||this.isClosing())throw new Error('Assistant orb is not active');
    if(payload.kind==='hover'&&keys==='inside,kind'&&typeof payload.inside==='boolean'){
      if(payload.inside)this._setExpanded(true);else{this.watchBlocked=false;this._scheduleLeave();}return this.get();
    }
    if(payload.kind==='dismiss'&&keys==='kind'){this._clearNotice();this._setExpanded(false);return this.get();}
    if(payload.kind==='drag'&&keys==='kind,phase'&&['start','move','end'].includes(payload.phase)){
      if(this.busy)throw new Error('Assistant orb is busy');
      const cursor=this.screen.getCursorScreenPoint();
      if(!Number.isFinite(cursor.x)||!Number.isFinite(cursor.y))throw new Error('Current cursor position is unavailable');
      if(payload.phase==='start'){this._setExpanded(false,true);this.drag={cursor:{...cursor},bounds:{...this.bubble}};}
      else if(payload.phase==='move'){
        if(!this.drag)throw new Error('Orb drag has not started');
        this.bubble=clampOrb({x:this.drag.bounds.x+cursor.x-this.drag.cursor.x,y:this.drag.bounds.y+cursor.y-this.drag.cursor.y},this._area(cursor));
        this._geometry();
      }else this.drag=null;
      this._refreshNotice();return this.get();
    }
    if(payload.kind==='action'&&keys==='action,kind'&&ACTIONS.includes(payload.action)){
      if(this.busy||this.drag)return {status:'BUSY'};
      if(payload.action==='tasks')this._clearNotice();
      this.busy=true;this.error=null;if(!['ask','annotate','tasks'].includes(payload.action))this._setExpanded(false,true);this._publish();
      try{
        // The host freezes external context before its in-place quick surface.
        // Only explicit show-agent/workbench actions may reveal Main.
        await this.onAction(payload.action,this.getAnchor());
        return {status:'ACTION_DELIVERED',action:payload.action};
      }catch(error){
        if(!this.disposed){
          this.error='操作未完成，请再次打开快捷入口。';
          if(!this.isClosing()){try{this.onError(error);}catch{}}
        }
        return {status:'ACTION_FAILED',action:payload.action};
      }finally{this.busy=false;if(!this.disposed)this._refreshNotice();}
    }
    throw new Error('Unsupported assistant orb event');
  }
  dispose(){
    if(this.disposed)return;
    this._cancelWatch();this.disposed=true;this.enabled=false;this.visible=false;this.drag=null;this._cancelShrink();this._cancelLeave();this._clearNotice();
    this.ipcMain.removeHandler(CHANNEL);
    this.screen.removeListener?.('display-removed',this._displayChanged);
    this.screen.removeListener?.('display-metrics-changed',this._displayChanged);
    if(!this._window.isDestroyed())this._window.destroy();
  }
}
module.exports={AssistantOrb,ACTIONS,CHANNEL,STATE_CHANNEL,clampOrb,expandedBounds,arcLayout,orbHtml,noticeLayout};
