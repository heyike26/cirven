import { api, type ApiClient } from './api.ts';
import type { ProviderProfile } from './contracts';

export type SubscriptionStatus = 'SIGNED_OUT' | 'WAITING_USER' | 'AUTHENTICATED' | 'UNKNOWN_OUTCOME';
export type SubscriptionAccount = {
  status: SubscriptionStatus;
  attempt_id: string | null;
  login_url?: string;
  phase?: string | null;
  model_access: 'NOT_VALIDATED' | 'VERIFIED';
  api_key_fallback: false;
  models: { id: string; name: string }[];
  profile?: ProviderProfile;
};
export type SubscriptionIntent = {
  operation_id: string;
  kind: 'LOGIN' | 'COMPLETE' | 'CANCEL' | 'PROFILE';
  attempt_id?: string;
  profile_id?: string;
  model_id?: string;
  acknowledged: boolean;
};
export type SubscriptionProfileOperation = {
  operation_id: string;
  status: 'COMPLETED' | 'INCOMPLETE' | 'NO_RECORD';
  result: ProviderProfile | null;
};
export const SUBSCRIPTION_HISTORY_KEY = 'eeas-subscription-operations';
export class SubscriptionAccountError extends Error {}
type MetadataStorage = Pick<Storage, 'getItem' | 'setItem'>;
type RequestClient = Pick<ApiClient, 'request'>;
const idValid = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value);
const modelValid = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_.:/-]{1,200}$/.test(value);
export const signedOutSubscription = (): SubscriptionAccount => ({ status: 'SIGNED_OUT', attempt_id: null, model_access: 'NOT_VALIDATED', api_key_fallback: false, models: [] });

export function subscriptionLoginURL(value: string): string {
  if (typeof value !== 'string' || value.length > 8192 || /[\u0000-\u0020\\]/u.test(value)) throw new SubscriptionAccountError('登录地址无效，请刷新账号状态');
  let url: URL;
  try { url = new URL(value); } catch { throw new SubscriptionAccountError('登录地址无效，请刷新账号状态'); }
  if (url.protocol !== 'https:' || url.hostname !== 'auth.openai.com' || url.username || url.password || url.port || url.hash) throw new SubscriptionAccountError('仅能打开 OpenAI 官方账号登录页');
  return url.href;
}

function publicProfile(value: unknown): ProviderProfile {
  const p = value as Partial<ProviderProfile> | null;
  if (!p?.profile_ref || !idValid(p.profile_ref.id) || !Number.isSafeInteger(p.profile_ref.revision) || p.profile_ref.revision < 1 || !/^[a-f0-9]{64}$/.test(p.profile_ref.sha256) || p.provider_url !== 'https://chatgpt.com/backend-api/codex' || !modelValid(p.model_id) || p.auth_kind !== 'OAUTH_SUBSCRIPTION' || typeof p.model_access !== 'string' || p.credential_present !== false) throw new SubscriptionAccountError('订阅连接回执无效，请刷新账号状态');
  return { profile_ref: { id: p.profile_ref.id, revision: p.profile_ref.revision, sha256: p.profile_ref.sha256 }, provider_url: p.provider_url, model_id: p.model_id, auth_kind: p.auth_kind, credential_present: p.credential_present, model_access: p.model_access, ...(typeof p.supports_images === 'boolean' ? { supports_images: p.supports_images } : {}) };
}

export function subscriptionAccount(value: unknown): SubscriptionAccount {
  const p = value as Partial<SubscriptionAccount> | null;
  if (!p || !['SIGNED_OUT', 'WAITING_USER', 'AUTHENTICATED', 'UNKNOWN_OUTCOME'].includes(p.status ?? '') || p.api_key_fallback !== false || !['NOT_VALIDATED', 'VERIFIED'].includes(p.model_access ?? '') || !(p.attempt_id === null || idValid(p.attempt_id)) || !Array.isArray(p.models) || p.models.length > 1000) throw new SubscriptionAccountError('账号状态回执无效，请只读刷新');
  if (p.status === 'WAITING_USER' && !p.attempt_id) throw new SubscriptionAccountError('账号状态缺少原登录尝试，请只读刷新');
  const models = p.models.map(model => {
    if (!model || !modelValid(model.id) || typeof model.name !== 'string' || !model.name || model.name.length > 300) throw new SubscriptionAccountError('账号返回的模型列表无效');
    return { id: model.id, name: model.name };
  });
  if (new Set(models.map(model => model.id)).size !== models.length) throw new SubscriptionAccountError('账号模型标识重复，请刷新');
  if (p.phase !== undefined && p.phase !== null && (typeof p.phase !== 'string' || !/^[A-Z_]{1,64}$/.test(p.phase))) throw new SubscriptionAccountError('登录阶段回执无效，请只读刷新');
  return { status: p.status!, attempt_id: p.attempt_id!, model_access: p.model_access!, api_key_fallback: false, models, ...(p.phase !== undefined ? { phase: p.phase } : {}), ...(typeof p.login_url === 'string' ? { login_url: p.login_url } : {}), ...(p.profile ? { profile: publicProfile(p.profile) } : {}) };
}

