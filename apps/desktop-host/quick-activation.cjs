'use strict';
const {performance}=require('node:perf_hooks');
const readTimeout=options=>options?.phase==='begin'&&options?.trigger==='CTRL_SHIFT_SPACE'?1250:450;
const valid=value=>value?.schema==='eeas.quick-activation-state.v1'&&value.status==='OBSERVED'&&
  /^[a-f0-9]{32}$/.test(value.authority_generation??'')&&/^[a-f0-9]{64}$/.test(value.foreground_identity_sha256??'')&&
  /^[a-f0-9]{64}$/.test(value.host_scope_sha256??'')&&
  Number.isSafeInteger(value.last_input_tick)&&value.last_input_tick>=0&&value.last_input_tick<=0xffffffff&&value.automatic_input_authorized===false;

class QuickActivation {
  constructor(read,{clock=()=>performance.now()}={}){this.read=read;this.clock=clock;}
  async begin(trigger){
    if(typeof this.read!=='function')return null;
    const started=this.clock();
    try{
      const options={phase:'begin',...(trigger==='CTRL_SHIFT_SPACE'?{trigger}:{})};
      const value=await this.read(options);
      if(!valid(value)||this.clock()-started>readTimeout(options)+50||this.clock()<started)return null;
      return {started,value:Object.freeze({...value})};
    }catch{return null;}
  }
  async unchanged(lease){
    if(!lease||!valid(lease.value)||this.clock()-lease.started>10000||this.clock()<lease.started)return false;
    const started=this.clock();
    try{
      const after=await this.read({phase:'verify'}),now=this.clock();
      return valid(after)&&now>=started&&now-started<=500&&now>=lease.started&&now-lease.started<=10000&&
        after.authority_generation===lease.value.authority_generation&&
        after.host_scope_sha256===lease.value.host_scope_sha256&&
        after.foreground_identity_sha256===lease.value.foreground_identity_sha256&&after.last_input_tick===lease.value.last_input_tick;
    }catch{return false;}
  }
}
module.exports={QuickActivation,readTimeout};
