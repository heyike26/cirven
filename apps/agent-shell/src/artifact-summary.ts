export const planningToolIds=['review_sources','validate_network','acceptance_coverage','pending_questions','prompt_preview','planning_templates']as const;
export type PlanningToolId=typeof planningToolIds[number];
export interface PlanningCapabilitySummary {tools:{id:string;title:string;description:string}[];skills:string[];projectScoped:boolean}
const own=(value:unknown,key:string):unknown=>{try{if(!value||typeof value!=='object')return;const descriptor=Object.getOwnPropertyDescriptor(value,key);return descriptor&&'value'in descriptor?descriptor.value:undefined;}catch{return;}};
const short=(value:unknown,max:number):string|undefined=>typeof value==='string'&&value.trim()?value.trim().slice(0,max)+(value.trim().length>max?'…':''):undefined;
export function artifactSummary(value:unknown):{title?:string;summary?:string;status?:string}{
  return {title:short(own(value,'filename'),500)??short(own(value,'title'),500)??short(own(value,'name'),500),summary:short(own(value,'summary'),1000),status:short(own(value,'status'),100)};
}
export function planningCapabilitySummary(value:unknown):PlanningCapabilitySummary|undefined{
  const tools=own(value,'tools'),skills=own(value,'skills'),mcp=own(value,'mcp');
  if(!Array.isArray(tools)||tools.length>64||!Array.isArray(skills)||skills.length>128)return;
  const result:PlanningCapabilitySummary={tools:[],skills:[],projectScoped:own(mcp,'project_scope')==='ONE_SELECTED_PROJECT'},seen=new Set<string>();
  for(const row of tools){const id=own(row,'id'),title=own(row,'title'),description=own(row,'description');if(typeof id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(id)||seen.has(id)||typeof title!=='string'||title.length>500||typeof description!=='string'||description.length>4000)return;seen.add(id);result.tools.push({id,title,description});}
  for(const row of skills){if(typeof row!=='string'||row.length>100)return;result.skills.push(row);}
  return result;
}
export function boundedArtifactDiagnostic(value:unknown,maxCharacters=12000):{text:string;truncated:boolean}{
  const cap=Math.max(256,Math.min(16000,maxCharacters)),seen=new WeakSet<object>();let nodes=0,truncated=false;
  function walk(item:unknown,depth:number):unknown{
    if(++nodes>200){truncated=true;return '[Limit]';}
    if(item===null||typeof item==='boolean'||typeof item==='number')return item;
    if(typeof item==='string'){if(item.length>1024){truncated=true;return item.slice(0,1024)+'…';}return item;}
    if(typeof item!=='object')return '['+typeof item+']';
    if(seen.has(item))return '[Circular]';seen.add(item);
    if(depth>=4){truncated=true;return '[Nested content]';}
    const result:Record<string,unknown>|unknown[]=Array.isArray(item)?[]:{};let count=0;
    try{for(const key in item){if(!Object.hasOwn(item,key))continue;if(++count>32||nodes>=200){truncated=true;break;}const descriptor=Object.getOwnPropertyDescriptor(item,key);const entry=/(?:password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|cookie|credential)/i.test(key)?'[Hidden]':descriptor&&'value'in descriptor?walk(descriptor.value,depth+1):'[Accessor omitted]';if(Array.isArray(result))result.push(entry);else Object.defineProperty(result,key,{value:entry,enumerable:true,configurable:true,writable:true});}}
    catch{return '[Unavailable]';}
    return result;
  }
  let text:string;try{text=JSON.stringify(walk(value,0),null,2)??'null';}catch{text='[Unavailable]';}
  if(text.length>cap){text=text.slice(0,cap)+'\n…';truncated=true;}
  return {text,truncated};
}
