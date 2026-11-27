import {createSignal} from 'solid-js';
import rendererCatalog from '../../../packages/i18n/renderer.json' with {type:'json'};
import agentCatalog from '../../../packages/i18n/renderer-agent.json' with {type:'json'};

export type UILanguagePreference='system'|'zh-CN'|'en';
export type UILocale='zh-CN'|'en';
export type UILanguageInfo={preference:UILanguagePreference;resolved_locale:UILocale};
export type UILanguageBridge={
  getUILanguage:()=>Promise<unknown>;
  setUILanguage:(preference:UILanguagePreference)=>Promise<unknown>;
  onUILanguageChanged:(listener:(value:unknown)=>void)=>(()=>void);
};
type StoragePort={getItem:(key:string)=>string|null;setItem:(key:string,value:string)=>void};
type ControllerOptions={storage?:StoragePort;bridge?:UILanguageBridge;native?:boolean;systemLocale?:()=>string;onChange?:(value:UILanguageInfo)=>void};
type MessageMap=Record<string,{'zh-CN':string;en:string}>;
const messages:MessageMap={...rendererCatalog.messages,...agentCatalog.messages};
export const UI_LANGUAGE_STORAGE_KEY='eeas.ui-language.v1';
export function isUILanguagePreference(value:unknown):value is UILanguagePreference{return value==='system'||value==='zh-CN'||value==='en';}
export function resolveUILocale(preference:UILanguagePreference='system',systemLocale='en'):UILocale{return preference==='zh-CN'||preference==='system'&&/^zh(?:[-_]|$)/i.test(systemLocale)?'zh-CN':'en';}
export function assertUILanguageInfo(value:unknown):UILanguageInfo{
  const info=value as UILanguageInfo|undefined;
  if(!info||!isUILanguagePreference(info.preference)||!['zh-CN','en'].includes(info.resolved_locale))throw new Error('UI_LANGUAGE_RECEIPT_INVALID');
  if(info.preference!=='system'&&info.preference!==info.resolved_locale)throw new Error('UI_LANGUAGE_RECEIPT_INVALID');
  return {preference:info.preference,resolved_locale:info.resolved_locale};
}
/** Preferences affect presentation only; this controller never creates a session or reloads it. */
export class UILanguageController{
  info:UILanguageInfo;
  private readonly options:ControllerOptions;
  private generation=0;private change=0;private eventVersion=0;private remove?:()=>void;
  constructor(options:ControllerOptions={}){this.options=options;this.info={preference:'system',resolved_locale:resolveUILocale('system',this.systemLocale())};}
  private systemLocale(){return this.options.systemLocale?.()??'en';}
  private apply(value:UILanguageInfo){this.info={...value};this.options.onChange?.({...value});}
  async start(){
    this.stop();const generation=this.generation,change=this.change;
    if(this.options.native){
      const bridge=this.options.bridge;if(!bridge)throw new Error('UI_LANGUAGE_NOT_SUPPORTED');
      this.remove=bridge.onUILanguageChanged(value=>{if(generation!==this.generation)return;try{const info=assertUILanguageInfo(value);this.eventVersion++;this.apply(info);}catch{/* Keep the last verified preference. */}});
      const eventVersion=this.eventVersion,value=assertUILanguageInfo(await bridge.getUILanguage());
      if(generation===this.generation&&change===this.change&&eventVersion===this.eventVersion)this.apply(value);
    }else{
      let preference:UILanguagePreference='system';try{const stored=this.options.storage?.getItem(UI_LANGUAGE_STORAGE_KEY);if(isUILanguagePreference(stored))preference=stored;}catch{/* An unreadable preference uses the OS locale, without overwriting storage. */}
      this.apply({preference,resolved_locale:resolveUILocale(preference,this.systemLocale())});
    }
    return {...this.info};
  }
  async set(preference:UILanguagePreference){
    if(!isUILanguagePreference(preference))throw new Error('UI_LANGUAGE_PREFERENCE_INVALID');
    const generation=this.generation,change=++this.change,eventVersion=this.eventVersion;
    if(this.options.native){
      if(!this.options.bridge)throw new Error('UI_LANGUAGE_NOT_SUPPORTED');
      const value=assertUILanguageInfo(await this.options.bridge.setUILanguage(preference));
      if(generation===this.generation&&change===this.change&&eventVersion===this.eventVersion)this.apply(value);
    }else{
      if(!this.options.storage)throw new Error('UI_LANGUAGE_STORAGE_UNAVAILABLE');
      this.options.storage.setItem(UI_LANGUAGE_STORAGE_KEY,preference);
      if(generation===this.generation&&change===this.change)this.apply({preference,resolved_locale:resolveUILocale(preference,this.systemLocale())});
    }
    return {...this.info};
  }
  acceptStoredPreference(value:unknown){if(this.options.native||!isUILanguagePreference(value))return;this.change++;this.apply({preference:value,resolved_locale:resolveUILocale(value,this.systemLocale())});}
  stop(){this.generation++;this.remove?.();this.remove=undefined;}
}

const systemLocale=()=>typeof navigator!=='undefined'?navigator.language:'en';
const [language,setLanguage]=createSignal<UILanguageInfo>({preference:'system',resolved_locale:resolveUILocale('system',systemLocale())});
const [languageError,setLanguageError]=createSignal('');
let controller:UILanguageController|undefined,users=0,removeStorage:(()=>void)|undefined;
function environmentController(){
  const host=(globalThis as {eeasDesktop?:Partial<UILanguageBridge>&{version?:number}}).eeasDesktop;
  let storage:StoragePort|undefined;try{if(typeof localStorage!=='undefined')storage=localStorage;}catch{/* Storage access can be denied. */}
  const bridge=host&&typeof host.getUILanguage==='function'&&typeof host.setUILanguage==='function'&&typeof host.onUILanguageChanged==='function'?host as UILanguageBridge:undefined;
  return new UILanguageController({storage,bridge,native:!!host,systemLocale,onChange:value=>{setLanguage(value);if(typeof document!=='undefined')document.documentElement.lang=value.resolved_locale;}});
}
export const uiLanguage=language;
export const uiLanguageError=languageError;
export const uiLocale=()=>language().resolved_locale;
export function startUILanguage(){
  users++;if(!controller){controller=environmentController();const current=controller;setLanguageError('');void current.start().catch(()=>{if(controller===current)setLanguageError('无法读取界面语言，保留当前显示。');});if(typeof window!=='undefined'){const listener=(event:StorageEvent)=>{if(event.key===UI_LANGUAGE_STORAGE_KEY)controller?.acceptStoredPreference(event.newValue);};window.addEventListener('storage',listener);removeStorage=()=>window.removeEventListener('storage',listener);}}
  let stopped=false;return()=>{if(stopped)return;stopped=true;if(--users===0){controller?.stop();controller=undefined;removeStorage?.();removeStorage=undefined;}};
}
export async function setUILanguagePreference(preference:UILanguagePreference){const current=controller;if(!current)throw new Error('UI_LANGUAGE_NOT_STARTED');const result=await current.set(preference);if(controller===current)setLanguageError('');return result;}
export function translateUI(message:string,locale:UILocale,params:Record<string,string|number>={}):string{
  const row=messages[message];const value=row?.[locale]??message;
  return value.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g,(marker,key)=>Object.hasOwn(params,key)?String(params[key]):marker);
}
/** Only explicitly marked product copy calls t; user documents and replies never pass here. */
export function t(message:string,params:Record<string,string|number>={}):string{return translateUI(message,uiLocale(),params);}