export function readSubscriptionHistory(storage?: MetadataStorage): SubscriptionIntent[] {
  if (!storage) return [];
  let raw: string | null;
  try { raw = storage.getItem(SUBSCRIPTION_HISTORY_KEY); } catch { throw new SubscriptionAccountError('无法读取原登录操作索引；请保留记录，不会重发'); }
  if (!raw) return [];
  let values: unknown;
  try { values = JSON.parse(raw); } catch { throw new SubscriptionAccountError('登录操作索引已损坏；请保留记录，只读刷新，不会重发'); }
  if (!Array.isArray(values) || values.length > 1000) throw new SubscriptionAccountError('登录操作索引无法核对；请保留记录并刷新账号状态');
  return values.map(value => {
    const p = value as SubscriptionIntent;
    if (!p || !idValid(p.operation_id) || !['LOGIN', 'COMPLETE', 'CANCEL', 'PROFILE'].includes(p.kind) || typeof p.acknowledged !== 'boolean' || p.attempt_id !== undefined && !idValid(p.attempt_id) || p.profile_id !== undefined && !idValid(p.profile_id) || p.model_id !== undefined && !modelValid(p.model_id)) throw new SubscriptionAccountError('登录操作索引无效；不会重发原操作');
    return { operation_id: p.operation_id, kind: p.kind, acknowledged: p.acknowledged, ...(p.attempt_id ? { attempt_id: p.attempt_id } : {}), ...(p.profile_id ? { profile_id: p.profile_id } : {}), ...(p.model_id ? { model_id: p.model_id } : {}) };
  });
}

