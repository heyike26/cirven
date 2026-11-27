import {createSignal,onCleanup,Show,type JSX} from 'solid-js';
import type {ArtifactEnvelope,RendererContext} from './contracts';
import {confirmedArtifactResult} from './interaction-state';
import {useDraftSession} from './ObjectDraft';
import {AuthoredUIError} from './authored-ui-copy';
import {t as uiText} from './ui-language';

/** Observe only the original action receipt. No new GET, write, replay or model
 * loop. A confirmation can advance the owned pane while its old draft is kept. */
export default function ArtifactActionContext(props:{context:RendererContext;owned:(target:ArtifactEnvelope)=>boolean;observe:(target:ArtifactEnvelope,result:unknown)=>void;children:(context:RendererContext)=>JSX.Element}){
 const owner=useDraftSession(),[next,setNext]=createSignal<ArtifactEnvelope>();let alive=true,origin:ArtifactEnvelope|undefined;onCleanup(()=>{alive=false;});
 const fields=()=>JSON.stringify(owner?.session?.fields??{});
 const context:RendererContext={open:target=>props.context.open(target),dispatch:async(id,target,input)=>{
  if(next())throw new AuthoredUIError('新版本已保存，当前草稿保留。');
  const before=fields(),result=await props.context.dispatch(id,target,input);if(!alive)return result;
  props.observe(target,result);
  const confirmed=id==='discovery.confirm'?confirmedArtifactResult(target,result):undefined;
  if(confirmed){origin=target;setNext(confirmed);if(props.owned(target)&&fields()===before){props.context.open(confirmed);}}
  return result;
 }};
 return <><Show when={next()}>{value=><div class="as-artifact-version-update" role="status"><strong>{uiText('要求与图已确认')}</strong><p>{uiText('新版本已保存，当前草稿保留。')}</p><button type="button" onClick={()=>{if(alive&&origin&&props.owned(origin))props.context.open(value());}}>{uiText('查看详情')}</button></div>}</Show><div class="as-artifact-action-content" inert={!!next()}>{props.children(context)}</div></>;
}
