// Existing Chinese-language interaction fixtures explicitly select zh-CN.
// Production translation and catalogs remain real; no user data or business state is replaced.
export * from '../src/ui-language.ts';
import {translateUI} from '../src/ui-language.ts';
export const t=(message,params)=>translateUI(message,'zh-CN',params);
export const uiLocale=()=> 'zh-CN';
