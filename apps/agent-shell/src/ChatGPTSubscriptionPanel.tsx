import {t as uiText} from './ui-language';
import { createSignal, For, onCleanup, onMount, Show } from 'solid-js';
import type { ProviderProfile } from './contracts';
import { beginSubscriptionLogin, canOpenSubscriptionLogin, observeSubscriptionAccount, openSubscriptionLogin, SubscriptionAccountClient, SubscriptionAccountError, type SubscriptionAccount } from './subscription-account';
import './subscription-account.css';
import './provider-layout.css';
import {catalogSelection, sortModelCatalog} from './model-catalog';
import {BrandLoader} from './BrandLogo';

export type ChatGPTSubscriptionPanelProps = {
  entryId?:string;
  onProfile?: (profile: ProviderProfile, selection?:{explicit:boolean;entryId?:string}) => Promise<unknown> | unknown;
  openLogin?: (url: string, attemptId: string) => Promise<unknown> | unknown;
  client?: SubscriptionAccountClient;
  initialAccount?: SubscriptionAccount;
};

export default function ChatGPTSubscriptionPanel(props: ChatGPTSubscriptionPanelProps) {
  const client = props.client ?? new SubscriptionAccountClient(undefined, globalThis.localStorage);
  const [account, setAccount] = createSignal<SubscriptionAccount | undefined>(props.initialAccount);
  const [busy, setBusy] = createSignal(''), [error, setError] = createSignal('');
  const [model, setModel] = createSignal(catalogSelection(props.initialAccount?.models??[],'',props.initialAccount?.profile?.model_id,true)), [profileId, setProfileId] = createSignal(props.initialAccount?.profile?.profile_ref.id??'chatgpt-subscription');
  const [cancelAllowed, setCancelAllowed] = createSignal(false);
  const [watching, setWatching] = createSignal(false), [watchNote, setWatchNote] = createSignal('');
  let observer: AbortController | undefined;
  let selectionRestored=props.initialAccount?.status==='AUTHENTICATED';
  let alive = true;
  onCleanup(() => { alive = false; observer?.abort(); });
  function accept(state: SubscriptionAccount) {
    if (!alive) return;
    setAccount(state);
    const restoreSaved=!selectionRestored&&state.status==='AUTHENTICATED';
    setModel(catalogSelection(state.models,model(),state.profile?.model_id,restoreSaved));
    if(restoreSaved){selectionRestored=true;if(state.profile)setProfileId(state.profile.profile_ref.id);}
    setCancelAllowed(!!state.attempt_id && client.canCancel(state.attempt_id));
  }
  function observe() {
    observer?.abort();
    if (!alive || account()?.status !== 'WAITING_USER') { setWatching(false); return; }
    const current = new AbortController(); observer = current;
    setWatching(true); setWatchNote('');
    void observeSubscriptionAccount(client, { signal: current.signal, onAccount: accept }).then(result => {
      if (!alive || observer !== current) return;
      if (result === 'TIMEOUT') setWatchNote('等待已超过 10 分钟，已停止自动检查。可以只读检查原登录结果，或取消后重新登录。');
      if (result === 'UNKNOWN_OUTCOME') setWatchNote('原登录结果尚未核实。可以只读检查，或取消此尝试后重新登录。');
    }).catch(() => {
      if (alive && observer === current) setWatchNote('暂时无法读取登录结果，已停止自动检查。请点击“检查登录状态”恢复，只会读取原尝试。');
    }).finally(() => { if (alive && observer === current) { observer = undefined; setWatching(false); } });
  }
  async function perform(label: string, work: () => Promise<void>) {
    if (busy()) return;
    setBusy(label); setError('');
    try { await work(); }
    catch (failure) {
      if (alive) {
        setError(failure instanceof SubscriptionAccountError ? failure.message : '操作结果尚未核实。请刷新账号状态；不会自动重发登录、完成或保存操作。');
        const state = account();
        if (state) {
          setAccount({ ...state, status: 'UNKNOWN_OUTCOME' });
          try { setCancelAllowed(!!state.attempt_id && client.canCancel(state.attempt_id)); } catch { setCancelAllowed(false); }
        }
      }
    } finally { if (alive) setBusy(''); }
  }
  async function adoptProfile(profile: ProviderProfile,selection:{explicit:boolean;entryId?:string}) {
    if (!alive) return;
    setAccount(before => before ? {...before,profile,model_access:before.profile?.profile_ref.id===profile.profile_ref.id&&before.profile.profile_ref.revision===profile.profile_ref.revision&&before.profile.profile_ref.sha256===profile.profile_ref.sha256&&before.profile.model_id===profile.model_id?before.model_access:'NOT_VALIDATED'} : before);
    try { await props.onProfile?.(profile,selection); }
    catch { if (alive) setError('订阅连接已保存，模型列表刷新尚未完成。请刷新连接列表读取原配置。'); }
  }
  const refresh = async () => {
    const entryId=props.entryId;
    observer?.abort();
    const state = await client.refresh(); accept(state);
    const restored = await client.restoreProfiles();
    for (const operation of restored) {
      if (operation.status === 'COMPLETED' && operation.result) await adoptProfile(operation.result,{explicit:false,entryId});
      else if (alive) setError(operation.status === 'NO_RECORD' ? '未找到原订阅保存回执；这不证明未执行，不会重发。请保留操作记录。' : '原订阅连接保存尚未完成；请继续只读刷新，不会重发。');
    }
    observe();
  };
  onMount(() => { void perform('读取账号状态', refresh); });
  async function openPage() {
    const state = account();
    if (!state?.attempt_id) throw new SubscriptionAccountError('缺少当前登录尝试，请只读刷新');
    await (props.openLogin ?? openSubscriptionLogin)(state.login_url ?? '', state.attempt_id);
  }
  async function login() {
    const result = await beginSubscriptionLogin(client, props.openLogin ?? openSubscriptionLogin, accept);
    if (alive && result.browserError) setError(result.browserError);
    observe();
  }
  async function cancel() { observer?.abort(); setWatching(false); setWatchNote(''); const id = account()?.attempt_id; if (id) accept(await client.cancel(id)); }
  async function save() {
    const selection={explicit:true,entryId:props.entryId};
    const current=account(),saved=current?.profile,ref=saved?.profile_ref;
    if(current?.status==='AUTHENTICATED'&&savedSelection()&&saved&&ref&&Number.isInteger(ref.revision)&&ref.revision>0&&/^[a-f0-9]{64}$/.test(ref.sha256)){
      // Selecting the exact already-saved immutable profile is not a new save.
      // A different model/id still follows the original one-POST save path.
      await adoptProfile(saved,selection);return;
    }
    const profile = await client.createProfile(profileId().trim(), model());
    await adoptProfile(profile,selection);
  }
  const savedSelection=()=>!!model()&&account()?.profile?.model_id===model()&&account()?.profile?.profile_ref.id===profileId().trim();
  const reauth=()=>account()?.phase==='REAUTH_REQUIRED';
  const step = () => account()?.status!=='AUTHENTICATED'||reauth()?1:savedSelection()?3:2;
  const verified=()=>!reauth()&&account()?.status==='AUTHENTICATED'&&account()?.model_access==='VERIFIED'&&savedSelection();
  return <section class="as-capability-form as-subscription-account" aria-label={uiText("订阅账号")}>
    <ol class="as-subscription-steps" aria-label={uiText("订阅连接步骤")}>
      <li aria-current={step() === 1 ? 'step' : undefined} data-complete={step() > 1}>{uiText("连接账号")}</li>
      <li aria-current={step() === 2 ? 'step' : undefined} data-complete={step() > 2}>{uiText("选择模型")}</li>
      <li aria-current={step() === 3 ? 'step' : undefined}>{uiText("开始使用")}</li>
    </ol>
    <Show when={!account()}><p role="status">{uiText("正在读取账号状态…")}</p></Show>
    <Show when={account()}>{state => <>
      <div class="as-subscription-status" role="status"><strong>{reauth() ? uiText("请重新登录") : state().status === 'SIGNED_OUT' ? uiText("尚未登录") : state().status === 'WAITING_USER' ? uiText("等待你完成官方登录") : state().status === 'AUTHENTICATED' ? uiText("账号已登录") : uiText("原操作结果待核实")}</strong>
        <Show when={state().status==='AUTHENTICATED'&&!reauth()}><span>{verified()?uiText("此模型已有成功响应"):uiText("此模型尚未验证")}</span></Show></div>
      <Show when={reauth()}><p class="as-capability-note">{uiText("模型连接已失效。重新登录会保留会话与原问题，不会自动重发请求。")}</p></Show>
      <Show when={state().status === 'WAITING_USER'}><p class="as-capability-note">{uiText("正在等待原登录回调，可刷新查看。")}{state().phase === 'COMPLETING' ? uiText("在官方页面完成登录后，这里会自动进入模型选择，无需点击“完成登录”。") : uiText("这是之前的登录尝试；恢复页面只读取原状态，不会重放授权。若一直未完成，可以取消此尝试后重新登录。")}</p></Show>
      <Show when={state().status === 'UNKNOWN_OUTCOME'}><p class="as-capability-note">{uiText("暂时无法确认原登录结果。请检查状态，或取消此尝试后重新登录；未核实前不会发送模型请求。")}</p></Show>
      <Show when={state().attempt_id && !canOpenSubscriptionLogin(state()) && !props.openLogin && state().status !== 'AUTHENTICATED'}><p class="as-capability-note">{uiText("当前浏览器没有可恢复的登录网址。请先刷新原尝试；需要重新登录时，先取消此尝试。")}</p></Show>
      <div class="as-capability-actions">
        <Show when={state().status === 'SIGNED_OUT'||reauth()}><button type="button" class="as-subscription-primary" title={uiText("在浏览器中登录订阅账号，完成后自动返回。")} disabled={!!busy()} onClick={() => void perform('正在准备官方登录', login)}>{reauth()?uiText("重新登录"):uiText("登录 ChatGPT 账号")}</button></Show>
        <Show when={state().attempt_id && (props.openLogin || canOpenSubscriptionLogin(state())) && state().status !== 'AUTHENTICATED'}><button type="button" disabled={!!busy()} onClick={() => void perform('打开官方登录页', openPage)}>{uiText("打开官方登录页")}</button></Show>
        <Show when={state().attempt_id && ['WAITING_USER', 'UNKNOWN_OUTCOME'].includes(state().status) && cancelAllowed()}><button type="button" disabled={!!busy()} onClick={() => void perform('取消原登录尝试', cancel)}>{uiText("取消此登录")}</button></Show>
      </div>
      <Show when={state().status === 'AUTHENTICATED'}>
        <Show when={!reauth()}><Show when={state().models.length} fallback={<p class="as-inline-warning">{uiText("账号已登录，尚未返回可用模型。请刷新账号状态。")}</p>}>
          <form class="as-subscription-selection" noValidate onSubmit={event => { event.preventDefault(); void perform('保存订阅模型连接', save); }}>
            <label>{uiText("选择模型")}<select aria-label={uiText("选择模型")} value={model()} disabled={!!busy()} onChange={event => setModel(event.currentTarget.value)}>
              <option value="" selected={!state().models.some(item => item.id === model())}>{uiText("请选择模型")}</option>
              <For each={sortModelCatalog(state().models)}>{item => <option value={item.id} selected={item.id === model()}>{item.name}</option>}</For>
            </select></label>
            <Show when={model()}><small class="as-provider-identifier">{model()}</small></Show>
            <details><summary>{uiText("连接名称")}</summary><label>{uiText("订阅连接标识")}<input aria-label={uiText("订阅连接标识")} value={profileId()} maxLength={100} disabled={!!busy()} onInput={event => setProfileId(event.currentTarget.value)} /></label></details>
            <div class="as-capability-actions"><button type="submit" class="as-subscription-primary" disabled={!!busy() || !model() || !profileId().trim()}>{uiText("使用此订阅模型")}</button></div>
          </form>
        </Show></Show>
        <Show when={state().profile}><details class="as-provider-details"><summary>{uiText("已保存订阅连接 ")}{state().profile!.model_id}</summary><dl><dt>{uiText("订阅连接标识")}</dt><dd class="as-provider-identifier">{state().profile!.profile_ref.id}</dd><dt>{uiText("版本 ")}</dt><dd>{state().profile!.profile_ref.revision}</dd><dt>{uiText("模型标识")}</dt><dd class="as-provider-identifier">{state().profile!.model_id}</dd></dl><p class="as-capability-note">{uiText("实际请求结果单独验证。")}</p><Show when={!reauth()}><button type="button" disabled={!!busy()} onClick={()=>void perform('正在准备官方登录',login)}>{uiText("重新登录")}</button></Show></details></Show>
      </Show>
    </>}</Show>
    <Show when={watching() && !busy()}><p class="as-capability-pending" role="status">{uiText("正在自动检查登录结果…")}</p></Show>
    <Show when={watchNote()}><p class="as-inline-warning" role="status">{uiText(watchNote())}</p></Show>
    <div class="as-capability-actions"><button type="button" disabled={!!busy()} onClick={() => void perform('只读刷新账号状态', refresh)}>{uiText("检查登录状态")}</button></div>
    <Show when={busy()}><p class="as-capability-pending" role="status"><BrandLoader/>{uiText(busy())}…</p></Show>
    <Show when={error()}><p class="as-inline-warning" role="alert">{uiText(error())}</p></Show>
  </section>;
}
