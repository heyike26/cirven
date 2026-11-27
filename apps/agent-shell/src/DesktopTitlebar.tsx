import {t as uiText} from './ui-language';
import { createMemo, Show, type JSX } from 'solid-js';
import productBrand from './brand';
import brandIcon from '../../../packages/branding/icon.svg';

/** Electron owns the caption buttons; this band only supplies its drag surface. */
export default function DesktopTitlebar(props:{title?:string;mode:'companion'|'workbench';pinned?:boolean;pinBusy?:boolean;onPin?:()=>void;actions?:JSX.Element;center?:JSX.Element}){
  const actions=createMemo(()=>props.actions),center=createMemo(()=>props.center);
  return <header class="as-titleband" data-has-center={String(!!center())} aria-label={uiText("应用标题栏")}>
    <div class="as-titleband-leading">
    <span class="as-titleband-mark" aria-hidden="true"><img src={brandIcon} alt="" draggable={false}/></span>
    <strong>{productBrand.display_name}</strong>
    <Show when={actions()}><div class="as-titleband-actions">{actions()}</div></Show>
    <Show when={props.title}><span class="as-titleband-divider" aria-hidden="true"/><span class="as-titleband-project" title={props.title}>{props.title}</span></Show>
    </div>
    <Show when={center()}><div class="as-titleband-center">{center()}</div></Show>
    <div class="as-titleband-trailing">
    <Show when={props.mode==='companion'&&props.onPin}><button type="button" class="as-titleband-pin"
      aria-pressed={props.pinned===true} disabled={props.pinBusy}
      aria-label={uiText(props.pinned?'取消固定轻量窗口':'固定轻量协作窗口')}
      title={uiText(props.pinned?'取消固定轻量窗口':'固定轻量协作窗口')} onClick={()=>props.onPin?.()}>
      <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 3h6l-1 5 3 3v1H5v-1l3-3-1-5Z M10 12v5"/></svg>
    </button></Show>
    </div>
  </header>;
}
