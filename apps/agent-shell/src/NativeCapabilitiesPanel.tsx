import {flushResourceAllowanceDrafts} from './resource-allowance-journal';
import {NativeReviewPicker} from './NativeReviewActions';
import type {NativePageCatalog,NativePageSelection} from './native-review';
import {Dialog} from '@kobalte/core/dialog';
import {Switch} from '@kobalte/core/switch';
import {createEffect,createMemo,createSignal,For,onCleanup,Show,untrack} from 'solid-js';
import {authoredErrorCopy,authoredText,type UIErrorCopy} from './authored-ui-copy';
import {t as uiText} from './ui-language';
import {AgentCapabilityClient,MCP_TIMEOUT_DEFAULT,MCP_TIMEOUT_MAX,MCP_TIMEOUT_MIN,agentCapabilityBridge,capabilityAvailability,capabilityBlockReason,capabilityExperienceOrigin,isCapabilityRejected,mcpConfiguration,probeStatus,skillText,type AgentCapabilities,type AgentMCP,type AgentSkill,type CapabilityReceipt,type MCPProbe} from './native-capabilities';
import OriginalWorkExperience from './OriginalWorkExperience';
import {TaskExperienceApi,type ExperienceOrigin,type ExperienceSourceWork} from './task-experience';
import {flushExperienceDrafts} from './task-experience-journal';
import {uiLocale} from './ui-language';
import {NativeToolsClient,chooseMCPTools,withMCPConnectionHints,nativeToolsBridge,scopeForTools,type MCPToolSelectionDraft,type NativeToolsCatalog} from './native-tools';
import {sameNativeReference,type NativeRef} from './native-collaboration';
import type {AgentSession} from './agent-sessions';
import './native-capabilities.css';

