import {Popover} from '@kobalte/core/popover';
import {createEffect,createSignal,type JSX} from 'solid-js';
/** Anchored form surface. Primitive owns dismissal/focus; layout never uses the
 * whole window's right edge as a proxy for the actual clicked control. */
export default function StudioPopover(props:{open:boolean;onOpenChange:(open:boolean)=>void;trigger:JSX.Element;children:JSX.Element;label:string;placement?:'bottom-end'|'bottom-start'|'top-end'|'top-start';class?:string;id?:string;portalMount?:HTMLElement;initialFocus?:()=>HTMLElement|undefined;onCloseAutoFocus?:(event:Event)=>void}){
 let anchor:HTMLSpanElement|undefined,outside=false;
 const [keyboard,setKeyboard]=createSignal(false);
 // Controlled callers open via their own button, rather than Popover.Trigger.
 // Reset on the actual open edge, including reopening after an outside click.
 createEffect(()=>{if(props.open)outside=false;});
 return <Popover open={props.open} onOpenChange={value=>{if(value)outside=false;props.onOpenChange(value);}} modal={false} placement={props.placement??'bottom-end'} gutter={8} flip slide fitViewport overflowPadding={16}>
  <Popover.Anchor as="span" class="as-popover-anchor" ref={anchor} onPointerDown={()=>setKeyboard(false)} onKeyDown={()=>setKeyboard(true)}>{props.trigger}</Popover.Anchor>
  <Popover.Portal mount={props.portalMount}><Popover.Content id={props.id} class={'as-studio-popover '+(props.class??'')} aria-label={props.label} data-input-modality={keyboard()?'keyboard':'pointer'} data-edge={(props.placement??'bottom-end').startsWith('top')?'top':'bottom'} onOpenAutoFocus={event=>{const target=props.initialFocus?.();if(target){event.preventDefault();target.focus({preventScroll:true});}}} onPointerDownOutside={event=>{const target=event?.detail?.originalEvent?.target;if(target&&target instanceof Node&&anchor?.contains(target)){event.preventDefault();return;}outside=true;}} onFocusOutside={event=>{const target=event?.detail?.originalEvent?.target;if(target&&target instanceof Node&&anchor?.contains(target)){event.preventDefault();return;}outside=true;}} onEscapeKeyDown={event=>{event.preventDefault();event.stopPropagation();if(!event.isComposing)props.onOpenChange(false);}} onCloseAutoFocus={event=>{props.onCloseAutoFocus?.(event);if(event.defaultPrevented)return;event.preventDefault();if(!outside)anchor?.querySelector<HTMLElement>('button')?.focus({preventScroll:true});}}><Popover.Title class="as-visually-hidden">{props.label}</Popover.Title>{props.children}</Popover.Content></Popover.Portal>
 </Popover>;
}
