'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');

function createNativeAppendActivation({root,dataDir,platform=process.platform,launch=spawn}) {
  const pending=new Set();
  const activate=async function(privateTicket) {
    if(platform!=='win32'||!fs.existsSync(path.join(root,'PACKAGE_MANIFEST.json'))) {
      return {status:'OS_REFUSED',reason:'EXACT_PACKAGED_WINDOWS_HANDOFF_REQUIRED',automatic_retry:false};
    }
    const python=path.join(root,'runtime','python','python.exe');
    const bootstrap=path.join(root,'scripts','native_package_bootstrap.py');
    const input=Buffer.from(JSON.stringify(privateTicket),'utf8');
    if(input.length>32768)throw new Error('Private activation request is too large');
    const env={...process.env,EEAS_DATA_DIR:dataDir,PYTHONDONTWRITEBYTECODE:'1',
      PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8',TEMP:path.join(dataDir,'tmp'),TMP:path.join(dataDir,'tmp')};
    for(const key of ['EEAS_OPENCODE_PROFILE','EEAS_OPENCODE_API_KEY','ANTHROPIC_API_KEY','OPENAI_API_KEY',
      'CLAUDE_CODE_OAUTH_TOKEN','CHATGPT_ACCESS_TOKEN','PYTHONPATH','PYTHONHOME'])delete env[key];
    return new Promise(resolve=>{
      let settled=false,output=Buffer.alloc(0),stderrBytes=0,child;
      const started=Date.now();
      const job={stop:()=>unknown()};
      const finish=value=>{
        if(settled)return;settled=true;clearTimeout(timer);pending.delete(job);
        const record={operation_id:privateTicket.operation_id,ticket_sha256:privateTicket.ticket_ref?.sha256,
          status:value.status,phase:value.phase??'HELPER_OUTCOME_NOT_OBSERVED',elapsed_ms:Date.now()-started,
          input_dispatched_by_helper:false};
        try{fs.appendFileSync(path.join(dataDir,'native-append-helper.jsonl'),JSON.stringify(record)+'\n',{mode:0o600});}
        catch{/* A missing private diagnostic is never activation proof. */}
        resolve(value);
      };
      const unknown=()=>{finish({status:'UNKNOWN',automatic_retry:false});if(child&&child.exitCode===null)child.kill();};
      // Only this newly spawned Main child is terminated; never a target app.
      const timer=setTimeout(unknown,45000);
      pending.add(job);
      try {
        child=launch(python,['-B',bootstrap,'--script','packages/desktop/native_append_handoff.py'],{
          cwd:root,env,windowsHide:true,stdio:['pipe','pipe','pipe'],shell:false});
      } catch { unknown();return; }
      child.on('error',unknown);
      child.stdout.on('data',part=>{
        if(output.length+part.length>8192){unknown();return;}
        output=Buffer.concat([output,part]);
      });
      child.stderr.on('data',part=>{
        stderrBytes+=part.length;
        if(stderrBytes>8192)unknown();
      });
      child.on('close',(code,signal)=>{
        if(signal){unknown();return;}
        try {
          const value=JSON.parse(output.toString('utf8'));
          if(code===0&&value.status==='ACTIVATION_REQUEST_RETURNED')finish({status:value.status,phase:value.phase});
          else if(value.status==='OS_REFUSED')finish({status:'OS_REFUSED',phase:value.phase});
          else unknown();
        } catch { unknown(); }
      });
      child.stdin.on('error',unknown);
      child.stdin.end(input);
    });
  };
  activate.cancelPending=()=>{const count=pending.size;for(const job of [...pending])job.stop();return {status:count?'OWN_HELPER_STOP_REQUESTED':'NO_OWN_HELPER_PENDING',owned_helpers:count,foreground_effect:count?'UNKNOWN':'NOT_OBSERVED'};};
  return activate;
}

module.exports={createNativeAppendActivation};