export type CapabilityTab='tools'|'mcp'|'skills';
export type NativeCapabilitiesPanelProps={open:boolean;onOpenChange:(open:boolean)=>void;initialTab?:CapabilityTab;owner?:AgentSession;onChooseTools?:(selection:MCPToolSelectionDraft)=>void;onOpenSessionSettings?:()=>void;review?:{catalog:NativePageCatalog;selection:NativePageSelection|null;onChange:(v:NativePageSelection|null)=>void;onChoose:()=>void}};
type Detail={kind:'skill'|'mcp'|'tool-group'|'experience-source';id:string}|{kind:'new-skill'|'new-mcp'};
type UnknownWrite={operation:string;id:string;expected:number;kind:'skills'|'mcp'};
type SkillDraft={id:string;content:string;enabled:boolean;basis?:NativeRef};
type MCPDraft={id:string;type:'local'|'remote';command:string;files:string;url:string;enabled:boolean;timeout:number;basis?:NativeRef};
const availabilityCopy={AVAILABLE:'可选择',DISABLED:'已停用',NOT_PROBED:'尚未读取工具',STALE:'工具目录已变化',FAILED:'连接失败',COLLISION:'工具名称冲突',INTEGRITY_ERROR:'来源已变化',TOO_LARGE:'工具目录过大'} as const;
const permissionCopy={allow:'允许',ask:'调用时询问',deny:'不允许',SCOPED:'按授权范围',UNKNOWN:'状态未知'} as const;
// A bounded convenience hint; the service still validates the complete frontmatter.
function importedSkillName(text:string):string|undefined{
 const header=/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(text)?.[1];if(!header)return;
 const names=header.split(/\r?\n/).filter(line=>/^name:/.test(line));if(names.length!==1)return;
 const raw=names[0].slice(5).trim(),name=/^([a-z0-9]+(?:-[a-z0-9]+)*)$/.exec(raw)?.[1]??/^(['"])([a-z0-9]+(?:-[a-z0-9]+)*)\1$/.exec(raw)?.[2];
 return name&&name.length<=64?name:undefined;
}
const emptySkill=():SkillDraft=>({id:'',content:'',enabled:false});
const emptyMCP=():MCPDraft=>({id:'',type:'local',command:'',files:'',url:'',enabled:false,timeout:MCP_TIMEOUT_DEFAULT});

/** One controlled dialog; configuration changes do not create or mutate an owner. */
export default function NativeCapabilitiesPanel(props:NativeCapabilitiesPanelProps){
 const bridge=agentCapabilityBridge(globalThis),client=bridge?new AgentCapabilityClient(bridge):undefined;
 const toolBridge=nativeToolsBridge(globalThis),toolsClient=toolBridge?new NativeToolsClient(toolBridge):undefined;
 const [tab,setTab]=createSignal<CapabilityTab>(props.initialTab??'tools'),[query,setQuery]=createSignal(''),[detail,setDetail]=createSignal<Detail>();
 const [state,setState]=createSignal<AgentCapabilities>(),[catalog,setCatalog]=createSignal<NativeToolsCatalog>(),[catalogScope,setCatalogScope]=createSignal('');
 const [busy,setBusy]=createSignal<Record<string,boolean>>({}),[errors,setErrors]=createSignal<Record<string,UIErrorCopy>>({}),[notice,setNotice]=createSignal(''),[conflicts,setConflicts]=createSignal<Record<string,boolean>>({}),[recoverable,setRecoverable]=createSignal<Record<string,boolean>>({});
 const [receipts,setReceipts]=createSignal<Record<string,CapabilityReceipt|MCPProbe>>({}),[unknown,setUnknown]=createSignal<Record<string,UnknownWrite>>({});
 const [selected,setSelected]=createSignal<string[]>([]),[selectionBasis,setSelectionBasis]=createSignal(''),[toolsInvalidation,setToolsInvalidation]=createSignal(0);
 const [skill,setSkill]=createSignal<SkillDraft>(emptySkill()),[mcp,setMCP]=createSignal<MCPDraft>(emptyMCP());
 const [experienceSource,setExperienceSource]=createSignal<ExperienceSourceWork>();
 let closePending=false;let sourceReturn:ExperienceOrigin|undefined;
 const skillDrafts=new Map<string,SkillDraft>(),mcpDrafts=new Map<string,MCPDraft>(),loadedDrafts=new Set<string>(),scrolls:Partial<Record<CapabilityTab,number>>={};
 const queries:Record<CapabilityTab,string>={tools:'',mcp:'',skills:''};
 const conflictDrafts=new Map<string,SkillDraft|MCPDraft>();
 const completions=new Map<string,(receipt:CapabilityReceipt)=>void>();
 let disposed=false,loadEpoch=0,toolsEpoch=0,editEpoch=0,wasOpen=false,listElement:HTMLDivElement|undefined,toolsRunning=false,toolsAgain=false,opener:HTMLElement|undefined,skipReturnFocus=false;
 const scopeKey=()=>JSON.stringify(scopeForTools(props.owner)??null);
 const detailID=()=>{const d=detail();return d&&'id'in d?d.id:'';};
 const keyFor=(kind:'skills'|'mcp',id:string)=>kind+':'+id;
 const detailKey=()=>{const d=detail();return d?.kind==='skill'?keyFor('skills',d.id):d?.kind==='mcp'?keyFor('mcp',d.id):d?.kind==='new-skill'?'skills:new':d?.kind==='new-mcp'?'mcp:new':'tools';};
 const pending=(key:string)=>busy()[key]===true;
 const blocked=(key:string)=>pending(key)||!!unknown()[key];
 const selectedStale=()=>!!selected().length&&selectionBasis()!==catalog()?.capability_binding_sha256;
 const toolsCurrent=()=>catalogScope()===scopeKey();
 const matches=(...values:(string|undefined)[])=>!query().trim()||values.some(value=>value?.toLocaleLowerCase().includes(query().trim().toLocaleLowerCase()));
 const skills=createMemo(()=>(state()?.skills??[]).filter(item=>matches(item.title,item.description,item.id)));
 const mcps=createMemo(()=>(state()?.mcp??[]).filter(item=>matches(item.id,item.type)));
 const toolServers=createMemo(()=>(toolsCurrent()?catalog()?.mcp_servers??[]:[]).filter(server=>matches(server.id,...server.tools.map(tool=>tool.name))));
 const currentMCP=()=>{
  const selected=detail();if(selected?.kind==='mcp')return state()?.mcp.find(item=>item.id===selected.id);
  if(selected?.kind!=='new-mcp')return;const saved=mcp();
  return saved.basis?state()?.mcp.find(item=>item.id===saved.id&&sameNativeReference(item.config_ref,saved.basis)):undefined;
 };
 const ownerChanged=()=>!!props.owner&&(props.owner.configuration_current===false||toolsCurrent()&&catalog()?.owner.status==='STALE');
 const ownerUnavailable=()=>!!props.owner&&toolsCurrent()&&catalog()?.owner.status==='UNAVAILABLE';
 onCleanup(()=>{disposed=true;loadEpoch++;toolsEpoch++;editEpoch++;completions.clear();});
 function setError(key:string,value:UIErrorCopy){setErrors(old=>({...old,[key]:value}));}
 async function run(key:string,task:()=>Promise<void>){if(pending(key))return;setBusy(old=>({...old,[key]:true}));setError(key,'');try{await task();}catch(e){if(!disposed)setError(key,authoredErrorCopy(e));}finally{if(!disposed)setBusy(old=>({...old,[key]:false}));}}
 async function load(){if(!client)return;const epoch=++loadEpoch,value=await client.get();if(!disposed&&epoch===loadEpoch)setState(value);}
 async function refreshSaved(){try{await load();}catch(e){if(!disposed)setError('directory',authoredErrorCopy(e));}}
 async function loadTools(){
  if(!toolsClient)return;if(toolsRunning){toolsAgain=true;return;}toolsRunning=true;toolsAgain=false;
  const epoch=++toolsEpoch,scope=scopeForTools(props.owner),key=scopeKey();
  await run('catalog',async()=>{const value=await toolsClient.get(scope);if(!disposed&&epoch===toolsEpoch&&key===scopeKey()){setCatalog(value);setCatalogScope(key);}});
  toolsRunning=false;if(toolsAgain&&!disposed&&props.open&&(tab()==='tools'||!!props.owner)){toolsAgain=false;void loadTools();}
 }
 createEffect(()=>{const open=props.open,initial=props.initialTab;if(open&&!wasOpen)untrack(()=>{opener=typeof document!=='undefined'&&document.activeElement instanceof HTMLElement?document.activeElement:undefined;skipReturnFocus=false;if(initial&&initial!==tab()){queries[tab()]=query();setTab(initial);setQuery(queries[initial]);}setDetail(undefined);if(!props.review)void run('directory',load);});if(!open&&wasOpen)untrack(()=>{retainDraft();editEpoch++;});wasOpen=open;});
 createEffect(()=>{if(!props.open||props.review||tab()!=='tools'&&!props.owner)return;scopeKey();toolsInvalidation();untrack(()=>void loadTools());});
 createEffect(()=>{if(!props.open||detail())return;const value=tab();queueMicrotask(()=>{if(!disposed&&props.open&&!detail()&&tab()===value&&listElement)listElement.scrollTop=scrolls[value]??0;});});
 function retainDraft(){const d=detail();if(!d||!loadedDrafts.has(detailKey()))return;if(d.kind==='skill'||d.kind==='new-skill')skillDrafts.set(detailKey(),{...skill()});if(d.kind==='mcp'||d.kind==='new-mcp')mcpDrafts.set(detailKey(),{...mcp()});}
 function rememberScroll(){if(listElement&&!detail())scrolls[tab()]=listElement.scrollTop;retainDraft();}
 function openDetail(value:Detail){rememberScroll();editEpoch++;setDetail(value);setNotice('');}
 async function back(){try{await flushExperienceDrafts();await flushResourceAllowanceDrafts();retainDraft();editEpoch++;setExperienceSource(undefined);setDetail(undefined);setNotice('');queueMicrotask(()=>{if(sourceReturn&&listElement)[...listElement.querySelectorAll<HTMLButtonElement>('button[data-experience-origin]')].find(el=>el.dataset.experienceOrigin===JSON.stringify(sourceReturn))?.focus({preventScroll:true});});}catch(e){setError('directory',authoredErrorCopy(e));}}
 async function changeOpen(open:boolean){if(open){props.onOpenChange(true);return;}if(closePending)return;closePending=true;try{await flushExperienceDrafts();await flushResourceAllowanceDrafts();props.onOpenChange(false);}catch(e){setError('directory',authoredErrorCopy(e));}finally{closePending=false;}}
 async function openExperience(origin:ExperienceOrigin){sourceReturn={...origin,candidate_ref:{...origin.candidate_ref}};retainDraft();openDetail({kind:'experience-source',id:origin.candidate_ref.id});setExperienceSource(undefined);const epoch=editEpoch;await run('experience-source',async()=>{const value=await new TaskExperienceApi().sourceWork(origin);if(!disposed&&props.open&&editEpoch===epoch)setExperienceSource(value);});}
 const sourceButton=(item:AgentSkill|AgentMCP)=><Show when={capabilityExperienceOrigin(item)}>{origin=><button type="button" class="as-nc-link" data-experience-origin={JSON.stringify(origin())} onClick={()=>void openExperience(origin())}>{uiText('查看来源工作与经验')}</button>}</Show>;
 function changeTab(value:CapabilityTab){rememberScroll();queries[tab()]=query();editEpoch++;setDetail(undefined);setTab(value);setQuery(queries[value]);setNotice('');}
 function adopt(receipt:CapabilityReceipt,kind:'skills'|'mcp',id:string,enabled:boolean,key:string){
  setConflicts(old=>({...old,[key]:false}));setReceipts(old=>({...old,[key]:receipt}));setUnknown(old=>{const next={...old};delete next[key];return next;});
  setState(old=>old?{...old,[kind]:old[kind].map(item=>item.id===id?{...item,enabled,config_ref:receipt.config_ref,loaded_in_original_session:false,availability_observation_current:false}:item)}:old);
  setToolsInvalidation(value=>value+1);
 }
 async function write(key:string,kind:'skills'|'mcp',id:string,enabled:boolean,ref:NativeRef|undefined,send:(operation:ReturnType<typeof crypto.randomUUID>)=>Promise<CapabilityReceipt>,after?:(receipt:CapabilityReceipt)=>void){
  if(!client||blocked(key))return;const operation=crypto.randomUUID(),epoch=editEpoch,origin=detailKey();if(after)completions.set(operation,after);
  await run(key,async()=>{let receipt:CapabilityReceipt;try{receipt=await send(operation);}catch(e){if(isCapabilityRejected(e)){completions.delete(operation);if(e.status===409&&!disposed)setConflicts(old=>({...old,[key]:true}));}else if(!disposed)setUnknown(old=>({...old,[key]:{operation,id,expected:ref?.revision??0,kind}}));throw e;}if(!disposed){adopt(receipt,kind,id,enabled,key);completions.get(operation)?.(receipt);completions.delete(operation);if(props.open&&epoch===editEpoch&&origin===detailKey())setNotice('配置已保存');await refreshSaved();}});
 }
 async function toggle(kind:'skills'|'mcp',item:AgentSkill|AgentMCP,enabled:boolean){if(enabled&&capabilityBlockReason(item)){setError(keyFor(kind,item.id),'来源或暂停状态阻止启用，请先核对原操作');return;}await write(keyFor(kind,item.id),kind,item.id,enabled,item.config_ref,operation=>client!.enabled(kind,item.config_ref,enabled,operation));}
 async function recover(key:string){
  const saved=unknown()[key];if(!client||!saved)return;const epoch=editEpoch,origin=detailKey();
  await run(key,async()=>{const result=await client.operation(saved.operation);if(result.operation_id!==saved.operation||result.receipt.config_ref.id!==saved.id||result.receipt.config_ref.revision!==saved.expected+1)throw new Error('原操作尚未确认，请勿重复提交');if(!disposed){adopt(result.receipt,saved.kind,saved.id,result.configuration.enabled===true,key);completions.get(saved.operation)?.(result.receipt);completions.delete(saved.operation);setError(key,'');if(props.open&&epoch===editEpoch&&origin===detailKey())setNotice('已核对原操作');await refreshSaved();}});
 }
 async function editSkill(item:AgentSkill){
  openDetail({kind:'skill',id:item.id});const epoch=editEpoch,key=detailKey(),draft=skillDrafts.get(key);
  if(draft){setSkill({...draft});return;}setSkill({...emptySkill(),id:item.id,basis:item.config_ref});
  await run(key,async()=>{const source=await client!.skill(item.config_ref);if(!disposed&&epoch===editEpoch){loadedDrafts.add(key);setSkill({id:item.id,content:source.content,enabled:source.enabled,basis:source.config_ref});}});
 }
 async function editMCP(item:AgentMCP){
  openDetail({kind:'mcp',id:item.id});const epoch=editEpoch,key=detailKey(),draft=mcpDrafts.get(key);
  if(draft){setMCP({...draft});return;}setMCP({...emptyMCP(),id:item.id,basis:item.config_ref});
  await run(key,async()=>{const source=await client!.mcp(item.config_ref);if(!disposed&&epoch===editEpoch){loadedDrafts.add(key);const c=source.configuration;setMCP({id:item.id,basis:source.config_ref,type:c.type,enabled:c.enabled,timeout:c.timeout,command:c.type==='local'?JSON.stringify(c.command,null,2):'',files:c.type==='local'?JSON.stringify(c.files_sha256,null,2):'',url:c.type==='remote'?c.url:''});}});
 }
 function add(kind:'skills'|'mcp'){openDetail({kind:kind==='skills'?'new-skill':'new-mcp'});loadedDrafts.add(detailKey());if(kind==='skills'){const draft=skillDrafts.get('skills:new');setSkill({...draft&&!draft.basis?draft:emptySkill()});}else{const draft=mcpDrafts.get('mcp:new');setMCP({...draft&&!draft.basis?draft:emptyMCP()});}}
 async function readLatest(){
  const d=detail(),key=detailKey(),epoch=editEpoch;if(!client||blocked(key)||!d||!['skill','mcp','new-skill','new-mcp'].includes(d.kind))return;
  const isSkill=d.kind==='skill'||d.kind==='new-skill',draft=isSkill?{...skill()}:{...mcp()},id=draft.id;
  conflictDrafts.set(key,draft);setRecoverable(old=>({...old,[key]:true}));
  await run(key,async()=>{const current=await client.get(),item=(isSkill?current.skills:current.mcp).find(item=>item.id===id);if(!item)throw new Error('配置缺少确切版本，请刷新');
   const source=isSkill?await client.skill(item.config_ref):await client.mcp(item.config_ref);
   if(disposed||epoch!==editEpoch||key!==detailKey())return;setState(current);setConflicts(old=>({...old,[key]:false}));
   if('content'in source){const value={id,content:source.content,enabled:source.enabled,basis:source.config_ref};skillDrafts.set(key,value);setSkill(value);}
   else{const c=source.configuration,value:MCPDraft={id,basis:source.config_ref,type:c.type,enabled:c.enabled,timeout:c.timeout,command:c.type==='local'?JSON.stringify(c.command,null,2):'',files:c.type==='local'?JSON.stringify(c.files_sha256,null,2):'',url:c.type==='remote'?c.url:''};mcpDrafts.set(key,value);setMCP(value);}
  });
 }
 function restoreConflictDraft(){const key=detailKey(),saved=conflictDrafts.get(key);if(!saved||blocked(key))return;if('content'in saved){setSkill({...saved});skillDrafts.set(key,{...saved});}else{setMCP({...saved});mcpDrafts.set(key,{...saved});}conflictDrafts.delete(key);setRecoverable(old=>({...old,[key]:false}));setConflicts(old=>({...old,[key]:true}));setError(key,'');}
 async function selectSkill(file?:File){if(!file)return;const epoch=editEpoch;await run(detailKey(),async()=>{if(file.size<1||file.size>65536)throw new Error('SKILL.md 应为 1–65536 字节的 UTF-8 文本');const text=skillText(new Uint8Array(await file.arrayBuffer()),file.name);if(!disposed&&epoch===editEpoch)setSkill(old=>({...old,content:text,id:detail()?.kind==='new-skill'&&!old.id.trim()?importedSkillName(text)??old.id:old.id}));});}
 async function saveSkill(){
  const d={...skill()},key=detailKey(),epoch=editEpoch;if(!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(d.id)||d.id.length>64||!d.content||new TextEncoder().encode(d.content).byteLength>65536){setError(key,'Skill 标识或原文无效，编辑内容已保留');return;}
  await write(key,'skills',d.id.trim(),d.enabled,d.basis,op=>client!.importSkill(d.id.trim(),d.content,d.enabled,d.basis,op),receipt=>{const saved={...d,basis:receipt.config_ref};skillDrafts.set(key,saved);if(props.open&&epoch===editEpoch&&key===detailKey())setSkill(saved);});
 }
 async function saveMCP(){
  const d={...mcp()},key=detailKey(),epoch=editEpoch;let configuration:ReturnType<typeof mcpConfiguration>;try{configuration=mcpConfiguration(d);if(!/^[A-Za-z0-9_-]{1,100}$/.test(d.id))throw new Error('MCP 标识使用字母、数字、下划线或连字符');}catch(e){setError(key,authoredErrorCopy(e));return;}
  await write(key,'mcp',d.id.trim(),d.enabled,d.basis,op=>client!.configure(d.id.trim(),configuration,d.basis,op),receipt=>{const saved={...d,basis:receipt.config_ref};mcpDrafts.set(key,saved);if(props.open&&epoch===editEpoch&&key===detailKey())setMCP(saved);});
 }
 async function probe(item:AgentMCP){const key=detailKey();await run(key,async()=>{const value=await client!.probe(item);if(!disposed){setReceipts(old=>({...old,[key]:value}));await refreshSaved();setToolsInvalidation(value=>value+1);}});}
 function selectTool(id:string,checked:boolean){if(!catalog()||!toolsCurrent()||selectedStale())return;if(checked&&selected().length>=64){setError('tools','最多选择 64 个工具');return;}setSelectionBasis(catalog()!.capability_binding_sha256);setSelected(old=>checked?[...old.filter(value=>value!==id),id]:old.filter(value=>value!==id));}
 function choose(){try{if(!catalog()||!toolsCurrent()||selectedStale())throw new Error('所选工具已变化，请刷新后重新选择');const selection=withMCPConnectionHints(chooseMCPTools(catalog()!,selected()),state()?.mcp??[]);skipReturnFocus=true;props.onChooseTools?.(selection);setNotice('已交给新会话');}catch(e){setError('tools',authoredErrorCopy(e));}}
 const glyph=(kind:string)=><span class="as-nc-icon" aria-hidden="true">{kind==='mcp'?<svg viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="7" rx="2"/><rect x="4" y="14" width="16" height="7" rx="2"/><path d="M8 6.5h.01M8 17.5h.01M12 6.5h5M12 17.5h5"/></svg>:kind==='skills'?<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h5"/></svg>:<svg viewBox="0 0 24 24"><path d="m4 5 5 5-5 5M12 18h8"/></svg>}</span>;
 const configurationSwitch=(kind:'skills'|'mcp',getItem:()=>AgentSkill|AgentMCP)=><Switch class="as-nc-switch" checked={getItem().enabled} disabled={!client||(!getItem().enabled&&(getItem().integrity!=='CURRENT'||!!capabilityBlockReason(getItem())))} readOnly={blocked(keyFor(kind,getItem().id))} onChange={value=>void toggle(kind,getItem(),value)}><Switch.Input aria-disabled={blocked(keyFor(kind,getItem().id))} aria-label={uiText('启用 {name}',{name:'title'in getItem()?(getItem() as AgentSkill).title:getItem().id})}/><Switch.Control><Switch.Thumb/></Switch.Control></Switch>;
 const status=(item:AgentSkill|AgentMCP)=>capabilityBlockReason(item)||item.enabled&&item.effective_enabled===false?uiText(capabilityAvailability(item)):item.integrity!=='CURRENT'?uiText('来源已变化'):'last_probe'in item&&sameNativeReference(item.last_probe?.config_ref,item.config_ref)&&item.last_probe&&!item.last_probe.list_tools_succeeded?uiText('连接失败'):uiText(capabilityAvailability(item));
 const feedback=(key:string)=><><Show when={pending(key)}><span role="status">{uiText('正在保存…')}</span></Show><Show when={errors()[key]}><p class="as-nc-error" role="alert">{authoredText(errors()[key],uiText)}</p></Show><Show when={unknown()[key]}><button type="button" class="as-nc-link" disabled={pending(key)} onClick={()=>void recover(key)}>{uiText('核对原操作')}</button></Show></>;
 const detailTitle=()=>{const d=detail();return d?.kind==='experience-source'?uiText('来源工作中的经验'):d?.kind==='new-skill'?uiText('导入 Skill'):d?.kind==='new-mcp'?uiText('新增 MCP'):d?.kind==='tool-group'&&d.id==='builtin'?uiText('内置工具'):detailID();};
 return <Dialog open={props.open} onOpenChange={open=>void changeOpen(open)} modal><Dialog.Portal><Dialog.Overlay class="as-nc-overlay"/><div class="as-nc-position"><Dialog.Content class="as-nc-dialog" aria-describedby={undefined} onCloseAutoFocus={event=>{event.preventDefault();if(!skipReturnFocus&&opener?.isConnected)opener.focus({preventScroll:true});}}>
  <header class="as-nc-header"><div><Dialog.Title>{uiText(props.review?props.review.catalog.schema==='eeas.native-capability-task-capabilities.v2'?'技能与工具':'当前页检查':'能力管理')}</Dialog.Title><span class="as-nc-scope">{props.review?'':uiText(tab()==='tools'?'新会话':'本机配置')}</span></div><Dialog.CloseButton class="as-nc-close" aria-label={uiText('关闭')}>×</Dialog.CloseButton></header>
  <Show when={!props.review}><Show when={!detail()} fallback={<div class="as-nc-detail-heading"><button type="button" class="as-nc-link" onClick={back}>← {uiText('返回目录')}</button><strong>{detailTitle()}</strong></div>}>
   <div class="as-nc-toolbar"><div role="tablist" aria-label={uiText('能力分类')}><For each={['tools','mcp','skills'] as CapabilityTab[]}>{value=><button type="button" role="tab" aria-selected={tab()===value} tabIndex={tab()===value?0:-1} onKeyDown={event=>{const values:CapabilityTab[]=['tools','mcp','skills'];const index=values.indexOf(value),next=event.key==='ArrowRight'?values[(index+1)%3]:event.key==='ArrowLeft'?values[(index+2)%3]:event.key==='Home'?values[0]:event.key==='End'?values[2]:undefined;if(next){event.preventDefault();changeTab(next);const buttons=event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role=tab]');buttons?.[values.indexOf(next)]?.focus();}}} onClick={()=>changeTab(value)}>{value==='tools'?uiText('工具'):value==='mcp'?'MCP':uiText('技能')}</button>}</For></div><input type="search" aria-label={uiText('搜索能力')} placeholder={uiText('搜索能力')} value={query()} onInput={event=>setQuery(event.currentTarget.value)}/><Show when={tab()!=='tools'}><button type="button" class="as-nc-add" onClick={()=>add(tab()==='skills'?'skills':'mcp')}>{uiText(tab()==='skills'?'导入 Skill':'新增 MCP')}</button></Show><button type="button" class="as-nc-refresh" disabled={pending('directory')||pending('catalog')} aria-label={uiText('刷新目录')} onClick={()=>void run('directory',async()=>{await load();if(tab()==='tools'||props.owner)await loadTools();})}>↻</button></div>
  </Show>
  </Show><div class="as-nc-body" ref={element=>{listElement=element;}}>
   <Show when={props.review} fallback={<><Show when={!client}><p class="as-nc-error">{uiText('当前入口未提供原生管理接口。请在桌面 App 中管理 Skill 与 MCP。')}</p></Show>
   <Show when={errors().directory}><p class="as-nc-error" role="alert">{authoredText(errors().directory,uiText)}</p></Show><Show when={notice()}><p class="as-nc-notice" role="status">{uiText(notice())}</p></Show>
   <Show when={ownerChanged()||ownerUnavailable()||tab()==='tools'&&catalog()?.owner.status==='NOT_SELECTED'}><div class="as-nc-notice"><span>{uiText(ownerChanged()?'配置已更新，需创建新会话':catalog()?.owner.status==='NOT_SELECTED'?'选择会话':'当前会话状态暂不可读取')}</span><Show when={props.onOpenSessionSettings}><button type="button" class="as-nc-link" onClick={()=>void run('directory',async()=>{await flushExperienceDrafts();await flushResourceAllowanceDrafts();skipReturnFocus=true;props.onOpenSessionSettings?.();})}>{uiText('Agent 连接设置')}</button></Show></div></Show>
   <Show when={detail()?.kind==='experience-source'}><Show when={pending('experience-source')}><p role="status">{uiText('正在读取…')}</p></Show><Show when={errors()['experience-source']}><p role="alert">{authoredText(errors()['experience-source'],uiText)}</p></Show><Show when={experienceSource()}>{source=><OriginalWorkExperience context={source().context} root={source().root_operation_id} rootSha={source().root_sha256} initialCandidate={source().candidate_ref} locale={uiLocale()} onConfigurationChange={()=>void refreshSaved()}/>}</Show></Show>
   <Show when={!detail()}>
    <Show when={pending('directory')&&!state()}><p role="status">{uiText('正在读取…')}</p></Show>
    <Show when={tab()==='skills'}><div class="as-nc-grid"><For each={skills().map(item=>item.id)}>{id=>{const item=()=>state()!.skills.find(value=>value.id===id)!;return <article class="as-nc-card"><button class="as-nc-card-main" onClick={()=>void editSkill(item())}>{glyph('skills')}<span><strong>{item().experience_source&&item().experience_source!.availability!=='ACTIVE'?uiText('已停用的经验来源'):item().title}</strong><span class="as-nc-description">{item().description}</span></span></button>{configurationSwitch('skills',item)}<footer><span>{status(item())}</span><span>v{item().config_ref.revision}</span></footer>{sourceButton(item())}{feedback(keyFor('skills',id))}</article>;}}</For></div><Show when={state()&&!skills().length}><p class="as-nc-empty">{uiText(query()?'没有匹配项':'尚未导入 Skill')}</p></Show></Show>
    <Show when={tab()==='mcp'}><div class="as-nc-grid"><For each={mcps().map(item=>item.id)}>{id=>{const item=()=>state()!.mcp.find(value=>value.id===id)!;return <article class="as-nc-card"><button class="as-nc-card-main" onClick={()=>void editMCP(item())}>{glyph('mcp')}<span><strong>{item().id}</strong><span class="as-nc-description">{uiText(item().type==='local'?'本地进程':'远程端点')}</span></span></button>{configurationSwitch('mcp',item)}<footer><span>{status(item())}</span><span>{sameNativeReference(item().last_probe?.config_ref,item().config_ref)&&item().last_probe?.list_tools_succeeded?uiText('{count} 个工具',{count:item().last_probe?.tool_count??item().last_probe?.tools?.length??0}):''}</span></footer>{sourceButton(item())}{feedback(keyFor('mcp',id))}</article>;}}</For></div><Show when={state()&&!mcps().length}><p class="as-nc-empty">{uiText(query()?'没有匹配项':'尚未保存 MCP 配置')}</p></Show></Show>
    <Show when={tab()==='tools'}><Show when={pending('catalog')}><p role="status">{uiText('正在读取…')}</p></Show><Show when={errors().catalog}><p class="as-nc-error" role="alert">{authoredText(errors().catalog,uiText)}</p></Show><Show when={!toolsClient}><p class="as-nc-empty">{uiText('工具目录暂不可用')}</p></Show><div class="as-nc-grid"><Show when={toolsCurrent()&&catalog()?.builtin_tools.length&&matches('内置工具',...catalog()!.builtin_tools.map(tool=>tool.id))}><article class="as-nc-card"><button class="as-nc-card-main" onClick={()=>openDetail({kind:'tool-group',id:'builtin'})}>{glyph('tools')}<span><strong>{uiText('内置工具')}</strong><span class="as-nc-description">{uiText('当前会话权限')}</span></span></button><footer><span>{uiText('{count} 个工具',{count:catalog()!.builtin_tools.length})}</span></footer></article></Show><For each={toolServers()}>{server=><article class="as-nc-card"><button class="as-nc-card-main" onClick={()=>openDetail({kind:'tool-group',id:server.id})}>{glyph('mcp')}<span><strong>{server.id}</strong><span class="as-nc-description">MCP</span></span></button><footer><span>{uiText(availabilityCopy[server.availability])}</span><span>{uiText('{count} 个工具',{count:server.tools.length})}</span></footer></article>}</For></div><Show when={catalog()&&!catalog()!.builtin_tools.length&&!toolServers().length}><p class="as-nc-empty">{uiText(query()?'没有匹配项':catalog()!.owner.status==='NOT_SELECTED'?'选择会话后可查看内置工具':'暂无可用工具')}</p></Show></Show>
   </Show>
   <Show when={detail()?.kind==='tool-group'}><Show when={detailID()==='builtin'} fallback={<For each={toolsCurrent()?catalog()?.mcp_servers.filter(server=>server.id===detailID())??[]:[]}>{server=><><p class="as-nc-meta">{uiText(availabilityCopy[server.availability])}</p><For each={server.tools}>{tool=><label class="as-nc-tool-row"><input type="checkbox" checked={selected().includes(tool.id)} disabled={!toolsCurrent()||selectedStale()||!tool.selectable} onChange={event=>selectTool(tool.id,event.currentTarget.checked)}/><span>{tool.name}</span></label>}</For></>}</For>}><For each={toolsCurrent()?catalog()?.builtin_tools??[]:[]}>{tool=><article class="as-nc-tool-row"><span><strong>{tool.id}</strong><p>{tool.description}</p></span><small>{uiText(permissionCopy[tool.permission])}</small></article>}</For></Show></Show>
   <Show when={detail()?.kind==='skill'||detail()?.kind==='new-skill'}><form onSubmit={event=>{event.preventDefault();void saveSkill();}}><fieldset disabled={blocked(detailKey())}>
    <label>{uiText('Skill 标识')}<input aria-label={uiText('Skill 标识')} value={skill().id} disabled={!!skill().basis} onInput={event=>setSkill(old=>({...old,id:event.currentTarget.value}))}/></label>
    <label>{uiText('选择原始 SKILL.md')}<input type="file" aria-label={uiText('选择原始 SKILL.md')} accept=".md,text/markdown,text/plain" onChange={event=>{void selectSkill(event.currentTarget.files?.[0]);event.currentTarget.value='';}}/></label>
    <label>{uiText('SKILL.md 原文')}<textarea aria-label={uiText('SKILL.md 原文')} value={skill().content} onInput={event=>setSkill(old=>({...old,content:event.currentTarget.value}))} spellcheck={false}/></label>
    <label class="as-nc-check"><input type="checkbox" checked={skill().enabled} onChange={event=>setSkill(old=>({...old,enabled:event.currentTarget.checked}))}/>{uiText('启用')}</label>
    <button class="as-nc-primary" type="submit" disabled={!skill().id.trim()||!skill().content}>{uiText(skill().basis?'保存新版本':'导入原文')}</button>
   </fieldset></form></Show>
   <Show when={detail()?.kind==='mcp'||detail()?.kind==='new-mcp'}><form onSubmit={event=>{event.preventDefault();void saveMCP();}}><fieldset disabled={blocked(detailKey())}>
    <label>{uiText('MCP 标识')}<input aria-label={uiText('MCP 标识')} value={mcp().id} disabled={!!mcp().basis} onInput={event=>setMCP(old=>({...old,id:event.currentTarget.value}))}/></label>
    <label>{uiText('连接方式')}<select aria-label={uiText('MCP 连接方式')} value={mcp().type} onChange={event=>setMCP(old=>({...old,type:event.currentTarget.value as 'local'|'remote'}))}><option value="local">{uiText('本地 stdio 进程')}</option><option value="remote">{uiText('远程 HTTPS 端点（仅保存）')}</option></select></label>
    <Show when={mcp().type==='local'} fallback={<label>{uiText('HTTPS 端点')}<input aria-label={uiText('MCP HTTPS 端点')} value={mcp().url} onInput={event=>setMCP(old=>({...old,url:event.currentTarget.value}))}/></label>}><label>{uiText('命令参数数组（JSON）')}<textarea aria-label={uiText('MCP 命令参数数组')} value={mcp().command} onInput={event=>setMCP(old=>({...old,command:event.currentTarget.value}))} spellcheck={false}/></label><label>{uiText('文件路径与 SHA256（JSON）')}<textarea aria-label={uiText('MCP 文件 SHA256')} value={mcp().files} onInput={event=>setMCP(old=>({...old,files:event.currentTarget.value}))} spellcheck={false}/></label></Show>
    <label>{uiText('连接超时（毫秒）')}<input type="number" aria-label={uiText('MCP 连接超时')} min={MCP_TIMEOUT_MIN} max={MCP_TIMEOUT_MAX} step="100" value={mcp().timeout} onInput={event=>setMCP(old=>({...old,timeout:Number(event.currentTarget.value)}))}/></label>
    <label class="as-nc-check"><input type="checkbox" checked={mcp().enabled} onChange={event=>setMCP(old=>({...old,enabled:event.currentTarget.checked}))}/>{uiText('启用')}</label>
    <button class="as-nc-primary" type="submit" disabled={!mcp().id.trim()}>{uiText('保存配置')}</button>
   </fieldset></form><Show when={currentMCP()}>{item=><div class="as-nc-detail-actions"><p class="as-nc-meta">{probeStatus(item().last_probe,item().config_ref,uiText)}</p><button type="button" disabled={blocked(detailKey())||!item().probe_permitted} onClick={()=>void probe(item())}>{uiText('连接并读取工具')}</button><Show when={item().last_probe?.tools?.length}><details><summary>{uiText('工具列表')}</summary><For each={item().last_probe?.tools??[]}>{tool=><p>{tool.name}</p>}</For></details></Show></div>}</Show></Show>
   <Show when={detail()&&(conflicts()[detailKey()]||recoverable()[detailKey()])}><div class="as-nc-conflict"><Show when={conflicts()[detailKey()]}><button type="button" class="as-nc-link" disabled={blocked(detailKey())} onClick={()=>void readLatest()}>{uiText('读取最新版本')}</button></Show><Show when={recoverable()[detailKey()]}><button type="button" class="as-nc-link" disabled={blocked(detailKey())} onClick={restoreConflictDraft}>{uiText('恢复原草稿')}</button></Show></div></Show>
   <Show when={detail()&&errors()[detailKey()]}><p class="as-nc-error" role="alert">{authoredText(errors()[detailKey()],uiText)}</p></Show>
   <Show when={detail()&&unknown()[detailKey()]}><button type="button" class="as-nc-link" disabled={pending(detailKey())} onClick={()=>void recover(detailKey())}>{uiText('核对原操作')}</button></Show>
   <Show when={detail()&&receipts()[detailKey()]}><details class="as-nc-receipt"><summary>{uiText('此次实际回执')}</summary><Show when={receipts()[detailKey()]}>{value=><dl><dt>{uiText('保存版本')}</dt><dd>v{value().config_ref.revision}</dd><dt>{uiText('原会话加载')}</dt><dd>{uiText(value().loaded_in_original_session?'原会话已加载':'尚未加载原会话')}</dd><dt>{uiText('资格')}</dt><dd>{uiText(value().qualified?'资格已核实':'资格未核实')}</dd><Show when={'status' in value()}><dt>{uiText('连接探测')}</dt><dd>{probeStatus(value() as MCPProbe,value().config_ref,uiText)}</dd></Show></dl>}</Show></details></Show>
   </>}><NativeReviewPicker catalog={props.review!.catalog} selection={props.review!.selection} onChange={value=>props.review!.onChange(value)}/></Show>
  </div>
  <footer class="as-nc-footer"><Show when={props.review} fallback={<><Show when={tab()==='tools'}><div><Show when={selectedStale()}><span class="as-nc-error">{uiText('所选工具已变化，请刷新后重新选择')}</span><button type="button" class="as-nc-link" onClick={()=>{setSelected([]);setSelectionBasis('');}}>{uiText('重新选择')}</button></Show><Show when={errors().tools}><span class="as-nc-error" role="alert">{authoredText(errors().tools,uiText)}</span></Show><Show when={selected().length}><span>{uiText('已选择 {count} 个工具',{count:selected().length})}</span></Show></div><button type="button" class="as-nc-primary" disabled={!props.onChooseTools||!selected().length||selectedStale()||!toolsCurrent()||pending('catalog')} onClick={choose}>{uiText('交给新会话')}</button></Show><Show when={tab()!=='tools'}><span/><Dialog.CloseButton class="as-nc-primary">{uiText('完成')}</Dialog.CloseButton></Show></>}><span/><button type="button" class="as-nc-primary" onClick={()=>props.review?.onChoose()}>{uiText('用于当前页')}</button></Show></footer>
 </Dialog.Content></div></Dialog.Portal></Dialog>;
}
