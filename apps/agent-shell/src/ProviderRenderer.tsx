import {t as uiText} from './ui-language';
import { createEffect, createSignal, createUniqueId, For, Show } from 'solid-js';
import type { ArtifactEnvelope, RendererContext } from './contracts';
import ProviderConnectionForm from './ProviderConnectionForm';
import type { ProviderConnectionProfile } from './provider-connection';
import ChatGPTSubscriptionPanel from './ChatGPTSubscriptionPanel';
import {sortModelProfiles} from './model-catalog';
import './provider-layout.css';

export default function ProviderRenderer(props:{artifact:ArtifactEnvelope;context:RendererContext;heading?:boolean}) {
  const profiles=()=>sortModelProfiles((props.artifact.data as ProviderConnectionProfile[]).filter(profile=>profile.auth_kind!=='OAUTH_SUBSCRIPTION'));
  const initialMode=props.artifact.presentation?.initialTab==='api'||!props.artifact.presentation&&props.artifact.artifactId.startsWith('providers-api-')?'api':'subscription';
  const [mode,setMode]=createSignal<'subscription'|'api'>(initialMode);
  const [subscriptionVisited,setSubscriptionVisited]=createSignal(initialMode==='subscription');
  const [apiVisited,setApiVisited]=createSignal(initialMode==='api');
  const choiceId=createUniqueId();
  let lastEntry=props.artifact.presentation?.entryId??props.artifact.artifactId;
  let subscriptionTab:HTMLButtonElement|undefined,apiTab:HTMLButtonElement|undefined;
  function choose(next:'subscription'|'api'){
    setMode(next);
    if(next==='subscription')setSubscriptionVisited(true);else setApiVisited(true);
  }
  createEffect(()=>{
    const entry=props.artifact.presentation?.entryId??props.artifact.artifactId;
    if(entry===lastEntry)return;
    lastEntry=entry;
    if(props.artifact.presentation?.initialTab==='api'||!props.artifact.presentation&&entry.startsWith('providers-api-'))choose('api');
    if(props.artifact.presentation?.initialTab==='subscription'||!props.artifact.presentation&&entry.startsWith('providers-subscription-'))choose('subscription');
  });
  function choiceKey(event:KeyboardEvent){
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();
    const next=event.key==='Home'?'subscription':event.key==='End'?'api':mode()==='subscription'?'api':'subscription';
    choose(next);(next==='subscription'?subscriptionTab:apiTab)?.focus();
  }
  const [selected,setSelected]=createSignal<ProviderConnectionProfile>();
  const saveTargets=new Map<string,ArtifactEnvelope>();
  const profileKey=(profile:ProviderConnectionProfile)=>`${profile.profile_ref.id}:${profile.profile_ref.revision}:${profile.profile_ref.sha256}`;
  const selectedInCatalog=()=>!selected()||profiles().some(profile=>profileKey(profile)===profileKey(selected()!));
  const [refreshing,setRefreshing]=createSignal(false),[error,setError]=createSignal('');
  async function refresh(){
    if(refreshing())return;setRefreshing(true);setError('');
    try{await props.context.dispatch('provider.refresh',props.artifact,{});}
    catch(error){setError(error instanceof Error?error.message:String(error));}
    finally{setRefreshing(false);}
  }
  return <div class="as-object as-provider"><Show when={props.heading!==false}><h2>{uiText("连接模型")}</h2></Show>
    <div class="as-provider-choices" role="tablist" aria-label={uiText("模型连接方式")} onKeyDown={choiceKey}>
      <button type="button" role="tab" ref={subscriptionTab} id={`${choiceId}-subscription-tab`} aria-controls={`${choiceId}-subscription-panel`} aria-selected={mode()==='subscription'} tabIndex={mode()==='subscription'?0:-1} onClick={()=>choose('subscription')}>{uiText("ChatGPT 账号")}</button>
      <button type="button" role="tab" ref={apiTab} id={`${choiceId}-api-tab`} aria-controls={`${choiceId}-api-panel`} aria-selected={mode()==='api'} tabIndex={mode()==='api'?0:-1} onClick={()=>choose('api')}>{uiText("API / 内网模型")}</button>
    </div>
    <Show when={subscriptionVisited()}><div role="tabpanel" id={`${choiceId}-subscription-panel`} aria-labelledby={`${choiceId}-subscription-tab`} hidden={mode()!=='subscription'}>
      <ChatGPTSubscriptionPanel entryId={props.artifact.presentation?.entryId} onProfile={async(profile,selection)=>{await props.context.dispatch('provider.refresh',{...props.artifact,presentation:{...props.artifact.presentation,entryId:selection?.entryId??''}},selection?.explicit?{select_id:profile.profile_ref.id}:{});}}/>
    </div></Show>
    <Show when={apiVisited()}><div class="as-provider-api" role="tabpanel" id={`${choiceId}-api-panel`} aria-labelledby={`${choiceId}-api-tab`} hidden={mode()!=='api'}>
    <h3>{uiText("OpenAI 兼容 API")}</h3>
    <div class="as-provider-directory">
      <label>{uiText("选择已有连接")}<select aria-label={uiText("选择已有连接")} value={selected()?profileKey(selected()!):''} onChange={event=>{const key=event.currentTarget.value;if(selected()&&profileKey(selected()!)===key)return;setSelected(profiles().find(profile=>profileKey(profile)===key));}}>
        <option value="" selected={!selected()}>{uiText("新增连接")}</option>
        <Show when={!selectedInCatalog()&&selected()}>{profile=><option value={profileKey(profile())} selected>{profile().model_id} · {profile().profile_ref.id} · v{profile().profile_ref.revision}</option>}</Show>
        <For each={profiles()}>{profile=><option value={profileKey(profile)} selected={selected()?.profile_ref.id===profile.profile_ref.id&&selected()?.profile_ref.revision===profile.profile_ref.revision&&selected()?.profile_ref.sha256===profile.profile_ref.sha256}>{profile.model_id} · {profile.profile_ref.id} · v{profile.profile_ref.revision}</option>}</For>
      </select></label>
      <button type="button" disabled={refreshing()} onClick={()=>void refresh()}>{uiText("刷新连接列表")}</button>
    </div>
    <Show when={selected()}>{profile=><div class="as-provider-selected" aria-live="polite">
      <strong class="as-provider-identifier">{profile().model_id}</strong>
      <p class="as-provider-identifier">{profile().provider_url}</p>
      <small>{uiText("版本 ")}{profile().profile_ref.revision} · {profile().auth_kind==='NONE'?uiText("免 API Key"):profile().credential_present?uiText("凭证已保存"):uiText("未保存凭证")}</small>
      <p class="as-capability-note">{uiText("已保存，尚未验证模型连接；本次没有发送测试请求。")}</p>
    </div>}</Show>
    <ProviderConnectionForm initialProfile={selected()}
      onNew={()=>setSelected(undefined)}
      onSave={body=>{const target=props.artifact;saveTargets.set(body.operation_id,target);return props.context.dispatch('provider.save',target,body);}}
      onInspect={operationId=>props.context.dispatch('provider.inspect',saveTargets.get(operationId)??props.artifact,{operation_id:operationId})}
      onSaved={async(profile,operationId)=>{setSelected(profile);const target=saveTargets.get(operationId);await props.context.dispatch('provider.refresh',target??{...props.artifact,presentation:undefined},{select_id:profile.profile_ref.id});saveTargets.delete(operationId);}}/>
    </div></Show>
    <Show when={error()}><p class="as-inline-error" role="alert">{uiText(error())}</p></Show>
  </div>;
}
