import {createUniqueId, onCleanup, onMount, type ComponentProps} from 'solid-js';
import icon from '../../../packages/branding/icon.svg';
import productBrand from './brand';

export function Mark(props:{class?:string}){
  return <img data-component="logo-mark" class={props.class} src={icon} alt={productBrand.short_name} draggable={false}/>;
}
export function Splash(props:Pick<ComponentProps<'svg'>,'ref'|'class'>){
  return <svg ref={props.ref} class={props.class} data-component="logo-splash" viewBox="0 0 32 32" role="img" aria-label={productBrand.short_name}><image href={icon} width="32" height="32"/></svg>;
}
export function Logo(props:{class?:string}){
  return <span data-component="product-wordmark" class={props.class}><Mark/><span>{productBrand.short_name}</span></span>;
}
export function BrandLoader(props:{class?:string}){
  const id=`cirven-fill-${createUniqueId()}`;
  let element:SVGSVGElement|undefined;
  onMount(()=>{
    if(!element||typeof IntersectionObserver==='undefined')return;
    const observer=new IntersectionObserver(entries=>{for(const entry of entries)element?.setAttribute('data-visible',String(entry.isIntersecting));});
    observer.observe(element);onCleanup(()=>observer.disconnect());
  });
  return <svg ref={element} class={`as-brand-loader ${props.class??''}`} viewBox="0 0 32 32" aria-hidden="true" data-visible="true">
    <defs><clipPath id={id}><rect class="as-brand-loader-fill" width="32" height="32"/></clipPath></defs>
    <image href={icon} width="32" height="32" opacity=".16"/>
    <g clip-path={`url(#${id})`}><image href={icon} width="32" height="32"/></g>
  </svg>;
}
