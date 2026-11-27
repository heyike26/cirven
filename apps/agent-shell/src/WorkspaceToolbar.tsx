import {createSignal, For, Show} from 'solid-js';
import {Icon} from '@opencode-ai/ui/icon';
import type {Workspace} from './contracts';
import StudioPopover from './StudioPopover';
import {railKey} from './activity-rail';
import {t as uiText} from './ui-language';
import './workspace-toolbar.css';

const folderName=(workspace:Workspace)=>workspace.root.split(/[\\/]/).filter(Boolean).at(-1)??workspace.root;

export default function WorkspaceToolbar(props:{workspace?:Workspace;workspaces:Workspace[];onOpen:()=>void;onSelect:(workspace:Workspace)=>void}){
  const [open,setOpen]=createSignal(false);
  return <nav class="as-directory-toolbar" aria-label={uiText('工作目录')}>
    <Show when={props.workspace} fallback={<button type="button" class="as-directory-empty" aria-label={uiText('打开工作目录')} onClick={props.onOpen}><Icon name="folder"/><span>{uiText('打开工作目录')}</span></button>}>{current=>
      <><StudioPopover open={open()} onOpenChange={setOpen} placement="bottom-start" label={uiText('工作目录')} class="as-directory-menu"
        trigger={<button type="button" class="as-directory-current" title={current().root} aria-label={`${uiText('工作目录')} · ${current().root}`} aria-expanded={open()} onClick={()=>setOpen(value=>!value)}>
          <Icon name="folder"/><span>{folderName(current())}</span><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4"/></svg>
        </button>}>
        <nav aria-label={uiText('工作目录')} onKeyDown={event=>railKey(event,event.currentTarget)}><For each={props.workspaces}>{item=><button type="button" aria-current={current().id===item.id?'true':undefined} title={item.root} onClick={()=>{setOpen(false);props.onSelect(item);}}>
          <Icon name="folder"/><span>{folderName(item)}</span><Show when={current().id===item.id}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3 8 3 3 7-7"/></svg></Show>
        </button>}</For><button type="button" class="as-directory-menu-open" onClick={()=>{setOpen(false);props.onOpen();}}><Icon name="plus"/><span>{uiText('打开工作目录')}</span></button></nav>
      </StudioPopover></>
    }</Show>
  </nav>;
}
