export type ConversationSidebarKind='native'|'original'|'discovery';
export type ConversationSidebarScope='current'|'all'|'unassigned';
export type ConversationSidebarGroupId='today'|'yesterday'|'week'|'older'|'undated';
export interface ConversationSidebarItem {
  id:string;kind:ConversationSidebarKind;projectId:string|null;projectName?:string;title:string;
  lastOperationId?:string;updatedAt?:string|number|null;readOnly?:boolean;source:unknown;
  organization?:{revision:number;archived:boolean};
}
export interface ConversationSidebarProjection {
  groups:{id:ConversationSidebarGroupId;items:ConversationSidebarItem[]}[];
  projects:{id:string;projectId:string|null;name:string;items:ConversationSidebarItem[]}[];
  total:number;matched:number;invalid:number;
}
export type SidebarProject={id:string;name:string};
export const sidebarProjectKey=(projectId:string|null)=>JSON.stringify(['project',projectId]);
const normalize=(value:string)=>value.normalize('NFKC').toLocaleLowerCase().trim();
export function conversationSidebarTimestamp(value:ConversationSidebarItem['updatedAt']):number|null{
  if(value===null||value===undefined||value==='')return null;
  const result=typeof value==='number'?value:Date.parse(value);
  return Number.isFinite(result)&&Number.isFinite(new Date(result).getTime())?result:null;
}
export function projectConversationSidebar(items:readonly ConversationSidebarItem[],options:{query?:string;scope?:ConversationSidebarScope;currentProjectId?:string|null;projectCatalog?:readonly SidebarProject[];defaultProjectName?:string;now?:number}={}):ConversationSidebarProjection{
  const now=options.now??Date.now(),today=new Date(now);today.setHours(0,0,0,0);
  const yesterday=new Date(today);yesterday.setDate(yesterday.getDate()-1);
  const week=new Date(today);week.setDate(week.getDate()-7);
  const tokens=normalize((options.query??'').slice(0,512)).split(/\s+/u).filter(Boolean);
  const counts=new Map<string,number>();for(const item of items)counts.set(item.id,(counts.get(item.id)??0)+1);
  const accepted=items.filter(item=>typeof item.id==='string'&&!!item.id&&counts.get(item.id)===1&&['native','original','discovery'].includes(item.kind)&&typeof item.title==='string'&&(item.projectId===null||typeof item.projectId==='string'));
  const scoped=accepted.filter(item=>options.scope==='unassigned'?item.projectId===null:options.scope==='current'?!!options.currentProjectId&&item.projectId===options.currentProjectId:true);
  const catalog=new Map<string,string>();
  for(const project of (options.projectCatalog??[]).slice(0,2000))if(typeof project.id==='string'&&project.id&&typeof project.name==='string'&&project.name.trim())catalog.set(project.id,project.name);
  const projectName=(item:ConversationSidebarItem)=>item.projectId===null?options.defaultProjectName??'':catalog.get(item.projectId)??item.projectName??'';
  const matched=scoped.filter(item=>{const haystack=normalize(item.title.slice(0,4096)+' '+projectName(item).slice(0,1024));return tokens.every(token=>haystack.includes(token));});
  const sorted=matched.map((item,index)=>({item,index,stamp:conversationSidebarTimestamp(item.updatedAt)})).sort((a,b)=>(b.stamp??-Infinity)-(a.stamp??-Infinity)||a.index-b.index);
  const map=new Map<ConversationSidebarGroupId,ConversationSidebarItem[]>();
  for(const row of sorted){const stamp=row.stamp,id:ConversationSidebarGroupId=stamp===null||stamp>now+300000?'undated':stamp>=today.getTime()?'today':stamp>=yesterday.getTime()?'yesterday':stamp>=week.getTime()?'week':'older';const group=map.get(id)??[];group.push(row.item);map.set(id,group);}
  // Project organization is display metadata. Never read the transcript/source,
  // mutate an item's project, or create a real project for the default Chats.
  const projects=new Map<string,ConversationSidebarProjection['projects'][number]>();
  const admitProject=(pid:string|null,name:string)=>{
    if(options.scope==='unassigned'&&pid!==null||options.scope==='current'&&(!options.currentProjectId||pid!==options.currentProjectId))return;
    const id=sidebarProjectKey(pid);if(!projects.has(id))projects.set(id,{id,projectId:pid,name,items:[]});
  };
  if(!tokens.length||tokens.every(token=>normalize(options.defaultProjectName??'').includes(token)))admitProject(null,options.defaultProjectName??'');
  for(const [pid,name]of catalog)if(!tokens.length||tokens.every(token=>normalize(name).includes(token)))admitProject(pid,name);
  for(const {item}of sorted){admitProject(item.projectId,projectName(item));projects.get(sidebarProjectKey(item.projectId))!.items.push(item);}
  const rank=(pid:string|null)=>pid===(options.currentProjectId??null)?0:pid===null?1:2;
  const recent=(project:ConversationSidebarProjection['projects'][number])=>{const stamp=conversationSidebarTimestamp(project.items[0]?.updatedAt);return stamp!==null&&stamp<=now+300000?stamp:-Infinity;};
  const folders=[...projects.values()].sort((a,b)=>rank(a.projectId)-rank(b.projectId)||(recent(b)-recent(a)||0));
  return {groups:(['today','yesterday','week','older','undated']as const).filter(id=>map.has(id)).map(id=>({id,items:map.get(id)!})),projects:folders,total:scoped.length,matched:matched.length,invalid:items.length-accepted.length};
}