/** The journal contains intent metadata only. OAuth URLs and credentials never enter storage. */
export class SubscriptionAccountClient {
  private state?: SubscriptionAccount;
  private busy = false;
  private journal?: SubscriptionIntent[];
  private readonly client: RequestClient;
  private readonly storage?: MetadataStorage;
  constructor(client: RequestClient = api, storage?: MetadataStorage) { this.client = client; this.storage = storage; }
  private records() { return this.journal ??= readSubscriptionHistory(this.storage); }
  private persist(records: SubscriptionIntent[]) {
    try { this.storage?.setItem(SUBSCRIPTION_HISTORY_KEY, JSON.stringify(records)); }
    catch { throw new SubscriptionAccountError('登录操作索引无法保存；不会重发，请只读刷新账号状态'); }
    this.journal = records;
  }
  private intent(kind: SubscriptionIntent['kind'], fields: Partial<SubscriptionIntent> = {}): SubscriptionIntent {
    if (this.busy) throw new SubscriptionAccountError('原账号操作仍在进行，请只读刷新');
    if (!this.storage) throw new SubscriptionAccountError('当前无法持久保存登录操作索引，尚未提交；请恢复本地存储后重试');
    const records = this.records();
    if (records.length >= 1000) throw new SubscriptionAccountError('登录操作索引已满；请保留原记录');
    if ((kind === 'COMPLETE' || kind === 'CANCEL') && records.some(record => record.kind === kind && record.attempt_id === fields.attempt_id)) throw new SubscriptionAccountError('此登录尝试已提交过该操作，请只读刷新，不会重发');
    const entry = { operation_id: `subscription-${crypto.randomUUID().replaceAll('-', '')}`, kind, acknowledged: false, ...fields };
    this.persist([...records, entry]);
    this.busy = true;
    return entry;
  }
  private async post<T>(path: string, body: unknown, entry: SubscriptionIntent, decode: (value: unknown) => T): Promise<T> {
    try {
      // ApiClient normally reboots and retries a 401. Side effects here are never replayed.
      const value = decode(await this.client.request<unknown>(path, 'POST', body, undefined, false));
      this.persist(this.records().map(record => record.operation_id === entry.operation_id ? { ...record, acknowledged: true } : record));
      return value;
    } catch (error) {
      if (this.state) this.state = { ...this.state, status: 'UNKNOWN_OUTCOME' };
      throw error;
    } finally { this.busy = false; }
  }
  async refresh(signal?: AbortSignal): Promise<SubscriptionAccount> {
    const state = subscriptionAccount(await this.client.request<unknown>('/api/subscription/account', 'GET', undefined, signal));
    if (signal?.aborted) throw signal.reason ?? new DOMException('Aborted', 'AbortError');
    this.state = state;
    return state;
  }
  async restoreProfiles(): Promise<SubscriptionProfileOperation[]> {
    const restored: SubscriptionProfileOperation[] = [];
    for (const entry of this.records().filter(record => record.kind === 'PROFILE' && !record.acknowledged)) {
      const raw = await this.client.request<unknown>(`/api/subscription/profile-operations/${encodeURIComponent(entry.operation_id)}`);
      const value = raw as Partial<SubscriptionProfileOperation> | null;
      if (!value || value.operation_id !== entry.operation_id || !['COMPLETED', 'INCOMPLETE', 'NO_RECORD'].includes(value.status ?? '')) throw new SubscriptionAccountError('原订阅保存操作回执不匹配；不会重发，请保留记录');
      if (value.status === 'COMPLETED') {
        const result = publicProfile(value.result);
        if (result.profile_ref.id !== entry.profile_id || result.model_id !== entry.model_id) throw new SubscriptionAccountError('原订阅保存操作来源不匹配；不会重发，请保留记录');
        this.persist(this.records().map(record => record.operation_id === entry.operation_id ? { ...record, acknowledged: true } : record));
        restored.push({ operation_id: entry.operation_id, status: 'COMPLETED', result });
      } else {
        if (value.result !== null) throw new SubscriptionAccountError('未完成的订阅保存回执不能作为已保存配置');
        restored.push({ operation_id: entry.operation_id, status: value.status as 'INCOMPLETE' | 'NO_RECORD', result: null });
      }
    }
    return restored;
  }
  async login(): Promise<SubscriptionAccount> {
    if (!this.state || !['SIGNED_OUT','AUTHENTICATED'].includes(this.state.status)) throw new SubscriptionAccountError('请先读取账号状态；已有尝试只刷新原状态');
    const entry = this.intent('LOGIN');
    const state = await this.post('/api/subscription/login', { operation_id: entry.operation_id, method: 'browser' }, entry, subscriptionAccount);
    this.state = state; return state;
  }
  private attempt(attemptId: string) {
    if (!idValid(attemptId) || this.state?.attempt_id !== attemptId || !['WAITING_USER', 'UNKNOWN_OUTCOME'].includes(this.state.status)) throw new SubscriptionAccountError('只能操作当前账号的原登录尝试，请刷新状态');
  }
  canComplete(attemptId: string) { return !this.records().some(record => record.kind === 'COMPLETE' && record.attempt_id === attemptId); }
  canCancel(attemptId: string) { return !this.records().some(record => record.kind === 'CANCEL' && record.attempt_id === attemptId); }
  async complete(attemptId: string) {
    this.attempt(attemptId);
    const entry = this.intent('COMPLETE', { attempt_id: attemptId });
    const state = await this.post(`/api/subscription/attempts/${encodeURIComponent(attemptId)}/complete`, {}, entry, subscriptionAccount);
    this.state = state; return state;
  }
  async cancel(attemptId: string) {
    this.attempt(attemptId);
    const entry = this.intent('CANCEL', { attempt_id: attemptId });
    const state = await this.post(`/api/subscription/attempts/${encodeURIComponent(attemptId)}/cancel`, {}, entry, subscriptionAccount);
    this.state = state; return state;
  }
  async createProfile(id: string, modelId: string): Promise<ProviderProfile> {
    if (this.state?.status !== 'AUTHENTICATED' || !idValid(id) || !this.state.models.some(model => model.id === modelId)) throw new SubscriptionAccountError('请登录后选择账号实际返回的模型，并填写连接标识');
    if (this.records().some(record => record.kind === 'PROFILE' && !record.acknowledged)) throw new SubscriptionAccountError('订阅连接保存结果尚未核实，请刷新原账号状态，不会重发');
    const entry = this.intent('PROFILE', { profile_id: id, model_id: modelId });
    return this.post('/api/subscription/profiles', { operation_id: entry.operation_id, id, model_id: modelId }, entry, publicProfile);
  }
}

export type SubscriptionLoginStarted = { account: SubscriptionAccount; browserError?: string };

