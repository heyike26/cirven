import {Show} from 'solid-js';
import {Dialog} from '@kobalte/core/dialog';
import type {ArtifactEnvelope,RendererContext} from './contracts';
import ProviderRenderer from './ProviderRenderer';
import {t as uiText} from './ui-language';
import './provider-dialog.css';

/** The form belongs to this component, not to the portal's presence lifetime.
 * Closing detaches its DOM without disposing a pending save or unsent fields.
 * The modal itself unmounts normally, releasing focus and scroll guards. */
export default function ProviderDialog(props:{artifact?:ArtifactEnvelope;open:boolean;onOpenChange:(open:boolean)=>void;context:RendererContext;mount?:HTMLElement;returnTarget?:HTMLElement;fallbackFocus:()=>void}){
 return <Show when={props.artifact}>{_artifact=>{
  const form=<div class="as-provider-dialog-form"><ProviderRenderer artifact={props.artifact!} context={props.context} heading={false}/></div>;
  return <Dialog open={props.open} onOpenChange={props.onOpenChange} modal><Dialog.Portal mount={props.mount}>
   <Dialog.Overlay class="as-provider-dialog-overlay"/>
   <div class="as-provider-dialog-position"><Dialog.Content class="as-provider-dialog" aria-describedby={undefined} onCloseAutoFocus={event=>{
    event.preventDefault();const target=props.returnTarget;
    if(target?.isConnected&&target.getClientRects().length&&!target.closest('[hidden],[inert]'))target.focus({preventScroll:true});else props.fallbackFocus();
   }}>
    <header><Dialog.Title>{uiText('模型连接')}</Dialog.Title><Dialog.CloseButton aria-label={uiText('关闭')}><span aria-hidden="true">×</span></Dialog.CloseButton></header>
    <div class="as-provider-dialog-body">{form}</div>
   </Dialog.Content></div>
  </Dialog.Portal></Dialog>;
 }}</Show>;
}
