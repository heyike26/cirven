import {createSignal,onCleanup,onMount} from 'solid-js';
import {isUILanguagePreference,setUILanguagePreference,startUILanguage,t,uiLanguage,uiLanguageError} from './ui-language';

export default function UILanguageControl(){
  const [busy,setBusy]=createSignal(false),[error,setError]=createSignal('');
  let select:HTMLSelectElement|undefined;
  onMount(()=>onCleanup(startUILanguage()));
  async function change(value:string){if(!isUILanguagePreference(value)||busy())return;setBusy(true);setError('');try{await setUILanguagePreference(value);}catch{setError('语言设置未保存，请重试。');}finally{if(select)select.value=uiLanguage().preference;setBusy(false);}}
  return <div class="as-ui-language"><label><span>{t('界面语言')}</span><select ref={select} aria-label={t('界面语言')} value={uiLanguage().preference} disabled={busy()} onChange={event=>void change(event.currentTarget.value)}><option value="system">{t('跟随系统')}</option><option value="zh-CN">简体中文</option><option value="en">English</option></select></label>{(error()||uiLanguageError())&&<small role="alert">{t(error()||uiLanguageError())}</small>}</div>;
}
