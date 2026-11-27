'use strict';

// Observe the one local supervisor. This host never prompts/retries a task.
class TaskActivityHost {
  constructor({request,watchRequest,notifications,onSnapshot=()=>{},onError=()=>{},isClosing=()=>false,clock}) {
    Object.assign(this,{request,watchRequest,notifications,onSnapshot,onError,isClosing});
    this.clock=clock??{setTimeout,clearTimeout};this.timer=null;this.pending=null;this.snapshot=null;
    this.closed=false;this.started=false;this.primed=false;this.readStatus='INITIAL';
    this.watchController=null;this.watchPending=null;this.watchRetry=null;this.watchRelease=null;
  }
  get(){return {snapshot:this.snapshot,readStatus:this.readStatus,notification:this.notifications?.get()??null};}
  start(){if(this.started||this.closed)return;this.started=true;void this.refresh();}
  async refresh(){
    if(this.closed||this.isClosing())return this.get();
    if(this.pending)return this.pending;
    if(this.timer!==null){this.clock.clearTimeout(this.timer);this.timer=null;}
    this.pending=(async()=>{
      try{
        const value=await this.request('/api/supervisor');
        if(!value?.settings||typeof value.settings.enabled!=='boolean'||typeof value.settings.notifications_enabled!=='boolean'||
          !Number.isSafeInteger(value.settings.interval_seconds)||value.settings.interval_seconds<5||value.settings.interval_seconds>3600||
          !Array.isArray(value.tasks)||value.tasks.length>16384||!Array.isArray(value.alerts)||value.alerts.length>1000)
          throw Error('Invalid local supervisor snapshot');
        if(this.closed||this.isClosing())return this.get();
        this.snapshot=JSON.parse(JSON.stringify(value));this.readStatus='READY';
        const initial=!this.primed;
        if(initial){this.notifications?.prime(value);this.primed=true;}
        else this.notifications?.observe(value);
        this.onSnapshot(this.snapshot,{initial});
        this._startWatch();
      }catch(error){
        if(!this.closed){this.readStatus='OBSERVATION_UNAVAILABLE';try{this.onError(error);}catch{}}
      }finally{
        this.pending=null;
        if(this.started&&!this.closed&&!this.isClosing()){
          // The API performs source checks at the user's interval. This GET
          // only projects cached facts and notices configuration changes.
          const seconds=Math.min(30,this.snapshot?.settings.interval_seconds??30);
          this.timer=this.clock.setTimeout(()=>{this.timer=null;void this.refresh();},seconds*1000);
        }
      }
      return this.get();
    })();
    return this.pending;
  }
  _startWatch(){
    if(!this.started||!this.watchRequest||this.watchPending||this.closed||!Number.isSafeInteger(this.snapshot?.change_sequence))return;
    const controller=new AbortController();this.watchController=controller;
    this.watchPending=(async()=>{
      while(!this.closed&&!this.isClosing()&&!controller.signal.aborted){
        try{
          const after=this.snapshot.change_sequence;
          const value=await this.watchRequest(`/api/supervisor/changes?after=${after}`,controller.signal);
          if(this.closed||controller.signal.aborted||value?.closed)break;
          if(!Number.isSafeInteger(value?.change_sequence)||value.change_sequence<0)throw Error('Invalid supervisor change cursor');
          if(value.change_sequence!==after){
            await this.refresh();
            if(this.snapshot?.change_sequence===after)throw Error('Supervisor change snapshot is not yet readable');
          }
        }catch(error){
          if(this.closed||controller.signal.aborted)break;
          try{this.onError(error);}catch{}
          // A lost change channel retains cached facts; normal GET checks also
          // continue. The bounded retry is cancelled and joined during close.
          await new Promise(resolve=>{this.watchRelease=resolve;this.watchRetry=this.clock.setTimeout(()=>{
            this.watchRetry=null;this.watchRelease=null;resolve();
          },5000);});
        }
      }
    })().finally(()=>{this.watchPending=null;this.watchController=null;});
  }
  async close(){
    if(this.closed)return;this.closed=true;
    if(this.timer!==null){this.clock.clearTimeout(this.timer);this.timer=null;}
    this.watchController?.abort();
    if(this.watchRetry!==null){this.clock.clearTimeout(this.watchRetry);this.watchRetry=null;}
    this.watchRelease?.();this.watchRelease=null;
    this.notifications?.close();
    if(this.pending)await this.pending;
    if(this.watchPending)await this.watchPending;
  }
}
module.exports={TaskActivityHost};
