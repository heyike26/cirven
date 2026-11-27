'use strict';
// Bounded display only. The adapter rechecks the exact original request and
// the original AgentSessions service owns authorization and execution.
function projectPending(item){
  const n=item?.native_request||{},questions=Array.isArray(n.questions)?n.questions:[],patterns=Array.isArray(n.patterns)?n.patterns:[];
  const bounded=(v,max)=>typeof v==='string'&&v.length<=max;
  const complete=item?.kind==='QUESTION'?
    questions.length>0&&questions.length<=16&&questions.every(q=>bounded(q.question,2000)&&bounded(q.header||'',100)&&
      Array.isArray(q.options)&&q.options.length<=32&&q.options.every(o=>bounded(o.label,300)&&bounded(o.description||'',1000))):
    item?.kind==='PERMISSION'&&bounded(n.permission,200)&&patterns.length<=64&&patterns.every(p=>bounded(p,1000));
  const clip=(v,max)=>typeof v==='string'?v.slice(0,max):'';
  return {request_ref:{id:item?.request_ref?.id,sha256:item?.request_ref?.sha256,generation:item?.request_ref?.generation},
    kind:item?.kind,display_complete:complete,
    allowed_choices:(Array.isArray(item?.allowed_choices)?item.allowed_choices:[]).filter(v=>['reply','reject','once','always'].includes(v)),
    reply_is_execution_authorization:item?.reply_is_execution_authorization===true,
    native_request:{id:clip(n.id,110),permission:clip(n.permission,200),patterns:patterns.slice(0,64).map(v=>clip(v,1000)),
      questions:questions.slice(0,16).map(q=>({question:clip(q.question,2000),header:clip(q.header,100),multiple:q.multiple===true,custom:q.custom!==false,
        options:(Array.isArray(q.options)?q.options:[]).slice(0,32).map(o=>({label:clip(o.label,300),description:clip(o.description,1000)}))}))}};
}
module.exports={projectPending};
