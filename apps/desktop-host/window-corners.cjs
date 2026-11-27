'use strict';

const WINDOW_RADIUS = 18;
const COORDINATES = 'PHYSICAL_SCREEN_PIXELS_THREAD_PMV2';

function roundWindowShape(width,height,radius=WINDOW_RADIUS){
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||width>1e6||height>1e6)
    throw new Error('Positive bounded whole-window DIP dimensions are required');
  const r=Math.min(radius,Math.floor((width-1)/2),Math.floor((height-1)/2));
  if(r<1)return [{x:0,y:0,width,height}];
  const rows=[];
  for(let y=0;y<r;y++){
    const inset=Math.ceil(r-Math.sqrt(r*r-(r-y-0.5)**2));
    rows.push({x:inset,y,width:width-2*inset,height:1},
      {x:inset,y:height-y-1,width:width-2*inset,height:1});
  }
  rows.push({x:0,y:r,width,height:height-2*r});
  return rows;
}

class OwnedWindowCorners {
  constructor({window,screen,readGeometry,getPresentationEpoch=()=>0,isClosing=()=>false,platform=process.platform,systemVersion,rectangular=false,
    radius=WINDOW_RADIUS,setTimeout:later=global.setTimeout,clearTimeout:cancel=global.clearTimeout,onError=()=>{}}) {
    if(!window||!Number.isInteger(radius)||radius<1||radius>WINDOW_RADIUS)throw new Error('An owned window and bounded corner radius are required');
    Object.assign(this,{window,screen,readGeometry,getPresentationEpoch,isClosing,platform,radius,later,cancel,onError});
    const version=typeof systemVersion==='string'&&/^(\d+)\.(\d+)\.(\d+)$/.exec(systemVersion);
    this.strategy=rectangular?'RECTANGULAR':platform==='win32'&&version&&Number(version[1])>=6
      ?Number(version[3])>=22000?'NATIVE':'SYSTEM_RECTANGLE':platform==='darwin'?'NATIVE':'UNVERIFIED';
    this.disposed=false;this.started=false;this.listeners=[];this.screenListeners=[];this.epoch=0;this.timer=null;this.flight=null;
    this.pending=false;this.applied=false;this.lastHash=null;this.sourceKey=null;
    this.state={strategy:this.strategy,status:'NOT_VERIFIED',experimental:false,native_verified:false,
      ...(this.strategy==='SYSTEM_RECTANGLE'?{reason:'WIN10_CUSTOM_MASK_DISABLED_AFTER_NATIVE_CLIPPING'}:{})};
    if(window.isDestroyed?.()){this.dispose();return;}
    const bind=(event,handler)=>{window.on(event,handler);this.listeners.push([event,handler]);};
    for(const event of ['maximize','unmaximize','enter-full-screen','leave-full-screen','restore'])bind(event,()=>this.apply());
    for(const event of ['will-resize','will-move'])bind(event,()=>{if(this.started&&this.capable())this.apply();});
    for(const event of ['resize','move'])bind(event,()=>{
      if(this.started&&this.capable()&&this.usable()){
        const stamp=this._stamp();
        // SetShape can itself send window-position notifications. A region
        // change without a geometry/presentation change must not start a loop.
        if(stamp!==this.lastEventStamp){this.lastEventStamp=stamp;this.apply();}
      }
    });
    if(typeof screen?.on==='function'&&typeof screen?.removeListener==='function'){
      for(const event of ['display-metrics-changed','display-added','display-removed']){
        const handler=()=>{if(this.started&&this.capable())this.apply();};
        screen.on(event,handler);this.screenListeners.push([event,handler]);
      }
    }
    bind('closed',()=>this.dispose());
    this._reflect();
  }
  alive(){return !this.disposed&&!this.window.isDestroyed?.();}
  usable(){return this.alive()&&!this.isClosing();}
  expanded(){return this.window.isMaximized?.()===true||this.window.isFullScreen?.()===true;}
  capable(){return this.strategy==='SYSTEM_RECTANGLE'&&typeof this.readGeometry==='function'&&
    typeof this.screen?.screenToDipRect==='function'&&typeof this.window.setShape==='function';}
  get(){return {...this.state,disposed:this.disposed,started:this.started,pending:!!(this.timer||this.flight),
    ...(!this.disposed&&this.isClosing()?{status:'CLOSING_NO_REGION_WRITES'}:{})};}
  _reflect(){
    const expanded=this.expanded();
    this.state={...this.state,expanded,radius_dip:expanded||['RECTANGULAR','SYSTEM_RECTANGLE'].includes(this.strategy)?0:null,
      status:this.strategy==='RECTANGULAR'?'RECTANGULAR_WINDOW_REQUESTED':this.strategy==='SYSTEM_RECTANGLE'?'NATIVE_RECTANGULAR_PRESERVED':
        this.strategy==='NATIVE'?'NATIVE_SYSTEM_REQUESTED':'NATIVE_OUTER_CORNERS_UNVERIFIED'};
  }
  _error(error){try{this.onError(error);}catch{} }
  _clear(){
    if(!this.applied||!this.usable())return;
    this.applied=false;this.lastHash=null;this.lastEventStamp=this._stamp();
    try{this.window.setShape([]);}catch(error){this.applied=true;throw error;}
  }
  _cancelTimer(){if(this.timer!==null){this.cancel(this.timer);this.timer=null;}}
  start(){
    if(!this.alive()||this.started)return this.get();
    this.started=true;return this.apply();
  }
  apply(){
    if(!this.alive())return this.get();
    this.epoch++;this._cancelTimer();
    this.flight?.controller.abort();
    if(!this.usable())return this.get();
    try{this._clear();}catch(error){
      this._error(error);this.state={...this.state,status:'OWN_MASK_CLEAR_FAILED',native_verified:false};return this.get();
    }
    this._reflect();
    if(this.started&&this.capable()&&!this.expanded()&&!this.window.isMinimized?.())this._schedule();
    return this.get();
  }
  _schedule(){
    if(!this.usable()||!this.started)return;
    if(this.flight){this.pending=true;return;}
    this._cancelTimer();
    this.timer=this.later(()=>{this.timer=null;void this._run();},70);
  }
  _stamp(){
    const bounds=this.window.getBounds();
    return JSON.stringify([bounds.x,bounds.y,bounds.width,bounds.height,this.expanded(),
      this.window.isMinimized?.()===true,this.getPresentationEpoch()]);
  }
  _valid(packet){
    const rect=packet?.window_bounds_physical;
    if(packet?.status!=='OWN_MAIN_GEOMETRY'||packet.coordinate_space!==COORDINATES||
      !/^[a-f0-9]{64}$/.test(packet.geometry_sha256??'')||
      !packet.surface_ref||!packet.host_scope||!Number.isInteger(packet.input_epoch)||
      !rect||!['x','y','width','height'].every(key=>Number.isInteger(rect[key]))||
      rect.width<1||rect.height<1||rect.width>1e6||rect.height>1e6||packet.minimized!==false||packet.maximized!==false)
      throw new Error('Stable exact owned Main geometry is unavailable');
    const sourceKey=JSON.stringify([packet.surface_ref,packet.host_scope,packet.input_epoch]);
    if(this.sourceKey&&this.sourceKey!==sourceKey)throw new Error('Owned Main geometry lifetime changed');
    this.sourceKey=sourceKey;return rect;
  }
  async _run(){
    if(!this.usable()||!this.started||!this.capable()||this.expanded()||this.window.isMinimized?.())return;
    if(this.flight){this.pending=true;return;}
    const epoch=this.epoch,stamp=this._stamp(),controller=new AbortController();
    this.lastEventStamp=stamp;
    const flight={controller};this.flight=flight;this.pending=false;
    const deadline=this.later(()=>controller.abort(),1800);
    try{
      const packet=await this.readGeometry({signal:controller.signal});
      if(!this.usable()||controller.signal.aborted||epoch!==this.epoch||this.expanded())return;
      if(stamp!==this._stamp()){this.epoch++;this.pending=true;return;}
      const rect=this._valid(packet);
      const dip=this.screen.screenToDipRect(this.window,rect);
      const shape=roundWindowShape(dip.width,dip.height,this.radius);
      if(this.lastHash!==packet.geometry_sha256){
        if(!this.usable())return;
        this.window.setShape(shape);this.applied=true;this.lastHash=packet.geometry_sha256;
      }
      this.state={strategy:'SYSTEM_RECTANGLE',status:'OWN_GEOMETRY_MASK_APPLIED_UNVERIFIED',
        experimental:true,native_verified:false,expanded:false,radius_dip:this.radius,
        geometry_sha256:packet.geometry_sha256,geometry_source:COORDINATES};
    }catch(error){
      if(this.usable()&&epoch===this.epoch){this._reflect();this.state.reason='OWN_MAIN_GEOMETRY_UNAVAILABLE';if(!controller.signal.aborted)this._error(error);}
    }finally{
      this.cancel(deadline);if(this.flight===flight)this.flight=null;
      if(this.pending&&this.usable()&&!this.expanded()&&!this.window.isMinimized?.()){this.pending=false;this._schedule();}
    }
  }
  dispose(){
    if(this.disposed)return;
    this.epoch++;this._cancelTimer();this.flight?.controller.abort();this.pending=false;
    try{this._clear();}catch(error){this._error(error);}
    this.disposed=true;
    for(const [event,handler]of this.listeners)this.window.removeListener(event,handler);
    for(const [event,handler]of this.screenListeners)this.screen.removeListener(event,handler);
    this.screenListeners=[];
    this.listeners=[];this.state={...this.state,status:'DISPOSED'};
  }
}
module.exports={OwnedWindowCorners,WINDOW_RADIUS,roundWindowShape};