/** Only an explicit new-login action starts the original callback. Mount/reload never calls this. */
export async function beginSubscriptionLogin(
  client: SubscriptionAccountClient,
  openLogin: (url: string, attemptId: string) => Promise<unknown> | unknown,
  onAccount?: (account: SubscriptionAccount) => void,
): Promise<SubscriptionLoginStarted> {
  const started = await client.login();
  onAccount?.(started);
  if (started.status !== 'WAITING_USER' || !started.attempt_id) throw new SubscriptionAccountError('原登录尝试尚未建立，请只读检查账号状态');
  let browserError: string | undefined;
  try { await openLogin(started.login_url ?? '', started.attempt_id); }
  catch { browserError = '官方登录页未能自动打开。请点击“打开官方登录页”继续，本软件会自动接收登录结果。'; }
  // The official browser is opened first so Main can resolve its current owned
  // target. Starting the callback does not wait for the browser to return.
  const account = await client.complete(started.attempt_id);
  onAccount?.(account);
  return { account, ...(browserError ? { browserError } : {}) };
}

export type SubscriptionObservation = 'AUTHENTICATED' | 'SIGNED_OUT' | 'UNKNOWN_OUTCOME' | 'TIMEOUT' | 'STOPPED';
export type SubscriptionObservationOptions = {
  signal?: AbortSignal;
  intervalMs?: number;
  timeoutMs?: number;
  onAccount: (account: SubscriptionAccount) => void;
};

/** Bounded, sequential GET observation. It cannot authorize, complete, save or invoke a model. */
export async function observeSubscriptionAccount(client: SubscriptionAccountClient, options: SubscriptionObservationOptions): Promise<SubscriptionObservation> {
  const interval = options.intervalMs ?? 1500;
  const timeout = options.timeoutMs ?? 10 * 60 * 1000;
  if (!Number.isFinite(interval) || interval < 1 || !Number.isFinite(timeout) || timeout < 1) throw new SubscriptionAccountError('登录状态观察时间无效');
  const controller = new AbortController();
  let timedOut = false;
  const stop = () => controller.abort();
  options.signal?.addEventListener('abort', stop, { once: true });
  if (options.signal?.aborted) stop();
  const deadline = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
  const readAccount = async () => {
    let interrupt!: () => void;
    const stopped = new Promise<never>((_resolve, reject) => {
      interrupt = () => reject(controller.signal.reason ?? new DOMException('Aborted', 'AbortError'));
      controller.signal.addEventListener('abort', interrupt, { once: true });
    });
    try { return await Promise.race([client.refresh(controller.signal), stopped]); }
    finally { controller.signal.removeEventListener('abort', interrupt); }
  };
  const delay = () => new Promise<void>(resolve => {
    if (controller.signal.aborted) { resolve(); return; }
    const finish = () => { clearTimeout(timer); controller.signal.removeEventListener('abort', finish); resolve(); };
    const timer = setTimeout(finish, interval);
    controller.signal.addEventListener('abort', finish, { once: true });
  });
  try {
    while (!controller.signal.aborted) {
      const account = await readAccount();
      if (controller.signal.aborted) break;
      options.onAccount(account);
      if (account.status !== 'WAITING_USER') return account.status;
      await delay();
    }
    return timedOut ? 'TIMEOUT' : 'STOPPED';
  } catch (error) {
    if (controller.signal.aborted) return timedOut ? 'TIMEOUT' : 'STOPPED';
    throw error;
  } finally {
    clearTimeout(deadline);
    options.signal?.removeEventListener('abort', stop);
  }
}

export function canOpenSubscriptionLogin(state: SubscriptionAccount, host: unknown = globalThis): boolean {
  const desktop = (host as { eeasDesktop?: { openSubscriptionLogin?: unknown } }).eeasDesktop;
  return !!state.attempt_id && (typeof desktop?.openSubscriptionLogin === 'function' || !!state.login_url);
}

export async function openSubscriptionLogin(url: string, attemptId: string, host: unknown = globalThis) {
  if (!idValid(attemptId)) throw new SubscriptionAccountError('缺少当前登录尝试，请刷新账号状态');
  const desktop = (host as { eeasDesktop?: { openSubscriptionLogin?: (attemptId: string) => Promise<unknown> } }).eeasDesktop;
  if (desktop) {
    if (typeof desktop.openSubscriptionLogin !== 'function') throw new SubscriptionAccountError('此桌面版本尚未连接账号登录入口');
    await desktop.openSubscriptionLogin(attemptId);
    return;
  }
  const target = subscriptionLoginURL(url);
  const browser = host as { open?: (url: string, target: string, features: string) => unknown };
  if (typeof browser.open !== 'function') throw new SubscriptionAccountError('请在浏览器或桌面应用中打开登录页');
  browser.open(target, '_blank', 'noopener,noreferrer');
}
