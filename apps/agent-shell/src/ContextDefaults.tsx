import {createEffect,createSignal,onCleanup,Show,untrack} from 'solid-js';
import type {ContextDefaults as Defaults,ContextGraphClient,ContextPolicy} from './context-graph';

/** Defaults configure inheritance; global source content has a separate opt-in. */
export default function ContextDefaults(props:{active:boolean;client:ContextGraphClient;version:number;busy:boolean;pending:boolean;en:boolean;change:(work:()=>Promise<unknown>)=>void}){
  const [open,setOpen]=createSignal(false),[current,setCurrent]=createSignal<Defaults>(),[draft,setDraft]=createSignal<Partial<ContextPolicy>>({}),[dirty,setDirty]=createSignal(false),[loading,setLoading]=createSignal(false),[error,setError]=createSignal('');
  let epoch=0;
  async function load(){const own=++epoch;setLoading(true);setError('');try{const value=await props.client.defaults();if(own!==epoch)return;setCurrent(value);if(!dirty())setDraft({...value.policy});}catch{if(own===epoch)setError(props.en?'Could not read the defaults. Your settings are kept.':'暂时无法读取默认设置，已保留当前输入。');}finally{if(own===epoch)setLoading(false);}}
  createEffect(()=>{const active=props.active,expanded=open();props.version;untrack(()=>{if(!active||!expanded){epoch++;setLoading(false);return;}if(!dirty())void load();});});
  onCleanup(()=>{epoch++;});
  function edit(key:keyof ContextPolicy,value:ContextPolicy[keyof ContextPolicy]|undefined){setDraft(old=>{const next={...old};if(value===undefined)delete next[key];else Object.assign(next,{[key]:value});return next;});setDirty(true);}
  function save(policy:Partial<ContextPolicy>){const basis=current();if(!basis)return;props.change(async()=>{const result=await props.client.saveDefaults(basis.revision,policy);setDirty(false);await load();return result;});}
  const disabled=()=>props.busy||props.pending||loading();
  const budgetValid=()=>{const value=draft().char_budget;return value===undefined||Number.isInteger(value)&&value>=0&&value<=16000;};
  return <details class="as-context-defaults" open={open()} onToggle={event=>setOpen(event.currentTarget.open)}><summary>{props.en?'Defaults for all projects':'所有项目默认设置'}</summary>
    <p class="as-context-muted">{props.en?'Projects and tasks can override these defaults. Sharing global sources is controlled separately.':'项目和任务可单独覆盖这些默认值。全局资料的引用需另行开启。'}</p>
    <Show when={error()}><p role="alert">{error()}</p></Show>
    <Show when={current()} fallback={<button disabled={disabled()} onClick={()=>void load()}>{props.en?'Load settings':'读取设置'}</button>}>
      <label>{props.en?'Capture source candidates':'收集候选资料'}<select disabled={disabled()} value={draft().capture===undefined?'':String(draft().capture)} onChange={event=>edit('capture',event.currentTarget.value===''?undefined:event.currentTarget.value==='true')}><option value="">{props.en?'Built-in: on':'内置默认：开启'}</option><option value="true">{props.en?'On':'开启'}</option><option value="false">{props.en?'Off':'关闭'}</option></select></label>
      <label>{props.en?'Retrieve context':'检索上下文'}<select disabled={disabled()} value={draft().retrieve===undefined?'':String(draft().retrieve)} onChange={event=>edit('retrieve',event.currentTarget.value===''?undefined:event.currentTarget.value==='true')}><option value="">{props.en?'Built-in: on':'内置默认：开启'}</option><option value="true">{props.en?'On':'开启'}</option><option value="false">{props.en?'Off':'关闭'}</option></select></label>
      <label>{props.en?'Archive sources':'归档资料'}<select disabled={disabled()} value={draft().archive_mode??''} onChange={event=>edit('archive_mode',(event.currentTarget.value||undefined) as ContextPolicy['archive_mode']|undefined)}><option value="">{props.en?'Built-in: review first':'内置默认：审阅后采用'}</option><option value="MANUAL">{props.en?'Manual only':'仅手动'}</option><option value="AUTO_REVIEW">{props.en?'Review candidates':'收集候选，审阅后采用'}</option><option value="AUTO">{props.en?'Adopt automatically':'自动采用'}</option></select></label>
      <label>{props.en?'Character limit (0–16000)':'字数上限（0–16000）'}<input type="number" min="0" max="16000" step="1" disabled={disabled()} value={draft().char_budget??''} placeholder="8000" onInput={event=>{const value=event.currentTarget.value;edit('char_budget',value===''?undefined:Number(value));}}/></label>
      <div class="as-context-item-actions"><button disabled={disabled()||!dirty()||!budgetValid()} onClick={()=>save({...draft()})}>{props.en?'Save defaults':'保存默认设置'}</button><button disabled={disabled()} onClick={()=>save({})}>{props.en?'Restore built-in defaults':'恢复内置默认'}</button><Show when={dirty()}><button disabled={disabled()} onClick={()=>{setDirty(false);void load();}}>{props.en?'Reload settings':'重新读取设置'}</button></Show></div>
    </Show>
  </details>;
}
