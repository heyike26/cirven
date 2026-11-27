import { AppBaseProviders, AppInterface, PlatformProvider, ServerConnection } from '@opencode-ai/app';
import { MemoryRouter } from '@solidjs/router';
import '@opencode-ai/app/index.css';
import {createEffect} from 'solid-js';
import {useLanguage} from '@/context/language';
import {uiLocale} from './ui-language';

function NativeUILanguageBridge(){
  const language=useLanguage();
  createEffect(()=>{const next=uiLocale()==='zh-CN'?'zh':'en';if(language.locale()!==next)language.setLocale(next);});
  return null;
}

/** Full pinned original application, not a mocked conversation or an iframe. */
export default function NativeApp(props: { serverUrl: string }) {
  const server: ServerConnection.Http = { type: 'http', http: { url: props.serverUrl } };
  const connection = ServerConnection.key(server);
  return <PlatformProvider value={{ platform: 'web', openExternal: url => window.open(url, '_blank', 'noopener,noreferrer'), restart: async () => window.location.reload(), notify: async (title,description,onClick) => { window.dispatchEvent(new CustomEvent('eeas-native-notification',{detail:{title,description}}));if('Notification' in window&&Notification.permission==='granted'){const notification=new Notification(title,{body:description});notification.onclick=()=>{window.focus();onClick?.();notification.close();};} } }}>
    <AppBaseProviders locale={uiLocale()==='zh-CN'?'zh':'en'}><NativeUILanguageBridge/><AppInterface defaultServer={connection} servers={[server]} router={MemoryRouter} /></AppBaseProviders>
  </PlatformProvider>;
}
