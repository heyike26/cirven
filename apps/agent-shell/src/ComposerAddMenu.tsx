import {createSignal,Show} from 'solid-js';
import StudioPopover from './StudioPopover';
import './native-input-attachments.css';

export default function ComposerAddMenu(props:{locale:string;busy:boolean;captureAvailable:boolean;onFile:()=>unknown;onPage:()=>unknown;onScreenshot:()=>unknown;onTools:()=>unknown;toolsAvailable:boolean;draftCopies?:number;onDraftCopies?:()=>unknown;draftRecoveryOpen?:boolean;previousFilename?:string;onPrevious?:()=>unknown}){
 const [open,setOpen]=createSignal(false),en=()=>props.locale.startsWith('en');
 const run=(callback:()=>unknown)=>{setOpen(false);callback();};
 return <StudioPopover onCloseAutoFocus={event=>{if(props.draftRecoveryOpen)event.preventDefault();}} open={open()} onOpenChange={setOpen} label={en()?'Add to this message':'添加到本条消息'} placement="top-start" class="as-composer-add-menu" trigger={<button type="button" class="as-composer-add-trigger" data-composer-add aria-label={en()?'Add to message':'添加输入'} aria-expanded={open()} aria-haspopup="dialog" disabled={props.busy} onClick={()=>setOpen(value=>!value)}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3v14M3 10h14"/></svg></button>}>
  <header>{en()?'Add':'添加'}</header><button type="button" onClick={()=>run(props.onFile)} disabled={props.busy}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m7 12 6-6a2 2 0 0 1 3 3l-7 7a3 3 0 0 1-4-4l8-8"/></svg><span>{en()?'Files':'选择文件'}</span></button>
  <button type="button" onClick={()=>run(props.onPage)} disabled={props.busy||!props.captureAvailable}><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="3" width="16" height="14" rx="2"/><path d="M2 7h16m-12 4h7m-7 3h4"/></svg><span>{en()?'Current page':'引用当前页面'}</span></button>
  <button type="button" onClick={()=>run(props.onScreenshot)} disabled={props.busy||!props.captureAvailable}><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="3" width="16" height="14" rx="2"/><circle cx="7" cy="7" r="1.5"/><path d="m2 14 5-5 4 4 3-3 4 4"/></svg><span>{en()?'Screenshot':'截图'}</span></button>
  <Show when={props.previousFilename&&props.onPrevious}><button type="button" class="as-composer-add-previous" disabled={props.busy} onClick={()=>run(()=>props.onPrevious?.())}><span>{en()?'Previous file':'上次选择'}</span><small title={props.previousFilename}>{props.previousFilename}</small></button></Show>
  <Show when={props.draftCopies&&props.onDraftCopies}><div class="as-composer-add-divider"/><button type="button" disabled={props.busy} onClick={()=>run(()=>props.onDraftCopies?.())}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5h11v12H5ZM3 2h10M2 3v11"/></svg><span>{en()?'Draft copies':'草稿副本'}</span><small>{props.draftCopies}</small></button></Show>
  <Show when={props.toolsAvailable}><div class="as-composer-add-divider"/><button type="button" disabled={props.busy} onClick={()=>run(props.onTools)}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m10 2 2 6 6 2-6 2-2 6-2-6-6-2 6-2Z"/></svg><span>{en()?'Skills and tools':'技能与工具'}</span></button></Show>
  <Show when={!props.captureAvailable}><p>{en()?'Page capture is available in the connected desktop app.':'连接桌面观察服务后，可添加页面与截图。'}</p></Show>
 </StudioPopover>;
}
