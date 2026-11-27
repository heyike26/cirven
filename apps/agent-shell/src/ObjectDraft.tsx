import {t as uiText} from './ui-language';
import {createContext,createSignal,onCleanup,Show,useContext,type Accessor,type JSX,type Setter} from 'solid-js';
import type {ArtifactEnvelope} from './contracts';
import {artifactStateKey,objectDraft,type ObjectDraftSession} from './workbench-state';
const DraftContext=createContext<{session?:ObjectDraftSession;version:Accessor<number>}>();
export function ObjectDraftBoundary(props:{artifact:ArtifactEnvelope;children:JSX.Element}){
 const key=artifactStateKey(props.artifact),session=key?objectDraft(key):undefined;const [version,setVersion]=createSignal(0);const release=session?.subscribe(()=>setVersion(value=>value+1));onCleanup(()=>{release?.();void session?.flush();});
 return <DraftContext.Provider value={{session,version}}>{props.children}</DraftContext.Provider>;
}
export function useDraftSession(){return useContext(DraftContext);}
export function draftSignal<T>(name:string,initial:T,channel:'fields'|'view'='fields'):[Accessor<T>,Setter<T>]{
 const owner=useContext(DraftContext);if(!owner?.session)return createSignal(initial);
 const session=owner.session;session.registerDefault(channel,name,initial);const read=()=>{owner.version();return session.value(channel,name,initial);};
 const set=((value:T|((previous:T)=>T))=>{const next=typeof value==='function'?(value as (previous:T)=>T)(read()):value;session.set(channel,name,next);return next;}) as Setter<T>;
 return [read,set];
}
export function DraftStatus(){const value=useContext(DraftContext);const session=()=>{value?.version();return value?.session;};return <Show when={session()}><div class="as-draft-status" role="status" data-state={session()!.status}><span>{session()!.status==='loading'?uiText("正在恢复本机草稿"):session()!.status==='saving'?uiText("正在保存本机草稿…"):session()!.status==='saved'?uiText("本机草稿已保存"):uiText(session()!.error)|| (session()!.dirty?uiText("编辑草稿 · 尚未提交新版本"):uiText("按当前版本审阅"))}</span><Show when={['error','unknown'].includes(session()!.status)}><button type="button" onClick={()=>void session()!.inspect()}>{uiText("核查草稿")}</button></Show></div></Show>;}
export function draftRecordSignal<T>(name:string,key:string,initial:T):[Accessor<T>,Setter<T>]{const [records,setRecords]=draftSignal<Record<string,T>>(name,{});useContext(DraftContext)?.session?.registerRecordDefault(name,key,initial);const read=()=>records()[key]??initial;const set=((value:T|((previous:T)=>T))=>{const next=typeof value==='function'?(value as (previous:T)=>T)(read()):value;setRecords(current=>({...current,[key]:next}));return next;}) as Setter<T>;return [read,set];}
